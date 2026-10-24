"""Live execution — PREPARED, NOT ACTIVE.

This module is not wired into the agent loop. The agent only uses the paper
engine. To ever place a real order, ALL of these must hold (checked on every call):

1. env ``LIVE_TRADING_ENABLED=true`` (server configuration, not reachable from UI/Telegram);
2. an encrypted keystore exists and decrypts with ``LIVE_KEYSTORE_PASSPHRASE``;
3. ``live_preflight_ok`` was recorded by ``scripts/live_preflight.py`` within 24h
   (RPC reachable, Jupiter quote + swap build + simulateTransaction succeed);
4. an admin armed live mode through ``POST /api/live/arm`` re-entering the
   password and typing the confirmation phrase; arming expires after 12h;
5. the order passed the deterministic risk engine, and is below ``max_live_order_usd``.

Swap path: Jupiter ``GET /swap/v1/quote`` -> ``POST /swap/v1/swap`` (unsigned
versioned tx, base64) -> sign locally with solders -> ``sendTransaction``.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.runtime import kv_get

ARM_PHRASE = "ACEPTO OPERAR CON DINERO REAL"
ARM_TTL = timedelta(hours=12)
PREFLIGHT_TTL = timedelta(hours=24)


class LiveTradingDisabled(Exception):
    pass


# ---------------- keystore ----------------
def _kdf(salt: bytes) -> Scrypt:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)


def encrypt_secret(secret: bytes, passphrase: str) -> dict[str, str]:
    if len(passphrase) < 12:
        raise ValueError("passphrase must have at least 12 characters")
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _kdf(salt).derive(passphrase.encode())
    ct = AESGCM(key).encrypt(nonce, secret, b"autonomous-trading-ai/v1")
    return {"v": "1", "kdf": "scrypt", "salt": base64.b64encode(salt).decode(),
            "nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}


def decrypt_secret(blob: dict[str, str], passphrase: str) -> bytes:
    key = _kdf(base64.b64decode(blob["salt"])).derive(passphrase.encode())
    return AESGCM(key).decrypt(base64.b64decode(blob["nonce"]), base64.b64decode(blob["ct"]), b"autonomous-trading-ai/v1")


def write_keystore(path: str, secret: bytes, passphrase: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(encrypt_secret(secret, passphrase)))
    os.chmod(p, 0o600)


# ---------------- guard ----------------
@dataclass
class GuardStatus:
    allowed: bool
    checks: dict[str, bool]
    reasons: list[str]


async def guard_status(session: AsyncSession, s: Settings) -> GuardStatus:
    now = datetime.now(UTC)
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    checks["env_enabled"] = s.live_trading_enabled
    if not s.live_trading_enabled:
        reasons.append("LIVE_TRADING_ENABLED is false (default)")
    ks = Path(s.live_keystore_path)
    checks["keystore_present"] = ks.exists()
    checks["keystore_decrypts"] = False
    if ks.exists() and s.live_keystore_passphrase:
        try:
            decrypt_secret(json.loads(ks.read_text()), s.live_keystore_passphrase)
            checks["keystore_decrypts"] = True
        except Exception:
            reasons.append("keystore cannot be decrypted")
    elif not ks.exists():
        reasons.append("no encrypted keystore")
    pre = await kv_get(session, "live_preflight_ok")
    checks["preflight_recent"] = bool(pre) and now - datetime.fromisoformat(pre) < PREFLIGHT_TTL
    if not checks["preflight_recent"]:
        reasons.append("integration preflight missing or older than 24h")
    armed = await kv_get(session, "live_armed_at")
    checks["admin_armed"] = bool(armed) and now - datetime.fromisoformat(armed) < ARM_TTL
    if not checks["admin_armed"]:
        reasons.append("not armed by an administrator (or arming expired)")
    return GuardStatus(all(checks.values()), checks, reasons)


class LiveExecutor:
    """Builds, signs and sends Jupiter swaps. Every public method re-checks the guard."""

    def __init__(self, settings: Settings, jupiter, rpc, max_live_order_usd: float = 25.0):
        self.s = settings
        self.jup = jupiter
        self.rpc = rpc
        self.max_live_order_usd = max_live_order_usd

    async def _require(self, session: AsyncSession) -> None:
        st = await guard_status(session, self.s)
        if not st.allowed:
            raise LiveTradingDisabled("; ".join(st.reasons))

    def _keypair(self):
        try:
            from solders.keypair import Keypair  # optional dependency: pip install .[live]
        except ImportError as exc:
            raise LiveTradingDisabled("solders not installed (pip install '.[live]')") from exc
        blob = json.loads(Path(self.s.live_keystore_path).read_text())
        return Keypair.from_bytes(decrypt_secret(blob, self.s.live_keystore_passphrase))

    async def build_swap(self, quote_response: dict[str, Any], user_pubkey: str) -> str:
        res = await self.jup.post("/swap/v1/swap", json_body={
            "quoteResponse": quote_response, "userPublicKey": user_pubkey,
            "dynamicComputeUnitLimit": True, "prioritizationFeeLamports": "auto"}, cache_ttl_s=0)
        return res.value["swapTransaction"]

    async def execute(self, session: AsyncSession, quote_response: dict[str, Any], usd_value: float,
                      risk_approved: bool) -> str:
        await self._require(session)
        if not risk_approved:
            raise LiveTradingDisabled("order not approved by the risk engine")
        if usd_value > self.max_live_order_usd:
            raise LiveTradingDisabled(f"order {usd_value:.2f} USD above live cap {self.max_live_order_usd:.2f}")
        from solders.transaction import VersionedTransaction

        kp = self._keypair()
        raw = base64.b64decode(await self.build_swap(quote_response, str(kp.pubkey())))
        tx = VersionedTransaction(VersionedTransaction.from_bytes(raw).message, [kp])
        sim = await self.rpc.call("simulateTransaction", [base64.b64encode(bytes(tx)).decode(),
                                                          {"encoding": "base64", "commitment": "processed"}])
        if (sim.value or {}).get("value", {}).get("err"):
            raise LiveTradingDisabled(f"simulation failed: {sim.value['value']['err']}")
        sent = await self.rpc.call("sendTransaction", [base64.b64encode(bytes(tx)).decode(),
                                                       {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}])
        return sent.value
