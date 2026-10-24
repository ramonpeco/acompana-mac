"""Reconstruct BUY / SELL / TRANSFER actions of a wallet from balance deltas.

Rules (deliberately conservative):
* SOL and WSOL deltas are merged into one "SOL" quote leg; USDC/USDT are stable quote legs.
* exactly one non-quote token up and a quote leg down  -> BUY
* exactly one non-quote token down and a quote leg up  -> SELL
* a token moves with no meaningful quote leg            -> TRANSFER_IN / TRANSFER_OUT
  (airdrops, deposits, wallet-to-wallet moves; never counted as trading PnL)
* token-to-token swaps produce a SELL and a BUY with unknown USD value
* anything more complex is skipped (recorded as UNKNOWN by callers if desired)
"""

from __future__ import annotations

from app.chains.types import NATIVE_SOL, QUOTE_MINTS, STABLE_MINTS, WSOL_MINT, ParsedTx, TradeEvent

SOL_DUST = 0.003  # below this a SOL change is rent/fees/tips, not a trade leg
STABLE_DUST = 0.01


def classify(tx: ParsedTx, wallet: str, sol_usd: float | None) -> list[TradeEvent]:
    if not tx.success:
        return []
    sol = 0.0
    stable: dict[str, float] = {}
    tokens: dict[str, float] = {}
    for d in tx.deltas:
        if d.owner != wallet:
            continue
        if d.mint in (NATIVE_SOL, WSOL_MINT):
            sol += d.change
        elif d.mint in STABLE_MINTS:
            stable[d.mint] = stable.get(d.mint, 0.0) + d.change
        elif d.mint not in QUOTE_MINTS:
            tokens[d.mint] = tokens.get(d.mint, 0.0) + d.change
    tokens = {m: v for m, v in tokens.items() if v != 0}
    if not tokens:
        return []

    quote_mint, quote_amt = None, 0.0
    if abs(sol) >= SOL_DUST:
        quote_mint, quote_amt = WSOL_MINT, sol
    for m, v in stable.items():
        if abs(v) >= STABLE_DUST and abs(v) > abs(quote_amt * (sol_usd or 0)):
            quote_mint, quote_amt = m, v

    def usd(amount: float) -> float | None:
        if quote_mint in STABLE_MINTS:
            return abs(amount)
        if quote_mint == WSOL_MINT and sol_usd:
            return abs(amount) * sol_usd
        return None

    base = dict(wallet=wallet, signature=tx.signature, slot=tx.slot, block_time=tx.block_time,
                fee_sol=tx.fee_sol, source=tx.source)
    events: list[TradeEvent] = []
    if len(tokens) == 1:
        mint, ch = next(iter(tokens.items()))
        if quote_mint and ch > 0 and quote_amt < 0:
            kind = "BUY"
        elif quote_mint and ch < 0 and quote_amt > 0:
            kind = "SELL"
        else:
            kind = "TRANSFER_IN" if ch > 0 else "TRANSFER_OUT"
        if kind in ("BUY", "SELL"):
            q_usd = usd(quote_amt)
            events.append(TradeEvent(**base, kind=kind, mint=mint, token_amount=abs(ch), quote_mint=quote_mint,
                                     quote_amount=abs(quote_amt), quote_usd=q_usd,
                                     price_usd=(q_usd / abs(ch)) if q_usd else None,
                                     usd_is_estimate=quote_mint not in STABLE_MINTS))
        else:
            events.append(TradeEvent(**base, kind=kind, mint=mint, token_amount=abs(ch)))
    elif len(tokens) == 2 and not quote_mint:
        (m1, c1), (m2, c2) = tokens.items()
        if c1 * c2 < 0:
            for m, c in ((m1, c1), (m2, c2)):
                events.append(TradeEvent(**base, kind="BUY" if c > 0 else "SELL", mint=m, token_amount=abs(c)))
    return events
