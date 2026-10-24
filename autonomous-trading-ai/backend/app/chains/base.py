"""Chain adapter interface. One implementation per network; Solana first."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.chains.types import ParsedTx, TokenMarket


class ChainAdapter(ABC):
    chain: str
    data_label: str = "live"  # "live" or "synthetic"

    @abstractmethod
    async def markets(self, addresses: list[str]) -> dict[str, TokenMarket]:
        """Best-pair market data for each token (missing tokens omitted)."""

    @abstractmethod
    async def trending(self) -> list[TokenMarket]: ...

    @abstractmethod
    async def new_pairs(self) -> list[TokenMarket]: ...

    @abstractmethod
    async def boosted(self) -> list[dict[str, Any]]:
        """Boost/profile listings: [{address, boosts, has_profile}]"""

    @abstractmethod
    async def dex_paid(self, address: str) -> bool | None: ...

    @abstractmethod
    async def prices(self, addresses: list[str]) -> dict[str, float]: ...

    @abstractmethod
    async def native_price_usd(self) -> float | None: ...

    @abstractmethod
    async def wallet_transactions(
        self, wallet: str, *, until: str | None = None, before: str | None = None, limit: int = 50
    ) -> list[ParsedTx]:
        """Newest-first parsed transactions (successful and failed)."""

    @abstractmethod
    async def pool_traders(self, pool: str) -> list[dict[str, Any]]:
        """Recent trades on a pool: [{wallet, kind, volume_usd, block_time, tx_hash}]"""

    @abstractmethod
    async def token_onchain(self, address: str) -> dict[str, Any]:
        """Authorities, supply, top holders, token-program extensions."""

    @abstractmethod
    async def quote(self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict | None:
        """Executable-route quote summary or None if unavailable."""

    @abstractmethod
    async def is_finalized(self, signature: str) -> bool | None: ...

    @abstractmethod
    def health(self) -> list[dict[str, Any]]: ...

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
