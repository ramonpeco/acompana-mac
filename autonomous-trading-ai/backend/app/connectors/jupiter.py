"""Jupiter APIs (spec: github.com/jup-ag/docs openapi-spec/).

  GET {base}/price/v3?ids=mint1,mint2      up to 50 ids; unreliable prices omitted
  GET {base}/swap/v1/quote?inputMint&outputMint&amount&slippageBps

Base URL is https://api.jup.ag. Without ``x-api-key`` requests are "keyless"
(0.5 req/s, 60 s sliding window). ``lite-api.jup.ag`` is being retired.
"""

from __future__ import annotations

from typing import Any

from app.connectors.base import HttpConnector, Sourced, fnum


class Jupiter(HttpConnector):
    name = "jupiter"

    def __init__(self, base_url: str = "https://api.jup.ag", api_key: str = "", **kw: Any):
        headers = {"x-api-key": api_key} if api_key else None
        rate = 0.9 if api_key else 0.45
        super().__init__(base_url, rate_per_s=rate, burst=1, cache_ttl_s=5, headers=headers, **kw)
        self.has_key = bool(api_key)

    async def prices(self, mints: list[str]) -> Sourced[dict[str, dict]]:
        assert len(mints) <= 50
        return await self.get("/price/v3", params={"ids": ",".join(mints)})

    async def quote(
        self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int = 300
    ) -> Sourced[dict]:
        return await self.get(
            "/swap/v1/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(int(amount_raw)),
                "slippageBps": slippage_bps,
                "restrictIntermediateTokens": "true",
            },
            cache_ttl_s=2,
        )


def price_of(payload: dict, mint: str) -> float | None:
    return fnum((payload.get(mint) or {}).get("usdPrice"))


def quote_summary(q: dict) -> dict[str, Any]:
    fee_raw = 0
    for step in q.get("routePlan") or []:
        info = step.get("swapInfo") or {}
        if info.get("feeMint") == q.get("outputMint"):
            fee_raw += int(info.get("feeAmount") or 0)
    return {
        "in_amount": int(q["inAmount"]),
        "out_amount": int(q["outAmount"]),
        "min_out": int(q.get("otherAmountThreshold") or 0),
        # priceImpactPct is a string fraction in the spec example ("0.0001")
        "price_impact": fnum(q.get("priceImpactPct")) or 0.0,
        "route": [((s.get("swapInfo") or {}).get("label")) for s in q.get("routePlan") or []],
        "fee_out_raw": fee_raw,
        "context_slot": q.get("contextSlot"),
    }
