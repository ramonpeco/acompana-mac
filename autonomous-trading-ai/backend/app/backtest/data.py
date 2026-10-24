"""Backtest datasets: recorded (from our own DB) or synthetic (SandboxWorld)."""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.sandbox import SandboxWorld
from app.chains.types import WSOL_MINT
from app.models import Token, TokenPricePoint, WalletTrade


@dataclass
class BTTrade:
    wallet: str
    signature: str
    block_time: datetime
    kind: str
    mint: str
    token_amount: float
    quote_usd: float | None
    price_usd: float | None = None


class PriceSeries:
    def __init__(self) -> None:
        self._t: dict[str, list[datetime]] = defaultdict(list)
        self._p: dict[str, list[float]] = defaultdict(list)
        self._sorted = True

    def add(self, mint: str, ts: datetime, price: float) -> None:
        if price and price > 0:
            self._t[mint].append(ts)
            self._p[mint].append(price)
            self._sorted = False

    def finalize(self) -> None:
        for m in list(self._t):
            pairs = sorted(zip(self._t[m], self._p[m], strict=True))
            self._t[m] = [a for a, _ in pairs]
            self._p[m] = [b for _, b in pairs]
        self._sorted = True

    def at(self, mint: str, ts: datetime, max_gap: timedelta = timedelta(hours=2)) -> tuple[float, datetime] | None:
        """Latest observation at or before ``ts`` (never after: no look-ahead)."""
        ts_list = self._t.get(mint)
        if not ts_list:
            return None
        i = bisect.bisect_right(ts_list, ts) - 1
        if i < 0 or ts - ts_list[i] > max_gap:
            return None
        return self._p[mint][i], ts_list[i]

    def mints(self) -> list[str]:
        return list(self._t)


class Dataset:
    label = "recorded"

    def __init__(self, trades: list[BTTrade], prices: PriceSeries, liquidity: PriceSeries,
                 start: datetime, end: datetime, risk: dict[str, float] | None = None, exact_prices: bool = False):
        self.trades = sorted(trades, key=lambda t: t.block_time)
        self.prices = prices
        self.liquidity = liquidity
        self.start, self.end = start, end
        self.risk = risk or {}
        self.exact_prices = exact_prices  # True for synthetic worlds (continuous price path)

    def liq(self, mint: str, ts: datetime) -> float | None:
        # liquidity as observed at (or before) ts; never today's value (avoids look-ahead/survivorship)
        hit = self.liquidity.at(mint, ts, max_gap=timedelta(hours=12))
        return hit[0] if hit else None

    def token_risk(self, mint: str, ts: datetime) -> float:
        return self.risk.get(mint, 40.0)


class SyntheticDataset(Dataset):
    label = "synthetic"

    def __init__(self, world: SandboxWorld, start: datetime | None = None, end: datetime | None = None):
        trades = [BTTrade(t.wallet, t.signature, world.time_of(t.step), t.kind, t.token, t.qty, t.sol * world.sol_price)
                  for t in world.trades]
        ps = PriceSeries()
        for addr, tok in world.tokens.items():
            for s in range(tok.created_step, world.n_steps, 5):
                ps.add(addr, world.time_of(s), tok.prices[s])
        ps.finalize()
        for s in range(0, world.n_steps, 60):
            ps.add(WSOL_MINT, world.time_of(s), world.sol_price)
        ps.finalize()
        self.world = world
        risk = {a: (25 if t.mint_authority else 0) + (25 if t.freeze_authority else 0) + 20 for a, t in world.tokens.items()}
        super().__init__(trades, ps, PriceSeries(), start or world.time_of(0), end or world.time_of(world.n_steps - 1), risk, exact_prices=True)

    def liq(self, mint: str, ts: datetime) -> float | None:
        return self.world.liquidity(mint, ts) if mint in self.world.tokens else None

    def price_exact(self, mint: str, ts: datetime) -> float | None:
        return self.world.price(mint, ts)


async def load_recorded(session: AsyncSession, start: datetime, end: datetime) -> Dataset:
    rows = (await session.execute(select(WalletTrade).where(
        WalletTrade.block_time >= start, WalletTrade.block_time <= end, WalletTrade.status != "dropped"))).scalars().all()
    trades = [BTTrade(r.wallet, r.signature, r.block_time, r.kind, r.mint, r.token_amount, r.quote_usd, r.price_usd) for r in rows]
    ps, liq = PriceSeries(), PriceSeries()
    for p in (await session.execute(select(TokenPricePoint).where(
            TokenPricePoint.ts >= start - timedelta(days=1), TokenPricePoint.ts <= end))).scalars().all():
        ps.add(p.address, p.ts, p.price_usd)
        if p.liquidity_usd:
            liq.add(p.address, p.ts, p.liquidity_usd)
    liq.finalize()
    for t in trades:  # trader fills are genuine price observations too
        if t.price_usd:
            ps.add(t.mint, t.block_time, t.price_usd)
    ps.finalize()
    toks = (await session.execute(select(Token))).scalars().all()
    risk = {t.address: (t.risk or {}).get("score") for t in toks if (t.risk or {}).get("score") is not None}
    return Dataset(trades, ps, liq, start, end, risk)
