from datetime import UTC, datetime, timedelta

from app.backtest.data import BTTrade, Dataset, PriceSeries, SyntheticDataset
from app.backtest.engine import STRATEGIES, Backtester, BTConfig, run_all, walk_forward
from app.chains.sandbox import SandboxWorld

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_price_series_never_looks_ahead():
    ps = PriceSeries()
    ps.add("M", T0, 1.0)
    ps.add("M", T0 + timedelta(minutes=10), 2.0)
    ps.finalize()
    assert ps.at("M", T0 + timedelta(minutes=9))[0] == 1.0
    assert ps.at("M", T0 - timedelta(minutes=1)) is None


def test_ranking_ignores_future_trades():
    trades = []
    for i in range(10):  # wallet X: only profitable AFTER t=day5
        t = T0 + timedelta(days=5, hours=i * 3)
        trades += [BTTrade("X", f"x{i}b", t, "BUY", "M", 100, 100), BTTrade("X", f"x{i}s", t + timedelta(hours=1), "SELL", "M", 100, 200)]
    ds = Dataset(trades, PriceSeries(), PriceSeries(), T0, T0 + timedelta(days=8))
    bt = Backtester(ds, BTConfig())
    from collections import defaultdict
    by = defaultdict(list)
    for t in trades:
        by[t.wallet].append(t)
    assert bt._rank(T0 + timedelta(days=4), by) == []  # nothing known yet
    assert bt._rank(T0 + timedelta(days=7), by)[0]["n_closed"] >= 8


def test_strategies_run_and_baseline():
    world = SandboxWorld(seed=2, start=T0, history_days=0, horizon_days=5, n_traders=20)
    res = run_all(SyntheticDataset(world), BTConfig(profile="AGRESIVO"))
    assert set(res["results"]) == set(STRATEGIES)
    for r in res["results"].values():
        assert r["data_label"] == "synthetic" and r["final_equity"] > 0
        assert r["max_drawdown_pct"] >= 0
    assert res["results"]["F"]["net_return_pct"] <= 0  # flat synthetic SOL minus entry fee
    assert any("SINTÉTICOS" in n for n in res["results"]["B"]["notes"])


def test_walk_forward():
    world = SandboxWorld(seed=4, start=T0, history_days=0, horizon_days=8, n_traders=20)
    wf = walk_forward(SyntheticDataset(world), BTConfig(profile="AGRESIVO", lookback_days=3), folds=2)
    assert len(wf["folds"]) == 2 and all(f["chosen_n"] in (1, 2, 3, 4) for f in wf["folds"])
