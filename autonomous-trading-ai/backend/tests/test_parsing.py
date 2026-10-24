import json
from datetime import UTC, datetime
from pathlib import Path

from app.chains.types import NATIVE_SOL, USDC_MINT, WSOL_MINT, BalanceDelta, ParsedTx
from app.connectors.dexscreener import best_pair, is_paid_order, pair_to_market
from app.connectors.geckoterminal import parse_trades, pools_to_markets
from app.connectors.helius import enhanced_to_parsed
from app.connectors.jupiter import price_of, quote_summary
from app.connectors.solana_rpc import parse_transaction
from app.traders.swaps import classify

FX = Path(__file__).parent / "fixtures"
W = "TraderWa11et111111111111111111111111111111111"
MEME = "MemeMint1111111111111111111111111111111pump"


def tx(deltas, ok=True):
    return ParsedTx("sig", 1, datetime(2026, 1, 1, tzinfo=UTC), W, 0.000005, ok, deltas)


def test_rpc_transaction_to_buy():
    raw = json.loads((FX / "rpc_swap_tx.json").read_text())
    p = parse_transaction(raw)
    assert p.success and p.fee_sol == 0.000005
    sol = next(d for d in p.deltas if d.owner == W and d.mint == NATIVE_SOL)
    assert abs(sol.change + 0.505) < 1e-9  # network fee reported separately
    ev = classify(p, W, sol_usd=150.0)
    assert len(ev) == 1 and ev[0].kind == "BUY" and ev[0].mint == MEME
    assert ev[0].token_amount == 20000 and abs(ev[0].quote_usd - 75.75) < 1e-6
    assert ev[0].usd_is_estimate is True


def test_sell_with_usdc_quote_is_exact_usd():
    ev = classify(tx([BalanceDelta(MEME, W, -1000, 6), BalanceDelta(USDC_MINT, W, 42.5, 6)]), W, 150)
    assert ev[0].kind == "SELL" and ev[0].quote_usd == 42.5 and ev[0].usd_is_estimate is False


def test_transfer_is_not_a_trade():
    ev = classify(tx([BalanceDelta(MEME, W, 5000, 6), BalanceDelta(NATIVE_SOL, W, -0.00204, 9)]), W, 150)
    assert ev[0].kind == "TRANSFER_IN" and ev[0].quote_usd is None  # ATA rent is not a purchase


def test_wsol_and_native_are_merged_and_failed_tx_ignored():
    d = [BalanceDelta(MEME, W, 10, 6), BalanceDelta(WSOL_MINT, W, -0.3, 9), BalanceDelta(NATIVE_SOL, W, -0.2, 9)]
    assert classify(tx(d), W, 100)[0].quote_amount == 0.5
    assert classify(tx(d, ok=False), W, 100) == []


def test_token_to_token_swap_has_unknown_usd():
    ev = classify(tx([BalanceDelta(MEME, W, -10, 6), BalanceDelta("OtherMint1111111111111111111111111111111111", W, 99, 6)]), W, 100)
    assert {e.kind for e in ev} == {"BUY", "SELL"} and all(e.quote_usd is None for e in ev)


def test_helius_enhanced_conversion():
    t = {"signature": "s", "timestamp": 1767225600, "fee": 5000, "feePayer": W, "source": "JUPITER",
         "nativeTransfers": [{"fromUserAccount": W, "toUserAccount": "Pool", "amount": 500_000_000}],
         "tokenTransfers": [{"fromUserAccount": "Pool", "toUserAccount": W, "mint": MEME, "tokenAmount": 1234.5}]}
    ev = classify(enhanced_to_parsed(t), W, 200)
    assert ev[0].kind == "BUY" and ev[0].quote_usd == 100.0 and ev[0].token_amount == 1234.5


def test_dexscreener_parsing():
    pairs = json.loads((FX / "dexscreener_tokens.json").read_text())
    bp = best_pair(pairs, MEME)
    m = pair_to_market(bp)
    assert m.pair_address.startswith("PairA") and m.liquidity_usd == 85000.12 and m.price_usd == 1.5e-05
    assert m.txns["h1"] == {"buys": 120, "sells": 95} and m.volume["h24"] == 350000
    assert m.pool_created_at == datetime(2025, 12, 31, tzinfo=UTC) and m.graduated is True
    assert m.holders is None  # unknown, never guessed
    assert is_paid_order([{"type": "tokenProfile", "status": "approved", "paymentTimestamp": 1}]) is True
    assert is_paid_order([]) is False and is_paid_order("weird") is None


def test_gecko_parsing():
    ms = pools_to_markets(json.loads((FX / "gecko_trending.json").read_text()), "solana")
    assert ms[0].address == "WifxMint111111111111111111111111111111111" and ms[0].symbol == "WIFX"
    assert ms[0].liquidity_usd == 1500000.5 and ms[0].market_cap_usd is None and ms[0].fdv_usd == 420000000
    tr = parse_trades(json.loads((FX / "gecko_trades.json").read_text()))
    assert tr[0]["wallet"].startswith("8ZUcz") and tr[0]["kind"] == "buy" and tr[0]["volume_usd"] == 225.0


def test_jupiter_parsing():
    assert price_of({WSOL_MINT: {"usdPrice": 147.48, "decimals": 9}}, WSOL_MINT) == 147.48
    assert price_of({}, WSOL_MINT) is None
    q = {"inputMint": WSOL_MINT, "inAmount": "100000000", "outputMint": USDC_MINT, "outAmount": "17057460",
         "otherAmountThreshold": "16886885", "swapMode": "ExactIn", "slippageBps": 100, "priceImpactPct": "0.0001",
         "routePlan": [{"swapInfo": {"label": "Meteora DLMM", "feeAmount": "1285", "feeMint": USDC_MINT}, "percent": 100}]}
    s = quote_summary(q)
    assert s["out_amount"] == 17057460 and s["price_impact"] == 0.0001 and s["route"] == ["Meteora DLMM"] and s["fee_out_raw"] == 1285
