"""Deterministic synthetic market (DATA_MODE=sandbox, tests and synthetic backtests).

THIS IS NOT MARKET DATA. It exists so the whole pipeline (discovery -> ranking ->
copy -> switching -> risk -> reporting) can be exercised end-to-end without
network access, and so strategies can be stress-tested in controlled regimes
(favourable / unfavourable). Every object it produces is labelled ``synthetic``.

Model
-----
* Tokens follow a jump-diffusion random walk on a 60 s grid; some tokens "rug"
  (price collapses ~95% and liquidity is pulled).
* Traders have a hidden ``skill`` in [0, 1]. Before entering, a skilled trader
  peeks at the *future* path (that is the definition of skill in this toy world);
  an unskilled one enters at random. Copiers only see the trade after it
  happened, so they enter later and at a different price, which is the effect
  the simulator must capture.
"""

from __future__ import annotations

import hashlib
import math
import random
from array import array
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.chains.base import ChainAdapter
from app.chains.types import NATIVE_SOL, WSOL_MINT, BalanceDelta, ParsedTx, TokenMarket

STEP_S = 60


def _h(s: str) -> int:
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def fake_address(seed: str, suffix: str = "") -> str:
    h = hashlib.sha256(seed.encode()).digest()
    n = int.from_bytes(h, "big")
    s = ""
    while len(s) < 44 - len(suffix):
        n, r = divmod(n, 58)
        s += B58[r]
        if n == 0:
            n = int.from_bytes(hashlib.sha256(s.encode()).digest(), "big")
    return s + suffix


@dataclass
class SynthToken:
    address: str
    symbol: str
    created_step: int
    liquidity: float
    rug_step: int | None
    mint_authority: bool
    freeze_authority: bool
    top10_share: float
    prices: array = field(default_factory=lambda: array("d"))


@dataclass
class SynthTrade:
    wallet: str
    token: str
    step: int
    kind: str
    qty: float
    sol: float
    signature: str


@dataclass
class Regime:
    name: str = "neutral"
    drift: float = 0.0  # per-step log drift
    vol: float = 0.012
    rug_prob: float = 0.15
    skill_mean: float = 0.5
    skill_redraw: float = 0.05  # per-trade probability that a trader's edge changes (non-persistent skill)


REGIMES = {
    "neutral": Regime(),
    "favorable": Regime("favorable", drift=0.00004, vol=0.010, rug_prob=0.08, skill_mean=0.65, skill_redraw=0.01),
    "unfavorable": Regime("unfavorable", drift=-0.00006, vol=0.016, rug_prob=0.30, skill_mean=0.15, skill_redraw=0.25),
}


class SandboxWorld:
    def __init__(
        self,
        seed: int = 7,
        n_tokens: int = 30,
        n_traders: int = 24,
        start: datetime | None = None,
        history_days: float = 4,
        horizon_days: float = 14,
        regime: str = "neutral",
    ):
        self.rng = random.Random(seed)
        self.seed = seed
        self.regime = REGIMES[regime]
        now = start or datetime.now(UTC)
        self.t0 = (now - timedelta(days=history_days)).replace(second=0, microsecond=0)
        self.n_steps = int((history_days + horizon_days) * 86400 / STEP_S)
        self.sol_price = 150.0
        self.tokens: dict[str, SynthToken] = {}
        self.trader_skill: dict[str, float] = {}
        self.trades: list[SynthTrade] = []
        self._gen_tokens(n_tokens)
        self._gen_traders(n_traders)

    # ---------- generation ----------
    def _gen_tokens(self, n: int) -> None:
        r, g = self.rng, self.regime
        for i in range(n):
            created = 0 if i < n // 2 else r.randint(0, int(self.n_steps * 0.6))
            rug = created + r.randint(300, 6000) if r.random() < g.rug_prob else None
            addr = fake_address(f"tok{self.seed}-{i}", "pump" if i % 3 == 0 else "")
            tok = SynthToken(
                address=addr,
                symbol=f"SYN{i:02d}",
                created_step=created,
                liquidity=r.choice([8_000, 20_000, 45_000, 90_000, 250_000]),
                rug_step=rug,
                mint_authority=r.random() < 0.12,
                freeze_authority=r.random() < 0.10,
                top10_share=r.uniform(0.15, 0.85),
            )
            p = r.uniform(0.00001, 0.01)
            prices = array("d", [0.0] * self.n_steps)
            for s in range(created, self.n_steps):
                if rug is not None and s >= rug:
                    p *= 0.97 if s < rug + 60 else 1.0
                else:
                    jump = r.gauss(0.08, 0.05) if r.random() < 0.002 else 0.0
                    jump -= r.gauss(0.06, 0.03) if r.random() < 0.002 else 0.0
                    p *= math.exp(g.drift + r.gauss(0, g.vol) + jump)
                prices[s] = max(p, 1e-12)
            tok.prices = prices
            self.tokens[addr] = tok

    def _gen_traders(self, n: int) -> None:
        r, g = self.rng, self.regime
        toks = list(self.tokens.values())
        for i in range(n):
            wallet = fake_address(f"wallet{self.seed}-{i}")
            skill = min(1.0, max(0.0, r.gauss(g.skill_mean, 0.22)))
            self.trader_skill[wallet] = skill
            size_sol = r.choice([0.2, 0.5, 1.0, 2.0, 5.0])
            step = r.randint(0, 300)
            k = 0
            while step < self.n_steps - 10:
                live = [t for t in toks if t.created_step <= step and (t.rug_step is None or step < t.rug_step)]
                if not live:
                    step += 60
                    continue
                if r.random() < g.skill_redraw:
                    skill = min(1.0, max(0.0, r.gauss(g.skill_mean, 0.22)))
                hold = r.randint(10, 240)
                exit_step = min(step + hold, self.n_steps - 1)
                tok = r.choice(live)
                if r.random() < skill:  # informed: choose the live token with best forward return
                    cands = r.sample(live, min(6, len(live)))
                    tok = max(cands, key=lambda t: t.prices[exit_step] / t.prices[step])
                sol = size_sol * r.uniform(0.6, 1.4)
                qty = sol * self.sol_price / tok.prices[step]
                k += 1
                self.trades.append(SynthTrade(wallet, tok.address, step, "BUY", qty, sol, fake_address(f"sig{wallet}{k}b")))
                partial = r.random() < 0.3
                if partial:
                    mid = step + max(1, (exit_step - step) // 2)
                    q1 = qty * 0.5
                    self.trades.append(
                        SynthTrade(wallet, tok.address, mid, "SELL", q1, q1 * tok.prices[mid] / self.sol_price, fake_address(f"sig{wallet}{k}p"))
                    )
                    rest = qty - q1
                else:
                    rest = qty
                px = tok.prices[exit_step]
                self.trades.append(
                    SynthTrade(wallet, tok.address, exit_step, "SELL", rest, rest * px / self.sol_price, fake_address(f"sig{wallet}{k}s"))
                )
                step = exit_step + r.randint(10, 300)
        self.trades.sort(key=lambda t: t.step)

    # ---------- queries ----------
    def step_at(self, ts: datetime) -> int:
        return max(0, min(self.n_steps - 1, int((ts - self.t0).total_seconds() // STEP_S)))

    def time_of(self, step: int) -> datetime:
        return self.t0 + timedelta(seconds=step * STEP_S)

    def price(self, token: str, ts: datetime) -> float | None:
        t = self.tokens.get(token)
        if not t:
            return None
        s = self.step_at(ts)
        return t.prices[s] if s >= t.created_step else None

    def liquidity(self, token: str, ts: datetime) -> float:
        t = self.tokens[token]
        s = self.step_at(ts)
        if t.rug_step is not None and s >= t.rug_step:
            return t.liquidity * 0.02
        return t.liquidity


class SandboxAdapter(ChainAdapter):
    chain = "solana"
    data_label = "synthetic"

    def __init__(self, world: SandboxWorld | None = None, clock=None):
        self.world = world or SandboxWorld()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.calls = 0

    def now(self) -> datetime:
        return self.clock()

    def health(self) -> list[dict[str, Any]]:
        return [{"name": "sandbox", "status": "ok", "requests": self.calls, "errors": 0, "note": "SYNTHETIC DATA"}]

    def _market(self, addr: str) -> TokenMarket | None:
        w, now = self.world, self.now()
        t = w.tokens.get(addr)
        p = w.price(addr, now)
        if not t or p is None:
            return None
        s = w.step_at(now)

        def chg(minutes: int) -> float | None:
            ps = max(t.created_step, s - minutes)
            return (t.prices[s] / t.prices[ps] - 1) * 100 if t.prices[ps] else None

        recent = [x for x in w.trades if x.token == addr and s - 1440 <= x.step <= s]
        vol24 = sum(x.sol for x in recent) * w.sol_price
        vol1 = sum(x.sol for x in recent if x.step >= s - 60) * w.sol_price
        supply = 1_000_000_000
        return TokenMarket(
            chain="solana",
            address=addr,
            symbol=t.symbol,
            name=f"Synthetic {t.symbol}",
            pair_address=fake_address("pool" + addr),
            dex_id="sandbox",
            price_usd=p,
            market_cap_usd=p * supply,
            fdv_usd=p * supply,
            liquidity_usd=w.liquidity(addr, now),
            volume={"m5": vol1 / 12, "h1": vol1, "h6": vol24 / 4, "h24": vol24},
            price_change={"m5": chg(5), "h1": chg(60), "h6": chg(360), "h24": chg(1440)},
            txns={"h1": {"buys": sum(1 for x in recent if x.kind == "BUY" and x.step >= s - 60), "sells": sum(1 for x in recent if x.kind == "SELL" and x.step >= s - 60)},
                  "h24": {"buys": sum(1 for x in recent if x.kind == "BUY"), "sells": sum(1 for x in recent if x.kind == "SELL")}},
            holders=200 + (_h(addr) % 5000),
            pool_created_at=w.time_of(t.created_step),
            graduated=addr.endswith("pump") and t.created_step < s - 600,
            dex_paid=(_h(addr) % 4 == 0),
            sources={"market": "sandbox"},
            estimated=["all (synthetic)"],
            fetched_at=now,
        )

    async def markets(self, addresses: list[str]) -> dict[str, TokenMarket]:
        self.calls += 1
        out = {}
        for a in addresses:
            m = self._market(a)
            if m:
                out[a] = m
        return out

    async def trending(self) -> list[TokenMarket]:
        self.calls += 1
        ms = [m for m in (self._market(a) for a in self.world.tokens) if m]
        return sorted(ms, key=lambda m: m.volume.get("h1") or 0, reverse=True)[:20]

    async def new_pairs(self) -> list[TokenMarket]:
        self.calls += 1
        ms = [m for m in (self._market(a) for a in self.world.tokens) if m]
        return sorted(ms, key=lambda m: m.pool_created_at, reverse=True)[:20]

    async def boosted(self) -> list[dict[str, Any]]:
        return [{"address": a, "boosts": 100, "has_profile": True} for a in list(self.world.tokens)[:5]]

    async def dex_paid(self, address: str) -> bool | None:
        return _h(address) % 4 == 0

    async def prices(self, addresses: list[str]) -> dict[str, float]:
        self.calls += 1
        now = self.now()
        out = {}
        for a in addresses:
            if a == WSOL_MINT:
                out[a] = self.world.sol_price
            else:
                p = self.world.price(a, now)
                if p is not None:
                    out[a] = p
        return out

    async def native_price_usd(self) -> float | None:
        return self.world.sol_price

    def _to_parsed(self, t: SynthTrade) -> ParsedTx:
        sign = 1 if t.kind == "BUY" else -1
        return ParsedTx(
            signature=t.signature,
            slot=t.step,
            block_time=self.world.time_of(t.step),
            fee_payer=t.wallet,
            fee_sol=0.000005,
            success=True,
            deltas=[
                BalanceDelta(t.token, t.wallet, sign * t.qty, 6),
                BalanceDelta(NATIVE_SOL, t.wallet, -sign * t.sol, 9),
            ],
            source="sandbox",
        )

    async def wallet_transactions(
        self, wallet: str, *, until: str | None = None, before: str | None = None, limit: int = 50
    ) -> list[ParsedTx]:
        self.calls += 1
        s_now = self.world.step_at(self.now())
        mine = [t for t in self.world.trades if t.wallet == wallet and t.step <= s_now]
        mine.reverse()  # newest first
        if before:
            idx = next((i for i, t in enumerate(mine) if t.signature == before), None)
            mine = mine[idx + 1 :] if idx is not None else mine
        out = []
        for t in mine:
            if until and t.signature == until:
                break
            out.append(self._to_parsed(t))
            if len(out) >= limit:
                break
        return out

    async def pool_traders(self, pool: str) -> list[dict[str, Any]]:
        self.calls += 1
        token = next((a for a in self.world.tokens if fake_address("pool" + a) == pool), None)
        s = self.world.step_at(self.now())
        return [
            {"wallet": t.wallet, "kind": t.kind.lower(), "volume_usd": t.sol * self.world.sol_price,
             "block_time": self.world.time_of(t.step), "tx_hash": t.signature}
            for t in self.world.trades
            if t.token == token and s - 1440 <= t.step <= s
        ][-300:]

    async def token_onchain(self, address: str) -> dict[str, Any]:
        t = self.world.tokens[address]
        supply = 1_000_000_000.0
        top = [supply * t.top10_share * w for w in (0.4, 0.15, 0.1, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02)]
        return {
            "address": address,
            "program": "spl-token",
            "mint_authority": "SynthAuth111" if t.mint_authority else None,
            "freeze_authority": "SynthAuth111" if t.freeze_authority else None,
            "decimals": 6,
            "extensions": [],
            "risky_extensions": [],
            "supply": supply,
            "largest_accounts": [{"address": fake_address(f"h{address}{i}"), "amount": a} for i, a in enumerate(top)],
        }

    async def quote(self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict | None:
        now = self.now()
        w = self.world
        if input_mint == WSOL_MINT:
            token, usd = output_mint, amount_raw / 1e9 * w.sol_price
        else:
            token = input_mint
            p = w.price(token, now)
            if p is None:
                return None
            usd = amount_raw / 1e6 * p
        p = w.price(token, now)
        if p is None:
            return None
        liq = w.liquidity(token, now)
        impact = usd / (liq / 2 + usd)
        out_usd = usd * (1 - impact) * (1 - 0.0025)
        out_raw = int(out_usd / p * 1e6) if input_mint == WSOL_MINT else int(out_usd / w.sol_price * 1e9)
        return {"in_amount": amount_raw, "out_amount": out_raw, "min_out": int(out_raw * (1 - slippage_bps / 1e4)),
                "price_impact": impact, "route": ["sandbox-amm"], "fee_out_raw": 0, "context_slot": w.step_at(now)}

    async def is_finalized(self, signature: str) -> bool | None:
        return True
