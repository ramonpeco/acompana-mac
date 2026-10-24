"""Pure decision logic shared by the live paper engine and the backtester."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from app.risk.profiles import CopyParams, SwitchRules


def copy_buy_size(equity: float, trader_usd: float | None, cp: CopyParams) -> float:
    """USD to spend on a copied buy.

    ``multiplier`` scales a *position size*, not leverage: at 1.00x a trader buy of
    ``reference_trade_usd`` maps to ``base_order_pct`` of our equity. Trader size
    only matters in proportional mode and is capped at 3x the base so a whale
    trade cannot blow through the risk caps (those are applied later anyway).
    """
    base = equity * cp.base_order_pct / 100 * cp.multiplier
    if cp.sizing == "fixed" or not trader_usd or cp.reference_trade_usd <= 0:
        return base
    ratio = min(max(trader_usd / cp.reference_trade_usd, 0.1), 3.0)
    return base * ratio


def sell_fraction(trader_sold: float, trader_qty_before: float) -> float:
    if trader_qty_before <= 0:
        return 1.0
    f = trader_sold / trader_qty_before
    return 1.0 if f >= 0.98 else max(0.0, min(f, 1.0))


@dataclass
class SessionStats:
    started_at: datetime
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    realized_pnl: float = 0.0
    peak_pnl: float = 0.0
    equity_at_start: float = 0.0
    day_pnl: float = 0.0
    score_at_start: float | None = None
    trades_per_day_at_start: float | None = None
    avg_trade_usd_at_start: float | None = None

    def on_close(self, pnl: float) -> None:
        self.closed_trades += 1
        self.realized_pnl += pnl
        self.day_pnl += pnl
        self.peak_pnl = max(self.peak_pnl, self.realized_pnl)
        if pnl <= 0:
            self.losses += 1
            self.consecutive_losses += 1
        else:
            self.wins += 1
            self.consecutive_losses = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        return d


@dataclass
class SwitchDecision:
    switch: bool
    reason: str | None = None
    critical: bool = False
    detail: str | None = None


def evaluate_switch(
    st: SessionStats,
    rules: SwitchRules,
    now: datetime,
    *,
    last_trader_trade_at: datetime | None,
    current_score: float | None = None,
    trades_per_day_now: float | None = None,
    avg_trade_usd_now: float | None = None,
    recent_avg_liquidity: float | None = None,
    min_liquidity: float | None = None,
) -> SwitchDecision:
    minutes = (now - st.started_at).total_seconds() / 60
    # --- critical (immediate) rules ---
    if (rules.consecutive_losses and st.consecutive_losses >= rules.consecutive_losses
            and st.closed_trades >= max(rules.min_observation_trades, rules.consecutive_losses)):
        return SwitchDecision(True, f"{st.consecutive_losses}_consecutive_losses", True,
                              f"{st.consecutive_losses} operaciones cerradas seguidas con pérdidas")
    base = st.equity_at_start or 1.0
    if rules.max_session_drawdown_pct and (st.peak_pnl - st.realized_pnl) / base * 100 >= rules.max_session_drawdown_pct:
        return SwitchDecision(True, "session_drawdown", True,
                              f"drawdown de la sesión {(st.peak_pnl - st.realized_pnl) / base * 100:.1f}% del capital")
    if rules.max_session_daily_loss_pct and -st.day_pnl / base * 100 >= rules.max_session_daily_loss_pct:
        return SwitchDecision(True, "session_daily_loss", True, f"pérdida diaria {st.day_pnl:.2f} USD")
    # --- non-critical rules respect the minimum follow period (anti-churn) ---
    if minutes < rules.min_follow_minutes:
        return SwitchDecision(False)
    if rules.inactivity_hours and last_trader_trade_at is not None:
        idle_h = (now - last_trader_trade_at).total_seconds() / 3600
        if idle_h >= rules.inactivity_hours and minutes / 60 >= rules.inactivity_hours:
            return SwitchDecision(True, "inactivity", False, f"{idle_h:.1f}h sin operar")
    if (rules.score_drop_points and st.score_at_start is not None and current_score is not None
            and st.score_at_start - current_score >= rules.score_drop_points):
        return SwitchDecision(True, "score_deterioration", False,
                              f"puntuación {st.score_at_start:.0f} -> {current_score:.0f}")
    if st.trades_per_day_at_start and trades_per_day_now and (
            trades_per_day_now / st.trades_per_day_at_start >= 4 or trades_per_day_now / st.trades_per_day_at_start <= 0.15):
        return SwitchDecision(True, "behaviour_change", False,
                              f"frecuencia {st.trades_per_day_at_start:.1f} -> {trades_per_day_now:.1f} compras/día")
    if st.avg_trade_usd_at_start and avg_trade_usd_now and avg_trade_usd_now / st.avg_trade_usd_at_start >= 5:
        return SwitchDecision(True, "risk_increase", False,
                              f"tamaño medio {st.avg_trade_usd_at_start:.0f} -> {avg_trade_usd_now:.0f} USD")
    if min_liquidity and recent_avg_liquidity is not None and recent_avg_liquidity < min_liquidity:
        return SwitchDecision(True, "liquidity_reduction", False,
                              f"liquidez media reciente ${recent_avg_liquidity:,.0f}")
    return SwitchDecision(False)
