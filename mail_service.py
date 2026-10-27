"""Envío de correos transaccionales. Sin dependencias de Flask.

Configuración (variables de entorno; nada va en el repositorio):
  MAIL_ENABLED     "true" para activar. Si falta o es otro valor, no se envía nada y la app funciona igual.
  MAIL_PROVIDER    "resend" (API por HTTPS, recomendado en Render) o "smtp".
  MAIL_FROM        Remitente, p. ej. "EcoSoporte <soporte@tudominio.com>".
  RESEND_API_KEY   (provider resend)
  SMTP_HOST, SMTP_PORT (587 por defecto), SMTP_USER, SMTP_PASSWORD, SMTP_SECURITY ("starttls" o "ssl")  (provider smtp)
Un fallo al enviar nunca interrumpe la creación del ticket.
"""
import json, logging, os, re, smtplib, ssl, threading, urllib.request
from email.message import EmailMessage
from email.utils import parseaddr
from html import escape

log = logging.getLogger("ecosoporte.mail")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")


def enabled():
    return os.environ.get("MAIL_ENABLED", "").strip().lower() in ("1", "true", "yes")


def status():
    """Estado de configuración sin exponer valores."""
    prov = os.environ.get("MAIL_PROVIDER", "resend").strip().lower()
    need = ["MAIL_FROM"] + (["SMTP_HOST"] if prov == "smtp" else ["RESEND_API_KEY"])
    return dict(enabled=enabled(), provider=prov, missing=[k for k in need if not os.environ.get(k, "").strip()])


def _clean(s, n=200):
    return re.sub(r"[\r\n]+", " ", str(s or "")).strip()[:n]


def build(to, subject, text, html):
    sender = os.environ.get("MAIL_FROM", "").strip()
    if not _EMAIL.match(parseaddr(sender)[1] or "") or not _EMAIL.match(to or ""):
        raise ValueError("Dirección de correo no válida")
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = _clean(sender), to, _clean(subject, 150)
    m.set_content(text)
    m.add_alternative(html, subtype="html")
    return m


def _send_resend(m, to, subject, text, html):
    body = json.dumps(dict(**{"from": m["From"]}, to=[to], subject=m["Subject"], text=text, html=html)).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=body, method="POST", headers={
        "Authorization": "Bearer " + os.environ["RESEND_API_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return 200 <= r.status < 300


def _send_smtp(m):
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    user, pwd = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    ctx = ssl.create_default_context()
    if os.environ.get("SMTP_SECURITY", "starttls").lower() == "ssl":
        s = smtplib.SMTP_SSL(host, port, timeout=10, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=10); s.starttls(context=ctx)
    with s:
        if user: s.login(user, pwd)
        s.send_message(m)
    return True


def send(to, subject, text, html):
    """Envía de forma síncrona. Devuelve True/False; nunca lanza ni registra datos del mensaje."""
    if not enabled() or status()["missing"]:
        return False
    try:
        m = build(to, subject, text, html)
        if os.environ.get("MAIL_PROVIDER", "resend").strip().lower() == "smtp":
            return _send_smtp(m)
        return _send_resend(m, to, subject, text, html)
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo enviar el correo: %s", type(e).__name__)
        return False


def send_async(*a, **k):
    """Envía en segundo plano para no hacer esperar la respuesta web."""
    if not enabled():
        return
    threading.Thread(target=send, args=a, kwargs=k, daemon=True).start()


def ticket_created_message(code, name, title, description, category, priority, company, response_due, solution_due, url):
    """Devuelve (asunto, texto, html) con todo escapado."""
    desc = (description or "").strip()
    desc = desc[:600] + ("…" if len(desc) > 600 else "")
    rows = [("Código", code), ("Asunto", title), ("Categoría", category), ("Prioridad", priority), ("Empresa", company or "—"),
            ("Primera respuesta antes de", response_due or "—"), ("Solución estimada antes de", solution_due or "—")]
    text = f"Hola {name},\n\nRecibimos tu solicitud de soporte.\n\n" + "\n".join(f"{k}: {v}" for k, v in rows) + \
           f"\n\nDescripción:\n{desc}\n\nPuedes seguir tu ticket aquí: {url}\n\nEcoSoporte Digital"
    tr = "".join(f"<tr><td style='padding:4px 12px 4px 0;color:#555'>{escape(k)}</td><td><b>{escape(str(v))}</b></td></tr>" for k, v in rows)
    html = (f"<div style='font-family:Arial,sans-serif;max-width:560px'><h2 style='color:#0f766e'>Recibimos tu ticket {escape(code)}</h2>"
            f"<p>Hola {escape(name)}, ya registramos tu solicitud de soporte.</p><table>{tr}</table>"
            f"<p style='margin-top:14px'><b>Descripción</b><br>{escape(desc).replace(chr(10), '<br>')}</p>"
            f"<p><a href='{escape(url, quote=True)}' style='background:#0f766e;color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none'>Ver mi ticket</a></p>"
            f"<p style='color:#777;font-size:12px'>Este es un aviso automático de EcoSoporte Digital.</p></div>")
    return f"Recibimos tu ticket {code}", text, html
