"""Wallet discovery, history sync and ranking."""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.base import ChainAdapter
from app.chains.types import TradeEvent
from app.connectors.base import ConnectorError
from app.models import Token, TraderScore, Wallet, WalletTrade
from app.traders.metrics import compute_metrics
from app.traders.scoring import SCORING_VERSION, ScoringConfig, score_trader
from app.traders.swaps import classify

log = logging.getLogger(__name__)
B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def valid_solana_address(addr: str) -> bool:
    return bool(B58_RE.match(addr or ""))


class TraderService:
    def __init__(self, adapter: ChainAdapter, scoring: ScoringConfig | None = None, now_fn=None):
        self.adapter = adapter
        self.now = now_fn or (lambda: datetime.now(UTC))
        self.scoring = scoring or ScoringConfig()
        self._sol_price: tuple[datetime, float] | None = None

    async def sol_price(self) -> float | None:
        now = self.now()
        if self._sol_price and (now - self._sol_price[0]).total_seconds() < 60:
            return self._sol_price[1]
        try:
            p = await self.adapter.native_price_usd()
        except ConnectorError:
            p = None
        if p:
            self._sol_price = (now, p)
            return p
        return self._sol_price[1] if self._sol_price else None

    async def add_wallet(self, session: AsyncSession, address: str, *, origin: str = "manual",
                         label: str | None = None, via: dict | None = None) -> Wallet:
        if not valid_solana_address(address):
            raise ValueError("invalid Solana address")
        w = (await session.execute(select(Wallet).where(Wallet.address == address))).scalar_one_or_none()
        if w is None:
            w = Wallet(address=address, origin=origin, label=label, discovered_via=via or {})
            session.add(w)
            await session.flush()
        elif label:
            w.label = label
        return w

    async def sync_wallet(self, session: AsyncSession, wallet: Wallet, *, backfill_pages: int = 0,
                          page_size: int = 50) -> list[TradeEvent]:
        """Fetch new transactions (and optionally older pages). Returns newly stored events, oldest first."""
        sol_usd = await self.sol_price()
        new_txs = await self.adapter.wallet_transactions(wallet.address, until=wallet.newest_signature, limit=page_size)
        older = []
        before = wallet.oldest_signature
        for _ in range(backfill_pages):
            if wallet.history_complete:
                break
            if before is None and not new_txs:
                page = await self.adapter.wallet_transactions(wallet.address, limit=page_size)
            else:
                page = await self.adapter.wallet_transactions(wallet.address, before=before or new_txs[-1].signature, limit=page_size)
            if not page:
                wallet.history_complete = True
                break
            older.extend(page)
            before = page[-1].signature
        txs = new_txs + older
        if new_txs:
            wallet.newest_signature = new_txs[0].signature
        if txs:
            wallet.oldest_signature = txs[-1].signature if (older or wallet.oldest_signature is None) else wallet.oldest_signature
        events: list[TradeEvent] = []
        for tx in txs:
            events.extend(classify(tx, wallet.address, sol_usd))
        if not events:
            wallet.last_synced_at = self.now()
            return []
        existing = set(
            (await session.execute(
                select(WalletTrade.signature, WalletTrade.mint).where(
                    WalletTrade.wallet == wallet.address,
                    WalletTrade.signature.in_([e.signature for e in events]),
                )
            )).all()
        )
        stored = []
        now = self.now()
        for e in sorted(events, key=lambda e: e.block_time):
            if (e.signature, e.mint) in existing:
                continue  # dedup (re-delivered / overlapping pages)
            existing.add((e.signature, e.mint))
            session.add(WalletTrade(
                wallet=e.wallet, signature=e.signature, slot=e.slot, block_time=e.block_time, detected_at=now,
                kind=e.kind, mint=e.mint, token_amount=e.token_amount, quote_mint=e.quote_mint,
                quote_amount=e.quote_amount, quote_usd=e.quote_usd, price_usd=e.price_usd,
                usd_is_estimate=e.usd_is_estimate, fee_sol=e.fee_sol, source=e.source,
            ))
            stored.append(e)
        if stored:
            last = max(e.block_time for e in stored if e.kind in ("BUY", "SELL")) if any(
                e.kind in ("BUY", "SELL") for e in stored) else None
            if last and (wallet.last_trade_at is None or last > wallet.last_trade_at):
                wallet.last_trade_at = last
            first = min(e.block_time for e in stored)
            if wallet.first_tx_at is None or first < wallet.first_tx_at:
                wallet.first_tx_at = first
        wallet.last_synced_at = now
        return new_events_filter(stored, new_txs)

    async def discover(self, session: AsyncSession, *, max_pools: int = 8, max_new: int = 25,
                       min_buys: int = 1) -> dict[str, Any]:
        """Find wallets that recently traded trending/new tokens (public pool trades)."""
        toks = (await session.execute(select(Token).order_by(Token.updated_at.desc()).limit(300))).scalars().all()
        toks = [t for t in toks if (t.market or {}).get("pair_address") and (t.market or {}).get("liquidity_usd")]
        toks.sort(key=lambda t: ((t.market or {}).get("volume") or {}).get("h24") or 0, reverse=True)
        counts: Counter[str] = Counter()
        vol: dict[str, float] = defaultdict(float)
        via: dict[str, list[str]] = defaultdict(list)
        errors = []
        for t in toks[:max_pools]:
            try:
                trades = await self.adapter.pool_traders(t.market["pair_address"])
            except ConnectorError as exc:
                errors.append(str(exc))
                continue
            for tr in trades:
                w = tr.get("wallet")
                if not w or not valid_solana_address(w):
                    continue
                if tr.get("kind") == "buy":
                    counts[w] += 1
                vol[w] += tr.get("volume_usd") or 0
                if t.symbol and t.symbol not in via[w]:
                    via[w].append(t.symbol)
        known = set((await session.execute(select(Wallet.address))).scalars().all())
        # prefer wallets active across several tokens with meaningful size (less likely bots/one-offs)
        cands = sorted((w for w in counts if counts[w] >= min_buys and w not in known),
                       key=lambda w: (len(via[w]), vol[w]), reverse=True)
        added = []
        for w in cands[:max_new]:
            await self.add_wallet(session, w, origin="discovered", via={"tokens": via[w][:10], "pool_volume_usd": round(vol[w], 2)})
            added.append(w)
        return {"scanned_pools": min(len(toks), max_pools), "candidates": len(cands), "added": added, "errors": errors}

    async def rank(self, session: AsyncSession, *, as_of: datetime | None = None,
                   lookback_days: float = 30) -> list[dict[str, Any]]:
        now = as_of or self.now()
        wallets = (await session.execute(select(Wallet))).scalars().all()
        liq = {t.address: (t.market or {}).get("liquidity_usd") for t in
               (await session.execute(select(Token))).scalars().all()}
        prices = {a: (m or 0) for a, m in ((t.address, (t.market or {}).get("price_usd")) for t in
                  (await session.execute(select(Token))).scalars().all()) if m}
        out = []
        for w in wallets:
            trades = (await session.execute(
                select(WalletTrade).where(WalletTrade.wallet == w.address, WalletTrade.status != "dropped",
                                          WalletTrade.block_time <= now,
                                          WalletTrade.block_time >= now - timedelta(days=lookback_days))
            )).scalars().all()
            m = compute_metrics(trades, prices=prices, now=now)
            mints = {t.mint for t in trades if t.kind == "BUY"}
            liqs = [liq[x] for x in mints if liq.get(x)]
            avg_liq = statistics.median(liqs) if liqs else None
            res = score_trader(m, avg_liquidity_usd=avg_liq, now=now, cfg=self.scoring)
            w.score = res.score
            w.metrics = {**m.to_dict(), "avg_liquidity_usd": avg_liq, "components": res.components}
            if w.status != "followed":
                w.status = "candidate" if res.eligible else "excluded"
            w.excluded_reasons = res.reasons
            session.add(TraderScore(wallet=w.address, computed_at=now, score=res.score, eligible=res.eligible,
                                    reasons=res.reasons, components=res.components, metrics=m.to_dict(),
                                    scoring_version=SCORING_VERSION))
            out.append({"wallet": w.address, **res.to_dict(), "roi": m.roi, "n_closed": m.n_closed})
        out.sort(key=lambda r: r["score"], reverse=True)
        return out


def new_events_filter(stored: list[TradeEvent], new_txs: list) -> list[TradeEvent]:
    """Events that came from the *new* (not backfilled) page: those are live signals."""
    sigs = {t.signature for t in new_txs}
    return [e for e in stored if e.signature in sigs]
