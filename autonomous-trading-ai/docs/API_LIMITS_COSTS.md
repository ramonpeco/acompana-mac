# Informe de limitaciones y costes de APIs

Fecha de revisión: octubre 2026. **Verifica siempre las condiciones vigentes**: los proveedores cambian
límites y precios con frecuencia.

## Cómo se verificó

* **Jupiter**: especificaciones OpenAPI oficiales del repositorio `jup-ag/docs` (`openapi-spec/price/v3`,
  `swap/v1`) y su página de *rate limits*.
* **Helius**: SDK oficial `helius-labs/helius-sdk` (parámetros y base URL de Enhanced Transactions).
* **GeckoTerminal**: guía oficial de la API y cliente de referencia (`alaarab/geckoterminal-api`).
* **DexScreener**: referencia pública de la API (no hay repositorio oficial; contrastado con clientes
  mantenidos como `openSVM/dexscreener-mcp-server`).
* **Pump.fun**: documentación pública del programa (`pump-fun/pump-public-docs`). No se usan APIs web no
  documentadas.
* **Solana RPC**: métodos JSON‑RPC estándar.

> En el entorno donde se desarrolló este proyecto la política de red **bloqueaba** estas APIs
> (api.dexscreener.com, api.geckoterminal.com, api.jup.ag, api.mainnet-beta.solana.com, api.telegram.org).
> Los conectores se probaron con respuestas con la forma documentada (tests `test_parsing.py`,
> `test_solana_adapter.py`) y el pipeline completo con el mercado sintético. **La primera ejecución con
> datos reales debe hacerse en tu servidor** y revisarse en el panel (estado de conectores) y con
> `deploy/diagnose.sh`.

## Proveedores

| Fuente | Uso | Endpoints | Límite | Clave | Coste |
|---|---|---|---|---|---|
| DexScreener | mercados, boosts, perfiles, "DEX paid" | `/tokens/v1/{chain}/{addrs≤30}`, `/token-pairs/v1/...`, `/latest/dex/search`, `/token-boosts/{latest,top}/v1`, `/token-profiles/latest/v1`, `/orders/v1/{chain}/{token}` | 300/min (pares), 60/min (perfiles/boosts/orders) | no | gratis |
| GeckoTerminal | trending, nuevos pools, trades por pool (descubrimiento de wallets), OHLCV | `/networks/solana/{trending_pools,new_pools}`, `/pools/{p}/trades` (últimos 300, 24 h), `/pools/{p}/ohlcv/{tf}` | ~30/min (guía) · ~10/min (docs keyless de CoinGecko) → configurado a 0,3 req/s | no | gratis; planes de pago de CoinGecko para más |
| Jupiter | precios, cotizaciones (impacto real), construcción de swaps (futuro) | `GET /price/v3?ids≤50`, `GET /swap/v1/quote`, `POST /swap/v1/swap` en `api.jup.ag` | keyless 0,5 rps; Free 1 rps; Developer 10 rps… (ventana deslizante 60 s) | opcional `x-api-key` | ver portal de Jupiter; `lite-api.jup.ag` en retirada |
| Solana RPC público | historial de wallets, transacciones, datos de mint | `getSignaturesForAddress`, `getTransaction`, `getAccountInfo`, `getTokenLargestAccounts`, `getTokenSupply`, `getSignatureStatuses` | muy limitado y sin garantías; no apto para producción | no | gratis |
| Helius | RPC fiable + historial parseado (acelerador) | RPC `mainnet.helius-rpc.com`, `GET /v0/addresses/{a}/transactions` | según plan | sí | plan gratuito disponible; planes de pago para volumen |
| Birdeye | (futuro) OHLCV y holders | — | según plan | sí | de pago para uso serio |
| Hyperliquid | integración futura (perpetuos) | — | — | — | — |
| Anthropic | supervisión IA | Messages API | según cuenta | sí | ver abajo |
| Telegram Bot API | notificaciones/comandos | `getUpdates`, `sendMessage` | ~30 msg/s global | token del bot | gratis |

Notas:
* Helius indica que *Enhanced Transactions* está en mantenimiento; se usa solo como acelerador y el
  sistema cae al RPC estándar (que es la vía principal y portable).
* El número de holders no está disponible en las fuentes gratuitas usadas: se muestra como desconocido.
  La concentración se estima con `getTokenLargestAccounts` (incluye vaults de pools).

## Presupuesto de peticiones del agente (valores por defecto)

* Seguimiento del trader: 1 `getSignaturesForAddress` + 1 `getTransaction` por transacción nueva cada
  20 s → ~4.300 llamadas/día en reposo.
* Ranking horario: hasta 20 wallets × (1 + nuevas tx) llamadas RPC; backfill inicial de 3 páginas × 50 tx.
* Escáner cada 60 s: 2 llamadas GeckoTerminal + 1–14 DexScreener; DEX paid cada 10 min (≤ 15).
* Riesgo de token: 3 llamadas RPC por token nuevo (máx. 5 por ciclo de escáner).

Con el RPC público esto **excederá los límites** en cuanto haya actividad: usa Helius u otro RPC privado.
El conector abre el *circuit breaker* tras 6 fallos y el motor de riesgo bloquea entradas con datos
obsoletos.

## Coste de IA (Claude)

Modelo por defecto `claude-opus-5-5` (4 $/MTok entrada, 20 $/MTok salida), esfuerzo `low`.
Una llamada típica (~3k tokens de entrada, ~800 de salida) ≈ 0,03 $. Uso esperado: informe diario +
explicaciones de cambio (≤ 6/día) + análisis de fallos ≈ 10–20 llamadas/día ≈ **0,3–0,6 $/día**. El
límite `AI_DAILY_CALL_BUDGET` (200) acota el máximo (~6 $/día). Para reducir coste: `AI_MODEL=claude-haiku-5-5`
(0,10 $/0,50 $ por MTok) o `AI_PROVIDER=none` (el agente funciona igual con reglas deterministas).

## Limitaciones conocidas

1. **Latencia por sondeo** (15–20 s): no compite con bots que copian en el mismo bloque. Por eso se
   excluyen traders con mantenimiento < 3 min y se rechazan señales con retraso > `max_signal_delay_s`.
   Mejora futura: websockets `logsSubscribe` o webhooks de Helius.
2. **Descubrimiento de wallets** limitado a los últimos 300 trades por pool de GeckoTerminal.
3. **USD estimados** para operaciones en SOL (precio de SOL en la ingesta).
4. **Historial**: el backfill inicial cubre 150 transacciones por wallet; wallets muy activas tendrán
   ventanas cortas (la calidad de datos lo refleja).
5. **Graduated** y **holders** son estimados/desconocidos con fuentes gratuitas.
6. **Backtests con datos registrados** requieren que el agente haya acumulado datos (precios propios,
   operaciones de traders); el riesgo del token usado es el último evaluado (pequeño sesgo).
7. El mundo **sintético** asigna "habilidad" mirando el futuro: sirve para validar mecánica y robustez,
   **no** para estimar rentabilidad.
