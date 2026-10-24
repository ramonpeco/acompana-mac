# Autonomous Crypto Trading AI

Plataforma de **investigación** de copy trading autónomo en Solana: descubre wallets, las clasifica, sigue
automáticamente a la mejor según reglas deterministas, replica sus operaciones con **capital simulado**
(paper trading con ejecución realista), cambia de trader cuando incumple las reglas (p. ej. 2 pérdidas
consecutivas) y mide científicamente si eso funciona mejor que las alternativas.

> **Estado: PAPER TRADING.** La ejecución real está preparada pero **desactivada** y **no conectada** al agente
> (ver [docs/LIVE_TRADING.md](docs/LIVE_TRADING.md)). Nada de este proyecto promete rentabilidad.

## Qué incluye

| Módulo | Descripción |
|---|---|
| Escáner de tokens | Trending, Gainers, Top, New pairs, Graduated, DEX paid, High volume, Smart money · 1m–7d · DexScreener + GeckoTerminal + Jupiter |
| Riesgo de memecoins | Mint/freeze authority, extensiones Token‑2022, concentración, liquidez, edad, wash volume, honeypot, liquidez retirada… explicable y con "desconocidos" |
| Descubrimiento de wallets | Traders de pools trending (trades públicos) + alta manual |
| Reconstrucción de operaciones | Swaps desde `getTransaction` (deltas de balance), separa transferencias/airdrops |
| Métricas y ranking | FIFO, PnL realizado/no realizado, win rate, PF, drawdown, hold, frecuencia, concentración, calidad de datos → puntuación 0–100 + filtros de exclusión |
| Motor de copy trading | Tamaño proporcional con multiplicador (no apalancamiento), ventas proporcionales, idempotencia, retraso de señal |
| Cambio automático de trader | N pérdidas consecutivas, drawdown/pérdida diaria de sesión, inactividad, deterioro, cambio de comportamiento, liquidez; anti‑churn y enfriamiento |
| Carteras sombra | Siguen copiando a los traders abandonados para medir si abandonarlos fue acertado |
| Simulador | Latencia, deriva adversa, impacto (ruta Jupiter o AMM), slippage con reversión, comisiones, fallos, parciales |
| Motor de riesgo | Perfiles CONSERVADOR/EQUILIBRADO/AGRESIVO, límites por operación/token/total, pérdida diaria, drawdown, SL/TP, datos obsoletos, kill switch. Sin martingala |
| IA (Claude) | Supervisa: evalúa candidatos, explica cambios, informes diarios, análisis de fallos, propuestas versionadas. **No ejecuta órdenes** |
| Backtesting | Estrategias A–F, sin look‑ahead, bootstrap vs referencia, walk‑forward, escenarios favorable/desfavorable |
| Panel web | React + TypeScript + Tailwind: Dashboard, Scanner, Trader Explorer, Copy Trading, AI Agent, Risk Control, Backtesting |
| Telegram | Notificaciones y `/status /balance /trader /positions /history /risk /report /pause /resume` |
| Infra | FastAPI, SQLAlchemy async, PostgreSQL, Alembic, Docker Compose, Caddy (HTTPS), health checks, métricas, backups |

## Inicio rápido

```bash
# Desarrollo local con mercado SINTÉTICO (sin claves ni red):
cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
DATA_MODE=sandbox ADMIN_PASSWORD=cambia-esto-123 .venv/bin/uvicorn app.main:app --port 8000
cd ../frontend && npm install && npm run dev        # http://localhost:5173

# Servidor Ubuntu con datos reales (Docker):
./deploy/install.sh
```

Documentación: [Instalación](docs/INSTALL.md) · [Manual de usuario](docs/USER_GUIDE.md) ·
[Arquitectura](docs/ARCHITECTURE.md) · [Despliegue](docs/DEPLOYMENT.md) ·
[Límites y costes de APIs](docs/API_LIMITS_COSTS.md) · [Evaluación científica](docs/EVALUATION.md) ·
[Ejecución real](docs/LIVE_TRADING.md)

## Pruebas

```bash
cd backend && PYTHONPATH=. .venv/bin/pytest -q          # 52 pruebas (unitarias + integración + API)
TEST_DATABASE_URL=postgresql+asyncpg://... .venv/bin/pytest tests/test_agent_sandbox.py   # mismo escenario en PostgreSQL
cd frontend && npm run build                              # typecheck + build
```

## Aviso

Herramienta de investigación con capital simulado. No es asesoramiento financiero. Las memecoins son
extremadamente volátiles y muchas son fraudulentas; los resultados simulados o históricos no garantizan
resultados futuros.
