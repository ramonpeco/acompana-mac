"""Application settings loaded from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- core ---
    app_name: str = "Autonomous Crypto Trading AI"
    environment: Literal["development", "production", "test"] = "development"
    database_url: str = "sqlite+aiosqlite:///./trading.db"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    # --- auth ---
    jwt_secret: str = Field(default="change-me-in-production-please-32b", min_length=16)
    jwt_ttl_minutes: int = 720
    admin_username: str = "admin"
    # bcrypt hash; if empty, ADMIN_PASSWORD is hashed at startup (dev convenience)
    admin_password_hash: str = ""
    admin_password: str = ""

    # --- data sources ---
    # "live": real public APIs. "sandbox": deterministic synthetic market used only for
    # local end-to-end testing of the pipeline; the UI shows a permanent warning banner.
    data_mode: Literal["live", "sandbox"] = "live"
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_rpc_rps: float = 4.0
    helius_api_key: str = ""
    dexscreener_base_url: str = "https://api.dexscreener.com"
    geckoterminal_base_url: str = "https://api.geckoterminal.com/api/v2"
    jupiter_base_url: str = "https://api.jup.ag"
    jupiter_api_key: str = ""
    birdeye_api_key: str = ""
    stale_after_seconds: int = 120

    # --- agent ---
    agent_autostart: bool = True
    agent_tick_seconds: int = 20
    wallet_poll_seconds: int = 15
    discovery_interval_minutes: int = 30
    ranking_interval_minutes: int = 60
    scanner_interval_seconds: int = 60
    max_followed_traders: int = 1

    # --- paper trading ---
    initial_capital_usd: float = 52.0
    risk_profile: Literal["CONSERVADOR", "EQUILIBRADO", "AGRESIVO"] = "EQUILIBRADO"
    copy_multiplier: float = 0.25
    reference_trade_usd: float = 100.0  # trader size that maps to 1.00x of our base size
    sim_latency_ms: int = 2500
    sim_failure_rate: float = 0.03
    sim_priority_fee_usd: float = 0.002
    sim_dex_fee_bps: float = 25.0
    sim_seed: int = 42

    # --- AI ---
    ai_provider: Literal["anthropic", "openai_compatible", "none"] = "anthropic"
    anthropic_api_key: str = ""
    ai_model: str = "claude-opus-5-5"
    ai_effort: Literal["low", "medium", "high"] = "low"
    openai_compatible_base_url: str = ""
    openai_compatible_api_key: str = ""
    openai_compatible_model: str = ""
    ai_daily_call_budget: int = 200

    # --- telegram ---
    telegram_bot_token: str = ""
    telegram_admin_chat_ids: str = ""  # comma separated numeric chat ids
    telegram_notify_chat_ids: str = ""

    # --- live execution (disabled by default; see docs/LIVE_TRADING.md) ---
    live_trading_enabled: bool = False
    live_keystore_path: str = "./keystore/wallet.enc"
    live_keystore_passphrase: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_admin_ids(self) -> set[int]:
        return {int(x) for x in self.telegram_admin_chat_ids.split(",") if x.strip().lstrip("-").isdigit()}

    @property
    def telegram_notify_ids(self) -> set[int]:
        ids = {int(x) for x in self.telegram_notify_chat_ids.split(",") if x.strip().lstrip("-").isdigit()}
        return ids or self.telegram_admin_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()
