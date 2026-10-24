"""Chain-agnostic data types shared by adapters, scanner and traders modules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# Well-known Solana mints used as "quote" assets when reconstructing swaps.
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
STABLE_MINTS = {USDC_MINT, USDT_MINT}
QUOTE_MINTS = {WSOL_MINT, *STABLE_MINTS}
NATIVE_SOL = "SOL"

PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_SWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


@dataclass
class TokenMarket:
    """Normalized market snapshot. ``None`` means *unknown* (never guessed).

    ``estimated`` lists fields that are derived rather than reported by a source;
    ``sources`` maps each field group to the connector that supplied it.
    """

    chain: str
    address: str
    symbol: str | None = None
    name: str | None = None
    pair_address: str | None = None
    dex_id: str | None = None
    price_usd: float | None = None
    market_cap_usd: float | None = None
    fdv_usd: float | None = None
    liquidity_usd: float | None = None
    volume: dict[str, float | None] = field(default_factory=dict)  # m5,h1,h6,h24
    price_change: dict[str, float | None] = field(default_factory=dict)
    txns: dict[str, dict[str, int | None]] = field(default_factory=dict)  # window -> {buys, sells}
    holders: int | None = None
    pool_created_at: datetime | None = None
    boosts_active: int | None = None
    dex_paid: bool | None = None  # paid listing/profile on DexScreener; NOT a legitimacy signal
    graduated: bool | None = None
    labels: list[str] = field(default_factory=list)
    url: str | None = None
    sources: dict[str, str] = field(default_factory=dict)
    estimated: list[str] = field(default_factory=list)
    fetched_at: datetime | None = None

    def age_hours(self, now: datetime) -> float | None:
        if not self.pool_created_at:
            return None
        return (now - self.pool_created_at).total_seconds() / 3600

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("pool_created_at", "fetched_at"):
            if d.get(k):
                d[k] = d[k].isoformat()
        return d


@dataclass
class BalanceDelta:
    mint: str  # SPL mint or "SOL" for native lamports
    owner: str
    change: float  # UI units (decimals applied)
    decimals: int | None = None


@dataclass
class ParsedTx:
    signature: str
    slot: int | None
    block_time: datetime
    fee_payer: str | None
    fee_sol: float
    success: bool
    deltas: list[BalanceDelta]
    programs: list[str] = field(default_factory=list)
    source: str = "solana_rpc"


@dataclass
class TradeEvent:
    """A wallet action reconstructed from a transaction."""

    wallet: str
    signature: str
    slot: int | None
    block_time: datetime
    kind: str  # BUY | SELL | TRANSFER_IN | TRANSFER_OUT
    mint: str
    token_amount: float
    quote_mint: str | None = None
    quote_amount: float | None = None
    quote_usd: float | None = None
    price_usd: float | None = None
    usd_is_estimate: bool = True
    fee_sol: float = 0.0
    source: str = "solana_rpc"
