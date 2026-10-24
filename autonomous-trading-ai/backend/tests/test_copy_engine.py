"""Copy engine against the DB using the synthetic adapter (dedup, proportional sells)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.chains.sandbox import SandboxAdapter, SandboxWorld
from app.chains.types import TradeEvent
from app.copytrade.engine import CopyEngine
from app.db import create_all, init_engine, session_scope
from app.models import Order
from app.runtime import kv_set, load_runtime
from app.scanner.service import Scanner
from app.sim.execution import ExecConfig
from app.sim.portfolio import get_or_create_portfolio, open_positions

T0 = datetime(2026, 2, 1, tzinfo=UTC)


@pytest.fixture
async def setup(tmp_path):
    init_engine(f"sqlite+aiosqlite:///{tmp_path}/c.db")
    await create_all()
    world = SandboxWorld(seed=9, start=T0, history_days=1, horizon_days=1)
    clock = lambda: T0  # noqa: E731
    ad = SandboxAdapter(world, clock)
    eng = CopyEngine(ad, Scanner(ad, now_fn=clock), ExecConfig(failure_rate=0), now_fn=clock)
    mint = next(a for a, t in world.tokens.items() if t.created_step == 0 and t.rug_step is None and t.liquidity >= 45_000)
    async with session_scope() as s:
        await kv_set(s, "risk_overrides", {"max_token_risk_score": 100, "min_token_age_minutes": 0})
    return eng, mint


def ev(kind, mint, qty, usd, sig):
    return TradeEvent("Wallet1", sig, 1, T0 - timedelta(seconds=10), kind, mint, qty, quote_usd=usd)


async def test_dedup_and_proportional_sell(setup):
    eng, mint = setup
    async with session_scope() as s:
        p = await get_or_create_portfolio(s, "main", 52.0)
        rt = await load_runtime(s)
        r1 = await eng.process(s, p, None, ev("BUY", mint, 1000, 100, "sig1"), rt, T0)
        assert r1.action == "bought", r1
        r_dup = await eng.process(s, p, None, ev("BUY", mint, 1000, 100, "sig1"), rt, T0)
        assert r_dup.action == "skipped" and "duplicate" in r_dup.detail
        pos = (await open_positions(s, p.id))[0]
        q0 = pos.qty
        r2 = await eng.process(s, p, None, ev("SELL", mint, 500, 60, "sig2"), rt, T0)
        assert r2.action == "sold"
        assert abs(pos.qty - q0 / 2) / q0 < 0.01  # trader sold half -> we sell half
        r3 = await eng.process(s, p, None, ev("SELL", mint, 500, 60, "sig3"), rt, T0)
        assert r3.action == "closed" and r3.position_closed
        assert p.cash < 52.0 + 10  # sanity
        n = (await s.execute(select(func.count(Order.id)))).scalar_one()
        assert n == 3


async def test_sell_without_copied_position_is_ignored(setup):
    eng, mint = setup
    async with session_scope() as s:
        p = await get_or_create_portfolio(s, "main", 52.0)
        r = await eng.process(s, p, None, ev("SELL", mint, 10, 1, "sigX"), await load_runtime(s), T0)
        assert r.action == "skipped"


async def test_kill_switch_blocks_buys(setup):
    eng, mint = setup
    async with session_scope() as s:
        await kv_set(s, "kill_switch", True)
        p = await get_or_create_portfolio(s, "main", 52.0)
        r = await eng.process(s, p, None, ev("BUY", mint, 1000, 100, "sigK"), await load_runtime(s), T0)
        assert r.action == "rejected" and "kill_switch_active" in r.reasons
