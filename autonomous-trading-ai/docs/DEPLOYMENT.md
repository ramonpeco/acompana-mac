# Guía de despliegue (Ubuntu / Google Cloud VM)

## Antes de instalar en tu VM existente

Tu VM ya tiene PostgreSQL y servicios web. El instalador **solo inspecciona**:

```bash
free -m; df -h; ss -ltnp            # memoria, disco y puertos en uso
systemctl list-units --type=service --state=running | grep -E 'nginx|apache|postgres|caddy'
```

* Si 80/443 están ocupados → no uses el perfil `https`; añade `deploy/nginx-site.conf` a tu nginx.
* Si 8000 está ocupado → `APP_PORT=8010` en `.env`.
* El contenedor `db` no publica el puerto 5432, así que no colisiona con tu PostgreSQL. Si prefieres usar
  tu PostgreSQL, sigue la opción B de [INSTALL.md](INSTALL.md) (base de datos y usuario nuevos).
* Una e2‑small (2 GB) es suficiente; con e2‑micro (1 GB) añade 1–2 GB de swap.

## Google Cloud

1. Regla de firewall solo para 80/443 (o ninguna si accedes por túnel SSH:
   `gcloud compute ssh VM -- -L 8000:127.0.0.1:8000`).
2. `./deploy/install.sh`
3. DNS → IP externa estática; HTTPS con Caddy (`DOMAIN`) o con tu nginx + certbot.

## Operación continua

* `restart: unless-stopped` en Docker (o `Restart=always` en systemd) reinicia ante fallos y al arrancar el
  servidor.
* Healthcheck del contenedor contra `/api/health` (DB + estado del agente + conectores).
* El estado está en PostgreSQL: el agente continúa tras reinicios sin duplicar órdenes.
* Logs JSON con rotación (`docker compose logs -f app`).
* Métricas estilo Prometheus en `/metrics` (ticks, errores consecutivos, peticiones/errores/estado por
  conector, órdenes, posiciones abiertas).

## Monitorización mínima recomendada

* Un monitor externo (UptimeRobot, Cloud Monitoring uptime check) sobre `https://dominio/api/health`
  comprobando `"ok":true` y `"agent_heartbeat_stale":false`.
* Notificaciones de Telegram para alertas de riesgo.
* `./deploy/diagnose.sh` para diagnóstico rápido (solo lectura).

## Scripts

| Script | Función |
|---|---|
| `deploy/install.sh` | Comprobaciones, `.env` con secretos, build y arranque |
| `deploy/update.sh` | Backup → `git pull` → rebuild → migraciones → health |
| `deploy/backup.sh` | `pg_dump` comprimido, conserva 14 (añádelo a cron) |
| `deploy/restore.sh` | Restaura un dump (confirmación explícita) |
| `deploy/diagnose.sh` | Estado, salud, métricas, recursos, puertos, errores, conectividad a APIs |

## Seguridad

* `.env` con permisos 600, nunca en el repositorio (`.gitignore`).
* App y DB solo en loopback/red interna; HTTPS con cabeceras HSTS/nosniff/DENY en Caddy.
* Login con bcrypt y retardo ante fallos; JWT HS256 con secreto aleatorio; todas las acciones
  administrativas en `audit_log`.
* Claves privadas: solo en keystore cifrado (scrypt + AES‑GCM), y únicamente si algún día se habilita la
  ejecución real.
