"""Integration preflight for live execution. Never sends a transaction.

Checks: RPC reachable; Jupiter quote 0.01 SOL->USDC; swap transaction built for
the keystore wallet; simulateTransaction succeeds (sigVerify=false). If every
check passes it records `live_preflight_ok` (valid 24h), one of the conditions
required before an administrator can arm live mode.
    python scripts/live_preflight.py
"""
import asyncio
import base64
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.chains.types import USDC_MINT, WSOL_MINT  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.connectors.jupiter import Jupiter  # noqa: E402
from app.connectors.solana_rpc import SolanaRPC  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.execution.live import decrypt_secret  # noqa: E402
from app.runtime import kv_set  # noqa: E402


async def main() -> None:
    s = get_settings()
    from solders.keypair import Keypair

    kp = Keypair.from_bytes(decrypt_secret(json.loads(Path(s.live_keystore_path).read_text()), s.live_keystore_passphrase))
    rpc, jup = SolanaRPC(s.solana_rpc_url), Jupiter(s.jupiter_base_url, s.jupiter_api_key)
    print("slot:", await rpc.get_slot())
    q = (await jup.quote(WSOL_MINT, USDC_MINT, 10_000_000, 100)).value
    print("quote outAmount:", q["outAmount"], "impact:", q["priceImpactPct"])
    tx = (await jup.post("/swap/v1/swap", json_body={"quoteResponse": q, "userPublicKey": str(kp.pubkey()),
                                                     "dynamicComputeUnitLimit": True}, cache_ttl_s=0)).value["swapTransaction"]
    sim = await rpc.call("simulateTransaction", [tx, {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True}])
    err = (sim.value or {}).get("value", {}).get("err")
    print("simulation err:", err, "logs:", len((sim.value or {}).get("value", {}).get("logs") or []))
    if err:
        raise SystemExit("Preflight FALLIDO (¿saldo insuficiente en la wallet?)")
    async with session_scope() as ses:
        await kv_set(ses, "live_preflight_ok", datetime.now(UTC).isoformat(), "preflight")
    print("Preflight OK registrado (válido 24 h). Sigue siendo necesario armar el modo real desde el panel.")
    _ = base64  # keep import for clarity of the tx encoding used above


asyncio.run(main())
