from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.chains.types import TokenMarket
from app.risk.token_risk import assess_token
from app.traders.metrics import compute_metrics, compute_round_trips
from app.traders.scoring import score_trader

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class Tr:
    kind: str
    mint: str
    token_amount: float
    quote_usd: float | None
    block_time: datetime
    signature: str = "s"


def rt(mint, buy_usd, sell_usd, t, qty=100.0, hold_h=2):
    return [Tr("BUY", mint, qty, buy_usd, t), Tr("SELL", mint, qty, sell_usd, t + timedelta(hours=hold_h))]


def test_round_trip_fifo_and_partial_sells():
    trades = [Tr("BUY", "A", 100, 10, T0), Tr("SELL", "A", 50, 8, T0 + timedelta(hours=1)),
              Tr("SELL", "A", 50, 12, T0 + timedelta(hours=2))]
    trips, _ = compute_round_trips(trades)
    assert len(trips) == 1 and abs(trips[0].pnl - 10) < 1e-9 and trips[0].sells == 2


def test_unknown_basis_is_excluded_not_counted_as_profit():
    # tokens arrived by transfer (airdrop) then sold: not a profitable trade
    trades = [Tr("TRANSFER_IN", "B", 1000, None, T0), Tr("SELL", "B", 1000, 500, T0 + timedelta(hours=1))]
    m = compute_metrics(trades)
    assert m.n_closed == 0 and m.realized_pnl == 0 and m.known_basis_ratio == 0 and not m.verified
    # selling more than seen bought (pre-window holdings)
    trades = [Tr("BUY", "C", 10, 1, T0), Tr("SELL", "C", 100, 50, T0 + timedelta(hours=1))]
    assert compute_metrics(trades).n_closed == 0


def test_metrics_basic():
    trades = []
    for i in range(12):
        win = i % 3 != 0
        trades += rt(f"M{i % 5}", 100, 130 if win else 80, T0 + timedelta(hours=6 * i))
    m = compute_metrics(trades)
    assert m.n_closed == 12 and abs(m.win_rate - 8 / 12) < 1e-9
    assert abs(m.realized_pnl - (8 * 30 - 4 * 20)) < 1e-6
    assert abs(m.profit_factor - 240 / 80) < 1e-9 and m.verified
    assert m.max_loss_streak == 1 and m.median_hold_hours == 2


def test_score_filters():
    good = []
    for i in range(20):
        good += rt(f"M{i % 6}", 100, 125 if i % 4 else 85, T0 + timedelta(hours=8 * i))
    m = compute_metrics(good)
    r = score_trader(m, avg_liquidity_usd=80_000, now=T0 + timedelta(days=7))
    assert r.eligible and 0 <= r.score <= 100, r.reasons
    few = compute_metrics(rt("X", 100, 300, T0))
    r2 = score_trader(few, avg_liquidity_usd=80_000, now=T0 + timedelta(hours=3))
    assert not r2.eligible and any("insufficient_history" in x for x in r2.reasons)
    # sub-minute flipping cannot be replicated with polling latency
    fast = []
    for i in range(20):
        fast += rt("F", 100, 110, T0 + timedelta(hours=i), hold_h=0.005)
    r3 = score_trader(compute_metrics(fast), avg_liquidity_usd=80_000, now=T0 + timedelta(hours=21))
    assert any("not_replicable" in x for x in r3.reasons)
    r4 = score_trader(m, avg_liquidity_usd=3_000, now=T0 + timedelta(days=7))
    assert any("illiquid" in x for x in r4.reasons)


def test_weekly_pnl_alone_does_not_dominate():
    lucky = rt("L", 100, 5000, T0) * 1
    for i in range(9):
        lucky += rt(f"Q{i}", 100, 60, T0 + timedelta(hours=3 * (i + 1)))
    steady = []
    for i in range(20):
        steady += rt(f"S{i % 6}", 100, 120 if i % 5 else 90, T0 + timedelta(hours=8 * i))
    a = score_trader(compute_metrics(lucky), avg_liquidity_usd=80_000, now=T0 + timedelta(days=2))
    b = score_trader(compute_metrics(steady), avg_liquidity_usd=80_000, now=T0 + timedelta(days=7))
    assert b.score > a.score


def test_token_risk_flags_and_unknowns():
    now = T0
    m = TokenMarket("solana", "X", liquidity_usd=2000, market_cap_usd=1_000_000, pool_created_at=now - timedelta(minutes=5),
                    txns={"h1": {"buys": 50, "sells": 0}}, price_change={"h1": 20})
    r = assess_token(m, {"mint_authority": "A", "freeze_authority": "F", "risky_extensions": [], "supply": 100,
                         "largest_accounts": [{"amount": 90}]}, now)
    codes = {f.code for f in r.factors}
    assert {"mint_authority", "freeze_authority", "very_low_liquidity", "no_sells", "very_new"} <= codes
    assert r.level == "alto" and r.sellability == "restricted"
    r2 = assess_token(None, None, now)
    assert r2.unknowns and r2.score > 0  # unknown is not safe
