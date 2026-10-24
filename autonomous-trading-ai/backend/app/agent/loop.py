"""Autonomous agent: the always-on loop that ties everything together.

All state lives in the database, so a restart resumes the active follow
session, open positions and shadow portfolios exactly where they were.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.supervisor import AISupervisor
from app.chains.base import ChainAdapter
from app.chains.types import TradeEvent
from app.config import Settings
from app.connectors.base import ConnectorError
from app.copytrade.engine import CopyEngine, CopyResult
from app.copytrade.logic import SessionStats, evaluate_switch
from app.db import session_scope
from app.events import EventBus
from app.models import FollowSession, Order, Portfolio, Position, Token, Wallet, WalletTrade
from app.runtime import RuntimeConfig, kv_get, kv_set, load_runtime
from app.scanner.service import Scanner
from app.sim.execution import ExecConfig
from app.sim.portfolio import get_or_create_portfolio, open_positions, snapshot, valuation
from app.traders.metrics import compute_metrics, compute_round_trips
from app.traders.service import TraderService

log = logging.getLogger(__name__)
MAIN = "main"
SHADOW_DAYS = 7


def _event_from_row(r: WalletTrade) -> TradeEvent:
    return TradeEvent(wallet=r.wallet, signature=r.signature, slot=r.slot, block_time=r.block_time, kind=r.kind,
                      mint=r.mint, token_amount=r.token_amount, quote_mint=r.quote_mint, quote_amount=r.quote_amount,
                      quote_usd=r.quote_usd, price_usd=r.price_usd, usd_is_estimate=r.usd_is_estimate,
                      fee_sol=r.fee_sol or 0, source=r.source)


class Agent:
    def __init__(self, settings: Settings, adapter: ChainAdapter, bus: EventBus, ai: AISupervisor,
                 now_fn: Callable[[], datetime] | None = None):
        self.s = settings
        self.adapter = adapter
        self.bus = bus
        self.ai = ai
        self.now = now_fn or (lambda: datetime.now(UTC))
        self.scanner = Scanner(adapter, now_fn=self.now)
        self.traders = TraderService(adapter, now_fn=self.now)
        self.engine = CopyEngine(adapter, self.scanner, ExecConfig(
            latency_ms=settings.sim_latency_ms, failure_rate=settings.sim_failure_rate,
            dex_fee_bps=settings.sim_dex_fee_bps, priority_fee_usd=settings.sim_priority_fee_usd),
            seed=settings.sim_seed, now_fn=self.now)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_run: dict[str, datetime] = {}
        self.status: dict[str, Any] = {"state": "stopped", "last_tick": None, "last_error": None, "ticks": 0,
                                       "consecutive_errors": 0}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="agent-loop")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=30)
            except TimeoutError:
                self._task.cancel()
        self.status["state"] = "stopped"

    async def _run(self) -> None:
        self.status["state"] = "running"
        async with session_scope() as session:
            await self.bus.emit(session, "info", "agent_started", f"Agente iniciado (datos: {self.adapter.data_label}, modo PAPER)")
        while not self._stop.is_set():
            try:
                await self.tick()
                self.status["consecutive_errors"] = 0
            except Exception as exc:  # the loop must survive anything; errors are surfaced
                self.status["consecutive_errors"] += 1
                self.status["last_error"] = f"{type(exc).__name__}: {exc}"
                log.error("agent tick failed: %s\n%s", exc, traceback.format_exc())
                try:
                    async with session_scope() as session:
                        await self.bus.emit(session, "error", "tick_error", str(exc)[:500])
                except Exception:
                    pass
            delay = self.s.agent_tick_seconds * min(2 ** self.status["consecutive_errors"], 16)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass
        async with session_scope() as session:
            await self.bus.emit(session, "info", "agent_stopped", "Agente detenido")

    def _due(self, job: str, seconds: float) -> bool:
        last = self.last_run.get(job)
        return last is None or (self.now() - last).total_seconds() >= seconds

    def _mark(self, job: str) -> None:
        self.last_run[job] = self.now()

    # ------------------------------------------------------------------ main tick
    async def tick(self) -> None:
        now = self.now()
        async with session_scope() as session:
            rt = await load_runtime(session)
            await kv_set(session, "agent_heartbeat", now.isoformat())
            main = await get_or_create_portfolio(session, MAIN, self.s.initial_capital_usd, now=now)

            if self._due("scanner", self.s.scanner_interval_seconds):
                await self._job_scanner(session)
                self._mark("scanner")
            if self._due("discovery", self.s.discovery_interval_minutes * 60):
                await self._job_discovery(session)
                self._mark("discovery")
            if self._due("ranking", self.s.ranking_interval_minutes * 60):
                await self._job_ranking(session)
                self._mark("ranking")

            follow = await self.active_follow(session, main.id)
            if follow:
                await self._follow_tick(session, main, follow, rt)
            await self._risk_tick(session, main, rt)
            follow = await self.active_follow(session, main.id)
            if follow:
                await self._maybe_switch(session, main, follow, rt)
            follow = await self.active_follow(session, main.id)
            if follow is None and not rt.paused and not rt.kill_switch:
                await self._select_and_follow(session, main, rt, reason="no_active_trader")
            await self._shadow_tick(session, rt)
            if self._due("snapshot", 60):
                v = await snapshot(session, main, now)
                self.bus.publish({"type": "equity", "ts": now.isoformat(), "equity": v.equity, "drawdown_pct": v.drawdown_pct})
                self._mark("snapshot")
            if await self._new_day(session):
                await self._job_daily(session, main)
        self.status.update(last_tick=now.isoformat(), ticks=self.status["ticks"] + 1, state="running")

    # ------------------------------------------------------------------ jobs
    async def _job_scanner(self, session: AsyncSession) -> None:
        stats = await self.scanner.refresh(session)
        if stats["errors"]:
            await self.bus.emit(session, "warn", "scanner_errors", f"Escáner con errores: {stats['errors'][:3]}")
        if self._due("dex_paid", 600):
            await self.scanner.check_dex_paid(session)
            self._mark("dex_paid")
        # assess risk for a few unassessed tokens per pass (RPC budget)
        toks = (await session.execute(select(Token).order_by(Token.updated_at.desc()).limit(80))).scalars().all()
        for t in [t for t in toks if not t.risk][:5]:
            await self.scanner.assess(session, t)

    async def _job_discovery(self, session: AsyncSession) -> None:
        res = await self.traders.discover(session)
        await self.bus.emit(session, "info", "discovery", f"Descubrimiento: {len(res['added'])} wallets nuevas de {res['candidates']} candidatas",
                            added=res["added"][:20], errors=res["errors"][:3])

    async def _job_ranking(self, session: AsyncSession, max_wallets: int = 20) -> None:
        wallets = (await session.execute(
            select(Wallet).where(Wallet.status != "retired").order_by(Wallet.last_synced_at.asc().nullsfirst()).limit(max_wallets)
        )).scalars().all()
        for w in wallets:
            try:
                await self.traders.sync_wallet(session, w, backfill_pages=0 if w.history_complete else 3)
            except ConnectorError as exc:
                log.info("sync %s failed: %s", w.address, exc)
        ranking = await self.traders.rank(session, as_of=self.now())
        eligible = [r for r in ranking if r["eligible"]]
        await self.bus.emit(session, "info", "ranking", f"Ranking actualizado: {len(eligible)} elegibles de {len(ranking)}",
                            top=[{"wallet": r["wallet"], "score": r["score"]} for r in eligible[:5]])

    async def _new_day(self, session: AsyncSession) -> bool:
        today = self.now().date().isoformat()
        last = await kv_get(session, "last_daily_report")
        if last == today:
            return False
        await kv_set(session, "last_daily_report", today)
        return last is not None  # skip on very first boot

    async def _job_daily(self, session: AsyncSession, main: Portfolio) -> None:
        v = await valuation(session, main)
        since = self.now() - timedelta(days=1)
        closed = (await session.execute(select(func.count(Position.id)).where(
            Position.portfolio_id == main.id, Position.closed_at >= since))).scalar_one()
        switches = (await session.execute(select(func.count(FollowSession.id)).where(FollowSession.ended_at >= since))).scalar_one()
        stats = {"equity": v.equity, "initial_capital": main.initial_capital, "daily_pnl_pct": v.daily_pnl_pct,
                 "drawdown_pct": v.drawdown_pct, "closed_today": closed, "switches_today": switches,
                 "fees_paid": main.fees_paid, "slippage_paid": main.slippage_paid,
                 "shadow": await self.shadow_comparison(session)}
        rep = await self.ai.daily_report(session, stats)
        await self.bus.emit(session, "info", "daily_report", rep.get("text", ""), report=rep, stats=stats)
        losers = (await session.execute(select(Position).where(
            Position.portfolio_id == main.id, Position.closed_at >= since, Position.realized_pnl < 0))).scalars().all()
        if losers:
            await self.ai.analyse_failures(session, {
                "losing_positions": [{"mint": p.mint, "pnl": p.realized_pnl, "reason": p.close_reason,
                                      "held_min": ((p.closed_at - p.opened_at).total_seconds() / 60) if p.closed_at else None}
                                     for p in losers[:50]],
                "shadow": stats["shadow"]})

    # ------------------------------------------------------------------ following
    async def active_follow(self, session: AsyncSession, portfolio_id: int) -> FollowSession | None:
        return (await session.execute(select(FollowSession).where(
            FollowSession.portfolio_id == portfolio_id, FollowSession.ended_at.is_(None)
        ).order_by(FollowSession.id.desc()))).scalars().first()

    async def _follow_tick(self, session: AsyncSession, main: Portfolio, follow: FollowSession, rt: RuntimeConfig) -> None:
        w = (await session.execute(select(Wallet).where(Wallet.address == follow.wallet))).scalar_one()
        try:
            events = await self.traders.sync_wallet(session, w, backfill_pages=0)
        except ConnectorError as exc:
            await self.bus.emit(session, "warn", "sync_failed", f"No se pudo sincronizar el trader: {exc}")
            return
        detected = self.now()
        for ev in events:
            if ev.block_time < follow.started_at:
                continue
            res = await self.engine.process(session, main, follow, ev, rt, detected)
            await self._after_copy(session, main, follow, ev, res)

    async def _after_copy(self, session, main, follow, ev, res: CopyResult) -> None:
        if res.action == "bought":
            await self.bus.emit(session, "info", "position_opened", f"Compra simulada {ev.mint[:6]}…: {res.detail}",
                                mint=ev.mint, wallet=ev.wallet, order_id=res.order_id)
        elif res.action == "rejected":
            await self.bus.emit(session, "info", "order_rejected", f"Copia rechazada por riesgo ({ev.mint[:6]}…): {', '.join(res.reasons or [])}",
                                mint=ev.mint, reasons=res.reasons)
        elif res.action == "failed":
            await self.bus.emit(session, "warn", "order_failed", f"Ejecución simulada fallida ({ev.mint[:6]}…): {res.detail}")
        if res.position_closed:
            await self._on_position_closed(session, follow, res.closed_pnl or 0.0, ev.mint, "copy_sell")

    async def _on_position_closed(self, session, follow: FollowSession | None, pnl: float, mint: str, reason: str) -> None:
        if follow is not None:
            st = self._stats(follow)
            st.on_close(pnl)
            self._store_stats(follow, st)
        await self.bus.emit(session, "info", "position_closed", f"Posición cerrada {mint[:6]}… ({reason}): {pnl:+.2f} USD",
                            mint=mint, pnl=pnl, reason=reason, consecutive_losses=follow.consecutive_losses if follow else None)

    def _stats(self, f: FollowSession) -> SessionStats:
        sel = f.selection or {}
        st = SessionStats(started_at=f.started_at, closed_trades=f.closed_trades, wins=f.wins, losses=f.losses,
                          consecutive_losses=f.consecutive_losses, realized_pnl=f.realized_pnl, peak_pnl=f.peak_pnl,
                          equity_at_start=sel.get("equity_at_start", 0.0), day_pnl=sel.get("day_pnl", 0.0),
                          score_at_start=f.score_at_start, trades_per_day_at_start=sel.get("trades_per_day"),
                          avg_trade_usd_at_start=sel.get("avg_trade_usd"))
        day = sel.get("day_pnl_date")
        if day != self.now().date().isoformat():
            st.day_pnl = 0.0
        return st

    def _store_stats(self, f: FollowSession, st: SessionStats) -> None:
        f.closed_trades, f.wins, f.losses = st.closed_trades, st.wins, st.losses
        f.consecutive_losses, f.realized_pnl, f.peak_pnl = st.consecutive_losses, st.realized_pnl, st.peak_pnl
        f.selection = {**(f.selection or {}), "day_pnl": st.day_pnl, "day_pnl_date": self.now().date().isoformat()}

    async def _risk_tick(self, session: AsyncSession, main: Portfolio, rt: RuntimeConfig) -> None:
        follow = await self.active_follow(session, main.id)
        for pos, res in await self.engine.mark_to_market(session, main, rt):
            if res.position_closed:
                f = follow if follow and pos.follow_session_id == follow.id else None
                await self._on_position_closed(session, f, res.closed_pnl or 0.0, pos.mint, pos.close_reason or "exit")
        v = await valuation(session, main)
        if v.drawdown_pct >= rt.limits.max_drawdown_pct and not rt.paused:
            await kv_set(session, "agent_paused", True, "risk_engine")
            await self.bus.emit(session, "alert", "risk_alert",
                                f"Drawdown {v.drawdown_pct:.1f}% ≥ límite {rt.limits.max_drawdown_pct}%: nuevas entradas detenidas. "
                                "Requiere revisión del administrador.", drawdown=v.drawdown_pct)
        elif -v.daily_pnl_pct >= rt.limits.max_daily_loss_pct:
            flag = f"daily_loss_alert:{self.now().date().isoformat()}"
            if not await kv_get(session, flag):
                await kv_set(session, flag, True)
                await self.bus.emit(session, "alert", "risk_alert",
                                    f"Pérdida diaria {v.daily_pnl_pct:.1f}%: entradas bloqueadas hasta mañana.")

    async def _trader_now(self, session: AsyncSession, wallet: str) -> dict[str, Any]:
        since = self.now() - timedelta(days=3)
        rows = (await session.execute(select(WalletTrade).where(WalletTrade.wallet == wallet, WalletTrade.block_time >= since))).scalars().all()
        m = compute_metrics(rows, now=self.now())
        mints = {r.mint for r in rows if r.kind == "BUY"}
        toks = (await session.execute(select(Token).where(Token.address.in_(mints)))).scalars().all() if mints else []
        liqs = [(t.market or {}).get("liquidity_usd") for t in toks if (t.market or {}).get("liquidity_usd")]
        return {"trades_per_day": m.trades_per_day, "avg_trade_usd": m.avg_trade_usd,
                "avg_liquidity": statistics.median(liqs) if liqs else None, "rows": rows}

    async def _maybe_switch(self, session: AsyncSession, main: Portfolio, follow: FollowSession, rt: RuntimeConfig) -> None:
        w = (await session.execute(select(Wallet).where(Wallet.address == follow.wallet))).scalar_one()
        now_info = await self._trader_now(session, w.address)
        st = self._stats(follow)
        if rt.switch.loss_basis == "trader":
            trips, _ = compute_round_trips([r for r in now_info["rows"] if r.block_time >= follow.started_at])
            streak = 0
            for t in sorted(trips, key=lambda t: t.closed_at):
                streak = streak + 1 if t.pnl <= 0 else 0
            st.consecutive_losses = streak
            st.closed_trades = max(st.closed_trades, len(trips))
        dec = evaluate_switch(st, rt.switch, self.now(), last_trader_trade_at=w.last_trade_at or follow.started_at,
                              current_score=w.score, trades_per_day_now=now_info["trades_per_day"],
                              avg_trade_usd_now=now_info["avg_trade_usd"], recent_avg_liquidity=now_info["avg_liquidity"],
                              min_liquidity=rt.limits.min_liquidity_usd)
        if dec.switch:
            await self.unfollow(session, main, follow, rt, dec.reason or "rule", dec.detail)
            await self._select_and_follow(session, main, rt, reason=dec.reason or "rule", previous=follow)

    async def unfollow(self, session: AsyncSession, main: Portfolio, follow: FollowSession, rt: RuntimeConfig,
                       reason: str, detail: str | None = None) -> None:
        now = self.now()
        follow.ended_at, follow.end_reason = now, reason
        v = await valuation(session, main)
        shadow = await get_or_create_portfolio(session, f"shadow:{follow.wallet[:8]}:{follow.id}", v.equity, "shadow",
                                               follow.wallet, now=now)
        follow.shadow_portfolio_id = shadow.id
        w = (await session.execute(select(Wallet).where(Wallet.address == follow.wallet))).scalar_one()
        w.status = "candidate"
        if rt.copy.exit_on_unfollow:
            for pos in await open_positions(session, main.id):
                if pos.follow_session_id == follow.id:
                    res = await self.engine.close_position(session, main, pos, rt, "unfollow_exit")
                    if res.position_closed:
                        await self.bus.emit(session, "info", "position_closed",
                                            f"Cierre al dejar de seguir {pos.mint[:6]}…: {pos.realized_pnl:+.2f} USD",
                                            mint=pos.mint, pnl=pos.realized_pnl, reason="unfollow_exit")
        await self.bus.emit(session, "warn", "trader_unfollowed",
                            f"Dejo de seguir {follow.wallet[:6]}…: {detail or reason}", wallet=follow.wallet, reason=reason,
                            session_pnl=follow.realized_pnl, shadow_portfolio=shadow.name)

    async def _recent_switches(self, session: AsyncSession) -> int:
        since = self.now() - timedelta(days=1)
        return (await session.execute(select(func.count(FollowSession.id)).where(FollowSession.ended_at >= since))).scalar_one()

    async def candidates(self, session: AsyncSession, rt: RuntimeConfig) -> list[Wallet]:
        cooldown_since = self.now() - timedelta(days=rt.switch.cooldown_days)
        recent = set((await session.execute(select(FollowSession.wallet).where(FollowSession.ended_at >= cooldown_since))).scalars().all())
        ws = (await session.execute(select(Wallet).where(Wallet.score.is_not(None)))).scalars().all()
        ws = [w for w in ws if w.address not in recent and w.status != "retired"]
        if rt.switch.selection == "top_pnl":  # strategy A: plain "number one by realized PnL"
            ws = [w for w in ws if (w.metrics or {}).get("n_closed", 0) >= 3]
            return sorted(ws, key=lambda w: (w.metrics or {}).get("realized_pnl") or 0, reverse=True)
        return sorted([w for w in ws if w.status == "candidate"], key=lambda w: w.score or 0, reverse=True)

    async def _select_and_follow(self, session: AsyncSession, main: Portfolio, rt: RuntimeConfig, *, reason: str,
                                 previous: FollowSession | None = None) -> FollowSession | None:
        if await self._recent_switches(session) >= rt.switch.max_switches_per_day:
            flag = f"switch_cap:{self.now().date().isoformat()}"
            if not await kv_get(session, flag):
                await kv_set(session, flag, True)
                await self.bus.emit(session, "alert", "risk_alert",
                                    f"Límite de {rt.switch.max_switches_per_day} cambios de trader/día alcanzado: sin trader hasta mañana.")
            return None
        cands = await self.candidates(session, rt)
        if not cands:
            if self._due("no_candidates_warn", 3600):
                await self.bus.emit(session, "warn", "no_candidates", "No hay traders elegibles todavía; el agente sigue descubriendo y clasificando.")
                self._mark("no_candidates_warn")
            return None
        chosen, ai_review = cands[0], None
        if rt.switch.selection == "ai_supervised":
            top = cands[:5]
            ai_review = await self.ai.review_candidates(session, [
                {"wallet": w.address, "score": w.score, "metrics": {k: (w.metrics or {}).get(k) for k in (
                    "roi", "win_rate", "n_closed", "profit_factor", "max_drawdown_pct", "median_hold_hours",
                    "trades_per_day", "top_token_share", "data_quality", "recent_roi", "early_roi", "avg_liquidity_usd")}}
                for w in top])
            approved = {r["wallet"] for r in ai_review.get("reviews", []) if r.get("verdict") == "approve"}
            pref = ai_review.get("preferred_wallet")
            pick = next((w for w in top if w.address == pref and pref in approved), None) or next(
                (w for w in top if w.address in approved), None)
            if pick is None:
                await self.bus.emit(session, "warn", "ai_veto_all", "La IA vetó a todos los candidatos; se espera a la próxima clasificación.",
                                    review=ai_review)
                return None
            chosen = pick
        # baseline sync: everything the trader did before now is history, never copied
        try:
            await self.traders.sync_wallet(session, chosen, backfill_pages=0)
        except ConnectorError as exc:
            await self.bus.emit(session, "warn", "sync_failed", f"No se pudo sincronizar {chosen.address[:6]}…: {exc}")
            return None
        v = await valuation(session, main)
        m = chosen.metrics or {}
        selection = {"mode": rt.switch.selection, "reason": reason, "score": chosen.score,
                     "components": m.get("components"), "roi": m.get("roi"), "win_rate": m.get("win_rate"),
                     "n_closed": m.get("n_closed"), "equity_at_start": v.equity, "trades_per_day": m.get("trades_per_day"),
                     "avg_trade_usd": m.get("avg_trade_usd"), "alternatives": [{"wallet": w.address, "score": w.score} for w in cands[1:4]],
                     "ai_review": ai_review, "previous": previous.wallet if previous else None}
        f = FollowSession(portfolio_id=main.id, wallet=chosen.address, started_at=self.now(), selection=selection,
                          score_at_start=chosen.score, strategy_version=rt.strategy_version)
        session.add(f)
        chosen.status = "followed"
        await session.flush()
        ctx = {"selected": chosen.address, **{k: selection[k] for k in ("mode", "reason", "score", "roi", "win_rate", "n_closed", "alternatives")},
               "previous": selection["previous"],
               "previous_end_reason": previous.end_reason if previous else None}
        fallback = (f"Seleccionado {chosen.address[:6]}… (puntuación {chosen.score:.1f}) por {reason}. "
                    f"ROI histórico estimado {((m.get('roi') or 0) * 100):.1f}% en {m.get('n_closed')} operaciones cerradas; "
                    "resultados pasados no garantizan resultados futuros.")
        expl = await self.ai.explain(session, "switch_explanation", ctx, fallback)
        f.selection = {**selection, "explanation": expl}
        kind = "trader_switched" if previous else "trader_selected"
        await self.bus.emit(session, "info", kind, expl.get("text") or fallback, wallet=chosen.address, score=chosen.score,
                            previous=selection["previous"])
        return f

    # ------------------------------------------------------------------ shadows
    async def _shadow_tick(self, session: AsyncSession, rt: RuntimeConfig) -> None:
        shadows = (await session.execute(select(Portfolio).where(Portfolio.kind == "shadow", Portfolio.active.is_(True)))).scalars().all()
        for sp in shadows:
            if (self.now() - sp.created_at) > timedelta(days=SHADOW_DAYS):
                for pos in await open_positions(session, sp.id):
                    await self.engine.close_position(session, sp, pos, rt, "shadow_end")
                sp.active = False
                continue
            w = (await session.execute(select(Wallet).where(Wallet.address == sp.tracked_wallet))).scalar_one_or_none()
            if w is None:
                continue
            # the followed wallet syncs itself; others sync here (shared WalletTrade rows => no double fetching)
            try:
                await self.traders.sync_wallet(session, w, backfill_pages=0)
            except ConnectorError:
                continue
            done = set((await session.execute(select(Order.trader_signature).where(Order.portfolio_id == sp.id))).scalars().all())
            rows = (await session.execute(select(WalletTrade).where(
                WalletTrade.wallet == w.address, WalletTrade.block_time >= sp.created_at,
                WalletTrade.kind.in_(("BUY", "SELL"))).order_by(WalletTrade.block_time))).scalars().all()
            for r in rows:
                if r.signature in done:
                    continue
                await self.engine.process(session, sp, None, _event_from_row(r), rt, r.detected_at, shadow=True)
            await self.engine.mark_to_market(session, sp, rt)
            if self._due(f"snap:{sp.id}", 300):
                await snapshot(session, sp, self.now())
                self._mark(f"snap:{sp.id}")

    async def shadow_comparison(self, session: AsyncSession) -> list[dict[str, Any]]:
        """For each dropped trader: our main return vs the return if we had kept copying them."""
        out = []
        sessions = (await session.execute(select(FollowSession).where(FollowSession.shadow_portfolio_id.is_not(None))
                                          .order_by(FollowSession.ended_at.desc()).limit(50))).scalars().all()
        main = (await session.execute(select(Portfolio).where(Portfolio.name == MAIN))).scalar_one_or_none()
        if main is None:
            return out
        main_now = (await valuation(session, main)).equity
        for f in sessions:
            sp = await session.get(Portfolio, f.shadow_portfolio_id)
            if sp is None:
                continue
            sv = await valuation(session, sp)
            shadow_ret = (sv.equity / sp.initial_capital - 1) * 100
            from app.models import EquitySnapshot  # local import to keep module deps light
            main_then = (await session.execute(select(EquitySnapshot.equity).where(
                EquitySnapshot.portfolio_id == main.id, EquitySnapshot.ts <= sp.created_at + timedelta(minutes=2))
                .order_by(EquitySnapshot.ts.desc()).limit(1))).scalar_one_or_none() or sp.initial_capital
            main_ret = (main_now / main_then - 1) * 100
            out.append({"wallet": f.wallet, "ended_at": f.ended_at.isoformat() if f.ended_at else None,
                        "end_reason": f.end_reason, "shadow_return_pct": round(shadow_ret, 3),
                        "main_return_since_pct": round(main_ret, 3),
                        "switch_was_beneficial": main_ret >= shadow_ret, "shadow_active": sp.active,
                        "shadow_trades": (await session.execute(select(func.count(Order.id)).where(
                            Order.portfolio_id == sp.id, Order.status.in_(("filled", "partial"))))).scalar_one()})
        return out
