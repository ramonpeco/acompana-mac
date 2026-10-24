"""AI supervision layer.

Responsibilities: evaluate/compare candidates, detect anomalies, explain switches,
write reports, analyse failure patterns and *propose* parameter changes.

Hard constraints enforced in code (not by prompt):
* the AI can only choose among candidates that already passed deterministic filters;
* it never creates orders; outputs are stored as analyses;
* proposals touching critical risk keys are rejected automatically; others are
  stored as ``StrategyVersion(status="proposed")`` and need admin approval plus
  out-of-sample validation before activation;
* when the provider is down or over budget, deterministic fallbacks are used.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import AIUnavailable, LLMProvider
from app.models import AIAnalysis, StrategyVersion
from app.risk.profiles import CRITICAL_KEYS

log = logging.getLogger(__name__)

SYSTEM = (
    "Eres el supervisor analítico de un sistema de investigación de copy trading en Solana que opera con "
    "capital SIMULADO. No ejecutas órdenes: solo analizas datos y explicas decisiones. Sé escéptico: los "
    "historiales on-chain pueden estar incompletos, las rentabilidades de memecoins tienen colas extremas y "
    "un buen resultado reciente puede ser suerte o manipulación. Nunca prometas rentabilidad. Responde en español."
)

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "wallet": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["approve", "veto"]},
                    "confidence": {"type": "number"},
                    "anomalies": {"type": "array", "items": {"type": "string"}},
                    "reasoning": {"type": "string"},
                },
                "required": ["wallet", "verdict", "confidence", "anomalies", "reasoning"],
                "additionalProperties": False,
            },
        },
        "preferred_wallet": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["reviews", "preferred_wallet", "summary"],
    "additionalProperties": False,
}

TEXT_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "text": {"type": "string"},
                   "alerts": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "text", "alerts"],
    "additionalProperties": False,
}

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "patterns": {"type": "array", "items": {"type": "string"}},
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": ["switch_rules", "copy_params", "risk_overrides"]},
                    "key": {"type": "string"},
                    "value": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "required": ["section", "key", "value", "rationale"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["patterns", "proposals", "summary"],
    "additionalProperties": False,
}


class AISupervisor:
    def __init__(self, provider: LLMProvider, daily_budget: int = 200):
        self.provider = provider
        self.daily_budget = daily_budget

    @property
    def available(self) -> bool:
        return self.provider.name != "none"

    async def _calls_today(self, session: AsyncSession) -> int:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return (await session.execute(select(func.count(AIAnalysis.id)).where(
            AIAnalysis.ts >= start, AIAnalysis.provider != "fallback"))).scalar_one()

    async def _run(self, session: AsyncSession, kind: str, prompt: str, schema: dict, payload: dict) -> dict | None:
        if not self.available:
            return None
        if await self._calls_today(session) >= self.daily_budget:
            log.info("AI daily budget exhausted")
            return None
        try:
            out = await self.provider.complete_json(SYSTEM, prompt, schema)
            session.add(AIAnalysis(kind=kind, provider=self.provider.name, model=self.provider.model, ok=True,
                                   input=payload, output=out))
            return out
        except AIUnavailable as exc:
            session.add(AIAnalysis(kind=kind, provider=self.provider.name, model=self.provider.model, ok=False,
                                   input=payload, output={}, error=str(exc)))
            return None

    def _fallback(self, session: AsyncSession, kind: str, payload: dict, out: dict) -> dict:
        session.add(AIAnalysis(kind=kind, provider="fallback", model=None, ok=True, input=payload, output=out))
        return out

    async def review_candidates(self, session: AsyncSession, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"candidates": candidates}
        prompt = (
            "Evalúa estos traders candidatos (ya han superado filtros deterministas). Para cada uno decide "
            "'approve' o 'veto', señala anomalías (rentabilidades irreales, concentración, pocas operaciones, "
            "posible wash trading, deterioro reciente) y elige el preferido SOLO entre las wallets listadas.\n\n"
            + json.dumps(candidates, default=str)[:60_000]
        )
        out = await self._run(session, "candidate_review", prompt, CANDIDATE_SCHEMA, payload)
        allowed = {c["wallet"] for c in candidates}
        if out is not None:
            out["reviews"] = [r for r in out.get("reviews", []) if r.get("wallet") in allowed]
            if out.get("preferred_wallet") not in allowed:
                out["preferred_wallet"] = None
            out["source"] = "ai"
            return out
        best = candidates[0]["wallet"] if candidates else None
        return self._fallback(session, "candidate_review", payload, {
            "reviews": [{"wallet": c["wallet"], "verdict": "approve", "confidence": 0.5, "anomalies": [],
                         "reasoning": "IA no disponible: se usa la puntuación determinista."} for c in candidates],
            "preferred_wallet": best, "summary": "Selección por puntuación determinista (IA no disponible).",
            "source": "fallback"})

    async def explain(self, session: AsyncSession, kind: str, context: dict[str, Any], fallback_text: str) -> dict[str, Any]:
        prompt = (f"Explica de forma breve y verificable la siguiente decisión del agente ({kind}). Usa solo los datos "
                  f"proporcionados y señala incertidumbres.\n\n{json.dumps(context, default=str)[:40_000]}")
        out = await self._run(session, kind, prompt, TEXT_SCHEMA, context)
        if out is not None:
            return {**out, "source": "ai"}
        return self._fallback(session, kind, context, {"title": kind, "text": fallback_text, "alerts": [], "source": "fallback"})

    async def daily_report(self, session: AsyncSession, stats: dict[str, Any]) -> dict[str, Any]:
        prompt = ("Redacta el informe diario del agente: resultados (simulados), traders seguidos y cambios, "
                  "riesgos y alertas, y qué datos faltan para sacar conclusiones estadísticas. No exageres.\n\n"
                  + json.dumps(stats, default=str)[:60_000])
        out = await self._run(session, "daily_report", prompt, TEXT_SCHEMA, stats)
        if out is not None:
            return {**out, "source": "ai"}
        eq = stats.get("equity")
        txt = (f"Patrimonio simulado {eq:.2f} USD; PnL del día {stats.get('daily_pnl_pct', 0):.2f}%. "
               f"Operaciones cerradas hoy: {stats.get('closed_today', 0)}. Cambios de trader: {stats.get('switches_today', 0)}.") if eq is not None else "Sin datos."
        return self._fallback(session, "daily_report", stats, {"title": "Informe diario", "text": txt, "alerts": [], "source": "fallback"})

    async def analyse_failures(self, session: AsyncSession, data: dict[str, Any]) -> dict[str, Any]:
        prompt = ("Analiza estas operaciones perdedoras y sesiones de traders abandonados (incluye el resultado "
                  "contrafactual de haber seguido copiándolos). Identifica patrones de fracaso y propone como máximo "
                  "3 ajustes de parámetros no críticos (switch_rules/copy_params/risk_overrides). Las propuestas se "
                  "probarán fuera de muestra antes de aplicarse.\n\n" + json.dumps(data, default=str)[:60_000])
        out = await self._run(session, "failure_analysis", prompt, PROPOSAL_SCHEMA, data)
        if out is None:
            return self._fallback(session, "failure_analysis", data, {"patterns": [], "proposals": [], "summary": "IA no disponible.", "source": "fallback"})
        accepted, rejected = [], []
        for prop in out.get("proposals", [])[:3]:
            if prop["section"] == "risk_overrides" and prop["key"] in CRITICAL_KEYS:
                rejected.append({**prop, "rejected": "critical risk limit: requires administrator"})
                continue
            accepted.append(prop)
        if accepted:
            session.add(StrategyVersion(name=f"ai-proposal-{datetime.now(UTC):%Y%m%d%H%M}", params={"changes": accepted},
                                        status="proposed", proposed_by=f"ai:{self.provider.model}",
                                        rationale=out.get("summary")))
        out["rejected"] = rejected
        out["source"] = "ai"
        return out
