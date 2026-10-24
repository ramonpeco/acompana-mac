# Manual de instalación

## Requisitos

* Ubuntu 22.04/24.04 (o cualquier Linux con Docker), 2 GB RAM recomendados (1 GB + swap funciona), 5 GB de disco.
* Docker Engine + plugin `docker compose` (instalación oficial: https://docs.docker.com/engine/install/ubuntu/).
* Opcional pero recomendado: clave de **Helius** (RPC y historial más fiables que el RPC público),
  clave de **Anthropic** (supervisión con IA), bot de **Telegram**.

## Opción A — Docker (recomendada)

```bash
git clone <repo> && cd <repo>/autonomous-trading-ai
./deploy/install.sh
```

El instalador:
1. muestra memoria, disco y CPU; aborta si hay < 5 GB libres;
2. comprueba los puertos `APP_PORT` (8000), 80 y 443. **No modifica** nginx, apache ni PostgreSQL
   existentes; si 80/443 están ocupados te indica que uses `deploy/nginx-site.conf` con tu servidor web;
3. crea `.env` con secretos aleatorios (PostgreSQL, JWT, contraseña inicial de admin) y permisos 600;
4. construye y arranca `db` + `app` (la base de datos no publica puertos; la app escucha solo en 127.0.0.1);
5. espera a que `/api/health` responda.

Después edita `.env` para añadir `HELIUS_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_*` y ejecuta
`docker compose up -d`.

Si tu red intercepta TLS (proxy corporativo), construye con la CA: `docker build --secret id=ca,src=/ruta/ca.crt .`

### HTTPS

* Sin servidor web previo: pon `DOMAIN=tu.dominio` en `.env`, apunta el DNS al servidor y
  `docker compose --profile https up -d` (Caddy obtiene el certificado automáticamente).
* Con nginx existente: copia `deploy/nginx-site.conf`, ajusta `server_name`, `nginx -t`, recarga y `certbot --nginx`.

## Opción B — Sin Docker (usar un PostgreSQL existente)

```bash
sudo -u postgres psql -c "CREATE USER trading WITH PASSWORD '...';" -c "CREATE DATABASE trading OWNER trading;"
sudo useradd -r -m tradingai && sudo mkdir -p /opt/autonomous-trading-ai && sudo chown tradingai /opt/autonomous-trading-ai
# copia el proyecto a /opt/autonomous-trading-ai
cd /opt/autonomous-trading-ai/backend && python3.12 -m venv .venv && .venv/bin/pip install .
cd ../frontend && npm ci && npm run build        # el backend sirve frontend/dist
cp ../.env.example ../.env  # DATABASE_URL=postgresql+asyncpg://trading:...@localhost:5432/trading
sudo cp ../deploy/trading-ai.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now trading-ai
```

Se crea una base de datos **nueva**; ninguna base existente se toca.

## Desarrollo local

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
DATA_MODE=sandbox ADMIN_PASSWORD=dev-password-123 .venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev     # proxy /api -> :8000
```

`DATA_MODE=sandbox` usa un mercado sintético determinista (banner naranja permanente en el panel). Para
datos reales usa `DATA_MODE=live` (por defecto) — requiere acceso de red a las APIs listadas en
[API_LIMITS_COSTS.md](API_LIMITS_COSTS.md).

## Primer acceso

1. Abre el panel, usuario `admin` y la contraseña inicial mostrada por el instalador.
2. Genera un hash: `docker compose exec app python /app/scripts/hash_password.py`, ponlo en
   `ADMIN_PASSWORD_HASH`, borra `ADMIN_PASSWORD` y reinicia.
3. Revisa **Risk Control** (perfil EQUILIBRADO por defecto, capital simulado 52 $).
