#!/usr/bin/env bash
# Update: backup -> pull -> rebuild -> migrate (on start) -> health check.
set -euo pipefail
cd "$(dirname "$0")/.."
./deploy/backup.sh
git pull --ff-only
docker compose up -d --build
APP_PORT=$(grep -E '^APP_PORT=' .env | cut -d= -f2); APP_PORT=${APP_PORT:-8000}
for i in $(seq 1 30); do curl -fsS "http://127.0.0.1:$APP_PORT/api/health" && { echo; echo "Actualizado."; exit 0; }; sleep 3; done
echo "La app no respondió tras actualizar. Revisa: docker compose logs app"; exit 1
