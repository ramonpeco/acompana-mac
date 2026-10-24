# Manual de usuario

Todo lo que ves es **paper trading**: datos de mercado reales (en modo `live`) y capital simulado.

## Dashboard
Patrimonio, PnL realizado/no realizado, rentabilidad, drawdown, posiciones abiertas/cerradas, tasa de
acierto, costes (comisiones + slippage), trader seguido (racha de pérdidas en curso), estado del agente
y de cada conector (peticiones, errores, latencia), y actividad en tiempo real por WebSocket.

## Token Scanner
Categorías Trending, Gainers, Top, New pairs, Graduated, DEX paid, High volume y Smart money; intervalos
1m–7d; filtros de liquidez, volumen y capitalización. Notas:
* Las fuentes dan ventanas m5/h1/h6/h24; 1m y 4h se aproximan (se indica en pantalla) y 7d se calcula
  solo con historial propio de ≥ 5 días.
* **Graduated**: tokens de pump.fun (mint terminado en `pump`) con pool fuera de la curva de bonding
  (PumpSwap/Raydium…) — marcado como estimado.
* **DEX paid**: pedido de pago aprobado en DexScreener (perfil/servicio). No es señal de legitimidad.
* **Smart money**: tokens comprados en 24 h por wallets con puntuación ≥ 55.
* Haz clic en un token para ver sus datos y **Evaluar riesgo** (factores explicados, desconocidos,
  posibilidad de venta).

## Trader Explorer
Ranking con puntuación, estado (elegible/excluido/seguido), ROI, PnL, acierto, PF, drawdown,
mantenimiento mediano, calidad de datos (✓ = historial suficiente) y motivos de exclusión. Puedes añadir
wallets manualmente (se sincroniza su historial). En el detalle: curva de PnL estimada, componentes de
la puntuación, operaciones cerradas y actividad on‑chain.

## Copy Trading
* **Trader actual** con el motivo de la selección (IA o determinista) y alternativas; botón para cambiar.
* **Multiplicador**: 0,10x / 0,25x / 0,50x / 1,00x / personalizado. Es una escala de tamaño, no
  apalancamiento: a 1,00x, una compra del trader de `reference_trade_usd` (100 $) equivale a
  `base_order_pct` (10 %) de tu patrimonio; después actúan los límites de riesgo.
* **Reglas de sustitución**: pérdidas consecutivas (2 por defecto, como en la publicación de X), base de
  cálculo (copias propias o cierres del trader), drawdown y pérdida diaria de sesión, inactividad,
  observación mínima, seguimiento mínimo (anti‑churn), enfriamiento, caída de puntuación, cambios
  máximos al día y modo de selección (puntuación / nº1 por PnL / supervisada por IA).
* **¿Fue beneficioso abandonar?**: para cada trader descartado, una cartera sombra sigue copiándolo 7 días
  con las mismas reglas; se compara con tu cartera real desde ese momento.
* **Operaciones replicadas**: precio del trader vs. precio de referencia vs. ejecución, slippage, retraso
  de la señal, latencia y motivo de rechazo.

## AI Agent
Estado, proveedor/modelo, decisiones con su justificación, informes diarios, análisis de la IA y
**propuestas de estrategia** versionadas: *Validar* (walk‑forward con datos registrados, propuesta vs.
actual) → *Aprobar* → *Activar*. Las propuestas que tocan límites críticos se rechazan automáticamente.

## Risk Control
Perfil (CONSERVADOR / EQUILIBRADO / AGRESIVO), límites editables (los críticos piden confirmación y
quedan auditados), estado actual frente a los límites, **parada de emergencia** (bloquea entradas y pausa
el agente; los stop‑loss siguen permitidos) y estado de la ejecución real (bloqueada).

| Límite | CONSERVADOR | EQUILIBRADO | AGRESIVO |
|---|---|---|---|
| Máx. por operación | 5 % | 10 % | 20 % |
| Máx. por token | 8 % | 15 % | 25 % |
| Exposición total | 30 % | 50 % | 80 % |
| Pérdida diaria | 4 % | 8 % | 15 % |
| Drawdown máx. | 12 % | 25 % | 40 % |
| Stop‑loss / Take‑profit | 15 % / 40 % | 25 % / 80 % | 35 % / — |
| Liquidez mínima | 50k $ | 20k $ | 8k $ |
| Riesgo token máx. | 45 | 60 | 75 |
| Edad mínima | 6 h | 1 h | 10 min |

## Backtesting
Elige datos (sintéticos para validar la mecánica; registrados por el agente para evaluar de verdad),
escenario, días, capital, perfil, latencia, sondeo, comisiones, semilla y estrategias A–F; opcionalmente
walk‑forward. Resultados: curvas, rentabilidad neta, drawdown, volatilidad, Sharpe/Sortino (≥ 14 días),
PF, acierto, operaciones, cambios, comisiones, slippage, exposición, % días positivos e IC95 bootstrap
frente a la referencia. Ver [EVALUATION.md](EVALUATION.md).

## Telegram
Configura `TELEGRAM_BOT_TOKEN` y tu chat id en `TELEGRAM_ADMIN_CHAT_IDS`. Recibirás inicio/parada,
selección y cambio de trader, aperturas/cierres, alertas de riesgo y el resumen diario. Comandos:
`/status /balance /trader /positions /history /risk /report /pause /resume /help`. Los chats no
autorizados se ignoran y se auditan; no existe ningún comando para activar operaciones reales.
