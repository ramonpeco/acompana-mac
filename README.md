# Acompaña para Mac

Asistente de voz para personas mayores o dependientes. Escucha de forma continua, entiende lo que se le pide y responde en voz alta. Usa Claude Haiku, de Anthropic, para razonar y ejecuta en el propio Mac las herramientas que consultan tus datos.

Creada por Ramón Peco · ramonpeco@gmail.com

## Qué hace

- Escucha siempre mientras está abierta: detecta cuándo terminas de hablar y responde en voz alta. Una luz de colores en el borde de la ventana indica que te está oyendo.
- Se le puede interrumpir diciendo «para» o «calla».
- Consulta el tiempo, las noticias (La Vanguardia, RTVE, Europa Press), Wikipedia e internet.
- Con tu permiso, lee tus contactos, calendario, recordatorios, fotos y las carpetas de documentos que elijas (PDF, también escaneados, textos e imágenes).
- Lee los mensajes que llegan del iPhone y avisa en voz alta de los nuevos.
- Prepara llamadas a través del iPhone. Cada llamada se confirma en pantalla.
- No da consejos médicos ni indica dosis: esas preguntas las responden reglas fijas, no la IA.

## Requisitos

- macOS 27 o posterior.
- Micrófono (el Mac mini no trae micrófono integrado: sirve uno USB, unos auriculares o una cámara web).
- Una clave de la API de Anthropic. El consumo se cobra en tu cuenta de Anthropic: una pregunta sencilla cuesta menos de medio céntimo de dólar.

## Instalación

1. Descarga `Acompana-Mac.dmg` en la sección **Releases** de esta página.
2. Ábrelo y arrastra **Acompana Mac** a la carpeta **Aplicaciones**.
3. La primera vez, macOS avisará de que no puede comprobar al desarrollador, porque la app no se distribuye por la App Store. Ve a **Ajustes del Sistema → Privacidad y seguridad**, baja hasta el aviso sobre Acompaña y pulsa **Abrir igualmente**.
4. Acepta los permisos de micrófono y reconocimiento de voz.
5. Crea una clave en **console.anthropic.com → API Keys** y pégala en **Ajustes de Acompaña → Claude**. Se guarda en el llavero de tu Mac.
6. En los mismos ajustes, da acceso a contactos, calendario, recordatorios, fotos y a las carpetas que quieras.

### Mensajes y llamadas del iPhone

- El iPhone y el Mac deben usar la misma cuenta de Apple.
- En el iPhone, activa **Ajustes → Apps → Mensajes → Reenvío de SMS** y **Ajustes → Apps → Teléfono → Llamadas en otros dispositivos**.
- Para que Acompaña pueda leer los mensajes, añádela en **Ajustes del Sistema → Privacidad y seguridad → Acceso total al disco** y vuelve a abrirla.

## Privacidad

- El reconocimiento de voz y la voz que lee las respuestas funcionan en el Mac.
- Para responder, Claude recibe tu pregunta y lo que devuelven las herramientas (fragmentos de documentos, datos de una cita, el texto de un mensaje…), no tus carpetas completas. Anthropic indica que, por defecto, no usa el contenido de su API para entrenar sus modelos.
- Las búsquedas en internet que contengan algo parecido a un DNI, un teléfono, una cuenta bancaria o un correo electrónico se bloquean.
- La app no envía mensajes ni hace compras. Las llamadas solo se hacen cuando las confirmas.

## Aviso

Acompaña es una ayuda para el día a día, no un producto sanitario ni un servicio de emergencias.

---

© 2026 Ramón Peco (ramonpeco@gmail.com). Todos los derechos reservados.
