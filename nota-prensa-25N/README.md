# Distribución de la nota de prensa del 25N (Apps Script)

Envío individual de la nota de prensa (PDF) y del cartel ganador (JPG) desde
`promocionartistica@escueladeartepedroalmodovar.es`, con registro y protección contra duplicados.

- `Codigo.gs`: código completo.
- `appsscript.json`: manifiesto (zona horaria Europe/Madrid).

## 1. Subir los archivos a Drive (con la cuenta institucional)

1. Entra en drive.google.com con `promocionartistica@…` y crea la carpeta `Nota de prensa 25N 2026`.
2. Sube **sin convertir**:
   - `Nota de prensa 25N 2026 - EASD Pedro Almodovar.pdf`
   - `25N CARTEL - M.M.MERINO-cambios.jpg`
   - `Envio_notas_prensa_Promocion_Artistica.xlsx`
3. Copia el **ID** del PDF y del JPG: clic derecho › Compartir › Copiar enlace. El ID es el texto entre `/d/` y `/view`.
   No hace falta compartirlos con nadie: el script los lee con la cuenta propietaria y adjunta los archivos originales.

## 2. Convertir la lista y crear el script

1. Abre el `.xlsx` y ve a **Archivo › Guardar como hoja de cálculo de Google**. Trabaja siempre en esa copia.
2. En la hoja de Google: **Extensiones › Apps Script**.
3. Sustituye el contenido de `Código.gs` por el de `Codigo.gs`.
4. En **Configuración del proyecto** (rueda dentada), marca «Mostrar el archivo de manifiesto appsscript.json»
   y pega el contenido de `appsscript.json`.
5. En `CONFIG`, pega los dos IDs en `PDF_FILE_ID` y `JPG_FILE_ID`. Si los contactos no están en la
   primera pestaña, escribe su nombre en `SOURCE_SHEET_NAME`.
6. Guarda y recarga la hoja. Aparecerá el menú **Nota de prensa 25N**.
7. La primera vez, Google pedirá permisos para Gmail, Drive y Hojas de cálculo. Al ser un script propio del
   dominio no se conecta ninguna aplicación externa. Si el administrador ha restringido Apps Script, el
   aviso lo indicará.

## 3. Fase 1: prueba

1. **1. Verificar configuración**: comprueba que el remitente es la cuenta institucional, que los adjuntos
   son un PDF y un JPG y que su tamaño cabe en Gmail. También muestra la cuota de envío restante.
2. **2. Preparar lista de destinatarios**: crea la pestaña `Destinatarios`, con direcciones en minúsculas,
   sin duplicados y con las internas del dominio excluidas. Debe dar **72**; si no, avisa.
3. **3. Enviar correo de PRUEBA**: envía un único correo a `ramonpeco@gmail.com` y lo registra en
   `Registro_envios`.
4. Revisa en ramonpeco@gmail.com el remitente, el asunto, el texto y los dos adjuntos (que se abran bien).

## 4. Fase 2: distribución (solo tras aprobar la prueba)

Está bloqueada por código: `enviarDistribucion` no envía nada hasta completar estos pasos.

1. **Fase 2 › 4. Autorizar distribución**: exige que la prueba esté enviada y que se escriba exactamente
   `AUTORIZO EL ENVIO A MEDIOS`.
2. **Fase 2 › 5. Enviar / reanudar distribución**: un mensaje por destinatario (solo en «Para»,
   sin CC ni CCO), con una pausa de 3 s entre envíos. Si se acerca el límite de 6 minutos por
   ejecución, programa la continuación automática al cabo de un minuto.
3. **Pausar distribución** detiene la cola en cualquier momento.

### Protección contra envíos duplicados
- Antes de cada envío la fila se marca como `ENVIANDO` y se guarda.
- Antes de enviar, se busca en **Enviados** un mensaje con el mismo asunto a esa dirección desde la
  autorización. Si existe, se reconcilia y no se reenvía.
- Si una ejecución se corta en pleno envío y el mensaje no aparece en Enviados, la fila pasa a `REVISAR`
  y nunca se reenvía sola. Compruébalo a mano y, si procede, usa «Volver a poner ERROR/REVISAR como PENDIENTE».
- Un bloqueo (`LockService`) impide que dos ejecuciones se solapen.

## 5. Registros

- Pestaña `Destinatarios`: estado actual de cada dirección (fecha, hora, ID de mensaje de Gmail,
  Message-ID RFC 822, ID de hilo, intentos y detalle).
- Pestaña `Registro_envios`: historial de cada intento, sin sobrescribir nada.
- En Gmail › **Enviados** busca `subject:"La Escuela de Arte Pedro Almodóvar inaugura"`.
- En Apps Script › **Ejecuciones** se ven el registro técnico y los errores de cada ejecución.

**Aceptado no es entregado.** `ACEPTADO_POR_GMAIL` solo significa que Gmail admitió el mensaje.
Pasadas unas horas, ejecuta **Comprobar rebotes**: busca avisos de no entrega en el buzón y marca `REBOTE`.
Que no haya rebote tampoco garantiza que el mensaje llegara a la bandeja de entrada (puede ir a spam
o ser filtrado). Un administrador puede consultar la entrega real en la **Consola de administración ›
Informes › Búsqueda de registros de correo electrónico**.

## Límites

- Apps Script en Workspace: 1.500 destinatarios al día (se consulta con `MailApp.getRemainingDailyQuota()`).
  72 envíos están muy por debajo.
- Gmail: 25 MB por mensaje, contando la codificación. El script se niega a enviar si los adjuntos
  superan los 18 MB.
- Máximo de 6 minutos por ejecución: la continuación automática lo gestiona.
