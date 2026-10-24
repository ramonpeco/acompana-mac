"""DexScreener public API (no key).

Endpoints used (public reference at docs.dexscreener.com/api/reference):
  GET /token-profiles/latest/v1                 (60 req/min)
  GET /token-boosts/latest/v1                   (60 req/min)
  GET /token-boosts/top/v1                      (60 req/min)
  GET /orders/v1/{chainId}/{tokenAddress}       (60 req/min)  paid orders ("DEX paid")
  GET /latest/dex/search?q=                     (300 req/min)
  GET /latest/dex/pairs/{chainId}/{pairId}      (300 req/min)
  GET /token-pairs/v1/{chainId}/{tokenAddress}  (300 req/min)
  GET /tokens/v1/{chainId}/{addresses}          (300 req/min, up to 30 addresses)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.chains.types import TokenMarket
from app.connectors.base import HttpConnector, Sourced, TokenBucket, fnum

CHAIN_IDS = {"solana": "solana", "ethereum": "ethereum", "base": "base", "bsc": "bsc", "arbitrum": "arbitrum"}


class DexScreener(HttpConnector):
    name = "dexscreener"

    def __init__(self, base_url: str = "https://api.dexscreener.com", **kw: Any):
        # 300/min = 5/s for pair endpoints; profile endpoints get a separate 1/s bucket.
        super().__init__(base_url, rate_per_s=4.5, burst=5, cache_ttl_s=15, **kw)
        self.profile_bucket = TokenBucket(0.9, 2)

    async def _profile_get(self, path: str) -> Sourced[Any]:
        await self.profile_bucket.acquire()
        return await self.get(path, cache_ttl_s=60)

    async def latest_profiles(self) -> Sourced[list[dict]]:
        return await self._profile_get("/token-profiles/latest/v1")

    async def latest_boosts(self) -> Sourced[list[dict]]:
        return await self._profile_get("/token-boosts/latest/v1")

    async def top_boosts(self) -> Sourced[list[dict]]:
        return await self._profile_get("/token-boosts/top/v1")

    async def orders(self, chain: str, token: str) -> Sourced[Any]:
        return await self._profile_get(f"/orders/v1/{CHAIN_IDS[chain]}/{token}")

    async def tokens(self, chain: str, addresses: list[str]) -> Sourced[list[dict]]:
        assert len(addresses) <= 30, "DexScreener accepts at most 30 addresses per call"
        return await self.get(f"/tokens/v1/{CHAIN_IDS[chain]}/{','.join(addresses)}")

    async def token_pairs(self, chain: str, address: str) -> Sourced[list[dict]]:
        return await self.get(f"/token-pairs/v1/{CHAIN_IDS[chain]}/{address}")

    async def search(self, q: str) -> Sourced[dict]:
        return await self.get("/latest/dex/search", params={"q": q})


def is_paid_order(orders_payload: Any) -> bool | None:
    """True if any approved paid order exists. None if response shape is unknown."""
    if isinstance(orders_payload, dict):
        orders_payload = orders_payload.get("orders", orders_payload.get("data"))
    if not isinstance(orders_payload, list):
        return None
    return any(isinstance(o, dict) and o.get("status") == "approved" for o in orders_payload)


def best_pair(pairs: list[dict], address: str) -> dict | None:
    """Pick the most liquid pair where the token is the base token."""
    own = [p for p in pairs if (p.get("baseToken") or {}).get("address") == address] or pairs
    if not own:
        return None
    return max(own, key=lambda p: fnum((p.get("liquidity") or {}).get("usd")) or 0.0)


def pair_to_market(pair: dict, fetched_at: datetime | None = None) -> TokenMarket:
    base = pair.get("baseToken") or {}
    liq = pair.get("liquidity") or {}
    created = pair.get("pairCreatedAt")
    txns = {
        w: {"buys": (v or {}).get("buys"), "sells": (v or {}).get("sells")}
        for w, v in (pair.get("txns") or {}).items()
    }
    labels = list(pair.get("labels") or [])
    dex_id = pair.get("dexId")
    address = base.get("address", "")
    graduated: bool | None = None
    if address.endswith("pump"):
        # pump.fun mints end in "pump". Once a pool exists outside the bonding curve
        # (PumpSwap / Raydium / Meteora) the token has migrated ("graduated").
        graduated = dex_id not in (None, "pumpfun")
    return TokenMarket(
        chain=pair.get("chainId", "solana"),
        address=address,
        symbol=base.get("symbol"),
        name=base.get("name"),
        pair_address=pair.get("pairAddress"),
        dex_id=dex_id,
        price_usd=fnum(pair.get("priceUsd")),
        market_cap_usd=fnum(pair.get("marketCap")),
        fdv_usd=fnum(pair.get("fdv")),
        liquidity_usd=fnum(liq.get("usd")),
        volume={k: fnum(v) for k, v in (pair.get("volume") or {}).items()},
        price_change={k: fnum(v) for k, v in (pair.get("priceChange") or {}).items()},
        txns=txns,
        pool_created_at=datetime.fromtimestamp(created / 1000, UTC) if created else None,
        boosts_active=(pair.get("boosts") or {}).get("active"),
        graduated=graduated,
        labels=labels,
        url=pair.get("url"),
        sources={"market": "dexscreener"},
        estimated=["graduated"] if graduated is not None else [],
        fetched_at=fetched_at,
    )
