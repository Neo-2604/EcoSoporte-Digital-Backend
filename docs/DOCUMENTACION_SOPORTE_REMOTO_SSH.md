# Soporte remoto por SSH en EcoSoporte Digital

Documentación funcional y técnica del módulo de soporte remoto. Está pensada para técnicos, Soporte TI, Administración y para defender el proyecto.

## 1. Qué es SSH y qué hace (y qué no hace) EcoSoporte

**SSH (Secure Shell)** es un protocolo que permite abrir una terminal en otro equipo a través de la red, con la comunicación cifrada y autenticada con llaves. Es la forma estándar de administrar servidores y equipos Linux (y también Windows 10/11 y Windows Server con el servicio «OpenSSH Server»).

EcoSoporte Digital **no abre las conexiones SSH por ti**. La plataforma hace tres cosas:

1. **Autoriza**: solo se puede conectar a equipos registrados previamente, y solo con una sesión solicitada, aprobada y vigente.
2. **Audita**: queda registrado quién pidió, quién aprobó, quién inició, cuándo y con qué resultado, y se anota en el ticket.
3. **Escala**: si no se resuelve a distancia, un botón convierte el caso en visita presencial (el ticket pasa a «En espera» con la nota del técnico).

La conexión real la abre el técnico **desde su propio computador o portátil**, con su llave SSH personal, usando el comando que la plataforma le muestra. Por eso la plataforma **nunca guarda contraseñas ni llaves privadas**.

> Por qué no se abre desde la página: el servidor de la web (Render) está en internet y los equipos de los clientes casi siempre están detrás de un router (NAT) y un firewall. Además, una terminal remota controlada por la página web sería un blanco de ataque muy delicado. Este diseño es más seguro y más sencillo de sustentar.

## 2. Flujo completo

```
Técnico                    Plataforma                        Soporte TI / Admin
   |  1. Solicita sesión ----->|  (equipo + ticket + motivo)         |
   |                           |------ 2. Notifica en "Sesiones" --->|
   |                           |<----- 3. Aprueba (o rechaza) -------|
   |<-- 4. Muestra comando ----|  (válida 60 min para iniciar)       |
   |  5. Abre SSH desde su PC  |                                     |
   |  6. "Ya me conecté" ----->|  estado: Activa (máx. 4 h)          |
   |  7a. Resuelto: cierra --->|  nota en el ticket                  |
   |  7b. No se pudo: escala ->|  ticket "En espera" + visita        |
```

Estados de una sesión: **Solicitada → Aprobada → Activa → Cerrada** (o **Escalada**). También **Rechazada** y **Expirada** (la aprobada que no se inició a tiempo, o la activa que superó 4 horas).

## 3. Roles y permisos

| Rol | Qué puede hacer |
|---|---|
| Técnico | Solicitar sesiones **solo** sobre equipos de empresas con tickets activos asignados a él, y **siempre** ligadas a uno de sus tickets. Iniciar, cerrar o escalar **sus** sesiones. Ver el comando de conexión solo de sus sesiones. |
| Soporte TI | Registrar y deshabilitar equipos. Aprobar o rechazar solicitudes. Ver todas las sesiones. Sus propias solicitudes se aprueban automáticamente. |
| Administrador | Todo lo de Soporte TI, más la actividad en vivo. |
| Cliente | Sin acceso al módulo. |

Reglas que el sistema impone: motivo obligatorio (mín. 10 caracteres), resumen obligatorio al cerrar o escalar, un técnico no puede aprobar su propia solicitud, un técnico no ve sesiones de otros, y la dirección del equipo solo admite letras, números, puntos y guiones (se rechaza cualquier intento de inyectar comandos).

## 4. Preparar un equipo del cliente (una sola vez)

Hazlo con el cliente presente y con su autorización escrita.

**Linux (Ubuntu/Debian)**
```bash
sudo apt install openssh-server fail2ban
sudo adduser --disabled-password soporte          # usuario dedicado, no usar "root"
sudo mkdir -p /home/soporte/.ssh && sudo nano /home/soporte/.ssh/authorized_keys   # pegar la llave PÚBLICA del técnico
sudo chown -R soporte:soporte /home/soporte/.ssh && sudo chmod 700 /home/soporte/.ssh && sudo chmod 600 /home/soporte/.ssh/authorized_keys
```
Endurece `/etc/ssh/sshd_config`:
```
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
AllowUsers soporte
```
Luego `sudo systemctl restart ssh`. Si el técnico necesita privilegios, concédelos con `sudo` limitado a los comandos necesarios (archivo en `/etc/sudoers.d/`), no con acceso total.

**Windows 10/11 / Server**: Configuración → Aplicaciones → Características opcionales → «Servidor OpenSSH»; iniciar el servicio `sshd` y ponerlo en automático. Crear un usuario local `soporte` y colocar la llave pública en `C:\ProgramData\ssh\administrators_authorized_keys` (si es administrador) o en `C:\Users\soporte\.ssh\authorized_keys`.

**Huella del servidor** (para que el técnico verifique que no lo están suplantando):
```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
# SHA256:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx ...
```
Cópiala en el campo «Huella» al registrar el equipo.

## 5. Cómo llega el técnico al equipo (el problema del router)

Elige **una** de estas opciones por cliente:

- **A. Servidor puente (recomendado).** Un servidor pequeño con IP pública (un VPS) hace de «salto». El equipo del cliente se conecta hacia él con un túnel inverso y el técnico entra con `ssh -J`. En Render se define la variable `SSH_BASTION` (por ejemplo `salto@puente.ecosoporte.com`) y el comando que muestra la plataforma ya incluye `-J`.
  En el equipo del cliente: `autossh -M 0 -N -R 22022:localhost:22 salto@puente.ecosoporte.com`
- **B. VPN** (WireGuard, Tailscale o similar): técnico y equipo quedan en la misma red privada; el equipo se registra con su IP de la VPN.
- **C. Conexión directa**: solo si el cliente tiene IP pública y abre el puerto en el router (menos recomendado; si se hace, usar un puerto distinto al 22 y restringir por IP).

## 6. Llaves del técnico

Cada técnico genera **su propia** llave en su computador (no se comparten):
```bash
ssh-keygen -t ed25519 -C "daniela@ecosoporte" 
```
- Poner una **contraseña** a la llave (passphrase).
- Entregar al administrador solo el archivo `.pub` (la llave pública). **La privada (sin `.pub`) jamás se comparte ni se sube a ningún sitio.**
- Si un técnico sale de la empresa o pierde el portátil: se elimina su llave pública de los equipos y se deshabilita su cuenta en la plataforma.

## 7. Paso a paso en la plataforma

**Registrar el equipo** (Soporte TI o Administrador): menú *Soporte remoto → Equipos autorizados*. Completa nombre, empresa, dirección (IP o nombre), puerto, usuario SSH (ej. `soporte`), sistema y huella.

**Solicitar** (técnico): *Soporte remoto → Solicitar una sesión*. Elige el equipo y el ticket, escribe el motivo. En la app, desde el detalle del ticket → *Soporte remoto*.

**Aprobar** (Soporte TI/Admin): abre la sesión y pulsa *Aprobar* o *Rechazar*.

**Conectar** (técnico): al aprobarse aparece el comando, por ejemplo
```
ssh -J salto@puente.ecosoporte.com -p 22 soporte@10.0.0.5
```
Cópialo en tu terminal. La primera vez, SSH mostrará la huella: **debe coincidir** con la de la plataforma; si no coincide, cancela y avisa a Seguridad. Después pulsa *Ya me conecté: iniciar sesión*.

**Terminar**: *Resuelto: cerrar sesión* o *No se pudo: escalar a visita presencial*. En ambos casos escribe qué hiciste; queda como nota del ticket. Al escalar, el ticket pasa a **En espera** para que Soporte TI programe la visita.

## 8. Qué queda registrado (auditoría)

Por cada sesión: solicitante, equipo, ticket, motivo, quién aprobó y cuándo, hora de inicio y fin, resultado y resumen, y la línea de eventos (solicitud, aprobación, inicio, cierre/escalamiento). Esto responde a «quién entró, cuándo y por qué».

**Limitación honesta:** la plataforma no graba los comandos que se escriben dentro de la sesión SSH, porque la conexión no pasa por ella. Si se necesita ese nivel de detalle, el servidor puente puede grabar las sesiones (herramientas como `script` o `tlog`) y esa es una mejora futura recomendada.

## 9. Buenas prácticas y seguridad

- Un usuario `soporte` dedicado por equipo, sin contraseña, solo por llave.
- Mínimo privilegio: `sudo` solo para lo necesario.
- Sesiones cortas, con motivo y ticket. Nada de accesos permanentes sin autorización escrita del cliente.
- No copiar ni descargar datos del cliente; solo lo necesario para el diagnóstico.
- Mantener el sistema operativo y OpenSSH actualizados; usar `fail2ban`.
- Revisar periódicamente la lista de equipos y deshabilitar los que ya no se atiendan.
- Estas reglas están incluidas en las **Políticas** de la página (apartado «Soporte remoto y acceso SSH»).

## 10. Configuración en Render (variables de entorno)

| Variable | Para qué sirve |
|---|---|
| `SSH_BASTION` | (Opcional) servidor puente. Si existe, el comando incluye `-J`. |
| `APP_DOWNLOAD_URL` | Dirección del instalador de la app para técnicos. |
| `APP_VERSION` | (Opcional) versión que se muestra junto al botón de descarga. |

## 11. Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `Connection timed out` | Router/firewall o el equipo apagado | Verificar el túnel/VPN o que el equipo esté encendido y en red |
| `Permission denied (publickey)` | Tu llave pública no está en `authorized_keys` o permisos incorrectos | Revisar permisos (700 en `.ssh`, 600 en `authorized_keys`) |
| `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED` | El equipo cambió o alguien lo suplanta | **Detente.** Compara con la huella de la plataforma y avisa |
| La sesión aparece «Expirada» | Pasó el tiempo sin iniciarla (60 min) o superó 4 h activa | Solicitar una nueva |
| No veo el equipo al solicitar | No tienes un ticket activo asignado de esa empresa, o el equipo está deshabilitado | Pedir a Soporte TI que revise la asignación |

## 12. Glosario

- **Llave pública / privada:** par de archivos; la pública se coloca en el equipo, la privada nunca sale de tu computador.
- **Huella (fingerprint):** resumen corto de la llave del servidor para comprobar su identidad.
- **Servidor puente (bastion / jump host):** equipo intermedio por el que se pasa para llegar a otro.
- **NAT:** el router que oculta los equipos internos y no deja entrar conexiones directas.
- **Túnel inverso:** conexión que abre el propio equipo del cliente hacia afuera para que el técnico pueda entrar por ella.
