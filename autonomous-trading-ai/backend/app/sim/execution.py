"""Paper execution model.

Never fills at the original trader's price. A copy order is priced from the
market price *we* observed at decision time, then:

1. latency is drawn (lognormal around the configured mean);
2. the price drifts during that latency: random walk scaled by the token's
   short-term volatility plus an *adverse* component (after a trader buys,
   copiers are on average late into the move);
3. price impact comes from an executable Jupiter route when available, else a
   constant-product approximation ``usd / (liquidity/2 + usd)``;
4. if total adverse move exceeds the slippage tolerance the swap reverts
   (status=failed) and only the network/priority fee is paid;
5. random infrastructure failures happen with a liquidity-dependent probability;
6. sells that would exceed the max impact are partially filled.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ExecConfig:
    latency_ms: int = 2500
    failure_rate: float = 0.03
    dex_fee_bps: float = 25.0
    priority_fee_usd: float = 0.002
    network_fee_usd: float = 0.001
    adverse_bps_per_s: float = 8.0  # expected adverse drift per second after a copied trade
    vol_per_sqrt_s: float = 0.004  # fallback volatility per sqrt(second)


@dataclass
class MarketContext:
    price: float  # observed market price at decision time (USD per token)
    liquidity_usd: float | None
    price_change_5m_pct: float | None = None
    quote_impact: float | None = None  # fraction, from an executable route
    quote_includes_fees: bool = False
    source: str = "market"


@dataclass
class Fill:
    status: str  # filled | partial | failed
    side: str
    requested_usd: float
    requested_qty: float
    filled_qty: float
    filled_usd: float  # gross notional at exec price
    exec_price: float | None
    reference_price: float
    fee_usd: float
    slippage_bps: float | None
    impact: float
    latency_ms: int
    reason: str | None
    quote_source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _impact(usd: float, ctx: MarketContext) -> float:
    if ctx.quote_impact is not None:
        return max(0.0, ctx.quote_impact)
    if not ctx.liquidity_usd or ctx.liquidity_usd <= 0:
        return 1.0
    return usd / (ctx.liquidity_usd / 2 + usd)


def simulate(
    side: str,
    ctx: MarketContext,
    rng: random.Random,
    cfg: ExecConfig,
    *,
    usd: float | None = None,
    qty: float | None = None,
    max_slippage_bps: float = 300,
    max_impact: float = 0.05,
    copy_signal: bool = True,
) -> Fill:
    assert side in ("BUY", "SELL")
    ref = ctx.price
    if side == "BUY":
        assert usd is not None
        req_usd, req_qty = usd, usd / ref
    else:
        assert qty is not None
        req_qty, req_usd = qty, qty * ref
    latency = int(cfg.latency_ms * math.exp(rng.gauss(0, 0.35)))
    lat_s = latency / 1000
    gas = cfg.priority_fee_usd + cfg.network_fee_usd

    vol = cfg.vol_per_sqrt_s
    if ctx.price_change_5m_pct is not None:
        vol = max(vol, abs(ctx.price_change_5m_pct) / 100 / math.sqrt(300))
    adverse = (cfg.adverse_bps_per_s / 1e4) * lat_s if copy_signal else 0.0
    drift = rng.gauss(0, vol * math.sqrt(lat_s))
    # BUY: price moves up against us; SELL: price moves down against us
    move = adverse + drift if side == "BUY" else -adverse + drift
    px_after_latency = ref * math.exp(move)

    liq = ctx.liquidity_usd or 0
    p_fail = cfg.failure_rate * (1 + (2 if liq and liq < 20_000 else 0))
    if rng.random() < p_fail:
        return Fill("failed", side, req_usd, req_qty, 0, 0, None, ref, gas, None, 0, latency,
                    "execution_failed (simulated RPC/route failure)", ctx.source)

    fill_qty = req_qty
    status = "filled"
    imp = _impact(req_qty * px_after_latency, ctx)
    if side == "SELL" and imp > max_impact and ctx.quote_impact is None and liq > 0:
        # reduce size until impact <= max_impact: usd/(L/2+usd) = m -> usd = m*L/2/(1-m)
        max_usd = max_impact * (liq / 2) / (1 - max_impact)
        fill_qty = min(req_qty, max_usd / px_after_latency)
        imp = _impact(fill_qty * px_after_latency, ctx)
        status = "partial" if fill_qty < req_qty * 0.999 else "filled"
    elif side == "BUY" and imp > max_impact:
        return Fill("failed", side, req_usd, req_qty, 0, 0, None, ref, gas, None, imp, latency,
                    f"price_impact {imp:.2%} > max {max_impact:.2%}", ctx.source)

    exec_px = px_after_latency * (1 + imp) if side == "BUY" else px_after_latency * (1 - imp)
    slip_bps = (exec_px / ref - 1) * 1e4 if side == "BUY" else (1 - exec_px / ref) * 1e4
    if slip_bps > max_slippage_bps:
        return Fill("failed", side, req_usd, req_qty, 0, 0, None, ref, gas, slip_bps, imp, latency,
                    f"slippage {slip_bps:.0f}bps > tolerance {max_slippage_bps:.0f}bps (swap reverted)", ctx.source)
    if fill_qty <= 0:
        return Fill("failed", side, req_usd, req_qty, 0, 0, None, ref, gas, None, imp, latency, "no liquidity", ctx.source)

    if side == "BUY":
        notional = req_usd
        fill_qty = notional / exec_px
    else:
        notional = fill_qty * exec_px
    dex_fee = 0.0 if ctx.quote_includes_fees else notional * cfg.dex_fee_bps / 1e4
    return Fill(status, side, req_usd, req_qty, fill_qty, notional, exec_px, ref, dex_fee + gas, slip_bps, imp,
                latency, None if status == "filled" else "partial fill (liquidity)", ctx.source)
