#!/usr/bin/env bash
# Restore a dump created by backup.sh: ./deploy/restore.sh backups/trading-XXXX.sql.gz
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f "${1:-}" ] || { echo "uso: $0 backups/trading-....sql.gz"; exit 1; }
read -r -p "Esto SOBRESCRIBE la base de datos actual. Escribe 'restaurar' para continuar: " ok
[ "$ok" = "restaurar" ] || exit 1
docker compose stop app
docker compose exec -T db psql -U trading -d postgres -c "DROP DATABASE IF EXISTS trading WITH (FORCE);" -c "CREATE DATABASE trading OWNER trading;"
gunzip -c "$1" | docker compose exec -T db psql -U trading -d trading -q
docker compose start app
echo "Restaurado desde $1"
