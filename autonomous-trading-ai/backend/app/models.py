"""Database schema.

Monetary values are stored as floats (USD / token units). This is a research and
paper-trading system; the live executor works with integer base units from Jupiter
quotes and never relies on these floats for on-chain amounts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class TZDateTime(TypeDecorator):
    """DateTime that is always stored as UTC and comes back timezone-aware (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            value = value.astimezone(UTC)
            if dialect.name == "sqlite":
                value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="admin")
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))  # username, "agent", "telegram:<id>"
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Setting(Base):
    """Runtime-editable configuration (risk limits, kill switch, copy params)."""

    __tablename__ = "settings_kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")


class Token(Base):
    __tablename__ = "tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain: Mapped[str] = mapped_column(String(16), default="solana")
    address: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(128))
    decimals: Mapped[int | None] = mapped_column(Integer)
    pool_created_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    first_seen: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    market: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # latest normalized snapshot
    risk: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # latest risk report
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_token_chain_address"),)


class TokenPricePoint(Base):
    """Observed price points, used for unrealized PnL and for backtest replays."""

    __tablename__ = "token_price_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain: Mapped[str] = mapped_column(String(16), default="solana")
    address: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(TZDateTime())
    price_usd: Mapped[float] = mapped_column(Float)
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))
    __table_args__ = (Index("ix_price_addr_ts", "address", "ts"),)


class Wallet(Base):
    __tablename__ = "wallets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain: Mapped[str] = mapped_column(String(16), default="solana")
    address: Mapped[str] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(32), default="discovered")  # manual | discovered
    discovered_via: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="candidate")  # candidate|excluded|followed|retired
    excluded_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    first_seen: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    first_tx_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    last_trade_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    last_synced_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    newest_signature: Mapped[str | None] = mapped_column(String(100))
    oldest_signature: Mapped[str | None] = mapped_column(String(100))
    history_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_wallet_chain_address"),)


class WalletTrade(Base):
    """A normalized wallet action reconstructed from an on-chain transaction."""

    __tablename__ = "wallet_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet: Mapped[str] = mapped_column(String(64), index=True)
    signature: Mapped[str] = mapped_column(String(100))
    slot: Mapped[int | None] = mapped_column(Integer)
    block_time: Mapped[datetime] = mapped_column(TZDateTime(), index=True)
    detected_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    # BUY | SELL | TRANSFER_IN | TRANSFER_OUT | UNKNOWN
    kind: Mapped[str] = mapped_column(String(16))
    mint: Mapped[str] = mapped_column(String(64))
    token_amount: Mapped[float] = mapped_column(Float)
    quote_mint: Mapped[str | None] = mapped_column(String(64))
    quote_amount: Mapped[float | None] = mapped_column(Float)
    quote_usd: Mapped[float | None] = mapped_column(Float)
    price_usd: Mapped[float | None] = mapped_column(Float)
    usd_is_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    fee_sol: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")  # confirmed|finalized|dropped
    source: Mapped[str] = mapped_column(String(32), default="solana_rpc")
    __table_args__ = (
        UniqueConstraint("wallet", "signature", "mint", name="uq_wallet_sig_mint"),
        Index("ix_wallet_trades_wallet_time", "wallet", "block_time"),
    )


class TraderScore(Base):
    __tablename__ = "trader_scores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet: Mapped[str] = mapped_column(String(64), index=True)
    computed_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    score: Mapped[float] = mapped_column(Float)
    eligible: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    components: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scoring_version: Mapped[str] = mapped_column(String(16), default="v1")


class Portfolio(Base):
    """A paper account. kind=main is the agent's account; kind=shadow tracks
    counterfactual results of traders we stopped following."""

    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(96), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="main")  # main | shadow | backtest
    initial_capital: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    peak_equity: Mapped[float] = mapped_column(Float)
    day_start_equity: Mapped[float] = mapped_column(Float)
    day_start_date: Mapped[str] = mapped_column(String(10), default="")
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_paid: Mapped[float] = mapped_column(Float, default=0.0)
    tracked_wallet: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    mint: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    cost_basis: Mapped[float] = mapped_column(Float, default=0.0)  # USD of remaining qty, incl. buy fees
    invested_total: Mapped[float] = mapped_column(Float, default=0.0)
    proceeds_total: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    last_price: Mapped[float | None] = mapped_column(Float)
    last_price_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    source_wallet: Mapped[str | None] = mapped_column(String(64))
    trader_qty: Mapped[float] = mapped_column(Float, default=0.0)  # trader's holdings we mirror
    follow_session_id: Mapped[int | None] = mapped_column(ForeignKey("follow_sessions.id"))
    stop_loss_pct: Mapped[float | None] = mapped_column(Float)
    take_profit_pct: Mapped[float | None] = mapped_column(Float)
    close_reason: Mapped[str | None] = mapped_column(String(48))
    opened_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    mint: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(48), default="copy")
    status: Mapped[str] = mapped_column(String(16))  # filled | partial | failed | rejected
    reject_reason: Mapped[str | None] = mapped_column(Text)
    requested_usd: Mapped[float | None] = mapped_column(Float)
    requested_qty: Mapped[float | None] = mapped_column(Float)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    filled_usd: Mapped[float] = mapped_column(Float, default=0.0)
    reference_price: Mapped[float | None] = mapped_column(Float)  # market price when we decided
    exec_price: Mapped[float | None] = mapped_column(Float)
    trader_price: Mapped[float | None] = mapped_column(Float)
    fee_usd: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_bps: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    quote_source: Mapped[str | None] = mapped_column(String(32))
    trader_signature: Mapped[str | None] = mapped_column(String(100))
    trader_block_time: Mapped[datetime | None] = mapped_column(TZDateTime())
    detected_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    executed_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    risk_checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    exposure: Mapped[float] = mapped_column(Float)
    drawdown_pct: Mapped[float] = mapped_column(Float)


class FollowSession(Base):
    __tablename__ = "follow_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    wallet: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    end_reason: Mapped[str | None] = mapped_column(String(64))
    selection: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    score_at_start: Mapped[float | None] = mapped_column(Float)
    closed_trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    peak_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    strategy_version: Mapped[str] = mapped_column(String(32), default="default")
    shadow_portfolio_id: Mapped[int | None] = mapped_column(Integer)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(8), default="info")  # info|warn|error|alert
    kind: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AIAnalysis(Base):
    __tablename__ = "ai_analyses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(32))  # candidate_review | switch_explanation | daily_report | failure_analysis
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class StrategyVersion(Base):
    """Versioned strategy parameter sets. AI may propose (status=proposed);
    only an admin can approve/activate, and activation requires OOS results."""

    __tablename__ = "strategy_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed|approved|active|retired|rejected
    proposed_by: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str | None] = mapped_column(Text)
    oos_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="done")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    data_source: Mapped[str] = mapped_column(String(32), default="recorded")  # recorded | synthetic
