"""Encrypt a Solana keypair into the keystore used by the (disabled) live executor.

Usage (inside backend venv, requires `pip install '.[live]'`):
    python scripts/create_keystore.py /path/to/keypair.json [./keystore/wallet.enc]

The input is the standard 64-byte JSON array produced by `solana-keygen`. The
plain key is never written anywhere; delete the input file after verifying.
Use a dedicated wallet holding only the amount you are willing to lose.
"""
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.execution.live import write_keystore  # noqa: E402

src = Path(sys.argv[1])
dst = sys.argv[2] if len(sys.argv) > 2 else "./keystore/wallet.enc"
secret = bytes(json.loads(src.read_text()))
if len(secret) != 64:
    raise SystemExit("Se esperaba un keypair de 64 bytes (formato solana-keygen).")
try:
    from solders.keypair import Keypair
    print("Pubkey:", Keypair.from_bytes(secret).pubkey())
except ImportError:
    print("(instala solders para mostrar la pubkey)")
pw = getpass.getpass("Passphrase del keystore (>=12 caracteres): ")
if pw != getpass.getpass("Repite: "):
    raise SystemExit("No coinciden.")
write_keystore(dst, secret, pw)
print(f"Keystore cifrado escrito en {dst} (permisos 600). Configura LIVE_KEYSTORE_PASSPHRASE en .env.")
