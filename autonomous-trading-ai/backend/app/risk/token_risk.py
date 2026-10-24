"""Explainable risk indicator for (new) tokens / memecoins.

Score 0-100, higher = riskier. Each factor contributes points and a human
explanation. Missing information is listed under ``unknowns`` and adds a small
uncertainty penalty: absence of evidence is not evidence of safety.
A low score is NOT a guarantee of safety.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.chains.types import TokenMarket


@dataclass
class RiskFactor:
    code: str
    points: float
    detail: str


@dataclass
class TokenRisk:
    score: float
    level: str
    factors: list[RiskFactor] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    sellability: str = "unknown"  # unknown | route_found | restricted
    disclaimer: str = "Indicador heurístico. Una puntuación baja no garantiza seguridad."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_token(
    m: TokenMarket | None,
    onchain: dict[str, Any] | None,
    now: datetime,
    *,
    previous_liquidity: float | None = None,
    sell_route_ok: bool | None = None,
    creator_sold_pct: float | None = None,
) -> TokenRisk:
    f: list[RiskFactor] = []
    unknowns: list[str] = []

    def add(code: str, pts: float, detail: str) -> None:
        f.append(RiskFactor(code, pts, detail))

    sellability = "unknown"
    if onchain:
        if onchain.get("mint_authority"):
            add("mint_authority", 25, "La autoridad de mint sigue activa: pueden emitirse más tokens.")
        if onchain.get("freeze_authority"):
            add("freeze_authority", 25, "Existe autoridad de freeze: las cuentas de los holders pueden congelarse (posible bloqueo de ventas).")
            sellability = "restricted"
        for ext in onchain.get("risky_extensions") or []:
            pts = 40 if ext in ("nonTransferable", "permanentDelegate") else 20
            add(f"ext_{ext}", pts, f"Extensión Token-2022 '{ext}' presente (comisiones/restricciones de transferencia).")
            if ext in ("nonTransferable", "permanentDelegate", "transferHook"):
                sellability = "restricted"
        supply = onchain.get("supply")
        largest = onchain.get("largest_accounts")
        if supply and largest:
            top10 = sum(a["amount"] for a in largest[:10]) / supply if supply else 0
            # note: the AMM pool vault is usually among the largest accounts
            if top10 > 0.8:
                add("holder_concentration", 25, f"Top-10 cuentas tienen {top10:.0%} del suministro (incluye posibles vaults del pool).")
            elif top10 > 0.5:
                add("holder_concentration", 12, f"Top-10 cuentas tienen {top10:.0%} del suministro.")
        else:
            unknowns.append("holder_concentration")
    else:
        unknowns += ["mint_authority", "freeze_authority", "holder_concentration"]

    if m is None:
        unknowns.append("market")
    else:
        liq = m.liquidity_usd
        if liq is None:
            unknowns.append("liquidity")
        elif liq < 3_000:
            add("very_low_liquidity", 30, f"Liquidez muy baja (${liq:,.0f}).")
        elif liq < 10_000:
            add("low_liquidity", 15, f"Liquidez baja (${liq:,.0f}).")
        if liq and m.market_cap_usd and m.market_cap_usd > 0 and liq / m.market_cap_usd < 0.02:
            add("thin_liquidity_vs_mcap", 10, f"Liquidez/capitalización {liq / m.market_cap_usd:.1%}.")
        if previous_liquidity and liq is not None and liq < previous_liquidity * 0.5:
            add("liquidity_pulled", 35, f"La liquidez cayó de ${previous_liquidity:,.0f} a ${liq:,.0f}.")
        age = m.age_hours(now)
        if age is None:
            unknowns.append("age")
        elif age < 1 / 6:
            add("very_new", 20, f"Pool creado hace {age * 60:.0f} min.")
        elif age < 1:
            add("new", 10, f"Pool creado hace {age * 60:.0f} min.")
        v24 = m.volume.get("h24")
        if v24 and liq and liq > 0 and v24 / liq > 50:
            add("possible_wash_volume", 10, f"Volumen 24h {v24 / liq:.0f}x la liquidez (posible volumen artificial).")
        h1 = m.txns.get("h1") or {}
        buys, sells = h1.get("buys"), h1.get("sells")
        if buys is not None and sells is not None:
            if buys >= 30 and sells == 0:
                add("no_sells", 30, f"{buys} compras y 0 ventas en 1h: posible honeypot.")
                sellability = "restricted"
            elif buys + sells >= 30 and sells / max(buys + sells, 1) < 0.1:
                add("sell_imbalance", 12, "Muy pocas ventas frente a compras en 1h.")
        pc1 = m.price_change.get("h1")
        if pc1 is not None:
            if pc1 > 300:
                add("parabolic_pump", 10, f"Subida de {pc1:.0f}% en 1h (riesgo de pump and dump).")
            elif pc1 < -60:
                add("crash", 25, f"Caída de {pc1:.0f}% en 1h (posible rug/dump).")
        else:
            unknowns.append("price_change_h1")

    if creator_sold_pct is not None and creator_sold_pct > 0.5:
        add("creator_dumped", 25, f"El creador vendió {creator_sold_pct:.0%} de su posición.")
    if sell_route_ok is True and sellability == "unknown":
        sellability = "route_found"
    elif sell_route_ok is False:
        add("no_sell_route", 30, "No se encontró ruta de venta en el agregador.")
        sellability = "restricted"

    score = min(100.0, sum(x.points for x in f) + 4 * len(unknowns))
    level = "bajo" if score < 30 else "medio" if score < 60 else "alto"
    return TokenRisk(round(score, 1), level, f, unknowns, sellability)
