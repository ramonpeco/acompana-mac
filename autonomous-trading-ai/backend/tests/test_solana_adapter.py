"""SolanaAdapter wiring against recorded response shapes (no network)."""

import json
from pathlib import Path

import httpx

from app.chains.solana import SolanaAdapter
from app.chains.types import WSOL_MINT
from app.config import Settings
from app.traders.swaps import classify

FX = Path(__file__).parent / "fixtures"
MEME = "MemeMint1111111111111111111111111111111pump"
W = "TraderWa11et111111111111111111111111111111111"


def handler(req: httpx.Request) -> httpx.Response:
    u = str(req.url)
    if "api.dexscreener.com/tokens/v1/solana/" in u:
        return httpx.Response(200, json=json.loads((FX / "dexscreener_tokens.json").read_text()))
    if "geckoterminal.com/api/v2/networks/solana/trending_pools" in u:
        return httpx.Response(200, json=json.loads((FX / "gecko_trending.json").read_text()))
    if "geckoterminal.com/api/v2/networks/solana/pools/" in u and u.split("?")[0].endswith("/trades"):
        return httpx.Response(200, json=json.loads((FX / "gecko_trades.json").read_text()))
    if "api.jup.ag/price/v3" in u:
        return httpx.Response(200, json={WSOL_MINT: {"usdPrice": 150.0, "decimals": 9, "liquidity": 1e9, "blockId": 1,
                                                     "createdAt": "2024-06-05T08:55:25.527Z", "priceChange24h": 1}})
    if "rpc.test" in u:
        body = json.loads(req.content)
        m = body["method"]
        if m == "getSignaturesForAddress":
            res = [{"signature": "5igSwapSignature1111111111111111111111111111111111111111111111111111", "err": None, "slot": 1},
                   {"signature": "failedSig", "err": {"InstructionError": [0, "x"]}, "slot": 1}]
        elif m == "getTransaction":
            res = json.loads((FX / "rpc_swap_tx.json").read_text())
        elif m == "getAccountInfo":
            res = {"value": {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "data": {"parsed": {"info": {
                "mintAuthority": None, "freezeAuthority": None, "decimals": 6, "supply": "1000000000000000"}}}}}
        elif m == "getTokenSupply":
            res = {"value": {"uiAmountString": "1000000000", "decimals": 6}}
        elif m == "getTokenLargestAccounts":
            res = {"value": [{"address": "A", "uiAmountString": "300000000"}, {"address": "B", "uiAmountString": "100000000"}]}
        else:
            return httpx.Response(400)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": res})
    return httpx.Response(404)


def adapter():
    a = SolanaAdapter(Settings(solana_rpc_url="https://rpc.test"))
    for c in (a.rpc, a.dex, a.gecko, a.jup):
        c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        c.bucket.rate = 1000
    a.dex.profile_bucket.rate = 1000
    return a


async def test_markets_trending_prices():
    a = adapter()
    ms = await a.markets([MEME])
    assert ms[MEME].liquidity_usd == 85000.12
    tr = await a.trending()
    assert tr[0].symbol == "WIFX"
    assert await a.native_price_usd() == 150.0
    traders = await a.pool_traders("PoolG11111111111111111111111111111111111")
    assert traders[0]["kind"] == "buy"
    assert {h["name"] for h in a.health()} == {"solana_rpc", "dexscreener", "geckoterminal", "jupiter"}


async def test_wallet_history_via_rpc_skips_failed():
    a = adapter()
    txs = await a.wallet_transactions(W)
    assert len(txs) == 1
    ev = classify(txs[0], W, 150.0)
    assert ev[0].kind == "BUY" and ev[0].mint == MEME


async def test_token_onchain():
    a = adapter()
    info = await a.token_onchain(MEME)
    assert info["mint_authority"] is None and info["program"] == "spl-token" and info["supply"] == 1e9
    assert info["largest_accounts"][0]["amount"] == 3e8
