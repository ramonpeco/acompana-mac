"""Trader performance metrics from reconstructed wallet actions.

Cost basis uses FIFO lots. Lots acquired by transfer/airdrop or before the
observed history window have *unknown* cost: sells of those are excluded from
realized PnL and lower the data-quality score instead of inflating returns.
A "closed trade" is a round trip on a token: from a flat position, through
buys/sells, back to (almost) flat.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Protocol


class TradeLike(Protocol):
    kind: str
    mint: str
    token_amount: float
    quote_usd: float | None
    block_time: datetime
    signature: str


@dataclass
class RoundTrip:
    mint: str
    opened_at: datetime
    closed_at: datetime
    invested: float
    proceeds: float
    pnl: float
    ret: float
    hold_hours: float
    buys: int
    sells: int


@dataclass
class TraderMetrics:
    n_actions: int = 0
    n_buys: int = 0
    n_sells: int = 0
    n_transfers: int = 0
    n_closed: int = 0
    n_open_positions: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float | None = None
    invested_closed: float = 0.0
    roi: float | None = None  # realized pnl / capital invested in closed trades
    win_rate: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    avg_pnl: float | None = None
    profit_factor: float | None = None
    max_drawdown_pct: float | None = None
    ret_volatility: float | None = None
    sharpe_like: float | None = None
    avg_hold_hours: float | None = None
    median_hold_hours: float | None = None
    trades_per_day: float | None = None
    avg_trade_usd: float | None = None
    concentration_hhi: float | None = None  # 0..1 across tokens by invested capital
    top_token_share: float | None = None
    open_exposure: dict[str, float] = field(default_factory=dict)
    positive_day_ratio: float | None = None
    max_loss_streak: int = 0
    current_loss_streak: int = 0
    history_days: float = 0.0
    first_trade_at: str | None = None
    last_trade_at: str | None = None
    known_basis_ratio: float | None = None  # sold qty with known cost / all sold qty
    usd_known_ratio: float | None = None
    data_quality: float = 0.0  # 0..1
    recent_roi: float | None = None  # last 1/3 of closed trades
    early_roi: float | None = None
    verified: bool = False  # True only with sufficient, complete data

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Lot:
    qty: float
    cost: float | None  # USD; None = unknown basis


def compute_round_trips(trades: list[TradeLike]) -> tuple[list[RoundTrip], dict[str, Any]]:
    lots: dict[str, list[_Lot]] = defaultdict(list)
    open_trip: dict[str, dict[str, Any]] = {}
    trips: list[RoundTrip] = []
    sold_known = sold_total = 0.0
    tainted: set[str] = set()  # mints whose current trip touched unknown-basis lots
    for t in sorted(trades, key=lambda x: x.block_time):
        m = t.mint
        held = sum(lot.qty for lot in lots[m])
        if t.kind in ("BUY", "TRANSFER_IN"):
            if m not in open_trip:
                open_trip[m] = {"opened_at": t.block_time, "invested": 0.0, "proceeds": 0.0, "buys": 0, "sells": 0, "peak": 0.0}
                if held > 1e-12:
                    tainted.add(m)  # tokens held from before the observed window
            cost = t.quote_usd if t.kind == "BUY" else None
            lots[m].append(_Lot(t.token_amount, cost))
            trip = open_trip[m]
            if t.kind == "BUY":
                trip["buys"] += 1
                if cost is None:
                    tainted.add(m)
                else:
                    trip["invested"] += cost
            else:
                tainted.add(m)
            trip["peak"] = max(trip["peak"], held + t.token_amount)
        elif t.kind in ("SELL", "TRANSFER_OUT"):
            remaining = t.token_amount
            if t.kind == "SELL":
                sold_total += t.token_amount
            known_cost = 0.0
            known_qty = 0.0
            while remaining > 1e-12 and lots[m]:
                lot = lots[m][0]
                take = min(lot.qty, remaining)
                if lot.cost is not None:
                    part = lot.cost * take / lot.qty if lot.qty else 0.0
                    known_cost += part
                    known_qty += take
                    lot.cost -= part
                lot.qty -= take
                remaining -= take
                if lot.qty <= 1e-12:
                    lots[m].pop(0)
            if t.kind == "SELL":
                sold_known += known_qty if t.quote_usd is not None else 0.0
                if remaining > 1e-12:
                    tainted.add(m)  # sold more than we saw bought: pre-window or transferred-in tokens
            trip = open_trip.get(m)
            if trip is None:
                continue
            if t.kind == "SELL":
                trip["sells"] += 1
                if t.quote_usd is not None:
                    trip["proceeds"] += t.quote_usd
                else:
                    tainted.add(m)
            now_held = sum(lot.qty for lot in lots[m])
            if now_held <= max(1e-9, 0.01 * trip["peak"]):
                if m not in tainted and trip["invested"] > 0:
                    pnl = trip["proceeds"] - trip["invested"]
                    trips.append(RoundTrip(m, trip["opened_at"], t.block_time, trip["invested"], trip["proceeds"], pnl,
                                           pnl / trip["invested"], (t.block_time - trip["opened_at"]).total_seconds() / 3600,
                                           trip["buys"], trip["sells"]))
                open_trip.pop(m, None)
                tainted.discard(m)
                lots[m] = []
    open_positions = {m: lots[m] for m in lots if sum(lot.qty for lot in lots[m]) > 1e-12}
    return trips, {"open": open_positions, "sold_known": sold_known, "sold_total": sold_total, "tainted": tainted}


def _max_drawdown(pnls: list[float], capital: float) -> float | None:
    if not pnls or capital <= 0:
        return None
    eq = peak = capital
    mdd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak if peak > 0 else 0)
    return mdd * 100


def compute_metrics(trades: list[TradeLike], prices: dict[str, float] | None = None, now: datetime | None = None,
                    min_closed_for_verified: int = 10) -> TraderMetrics:
    m = TraderMetrics()
    if not trades:
        return m
    trades = sorted(trades, key=lambda x: x.block_time)
    m.n_actions = len(trades)
    m.n_buys = sum(1 for t in trades if t.kind == "BUY")
    m.n_sells = sum(1 for t in trades if t.kind == "SELL")
    m.n_transfers = sum(1 for t in trades if t.kind.startswith("TRANSFER"))
    first, last = trades[0].block_time, trades[-1].block_time
    m.first_trade_at, m.last_trade_at = first.isoformat(), last.isoformat()
    m.history_days = max((last - first).total_seconds() / 86400, 0.0)
    swaps = [t for t in trades if t.kind in ("BUY", "SELL")]
    known_usd = [t for t in swaps if t.quote_usd is not None]
    m.usd_known_ratio = len(known_usd) / len(swaps) if swaps else None
    buys_usd = [t.quote_usd for t in trades if t.kind == "BUY" and t.quote_usd]
    m.avg_trade_usd = statistics.fmean(buys_usd) if buys_usd else None
    if m.history_days > 0:
        m.trades_per_day = m.n_buys / max(m.history_days, 1 / 24)

    trips, extra = compute_round_trips(trades)
    m.known_basis_ratio = extra["sold_known"] / extra["sold_total"] if extra["sold_total"] > 0 else None
    m.n_closed = len(trips)
    m.n_open_positions = len(extra["open"])
    if prices is not None:
        unreal = 0.0
        for mint, lots in extra["open"].items():
            qty = sum(lot.qty for lot in lots)
            cost = sum(lot.cost for lot in lots if lot.cost is not None)
            p = prices.get(mint)
            if p is not None and all(lot.cost is not None for lot in lots):
                unreal += qty * p - cost
                m.open_exposure[mint] = qty * p
        m.unrealized_pnl = unreal

    by_token: dict[str, float] = defaultdict(float)
    for t in trades:
        if t.kind == "BUY" and t.quote_usd:
            by_token[t.mint] += t.quote_usd
    tot = sum(by_token.values())
    if tot > 0:
        shares = [v / tot for v in by_token.values()]
        m.concentration_hhi = sum(s * s for s in shares)
        m.top_token_share = max(shares)

    if trips:
        pnls = [r.pnl for r in trips]
        rets = [r.ret for r in trips]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        m.realized_pnl = sum(pnls)
        m.invested_closed = sum(r.invested for r in trips)
        m.roi = m.realized_pnl / m.invested_closed if m.invested_closed else None
        m.win_rate = len(wins) / len(trips)
        m.avg_win = statistics.fmean(wins) if wins else None
        m.avg_loss = statistics.fmean(losses) if losses else None
        m.avg_pnl = statistics.fmean(pnls)
        gl = -sum(losses)
        m.profit_factor = (sum(wins) / gl) if gl > 0 else (math.inf if wins else None)
        if len(rets) >= 2:
            m.ret_volatility = statistics.stdev(rets)
            m.sharpe_like = statistics.fmean(rets) / m.ret_volatility if m.ret_volatility > 0 else None
        holds = [r.hold_hours for r in trips]
        m.avg_hold_hours = statistics.fmean(holds)
        m.median_hold_hours = statistics.median(holds)
        capital = max(statistics.fmean([r.invested for r in trips]) * 3, 1e-9)
        m.max_drawdown_pct = _max_drawdown([r.pnl for r in sorted(trips, key=lambda r: r.closed_at)], capital)
        days: dict[str, float] = defaultdict(float)
        for r in trips:
            days[r.closed_at.date().isoformat()] += r.pnl
        m.positive_day_ratio = sum(1 for v in days.values() if v > 0) / len(days)
        streak = mx = 0
        for r in sorted(trips, key=lambda r: r.closed_at):
            streak = streak + 1 if r.pnl <= 0 else 0
            mx = max(mx, streak)
        m.max_loss_streak, m.current_loss_streak = mx, streak
        k = max(1, len(trips) // 3)
        ordered = sorted(trips, key=lambda r: r.closed_at)
        recent, early = ordered[-k:], ordered[:k]
        m.recent_roi = sum(r.pnl for r in recent) / sum(r.invested for r in recent)
        m.early_roi = sum(r.pnl for r in early) / sum(r.invested for r in early)

    # data quality: coverage of USD values, known cost basis, history length, sample size
    q_usd = m.usd_known_ratio if m.usd_known_ratio is not None else 0.0
    q_basis = m.known_basis_ratio if m.known_basis_ratio is not None else 0.0
    q_hist = min(m.history_days / 14, 1.0)
    q_n = min(m.n_closed / 30, 1.0)
    m.data_quality = round(0.3 * q_usd + 0.3 * q_basis + 0.2 * q_hist + 0.2 * q_n, 4)
    m.verified = m.n_closed >= min_closed_for_verified and q_basis >= 0.8 and q_usd >= 0.9
    if m.profit_factor == math.inf:
        m.profit_factor = 99.0
    return m
