"""Runtime configuration persisted in ``settings_kv`` (survives restarts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Setting
from app.risk.profiles import PROFILES, CopyParams, RiskLimits, SwitchRules


async def kv_get(session: AsyncSession, key: str, default: Any = None) -> Any:
    row = await session.get(Setting, key)
    return default if row is None else row.value


async def kv_set(session: AsyncSession, key: str, value: Any, actor: str = "system") -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value, updated_by=actor))
    else:
        row.value = value
        row.updated_by = actor


@dataclass
class RuntimeConfig:
    profile: str
    limits: RiskLimits
    switch: SwitchRules
    copy: CopyParams
    kill_switch: bool
    paused: bool
    strategy_version: str


async def load_runtime(session: AsyncSession) -> RuntimeConfig:
    s = get_settings()
    profile = await kv_get(session, "risk_profile", s.risk_profile)
    base = PROFILES.get(profile, PROFILES["EQUILIBRADO"]).to_dict()
    base.update(await kv_get(session, "risk_overrides", {}) or {})
    copy_defaults = CopyParams(multiplier=s.copy_multiplier, reference_trade_usd=s.reference_trade_usd).to_dict()
    copy_defaults.update(await kv_get(session, "copy_params", {}) or {})
    sw = SwitchRules().to_dict()
    sw.update(await kv_get(session, "switch_rules", {}) or {})
    return RuntimeConfig(
        profile=profile,
        limits=RiskLimits.from_dict(base),
        switch=SwitchRules.from_dict(sw),
        copy=CopyParams.from_dict(copy_defaults),
        kill_switch=bool(await kv_get(session, "kill_switch", False)),
        paused=bool(await kv_get(session, "agent_paused", False)),
        strategy_version=str(await kv_get(session, "strategy_version", "default")),
    )
