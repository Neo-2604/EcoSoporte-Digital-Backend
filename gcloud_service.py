"""
EcoSoporte Digital · Servicio de integración con Google Cloud (Vertex AI).

ÚNICO punto del proyecto que habla con Google Cloud. No depende de Flask ni de la base de datos: la app le inyecta un
«almacén de uso» (set_usage_store) para contar llamadas. Si algo no está configurado, el servicio queda DESACTIVADO y
la aplicación sigue funcionando igual.

Principios:
  * Configuración solo por variables de entorno; la credencial es un archivo de cuenta de servicio montado por Render
    (Secret File). Nada de claves en el repositorio, en la base de datos ni en pantalla.
  * Permisos mínimos: rol «Usuario de Vertex AI». La comprobación de API usa un permiso opcional.
  * Cuotas propias: tope diario de llamadas, tiempo máximo y tokens de salida acotados.
  * Los errores se traducen a mensajes seguros: nunca se devuelve el cuerpo de la respuesta de Google ni el texto enviado.
  * Se usa un modelo PREENTRENADO de Google. Este módulo no entrena ni ajusta ningún modelo.
"""
import json, os, re, time
from datetime import datetime

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
REQUIRED_VARS = ["GCP_PROJECT_ID", "GCP_LOCATION", "VERTEX_MODEL", "GOOGLE_APPLICATION_CREDENTIALS"]
_ID_RE = re.compile(r"^[a-z][a-z0-9\-]{4,61}[a-z0-9]$")
_LOC_RE = re.compile(r"^[a-z]+(?:-[a-z0-9]+)+$|^global$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{1,80}$")


class MLUnavailable(Exception):
    """La integración está desactivada o incompleta (no es un error: es el modo normal sin configurar)."""


class MLError(Exception):
    """Falla controlada al hablar con Google. `code` es estable; `message` es seguro para mostrar."""
    def __init__(self, code, message):
        super().__init__(message); self.code, self.message = code, message


# ------------------------------------------------------------------ configuración
def _bool(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def _int(v, default, lo, hi):
    try: return max(lo, min(hi, int(str(v).strip())))
    except (TypeError, ValueError): return default


def config():
    e = os.environ.get
    return dict(enabled=_bool(e("GCP_ML_ENABLED")), project=(e("GCP_PROJECT_ID") or "").strip(), location=(e("GCP_LOCATION") or "").strip(),
                model=(e("VERTEX_MODEL") or "").strip(), cred_path=(e("GOOGLE_APPLICATION_CREDENTIALS") or "").strip(),
                daily_limit=_int(e("VERTEX_DAILY_LIMIT"), 50, 1, 5000), timeout=_int(e("VERTEX_TIMEOUT_SECONDS"), 20, 3, 60),
                max_tokens=_int(e("VERTEX_MAX_OUTPUT_TOKENS"), 300, 8, 2048),
                docai_enabled=_bool(e("DOCAI_ENABLED")), docai_processor=(e("DOCAI_PROCESSOR_ID") or "").strip(),
                docai_location=(e("DOCAI_LOCATION") or "").strip())


def present_vars():
    """Solo NOMBRES y si están definidas (nunca los valores)."""
    e = os.environ.get
    names = ["GCP_ML_ENABLED"] + REQUIRED_VARS
    return [(n, bool((e(n) or "").strip())) for n in names]


def problems(cfg=None):
    """Lista de problemas de configuración, sin revelar valores."""
    c = cfg or config(); out = []
    for n, key in (("GCP_PROJECT_ID", "project"), ("GCP_LOCATION", "location"), ("VERTEX_MODEL", "model"), ("GOOGLE_APPLICATION_CREDENTIALS", "cred_path")):
        if not c[key]: out.append(f"Falta la variable {n}.")
    if c["project"] and not _ID_RE.match(c["project"]): out.append("GCP_PROJECT_ID no parece un ID de proyecto válido (usa el ID, no el nombre).")
    if c["location"] and not _LOC_RE.match(c["location"]): out.append("GCP_LOCATION no parece una región válida (ej. us-central1).")
    if c["model"] and not _MODEL_RE.match(c["model"]): out.append("VERTEX_MODEL contiene caracteres no válidos.")
    if c["cred_path"] and not os.path.isfile(c["cred_path"]): out.append("El archivo de credenciales no existe en la ruta indicada (¿Secret File en Render?).")
    return out


def status():
    c = config(); p = problems(c)
    state = "desactivada" if not c["enabled"] else ("incompleta" if p else "configurada")
    return dict(state=state, enabled=c["enabled"], problems=p, vars=present_vars(), daily_limit=c["daily_limit"], timeout=c["timeout"],
                max_tokens=c["max_tokens"], project=c["project"], location=c["location"], model=c["model"],
                docai=dict(enabled=c["docai_enabled"], configured=bool(c["docai_processor"] and c["docai_location"])))


def is_ready():
    c = config(); return c["enabled"] and not problems(c)


# ------------------------------------------------------------------ almacén de uso (inyectado por la app)
class MemoryUsage:
    def __init__(self): self.rows = []
    def count_today(self):
        d = datetime.utcnow().strftime("%Y-%m-%d"); return sum(1 for r in self.rows if r[0] == d)
    def record(self, kind, ok, status_text):
        self.rows.append((datetime.utcnow().strftime("%Y-%m-%d"), kind, ok, status_text))


_usage = MemoryUsage()


def set_usage_store(store):
    global _usage
    _usage = store


def usage_today():
    try: return _usage.count_today()
    except Exception: return 0


# ------------------------------------------------------------------ autenticación
_cred_cache = {"path": None, "creds": None}


def _credentials(cfg):
    """Carga la cuenta de servicio del archivo. Devuelve credenciales sin exponer su contenido."""
    if _cred_cache["creds"] is not None and _cred_cache["path"] == cfg["cred_path"]:
        return _cred_cache["creds"]
    try:
        from google.oauth2 import service_account
    except ImportError:
        raise MLUnavailable("Falta la librería google-auth en el servidor.")
    try:
        with open(cfg["cred_path"], encoding="utf-8") as f: info = json.load(f)
    except (OSError, ValueError):
        raise MLError("credentials", "No se pudo leer el archivo de credenciales (¿es un JSON válido?).")
    if info.get("type") != "service_account":
        raise MLError("credentials", "El archivo no es una clave de cuenta de servicio.")
    if info.get("project_id") and cfg["project"] and info["project_id"] != cfg["project"]:
        raise MLError("project_mismatch", "La cuenta de servicio pertenece a otro proyecto distinto de GCP_PROJECT_ID.")
    try:
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    except Exception:
        raise MLError("credentials", "La clave de la cuenta de servicio no es válida.")
    _cred_cache.update(path=cfg["cred_path"], creds=creds)
    return creds


def _token(cfg):
    creds = _credentials(cfg)
    try:
        from google.auth.transport.requests import Request
        if not creds.valid: creds.refresh(Request())
    except MLError: raise
    except Exception:
        raise MLError("auth", "Google rechazó la autenticación (clave revocada, reloj del servidor o red).")
    return creds.token


def reset_cache():
    _cred_cache.update(path=None, creds=None)


# ------------------------------------------------------------------ HTTP seguro
_STATUS_MESSAGES = {
    400: ("bad_request", "Google rechazó la solicitud (parámetros o modelo no válidos)."),
    401: ("auth", "Autenticación rechazada por Google."),
    403: ("permission", "Sin permiso: revisa que la API de Vertex AI esté habilitada y que la cuenta tenga el rol «Usuario de Vertex AI»."),
    404: ("not_found", "No se encontró el modelo o la región. Revisa VERTEX_MODEL y GCP_LOCATION."),
    429: ("quota", "Se superó la cuota de Google. Inténtalo más tarde."),
}


def _post(url, token, body, cfg):
    import requests
    for attempt in (0, 1):
        try:
            r = requests.post(url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=cfg["timeout"])
        except requests.Timeout:
            raise MLError("timeout", "Google tardó demasiado en responder.")
        except requests.RequestException:
            raise MLError("network", "No se pudo conectar con Google Cloud.")
        if r.status_code == 503 and attempt == 0:
            time.sleep(1); continue
        return r
    return r


def _get(url, token, cfg):
    import requests
    try: return requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=cfg["timeout"])
    except requests.Timeout: raise MLError("timeout", "Google tardó demasiado en responder.")
    except requests.RequestException: raise MLError("network", "No se pudo conectar con Google Cloud.")



def _check_http(r):
    if r.status_code == 200:
        return

    # Registrar únicamente información técnica segura.
    # No registrar el cuerpo de la respuesta, el token ni el prompt.
    status = r.status_code
    request_id = (
        (getattr(r, "headers", None) or {}).get("x-request-id")
        or (getattr(r, "headers", None) or {}).get("x-goog-request-id")
        or "no-disponible"
    )

    try:
        error_data = r.json().get("error", {})
        google_status = str(error_data.get("status", "UNKNOWN"))
        google_message = str(error_data.get("message", ""))
    except (ValueError, AttributeError):
        google_status = "UNKNOWN"
        google_message = ""

    # Clasificar el error sin exponer el mensaje completo de Google.
    msg_lower = google_message.lower()

    if "api has not been used" in msg_lower or "api is disabled" in msg_lower:
        reason = "API_DISABLED"
    elif "permission" in msg_lower or "permission_denied" in msg_lower:
        reason = "PERMISSION"
    elif "publisher model" in msg_lower or "not found" in msg_lower:
        reason = "MODEL_OR_REGION"
    elif "quota" in msg_lower or "resource_exhausted" in google_status.lower():
        reason = "QUOTA"
    elif "billing" in msg_lower:
        reason = "BILLING"
    else:
        reason = "OTHER"

    detail = re.sub(r"[^A-Za-z0-9 .,:_/-]", " ", google_message)[:200]
    print(
        "[Vertex AI] "
        f"http_status={status} "
        f"google_status={google_status} "
        f"reason={reason} "
        f"request_id={request_id} "
        f"detail={detail!r}"
    )

    code, msg = _STATUS_MESSAGES.get(
        status,
        (
            "server" if status >= 500 else "http",
            f"Google respondió con un error HTTP ({status}).",
        ),
    )

    if status == 403 and reason == "API_DISABLED":
        code, msg = (
            "api_disabled",
            "La API de Vertex AI parece estar deshabilitada. Revisa su estado en Google Cloud.",
        )
    elif status == 403 and reason == "BILLING":
        code, msg = (
            "billing",
            "El proyecto no tiene facturación activa. Vincula una cuenta de facturación en Google Cloud.",
        )
    elif status == 403 and reason == "MODEL_OR_REGION":
        code, msg = (
            "model_or_region",
            "Google rechazó el acceso al modelo o a la región configurada.",
        )

    raise MLError(code, msg)



# ------------------------------------------------------------------ llamadas
def _endpoint(cfg):
    host = "aiplatform.googleapis.com" if cfg["location"] == "global" else f"{cfg['location']}-aiplatform.googleapis.com"
    return (f"https://{host}/v1/projects/{cfg['project']}/locations/{cfg['location']}/publishers/google/models/{cfg['model']}:generateContent")


def generate(prompt, system=None, kind="generate", json_output=False, max_tokens=None):
    """Una llamada a Vertex AI (modelo preentrenado). Cuenta contra el tope diario. Devuelve dict(text, prompt_tokens, output_tokens)."""
    cfg = config()
    if not cfg["enabled"]: raise MLUnavailable("La integración con Google Cloud está desactivada.")
    p = problems(cfg)
    if p: raise MLUnavailable("La integración está incompleta: " + p[0])
    if usage_today() >= cfg["daily_limit"]:
        raise MLError("daily_limit", f"Se alcanzó el tope diario de {cfg['daily_limit']} llamadas (VERTEX_DAILY_LIMIT).")
    token = _token(cfg)
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": min(max_tokens or cfg["max_tokens"], cfg["max_tokens"])}}
    if system: body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_output: body["generationConfig"]["responseMimeType"] = "application/json"
    try:
        r = _post(_endpoint(cfg), token, body, cfg)
        try: _check_http(r)
        except MLError as e:
            _usage.record(kind, False, e.code); raise
        try:
            data = r.json(); cand = data["candidates"][0]
            text = "".join(part.get("text", "") for part in cand["content"]["parts"])
            meta = data.get("usageMetadata", {})
        except (ValueError, KeyError, IndexError, TypeError):
            _usage.record(kind, False, "empty"); raise MLError("empty", "Google no devolvió contenido (posible bloqueo de seguridad del modelo).")
        _usage.record(kind, True, "ok")
        return dict(text=text.strip(), prompt_tokens=meta.get("promptTokenCount"), output_tokens=meta.get("candidatesTokenCount"))
    except MLError: raise


def api_enabled_check(cfg=None):
    """Comprueba con Service Usage si Vertex AI está habilitada (llamada gratuita; permiso opcional). Devuelve (ok|None, detalle)."""
    cfg = cfg or config(); token = _token(cfg)
    r = _get(f"https://serviceusage.googleapis.com/v1/projects/{cfg['project']}/services/aiplatform.googleapis.com", token, cfg)
    if r.status_code == 200:
        try: st = r.json().get("state")
        except ValueError: st = None
        return (st == "ENABLED", "Vertex AI API habilitada." if st == "ENABLED" else "Vertex AI API NO está habilitada en el proyecto.")
    if r.status_code in (403, 401):
        return (None, "No verificable: la cuenta no tiene el permiso opcional «Visor de Service Usage». No es un error.")
    return (None, f"No verificable (código {r.status_code}).")


def check_connection(call_model=False):
    """Prueba de conexión. Sin datos privados: texto fijo y breve. call_model=False es gratuita (solo configuración + token + API)."""
    out = []
    def add(name, ok, detail): out.append(dict(name=name, ok=ok, detail=detail))
    cfg = config()
    add("Interruptor GCP_ML_ENABLED", cfg["enabled"], "Activado." if cfg["enabled"] else "Desactivado: la app funciona sin Google Cloud.")
    if not cfg["enabled"]: return out
    p = problems(cfg)
    add("Variables de configuración", not p, "Completas." if not p else " ".join(p))
    if p: return out
    try:
        creds = _credentials(cfg); add("Credenciales de la cuenta de servicio", True, "Archivo válido y del proyecto configurado.")
        _token(cfg); add("Token de acceso (gratuito)", True, "Google aceptó la autenticación.")
    except MLUnavailable as e: add("Credenciales", None, str(e)); return out
    except MLError as e: add("Credenciales / token", False, e.message); return out
    try:
        ok, detail = api_enabled_check(cfg); add("API de Vertex AI habilitada", ok, detail)
    except MLError as e: add("API de Vertex AI habilitada", None, e.message)
    if call_model:
        try:
            res = generate("Responde únicamente con la palabra OK.", kind="test", max_tokens=8)
            add("Llamada real al modelo", True, f"Respondió (tokens: entrada {res['prompt_tokens']}, salida {res['output_tokens']}). Consumió una llamada de cuota.")
        except (MLError, MLUnavailable) as e:
            add("Llamada real al modelo", False, getattr(e, "message", str(e)))
    return out


# ------------------------------------------------------------------ privacidad
_PII = [
    (re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+"), "[correo]"),
    (re.compile(r"https?://\S+"), "[enlace]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    (re.compile(r"(?<![\w])\+?\d[\d\s\-().]{6,}\d(?![\w])"), "[número]"),
]


def redact_pii(text):
    """Enmascara correos, enlaces, IPs y números largos (teléfonos, documentos, NIT) antes de enviar texto a Google."""
    out = text or ""
    for rx, rep in _PII: out = rx.sub(rep, out)
    return out
