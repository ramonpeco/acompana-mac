"""Paper portfolio accounting on top of the DB models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EquitySnapshot, Portfolio, Position
from app.sim.execution import Fill

DUST_QTY_FRACTION = 0.001


async def get_or_create_portfolio(session: AsyncSession, name: str, capital: float, kind: str = "main",
                                  tracked_wallet: str | None = None, now: datetime | None = None) -> Portfolio:
    p = (await session.execute(select(Portfolio).where(Portfolio.name == name))).scalar_one_or_none()
    if p is None:
        now = now or datetime.now(UTC)
        today = now.date().isoformat()
        p = Portfolio(created_at=now, name=name, kind=kind, initial_capital=capital, cash=capital, peak_equity=capital,
                      day_start_equity=capital, day_start_date=today, tracked_wallet=tracked_wallet)
        session.add(p)
        await session.flush()
    return p


async def open_positions(session: AsyncSession, portfolio_id: int) -> list[Position]:
    return list((await session.execute(
        select(Position).where(Position.portfolio_id == portfolio_id, Position.status == "open")
    )).scalars().all())


async def find_open_position(session: AsyncSession, portfolio_id: int, mint: str,
                             source_wallet: str | None = None) -> Position | None:
    q = select(Position).where(Position.portfolio_id == portfolio_id, Position.mint == mint, Position.status == "open")
    if source_wallet:
        q = q.where(Position.source_wallet == source_wallet)
    return (await session.execute(q)).scalars().first()


@dataclass
class Valuation:
    equity: float
    cash: float
    exposure: float
    unrealized: float
    drawdown_pct: float
    daily_pnl_pct: float


async def valuation(session: AsyncSession, p: Portfolio) -> Valuation:
    pos = await open_positions(session, p.id)
    exposure = sum(x.qty * (x.last_price or 0) for x in pos)
    unreal = sum(x.qty * (x.last_price or 0) - x.cost_basis for x in pos)
    eq = p.cash + exposure
    dd = (p.peak_equity - eq) / p.peak_equity * 100 if p.peak_equity > 0 else 0
    daily = (eq - p.day_start_equity) / p.day_start_equity * 100 if p.day_start_equity > 0 else 0
    return Valuation(eq, p.cash, exposure, unreal, max(dd, 0.0), daily)


def apply_buy(p: Portfolio, pos: Position, fill: Fill, now: datetime) -> None:
    cost = fill.filled_usd + fill.fee_usd
    p.cash -= cost
    p.fees_paid += fill.fee_usd
    p.slippage_paid += max(0.0, fill.filled_usd - fill.filled_qty * fill.reference_price)
    pos.qty += fill.filled_qty
    pos.cost_basis += cost
    pos.invested_total += cost
    pos.fees += fill.fee_usd
    pos.last_price = fill.exec_price
    pos.last_price_at = now


def apply_sell(p: Portfolio, pos: Position, fill: Fill, now: datetime, reason: str) -> float:
    """Returns realized PnL of this sell."""
    frac = min(1.0, fill.filled_qty / pos.qty) if pos.qty > 0 else 1.0
    cost_part = pos.cost_basis * frac
    proceeds = fill.filled_usd - fill.fee_usd
    pnl = proceeds - cost_part
    p.cash += proceeds
    p.fees_paid += fill.fee_usd
    p.slippage_paid += max(0.0, fill.filled_qty * fill.reference_price - fill.filled_usd)
    pos.qty -= fill.filled_qty
    pos.cost_basis -= cost_part
    pos.proceeds_total += proceeds
    pos.realized_pnl += pnl
    pos.fees += fill.fee_usd
    pos.last_price = fill.exec_price or pos.last_price
    pos.last_price_at = now
    if pos.qty <= max(1e-12, DUST_QTY_FRACTION * (pos.qty + fill.filled_qty)):
        pos.qty = 0.0
        pos.cost_basis = 0.0
        pos.status = "closed"
        pos.closed_at = now
        pos.close_reason = reason
    return pnl


def charge_failed(p: Portfolio, pos: Position | None, fill: Fill) -> None:
    p.cash -= fill.fee_usd
    p.fees_paid += fill.fee_usd
    if pos is not None:
        pos.fees += fill.fee_usd
        pos.cost_basis += fill.fee_usd if pos.status == "open" and pos.qty > 0 else 0


async def snapshot(session: AsyncSession, p: Portfolio, now: datetime | None = None) -> Valuation:
    now = now or datetime.now(UTC)
    v = await valuation(session, p)
    today = now.date().isoformat()
    if p.day_start_date != today:
        p.day_start_date = today
        p.day_start_equity = v.equity
    if v.equity > p.peak_equity:
        p.peak_equity = v.equity
    v = await valuation(session, p)
    session.add(EquitySnapshot(portfolio_id=p.id, ts=now, equity=v.equity, cash=v.cash, exposure=v.exposure,
                               drawdown_pct=v.drawdown_pct))
    return v
