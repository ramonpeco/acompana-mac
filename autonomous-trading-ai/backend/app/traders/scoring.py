"""Trader score (0-100) and eligibility filters.

The score is a weighted sum of bounded components, so no single metric (e.g.
last week's PnL) can dominate. Every component and every exclusion reason is
returned so the dashboard/AI can explain the ranking.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.traders.metrics import TraderMetrics

SCORING_VERSION = "v1"

WEIGHTS = {
    "risk_adjusted_return": 0.22,
    "consistency": 0.16,
    "drawdown": 0.12,
    "sample_size": 0.10,
    "data_quality": 0.10,
    "history": 0.06,
    "diversification": 0.06,
    "frequency": 0.05,
    "liquidity": 0.07,
    "replicability": 0.06,
}


@dataclass
class ScoringConfig:
    min_closed_trades: int = 8
    min_history_days: float = 2.0
    min_data_quality: float = 0.4
    min_median_hold_minutes: float = 3.0  # faster than this cannot be copied with polling latency
    max_trades_per_day: float = 60.0
    max_top_token_share: float = 0.6
    min_avg_liquidity_usd: float = 15_000
    inactive_hours: float = 48.0
    suspicious_win_rate: float = 0.97
    suspicious_profit_factor: float = 50.0


@dataclass
class ScoreResult:
    score: float
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_trader(
    m: TraderMetrics,
    *,
    avg_liquidity_usd: float | None = None,
    now: datetime | None = None,
    cfg: ScoringConfig | None = None,
) -> ScoreResult:
    cfg = cfg or ScoringConfig()
    reasons: list[str] = []
    c: dict[str, float] = {}

    roi = m.roi or 0.0
    sharpe = m.sharpe_like or 0.0
    # tanh squashes extreme memecoin returns so lottery wins don't dominate
    c["risk_adjusted_return"] = _clamp(0.5 + 0.25 * math.tanh(roi * 2) + 0.25 * math.tanh(sharpe * 1.5))
    stability = 1.0
    if m.recent_roi is not None and m.early_roi is not None:
        stability = _clamp(1 - max(0.0, m.early_roi - m.recent_roi))  # penalise decay
    c["consistency"] = _clamp(0.6 * (m.positive_day_ratio or 0) + 0.4 * stability)
    c["drawdown"] = 1 - _clamp((m.max_drawdown_pct or 100) / 60)
    c["sample_size"] = _clamp(m.n_closed / 30)
    c["data_quality"] = _clamp(m.data_quality)
    c["history"] = _clamp(m.history_days / 14)
    c["diversification"] = 1 - _clamp(((m.top_token_share or 1.0) - 0.2) / 0.6)
    tpd = m.trades_per_day or 0
    c["frequency"] = _clamp(min(tpd / 1.0, 1.0) * (1 - _clamp((tpd - 20) / 40)))
    if avg_liquidity_usd:
        c["liquidity"] = _clamp(math.log10(max(avg_liquidity_usd, 1) / 5_000) / math.log10(100))
    else:
        c["liquidity"] = 0.3  # unknown -> neutral-low
    hold_min = (m.median_hold_hours or 0) * 60
    c["replicability"] = _clamp((hold_min - cfg.min_median_hold_minutes) / 60)

    score = 100 * sum(WEIGHTS[k] * c[k] for k in WEIGHTS)

    # --- exclusion filters ---
    if m.n_closed < cfg.min_closed_trades:
        reasons.append(f"insufficient_history: {m.n_closed} closed trades < {cfg.min_closed_trades}")
    if m.history_days < cfg.min_history_days:
        reasons.append(f"short_history: {m.history_days:.1f}d < {cfg.min_history_days}d")
    if m.data_quality < cfg.min_data_quality:
        reasons.append(f"low_data_quality: {m.data_quality:.2f}")
    if m.median_hold_hours is not None and hold_min < cfg.min_median_hold_minutes:
        reasons.append(f"not_replicable: median hold {hold_min:.1f} min")
    if tpd > cfg.max_trades_per_day:
        reasons.append(f"bot_like_frequency: {tpd:.0f} buys/day")
    if m.top_token_share is not None and m.top_token_share > cfg.max_top_token_share and m.n_closed < 20:
        reasons.append(f"excessive_concentration: {m.top_token_share:.0%} in one token")
    if avg_liquidity_usd is not None and avg_liquidity_usd < cfg.min_avg_liquidity_usd:
        reasons.append(f"illiquid_assets: avg liquidity ${avg_liquidity_usd:,.0f}")
    if m.n_closed >= 15 and (m.win_rate or 0) >= cfg.suspicious_win_rate:
        reasons.append(f"suspicious: win rate {m.win_rate:.0%}")
    if m.n_closed >= 10 and (m.profit_factor or 0) >= cfg.suspicious_profit_factor:
        reasons.append(f"suspicious: profit factor {m.profit_factor:.0f}")
    if now and m.last_trade_at:
        idle_h = (now - datetime.fromisoformat(m.last_trade_at)).total_seconds() / 3600
        if idle_h > cfg.inactive_hours:
            reasons.append(f"inactive: {idle_h:.0f}h since last trade")
    if (m.roi or 0) <= 0:
        reasons.append("non_positive_realized_return")

    return ScoreResult(round(score, 2), not reasons, reasons, {k: round(v, 4) for k, v in c.items()})
