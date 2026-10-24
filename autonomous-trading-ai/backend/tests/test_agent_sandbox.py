"""End-to-end agent run on the synthetic world with a simulated clock (no network)."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.agent.loop import Agent
from app.ai.provider import NullProvider
from app.ai.supervisor import AISupervisor
from app.chains.sandbox import SandboxAdapter, SandboxWorld
from app.config import Settings
from app.db import create_all, init_engine, session_scope
from app.events import EventBus
from app.models import AgentEvent, FollowSession, Order, Portfolio, Position
from app.runtime import kv_set


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
async def env(tmp_path):
    pg = os.environ.get("TEST_DATABASE_URL")  # optional: run the same scenario on PostgreSQL
    eng = init_engine(pg or f"sqlite+aiosqlite:///{tmp_path}/t.db")
    if pg:
        from app.db import Base
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.drop_all)
    await create_all()
    start = datetime(2026, 3, 1, tzinfo=UTC)
    world = SandboxWorld(seed=11, start=start, history_days=4, horizon_days=6, n_traders=30)
    clock = Clock(start)
    adapter = SandboxAdapter(world, clock)
    s = Settings(data_mode="sandbox", discovery_interval_minutes=60, ranking_interval_minutes=120,
                 scanner_interval_seconds=900, sim_failure_rate=0.0)
    agent = Agent(s, adapter, EventBus(), AISupervisor(NullProvider()), now_fn=clock)
    async with session_scope() as ses:
        await kv_set(ses, "risk_profile", "AGRESIVO")
        await kv_set(ses, "risk_overrides", {"min_liquidity_usd": 5000, "max_token_risk_score": 90, "min_token_age_minutes": 0})
    return agent, clock


async def test_agent_runs_two_days(env):
    agent, clock = env
    for _ in range(int(48 * 60 / 5)):
        await agent.tick()
        clock.t += timedelta(minutes=5)
    async with session_scope() as s:
        main = (await s.execute(select(Portfolio).where(Portfolio.name == "main"))).scalar_one()
        n_orders = (await s.execute(select(func.count(Order.id)).where(Order.portfolio_id == main.id))).scalar_one()
        filled = (await s.execute(select(func.count(Order.id)).where(Order.portfolio_id == main.id, Order.status.in_(("filled", "partial"))))).scalar_one()
        closed = (await s.execute(select(func.count(Position.id)).where(Position.portfolio_id == main.id, Position.status == "closed"))).scalar_one()
        sessions = (await s.execute(select(FollowSession))).scalars().all()
        errors = (await s.execute(select(func.count(AgentEvent.id)).where(AgentEvent.level == "error"))).scalar_one()
        # never copy at the trader's own price: every filled buy has a later detection time
        late = (await s.execute(select(Order).where(Order.side == "BUY", Order.status == "filled"))).scalars().all()
    assert errors == 0
    assert sessions, "agent should select a trader autonomously"
    assert n_orders > 0 and filled > 0, "agent should copy trades"
    assert closed > 0
    assert all(o.detected_at >= o.trader_block_time for o in late)
    print(f"orders={n_orders} filled={filled} closed={closed} sessions={len(sessions)} "
          f"ended={[x.end_reason for x in sessions if x.end_reason]} cash={main.cash:.2f}")
