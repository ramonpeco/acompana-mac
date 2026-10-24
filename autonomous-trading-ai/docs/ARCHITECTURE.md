# Arquitectura técnica

## Visión general

```
                ┌────────────── Panel web (React/TS/Tailwind) ───────────────┐
                │ Dashboard · Scanner · Traders · Copy · AI · Risk · Backtest │
                └───────────────▲──────────────────────────▲────────────────┘
                         REST /api/*                 WebSocket /api/ws
┌───────────────────────────────┴──────────────────────────┴───────────────────────────┐
│ FastAPI (app/main.py, app/api/routes.py) · JWT admin · auditoría                        │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ Agent loop (app/agent/loop.py)  ── cada AGENT_TICK_SECONDS ──────────────────────────── │
│   scanner ▸ discovery ▸ ranking ▸ follow tick ▸ risk tick ▸ switch ▸ select ▸ shadows   │
├───────────────┬───────────────┬────────────────┬──────────────┬────────────────────────┤
│ Scanner       │ TraderService │ CopyEngine     │ Risk engine  │ AISupervisor           │
│ scanner/      │ traders/      │ copytrade/     │ risk/        │ ai/ (sin órdenes)      │
├───────────────┴───────┬───────┴────────────────┴──────┬───────┴────────────────────────┤
│ ChainAdapter (chains/base.py)                          │ Simulador (sim/execution.py)   │
│  ├ SolanaAdapter: DexScreener, GeckoTerminal, Jupiter, │ Cartera (sim/portfolio.py)     │
│  │                Solana RPC, Helius (opcional)        │                                │
│  └ SandboxAdapter: mundo sintético determinista         │ LiveExecutor (desactivado)    │
├────────────────────────────────────────────────────────┴────────────────────────────────┤
│ HttpConnector (connectors/base.py): rate limit · reintentos+backoff · caché · circuit    │
│ breaker · salud · etiqueta de fuente y hora (Sourced)                                    │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ PostgreSQL (SQLAlchemy async + Alembic) — todo el estado vive aquí                       │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

Separación estricta (requisito de seguridad): **análisis** (scanner/traders) · **decisión** (agent loop +
logic) · **riesgo** (risk/engine.py, determinista) · **simulación** (sim/) · **ejecución real**
(execution/live.py, no importada por el agente).

## Flujo de una copia

1. `TraderService.sync_wallet` pide firmas nuevas (`getSignaturesForAddress ... until=newest`) y cada
   transacción (`getTransaction`, jsonParsed, v0). Las transacciones fallidas se descartan.
2. `connectors/solana_rpc.parse_transaction` convierte pre/post balances (SOL y SPL, con
   `loadedAddresses` de tablas de lookup) en deltas por propietario; la comisión de red se separa.
3. `traders/swaps.classify` decide BUY / SELL / TRANSFER (SOL+WSOL fusionados; USDC/USDT como cotización;
   cambios de SOL < 0.003 son renta/fees, no operaciones). Se guarda en `wallet_trades` con
   `UNIQUE(wallet, signature, mint)` → deduplicación.
4. `CopyEngine.process` (idempotencia por `portfolio:signature:mint:side`):
   - mercado fresco (si el dato tiene más de `max_data_age_s/2`, se refresca);
   - riesgo del token (si no está evaluado o tiene >30 min, se evalúa);
   - tamaño `copy_buy_size` → `risk.check_entry` (rechazos y recortes explicados);
   - impacto real con cotización Jupiter si existe;
   - `sim.simulate` → orden con precio del trader, precio de referencia, precio ejecutado, latencia,
     retraso de señal (`detected_at - block_time`) y slippage.
5. Ventas: fracción = vendido / cantidad del trader que replicamos (`trader_qty`). Si el trader vende algo
   comprado antes de que lo siguiéramos, se ignora.
6. Al cerrarse una posición se actualizan las estadísticas de la sesión de seguimiento; `evaluate_switch`
   decide si se cambia de trader.

## Reglas de cambio de trader (`copytrade/logic.py`)

| Regla | Tipo | Parámetro |
|---|---|---|
| N pérdidas cerradas consecutivas | crítica (inmediata) | `consecutive_losses` (2 por defecto, 0 = off), `loss_basis` copied/trader, `min_observation_trades` |
| Drawdown de la sesión | crítica | `max_session_drawdown_pct` |
| Pérdida diaria de la sesión | crítica | `max_session_daily_loss_pct` |
| Inactividad | no crítica | `inactivity_hours` |
| Caída de puntuación | no crítica | `score_drop_points` |
| Cambio de comportamiento (frecuencia ×4 o ÷6) | no crítica | — |
| Aumento de riesgo (tamaño medio ×5) | no crítica | — |
| Reducción de liquidez | no crítica | `min_liquidity_usd` del perfil |

Las reglas no críticas respetan `min_follow_minutes` (anti‑churn). Un trader abandonado no puede volver a
elegirse durante `cooldown_days`. Más de `max_switches_per_day` cambios → el agente queda sin trader hasta
el día siguiente y emite una alerta.

## Puntuación de traders (`traders/scoring.py`)

Suma ponderada de componentes acotados en [0,1] (ninguno domina):
rentabilidad ajustada al riesgo (tanh de ROI y Sharpe‑like) 22 %, consistencia (días positivos y
estabilidad reciente vs. antigua) 16 %, drawdown 12 %, tamaño muestral 10 %, calidad de datos 10 %,
liquidez de activos 7 %, historial 6 %, diversificación 6 %, replicabilidad (tiempo de mantenimiento) 6 %,
frecuencia 5 %.

Exclusiones: < 8 operaciones cerradas, < 2 días de historial, calidad < 0.4, mantenimiento mediano
< 3 min (no replicable con sondeo), > 60 compras/día (bot), concentración > 60 % con pocas operaciones,
liquidez media < 15k$, win rate ≥ 97 % o PF ≥ 50 con muestra suficiente (sospechoso), inactividad > 48 h,
ROI ≤ 0.

## Datos y su fiabilidad

* Cada `TokenMarket` lleva `sources` y `estimated`; `None` significa **desconocido** (nunca se rellena).
* Los USD de operaciones en SOL son **estimaciones** con el precio de SOL en la ingesta
  (`usd_is_estimate=true`); ambos lados de una operación de ida y vuelta usan el mismo precio, de modo que
  el rendimiento relativo en SOL es exacto.
* El coste de tokens llegados por transferencia o comprados antes de la ventana observada es desconocido:
  esas ventas no cuentan como beneficio y reducen la calidad de datos (`known_basis_ratio`).
* `verified=true` solo con ≥ 10 cierres, ≥ 80 % de coste conocido y ≥ 90 % de valores USD conocidos.

## Persistencia y recuperación

Todo el estado (sesión de seguimiento, posiciones, órdenes, configuración en tiempo real, carteras
sombra, latido) está en PostgreSQL. Tras un reinicio el agente continúa la sesión activa; las órdenes son
idempotentes, por lo que un evento re‑entregado no duplica operaciones. El bucle sobrevive a cualquier
excepción (backoff exponencial hasta 16×) y la expone en `/api/health` y en el panel.

## Tablas principales

`wallets`, `wallet_trades`, `trader_scores`, `tokens`, `token_price_points`, `portfolios` (main/shadow),
`positions`, `orders`, `equity_snapshots`, `follow_sessions`, `agent_events`, `ai_analyses`,
`strategy_versions`, `backtest_runs`, `settings_kv`, `users`, `audit_log`.

## Redis

No se usa: un único proceso con caché en memoria por conector es suficiente para un VPS pequeño. Sería
útil si se escalara a varios workers (rate limits compartidos, pub/sub de WebSocket).

## Multi‑cadena

`ChainAdapter` define la interfaz (mercados, trending, nuevos pares, precios, historial de wallet,
traders de un pool, datos on‑chain del token, cotización, finalización). Solo Solana está implementada;
Ethereum/Base/BNB/Arbitrum están listadas pero desactivadas en el panel hasta validar Solana.
