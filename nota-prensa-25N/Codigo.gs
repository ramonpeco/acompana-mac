/**
 * Distribución de la nota de prensa del 25N.
 * Escuela de Arte y Superior de Diseño Pedro Almodóvar (Ciudad Real)
 *
 * Script vinculado a la hoja de cálculo de contactos (convertida a Google Sheets).
 * Se ejecuta con la cuenta que lo autoriza: debe ser
 * promocionartistica@escueladeartepedroalmodovar.es
 *
 * FASE 1 (activa): enviarPrueba() envía UN correo a CONFIG.TEST_RECIPIENT.
 * FASE 2 (bloqueada): enviarDistribucion() no hace nada hasta que se ejecute
 * autorizarDistribucion() después de la prueba y se escriba la frase de confirmación.
 *
 * Estados en la hoja «Destinatarios»:
 *   PENDIENTE           todavía no enviado
 *   ENVIANDO            se marcó justo antes de enviar; si la ejecución se corta,
 *                       en la siguiente se comprueba en «Enviados» antes de hacer nada
 *   ACEPTADO_POR_GMAIL  Gmail aceptó el mensaje y devolvió un ID. NO es entrega confirmada
 *   REBOTE              se encontró un aviso de no entrega (mailer-daemon) para esa dirección
 *   ERROR               Gmail rechazó el envío (ver Detalle)
 *   REVISAR             se cortó durante el envío y no aparece en «Enviados»; decisión manual
 *   EXCLUIDO            dirección interna del dominio propio u otra exclusión
 */

// ───────────────────────────── CONFIGURACIÓN ─────────────────────────────
const CONFIG = {
  SENDER: 'promocionartistica@escueladeartepedroalmodovar.es',
  SENDER_NAME: 'Promoción Artística · Escuela de Arte Pedro Almodóvar',
  OWN_DOMAIN: 'escueladeartepedroalmodovar.es',

  TEST_RECIPIENT: 'ramonpeco@gmail.com',
  SUBJECT: 'La Escuela de Arte Pedro Almodóvar inaugura la exposición de carteles del 25N',

  // IDs de Drive de los dos archivos originales (ver README, paso 2).
  PDF_FILE_ID: 'PEGAR_AQUI_EL_ID_DEL_PDF',
  JPG_FILE_ID: 'PEGAR_AQUI_EL_ID_DEL_JPG',
  EXPECTED_PDF_NAME: 'Nota de prensa 25N 2026 - EASD Pedro Almodovar.pdf',
  EXPECTED_JPG_NAME: '25N CARTEL - M.M.MERINO-cambios.jpg',

  SOURCE_SHEET_NAME: '',            // vacío = primera pestaña del libro
  RECIPIENTS_SHEET: 'Destinatarios',
  LOG_SHEET: 'Registro_envios',
  EXPECTED_RECIPIENTS: 72,

  PAUSE_MS: 3000,                   // pausa entre envíos
  MAX_RUNTIME_MS: 4.5 * 60 * 1000,  // margen bajo el límite de 6 min por ejecución
  // Gmail admite 25 MB por mensaje, pero los adjuntos crecen ~33 % al codificarse.
  MAX_ATTACH_BYTES: 18 * 1024 * 1024,
  CONFIRM_PHRASE: 'AUTORIZO EL ENVIO A MEDIOS',
};

const COLS = ['Nº', 'Email', 'Contexto (fila de origen)', 'Estado', 'Fecha', 'Hora',
  'ID mensaje Gmail', 'Message-ID (RFC 822)', 'ID hilo', 'Intentos', 'Detalle'];
const C = { N: 1, EMAIL: 2, CTX: 3, ESTADO: 4, FECHA: 5, HORA: 6, ID: 7, RFC: 8, HILO: 9, INTENTOS: 10, DETALLE: 11 };
const PROP = {
  TEST_SENT: 'PRUEBA_ENVIADA_EN',
  AUTH_AT: 'DISTRIBUCION_AUTORIZADA_EN',
  AUTH_BY: 'DISTRIBUCION_AUTORIZADA_POR',
  PAUSED: 'PAUSADO',
};
const EMAIL_RE = /[A-Z0-9._%+'\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}/gi;
const EMAIL_ONE = /[A-Z0-9._%+'\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}/i;

// ─────────────────────────────── MENÚ ───────────────────────────────
function onOpen() {
  SpreadsheetApp.getUi().createMenu('Nota de prensa 25N')
    .addItem('1. Verificar configuración', 'verificarConfiguracion')
    .addItem('2. Preparar lista de destinatarios (sin duplicados)', 'prepararDestinatarios')
    .addItem('3. Enviar correo de PRUEBA', 'enviarPrueba')
    .addSeparator()
    .addSubMenu(SpreadsheetApp.getUi().createMenu('Fase 2: distribución (bloqueada)')
      .addItem('4. Autorizar distribución', 'autorizarDistribucion')
      .addItem('5. Enviar / reanudar distribución', 'enviarDistribucion')
      .addItem('Pausar distribución', 'pausarDistribucion')
      .addItem('Volver a poner ERROR/REVISAR como PENDIENTE', 'reintentarFallidos'))
    .addSeparator()
    .addItem('Comprobar rebotes', 'comprobarRebotes')
    .addItem('Resumen de estados', 'resumen')
    .addToUi();
}

// ───────────────────────────── VERIFICACIÓN ─────────────────────────────
function verificarConfiguracion() {
  const r = preflight_();
  const quota = MailApp.getRemainingDailyQuota();
  const sh = SpreadsheetApp.getActive().getSheetByName(CONFIG.RECIPIENTS_SHEET);
  const n = sh ? Math.max(sh.getLastRow() - 1, 0) : 0;
  SpreadsheetApp.getUi().alert('Verificación correcta',
    'Remitente: ' + r.user + '\n' +
    'PDF: ' + r.pdf.getName() + ' (' + kb_(r.pdf.getBytes().length) + ')\n' +
    'JPG: ' + r.jpg.getName() + ' (' + kb_(r.jpg.getBytes().length) + ')\n' +
    'Tamaño total de adjuntos: ' + kb_(r.total) + '\n' +
    'Cuota de destinatarios restante hoy (Apps Script): ' + quota + '\n' +
    'Filas en «' + CONFIG.RECIPIENTS_SHEET + '»: ' + n + '\n' +
    'Prueba enviada: ' + (prop_(PROP.TEST_SENT) || 'no') + '\n' +
    'Distribución autorizada: ' + (prop_(PROP.AUTH_AT) || 'NO'),
    SpreadsheetApp.getUi().ButtonSet.OK);
}

/** Comprueba remitente y adjuntos. Lanza error si algo no cuadra. */
function preflight_() {
  const user = (Session.getEffectiveUser().getEmail() || '').toLowerCase();
  if (user !== CONFIG.SENDER.toLowerCase()) {
    throw new Error('El script se está ejecutando como «' + user + '». Debe ejecutarse con ' +
      CONFIG.SENDER + '. No se envía nada.');
  }
  const pdf = getAttachment_(CONFIG.PDF_FILE_ID, 'application/pdf', CONFIG.EXPECTED_PDF_NAME);
  const jpg = getAttachment_(CONFIG.JPG_FILE_ID, 'image/jpeg', CONFIG.EXPECTED_JPG_NAME);
  const total = pdf.getBytes().length + jpg.getBytes().length;
  if (total > CONFIG.MAX_ATTACH_BYTES) {
    throw new Error('Los adjuntos suman ' + kb_(total) + ' y superarían el límite de 25 MB de Gmail ' +
      'una vez codificados. Reduce el JPG o usa un enlace de Drive.');
  }
  return { user: user, pdf: pdf, jpg: jpg, total: total };
}

function getAttachment_(id, mime, expectedName) {
  if (!id || id.indexOf('PEGAR_AQUI') === 0) throw new Error('Falta el ID de Drive de «' + expectedName + '» en CONFIG.');
  const file = DriveApp.getFileById(id);
  if (file.getMimeType() !== mime) {
    throw new Error('«' + file.getName() + '» es ' + file.getMimeType() + ', se esperaba ' + mime +
      '. Sube el archivo original sin convertirlo.');
  }
  if (file.getName() !== expectedName) {
    Logger.log('Aviso: el archivo se llama «%s», se esperaba «%s».', file.getName(), expectedName);
  }
  return file.getBlob().setName(file.getName()); // archivo original, sin conversión
}

// ─────────────────────────── LISTA DE DESTINATARIOS ───────────────────────────
function prepararDestinatarios() {
  const ss = SpreadsheetApp.getActive();
  const ui = SpreadsheetApp.getUi();
  const src = CONFIG.SOURCE_SHEET_NAME ? ss.getSheetByName(CONFIG.SOURCE_SHEET_NAME) : ss.getSheets()[0];
  if (!src) throw new Error('No existe la pestaña de origen «' + CONFIG.SOURCE_SHEET_NAME + '».');
  if (src.getName() === CONFIG.RECIPIENTS_SHEET || src.getName() === CONFIG.LOG_SHEET) {
    throw new Error('La primera pestaña debe ser la lista original. Indica SOURCE_SHEET_NAME en CONFIG.');
  }

  let dest = ss.getSheetByName(CONFIG.RECIPIENTS_SHEET);
  if (dest && dest.getLastRow() > 1) {
    const estados = dest.getRange(2, C.ESTADO, dest.getLastRow() - 1, 1).getValues().flat();
    if (estados.some(function (e) { return e && e !== 'PENDIENTE' && e !== 'EXCLUIDO'; })) {
      ui.alert('La lista ya tiene envíos registrados y no se regenera para no perder el control de duplicados.');
      return;
    }
  }

  const values = src.getDataRange().getDisplayValues();
  const seen = {};
  const rows = [];
  let found = 0, dupes = 0, internal = 0;
  values.forEach(function (row, i) {
    const text = row.join(' ');
    const emails = text.match(EMAIL_RE) || [];
    const ctx = row.filter(function (c) { return c && !EMAIL_ONE.test(c); }).join(' | ').slice(0, 150);
    emails.forEach(function (raw) {
      found++;
      const email = raw.trim().toLowerCase().replace(/^[.'\-]+|[.'\-]+$/g, '');
      if (seen[email]) { dupes++; seen[email].push(i + 1); return; }
      seen[email] = [i + 1];
      const isInternal = email.split('@')[1] === CONFIG.OWN_DOMAIN;
      if (isInternal) internal++;
      rows.push([rows.length + 1, email, 'Fila ' + (i + 1) + (ctx ? ': ' + ctx : ''),
        isInternal ? 'EXCLUIDO' : 'PENDIENTE', '', '', '', '', '', 0,
        isInternal ? 'Dirección interna del dominio propio' : '']);
    });
  });
  rows.forEach(function (r) {
    const filas = seen[r[1]];
    if (filas.length > 1) r[C.DETALLE - 1] = (r[C.DETALLE - 1] ? r[C.DETALLE - 1] + '. ' : '') +
      'Duplicado en filas ' + filas.join(', ') + ' (se envía una sola vez)';
  });

  if (!dest) dest = ss.insertSheet(CONFIG.RECIPIENTS_SHEET);
  dest.clear();
  dest.getRange(1, 1, 1, COLS.length).setValues([COLS]).setFontWeight('bold');
  if (rows.length) dest.getRange(2, 1, rows.length, COLS.length).setValues(rows);
  dest.setFrozenRows(1);
  dest.autoResizeColumns(1, COLS.length);

  const externos = rows.length - internal;
  ui.alert('Lista preparada',
    'Direcciones encontradas: ' + found + '\nDuplicadas eliminadas: ' + dupes +
    '\nInternas excluidas: ' + internal + '\nDestinatarios externos únicos: ' + externos +
    (externos !== CONFIG.EXPECTED_RECIPIENTS ? '\n\n⚠ Se esperaban ' + CONFIG.EXPECTED_RECIPIENTS +
      '. Revisa la pestaña «' + CONFIG.RECIPIENTS_SHEET + '» antes de continuar.' : ''),
    ui.ButtonSet.OK);
}

// ─────────────────────────────── FASE 1: PRUEBA ───────────────────────────────
function enviarPrueba() {
  const ui = SpreadsheetApp.getUi();
  const r = preflight_();
  const ok = ui.alert('Enviar correo de prueba',
    'Se enviará UN mensaje a ' + CONFIG.TEST_RECIPIENT + ' desde ' + r.user +
    ' con los dos adjuntos.\nNo se enviará nada a los medios.\n\n¿Continuar?', ui.ButtonSet.YES_NO);
  if (ok !== ui.Button.YES) return;

  try {
    const res = sendOne_(CONFIG.TEST_RECIPIENT, [r.pdf, r.jpg]);
    log_('PRUEBA', CONFIG.TEST_RECIPIENT, res, 'ACEPTADO_POR_GMAIL', 'Correo de prueba');
    setProp_(PROP.TEST_SENT, new Date().toISOString());
    ui.alert('Gmail ha aceptado el correo de prueba.\nID: ' + res.id +
      '\n\nRevisa en ' + CONFIG.TEST_RECIPIENT + ' texto, asunto, remitente y que llegan los dos adjuntos.');
  } catch (e) {
    log_('PRUEBA', CONFIG.TEST_RECIPIENT, null, 'ERROR', String(e));
    throw e;
  }
}

// ─────────────────────────── FASE 2: DISTRIBUCIÓN ───────────────────────────
function autorizarDistribucion() {
  const ui = SpreadsheetApp.getUi();
  if (!prop_(PROP.TEST_SENT)) { ui.alert('Primero hay que enviar y revisar el correo de prueba.'); return; }
  const sh = SpreadsheetApp.getActive().getSheetByName(CONFIG.RECIPIENTS_SHEET);
  if (!sh || sh.getLastRow() < 2) { ui.alert('Primero prepara la lista de destinatarios.'); return; }
  const pendientes = sh.getRange(2, C.ESTADO, sh.getLastRow() - 1, 1).getValues().flat()
    .filter(function (e) { return e === 'PENDIENTE'; }).length;
  const resp = ui.prompt('Autorizar distribución',
    'Se enviarán ' + pendientes + ' mensajes individuales (sin CC ni CCO) desde ' + CONFIG.SENDER +
    '.\nPara autorizarlo, escribe exactamente:\n' + CONFIG.CONFIRM_PHRASE, ui.ButtonSet.OK_CANCEL);
  if (resp.getSelectedButton() !== ui.Button.OK || resp.getResponseText().trim() !== CONFIG.CONFIRM_PHRASE) {
    ui.alert('No autorizado. No se ha enviado nada.');
    return;
  }
  setProp_(PROP.AUTH_AT, new Date().toISOString());
  setProp_(PROP.AUTH_BY, Session.getEffectiveUser().getEmail());
  PropertiesService.getScriptProperties().deleteProperty(PROP.PAUSED);
  ui.alert('Distribución autorizada. Ahora usa «5. Enviar / reanudar distribución».');
}

function enviarDistribucion() {
  const authAt = prop_(PROP.AUTH_AT);
  if (!authAt || !prop_(PROP.TEST_SENT)) {
    throw new Error('La distribución no está autorizada. No se envía nada.');
  }
  deleteContinuationTriggers_();
  if (prop_(PROP.PAUSED)) { Logger.log('Distribución en pausa.'); return; }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) { Logger.log('Otra ejecución está en curso.'); return; }
  const start = Date.now();
  try {
    const r = preflight_();
    const attachments = [r.pdf, r.jpg];
    const sh = SpreadsheetApp.getActive().getSheetByName(CONFIG.RECIPIENTS_SHEET);
    const last = sh.getLastRow();
    if (last < 2) throw new Error('No hay destinatarios.');
    const data = sh.getRange(2, 1, last - 1, COLS.length).getValues();
    let quota = MailApp.getRemainingDailyQuota();
    let enviados = 0, cortado = false;

    for (let i = 0; i < data.length; i++) {
      const rowNum = i + 2;
      const email = String(data[i][C.EMAIL - 1]).trim().toLowerCase();
      const estado = data[i][C.ESTADO - 1];
      if (estado !== 'PENDIENTE' && estado !== 'ENVIANDO') continue;

      if (Date.now() - start > CONFIG.MAX_RUNTIME_MS) { cortado = true; break; }
      if (prop_(PROP.PAUSED)) { Logger.log('Pausa solicitada.'); return; }
      if (quota < 1) { Logger.log('Cuota diaria agotada; se reanudará mañana.'); break; }

      // Protección contra duplicados: si ya consta en «Enviados» desde la autorización, no se reenvía.
      const prev = findSent_(email, authAt);
      if (prev) {
        writeRow_(sh, rowNum, 'ACEPTADO_POR_GMAIL', prev, data[i][C.INTENTOS - 1],
          'Reconciliado: ya figuraba en Enviados; no se reenvía');
        log_('DISTRIBUCION', email, prev, 'ACEPTADO_POR_GMAIL', 'Reconciliado desde Enviados');
        continue;
      }
      if (estado === 'ENVIANDO') {
        // Se cortó en pleno envío y no aparece en Enviados: no se reenvía automáticamente.
        writeRow_(sh, rowNum, 'REVISAR', null, data[i][C.INTENTOS - 1],
          'Ejecución interrumpida durante el envío y no aparece en Enviados. Comprobar a mano.');
        log_('DISTRIBUCION', email, null, 'REVISAR', 'Interrumpido durante el envío');
        continue;
      }

      const intentos = Number(data[i][C.INTENTOS - 1] || 0) + 1;
      sh.getRange(rowNum, C.ESTADO).setValue('ENVIANDO');
      sh.getRange(rowNum, C.INTENTOS).setValue(intentos);
      SpreadsheetApp.flush(); // marca persistida ANTES de enviar

      try {
        const res = sendOne_(email, attachments);
        writeRow_(sh, rowNum, 'ACEPTADO_POR_GMAIL', res, intentos, 'Aceptado por Gmail (no implica entrega)');
        log_('DISTRIBUCION', email, res, 'ACEPTADO_POR_GMAIL', '');
        enviados++; quota--;
      } catch (e) {
        writeRow_(sh, rowNum, 'ERROR', null, intentos, String(e));
        log_('DISTRIBUCION', email, null, 'ERROR', String(e));
        if (/limit|too many times|quota|cuota|límite/i.test(String(e))) {
          Logger.log('Límite de Gmail alcanzado: se detiene la ejecución.');
          break;
        }
      }
      SpreadsheetApp.flush();
      Utilities.sleep(CONFIG.PAUSE_MS);
    }

    Logger.log('Enviados en esta ejecución: %s', enviados);
    if (cortado) {
      ScriptApp.newTrigger('enviarDistribucion').timeBased().after(60 * 1000).create();
      Logger.log('Tiempo agotado: continuará automáticamente en ~1 minuto.');
    }
  } finally {
    lock.releaseLock();
  }
}

function pausarDistribucion() {
  setProp_(PROP.PAUSED, new Date().toISOString());
  deleteContinuationTriggers_();
  SpreadsheetApp.getUi().alert('Distribución en pausa. Para continuar, vuelve a autorizarla y ejecuta el paso 5.');
}

function reintentarFallidos() {
  const ui = SpreadsheetApp.getUi();
  const sh = SpreadsheetApp.getActive().getSheetByName(CONFIG.RECIPIENTS_SHEET);
  if (!sh || sh.getLastRow() < 2) return;
  const rng = sh.getRange(2, C.ESTADO, sh.getLastRow() - 1, 1);
  const vals = rng.getValues();
  const n = vals.filter(function (v) { return v[0] === 'ERROR' || v[0] === 'REVISAR'; }).length;
  if (!n) { ui.alert('No hay filas en ERROR ni REVISAR.'); return; }
  if (ui.alert('Se marcarán ' + n + ' filas como PENDIENTE. Antes de reenviar se comprobará igualmente ' +
    'la carpeta Enviados. ¿Continuar?', ui.ButtonSet.YES_NO) !== ui.Button.YES) return;
  rng.setValues(vals.map(function (v) { return [(v[0] === 'ERROR' || v[0] === 'REVISAR') ? 'PENDIENTE' : v[0]]; }));
}

// ─────────────────────────────── REBOTES ───────────────────────────────
/** Busca avisos de no entrega en el buzón y marca REBOTE. La ausencia de rebote no prueba la entrega. */
function comprobarRebotes() {
  const since = prop_(PROP.AUTH_AT) || prop_(PROP.TEST_SENT);
  if (!since) { SpreadsheetApp.getUi().alert('Aún no se ha enviado nada.'); return; }
  const sinceDate = new Date(since);
  const q = '(from:mailer-daemon OR from:postmaster OR subject:"Delivery Status Notification" OR ' +
    'subject:"Undeliverable" OR subject:"No se ha podido entregar") after:' +
    Utilities.formatDate(new Date(sinceDate.getTime() - 86400000), 'UTC', 'yyyy/MM/dd');
  const threads = GmailApp.search(q, 0, 200);
  const texts = [];
  threads.forEach(function (t) {
    t.getMessages().forEach(function (m) {
      if (m.getDate() >= sinceDate) texts.push((m.getPlainBody() || '').toLowerCase());
    });
  });

  const sh = SpreadsheetApp.getActive().getSheetByName(CONFIG.RECIPIENTS_SHEET);
  let marcados = 0;
  if (sh && sh.getLastRow() > 1) {
    const data = sh.getRange(2, 1, sh.getLastRow() - 1, COLS.length).getValues();
    data.forEach(function (row, i) {
      const email = String(row[C.EMAIL - 1]).toLowerCase();
      if (row[C.ESTADO - 1] !== 'ACEPTADO_POR_GMAIL') return;
      if (texts.some(function (t) { return t.indexOf(email) !== -1; })) {
        sh.getRange(i + 2, C.ESTADO).setValue('REBOTE');
        sh.getRange(i + 2, C.DETALLE).setValue('Aviso de no entrega recibido en el buzón del remitente');
        log_('REBOTE', email, null, 'REBOTE', 'Detectado aviso de no entrega');
        marcados++;
      }
    });
  }
  SpreadsheetApp.getUi().alert('Avisos de no entrega revisados: ' + texts.length +
    '\nDestinatarios marcados como REBOTE: ' + marcados +
    '\n\nRecuerda: «ACEPTADO_POR_GMAIL» sin rebote no significa entrega confirmada.');
}

function resumen() {
  const sh = SpreadsheetApp.getActive().getSheetByName(CONFIG.RECIPIENTS_SHEET);
  if (!sh || sh.getLastRow() < 2) { SpreadsheetApp.getUi().alert('No hay lista preparada.'); return; }
  const counts = {};
  sh.getRange(2, C.ESTADO, sh.getLastRow() - 1, 1).getValues().flat()
    .forEach(function (e) { counts[e] = (counts[e] || 0) + 1; });
  SpreadsheetApp.getUi().alert('Resumen', Object.keys(counts).map(function (k) { return k + ': ' + counts[k]; })
    .join('\n') + '\n\nCuota restante hoy: ' + MailApp.getRemainingDailyQuota(), SpreadsheetApp.getUi().ButtonSet.OK);
}

// ─────────────────────────────── ENVÍO ───────────────────────────────
/** Envía un mensaje individual (un único destinatario en «Para», sin CC ni CCO). */
function sendOne_(to, attachments) {
  if (String(to).indexOf(',') !== -1 || String(to).indexOf(';') !== -1) {
    throw new Error('Destinatario no válido (más de una dirección): ' + to);
  }
  const body = buildBody_();
  const draft = GmailApp.createDraft(to, CONFIG.SUBJECT, body.text, {
    htmlBody: body.html,
    attachments: attachments,
    name: CONFIG.SENDER_NAME,
  });
  const msg = draft.send(); // devuelve el GmailMessage enviado → permite registrar su ID
  let rfc = '';
  try { rfc = msg.getHeader('Message-ID'); } catch (e) { /* no disponible */ }
  return { id: msg.getId(), threadId: msg.getThread().getId(), rfc: rfc, date: msg.getDate() };
}

/** Busca en «Enviados» un mensaje con este asunto a esta dirección desde la fecha indicada. */
function findSent_(email, sinceIso) {
  const since = new Date(sinceIso);
  const q = 'in:sent to:' + email + ' subject:"' + CONFIG.SUBJECT.replace(/"/g, '') + '" after:' +
    Utilities.formatDate(new Date(since.getTime() - 86400000), 'UTC', 'yyyy/MM/dd');
  const threads = GmailApp.search(q, 0, 10);
  for (let t = 0; t < threads.length; t++) {
    const msgs = threads[t].getMessages();
    for (let m = 0; m < msgs.length; m++) {
      const msg = msgs[m];
      if (msg.getDate() >= since && msg.getTo().toLowerCase().indexOf(email) !== -1 &&
          msg.getSubject() === CONFIG.SUBJECT) {
        let rfc = '';
        try { rfc = msg.getHeader('Message-ID'); } catch (e) { /* */ }
        return { id: msg.getId(), threadId: threads[t].getId(), rfc: rfc, date: msg.getDate() };
      }
    }
  }
  return null;
}

function buildBody_() {
  const text = [
    'Estimados/as profesionales de los medios de comunicación:',
    '',
    'La Escuela de Arte y Superior de Diseño Pedro Almodóvar, en colaboración con el Ayuntamiento de Ciudad Real, ' +
    'inaugura la exposición del concurso anual de carteles con motivo del 25N, Día Internacional para la ' +
    'Eliminación de la Violencia contra las Mujeres. En el mismo acto se entregarán los premios del concurso.',
    '',
    'CONVOCATORIA',
    'Fecha: jueves, 19 de noviembre de 2026',
    'Hora: 11:00 horas',
    'Lugar: sala de exposiciones de la Escuela de Arte y Superior de Diseño Pedro Almodóvar, Ciudad Real',
    '',
    'El cartel ganador es obra de la alumna María Magdalena Merino Arévalo, bajo el lema «Los tiempos cambian, ' +
    '¿y tú?». Realizado con la técnica del collage, representa un patrón persistente con las palabras «amor, ' +
    'respeto, igualdad, sin excusas, sin miedo».',
    '',
    'La directora del centro, Antonia Badillo, presentará la muestra, y la concejala de Igualdad del Ayuntamiento ' +
    'de Ciudad Real, María José Escobedo, participará en la entrega de galardones. Asistirán la autora del cartel ' +
    'ganador y sus compañeros de estudios.',
    '',
    'Adjuntamos la nota de prensa completa (PDF) y la imagen del cartel ganador (JPG). En su difusión debe citarse ' +
    'la autoría: María Magdalena Merino Arévalo.',
    '',
    'Quedamos a su disposición para cualquier información adicional.',
    '',
    'Un cordial saludo,',
    '',
    'Promoción Artística',
    'Escuela de Arte y Superior de Diseño Pedro Almodóvar',
    'Ciudad Real',
    CONFIG.SENDER,
  ].join('\n');

  const p = 'style="margin:0 0 14px 0"';
  const html =
    '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#222;max-width:640px">' +
    '<p ' + p + '>Estimados/as profesionales de los medios de comunicación:</p>' +
    '<p ' + p + '>La <b>Escuela de Arte y Superior de Diseño Pedro Almodóvar</b>, en colaboración con el ' +
    'Ayuntamiento de Ciudad Real, inaugura la exposición del concurso anual de carteles con motivo del <b>25N</b>, ' +
    'Día Internacional para la Eliminación de la Violencia contra las Mujeres. En el mismo acto se entregarán ' +
    'los premios del concurso.</p>' +
    '<table role="presentation" style="border-left:3px solid #7b2d8e;padding:4px 0 4px 12px;margin:0 0 14px 0">' +
    '<tr><td><b>CONVOCATORIA</b><br>' +
    '<b>Fecha:</b> jueves, 19 de noviembre de 2026<br>' +
    '<b>Hora:</b> 11:00 horas<br>' +
    '<b>Lugar:</b> sala de exposiciones de la Escuela de Arte y Superior de Diseño Pedro Almodóvar, Ciudad Real' +
    '</td></tr></table>' +
    '<p ' + p + '>El cartel ganador es obra de la alumna <b>María Magdalena Merino Arévalo</b>, bajo el lema ' +
    '«Los tiempos cambian, ¿y tú?». Realizado con la técnica del collage, representa un patrón persistente con ' +
    'las palabras «amor, respeto, igualdad, sin excusas, sin miedo».</p>' +
    '<p ' + p + '>La directora del centro, Antonia Badillo, presentará la muestra, y la concejala de Igualdad del ' +
    'Ayuntamiento de Ciudad Real, María José Escobedo, participará en la entrega de galardones. Asistirán la ' +
    'autora del cartel ganador y sus compañeros de estudios.</p>' +
    '<p ' + p + '>Adjuntamos la nota de prensa completa (PDF) y la imagen del cartel ganador (JPG). ' +
    '<b>En su difusión debe citarse la autoría: María Magdalena Merino Arévalo.</b></p>' +
    '<p ' + p + '>Quedamos a su disposición para cualquier información adicional.</p>' +
    '<p ' + p + '>Un cordial saludo,</p>' +
    '<p style="margin:0">Promoción Artística<br>Escuela de Arte y Superior de Diseño Pedro Almodóvar<br>' +
    'Ciudad Real<br><a href="mailto:' + CONFIG.SENDER + '">' + CONFIG.SENDER + '</a></p></div>';

  return { text: text, html: html };
}

// ─────────────────────────────── REGISTRO ───────────────────────────────
function writeRow_(sh, rowNum, estado, res, intentos, detalle) {
  const d = res && res.date ? res.date : new Date();
  sh.getRange(rowNum, C.ESTADO, 1, 8).setValues([[
    estado, fmt_(d, 'dd/MM/yyyy'), fmt_(d, 'HH:mm:ss'),
    res ? res.id : '', res ? res.rfc : '', res ? res.threadId : '', intentos || '', detalle || '',
  ]]);
}

function log_(tipo, email, res, resultado, detalle) {
  const ss = SpreadsheetApp.getActive();
  let sh = ss.getSheetByName(CONFIG.LOG_SHEET);
  if (!sh) {
    sh = ss.insertSheet(CONFIG.LOG_SHEET);
    sh.appendRow(['Fecha', 'Hora', 'Tipo', 'Destinatario', 'Resultado', 'ID mensaje Gmail',
      'Message-ID (RFC 822)', 'ID hilo', 'Remitente', 'Detalle']);
    sh.getRange(1, 1, 1, 10).setFontWeight('bold');
    sh.setFrozenRows(1);
  }
  const now = new Date();
  sh.appendRow([fmt_(now, 'dd/MM/yyyy'), fmt_(now, 'HH:mm:ss'), tipo, email, resultado,
    res ? res.id : '', res ? res.rfc : '', res ? res.threadId : '',
    Session.getEffectiveUser().getEmail(), detalle || '']);
}

// ─────────────────────────────── UTILIDADES ───────────────────────────────
function deleteContinuationTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'enviarDistribucion') ScriptApp.deleteTrigger(t);
  });
}
function prop_(k) { return PropertiesService.getScriptProperties().getProperty(k); }
function setProp_(k, v) { PropertiesService.getScriptProperties().setProperty(k, v); }
function fmt_(d, f) { return Utilities.formatDate(d, Session.getScriptTimeZone(), f); }
function kb_(n) { return (n / 1024 / 1024).toFixed(2) + ' MB'; }
