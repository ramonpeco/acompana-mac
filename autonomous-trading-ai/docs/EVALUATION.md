# Evaluación científica de estrategias

Objetivo: comprobar si la selección dinámica de traders supera a las alternativas **después** de
comisiones, latencia y slippage — sin presuponerlo.

## Estrategias

| | Estrategia | Implementación |
|---|---|---|
| A | Seguir siempre al nº1 | nº1 por PnL realizado (sin filtros de calidad), re‑evaluado en cada ranking |
| B | Cambiar tras 2 pérdidas consecutivas | selección por puntuación, `consecutive_losses=2` |
| C | Cambiar tras 3 pérdidas consecutivas | selección por puntuación, `consecutive_losses=3` |
| D | Selección por puntuación ajustada al riesgo | sin regla de pérdidas; cambia si otro supera en ≥ 10 puntos |
| E | Selección supervisada por IA | top‑5 por puntuación + veto; en backtest se usa un **proxy determinista** (veta ROI reciente ≤ 0, calidad < 0,5, concentración ≥ 50 %) para que sea reproducible y no cueste llamadas. En vivo, modo `ai_supervised` |
| F | Referencia sin copy trading | comprar y mantener SOL (o efectivo si no hay serie de SOL) |

Todas comparten las reglas comunes (inactividad, drawdown de sesión, enfriamiento), el motor de riesgo, el
dimensionado y el simulador de ejecución.

## Garantías contra sesgos

* **Sin look‑ahead**: el ranking en *t* usa solo operaciones con `block_time < t` en la ventana de
  `lookback_days`; los precios se toman de la última observación **≤ t** (`PriceSeries.at`), nunca después.
  La liquidez también es la observada en su momento (no la actual). Test: `test_ranking_ignores_future_trades`.
* **Nunca al precio del trader**: la copia se ejecuta en `block_time + U(0, sondeo) + retraso RPC`; si la
  última observación de precio es anterior, se aplica deriva adversa por el hueco; después latencia,
  impacto, comisiones y posible reversión por slippage.
* **Supervivencia**: los traders abandonados siguen en el conjunto de datos; además se informa de su
  rendimiento *después* de abandonarlos (`post_drop`) y en vivo con carteras sombra.
* **Selección retrospectiva**: los filtros y pesos son fijos y versionados (`SCORING_VERSION`); cualquier
  cambio de parámetros pasa por walk‑forward.
* **Sobreoptimización**: walk‑forward elige N (1–4) en cada ventana de entrenamiento con un objetivo que
  penaliza el drawdown (`rent − 0,5·DD`) y evalúa fuera de muestra; se compara con B fijo.

## Métricas

Rentabilidad neta, drawdown máximo, volatilidad diaria, Sharpe y Sortino (anualizados, solo con ≥ 14 días),
profit factor, tasa de acierto, nº de operaciones y de cambios, comisiones, slippage, exposición media,
% de días positivos, y **diferencia diaria media frente a F con IC95 por bootstrap pareado** (2.000
remuestreos): solo se considera significativa si el intervalo no contiene 0.

## Escenarios

* Sintéticos: `favorable` (deriva positiva, pocos rugs, habilidad persistente), `neutral`, `unfavorable`
  (deriva negativa, 30 % de rugs, habilidad baja y poco persistente). Sirven para comprobar la mecánica
  y la robustez de las reglas, no la rentabilidad.
* Estrés sobre datos registrados: repetir con `latency_ms` ×3, `poll_seconds` 60, `dex_fee_bps` ×2.

## Protocolo recomendado (con datos reales)

1. Dejar el agente en `live` + paper al menos **2–4 semanas** (registra operaciones de cientos de wallets
   y precios propios).
2. Backtest "registrados" de los últimos 14–28 días, estrategias A–F + walk‑forward.
3. Comparar con la cartera paper real y con las carteras sombra (¿abandonar tras 2 pérdidas fue mejor?).
4. Solo considerar cambios de reglas que mejoren **fuera de muestra** y con IC95 que excluya 0.
5. Repetir mensualmente: los regímenes de memecoins cambian rápido.

## Resultados de ejemplo (DATOS SINTÉTICOS — solo mecánica)

`python scripts/run_backtest.py --regime unfavorable --days 14 --walk-forward` (semilla 7) produjo, por
ejemplo, A +36 %, B +1,5 %, C +2,9 %, D +2,5 %, F 0 %, con B–E **no significativas** frente a F y un
walk‑forward medio fuera de muestra de −1,5 %. La ventaja de A es un artefacto del mundo sintético (la
"habilidad" de sus traders mira el futuro). Esto ilustra precisamente por qué hay que evaluar con datos
registrados reales antes de sacar conclusiones.
