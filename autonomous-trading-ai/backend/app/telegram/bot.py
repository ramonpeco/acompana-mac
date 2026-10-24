"""Telegram bot (Bot API long polling: getUpdates / sendMessage).

Security:
* only chat ids in TELEGRAM_ADMIN_CHAT_IDS (and, read-only, TELEGRAM_NOTIFY_CHAT_IDS)
  get answers; everyone else is ignored and the attempt is audited;
* administrative commands (/pause, /resume) require an admin chat id;
* there is deliberately NO command that enables live trading or changes risk limits.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.db import session_scope
from app.models import AuditLog, FollowSession, Portfolio, Position
from app.runtime import kv_set, load_runtime
from app.sim.portfolio import open_positions, valuation

log = logging.getLogger(__name__)

HELP = (
    "<b>Autonomous Trading AI</b> (PAPER TRADING)\n"
    "/status – estado del agente\n/balance – patrimonio simulado\n/trader – trader seguido\n"
    "/positions – posiciones abiertas\n/history – últimas operaciones cerradas\n/risk – límites de riesgo\n"
    "/report – último informe\n/pause – pausar nuevas entradas (admin)\n/resume – reanudar (admin)"
)


class TelegramBot:
    def __init__(self, token: str, admin_ids: set[int], notify_ids: set[int], agent_status: Any = None):
        self.token = token
        self.admin_ids = admin_ids
        self.notify_ids = notify_ids | admin_ids
        self.agent_status = agent_status
        self.base = f"https://api.telegram.org/bot{token}"
        self.client = httpx.AsyncClient(timeout=40)
        self.offset = 0
        self._task: asyncio.Task | None = None
        self.healthy = False
        self.last_error: str | None = None

    async def send(self, chat_id: int, text: str) -> None:
        try:
            r = await self.client.post(f"{self.base}/sendMessage", json={
                "chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            self.last_error = str(exc)
            log.warning("telegram send failed: %s", exc)

    async def notify(self, kind: str, message: str, data: dict) -> None:
        icon = {"risk_alert": "⚠️", "trader_switched": "🔄", "trader_selected": "🎯", "position_opened": "🟢",
                "position_closed": "🔴" if (data.get("pnl") or 0) < 0 else "✅", "daily_report": "📊",
                "kill_switch": "🛑"}.get(kind, "ℹ️")
        for cid in self.notify_ids:
            await self.send(cid, f"{icon} <b>{kind}</b> [PAPER]\n{html.escape(message)}")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll(), name="telegram-poll")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.client.aclose()

    async def _poll(self) -> None:
        backoff = 2
        while True:
            try:
                r = await self.client.get(f"{self.base}/getUpdates", params={"timeout": 30, "offset": self.offset})
                r.raise_for_status()
                self.healthy, backoff = True, 2
                for upd in r.json().get("result", []):
                    self.offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    chat_id = (msg.get("chat") or {}).get("id")
                    if text.startswith("/") and chat_id is not None:
                        await self.handle(chat_id, text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.healthy, self.last_error = False, str(exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    async def handle(self, chat_id: int, text: str) -> str | None:
        cmd = text.split()[0].split("@")[0].lower()
        if chat_id not in self.notify_ids:
            async with session_scope() as s:
                s.add(AuditLog(actor=f"telegram:{chat_id}", action="unauthorized_command", details={"cmd": cmd}))
            return None
        admin = chat_id in self.admin_ids
        reply = await self.render(cmd, admin, chat_id)
        await self.send(chat_id, reply)
        return reply

    async def render(self, cmd: str, admin: bool, chat_id: int) -> str:
        async with session_scope() as s:
            main = (await s.execute(select(Portfolio).where(Portfolio.name == "main"))).scalar_one_or_none()
            rt = await load_runtime(s)
            if cmd in ("/start", "/help"):
                return HELP
            if main is None:
                return "El agente todavía no ha creado la cartera."
            v = await valuation(s, main)
            if cmd == "/status":
                st = self.agent_status() if callable(self.agent_status) else {}
                return (f"Estado: <b>{st.get('state', '?')}</b> · último ciclo {st.get('last_tick', '-')}\n"
                        f"Pausado: {rt.paused} · Kill switch: {rt.kill_switch}\nPerfil: {rt.profile} · Modo: PAPER")
            if cmd == "/balance":
                ret = (v.equity / main.initial_capital - 1) * 100
                return (f"Patrimonio: <b>{v.equity:.2f} USD</b> ({ret:+.2f}%)\nEfectivo: {v.cash:.2f} · Expuesto: {v.exposure:.2f}\n"
                        f"Drawdown: {v.drawdown_pct:.2f}% · Día: {v.daily_pnl_pct:+.2f}%\nComisiones: {main.fees_paid:.2f} · Slippage: {main.slippage_paid:.2f}")
            if cmd == "/trader":
                f = (await s.execute(select(FollowSession).where(FollowSession.ended_at.is_(None), FollowSession.portfolio_id == main.id))).scalars().first()
                if not f:
                    return "Sin trader seguido ahora mismo."
                return (f"Siguiendo <code>{f.wallet}</code>\nDesde {f.started_at:%Y-%m-%d %H:%M} UTC · puntuación inicial {f.score_at_start or 0:.1f}\n"
                        f"Cerradas {f.closed_trades} (G {f.wins}/P {f.losses}) · racha pérdidas {f.consecutive_losses}\nPnL sesión {f.realized_pnl:+.2f} USD")
            if cmd == "/positions":
                ps = await open_positions(s, main.id)
                if not ps:
                    return "Sin posiciones abiertas."
                return "\n".join(f"{p.symbol or p.mint[:6]}: {p.qty:.4g} · coste {p.cost_basis:.2f} · valor {(p.qty * (p.last_price or 0)):.2f}" for p in ps)
            if cmd == "/history":
                ps = (await s.execute(select(Position).where(Position.portfolio_id == main.id, Position.status == "closed")
                                      .order_by(Position.closed_at.desc()).limit(10))).scalars().all()
                return "\n".join(f"{p.closed_at:%m-%d %H:%M} {p.symbol or p.mint[:6]} {p.realized_pnl:+.2f} ({p.close_reason})" for p in ps) or "Sin historial."
            if cmd == "/risk":
                L = rt.limits
                return (f"Perfil {rt.profile}\nMáx. por operación {L.max_trade_pct}% · por token {L.max_token_pct}% · total {L.max_total_exposure_pct}%\n"
                        f"Pérdida diaria máx. {L.max_daily_loss_pct}% · DD máx. {L.max_drawdown_pct}%\nSL {L.stop_loss_pct}% · TP {L.take_profit_pct}%\n"
                        f"Cambio tras {rt.switch.consecutive_losses} pérdidas consecutivas")
            if cmd == "/report":
                from app.models import AgentEvent
                ev = (await s.execute(select(AgentEvent).where(AgentEvent.kind == "daily_report").order_by(AgentEvent.ts.desc()).limit(1))).scalar_one_or_none()
                if ev:
                    return html.escape(ev.message)
                since = datetime.now(UTC) - timedelta(days=1)
                n = len((await s.execute(select(Position.id).where(Position.portfolio_id == main.id, Position.closed_at >= since))).all())
                return f"Aún no hay informe diario. Últimas 24h: {n} posiciones cerradas; patrimonio {v.equity:.2f} USD."
            if cmd in ("/pause", "/resume"):
                if not admin:
                    s.add(AuditLog(actor=f"telegram:{chat_id}", action="forbidden_admin_command", details={"cmd": cmd}))
                    return "Comando reservado a administradores."
                await kv_set(s, "agent_paused", cmd == "/pause", f"telegram:{chat_id}")
                s.add(AuditLog(actor=f"telegram:{chat_id}", action="pause" if cmd == "/pause" else "resume", details={}))
                return "Nuevas entradas en pausa." if cmd == "/pause" else "Agente reanudado."
            return "Comando no reconocido. /help"
