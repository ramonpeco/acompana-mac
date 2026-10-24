"""Deterministic pre-trade risk engine.

Every order (paper or live) must pass :func:`check_entry`. The AI layer has no
path around it. Exits are risk-reducing and are only blocked by missing prices.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.risk.profiles import RiskLimits


@dataclass
class EntryRequest:
    mint: str
    desired_usd: float
    equity: float
    cash: float
    exposure_total: float
    exposure_token: float
    open_positions: int
    already_holding: bool
    market_age_s: float | None
    signal_delay_s: float | None
    liquidity_usd: float | None
    token_age_minutes: float | None
    token_risk_score: float | None
    sellability: str | None
    daily_pnl_pct: float
    drawdown_pct: float
    kill_switch: bool
    paused: bool
    expected_impact: float | None = None


@dataclass
class RiskDecision:
    approved: bool
    size_usd: float
    reasons: list[str] = field(default_factory=list)
    adjustments: list[str] = field(default_factory=list)
    halt: str | None = None  # set when a portfolio-level limit is breached

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_entry(r: EntryRequest, L: RiskLimits) -> RiskDecision:
    reasons: list[str] = []
    adj: list[str] = []
    halt = None
    if r.kill_switch:
        reasons.append("kill_switch_active")
    if r.paused:
        reasons.append("agent_paused")
    if r.drawdown_pct >= L.max_drawdown_pct:
        reasons.append(f"max_drawdown_reached ({r.drawdown_pct:.1f}% >= {L.max_drawdown_pct}%)")
        halt = "max_drawdown"
    if -r.daily_pnl_pct >= L.max_daily_loss_pct:
        reasons.append(f"daily_loss_limit ({r.daily_pnl_pct:.1f}% <= -{L.max_daily_loss_pct}%)")
        halt = halt or "daily_loss"
    if r.market_age_s is None:
        reasons.append("no_market_data")
    elif r.market_age_s > L.max_data_age_s:
        reasons.append(f"stale_market_data ({r.market_age_s:.0f}s > {L.max_data_age_s}s)")
    if r.signal_delay_s is not None and r.signal_delay_s > L.max_signal_delay_s:
        reasons.append(f"signal_too_late ({r.signal_delay_s:.0f}s > {L.max_signal_delay_s}s)")
    if r.liquidity_usd is None or r.liquidity_usd < L.min_liquidity_usd:
        reasons.append(f"insufficient_liquidity ({r.liquidity_usd or 0:,.0f} < {L.min_liquidity_usd:,.0f})")
    if r.token_age_minutes is not None and r.token_age_minutes < L.min_token_age_minutes:
        reasons.append(f"token_too_new ({r.token_age_minutes:.0f}m < {L.min_token_age_minutes:.0f}m)")
    if r.token_risk_score is None:
        reasons.append("token_risk_unassessed")
    elif r.token_risk_score > L.max_token_risk_score:
        reasons.append(f"token_risk_too_high ({r.token_risk_score:.0f} > {L.max_token_risk_score:.0f})")
    if r.sellability == "restricted":
        reasons.append("token_sell_restricted")
    if not r.already_holding and r.open_positions >= L.max_open_positions:
        reasons.append(f"max_open_positions ({r.open_positions})")

    size = max(0.0, r.desired_usd)
    caps = {
        "max_trade": L.max_trade_pct / 100 * r.equity,
        "max_token": L.max_token_pct / 100 * r.equity - r.exposure_token,
        "max_total": L.max_total_exposure_pct / 100 * r.equity - r.exposure_total,
        "cash": r.cash * 0.98,  # keep a small buffer for fees
    }
    for name, cap in caps.items():
        if size > cap:
            size = max(0.0, cap)
            adj.append(f"size_capped_by_{name} -> ${size:.2f}")
    if r.expected_impact is not None and r.expected_impact > L.max_price_impact_pct / 100 and r.liquidity_usd:
        m = L.max_price_impact_pct / 100
        cap = m * (r.liquidity_usd / 2) / (1 - m)
        if size > cap:
            size = cap
            adj.append(f"size_capped_by_impact -> ${size:.2f}")
    if size < L.min_order_usd:
        reasons.append(f"below_min_order (${size:.2f} < ${L.min_order_usd:.2f})")
    return RiskDecision(not reasons, round(size, 6) if not reasons else 0.0, reasons, adj, halt)


def exit_triggers(avg_cost_price: float, price: float, L: RiskLimits, sl_pct: float | None = None,
                  tp_pct: float | None = None) -> str | None:
    sl = L.stop_loss_pct if sl_pct is None else sl_pct
    tp = L.take_profit_pct if tp_pct is None else tp_pct
    if avg_cost_price <= 0:
        return None
    ch = (price / avg_cost_price - 1) * 100
    if sl and ch <= -sl:
        return "stop_loss"
    if tp and ch >= tp:
        return "take_profit"
    return None


def age_seconds(ts: datetime | str | None, now: datetime) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return (now - ts).total_seconds()
