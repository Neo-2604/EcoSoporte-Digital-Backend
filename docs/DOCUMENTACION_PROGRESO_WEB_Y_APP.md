# Progreso de EcoSoporte Digital: página web y app móvil

Documento para mostrar la evolución del proyecto, qué se mejoró y por qué. Todo el código vive en dos repositorios: **EcoSoporte-Digital-Backend** (web + API) y **EcoSoporteApp** (app Expo).

## 1. Punto de partida
- Web Flask con tickets, roles (Administrador, Soporte TI, Técnico, Usuario/Cliente) y SLA, ya en Render.
- API REST `/api/v1` con login por token (válido 7 días).
- Base de datos **PostgreSQL en Render**, compartida por la web y la app: lo que se crea en una se ve en la otra.
- App básica con una sola vista para todos los roles.

## 2. Línea de tiempo de mejoras
| Etapa | Qué se hizo | Para qué |
|---|---|---|
| Datos | Migración a PostgreSQL; pruebas automáticas con base separada. | Datos reales y persistentes; no depender de un PC local. |
| Módulos | Actividad en vivo, chatbot, solicitudes, soporte remoto SSH, página de descarga de la app, políticas. | Seguimiento de usuarios, captar clientes y atender equipos a distancia. |
| Correcciones | Tablas sin barra horizontal; panel de edición de usuario en 4 columnas; filtro del directorio reparado (era un paréntesis faltante en el script). | Usabilidad. |
| Rediseño web | Menú de perfil arriba a la derecha, barra lateral delgada, diseño más compacto, modo claro/oscuro dentro del menú. | Orden y presentación profesional. |
| Personal | ID legible por empleado (`TEC-DG-01`) y perfil propio con privacidad por rol. | Saber a quién se asigna cada ticket. |
| App | Rediseño completo para técnicos (claro/oscuro, márgenes seguros, logo, «Mi jornada», soporte remoto, «En vivo», directorio y perfiles). | App útil en campo. |

## 3. Página web
**Barra superior nueva:** botón «Nuevo ticket» y un menú desplegable de perfil (arriba a la derecha) con: Mi información, Mi perfil de trabajo, Cambiar contraseña, Modo oscuro/claro (interruptor), App para técnicos, Directorio, Políticas de la empresa y Cerrar sesión. Se eligió agrupar ahí lo personal para liberar la barra lateral, que queda solo con navegación de trabajo.

**Barra lateral:** más delgada, con scroll discreto que no rompe el diseño.

**Diseño compacto:** menos espacio vertical, tablas que ajustan el texto (sin desplazamiento horizontal), tarjetas más pequeñas.

**ID de personal:** formato `PREFIJO-INICIALES-NN` (ADM administrador, STI Soporte TI, TEC técnico). Ejemplo: Daniela Granados → `TEC-DG-01`. Se asigna automáticamente al crear o editar usuarios, y aparece en el directorio, en el menú de perfil y al asignar tickets.

**Perfil del personal** (se abre desde el Directorio de contactos):
| Quién mira | Qué ve |
|---|---|
| Administrador (y el propio empleado) | Todo: documento, nacimiento, ingreso, ciudad, dirección, contacto de emergencia, especialidades y estadísticas. |
| Soporte TI | Datos de trabajo (cargo, área, ID, especialidades, tickets). **Sin datos personales.** |
| Cliente / Usuario | Solo nombre, correo, teléfono y área, y únicamente de técnicos asignados a tickets de su empresa. |

**Administración nueva:** *Actividad en vivo* (visitas, quién está conectado desde web o app), *Solicitudes del chat*, *Soporte remoto* (ver `DOCUMENTACION_SOPORTE_REMOTO_SSH.md`) y *App para técnicos* (descarga; el enlace se configura con la variable `APP_DOWNLOAD_URL` en Render).

**Contraseñas:** el administrador puede cambiar la contraseña al editar un usuario; cada usuario puede cambiar la suya desde el menú de perfil.

## 4. App móvil
- **Tema claro/oscuro** (seleccionable en Perfil; recuerda la preferencia).
- **Márgenes seguros:** encabezados y botón «volver» ya no quedan pegados a la barra de notificaciones.
- **Login con el logo** de EcoSoporte (antes mostraba una «E»).
- **Navegación inferior:** Mi jornada · Tickets · Remoto · En vivo (administrador) · Perfil.
- **Mi jornada:** resumen del día, tickets por prioridad.
- **Detalle de ticket:** tiempo de solución (SLA), cambio de estado, contacto del cliente, acciones.
- **Soporte remoto:** solicitar, ver y cerrar sesiones, con aviso de escalamiento a visita presencial.
- **En vivo (administrador):** quién está conectado y desde dónde.
- **Directorio y perfiles** con la misma privacidad que la web; cambio de contraseña.
- **Presencia:** la app envía un «latido» cada minuto (y al volver a primer plano) a `/api/v1/ping`; por eso aparece en *Actividad en vivo* como «App». **Importante:** las versiones anteriores de la app no envían ese latido; hay que instalar la nueva versión (APK) para que se vea la conexión desde la app.
- Distribución solo para técnicos y administrador (APK por EAS Build).

## 5. Seguridad y calidad
- Rutas y API protegidas por rol; datos de clientes aislados por empresa.
- Consultas con parámetros, sin contraseñas ni llaves SSH guardadas.
- **35 pruebas automáticas** sobre PostgreSQL (aislamiento entre empresas, CRUD y filtros, chatbot, soporte remoto, perfiles y presencia). Se ejecutan con `TEST_DATABASE_URL` apuntando a una base cuyo nombre contenga «test».

## 6. Pendientes sugeridos
Publicar el APK y configurar `APP_DOWNLOAD_URL`; restringir CORS al dominio real; definir retención de conversaciones del chatbot; integrar WhatsApp; evaluar notificaciones push en la app.
