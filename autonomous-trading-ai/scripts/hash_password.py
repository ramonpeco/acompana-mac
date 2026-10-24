"""Print a bcrypt hash for ADMIN_PASSWORD_HASH.  Usage: python scripts/hash_password.py"""
import getpass

import bcrypt

pw = getpass.getpass("Nueva contraseña de administrador: ")
if len(pw) < 12:
    raise SystemExit("Usa al menos 12 caracteres.")
if pw != getpass.getpass("Repite: "):
    raise SystemExit("No coinciden.")
print(bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode())
