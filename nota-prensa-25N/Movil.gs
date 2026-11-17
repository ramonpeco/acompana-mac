/**
 * Nota de prensa 25N · versión independiente (sin hoja de cálculo), pensada para el móvil.
 * Ejecutar con la cuenta promocionartistica@escueladeartepedroalmodovar.es
 *
 * Uso: elige la función en el desplegable de arriba y pulsa «Ejecutar».
 *   paso1_verificar     comprueba cuenta, adjuntos y lista. NO envía nada.
 *   paso2_enviarPrueba  envía UN correo a ramonpeco@gmail.com.
 *   paso3_enviarMedios  envía a los medios. Bloqueado salvo que AUTORIZACION tenga la frase exacta.
 *   comprobarRebotes    marca los avisos de no entrega.
 *   pausar              detiene la distribución.
 * Los resultados se ven en «Registro de ejecución» y en la hoja «Registro envíos nota de prensa 25N» de Drive.
 */

// Para la FASE 2, y solo tras revisar la prueba, cambia 'NO' por 'AUTORIZO EL ENVIO A MEDIOS'.
const AUTORIZACION = 'NO';
const INCLUIR_INTERNOS = false; // pedrolc@escueladeartepedroalmodovar.es es interno: no se envía si es false

const REMITENTE = 'promocionartistica@escueladeartepedroalmodovar.es';
const NOMBRE_REMITENTE = 'Promoción Artística · Escuela de Arte Pedro Almodóvar';
const DOMINIO = 'escueladeartepedroalmodovar.es';
const PRUEBA = 'ramonpeco@gmail.com';
const ASUNTO = 'La Escuela de Arte Pedro Almodóvar inaugura la exposición de carteles del 25N';
const BUSCAR_PDF = "title contains 'Nota de prensa 25N' and mimeType = 'application/pdf' and trashed = false";
const BUSCAR_JPG = "title contains '25N CARTEL' and mimeType = 'image/jpeg' and trashed = false";
const NOMBRE_PDF = 'Nota de prensa 25N 2026 - EASD Pedro Almodovar.pdf';
const NOMBRE_JPG = '25N CARTEL - M.M.MERINO-cambios.jpg';
const FRASE = 'AUTORIZO EL ENVIO A MEDIOS';
const PAUSA_MS = 3000, MAX_MS = 4.5 * 60 * 1000, MAX_ADJ = 18 * 1024 * 1024;

const CONTACTOS = [
  'aguerosraquel@yahoo.es', 'albacete@ondacero.es', 'alberto@complejoestoril.com',
  'alcazar@cadenaser.com', 'alcazar@ondacero.es', 'alcazartelevision@yahoo.es',
  'alfonso.mora@rtvcm.es', 'almaden.cr@cadenaser.com', 'amparo.aguilar@rtvcm.es',
  'amparo.tve@gmail.com', 'ana.mondejar@rtvcm.es', 'anibalbc@telefonica.net',
  'anulix83@gmail.com', 'anulix@gmail.com', 'belen.lario@rtvcm.es',
  'brodriguez@lanzadigital.com', 'canal2000@canal2000.com', 'canal4@villarrobledo.com',
  'castiblanqueana@hotmail.com', 'castillalamancha@europapress.es', 'cesardelrio@encastillalamancha.es',
  'ciudad_86@hotmail.com', 'ciudadreal@cadenaser.com', 'ciudadreal@ondacero.es',
  'clamancha@europapress.es', 'cmt.region@rtvcm.es', 'cnc.to@grupo-eldia.net',
  'comarcatalavera@diariolatribuna.com', 'comunicacion@rtvcm.es', 'consolacion.romero@ondacero.es',
  'criptanatv@campodecriptana.es', 'cristina.bravo@rtve.es', 'cuenca@ondacero.es',
  'daimieltv@daimieltv.com', 'damiantoledo14@hotmail.com', 'danielmiguelanez@yahoo.es',
  'david.revenga@rtvcm.es', 'diariodehellin@hotmail.com', 'direccion@televal.com',
  'direccion@televisionhellin.com', 'dolores.rios@rtvcm.es', 'dominv@ya.com',
  'efecuenca@hotmail.com', 'elche@dipualba.es', 'eldigital@eldigitalcastillalamancha.es',
  'encastillalamancha@encastillalamancha.es', 'esoubriet@interbook.net', 'esther.bogonez@rtvcm.es',
  'f.madinabeitia@rtvcm.es', 'felixgarcia@ondacero.es', 'fernando@diariolatribuna.com',
  'gemmagss@gmail.com', 'gracigalan@lanzadigital.com', 'informativos.tv@cmmedia.es',
  'josemizquierdo@lanzadigital.com', 'lanzapt@lanzadigital.com', 'lanzaval@lanzadigital.com',
  'latribunacr@gmail.com', 'lespinar@lanzadigital.com', 'mgarcialanza@gmail.com',
  'pedrolc@escueladeartepedroalmodovar.es', 'provincia@lanzadigital.com', 'provinciacr@diariolatribuna.com',
  'puertollano@cadenaser.com', 'redaccion@diariolatribuna.com', 'redaccion@lanzadigital.com',
  'redaccion_pto@diariolatribuna.com', 'redaccion_tal@diariolatribuna.com', 'redaccioncr@diariolatribuna.com',
  'redacciontoledo@diariolatribuna.com', 'region@diariolatribuna.com', 'rgratacos@lanzadigital.com'
];

// ───────────── PASOS ─────────────
function paso1_verificar() {
  const a = comprobar_();
  const lista = destinatarios_();
  const n = contar_(lista);
  Logger.log('OK. Cuenta: %s', a.usuario);
  Logger.log('PDF: %s (%s)', a.pdfNombre, mb_(a.pdf.getBytes().length));
  Logger.log('JPG: %s (%s)', a.jpgNombre, mb_(a.jpg.getBytes().length));
  Logger.log('Contactos: %s únicos. Estados: %s', CONTACTOS.length, JSON.stringify(n));
  Logger.log('Cuota de envío restante hoy: %s', MailApp.getRemainingDailyQuota());
  Logger.log('Registro: %s', libro_().getUrl());
  Logger.log('No se ha enviado nada.');
}

function paso2_enviarPrueba() {
  const a = comprobar_();
  try {
    const r = enviar_(PRUEBA, [a.pdf, a.jpg]);
    registrar_('PRUEBA', PRUEBA, r, 'ACEPTADO_POR_GMAIL', 'Correo de prueba');
    prop_('PRUEBA_ENVIADA', new Date().toISOString());
    Logger.log('Gmail ha aceptado la prueba a %s. ID: %s. Revisa el correo y los dos adjuntos.', PRUEBA, r.id);
  } catch (e) {
    registrar_('PRUEBA', PRUEBA, null, 'ERROR', String(e));
    throw e;
  }
}

function paso3_enviarMedios() {
  if (AUTORIZACION !== FRASE) throw new Error('Distribución NO autorizada (AUTORIZACION no contiene la frase). No se envía nada.');
  if (!prop_('PRUEBA_ENVIADA')) throw new Error('Primero hay que enviar y revisar la prueba (paso2). No se envía nada.');
  if (!prop_('AUTORIZADO_EN')) prop_('AUTORIZADO_EN', new Date().toISOString());
  borrarActivadores_();
  if (prop_('PAUSADO')) { Logger.log('En pausa. Borra la pausa con reanudar() para seguir.'); return; }
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) { Logger.log('Ya hay otra ejecución en curso.'); return; }
  const inicio = Date.now(), desde = prop_('AUTORIZADO_EN');
  try {
    const a = comprobar_();
    const sh = destinatarios_();
    const datos = sh.getRange(2, 1, sh.getLastRow() - 1, 9).getValues();
    let cuota = MailApp.getRemainingDailyQuota(), enviados = 0, cortado = false;
    for (let i = 0; i < datos.length; i++) {
      const fila = i + 2, email = datos[i][0], estado = datos[i][1];
      if (estado !== 'PENDIENTE' && estado !== 'ENVIANDO') continue;
      if (Date.now() - inicio > MAX_MS) { cortado = true; break; }
      if (prop_('PAUSADO')) { Logger.log('Pausa solicitada.'); return; }
      if (cuota < 1) { Logger.log('Cuota diaria agotada.'); break; }
      const previo = buscarEnviado_(email, desde);
      if (previo) { fila_(sh, fila, 'ACEPTADO_POR_GMAIL', previo, datos[i][7], 'Ya estaba en Enviados: no se reenvía');
        registrar_('MEDIOS', email, previo, 'ACEPTADO_POR_GMAIL', 'Reconciliado'); continue; }
      if (estado === 'ENVIANDO') { fila_(sh, fila, 'REVISAR', null, datos[i][7], 'Interrumpido y no aparece en Enviados: comprobar a mano');
        registrar_('MEDIOS', email, null, 'REVISAR', 'Interrumpido'); continue; }
      const intentos = Number(datos[i][7] || 0) + 1;
      sh.getRange(fila, 2).setValue('ENVIANDO'); sh.getRange(fila, 8).setValue(intentos); SpreadsheetApp.flush();
      try {
        const r = enviar_(email, [a.pdf, a.jpg]);
        fila_(sh, fila, 'ACEPTADO_POR_GMAIL', r, intentos, 'Aceptado por Gmail (no implica entrega)');
        registrar_('MEDIOS', email, r, 'ACEPTADO_POR_GMAIL', ''); enviados++; cuota--;
      } catch (e) {
        fila_(sh, fila, 'ERROR', null, intentos, String(e)); registrar_('MEDIOS', email, null, 'ERROR', String(e));
        if (/limit|too many times|quota|cuota|límite/i.test(String(e))) { Logger.log('Límite de Gmail: se detiene.'); break; }
      }
      SpreadsheetApp.flush(); Utilities.sleep(PAUSA_MS);
    }
    Logger.log('Enviados en esta ejecución: %s. Estados: %s', enviados, JSON.stringify(contar_(sh)));
    if (cortado) { ScriptApp.newTrigger('paso3_enviarMedios').timeBased().after(60000).create();
      Logger.log('Continuará automáticamente en 1 minuto.'); }
  } finally { lock.releaseLock(); }
}

function pausar() { prop_('PAUSADO', new Date().toISOString()); borrarActivadores_(); Logger.log('Distribución en pausa.'); }
function reanudar() { PropertiesService.getScriptProperties().deleteProperty('PAUSADO'); Logger.log('Pausa retirada.'); }

function comprobarRebotes() {
  const desde = new Date(prop_('AUTORIZADO_EN') || prop_('PRUEBA_ENVIADA') || Date.now());
  const q = '(from:mailer-daemon OR from:postmaster OR subject:"Delivery Status Notification" OR subject:Undeliverable) after:' +
    Utilities.formatDate(new Date(desde.getTime() - 86400000), 'UTC', 'yyyy/MM/dd');
  const textos = [];
  GmailApp.search(q, 0, 200).forEach(function (t) { t.getMessages().forEach(function (m) {
    if (m.getDate() >= desde) textos.push((m.getPlainBody() || '').toLowerCase()); }); });
  const sh = destinatarios_(); let n = 0;
  sh.getRange(2, 1, sh.getLastRow() - 1, 2).getValues().forEach(function (r, i) {
    if (r[1] === 'ACEPTADO_POR_GMAIL' && textos.some(function (t) { return t.indexOf(r[0]) !== -1; })) {
      sh.getRange(i + 2, 2).setValue('REBOTE'); sh.getRange(i + 2, 9).setValue('Aviso de no entrega recibido');
      registrar_('REBOTE', r[0], null, 'REBOTE', ''); n++; } });
  Logger.log('Avisos revisados: %s. Marcados como REBOTE: %s. Sin rebote no significa entrega confirmada.', textos.length, n);
}

// ───────────── INTERNAS ─────────────
function comprobar_() {
  const usuario = (Session.getEffectiveUser().getEmail() || '').toLowerCase();
  if (usuario !== REMITENTE) throw new Error('Estás en la cuenta «' + usuario + '». Debe ser ' + REMITENTE + '. No se envía nada.');
  const pdf = archivo_(BUSCAR_PDF, 'PDF de la nota de prensa'), jpg = archivo_(BUSCAR_JPG, 'JPG del cartel');
  const total = pdf.getSize() + jpg.getSize();
  if (total > MAX_ADJ) throw new Error('Los adjuntos suman ' + mb_(total) + ': demasiado para Gmail.');
  return { usuario: usuario, pdfNombre: pdf.getName(), jpgNombre: jpg.getName(),
    pdf: pdf.getBlob().setName(NOMBRE_PDF), jpg: jpg.getBlob().setName(NOMBRE_JPG) };
}

function archivo_(q, que) {
  const it = DriveApp.searchFiles(q), f = [];
  while (it.hasNext()) f.push(it.next());
  if (!f.length) throw new Error('No encuentro en Drive el ' + que + '. Súbelo con su nombre original.');
  if (f.length > 1) throw new Error('Hay ' + f.length + ' archivos posibles para el ' + que + ': ' +
    f.map(function (x) { return x.getName(); }).join(' | ') + '. Deja solo uno (envía los demás a la papelera).');
  return f[0];
}

function enviar_(para, adjuntos) {
  if (/[,;]/.test(para)) throw new Error('Destinatario no válido: ' + para);
  const c = cuerpo_();
  const msg = GmailApp.createDraft(para, ASUNTO, c.texto, { htmlBody: c.html, attachments: adjuntos, name: NOMBRE_REMITENTE }).send();
  let rfc = ''; try { rfc = msg.getHeader('Message-ID'); } catch (e) {}
  return { id: msg.getId(), hilo: msg.getThread().getId(), rfc: rfc, fecha: msg.getDate() };
}

function buscarEnviado_(email, desdeIso) {
  const desde = new Date(desdeIso);
  const q = 'in:sent to:' + email + ' subject:"' + ASUNTO + '" after:' + Utilities.formatDate(new Date(desde.getTime() - 86400000), 'UTC', 'yyyy/MM/dd');
  const hilos = GmailApp.search(q, 0, 10);
  for (let t = 0; t < hilos.length; t++) {
    const ms = hilos[t].getMessages();
    for (let m = 0; m < ms.length; m++) {
      if (ms[m].getDate() >= desde && ms[m].getTo().toLowerCase().indexOf(email) !== -1 && ms[m].getSubject() === ASUNTO) {
        let rfc = ''; try { rfc = ms[m].getHeader('Message-ID'); } catch (e) {}
        return { id: ms[m].getId(), hilo: hilos[t].getId(), rfc: rfc, fecha: ms[m].getDate() };
      }
    }
  }
  return null;
}

function libro_() {
  const id = prop_('LIBRO_ID');
  if (id) { try { return SpreadsheetApp.openById(id); } catch (e) {} }
  const ss = SpreadsheetApp.create('Registro envíos nota de prensa 25N');
  prop_('LIBRO_ID', ss.getId());
  return ss;
}

function destinatarios_() {
  const ss = libro_();
  let sh = ss.getSheetByName('Destinatarios');
  if (sh && sh.getLastRow() > 1) return sh;
  sh = sh || ss.getSheets()[0].setName('Destinatarios');
  const vistos = {}, filas = [];
  CONTACTOS.forEach(function (raw) {
    const e = String(raw).trim().toLowerCase();
    if (!e || vistos[e]) return; vistos[e] = true;
    const interno = e.split('@')[1] === DOMINIO;
    filas.push([e, interno && !INCLUIR_INTERNOS ? 'EXCLUIDO' : 'PENDIENTE', '', '', '', '', '', 0, interno ? 'Dirección interna' : '']);
  });
  sh.getRange(1, 1, 1, 9).setValues([['Email', 'Estado', 'Fecha', 'Hora', 'ID mensaje Gmail', 'Message-ID (RFC 822)',
    'ID hilo', 'Intentos', 'Detalle']]).setFontWeight('bold');
  sh.getRange(2, 1, filas.length, 9).setValues(filas);
  sh.setFrozenRows(1);
  return sh;
}

function fila_(sh, fila, estado, r, intentos, detalle) {
  const d = r && r.fecha ? r.fecha : new Date();
  sh.getRange(fila, 2, 1, 8).setValues([[estado, fmt_(d, 'dd/MM/yyyy'), fmt_(d, 'HH:mm:ss'), r ? r.id : '', r ? r.rfc : '',
    r ? r.hilo : '', intentos || '', detalle || '']]);
}

function registrar_(tipo, email, r, resultado, detalle) {
  const ss = libro_();
  let sh = ss.getSheetByName('Registro');
  if (!sh) { sh = ss.insertSheet('Registro'); sh.appendRow(['Fecha', 'Hora', 'Tipo', 'Destinatario', 'Resultado',
    'ID mensaje Gmail', 'Message-ID (RFC 822)', 'ID hilo', 'Detalle']); sh.setFrozenRows(1); }
  const d = new Date();
  sh.appendRow([fmt_(d, 'dd/MM/yyyy'), fmt_(d, 'HH:mm:ss'), tipo, email, resultado, r ? r.id : '', r ? r.rfc : '', r ? r.hilo : '', detalle || '']);
}

function contar_(sh) {
  const n = {}; sh.getRange(2, 2, sh.getLastRow() - 1, 1).getValues().forEach(function (v) { n[v[0]] = (n[v[0]] || 0) + 1; });
  return n;
}
function borrarActivadores_() { ScriptApp.getProjectTriggers().forEach(function (t) {
  if (t.getHandlerFunction() === 'paso3_enviarMedios') ScriptApp.deleteTrigger(t); }); }
function prop_(k, v) { const p = PropertiesService.getScriptProperties(); if (v === undefined) return p.getProperty(k); p.setProperty(k, v); }
function fmt_(d, f) { return Utilities.formatDate(d, 'Europe/Madrid', f); }
function mb_(n) { return (n / 1048576).toFixed(2) + ' MB'; }

function cuerpo_() {
  const lugar = 'sala de exposiciones de la Escuela de Arte y Superior de Diseño Pedro Almodóvar, Plaza de la Provincia, 1, Ciudad Real';
  const p = [
    'La Escuela de Arte y Superior de Diseño Pedro Almodóvar, en colaboración con el Ayuntamiento de Ciudad Real, inaugura la exposición del concurso anual de carteles con motivo del 25N, Día Internacional para la Eliminación de la Violencia contra las Mujeres. En el mismo acto se entregarán los premios del concurso.',
    'El cartel ganador es obra de la alumna María Magdalena Merino Arévalo, bajo el lema «Los tiempos cambian, ¿y tú?». Realizado con la técnica del collage, representa un patrón persistente con las palabras «amor, respeto, igualdad, sin excusas, sin miedo».',
    'La directora del centro, Antonia Badillo, presentará la muestra, y la concejala de Igualdad del Ayuntamiento de Ciudad Real, María José Escobedo, participará en la entrega de galardones. Asistirán la autora del cartel ganador y sus compañeros de estudios.',
    'Adjuntamos la nota de prensa completa (PDF) y la imagen del cartel ganador (JPG). En su difusión debe citarse la autoría: María Magdalena Merino Arévalo.',
  ];
  const texto = ['Estimados/as profesionales de los medios de comunicación:', '', p[0], '', 'CONVOCATORIA',
    'Fecha: jueves, 19 de noviembre de 2026', 'Hora: 11:00 horas', 'Lugar: ' + lugar, '', p[1], '', p[2], '', p[3], '',
    'Quedamos a su disposición para cualquier información adicional.', '', 'Un cordial saludo,', '',
    'Promoción Artística', 'Escuela de Arte y Superior de Diseño Pedro Almodóvar', 'Ciudad Real', REMITENTE].join('\n');
  const P = function (t) { return '<p style="margin:0 0 14px 0">' + t + '</p>'; };
  const html = '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#222;max-width:640px">' +
    P('Estimados/as profesionales de los medios de comunicación:') + P(p[0]) +
    '<table role="presentation" style="border-left:3px solid #7b2d8e;padding:4px 0 4px 12px;margin:0 0 14px 0"><tr><td>' +
    '<b>CONVOCATORIA</b><br><b>Fecha:</b> jueves, 19 de noviembre de 2026<br><b>Hora:</b> 11:00 horas<br><b>Lugar:</b> ' + lugar +
    '</td></tr></table>' + P(p[1]) + P(p[2]) +
    P('Adjuntamos la nota de prensa completa (PDF) y la imagen del cartel ganador (JPG). <b>En su difusión debe citarse la autoría: María Magdalena Merino Arévalo.</b>') +
    P('Quedamos a su disposición para cualquier información adicional.') + P('Un cordial saludo,') +
    '<p style="margin:0">Promoción Artística<br>Escuela de Arte y Superior de Diseño Pedro Almodóvar<br>Ciudad Real<br>' +
    '<a href="mailto:' + REMITENTE + '">' + REMITENTE + '</a></p></div>';
  return { texto: texto, html: html };
}
