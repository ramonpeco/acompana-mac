"""Agent event log + in-process pub/sub (WebSocket) + notifier hooks (Telegram)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentEvent

log = logging.getLogger(__name__)

NOTIFY_KINDS = {
    "agent_started", "agent_stopped", "trader_selected", "trader_switched", "position_opened",
    "position_closed", "risk_alert", "daily_report", "kill_switch", "agent_paused", "agent_resumed",
}


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._notifiers: list[Callable[[str, str, dict], Awaitable[None]]] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def add_notifier(self, fn: Callable[[str, str, dict], Awaitable[None]]) -> None:
        self._notifiers.append(fn)

    def publish(self, msg: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def emit(self, session: AsyncSession | None, level: str, kind: str, message: str, **data: Any) -> None:
        if session is not None:
            session.add(AgentEvent(level=level, kind=kind, message=message, data=data))
        msg = {"type": "event", "ts": datetime.now(UTC).isoformat(), "level": level, "kind": kind, "message": message, "data": data}
        self.publish(msg)
        log.log(logging.WARNING if level in ("warn", "alert", "error") else logging.INFO, "%s: %s", kind, message)
        if kind in NOTIFY_KINDS or level == "alert":
            for n in self._notifiers:
                try:
                    await n(kind, message, data)
                except Exception as exc:  # notifier failures must never break the agent
                    log.warning("notifier failed: %s", exc)


bus = EventBus()
