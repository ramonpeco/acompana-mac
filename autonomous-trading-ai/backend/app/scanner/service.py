"""Token scanner: maintains a token universe and serves the scanner categories."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.base import ChainAdapter
from app.chains.types import QUOTE_MINTS, TokenMarket
from app.connectors.base import ConnectorError
from app.models import Token, TokenPricePoint, Wallet, WalletTrade
from app.risk.token_risk import assess_token

log = logging.getLogger(__name__)

CATEGORIES = ["trending", "gainers", "top", "new_pairs", "graduated", "dex_paid", "high_volume", "smart_money"]
# requested timeframe -> (source window key, is_approximation)
TIMEFRAMES: dict[str, tuple[str | None, bool]] = {
    "1m": ("m5", True),
    "5m": ("m5", False),
    "15m": ("m15", False),
    "1h": ("h1", False),
    "4h": ("h6", True),
    "24h": ("h24", False),
    "7d": (None, True),  # computed from our own price history when available
}


def _window_value(d: dict[str, Any], key: str | None) -> float | None:
    if key is None:
        return None
    if key in d and d[key] is not None:
        return d[key]
    if key == "m15":
        return d.get("m5")
    return None


class Scanner:
    def __init__(self, adapter: ChainAdapter, max_universe: int = 400, now_fn=None):
        self.adapter = adapter
        self.now = now_fn or (lambda: datetime.now(UTC))
        self.max_universe = max_universe
        self.last_refresh: datetime | None = None
        self.last_error: str | None = None

    async def _upsert(self, session: AsyncSession, m: TokenMarket, category: str | None) -> Token:
        tok = (
            await session.execute(select(Token).where(Token.chain == m.chain, Token.address == m.address))
        ).scalar_one_or_none()
        if tok is None:
            tok = Token(chain=m.chain, address=m.address, categories=[])
            session.add(tok)
        prev = dict(tok.market or {})
        data = m.to_dict()
        # keep flags discovered by slower endpoints if the new snapshot lacks them
        for k in ("dex_paid", "holders"):
            if data.get(k) is None and prev.get(k) is not None:
                data[k] = prev[k]
        data["previous_liquidity_usd"] = prev.get("liquidity_usd")
        tok.symbol = m.symbol or tok.symbol
        tok.name = m.name or tok.name
        tok.pool_created_at = m.pool_created_at or tok.pool_created_at
        tok.market = data
        cats = set(tok.categories or [])
        if category:
            cats.add(category)
        tok.categories = sorted(cats)
        tok.updated_at = self.now()
        if m.price_usd:
            session.add(TokenPricePoint(chain=m.chain, address=m.address, ts=m.fetched_at or self.now(),
                                        price_usd=m.price_usd, liquidity_usd=m.liquidity_usd,
                                        source=m.sources.get("market", "?")))
        return tok

    async def refresh(self, session: AsyncSession, extra_addresses: list[str] | None = None) -> dict[str, Any]:
        """Pull category feeds + refresh market data for the tracked universe."""
        stats = {"trending": 0, "new_pairs": 0, "boosted": 0, "refreshed": 0, "errors": []}
        seen: set[str] = set()
        for cat, fn in (("trending", self.adapter.trending), ("new_pairs", self.adapter.new_pairs)):
            try:
                for m in await fn():
                    if m.address and m.address not in QUOTE_MINTS:
                        await self._upsert(session, m, cat)
                        seen.add(m.address)
                        stats[cat] += 1
            except ConnectorError as exc:
                stats["errors"].append(str(exc))
        boosted: list[dict[str, Any]] = []
        try:
            boosted = await self.adapter.boosted()
            stats["boosted"] = len(boosted)
        except ConnectorError as exc:
            stats["errors"].append(str(exc))

        # universe = feeds + boosted + tokens recently traded by tracked wallets + recent tokens in DB
        since = self.now() - timedelta(days=2)
        traded = (
            await session.execute(
                select(WalletTrade.mint).where(WalletTrade.block_time >= since).group_by(WalletTrade.mint)
                .order_by(func.max(WalletTrade.block_time).desc()).limit(150)
            )
        ).scalars().all()
        recent = (
            await session.execute(select(Token.address).order_by(Token.updated_at.desc()).limit(self.max_universe))
        ).scalars().all()
        universe = list(dict.fromkeys([*(b["address"] for b in boosted), *traded, *(extra_addresses or []), *recent]))
        universe = [a for a in universe if a not in seen and a not in QUOTE_MINTS][: self.max_universe]
        try:
            markets = await self.adapter.markets(universe)
            for m in markets.values():
                await self._upsert(session, m, None)
                stats["refreshed"] += 1
            boosted_set = {b["address"] for b in boosted}
            for addr in boosted_set & set(markets):
                tok = (await session.execute(select(Token).where(Token.address == addr))).scalar_one()
                tok.market = {**tok.market, "boosts_active": next((b.get("boosts") for b in boosted if b["address"] == addr), None)}
        except ConnectorError as exc:
            stats["errors"].append(str(exc))
        self.last_refresh = self.now()
        self.last_error = "; ".join(stats["errors"]) or None
        return stats

    async def check_dex_paid(self, session: AsyncSession, limit: int = 15) -> None:
        """Paid-order lookups are rate-limited (60/min): only for the top tokens."""
        toks = (await session.execute(select(Token).order_by(Token.updated_at.desc()).limit(200))).scalars().all()
        toks = sorted(toks, key=lambda t: (t.market or {}).get("volume", {}).get("h24") or 0, reverse=True)
        for t in [t for t in toks if (t.market or {}).get("dex_paid") is None][:limit]:
            paid = await self.adapter.dex_paid(t.address)
            if paid is not None:
                t.market = {**t.market, "dex_paid": paid}

    async def assess(self, session: AsyncSession, token: Token) -> dict[str, Any]:
        onchain = None
        try:
            onchain = await self.adapter.token_onchain(token.address)
        except (ConnectorError, KeyError) as exc:
            log.info("onchain info unavailable for %s: %s", token.address, exc)
        m = market_from_dict(token.market) if token.market else None
        risk = assess_token(m, onchain, self.now(),
                            previous_liquidity=(token.market or {}).get("previous_liquidity_usd"))
        token.risk = {**risk.to_dict(), "assessed_at": self.now().isoformat(), "onchain": onchain}
        return token.risk

    async def query(
        self,
        session: AsyncSession,
        category: str = "trending",
        timeframe: str = "1h",
        *,
        min_liquidity: float | None = None,
        min_volume: float | None = None,
        min_mcap: float | None = None,
        max_mcap: float | None = None,
        chain: str = "solana",
        limit: int = 100,
        smart_score: float = 55.0,
    ) -> dict[str, Any]:
        key, approx = TIMEFRAMES.get(timeframe, ("h1", True))
        toks = (await session.execute(select(Token).where(Token.chain == chain))).scalars().all()
        now = self.now()
        rows = []
        smart: dict[str, int] = {}
        if category == "smart_money":
            q = (
                select(WalletTrade.mint, func.count(func.distinct(WalletTrade.wallet)))
                .join(Wallet, Wallet.address == WalletTrade.wallet)
                .where(WalletTrade.kind == "BUY", Wallet.score >= smart_score,
                       WalletTrade.block_time >= now - timedelta(hours=24))
                .group_by(WalletTrade.mint)
            )
            smart = {m: n for m, n in (await session.execute(q)).all()}
        week = await self._week_changes(session, [t.address for t in toks]) if timeframe == "7d" else {}
        for t in toks:
            mk = t.market or {}
            liq = mk.get("liquidity_usd")
            vol = _window_value(mk.get("volume") or {}, key) if key else (mk.get("volume") or {}).get("h24")
            chg = _window_value(mk.get("price_change") or {}, key) if key else week.get(t.address)
            mcap = mk.get("market_cap_usd") or mk.get("fdv_usd")
            if min_liquidity and (liq or 0) < min_liquidity:
                continue
            if min_volume and (vol or 0) < min_volume:
                continue
            if min_mcap and (mcap or 0) < min_mcap:
                continue
            if max_mcap and mcap and mcap > max_mcap:
                continue
            if category == "trending" and "trending" not in (t.categories or []):
                continue
            if category == "new_pairs":
                created = t.pool_created_at
                if not created or (now - created) > timedelta(hours=24):
                    continue
            if category == "graduated" and mk.get("graduated") is not True:
                continue
            if category == "dex_paid" and mk.get("dex_paid") is not True:
                continue
            if category == "smart_money" and t.address not in smart:
                continue
            fetched = mk.get("fetched_at")
            age_s = (now - datetime.fromisoformat(fetched)).total_seconds() if fetched else None
            rows.append({
                "address": t.address, "symbol": t.symbol, "name": t.name, "chain": t.chain,
                "price_usd": mk.get("price_usd"), "market_cap_usd": mk.get("market_cap_usd"), "fdv_usd": mk.get("fdv_usd"),
                "liquidity_usd": liq, "volume": vol, "change_pct": chg, "txns": mk.get("txns"),
                "holders": mk.get("holders"), "pool_created_at": t.pool_created_at.isoformat() if t.pool_created_at else None,
                "age_hours": round((now - t.pool_created_at).total_seconds() / 3600, 2) if t.pool_created_at else None,
                "dex_id": mk.get("dex_id"), "dex_paid": mk.get("dex_paid"), "graduated": mk.get("graduated"),
                "boosts_active": mk.get("boosts_active"), "smart_wallets": smart.get(t.address),
                "risk_score": (t.risk or {}).get("score"), "risk_level": (t.risk or {}).get("level"),
                "sources": mk.get("sources"), "estimated": mk.get("estimated"), "data_age_s": age_s,
                "stale": age_s is None or age_s > 300, "url": mk.get("url"),
            })
        sort_key = {
            "gainers": lambda r: r["change_pct"] if r["change_pct"] is not None else -1e9,
            "high_volume": lambda r: r["volume"] or 0,
            "top": lambda r: (r["volume"] or 0) * min(1.0, (r["liquidity_usd"] or 0) / 100_000),
            "new_pairs": lambda r: r["pool_created_at"] or "",
            "smart_money": lambda r: r["smart_wallets"] or 0,
        }.get(category, lambda r: r["volume"] or 0)
        rows.sort(key=sort_key, reverse=True)
        return {
            "category": category, "timeframe": timeframe, "window_used": key or "own_price_history",
            "timeframe_is_approximation": approx, "count": len(rows), "rows": rows[:limit],
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "data_label": self.adapter.data_label,
        }

    async def _week_changes(self, session: AsyncSession, addresses: list[str]) -> dict[str, float]:
        since = self.now() - timedelta(days=7)
        out: dict[str, float] = {}
        q = (
            select(TokenPricePoint.address, func.min(TokenPricePoint.ts), func.max(TokenPricePoint.ts))
            .where(TokenPricePoint.ts >= since).group_by(TokenPricePoint.address)
        )
        for addr, t_min, t_max in (await session.execute(q)).all():
            if addr not in addresses or (t_max - t_min) < timedelta(days=5):
                continue  # not enough own history to claim a 7d change
            first = (await session.execute(select(TokenPricePoint.price_usd).where(
                TokenPricePoint.address == addr, TokenPricePoint.ts == t_min))).scalars().first()
            last = (await session.execute(select(TokenPricePoint.price_usd).where(
                TokenPricePoint.address == addr, TokenPricePoint.ts == t_max))).scalars().first()
            if first and last:
                out[addr] = (last / first - 1) * 100
        return out


def market_from_dict(d: dict[str, Any]) -> TokenMarket:
    allowed = TokenMarket.__dataclass_fields__.keys()
    kw = {k: v for k, v in d.items() if k in allowed}
    for k in ("pool_created_at", "fetched_at"):
        if isinstance(kw.get(k), str):
            kw[k] = datetime.fromisoformat(kw[k])
    return TokenMarket(**kw)
