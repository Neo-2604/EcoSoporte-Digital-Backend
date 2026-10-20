# EcoSoporte Digital — versión local para revisión

Plataforma Flask con interfaces independientes para clientes, técnicos, Soporte TI y administración; tickets, empresas, directorio, perfil, asignación de áreas, roles, filtros y ANS de referencia.

## Ejecutar en Windows
1. Instala Python 3.11 o superior.
2. Descomprime este proyecto.
3. Abre PowerShell dentro de la carpeta que contiene `app.py`.
4. Ejecuta:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:ADMIN_EMAIL="tu-correo-administrador@dominio.com"
$env:ADMIN_PASSWORD="EscribeUnaClavePropiaDe12CaracteresOMas"
$env:SECRET_KEY="cambia-esto-por-un-secreto-largo-y-aleatorio"
python app.py
```

Abre `http://127.0.0.1:5000`.

**Importante:** el primer administrador se crea únicamente si configuras `ADMIN_EMAIL` y `ADMIN_PASSWORD` (mínimo 12 caracteres) antes del primer arranque. No hay una contraseña de administrador fija en el código ni en la interfaz. Si ya se creó una base sin administrador, define las variables y reinicia; la aplicación creará la cuenta.

## Base de datos
En el primer arranque, la aplicación crea `instance/ecosoporte.db`. Para esta plataforma nueva es una base de desarrollo independiente. No se conecta a una base anterior de Render ni modifica la información de otro proyecto. Haz copias de seguridad antes de experimentar con datos.

## Funciones implementadas en esta versión
- Registro de empresa + cuenta de cliente.
- Inicio de sesión y permisos por rol.
- Cliente: solo ve sus tickets, sin filtros de categoría ni asignación de áreas.
- Técnico: ve los tickets asignados; no tiene accesos a empresas ni asignaciones.
- Soporte TI y administrador: asignación de área/técnico.
- Administrador: alta de usuarios, edición de rol/área y habilitar/deshabilitar.
- Perfil con datos registrados.
- Directorio de contactos filtrable por nombre, correo, empresa y rol.
- Filtros de estado y búsqueda de tickets.
- Vistas separadas de Inicio, Servicios, Nosotros y Políticas.
- Footer únicamente en páginas públicas; no aparece en interfaces autenticadas.
- Ajustes de contraste de modo oscuro para botones y áreas.
- Fechas objetivo de ANS de referencia por prioridad e historial de cambios.

## ANS
Objetivos de referencia configurados: Crítica 1 h respuesta / 4 h solución; Alta 4 h / 8 h; Media 8 h / 24 h; Baja 24 h / 48 h. Antes de usarlos como compromiso formal, confirma si son horas corridas o hábiles y establece horario de servicio, pausas y festivos.

## Publicación en Render
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`
- Variables de entorno: `SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`.
- No uses SQLite en almacenamiento efímero para producción: configura almacenamiento persistente o, preferiblemente, una base de datos administrada y prueba una migración con copia de seguridad antes de cambiar la configuración.
- El correo de soporte y los perfiles de redes sociales deben reemplazarse por los canales oficiales antes de publicar.
- La página de políticas es una base informativa y requiere revisión legal antes de utilizarla como política contractual definitiva.

## Pendientes antes de producción
CSRF con Flask-WTF, recuperación verificada de contraseña, auditoría ampliada, backups automáticos, almacenamiento seguro de adjuntos, tests automatizados y revisión legal de privacidad. No publiques datos reales ni credenciales en el repositorio.


## Ajustes de interfaz y privacidad (v3)
- La cuenta Cliente ya no consulta el directorio privado: en su lugar ve solo técnicos activos asignados a tickets de su propia empresa, con correo y celular profesional disponibles.
- El cierre de sesión está dentro del menú de la cuenta (**Mi perfil**); se quitó del pie del menú lateral.
- El formulario de administración de usuarios incluye celular y la tabla muestra el número registrado.
- El selector de técnico tiene ancho controlado y truncamiento visual para conservar el diseño.
- Las tarjetas de estado del panel principal son enlaces a la lista de tickets filtrada por estado; “Resueltos” incluye también los cerrados.

Al actualizar una instalación local, conserva `instance/ecosoporte.db` y el entorno `.venv`; reemplaza únicamente los archivos de la aplicación que contiene este ZIP.

## Módulo de seguridad y privacidad entre empresas

Esta versión aplica aislamiento por empresa en las consultas de tickets del cliente: los usuarios con rol `Usuario` consultan tickets asociados a su `company_id`, y el detalle de un ticket vuelve a validar esa pertenencia tanto en GET como en POST. Las cuentas sin empresa vinculada no pueden ver tickets de clientes ni crear tickets hasta que el administrador las vincule. El directorio de clientes muestra únicamente técnicos activos asignados a tickets de su empresa; los técnicos solo ven contactos de empresas relacionadas con tickets asignados a ellos. Administrador y Soporte TI conservan el directorio global.

### Pruebas de regresión

Desde la raíz del proyecto, con las dependencias instaladas, ejecutar:

```bash
python -m unittest discover -s tests -v
```

Las pruebas crean una base de datos temporal separada y comprueban que un cliente no vea tickets ni técnicos de otra empresa. No modifican `instance/ecosoporte.db`.

### Antes de desplegar en Render

1. Haz copia de seguridad de la base de datos de producción.
2. Publica los cambios en una rama y prueba primero en un servicio de staging, si está disponible.
3. Comprueba con dos empresas de prueba que no se puede acceder a tickets ajenos modificando la URL.
4. No subas `instance/ecosoporte.db`, contraseñas ni claves secretas al repositorio.
5. Configura `SECRET_KEY` como variable secreta en Render y verifica que la ruta de la base de datos tenga persistencia adecuada. El disco del sistema de archivos de un servicio web de Render puede ser efímero si no se configura almacenamiento persistente.
