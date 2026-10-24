"""Risk profiles and copy/switching parameters.

Defaults are deliberately prudent. Values are editable at runtime by an admin
(stored in ``settings_kv``); the AI layer can only *propose* changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class RiskLimits:
    max_trade_pct: float  # max % of equity per new order
    max_token_pct: float  # max % of equity in one token
    max_total_exposure_pct: float  # max % of equity invested
    max_daily_loss_pct: float  # pause new entries after this daily loss
    max_drawdown_pct: float  # halt after this drawdown from peak
    stop_loss_pct: float  # per-position stop (from avg cost)
    take_profit_pct: float  # per-position take-profit; 0 disables
    min_liquidity_usd: float
    max_price_impact_pct: float  # reject if expected impact above this
    max_slippage_bps: int
    max_token_risk_score: float  # 0..100 (higher = riskier)
    min_token_age_minutes: float
    min_order_usd: float
    max_open_positions: int
    max_data_age_s: int  # block decisions on older market data
    max_signal_delay_s: int  # ignore trader trades we learned about too late

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RiskLimits:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


PROFILES: dict[str, RiskLimits] = {
    "CONSERVADOR": RiskLimits(
        max_trade_pct=5, max_token_pct=8, max_total_exposure_pct=30, max_daily_loss_pct=4,
        max_drawdown_pct=12, stop_loss_pct=15, take_profit_pct=40, min_liquidity_usd=50_000,
        max_price_impact_pct=1.0, max_slippage_bps=150, max_token_risk_score=45,
        min_token_age_minutes=360, min_order_usd=1.0, max_open_positions=3,
        max_data_age_s=60, max_signal_delay_s=60,
    ),
    "EQUILIBRADO": RiskLimits(
        max_trade_pct=10, max_token_pct=15, max_total_exposure_pct=50, max_daily_loss_pct=8,
        max_drawdown_pct=25, stop_loss_pct=25, take_profit_pct=80, min_liquidity_usd=20_000,
        max_price_impact_pct=2.5, max_slippage_bps=300, max_token_risk_score=60,
        min_token_age_minutes=60, min_order_usd=1.0, max_open_positions=5,
        max_data_age_s=90, max_signal_delay_s=120,
    ),
    "AGRESIVO": RiskLimits(
        max_trade_pct=20, max_token_pct=25, max_total_exposure_pct=80, max_daily_loss_pct=15,
        max_drawdown_pct=40, stop_loss_pct=35, take_profit_pct=0, min_liquidity_usd=8_000,
        max_price_impact_pct=5.0, max_slippage_bps=600, max_token_risk_score=75,
        min_token_age_minutes=10, min_order_usd=0.5, max_open_positions=8,
        max_data_age_s=120, max_signal_delay_s=300,
    ),
}

# Limits the AI may never change and the API changes only with admin role + audit.
CRITICAL_KEYS = {"max_daily_loss_pct", "max_drawdown_pct", "max_total_exposure_pct", "max_trade_pct", "max_token_pct"}


@dataclass
class SwitchRules:
    """When to stop following a trader (strategy B/C of the evaluation plan)."""

    consecutive_losses: int = 2  # 0 disables
    loss_basis: str = "copied"  # copied (our closed copies) | trader (trader's own closed round trips)
    max_session_drawdown_pct: float = 15.0  # of equity attributable to this session; 0 disables
    max_session_daily_loss_pct: float = 6.0
    inactivity_hours: float = 12.0
    min_observation_trades: int = 0  # min closed trades before loss-streak rule may fire
    min_follow_minutes: float = 30.0  # anti-churn: minimum time before a non-critical switch
    cooldown_days: float = 3.0  # do not re-select a dropped trader for this long
    score_drop_points: float = 25.0  # switch if score falls this much below start
    max_switches_per_day: int = 6
    selection: str = "score"  # score | top_pnl | ai_supervised

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SwitchRules:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class CopyParams:
    multiplier: float = 0.25
    reference_trade_usd: float = 100.0  # a trader buy of this USD size => base order
    base_order_pct: float = 10.0  # base order as % of equity at 1.00x
    sizing: str = "proportional"  # proportional (to trader size) | fixed
    copy_sells: bool = True
    exit_on_unfollow: bool = True  # close copied positions when we stop following

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CopyParams:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})
