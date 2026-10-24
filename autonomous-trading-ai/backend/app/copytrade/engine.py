"""Paper copy-trading engine (DB backed): turns trader events into simulated orders."""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.base import ChainAdapter
from app.chains.types import WSOL_MINT, TradeEvent
from app.connectors.base import ConnectorError
from app.copytrade.logic import copy_buy_size, sell_fraction
from app.models import FollowSession, Order, Portfolio, Position, Token
from app.risk.engine import EntryRequest, age_seconds, check_entry, exit_triggers
from app.runtime import RuntimeConfig
from app.scanner.service import Scanner
from app.sim.execution import ExecConfig, Fill, MarketContext, simulate
from app.sim.portfolio import (
    apply_buy,
    apply_sell,
    charge_failed,
    find_open_position,
    open_positions,
    valuation,
)

log = logging.getLogger(__name__)


@dataclass
class CopyResult:
    action: str  # bought | sold | rejected | failed | skipped | closed
    order_id: int | None = None
    closed_pnl: float | None = None
    position_closed: bool = False
    detail: str | None = None
    reasons: list[str] | None = None


class CopyEngine:
    def __init__(self, adapter: ChainAdapter, scanner: Scanner, exec_cfg: ExecConfig, seed: int = 42,
                 now_fn: Callable[[], datetime] | None = None):
        self.adapter = adapter
        self.scanner = scanner
        self.exec_cfg = exec_cfg
        self.rng = random.Random(seed)
        self.now = now_fn or (lambda: datetime.now(UTC))

    # ---------------- market data ----------------
    async def market(self, session: AsyncSession, mint: str, max_age_s: float) -> Token | None:
        tok = (await session.execute(select(Token).where(Token.address == mint))).scalar_one_or_none()
        age = age_seconds((tok.market or {}).get("fetched_at"), self.now()) if tok and tok.market else None
        if tok is None or age is None or age > max_age_s / 2:
            try:
                ms = await self.adapter.markets([mint])
                if mint in ms:
                    tok = await self.scanner._upsert(session, ms[mint], None)
                    await session.flush()
            except ConnectorError as exc:
                log.warning("market refresh failed for %s: %s", mint, exc)
        return tok

    async def token_risk(self, session: AsyncSession, tok: Token) -> dict[str, Any]:
        r = tok.risk or {}
        ts = r.get("assessed_at")
        if not r or ts is None or (age_seconds(ts, self.now()) or 1e9) > 1800:
            r = await self.scanner.assess(session, tok)
        return r

    async def _buy_impact(self, mint: str, usd: float) -> float | None:
        try:
            sol = await self.adapter.native_price_usd()
            if not sol:
                return None
            q = await self.adapter.quote(WSOL_MINT, mint, int(usd / sol * 1e9), 300)
            return None if q is None else float(q["price_impact"])
        except (ConnectorError, KeyError, TypeError, ValueError):
            return None

    def _ctx(self, tok: Token, impact: float | None) -> MarketContext:
        mk = tok.market or {}
        return MarketContext(price=mk["price_usd"], liquidity_usd=mk.get("liquidity_usd"),
                             price_change_5m_pct=(mk.get("price_change") or {}).get("m5"),
                             quote_impact=impact, source="jupiter_quote" if impact is not None else (mk.get("sources") or {}).get("market", "market"))

    # ---------------- orders ----------------
    async def _order_exists(self, session: AsyncSession, key: str) -> bool:
        return (await session.execute(select(Order.id).where(Order.idempotency_key == key))).first() is not None

    def _record(self, session: AsyncSession, p: Portfolio, pos: Position | None, side: str, mint: str, fill: Fill | None,
                *, status: str, reason: str, key: str | None, ev: TradeEvent | None, detected_at: datetime | None,
                reject_reason: str | None = None, checks: dict | None = None, requested_usd: float | None = None) -> Order:
        o = Order(portfolio_id=p.id, position_id=pos.id if pos else None, side=side, mint=mint, reason=reason,
                  status=status, reject_reason=reject_reason, idempotency_key=key, risk_checks=checks or {},
                  requested_usd=requested_usd if fill is None else fill.requested_usd,
                  requested_qty=None if fill is None else fill.requested_qty,
                  filled_qty=0 if fill is None else fill.filled_qty, filled_usd=0 if fill is None else fill.filled_usd,
                  reference_price=None if fill is None else fill.reference_price,
                  exec_price=None if fill is None else fill.exec_price, fee_usd=0 if fill is None else fill.fee_usd,
                  slippage_bps=None if fill is None else fill.slippage_bps,
                  latency_ms=None if fill is None else fill.latency_ms,
                  quote_source=None if fill is None else fill.quote_source,
                  trader_price=ev.price_usd if ev else None, trader_signature=ev.signature if ev else None,
                  trader_block_time=ev.block_time if ev else None, detected_at=detected_at, executed_at=self.now())
        session.add(o)
        return o

    async def process(self, session: AsyncSession, p: Portfolio, follow: FollowSession | None, ev: TradeEvent,
                      rt: RuntimeConfig, detected_at: datetime, *, shadow: bool = False) -> CopyResult:
        if ev.kind not in ("BUY", "SELL"):
            return CopyResult("skipped", detail=f"{ev.kind} is not a trade")
        key = f"{p.id}:{ev.signature}:{ev.mint}:{ev.kind}"
        if await self._order_exists(session, key):
            return CopyResult("skipped", detail="duplicate event")
        if ev.kind == "BUY":
            return await self._copy_buy(session, p, follow, ev, rt, detected_at, key, shadow)
        if not rt.copy.copy_sells:
            return CopyResult("skipped", detail="copy_sells disabled")
        return await self._copy_sell(session, p, ev, rt, detected_at, key)

    async def _copy_buy(self, session, p, follow, ev, rt, detected_at, key, shadow) -> CopyResult:
        L = rt.limits
        now = self.now()
        tok = await self.market(session, ev.mint, L.max_data_age_s)
        mk = (tok.market or {}) if tok else {}
        risk = await self.token_risk(session, tok) if tok and mk.get("price_usd") else {}
        val = await valuation(session, p)
        positions = await open_positions(session, p.id)
        existing = next((x for x in positions if x.mint == ev.mint), None)
        desired = copy_buy_size(val.equity, ev.quote_usd, rt.copy)
        impact = await self._buy_impact(ev.mint, desired) if mk.get("price_usd") else None
        created = tok.pool_created_at if tok else None
        req = EntryRequest(
            mint=ev.mint, desired_usd=desired, equity=val.equity, cash=p.cash, exposure_total=val.exposure,
            exposure_token=(existing.qty * (existing.last_price or 0)) if existing else 0.0,
            open_positions=len(positions), already_holding=existing is not None,
            market_age_s=age_seconds(mk.get("fetched_at"), now) if mk.get("price_usd") else None,
            signal_delay_s=(detected_at - ev.block_time).total_seconds(),
            liquidity_usd=mk.get("liquidity_usd"),
            token_age_minutes=(now - created).total_seconds() / 60 if created else None,
            token_risk_score=risk.get("score"), sellability=risk.get("sellability"),
            daily_pnl_pct=val.daily_pnl_pct, drawdown_pct=val.drawdown_pct,
            kill_switch=rt.kill_switch and not shadow, paused=rt.paused and not shadow, expected_impact=impact,
        )
        dec = check_entry(req, L)
        checks = {"decision": dec.to_dict(), "request": {k: v for k, v in req.__dict__.items() if k != "mint"}}
        if not dec.approved:
            self._record(session, p, None, "BUY", ev.mint, None, status="rejected", reason="copy", key=key, ev=ev,
                         detected_at=detected_at, reject_reason="; ".join(dec.reasons), checks=checks, requested_usd=desired)
            return CopyResult("rejected", reasons=dec.reasons, detail=dec.halt)
        fill = simulate("BUY", self._ctx(tok, impact), self.rng, self.exec_cfg, usd=dec.size_usd,
                        max_slippage_bps=L.max_slippage_bps, max_impact=L.max_price_impact_pct / 100)
        pos = existing
        if fill.status == "failed":
            charge_failed(p, None, fill)
            o = self._record(session, p, None, "BUY", ev.mint, fill, status="failed", reason="copy", key=key, ev=ev,
                             detected_at=detected_at, reject_reason=fill.reason, checks=checks)
            await session.flush()
            return CopyResult("failed", o.id, detail=fill.reason)
        if pos is None:
            pos = Position(portfolio_id=p.id, mint=ev.mint, symbol=tok.symbol, source_wallet=ev.wallet,
                           follow_session_id=follow.id if follow else None, opened_at=now,
                           stop_loss_pct=L.stop_loss_pct, take_profit_pct=L.take_profit_pct)
            session.add(pos)
            await session.flush()
        apply_buy(p, pos, fill, now)
        pos.trader_qty += ev.token_amount
        o = self._record(session, p, pos, "BUY", ev.mint, fill, status=fill.status, reason="copy", key=key, ev=ev,
                         detected_at=detected_at, checks=checks)
        await session.flush()
        return CopyResult("bought", o.id, detail=f"{fill.filled_usd:.2f} USD @ {fill.exec_price:.8g}")

    async def _copy_sell(self, session, p, ev, rt, detected_at, key) -> CopyResult:
        pos = await find_open_position(session, p.id, ev.mint, ev.wallet)
        if pos is None:
            self._record(session, p, None, "SELL", ev.mint, None, status="rejected", reason="copy", key=key, ev=ev,
                         detected_at=detected_at, reject_reason="no copied position (trader bought before we followed)")
            return CopyResult("skipped", detail="no matching copied position")
        frac = sell_fraction(ev.token_amount, pos.trader_qty)
        pos.trader_qty = max(0.0, pos.trader_qty - ev.token_amount)
        qty = pos.qty if frac >= 1.0 else pos.qty * frac
        return await self._sell(session, p, pos, qty, rt, "copy_sell", key=key, ev=ev, detected_at=detected_at)

    async def _sell(self, session, p, pos: Position, qty: float, rt: RuntimeConfig, reason: str, *, key=None, ev=None,
                    detected_at=None, copy_signal: bool = True) -> CopyResult:
        L = rt.limits
        tok = await self.market(session, pos.mint, L.max_data_age_s)
        mk = (tok.market or {}) if tok else {}
        if not mk.get("price_usd"):
            self._record(session, p, pos, "SELL", pos.mint, None, status="failed", reason=reason, key=key, ev=ev,
                         detected_at=detected_at, reject_reason="no price available; will retry")
            return CopyResult("failed", detail="no price")
        fill = simulate("SELL", self._ctx(tok, None), self.rng, self.exec_cfg, qty=qty,
                        max_slippage_bps=max(L.max_slippage_bps, 1500) if reason in ("stop_loss", "emergency", "unfollow_exit") else L.max_slippage_bps,
                        max_impact=max(L.max_price_impact_pct / 100, 0.10), copy_signal=copy_signal)
        now = self.now()
        if fill.status == "failed":
            charge_failed(p, pos, fill)
            o = self._record(session, p, pos, "SELL", pos.mint, fill, status="failed", reason=reason, key=key, ev=ev,
                             detected_at=detected_at, reject_reason=fill.reason)
            await session.flush()
            return CopyResult("failed", o.id, detail=fill.reason)
        pnl = apply_sell(p, pos, fill, now, reason)
        o = self._record(session, p, pos, "SELL", pos.mint, fill, status=fill.status, reason=reason, key=key, ev=ev,
                         detected_at=detected_at)
        await session.flush()
        closed = pos.status == "closed"
        return CopyResult("closed" if closed else "sold", o.id, closed_pnl=pos.realized_pnl if closed else None,
                          position_closed=closed, detail=f"pnl {pnl:+.2f} USD")

    async def close_position(self, session: AsyncSession, p: Portfolio, pos: Position, rt: RuntimeConfig,
                             reason: str) -> CopyResult:
        return await self._sell(session, p, pos, pos.qty, rt, reason, copy_signal=False)

    async def mark_to_market(self, session: AsyncSession, p: Portfolio, rt: RuntimeConfig) -> list[tuple[Position, CopyResult]]:
        """Refresh prices of open positions and fire stop-loss / take-profit exits."""
        positions = await open_positions(session, p.id)
        if not positions:
            return []
        try:
            prices = await self.adapter.prices([x.mint for x in positions])
        except ConnectorError:
            prices = {}
        now = self.now()
        out = []
        for pos in positions:
            px = prices.get(pos.mint)
            if px is None:
                continue
            pos.last_price, pos.last_price_at = px, now
            avg = pos.cost_basis / pos.qty if pos.qty > 0 else 0
            trig = exit_triggers(avg, px, rt.limits, pos.stop_loss_pct, pos.take_profit_pct)
            if trig:
                out.append((pos, await self.close_position(session, p, pos, rt, trig)))
        return out
