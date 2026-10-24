#!/usr/bin/env bash
# Install on Ubuntu (22.04/24.04) with Docker. Safe by design:
#  * inspects resources and ports first and STOPS on conflicts instead of changing anything;
#  * never edits existing nginx/apache/postgresql configuration;
#  * only creates files inside this directory (.env, keystore/, backups/).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
red() { printf '\033[31m%s\033[0m\n' "$*"; }
grn() { printf '\033[32m%s\033[0m\n' "$*"; }
ylw() { printf '\033[33m%s\033[0m\n' "$*"; }

echo "== Comprobando el sistema"
. /etc/os-release 2>/dev/null && echo "SO: ${PRETTY_NAME:-desconocido}"
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
DISK_GB=$(df -BG --output=avail "$ROOT" | tail -1 | tr -dc '0-9')
echo "Memoria: ${MEM_MB} MB · Disco libre: ${DISK_GB} GB · CPUs: $(nproc)"
[ "$MEM_MB" -lt 1500 ] && ylw "Aviso: <1.5 GB de RAM. Recomendado 2 GB (o añade swap)."
[ "$DISK_GB" -lt 5 ] && { red "Menos de 5 GB libres: libera espacio antes de continuar."; exit 1; }

APP_PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2 || true); APP_PORT=${APP_PORT:-8000}
echo "== Puertos en uso relevantes"
CONFLICT=0
for p in "$APP_PORT" 80 443; do
  if ss -ltnH "( sport = :$p )" 2>/dev/null | grep -q .; then
    PROC=$(ss -ltnpH "( sport = :$p )" 2>/dev/null | sed -E 's/.*users:\(\("([^"]+)".*/\1/' | head -1)
    ylw "Puerto $p ocupado por: ${PROC:-?}"
    [ "$p" = "$APP_PORT" ] && CONFLICT=1
  fi
done
if [ "$CONFLICT" = 1 ]; then red "El puerto $APP_PORT está ocupado. Cambia APP_PORT en .env y reejecuta."; exit 1; fi
if ss -ltnH '( sport = :80 or sport = :443 )' | grep -q .; then
  ylw "80/443 ya en uso (¿nginx/apache existente?). NO uses el perfil https de Caddy;"
  ylw "usa deploy/nginx-site.conf como plantilla para tu servidor web actual."
fi
systemctl is-active --quiet postgresql 2>/dev/null && ylw "Hay un PostgreSQL del sistema activo: no se modifica. La app usa su propio contenedor (sin puerto publicado)."

echo "== Docker"
if ! command -v docker >/dev/null; then
  ylw "Docker no está instalado. Instálalo con el método oficial y reejecuta:"
  echo "  https://docs.docker.com/engine/install/ubuntu/"
  exit 1
fi
docker compose version >/dev/null || { red "Falta el plugin 'docker compose'."; exit 1; }

echo "== Configuración"
if [ ! -f .env ]; then
  cp .env.example .env
  rnd() { python3 -c "import secrets;print(secrets.token_urlsafe($1))"; }
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rnd 24)|" .env
  sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(rnd 48)|" .env
  ADMIN_PW=$(rnd 12)
  sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$ADMIN_PW|" .env
  chmod 600 .env
  grn "Creado .env con secretos aleatorios. Usuario: admin · contraseña inicial: $ADMIN_PW"
  ylw "Cámbiala por ADMIN_PASSWORD_HASH (scripts/hash_password.py) tras el primer acceso."
else
  echo ".env ya existe: no se toca."
fi
mkdir -p keystore backups && chmod 700 keystore backups

echo "== Construyendo y arrancando"
docker compose up -d --build
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$APP_PORT/api/health" >/dev/null 2>&1; then grn "OK: http://127.0.0.1:$APP_PORT"; break; fi
  sleep 3
done
curl -fsS "http://127.0.0.1:$APP_PORT/api/health" || { red "La app no responde; ejecuta deploy/diagnose.sh"; exit 1; }
echo
grn "Instalado. Copias de seguridad diarias (recomendado):"
echo "  (crontab -l 2>/dev/null; echo \"17 3 * * * $ROOT/deploy/backup.sh >> $ROOT/backups/backup.log 2>&1\") | crontab -"
