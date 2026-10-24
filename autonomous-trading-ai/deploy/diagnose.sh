#!/usr/bin/env bash
# Read-only diagnostics: containers, health, connectors, resources, recent errors.
cd "$(dirname "$0")/.."
APP_PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2); APP_PORT=${APP_PORT:-8000}
echo "== Contenedores"; docker compose ps
echo; echo "== Salud"; curl -s "http://127.0.0.1:$APP_PORT/api/health" | python3 -m json.tool 2>/dev/null || echo "sin respuesta"
echo; echo "== Métricas"; curl -s "http://127.0.0.1:$APP_PORT/metrics" | head -30
echo; echo "== Recursos"; free -m | head -2; df -h . | tail -1
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null
echo; echo "== Puertos"; ss -ltn | grep -E ":(80|443|$APP_PORT|5432)\b" || true
echo; echo "== Errores recientes (app)"; docker compose logs --since 1h app 2>&1 | grep -E '"level": "(ERROR|WARNING)"' | tail -20
echo; echo "== Conectividad a APIs públicas"
for u in https://api.dexscreener.com/token-profiles/latest/v1 https://api.geckoterminal.com/api/v2/networks https://api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112 https://api.telegram.org; do
  printf '%-75s %s\n' "$u" "$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$u")"
done
