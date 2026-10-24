"""Helius (optional, needs HELIUS_API_KEY).

* RPC:      https://mainnet.helius-rpc.com/?api-key=KEY   (standard Solana JSON-RPC)
* Enhanced: GET https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions
            ?api-key=&type=SWAP&before-signature=&limit=
  (helius-labs/helius-sdk src/enhanced). Helius marks Enhanced Transactions as
  maintenance-mode; we use it only as an accelerator and fall back to plain RPC.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.chains.types import NATIVE_SOL, BalanceDelta, ParsedTx
from app.connectors.base import HttpConnector, fnum

LAMPORTS = 1_000_000_000


def helius_rpc_url(api_key: str) -> str:
    return f"https://mainnet.helius-rpc.com/?api-key={api_key}"


class HeliusEnhanced(HttpConnector):
    name = "helius"

    def __init__(self, api_key: str, base_url: str = "https://api-mainnet.helius-rpc.com/v0", **kw: Any):
        super().__init__(base_url, rate_per_s=2.0, burst=2, cache_ttl_s=10, **kw)
        self.api_key = api_key

    async def address_transactions(
        self, address: str, *, before: str | None = None, tx_type: str | None = "SWAP", limit: int = 100
    ) -> list[dict]:
        params: dict[str, Any] = {"api-key": self.api_key, "limit": min(limit, 100)}
        if tx_type:
            params["type"] = tx_type
        if before:
            params["before-signature"] = before
        return (await self.get(f"/addresses/{address}/transactions", params=params)).value or []


def enhanced_to_parsed(tx: dict) -> ParsedTx:
    """Convert an enhanced transaction into balance deltas using its transfer lists."""
    deltas: dict[tuple[str, str], float] = defaultdict(float)
    decs: dict[str, int | None] = {}
    for nt in tx.get("nativeTransfers") or []:
        amt = (nt.get("amount") or 0) / LAMPORTS
        if nt.get("fromUserAccount"):
            deltas[(nt["fromUserAccount"], NATIVE_SOL)] -= amt
        if nt.get("toUserAccount"):
            deltas[(nt["toUserAccount"], NATIVE_SOL)] += amt
    for tt in tx.get("tokenTransfers") or []:
        amt = fnum(tt.get("tokenAmount")) or 0.0
        mint = tt.get("mint")
        decs[mint] = tt.get("decimals")
        if tt.get("fromUserAccount"):
            deltas[(tt["fromUserAccount"], mint)] -= amt
        if tt.get("toUserAccount"):
            deltas[(tt["toUserAccount"], mint)] += amt
    ts = tx.get("timestamp")
    return ParsedTx(
        signature=tx["signature"],
        slot=tx.get("slot"),
        block_time=datetime.fromtimestamp(ts, UTC) if ts else datetime.now(UTC),
        fee_payer=tx.get("feePayer"),
        fee_sol=(tx.get("fee") or 0) / LAMPORTS,
        success=not tx.get("transactionError"),
        deltas=[BalanceDelta(m, o, v, decs.get(m)) for (o, m), v in deltas.items() if abs(v) > 0],
        programs=[tx.get("source")] if tx.get("source") else [],
        source="helius",
    )
