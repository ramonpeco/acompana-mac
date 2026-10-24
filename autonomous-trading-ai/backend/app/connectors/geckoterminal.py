"""GeckoTerminal public API v2 (no key, ~30 calls/min; CoinGecko docs cite ~10/min
for keyless use, so we default to a conservative 0.3 req/s).

Endpoints used:
  GET /networks/{network}/trending_pools?include=base_token,quote_token,dex
  GET /networks/{network}/new_pools?include=base_token,quote_token,dex
  GET /networks/{network}/pools/{pool}/trades        (last 300 trades, 24h)
  GET /networks/{network}/pools/{pool}/ohlcv/{timeframe}?aggregate=&limit=
  GET /networks/{network}/tokens/{address}
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.chains.types import TokenMarket
from app.connectors.base import HttpConnector, Sourced, fnum

NETWORKS = {"solana": "solana", "ethereum": "eth", "base": "base", "bsc": "bsc", "arbitrum": "arbitrum"}


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


class GeckoTerminal(HttpConnector):
    name = "geckoterminal"

    def __init__(self, base_url: str = "https://api.geckoterminal.com/api/v2", **kw: Any):
        super().__init__(base_url, rate_per_s=0.3, burst=2, cache_ttl_s=45, **kw)

    async def trending_pools(self, chain: str, page: int = 1) -> Sourced[dict]:
        return await self.get(
            f"/networks/{NETWORKS[chain]}/trending_pools",
            params={"include": "base_token,quote_token,dex", "page": page},
        )

    async def new_pools(self, chain: str, page: int = 1) -> Sourced[dict]:
        return await self.get(
            f"/networks/{NETWORKS[chain]}/new_pools",
            params={"include": "base_token,quote_token,dex", "page": page},
        )

    async def pool_trades(self, chain: str, pool: str, min_volume_usd: float | None = None) -> Sourced[dict]:
        params = {"trade_volume_in_usd_greater_than": min_volume_usd} if min_volume_usd else None
        return await self.get(f"/networks/{NETWORKS[chain]}/pools/{pool}/trades", params=params, cache_ttl_s=20)

    async def ohlcv(
        self, chain: str, pool: str, timeframe: str = "minute", aggregate: int = 5, limit: int = 300
    ) -> Sourced[dict]:
        return await self.get(
            f"/networks/{NETWORKS[chain]}/pools/{pool}/ohlcv/{timeframe}",
            params={"aggregate": aggregate, "limit": limit, "currency": "usd"},
            cache_ttl_s=60,
        )


def _included_map(payload: dict) -> dict[str, dict]:
    return {f"{i.get('type')}:{i.get('id')}": i for i in payload.get("included", []) or []}


def pools_to_markets(payload: dict, chain: str, fetched_at: datetime | None = None) -> list[TokenMarket]:
    inc = _included_map(payload)
    out: list[TokenMarket] = []
    for pool in payload.get("data", []) or []:
        a = pool.get("attributes", {})
        rel = pool.get("relationships", {})
        base_ref = (rel.get("base_token") or {}).get("data") or {}
        base = inc.get(f"token:{base_ref.get('id')}", {}).get("attributes", {})
        address = base.get("address") or (base_ref.get("id", "").split("_", 1)[-1])
        dex_id = ((rel.get("dex") or {}).get("data") or {}).get("id")
        tx = a.get("transactions") or {}
        txns = {w: {"buys": (v or {}).get("buys"), "sells": (v or {}).get("sells")} for w, v in tx.items()}
        graduated = None
        if address.endswith("pump"):
            graduated = dex_id not in (None, "pump-fun")
        out.append(
            TokenMarket(
                chain=chain,
                address=address,
                symbol=base.get("symbol"),
                name=base.get("name"),
                pair_address=a.get("address"),
                dex_id=dex_id,
                price_usd=fnum(a.get("base_token_price_usd")),
                market_cap_usd=fnum(a.get("market_cap_usd")),
                fdv_usd=fnum(a.get("fdv_usd")),
                liquidity_usd=fnum(a.get("reserve_in_usd")),
                volume={k: fnum(v) for k, v in (a.get("volume_usd") or {}).items()},
                price_change={k: fnum(v) for k, v in (a.get("price_change_percentage") or {}).items()},
                txns=txns,
                pool_created_at=_parse_dt(a.get("pool_created_at")),
                graduated=graduated,
                sources={"market": "geckoterminal"},
                estimated=["graduated"] if graduated is not None else [],
                fetched_at=fetched_at,
            )
        )
    return out


def parse_trades(payload: dict) -> list[dict[str, Any]]:
    """Normalize pool trades. ``kind`` is buy/sell of the pool's base token."""
    trades = []
    for t in payload.get("data", []) or []:
        a = t.get("attributes", {})
        trades.append(
            {
                "tx_hash": a.get("tx_hash"),
                "wallet": a.get("tx_from_address"),
                "kind": a.get("kind"),
                "volume_usd": fnum(a.get("volume_in_usd")),
                "block_time": _parse_dt(a.get("block_timestamp")),
                "from_token": a.get("from_token_address"),
                "to_token": a.get("to_token_address"),
            }
        )
    return trades


def parse_ohlcv(payload: dict) -> list[tuple[datetime, float, float, float, float, float]]:
    rows = (((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
    out = []
    for r in rows:
        if len(r) >= 6:
            out.append((datetime.fromtimestamp(int(r[0]), UTC), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
    return sorted(out)
