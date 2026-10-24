"""Solana JSON-RPC (standard methods; works with any provider, incl. Helius RPC).

Methods used: getSignaturesForAddress, getTransaction (jsonParsed, v0),
getSignatureStatuses, getAccountInfo (jsonParsed), getTokenLargestAccounts,
getTokenSupply, getSlot.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.chains.types import NATIVE_SOL, BalanceDelta, ParsedTx
from app.connectors.base import ConnectorError, HttpConnector, Sourced

LAMPORTS = 1_000_000_000


class SolanaRPC(HttpConnector):
    name = "solana_rpc"

    def __init__(self, url: str, rate_per_s: float = 4.0, **kw: Any):
        super().__init__(url, rate_per_s=rate_per_s, burst=max(1, int(rate_per_s)), cache_ttl_s=0, **kw)
        self._ids = itertools.count(1)

    async def call(self, method: str, params: list[Any] | None = None, cache_ttl_s: float = 0) -> Sourced[Any]:
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or []}
        res = await self.request("POST", self.base_url, json_body=body, cache_ttl_s=cache_ttl_s)
        payload = res.value
        if isinstance(payload, dict) and payload.get("error"):
            err = payload["error"]
            raise ConnectorError(self.name, f"RPC error {err.get('code')}: {err.get('message')}")
        return Sourced(payload.get("result"), self.name, res.fetched_at, res.cached)

    async def get_slot(self) -> int:
        return (await self.call("getSlot", [{"commitment": "confirmed"}])).value

    async def signatures_for_address(
        self, address: str, *, before: str | None = None, until: str | None = None, limit: int = 100
    ) -> list[dict]:
        opts: dict[str, Any] = {"limit": min(limit, 1000), "commitment": "confirmed"}
        if before:
            opts["before"] = before
        if until:
            opts["until"] = until
        return (await self.call("getSignaturesForAddress", [address, opts])).value or []

    async def transaction(self, signature: str, commitment: str = "confirmed") -> dict | None:
        return (
            await self.call(
                "getTransaction",
                [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": commitment}],
                cache_ttl_s=300,
            )
        ).value

    async def signature_statuses(self, signatures: list[str]) -> list[dict | None]:
        res = await self.call("getSignatureStatuses", [signatures, {"searchTransactionHistory": True}])
        return (res.value or {}).get("value") or []

    async def account_info(self, address: str) -> dict | None:
        res = await self.call("getAccountInfo", [address, {"encoding": "jsonParsed"}], cache_ttl_s=300)
        return (res.value or {}).get("value")

    async def token_largest_accounts(self, mint: str) -> list[dict]:
        res = await self.call("getTokenLargestAccounts", [mint], cache_ttl_s=120)
        return (res.value or {}).get("value") or []

    async def token_supply(self, mint: str) -> dict | None:
        res = await self.call("getTokenSupply", [mint], cache_ttl_s=300)
        return (res.value or {}).get("value")


def _key(k: Any) -> str:
    return k["pubkey"] if isinstance(k, dict) else str(k)


def parse_transaction(tx: dict, signature: str | None = None) -> ParsedTx:
    """Turn a jsonParsed getTransaction result into per-owner balance deltas."""
    meta = tx.get("meta") or {}
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = [_key(k) for k in msg.get("accountKeys") or []]
    # v0 transactions with lookup tables append loaded addresses after static keys
    loaded = meta.get("loadedAddresses") or {}
    keys += list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
    sig = signature or ((tx.get("transaction") or {}).get("signatures") or [""])[0]
    fee_payer = keys[0] if keys else None
    fee_sol = (meta.get("fee") or 0) / LAMPORTS

    deltas: list[BalanceDelta] = []
    pre_b, post_b = meta.get("preBalances") or [], meta.get("postBalances") or []
    native: dict[str, float] = defaultdict(float)
    for i, (a, b) in enumerate(zip(pre_b, post_b, strict=False)):
        if i < len(keys) and a != b:
            native[keys[i]] += (b - a) / LAMPORTS
    if fee_payer in native:
        native[fee_payer] += fee_sol  # report the network fee separately
    for owner, ch in native.items():
        if abs(ch) > 1e-12:
            deltas.append(BalanceDelta(NATIVE_SOL, owner, ch, 9))

    tok: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    decs: dict[str, int] = {}
    for field_name, idx in (("preTokenBalances", 0), ("postTokenBalances", 1)):
        for tb in meta.get(field_name) or []:
            owner = tb.get("owner")
            if not owner:
                ai = tb.get("accountIndex")
                owner = keys[ai] if ai is not None and ai < len(keys) else "?"
            ui = tb.get("uiTokenAmount") or {}
            amt = ui.get("uiAmountString") or ui.get("uiAmount") or 0
            decs[tb["mint"]] = ui.get("decimals")
            tok[(owner, tb["mint"])][idx] += float(amt or 0)
    for (owner, mint), (pre, post) in tok.items():
        if abs(post - pre) > 0:
            deltas.append(BalanceDelta(mint, owner, post - pre, decs.get(mint)))

    programs = []
    for ins in msg.get("instructions") or []:
        pid = ins.get("programId")
        if pid and pid not in programs:
            programs.append(pid)
    bt = tx.get("blockTime")
    return ParsedTx(
        signature=sig,
        slot=tx.get("slot"),
        block_time=datetime.fromtimestamp(bt, UTC) if bt else datetime.now(UTC),
        fee_payer=fee_payer,
        fee_sol=fee_sol,
        success=meta.get("err") is None,
        deltas=deltas,
        programs=programs,
    )
