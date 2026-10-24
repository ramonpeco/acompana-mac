import pytest
from sqlalchemy import select

from app.db import create_all, init_engine, session_scope
from app.models import AuditLog
from app.runtime import load_runtime
from app.sim.portfolio import get_or_create_portfolio
from app.telegram.bot import TelegramBot


@pytest.fixture
async def bot(tmp_path):
    init_engine(f"sqlite+aiosqlite:///{tmp_path}/tg.db")
    await create_all()
    async with session_scope() as s:
        await get_or_create_portfolio(s, "main", 52.0)
    b = TelegramBot("TOKEN", admin_ids={1}, notify_ids={2}, agent_status=lambda: {"state": "running"})
    b.sent = []

    async def fake_send(chat_id, text):
        b.sent.append((chat_id, text))

    b.send = fake_send
    return b


async def test_unauthorized_chat_is_ignored_and_audited(bot):
    assert await bot.handle(999, "/balance") is None and bot.sent == []
    async with session_scope() as s:
        assert (await s.execute(select(AuditLog.action))).scalars().all() == ["unauthorized_command"]


async def test_read_commands_and_admin_only_pause(bot):
    assert "52.00 USD" in await bot.handle(2, "/balance")
    assert "administradores" in await bot.handle(2, "/pause")  # notify-only chat cannot pause
    async with session_scope() as s:
        assert (await load_runtime(s)).paused is False
    assert "pausa" in await bot.handle(1, "/pause")
    async with session_scope() as s:
        assert (await load_runtime(s)).paused is True
    for cmd in ("/status", "/trader", "/positions", "/history", "/risk", "/report", "/help"):
        assert await bot.handle(1, cmd)


async def test_no_live_trading_command(bot):
    assert "no reconocido" in await bot.handle(1, "/live_on")
