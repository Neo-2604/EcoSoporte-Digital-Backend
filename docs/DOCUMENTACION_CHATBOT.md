# Asistente virtual (chatbot) de EcoSoporte Digital

## 1. Qué es y qué hace
Un asistente que aparece como botón flotante (esquina inferior derecha) **solo a los visitantes que no han iniciado sesión**, en las páginas públicas (inicio, servicios, nosotros, políticas). Atiende cuatro caminos:

| Opción | Qué pasa |
|---|---|
| Vender mis componentes | Pregunta tipo de componente, marca/modelo, estado, cantidad, ciudad, nombre, celular, correo (opcional) y horario. |
| Solicitar un servicio | Pregunta servicio, empresa, nombre, celular, correo (opcional), detalle y horario. |
| Preguntas frecuentes | Responde 4 dudas comunes (cómo funciona el soporte, cómo registrar la empresa, precios, uso de datos). |
| Hablar con una persona | Pide nombre, celular y tema. |

Al terminar pide **autorización de tratamiento de datos** (Ley 1581 de 2012). Solo si el visitante acepta, la solicitud se guarda como un *lead* con código `SOL-AAAA-NNNN`. Si dice que no, no se guarda nada (la conversación sí queda registrada para auditoría).

## 2. Cómo funciona (no usa inteligencia artificial)
Es un **asistente por reglas** (máquina de estados). Es una decisión deliberada: es predecible, gratis, no inventa respuestas y no envía datos a terceros. Estados: `menu` → `flow` (preguntas paso a paso) → `done`; y `faq`. Escribir «menú», «inicio», «cancelar» o «volver» regresa al inicio. También reconoce palabras clave («vender», «ram», «soporte», «visita», «asesor»…).

Cada respuesta se valida: celular de 7 a 15 dígitos, correo con formato válido, cantidad numérica, opciones cerradas donde aplica. Límite de 120 mensajes por conversación y 400 caracteres por mensaje.

## 3. Dónde está el código
| Pieza | Archivo | Qué contiene |
|---|---|---|
| Interfaz del chat (botón, ventana, mensajes) | `static/js/chatbot.js` | JavaScript puro; habla con la API. |
| Estilos del chat | `static/css/extras.css` | Clases `cb-*`. |
| Se carga en las páginas | `templates/base.html` | Solo si no hay sesión y no es login/registro. |
| Lógica del asistente | `extras.py` (sección «Chatbot») | `FLOWS` (preguntas), `FAQ`, `_chat_reply` (máquina de estados), `_validate`, `_save_lead`. |
| Endpoints | `extras.py` | `POST /api/chat/start`, `POST /api/chat/message`. |
| Gestión de solicitudes | `extras.py` + `templates/leads.html` | Página `/solicitudes` y API `/api/v1/leads`. |
| Tablas | `extras.py` → `ensure_schema` | `chat_sessions`, `chat_messages`, `leads`. |
| Pruebas | `tests/test_extras.py` | Flujo completo del chat y permisos. |

Para **cambiar preguntas, opciones o preguntas frecuentes** edita las listas `COMPONENTS`, `CONDITIONS`, `SERVICES`, `TIMES`, `FAQ` y `FLOWS` en `extras.py`; no hay que tocar la interfaz.

## 4. Flujo técnico
1. El visitante abre el chat → `POST /api/chat/start` crea una sesión y devuelve un `token` aleatorio.
2. Cada mensaje → `POST /api/chat/message {token, text}`; el servidor avanza el estado, guarda el mensaje y devuelve `replies` y `options`.
3. Al aceptar el consentimiento se inserta el *lead* y se responde con el código `SOL-…`.

## 5. Base de datos
- `chat_sessions(token, state, data, messages, created_at, updated_at)`: estado de cada conversación.
- `chat_messages(session_id, sender, text, created_at)`: transcripción completa (bot y visitante).
- `leads(kind, name, phone, email, company, city, subject, details, quantity, condition, preferred_time, consent, status, assigned_to, appointment_at, notes, …)`: la solicitud.

## 6. Quién gestiona las solicitudes
Administrador y **Soporte TI** (menú lateral → *Solicitudes del chat*): ven la lista, filtran por estado (Nuevo, Contactado, Cita agendada, Cerrado…), se asignan la solicitud, fijan fecha de **cita** para ir a comprar/atender y dejan notas. No se creó un rol nuevo; si más adelante quieren uno (p. ej. «Ventas»), solo se agrega al control de roles.

## 7. Privacidad y seguridad
- Solo se guardan los datos que el visitante escribe y con su autorización.
- El texto se muestra siempre escapado (sin HTML), y las consultas usan parámetros (sin inyección SQL).
- Los endpoints del chat son públicos; la gestión requiere sesión con rol autorizado.
- Recomendado: definir cuánto tiempo se conservan las conversaciones.

## 8. Próximo paso: WhatsApp
La lógica está separada de la interfaz. Para WhatsApp (API de WhatsApp Business / Twilio) basta con un endpoint nuevo que reciba el mensaje del usuario, llame a `_chat_reply` con una sesión identificada por su número y devuelva el texto; los *leads* caerían en la misma tabla y pantalla.
