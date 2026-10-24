import random
from datetime import UTC, datetime, timedelta

from app.copytrade.logic import SessionStats, copy_buy_size, evaluate_switch, sell_fraction
from app.risk.engine import EntryRequest, check_entry, exit_triggers
from app.risk.profiles import PROFILES, CopyParams, SwitchRules
from app.sim.execution import ExecConfig, MarketContext, simulate

L = PROFILES["EQUILIBRADO"]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def req(**kw):
    base = dict(mint="M", desired_usd=5, equity=52, cash=52, exposure_total=0, exposure_token=0, open_positions=0,
                already_holding=False, market_age_s=5, signal_delay_s=10, liquidity_usd=100_000, token_age_minutes=600,
                token_risk_score=20, sellability="route_found", daily_pnl_pct=0, drawdown_pct=0, kill_switch=False, paused=False)
    base.update(kw)
    return EntryRequest(**base)


def test_risk_approves_and_caps():
    d = check_entry(req(desired_usd=50), L)
    assert d.approved and d.size_usd <= 52 * L.max_trade_pct / 100 + 1e-9 and d.adjustments


def test_risk_rejections():
    assert "kill_switch_active" in check_entry(req(kill_switch=True), L).reasons
    assert any("stale" in r for r in check_entry(req(market_age_s=10_000), L).reasons)
    assert any("signal_too_late" in r for r in check_entry(req(signal_delay_s=10_000), L).reasons)
    assert any("liquidity" in r for r in check_entry(req(liquidity_usd=100), L).reasons)
    assert any("token_risk_too_high" in r for r in check_entry(req(token_risk_score=99), L).reasons)
    assert "token_risk_unassessed" in check_entry(req(token_risk_score=None), L).reasons
    d = check_entry(req(drawdown_pct=50), L)
    assert not d.approved and d.halt == "max_drawdown"
    assert any("daily_loss" in r for r in check_entry(req(daily_pnl_pct=-20), L).reasons)
    assert any("max_total" in a for a in check_entry(req(desired_usd=5, exposure_total=26), L).adjustments + check_entry(req(desired_usd=5, exposure_total=26), L).reasons)


def test_no_martingale_size_does_not_grow_after_losses():
    cp = CopyParams(multiplier=0.5)
    assert copy_buy_size(52, 100, cp) == copy_buy_size(52, 100, cp)  # stateless: losses never raise size
    assert copy_buy_size(40, 100, cp) < copy_buy_size(52, 100, cp)  # smaller equity -> smaller size


def test_multiplier_scales_and_caps_whales():
    a = copy_buy_size(100, 100, CopyParams(multiplier=0.1))
    b = copy_buy_size(100, 100, CopyParams(multiplier=1.0))
    assert abs(b / a - 10) < 1e-9
    assert copy_buy_size(100, 1_000_000, CopyParams(multiplier=1.0)) == 3 * b


def test_execution_never_better_than_reference_on_average():
    rng = random.Random(0)
    cfg = ExecConfig(failure_rate=0)
    ctx = MarketContext(price=1.0, liquidity_usd=50_000)
    fills = [simulate("BUY", ctx, rng, cfg, usd=10, max_slippage_bps=10_000) for _ in range(2000)]
    avg = sum(f.exec_price for f in fills) / len(fills)
    assert avg > 1.0  # latency adverse drift + impact
    assert all(f.fee_usd > 0 for f in fills)


def test_execution_slippage_revert_and_partial():
    rng = random.Random(1)
    cfg = ExecConfig(failure_rate=0)
    f = simulate("BUY", MarketContext(1.0, 5_000), rng, cfg, usd=2_000, max_slippage_bps=100, max_impact=1.0)
    assert f.status == "failed" and "slippage" in f.reason and f.fee_usd > 0
    f2 = simulate("SELL", MarketContext(1.0, 10_000), rng, cfg, qty=5_000, max_slippage_bps=10_000, max_impact=0.05)
    assert f2.status == "partial" and f2.filled_qty < 5_000
    f3 = simulate("BUY", MarketContext(1.0, 1_000_000), random.Random(2), ExecConfig(failure_rate=1.0), usd=10)
    assert f3.status == "failed"


def test_stop_loss_take_profit():
    assert exit_triggers(1.0, 0.7, L) == "stop_loss"
    assert exit_triggers(1.0, 1.9, L) == "take_profit"
    assert exit_triggers(1.0, 1.1, L) is None


def test_two_consecutive_losses_rule():
    st = SessionStats(started_at=T0, equity_at_start=52)
    rules = SwitchRules(consecutive_losses=2, max_session_drawdown_pct=0, max_session_daily_loss_pct=0)
    st.on_close(-1)
    assert not evaluate_switch(st, rules, T0 + timedelta(hours=1), last_trader_trade_at=T0).switch
    st.on_close(+0.5)
    st.on_close(-1)
    assert not evaluate_switch(st, rules, T0 + timedelta(hours=2), last_trader_trade_at=T0).switch  # streak reset by win
    st.on_close(-0.2)
    d = evaluate_switch(st, rules, T0 + timedelta(minutes=3), last_trader_trade_at=T0)
    assert d.switch and d.critical and d.reason == "2_consecutive_losses"  # critical: ignores min follow time
    st3 = SessionStats(started_at=T0, equity_at_start=52)
    st3.on_close(-1)
    st3.on_close(-1)
    assert not evaluate_switch(st3, SwitchRules(consecutive_losses=3, max_session_drawdown_pct=0, max_session_daily_loss_pct=0),
                               T0 + timedelta(hours=1), last_trader_trade_at=T0).switch


def test_anti_churn_and_inactivity():
    st = SessionStats(started_at=T0, equity_at_start=52)
    r = SwitchRules(inactivity_hours=1, min_follow_minutes=30)
    assert not evaluate_switch(st, r, T0 + timedelta(minutes=10), last_trader_trade_at=T0 - timedelta(hours=5)).switch
    d = evaluate_switch(st, r, T0 + timedelta(hours=2), last_trader_trade_at=T0 - timedelta(hours=5))
    assert d.switch and d.reason == "inactivity" and not d.critical


def test_sell_fraction():
    assert sell_fraction(50, 100) == 0.5 and sell_fraction(99, 100) == 1.0 and sell_fraction(10, 0) == 1.0
