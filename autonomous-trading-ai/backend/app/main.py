"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import jwt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text

from app.agent.loop import Agent
from app.ai.provider import build_provider
from app.ai.supervisor import AISupervisor
from app.api.routes import router
from app.auth import decode_token, ensure_admin
from app.chains.sandbox import SandboxAdapter
from app.chains.solana import SolanaAdapter
from app.config import get_settings
from app.db import create_all, get_engine, session_scope
from app.events import bus
from app.models import Order, Position
from app.state import AppState, get_state, set_state
from app.telegram.bot import TelegramBot


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": datetime.now(UTC).isoformat(), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False)


def setup_logging(level: str) -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[h], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    if s.environment != "production":
        await create_all()  # production runs `alembic upgrade head` in the entrypoint
    async with session_scope() as session:
        await ensure_admin(session)
    adapter = SandboxAdapter() if s.data_mode == "sandbox" else SolanaAdapter(s)
    ai = AISupervisor(build_provider(s), s.ai_daily_call_budget)
    agent = Agent(s, adapter, bus, ai)
    tg = None
    if s.telegram_bot_token and s.telegram_admin_ids:
        tg = TelegramBot(s.telegram_bot_token, s.telegram_admin_ids, s.telegram_notify_ids, lambda: agent.status)
        bus.add_notifier(tg.notify)
        tg.start()
    set_state(AppState(adapter=adapter, agent=agent, ai=ai, telegram=tg))
    if s.agent_autostart:
        agent.start()
    yield
    await agent.stop()
    if tg:
        await tg.stop()
    await adapter.aclose()


app = FastAPI(title="Autonomous Crypto Trading AI", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origin_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.get("/api/health")
async def health():
    """Liveness + readiness: DB reachable and agent heartbeat recent."""
    db_ok = True
    try:
        async with get_engine().connect() as c:
            await c.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    try:
        st = get_state()
        agent = st.agent.status
        connectors = {c["name"]: c["status"] for c in st.adapter.health()}
    except AssertionError:
        agent, connectors = {"state": "starting"}, {}
    last = agent.get("last_tick")
    stale = last is None or (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() > 600
    ok = db_ok and agent.get("state") in ("running", "stopped", "starting")
    return {"ok": ok, "db": db_ok, "agent": agent.get("state"), "agent_heartbeat_stale": stale,
            "connectors": connectors, "mode": "PAPER"}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    st = get_state()
    lines = [f'agent_ticks_total {st.agent.status.get("ticks", 0)}',
             f'agent_consecutive_errors {st.agent.status.get("consecutive_errors", 0)}']
    for c in st.adapter.health():
        lines += [f'connector_requests_total{{name="{c["name"]}"}} {c.get("requests", 0)}',
                  f'connector_errors_total{{name="{c["name"]}"}} {c.get("errors", 0)}',
                  f'connector_up{{name="{c["name"]}"}} {1 if c.get("status") in ("ok", "idle") else 0}']
    async with session_scope() as s:
        lines.append(f"orders_total {(await s.execute(select(func.count(Order.id)))).scalar_one()}")
        lines.append(f'positions_open {(await s.execute(select(func.count(Position.id)).where(Position.status == "open"))).scalar_one()}')
    return "\n".join(lines) + "\n"


@app.websocket("/api/ws")
async def ws(websocket: WebSocket, token: str = ""):
    try:
        decode_token(token)
    except jwt.PyJWTError:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    q = bus.subscribe()
    try:
        await websocket.send_json({"type": "hello", "status": get_state().agent.status})
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=25)
                await websocket.send_json(msg)
            except TimeoutError:
                await websocket.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)


# Serve the built frontend if present (single-container deployments)
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        f = _dist / path
        return FileResponse(f if path and f.is_file() else _dist / "index.html")
