"""Solana adapter composed from public connectors."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.chains.base import ChainAdapter
from app.chains.types import WSOL_MINT, ParsedTx, TokenMarket
from app.config import Settings
from app.connectors.base import ConnectorError
from app.connectors.dexscreener import DexScreener, best_pair, is_paid_order, pair_to_market
from app.connectors.geckoterminal import GeckoTerminal, parse_trades, pools_to_markets
from app.connectors.helius import HeliusEnhanced, enhanced_to_parsed, helius_rpc_url
from app.connectors.jupiter import Jupiter, price_of, quote_summary
from app.connectors.solana_rpc import SolanaRPC, parse_transaction

log = logging.getLogger(__name__)

TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
RISKY_EXTENSIONS = {"transferFeeConfig", "permanentDelegate", "nonTransferable", "transferHook", "defaultAccountState"}


class SolanaAdapter(ChainAdapter):
    chain = "solana"

    def __init__(self, settings: Settings):
        rpc_url = settings.solana_rpc_url
        if settings.helius_api_key and "mainnet-beta" in rpc_url:
            rpc_url = helius_rpc_url(settings.helius_api_key)
        self.rpc = SolanaRPC(rpc_url, rate_per_s=settings.solana_rpc_rps)
        self.dex = DexScreener(settings.dexscreener_base_url)
        self.gecko = GeckoTerminal(settings.geckoterminal_base_url)
        self.jup = Jupiter(settings.jupiter_base_url, settings.jupiter_api_key)
        self.helius = HeliusEnhanced(settings.helius_api_key) if settings.helius_api_key else None

    def health(self) -> list[dict[str, Any]]:
        conns = [self.rpc, self.dex, self.gecko, self.jup] + ([self.helius] if self.helius else [])
        return [c.health.as_dict() for c in conns]

    async def aclose(self) -> None:
        for c in (self.rpc, self.dex, self.gecko, self.jup, self.helius):
            if c:
                await c.aclose()

    async def markets(self, addresses: list[str]) -> dict[str, TokenMarket]:
        out: dict[str, TokenMarket] = {}
        uniq = list(dict.fromkeys(addresses))
        for i in range(0, len(uniq), 30):
            chunk = uniq[i : i + 30]
            res = await self.dex.tokens("solana", chunk)
            by_token: dict[str, list[dict]] = {}
            for p in res.value or []:
                by_token.setdefault((p.get("baseToken") or {}).get("address"), []).append(p)
            for addr in chunk:
                bp = best_pair(by_token.get(addr, []), addr)
                if bp:
                    out[addr] = pair_to_market(bp, res.fetched_at)
        return out

    async def trending(self) -> list[TokenMarket]:
        res = await self.gecko.trending_pools("solana")
        return pools_to_markets(res.value, "solana", res.fetched_at)

    async def new_pairs(self) -> list[TokenMarket]:
        res = await self.gecko.new_pools("solana")
        return pools_to_markets(res.value, "solana", res.fetched_at)

    async def boosted(self) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        try:
            for b in (await self.dex.top_boosts()).value or []:
                if b.get("chainId") == "solana":
                    out[b["tokenAddress"]] = {"address": b["tokenAddress"], "boosts": b.get("totalAmount") or b.get("amount"), "has_profile": False}
        except ConnectorError as exc:
            log.info("boosts unavailable: %s", exc)
        try:
            for p in (await self.dex.latest_profiles()).value or []:
                if p.get("chainId") == "solana":
                    out.setdefault(p["tokenAddress"], {"address": p["tokenAddress"], "boosts": None})["has_profile"] = True
        except ConnectorError as exc:
            log.info("profiles unavailable: %s", exc)
        return list(out.values())

    async def dex_paid(self, address: str) -> bool | None:
        try:
            return is_paid_order((await self.dex.orders("solana", address)).value)
        except ConnectorError:
            return None

    async def prices(self, addresses: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        uniq = list(dict.fromkeys(addresses))
        for i in range(0, len(uniq), 50):
            chunk = uniq[i : i + 50]
            try:
                res = await self.jup.prices(chunk)
                for m in chunk:
                    p = price_of(res.value or {}, m)
                    if p:
                        out[m] = p
            except ConnectorError as exc:
                log.info("jupiter price failed (%s); falling back to dexscreener", exc)
        missing = [m for m in uniq if m not in out]
        if missing:
            try:
                for m, mk in (await self.markets(missing)).items():
                    if mk.price_usd:
                        out[m] = mk.price_usd
            except ConnectorError as exc:
                log.info("dexscreener price fallback failed: %s", exc)
        return out

    async def native_price_usd(self) -> float | None:
        return (await self.prices([WSOL_MINT])).get(WSOL_MINT)

    async def wallet_transactions(
        self, wallet: str, *, until: str | None = None, before: str | None = None, limit: int = 50
    ) -> list[ParsedTx]:
        if self.helius:
            try:
                txs = await self.helius.address_transactions(wallet, before=before, tx_type=None, limit=limit)
                out = []
                for t in txs:
                    if until and t.get("signature") == until:
                        break
                    out.append(enhanced_to_parsed(t))
                return out
            except ConnectorError as exc:
                log.info("helius enhanced failed (%s); using RPC", exc)
        sigs = await self.rpc.signatures_for_address(wallet, before=before, until=until, limit=limit)
        out: list[ParsedTx] = []
        for s in sigs:
            if s.get("err") is not None:
                continue  # failed transactions never move balances besides fees
            tx = await self.rpc.transaction(s["signature"])
            if tx:
                out.append(parse_transaction(tx, s["signature"]))
        return out

    async def pool_traders(self, pool: str) -> list[dict[str, Any]]:
        return parse_trades((await self.gecko.pool_trades("solana", pool)).value)

    async def token_onchain(self, address: str) -> dict[str, Any]:
        info: dict[str, Any] = {"address": address}
        acct = await self.rpc.account_info(address)
        if acct:
            parsed = ((acct.get("data") or {}).get("parsed") or {}).get("info") or {}
            info["program"] = "token-2022" if acct.get("owner") == TOKEN_2022 else "spl-token"
            info["mint_authority"] = parsed.get("mintAuthority")
            info["freeze_authority"] = parsed.get("freezeAuthority")
            info["decimals"] = parsed.get("decimals")
            exts = [e.get("extension") for e in parsed.get("extensions") or [] if isinstance(e, dict)]
            info["extensions"] = exts
            info["risky_extensions"] = sorted(set(exts) & RISKY_EXTENSIONS)
        supply, largest = await asyncio.gather(
            self.rpc.token_supply(address), self.rpc.token_largest_accounts(address), return_exceptions=True
        )
        if isinstance(supply, dict):
            info["supply"] = float(supply.get("uiAmountString") or supply.get("uiAmount") or 0)
        if isinstance(largest, list):
            info["largest_accounts"] = [
                {"address": a.get("address"), "amount": float(a.get("uiAmountString") or a.get("uiAmount") or 0)}
                for a in largest
            ]
        return info

    async def quote(self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict | None:
        try:
            return quote_summary((await self.jup.quote(input_mint, output_mint, amount_raw, slippage_bps)).value)
        except (ConnectorError, KeyError, ValueError) as exc:
            log.info("quote unavailable: %s", exc)
            return None

    async def is_finalized(self, signature: str) -> bool | None:
        st = await self.rpc.signature_statuses([signature])
        if not st or st[0] is None:
            return None
        return st[0].get("confirmationStatus") == "finalized" and st[0].get("err") is None
