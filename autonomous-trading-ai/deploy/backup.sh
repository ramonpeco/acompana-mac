#!/usr/bin/env bash
# Compressed pg_dump of the app database, keeps the last 14 dumps.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
F="backups/trading-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
docker compose exec -T db pg_dump -U trading -d trading --no-owner | gzip > "$F"
chmod 600 "$F"
ls -1t backups/trading-*.sql.gz | tail -n +15 | xargs -r rm --
echo "Backup: $F ($(du -h "$F" | cut -f1))"
