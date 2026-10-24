"""Command-line backtest.

    python scripts/run_backtest.py --source synthetic --regime unfavorable --days 14 --walk-forward
    python scripts/run_backtest.py --source recorded --days 7        # uses data recorded by the agent
"""
import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.backtest.data import SyntheticDataset, load_recorded  # noqa: E402
from app.backtest.engine import BTConfig, run_all, walk_forward  # noqa: E402
from app.chains.sandbox import SandboxWorld  # noqa: E402
from app.db import session_scope  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["synthetic", "recorded"], default="synthetic")
    ap.add_argument("--regime", choices=["neutral", "favorable", "unfavorable"], default="neutral")
    ap.add_argument("--days", type=float, default=10)
    ap.add_argument("--profile", default="EQUILIBRADO")
    ap.add_argument("--capital", type=float, default=52.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--walk-forward", action="store_true")
    ap.add_argument("--json", help="write full results to this file")
    a = ap.parse_args()
    cfg = BTConfig(initial_capital=a.capital, profile=a.profile, seed=a.seed)
    if a.source == "recorded":
        end = datetime.now(UTC)
        async with session_scope() as s:
            data = await load_recorded(s, end - timedelta(days=a.days), end)
        print(f"{len(data.trades)} operaciones registradas")
    else:
        world = SandboxWorld(seed=a.seed, history_days=7, horizon_days=a.days, start=datetime(2026, 1, 8, tzinfo=UTC),
                             regime=a.regime, n_traders=40)
        data, cfg.warmup_days = SyntheticDataset(world), 7
    res = run_all(data, cfg)
    if a.walk_forward:
        res["walk_forward"] = walk_forward(data, cfg)
    print(f"{'Estr.':6}{'Rent%':>9}{'MaxDD%':>9}{'Sharpe':>8}{'PF':>7}{'Ops':>6}{'Camb.':>6}{'Comis.$':>9}")
    for k, r in res["results"].items():
        print(f"{k:6}{r['net_return_pct']:9.2f}{r['max_drawdown_pct']:9.2f}{(r['sharpe'] or 0):8.2f}"
              f"{(r['profit_factor'] or 0):7.2f}{r['n_trades']:6d}{r['n_switches']:6d}{r['fees']:9.3f}")
    for row in res["comparison"]["table"]:
        vs = row.get("vs_baseline")
        if vs:
            print(f"  {row['strategy']} vs F: {vs['mean_daily_diff'] * 100:+.3f}%/día IC95 "
                  f"[{vs['ci95'][0] * 100:+.3f}, {vs['ci95'][1] * 100:+.3f}] {'significativo' if vs['significant'] else 'no significativo'}")
    if "walk_forward" in res:
        print("walk-forward OOS medio:", res["walk_forward"]["mean_oos_return_pct"])
    print("Etiqueta de datos:", data.label, "·", res["comparison"]["warning"])
    if a.json:
        Path(a.json).write_text(json.dumps(res, default=str, indent=1))


asyncio.run(main())
