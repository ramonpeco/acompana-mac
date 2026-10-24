# Ejecución real — preparada, NO activa

El agente **solo** utiliza el motor paper. `app/execution/live.py` no se importa desde el bucle del agente
(`wired_to_agent: false` en `/api/live/status`). Conectarlo requeriría un cambio de código deliberado y
revisado.

## Salvaguardas (todas obligatorias, comprobadas en cada llamada)

1. `LIVE_TRADING_ENABLED=true` en el servidor (no modificable desde el panel ni desde Telegram).
2. Keystore cifrado (scrypt N=2^15 + AES‑256‑GCM) que se descifra con `LIVE_KEYSTORE_PASSPHRASE`.
   La clave privada nunca se guarda en claro ni en la base de datos.
3. `scripts/live_preflight.py` superado en las últimas 24 h: RPC accesible, cotización Jupiter, construcción
   del swap para la wallet del keystore y `simulateTransaction` sin error. **No envía transacciones.**
4. Armado por un administrador: `POST /api/live/arm` re‑introduciendo la contraseña y escribiendo la frase
   `ACEPTO OPERAR CON DINERO REAL`. Caduca a las 12 h. Queda auditado.
5. Orden aprobada por el motor de riesgo determinista y por debajo de `max_live_order_usd` (25 $).
6. Simulación previa de cada transacción firmada antes de `sendTransaction`.

Telegram no tiene ningún comando relacionado con la ejecución real.

## Pasos si algún día se decide probar

1. Resultados paper y backtests con datos registrados que justifiquen el riesgo (ver EVALUATION.md).
2. Wallet dedicada con un importe que puedas perder por completo.
3. `pip install '.[live]'` (instala `solders`), `python scripts/create_keystore.py keypair.json`, borrar el
   `keypair.json` original.
4. `python scripts/live_preflight.py`.
5. Revisión de código para conectar `LiveExecutor` a un modo `live` explícito del agente, con límites
   propios, pruebas de integración en devnet y conciliación de saldos on‑chain.

Mientras tanto, este módulo es una base revisable, no una funcionalidad disponible.
