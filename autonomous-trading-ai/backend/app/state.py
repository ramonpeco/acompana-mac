"""Process-wide singletons wired at startup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent.loop import Agent
from app.ai.supervisor import AISupervisor
from app.chains.base import ChainAdapter
from app.telegram.bot import TelegramBot


@dataclass
class AppState:
    adapter: ChainAdapter
    agent: Agent
    ai: AISupervisor
    telegram: TelegramBot | None = None
    extra: dict[str, Any] = field(default_factory=dict)


_state: AppState | None = None


def set_state(s: AppState) -> None:
    global _state
    _state = s


def get_state() -> AppState:
    assert _state is not None, "app not started"
    return _state
