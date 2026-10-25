import os, json, secrets, re
import psycopg2
import psycopg2.extensions
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool
try:  # carga el archivo .env si existe (SECRET_KEY, DATABASE_URL, ADMIN_EMAIL...)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, abort
from werkzeug.security import generate_password_hash, check_password_hash
import extras
import payroll, catalog


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production")
# Cadena de conexión PostgreSQL, p. ej. postgresql://usuario:clave@localhost:5432/ecosoporte
app.config["DATABASE_URL"] = os.environ.get("DATABASE_URL", "postgresql://postgres@localhost:5432/ecosoporte")
with open(os.path.join(os.path.dirname(__file__), "seed_data.json"), encoding="utf-8") as f:
    SEED = json.load(f)
AREAS = sorted(set([r["area"] for r in SEED.get("roles", [])] + ["Soporte TI", "Infraestructura", "Seguridad", "Aplicaciones", "Datos", "Logística TI"]))
CATEGORIES = SEED.get("categories", ["Hardware", "Software", "Redes", "Seguridad", "Aplicaciones", "Periféricos", "Sistemas", "Soporte al usuario", "Otros"])
ROLES = ["Administrador", "Soporte TI", "Técnico", "Usuario"]
STATUSES = ["Abierto", "En proceso", "En espera", "Resuelto", "Cerrado"]
PRIORITIES = ["Baja", "Media", "Alta", "Crítica"]
SLA = {"Crítica": (1,4), "Alta": (4,8), "Media": (8,24), "Baja": (24,48)}

# --- PostgreSQL ---------------------------------------------------------------
# Las columnas TIMESTAMP se devuelven como texto "YYYY-MM-DD HH:MM:SS" (igual que en SQLite)
# para que las plantillas y el JSON de la API sigan viendo el mismo formato.
psycopg2.extensions.register_type(
    psycopg2.extensions.new_type((1114,), "TS_AS_TEXT", lambda v, cur: v[:19] if v else None))

_pool = None
_schema_ready = False

def _get_pool():
    global _pool
    if _pool is None:
        # timezone=UTC: CURRENT_TIMESTAMP y datetime.utcnow() quedan en la misma zona.
        _pool = ThreadedConnectionPool(1, int(os.environ.get("DB_POOL_MAX", "10")),
                                       app.config["DATABASE_URL"], options="-c timezone=UTC")
    return _pool

class Conn:
    """Envoltura mínima para conservar el estilo c.execute(...).fetchone() de sqlite3."""
    def __init__(self, raw): self.raw = raw
    def execute(self, sql, params=None):
        cur = self.raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()

def db():
    if "db" not in g:
        pool = _get_pool()
        raw = pool.getconn()
        if raw.closed:  # conexión caída (p. ej. reinicio del servidor): descartar y pedir otra
            pool.putconn(raw, close=True)
            raw = pool.getconn()
        g.db = Conn(raw)
    return g.db

@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn:
        if not conn.raw.closed: conn.raw.rollback()  # descarta transacciones abiertas
        _get_pool().putconn(conn.raw)

def to_int(v):
    """Convierte a int (rango INTEGER de PostgreSQL) o devuelve None si no es válido.
    SQLite aceptaba cualquier texto en columnas enteras; PostgreSQL lanza error."""
    if isinstance(v, bool): return None
    try: n = int(str(v).strip())
    except (TypeError, ValueError): return None
    return n if 0 < n <= 2147483647 else None

def person_label(c, uid):
    """Nombre legible (y ID de trabajo) de una persona, para dejarlo escrito en el historial del ticket."""
    if not uid: return "sin asignar"
    r = c.execute("SELECT name, tech_code FROM users WHERE id=%s", (uid,)).fetchone()
    if not r: return "cuenta eliminada"
    return f"{r['name']} ({r['tech_code']})" if r["tech_code"] else r["name"]

def _notify_new_ticket(c, tid, title, priority, created):
    """Avisa a Administrador y Soporte TI quién creó el ticket, de qué empresa y con qué prioridad."""
    who = g.user
    co = c.execute("SELECT co.name FROM tickets t LEFT JOIN companies co ON co.id=t.company_id WHERE t.id=%s", (tid,)).fetchone()
    empresa = co["name"] if co and co["name"] else "sin empresa"
    role = "Cliente" if who["role"] == "Usuario" else who["role"]
    extras.notify(c, "ticket", f"Nuevo ticket {ticket_code(tid, created)}: {title}",
                  f"{who['name']} ({role}) · {empresa} · prioridad {priority}",
                  actor_id=who["id"], actor_name=who["name"], ticket_id=tid)

def _tick_info(c, ticket_id):
    return c.execute("SELECT created_by, assigned_to, created_at, status FROM tickets WHERE id=%s", (ticket_id,)).fetchone()


def _notify_creator(c, ticket_id, kind, title, body):
    """Avisa a quien creó el ticket (el cliente) de cualquier movimiento, salvo que lo haya hecho él mismo."""
    t = _tick_info(c, ticket_id)
    if not t or not t["created_by"] or t["created_by"] == g.user["id"]:
        return
    extras.notify(c, kind, title.replace("{code}", ticket_code(ticket_id, t["created_at"])), body,
                  actor_id=g.user["id"], actor_name=g.user["name"], target_user_id=t["created_by"], ticket_id=ticket_id)


def _notify_followup(c, ticket_id, old_status, new_status, note):
    """Estado nuevo u observación: avisa al cliente creador; si escribe el cliente, avisa al técnico responsable."""
    t = _tick_info(c, ticket_id)
    if not t:
        return
    code = ticket_code(ticket_id, t["created_at"])
    snippet = (note or "").strip()[:160] or None
    who = g.user["name"]
    if g.user["role"] == "Usuario":
        if t["assigned_to"] and t["assigned_to"] != g.user["id"] and snippet:
            extras.notify(c, "nota", f"El cliente escribió en {code}", f"{who}: {snippet}", actor_id=g.user["id"],
                          actor_name=who, target_user_id=t["assigned_to"], ticket_id=ticket_id)
        return
    if new_status != old_status:
        _notify_creator(c, ticket_id, "estado", f"Tu ticket {{code}} ahora está: {new_status}",
                        f"{who}" + (f": {snippet}" if snippet else ""))
    elif snippet:
        _notify_creator(c, ticket_id, "nota", "Nueva observación en tu ticket {code}", f"{who}: {snippet}")


def _log_assignment(c, ticket_id, area, tech_id):
    """Deja en el historial el área y el responsable con su nombre, y avisa al técnico asignado."""
    c.execute("INSERT INTO ticket_updates(ticket_id,user_id,user_name,note,status) VALUES(%s,%s,%s,%s,NULL)",
              (ticket_id, g.user["id"], g.user["name"],
               f"Asignación actualizada: área {area}; responsable {person_label(c, tech_id)}."))
    if tech_id and tech_id != g.user["id"]:
        t = c.execute("SELECT created_at FROM tickets WHERE id=%s", (ticket_id,)).fetchone()
        extras.notify(c, "asignacion", f"Te asignaron el ticket {ticket_code(ticket_id, t['created_at'] if t else None)}",
                      f"Asignado por {g.user['name']} · área {area}", actor_id=g.user["id"], actor_name=g.user["name"],
                      target_user_id=tech_id, ticket_id=ticket_id)
    if tech_id:
        _notify_creator(c, ticket_id, "asignacion", "Tu ticket {code} fue asignado",
                        f"Responsable: {person_label(c, tech_id)} · área {area}")

def ticket_code(tid, created=None):
    """Código visible y único del ticket: TCK-AAAA-NNNN (año de creación + número del ticket).
    Se calcula a partir del id, que es único; no se guarda en la base de datos."""
    year = getattr(created, "year", None)
    if year is None:
        s = str(created or "")[:4]
        year = int(s) if s.isdigit() else datetime.utcnow().year
    return f"TCK-{year}-{int(tid):04d}"

def _code_of(t):
    try: created = t["created_at"]
    except Exception: created = None
    return ticket_code(t["id"], created)

def with_code(row):
    d = dict(row); d["code"] = _code_of(d)
    d["year"] = int(d["code"].split("-")[1]); return d

def search_id(q):
    """Permite buscar por 12, #12 o TCK-2026-0012."""
    m = re.fullmatch(r"(?:tck-\d{4}-|#)?0*(\d+)", (q or "").strip(), re.I)
    return m.group(1) if m else q

app.jinja_env.filters["tcode"] = _code_of

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies(
  id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, name TEXT NOT NULL, nit TEXT UNIQUE, email TEXT NOT NULL,
  phone TEXT, address TEXT, city TEXT, contact_name TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'Usuario', area TEXT,
  active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  company_id INTEGER REFERENCES companies(id), phone TEXT, position TEXT);
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'Soporte al usuario', priority TEXT NOT NULL DEFAULT 'Media',
  status TEXT NOT NULL DEFAULT 'Abierto', company_id INTEGER REFERENCES companies(id),
  created_by INTEGER NOT NULL REFERENCES users(id), assigned_to INTEGER REFERENCES users(id),
  area TEXT NOT NULL DEFAULT 'Soporte TI',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  response_due TIMESTAMP, solution_due TIMESTAMP, resolved_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS ticket_updates(
  id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  ticket_id INTEGER NOT NULL REFERENCES tickets(id), user_id INTEGER NOT NULL REFERENCES users(id),
  note TEXT NOT NULL, status TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_users_company ON users(company_id);
CREATE INDEX IF NOT EXISTS idx_tickets_company ON tickets(company_id);
CREATE INDEX IF NOT EXISTS idx_tickets_created_by ON tickets(created_by);
CREATE INDEX IF NOT EXISTS idx_tickets_assigned_to ON tickets(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_ticket_updates_ticket ON ticket_updates(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_updates_user ON ticket_updates(user_id);
"""

def init_db():
    """Crea las tablas si no existen (la base migrada ya las trae) y el administrador inicial."""
    c = db()
    c.execute("SELECT pg_advisory_xact_lock(727274)")  # evita carreras entre varios workers
    c.execute(SCHEMA)
    extras.ensure_schema(c)  # actividad, chatbot, soporte remoto
    payroll.ensure_schema(c)  # nómina y desempeño
    catalog.ensure_schema(c)  # catálogo y cotizaciones
    # A fresh development database gets one administrator from environment variables.
    if not c.execute("SELECT id FROM users WHERE role='Administrador' LIMIT 1").fetchone():
        # Never ship a public, hard-coded administrator password. Bootstrap only from environment.
        email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
        password = os.environ.get("ADMIN_PASSWORD", "")
        if email and password and len(password) >= 12:
            c.execute("INSERT INTO users(name,email,password_hash,role,area) VALUES(%s,%s,%s,%s,%s) ON CONFLICT (email) DO NOTHING",
                      ("Administrador EcoSoporte", email, generate_password_hash(password), "Administrador", "Administración"))
    c.commit()

@app.before_request
def ensure_db():
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"): return redirect(url_for("login", next=request.path))
        user = db().execute("SELECT * FROM users WHERE id=%s AND active", (session["user_id"],)).fetchone()
        if not user:
            session.clear()
            flash("La cuenta no está activa. Contacta al administrador.", "error")
            return redirect(url_for("login"))
        g.user = user
        return fn(*args, **kwargs)
    return wrapper

def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if g.user["role"] not in roles: abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return deco

@app.context_processor
def inject():
    return {"current_user": getattr(g, "user", None), "areas": AREAS, "categories": CATEGORIES,
            "all_roles": ROLES, "statuses": STATUSES, "priorities": PRIORITIES}

def role_home(user):
    return "dashboard"

@app.route("/")
def home():
    if session.get("user_id"): return redirect(url_for("dashboard"))
    return render_template("landing.html")

@app.route("/servicios")
def services(): return render_template("services.html")

@app.route("/nosotros")
def about(): return render_template("about.html")

@app.route("/politicas")
def policies(): return render_template("policies.html")

@app.route("/mi-cuenta/politicas")
@login_required
def policies_account(): return render_template("policies_app.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if session.get("user_id"): return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        user = db().execute("SELECT * FROM users WHERE email=%s AND active", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear(); session["user_id"] = user["id"]
            flash("Inicio de sesión correcto.", "success")
            return redirect(url_for("dashboard"))
        flash("Correo o contraseña incorrectos, o cuenta deshabilitada.", "error")
    return render_template("login.html")

@app.route("/registro", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
        password=request.form.get("password",""); company_name=request.form.get("company","").strip()
        nit=request.form.get("nit","").strip() or None; phone=request.form.get("phone","").strip()
        city=request.form.get("city","").strip(); address=request.form.get("address","").strip()
        if not all([name,email,password,company_name]):
            flash("Completa los campos obligatorios.", "error")
        elif len(password)<10:
            flash("La contraseña debe tener al menos 10 caracteres.", "error")
        elif db().execute("SELECT id FROM users WHERE email=%s",(email,)).fetchone():
            flash("Ya existe una cuenta con ese correo.", "error")
        elif nit and db().execute("SELECT id FROM companies WHERE nit=%s",(nit,)).fetchone():
            flash("Ya existe una empresa registrada con ese NIT.", "error")
        else:
            c=db()
            try:
                cur=c.execute("INSERT INTO companies(name,nit,email,phone,address,city,contact_name) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                              (company_name,nit,email,phone,address,city,name))
                company_id=cur.fetchone()["id"]
                c.execute("INSERT INTO users(name,email,password_hash,role,area,company_id,phone) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                          (name,email,generate_password_hash(password),"Usuario","Cliente",company_id,phone))
                c.commit()
                flash("¡Cuenta creada correctamente! Ya puedes iniciar sesión.", "success")
                return redirect(url_for("login", registered="1"))
            except psycopg2.IntegrityError:
                c.rollback(); flash("No fue posible registrar la cuenta. Verifica los datos e inténtalo de nuevo.", "error")
    return render_template("register.html")

@app.route("/logout", methods=["GET","POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    c = db()
    role = g.user["role"]

    # Filtro para la consulta de tickets, que utiliza el alias t.
    where = ""
    args = []

    # Filtro separado para los contadores, que consulta directamente tickets.
    count_where = ""
    count_args = []

    if role == "Usuario":
        company_id = g.user["company_id"]

        if company_id:
            where = " WHERE t.company_id = %s"
            args = [company_id]
            count_where = " WHERE company_id = %s"
            count_args = [company_id]
        else:
            where = " WHERE 1 = 0"
            count_where = " WHERE 1 = 0"

    elif role == "Técnico":
        where = " WHERE t.assigned_to = %s"
        args = [g.user["id"]]
        count_where = " WHERE assigned_to = %s"
        count_args = [g.user["id"]]

    counts = {
        s: c.execute(
            "SELECT COUNT(*) n FROM tickets"
            + count_where
            + (" AND" if count_where else " WHERE")
            + " status = %s",
            count_args + [s]
        ).fetchone()["n"]
        for s in STATUSES
    }

    sql = (
        "SELECT t.*, co.name company, u.name creator, "
        "tech.name technician "
        "FROM tickets t "
        "JOIN users u ON u.id = t.created_by "
        "LEFT JOIN companies co ON co.id = t.company_id "
        "LEFT JOIN users tech ON tech.id = t.assigned_to"
        + where
        + " ORDER BY EXTRACT(YEAR FROM t.created_at) DESC, t.id DESC LIMIT 8"
    )

    tickets = c.execute(sql, args).fetchall()

    return render_template(
        "dashboard.html",
        tickets=tickets,
        counts=counts
    )

@app.route("/tickets")
@login_required
def tickets():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    category = request.args.get("category", "")
    priority = request.args.get("priority", "")
    area = request.args.get("area", "")
    company_id = request.args.get("company_id", "")

    sql = ("SELECT t.*, COALESCE(u.name, t.creator_name, 'Cuenta eliminada') creator, co.name company, tech.name technician "
           "FROM tickets t LEFT JOIN users u ON u.id=t.created_by "
           "LEFT JOIN companies co ON co.id=t.company_id "
           "LEFT JOIN users tech ON tech.id=t.assigned_to WHERE 1=1")
    args = []

    if g.user["role"] == "Usuario":
        if g.user["company_id"]:
            sql += " AND t.company_id=%s"
            args.append(g.user["company_id"])
        else:
            sql += " AND 1=0"
    elif g.user["role"] == "Técnico":
        sql += " AND t.assigned_to=%s"
        args.append(g.user["id"])

    if q:
        sql += " AND (t.title ILIKE %s OR CAST(t.id AS TEXT)=%s)"
        args.extend(["%" + q + "%", search_id(q)])

    if status in STATUSES:
        sql += " AND t.status=%s"
        args.append(status)
    elif status == "finalizados":
        sql += " AND t.status IN ('Resuelto','Cerrado')"

    if category and g.user["role"] != "Usuario" and category in CATEGORIES:
        sql += " AND t.category=%s"
        args.append(category)

    if priority in PRIORITIES:
        sql += " AND t.priority=%s"
        args.append(priority)

    if area and area in AREAS:
        sql += " AND t.area=%s"
        args.append(area)

    if company_id and g.user["role"] in ["Administrador", "Soporte TI"]:
        sql += " AND t.company_id=%s"
        args.append(to_int(company_id))  # id no válido => sin resultados (PostgreSQL es estricto con los tipos)

    sql += " ORDER BY EXTRACT(YEAR FROM t.created_at) DESC, t.id DESC"
    rows = db().execute(sql, args).fetchall()
    companies_list = db().execute("SELECT id, name FROM companies ORDER BY name").fetchall() if g.user["role"] in ["Administrador", "Soporte TI"] else []

    return render_template("tickets.html", tickets=rows, companies=companies_list)

@app.route("/tickets/new",methods=["GET","POST"])
@login_required
def ticket_new():
    if request.method=="POST":
        title=request.form.get("title","").strip(); desc=request.form.get("description","").strip()
        if not title or not desc: flash("Completa el asunto y la descripción.", "error")
        else:
            role=g.user["role"]
            category=request.form.get("category","Soporte usuario") if role!="Usuario" else "Soporte usuario"
            if category not in CATEGORIES: category="Soporte usuario"
            priority=request.form.get("priority","Media")
            if priority not in PRIORITIES: priority="Media"
            area=request.form.get("area","Soporte TI") if role in ["Administrador","Soporte TI"] else "Soporte TI"
            if area not in AREAS: area="Soporte TI"
            raw_company=None if role=="Usuario" else (request.form.get("company_id") or None)
            company_id=g.user["company_id"] if role=="Usuario" else to_int(raw_company)
            if role=="Usuario" and not company_id:
                flash("Tu cuenta no está vinculada a una empresa. Contacta al administrador.", "error")
                return redirect(url_for("ticket_new"))
            if (raw_company and company_id is None) or (company_id and not db().execute("SELECT id FROM companies WHERE id=%s", (company_id,)).fetchone()):
                flash("La empresa seleccionada no es válida.", "error")
                return redirect(url_for("ticket_new"))
            response_h, solution_h=SLA[priority]
            now=datetime.utcnow()
            cur=db().execute("""INSERT INTO tickets(title,description,category,priority,company_id,created_by,area,response_due,solution_due)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",(title,desc,category,priority,company_id,g.user["id"],area,
                (now+timedelta(hours=response_h)).isoformat(timespec="seconds"),(now+timedelta(hours=solution_h)).isoformat(timespec="seconds")))
            new_id=cur.fetchone()["id"]
            db().execute("UPDATE tickets SET creator_name=%s WHERE id=%s",(g.user["name"],new_id))
            db().execute("INSERT INTO ticket_updates(ticket_id,user_id,user_name,note,status) VALUES(%s,%s,%s,%s,%s)",(new_id,g.user["id"],g.user["name"],"Ticket creado.", "Abierto"))
            _notify_new_ticket(db(), new_id, title, priority, now)
            db().commit(); flash(f"Ticket {ticket_code(new_id, now)} creado correctamente.", "success")
            return redirect(url_for("ticket_detail",ticket_id=new_id))
    companies=db().execute("SELECT id,name FROM companies ORDER BY name").fetchall() if g.user["role"] in ["Administrador","Soporte TI"] else []
    return render_template("ticket_form.html",companies=companies)

@app.route("/tickets/<int:ticket_id>",methods=["GET","POST"])
@login_required
def ticket_detail(ticket_id):
    c=db()
    ticket=c.execute("SELECT t.*,COALESCE(u.name,t.creator_name,'Cuenta eliminada') creator,co.name company,tech.name technician FROM tickets t LEFT JOIN users u ON u.id=t.created_by LEFT JOIN companies co ON co.id=t.company_id LEFT JOIN users tech ON tech.id=t.assigned_to WHERE t.id=%s",(ticket_id,)).fetchone()
    if not ticket: abort(404)
    # Enforce authorization on the server for both GET and POST. Customers may
    # collaborate on tickets belonging to their own company, but never another tenant.
    if g.user["role"]=="Usuario":
        if not g.user["company_id"] or ticket["company_id"] != g.user["company_id"]: abort(403)
    if g.user["role"]=="Técnico" and ticket["assigned_to"]!=g.user["id"]: abort(403)
    if request.method=="POST":
        note=request.form.get("note","").strip()
        status=request.form.get("status",ticket["status"])
        can_change_status=g.user["role"] in ["Administrador","Soporte TI","Técnico"]
        if not can_change_status: status=ticket["status"]
        if status not in STATUSES: status=ticket["status"]
        if note:
            resolved = datetime.utcnow().isoformat(timespec="seconds") if status in ["Resuelto","Cerrado"] else None
            c.execute("INSERT INTO ticket_updates(ticket_id,user_id,user_name,note,status) VALUES(%s,%s,%s,%s,%s)",(ticket_id,g.user["id"],g.user["name"],note,status))
            c.execute("UPDATE tickets SET status=%s,updated_at=CURRENT_TIMESTAMP,resolved_at=COALESCE(%s,resolved_at) WHERE id=%s",(status,resolved,ticket_id))
            _notify_followup(c, ticket_id, ticket["status"], status, note)
            c.commit(); flash("Seguimiento registrado.", "success")
        return redirect(url_for("ticket_detail",ticket_id=ticket_id))
    updates=c.execute("SELECT tu.*,COALESCE(u.name,tu.user_name,'Cuenta eliminada') AS name FROM ticket_updates tu LEFT JOIN users u ON u.id=tu.user_id WHERE ticket_id=%s ORDER BY tu.created_at DESC, tu.id DESC",(ticket_id,)).fetchall()
    return render_template("ticket_detail.html",ticket=ticket,updates=updates)

@app.route("/tickets/<int:ticket_id>/edit", methods=["GET", "POST"])
@login_required
def edit_ticket(ticket_id):
    c = db()
    ticket = c.execute("SELECT * FROM tickets WHERE id=%s", (ticket_id,)).fetchone()
    if not ticket:
        abort(404)
    role = g.user["role"]
    staff = role in ["Administrador", "Soporte TI"]
    can_edit = staff or (
        role == "Usuario" and ticket["created_by"] == g.user["id"]
    ) or (
        role == "Técnico" and ticket["assigned_to"] == g.user["id"]
    )
    if not can_edit:
        abort(403)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        desc = request.form.get("description", "").strip()
        # Cada rol solo puede cambiar los campos que le corresponden (se valida aquí, no solo en la plantilla):
        #  - Usuario (cliente): asunto, descripción y prioridad.
        #  - Técnico: además categoría y estado.
        #  - Administrador / Soporte TI: además área, empresa y técnico responsable.
        category = request.form.get("category", ticket["category"]) if role != "Usuario" else ticket["category"]
        priority = request.form.get("priority", ticket["priority"])
        status = request.form.get("status", ticket["status"]) if role != "Usuario" else ticket["status"]
        area = request.form.get("area", ticket["area"]) if staff else ticket["area"]
        company_id = ticket["company_id"]
        assigned_to = ticket["assigned_to"]
        error = None

        if staff and "company_id" in request.form:
            raw = request.form.get("company_id")
            company_id = to_int(raw) if raw else None
            if raw and (company_id is None or not c.execute("SELECT id FROM companies WHERE id=%s", (company_id,)).fetchone()):
                error = "La empresa seleccionada no es válida."
        if staff and "assigned_to" in request.form and not error:
            raw = request.form.get("assigned_to")
            new_tech = to_int(raw) if raw else None
            if new_tech != ticket["assigned_to"]:
                if raw and (new_tech is None or not c.execute("SELECT id FROM users WHERE id=%s AND active AND role='Técnico'", (new_tech,)).fetchone()):
                    error = "El técnico debe estar activo y tener rol Técnico."
                else:
                    assigned_to = new_tech
        if not title or not desc:
            error = "Asunto y descripción son obligatorios."

        if error:
            flash(error, "error")
        else:
            if category not in CATEGORIES: category = ticket["category"]
            if priority not in PRIORITIES: priority = ticket["priority"]
            if status not in STATUSES: status = ticket["status"]
            if area not in AREAS: area = ticket["area"]

            response_due = ticket["response_due"]
            solution_due = ticket["solution_due"]
            if priority != ticket["priority"]:
                rh, sh = SLA[priority]
                now = datetime.utcnow()
                response_due = (now + timedelta(hours=rh)).isoformat(timespec="seconds")
                solution_due = (now + timedelta(hours=sh)).isoformat(timespec="seconds")

            resolved = datetime.utcnow().isoformat(timespec="seconds") if status in ["Resuelto", "Cerrado"] else None

            c.execute("""
                UPDATE tickets
                SET title=%s, description=%s, category=%s, priority=%s, area=%s, status=%s, company_id=%s, assigned_to=%s,
                    response_due=%s, solution_due=%s, updated_at=CURRENT_TIMESTAMP, resolved_at=COALESCE(%s, resolved_at)
                WHERE id=%s
            """, (title, desc, category, priority, area, status, company_id, assigned_to, response_due, solution_due, resolved, ticket_id))

            c.execute("INSERT INTO ticket_updates(ticket_id, user_id, user_name, note, status) VALUES(%s,%s,%s,%s,%s)",
                      (ticket_id, g.user["id"], g.user["name"], "Ticket modificado.", status))
            if assigned_to != ticket["assigned_to"]:
                _log_assignment(c, ticket_id, area, assigned_to)
            if status != ticket["status"]:
                _notify_followup(c, ticket_id, ticket["status"], status, None)
            c.commit()
            flash("Ticket actualizado correctamente.", "success")
            return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    companies = c.execute("SELECT id, name FROM companies ORDER BY name").fetchall() if staff else []
    technicians = c.execute("SELECT id, name, area, tech_code FROM users WHERE active AND role='Técnico' ORDER BY name").fetchall() if staff else []
    return render_template("ticket_edit.html", ticket=ticket, companies=companies, technicians=technicians)

@app.route("/companies",methods=["GET","POST"])
@roles_required("Administrador","Soporte TI")
def companies():
    c=db()
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); nit=request.form.get("nit","").strip() or None
        if not name or not email: flash("Nombre y correo son obligatorios.","error")
        elif nit and c.execute("SELECT id FROM companies WHERE nit=%s",(nit,)).fetchone(): flash("Ese NIT ya está registrado.","error")
        else:
            try:
                c.execute("INSERT INTO companies(name,nit,email,phone,address,city,contact_name) VALUES(%s,%s,%s,%s,%s,%s,%s)",(name,nit,email,request.form.get("phone"),request.form.get("address"),request.form.get("city"),request.form.get("contact_name")))
                c.commit(); flash("Empresa registrada correctamente.","success")
            except psycopg2.IntegrityError: c.rollback(); flash("No se pudo registrar la empresa. Revisa los datos.","error")
        return redirect(url_for("companies"))
    rows=c.execute("SELECT * FROM companies ORDER BY name").fetchall()
    return render_template("companies.html",companies=rows)

@app.route("/companies/<int:company_id>/edit", methods=["POST"])
@roles_required("Administrador", "Soporte TI")
def edit_company(company_id):
    c = db()
    company = c.execute("SELECT * FROM companies WHERE id=%s", (company_id,)).fetchone()
    if not company:
        abort(404)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    nit = request.form.get("nit", "").strip() or None
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    city = request.form.get("city", "").strip()
    contact_name = request.form.get("contact_name", "").strip()

    if not name or not email:
        flash("Nombre y correo son obligatorios.", "error")
    elif nit and c.execute("SELECT id FROM companies WHERE nit=%s AND id!=%s", (nit, company_id)).fetchone():
        flash("Ese NIT ya está registrado en otra empresa.", "error")
    else:
        try:
            c.execute("""
                UPDATE companies
                SET name=%s, nit=%s, email=%s, phone=%s, address=%s, city=%s, contact_name=%s
                WHERE id=%s
            """, (name, nit, email, phone, address, city, contact_name, company_id))
            c.commit()
            flash("Empresa actualizada correctamente.", "success")
        except psycopg2.IntegrityError:
            c.rollback()
            flash("No se pudo actualizar la empresa. Revisa los datos.", "error")
    return redirect(url_for("companies"))

@app.route("/users",methods=["GET","POST"])
@roles_required("Administrador")
def users():
    c=db()
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        phone=request.form.get("phone","").strip()
        role=request.form.get("role","Usuario"); area=request.form.get("area","Cliente")
        raw_company=request.form.get("company_id") or None; company_id=to_int(raw_company)
        if role not in ROLES: role="Usuario"
        if role=="Usuario": area="Cliente"
        if not name or not email or len(password)<10: flash("Completa nombre/correo y usa contraseña de mínimo 10 caracteres.","error")
        elif c.execute("SELECT id FROM users WHERE email=%s",(email,)).fetchone(): flash("Ese correo ya está registrado.","error")
        elif raw_company and (company_id is None or not c.execute("SELECT id FROM companies WHERE id=%s",(company_id,)).fetchone()):
            flash("La empresa seleccionada no es válida.","error")
        else:
            c.execute("INSERT INTO users(name,email,password_hash,role,area,company_id,phone) VALUES(%s,%s,%s,%s,%s,%s,%s)",(name,email,generate_password_hash(password),role,area,company_id,phone)); c.commit()
            extras.assign_codes(c)  # ID legible para el personal (TEC-DG-01)
            flash("Usuario creado correctamente.","success")
        return redirect(url_for("users"))
    rows=c.execute("SELECT u.id,u.tech_code,u.name,u.email,u.role,u.area,u.active,u.created_at,u.company_id,u.phone,u.position,co.name AS company_name FROM users u LEFT JOIN companies co ON co.id=u.company_id ORDER BY u.name").fetchall()
    companies_list=c.execute("SELECT id,name FROM companies ORDER BY name").fetchall()
    return render_template("users.html",users=rows,companies=companies_list)

@app.route("/users/<int:user_id>/edit",methods=["POST"])
@roles_required("Administrador")
def edit_user(user_id):
    c=db(); user=c.execute("SELECT * FROM users WHERE id=%s",(user_id,)).fetchone()
    if not user: abort(404)
    name = request.form.get("name", user["name"]).strip()
    email = request.form.get("email", user["email"]).strip().lower()
    phone = request.form.get("phone", user["phone"] or "").strip()
    position = request.form.get("position", user["position"] or "").strip()
    role = request.form.get("role", user["role"])
    area = request.form.get("area", user["area"])
    raw_company = request.form.get("company_id") or None
    company_id = to_int(raw_company)
    password = request.form.get("password", "").strip()

    if not name or not email:
        flash("Nombre y correo son obligatorios.", "error")
    elif role not in ROLES:
        flash("Rol inválido.", "error")
    elif user_id==g.user["id"] and role!="Administrador":
        flash("No puedes quitarte tu propio rol administrativo.", "error")
    elif c.execute("SELECT id FROM users WHERE email=%s AND id!=%s", (email, user_id)).fetchone():
        flash("Ese correo ya está registrado en otra cuenta.", "error")
    elif raw_company and (company_id is None or not c.execute("SELECT id FROM companies WHERE id=%s", (company_id,)).fetchone()):
        flash("La empresa seleccionada no es válida.", "error")
    else:
        if role=="Usuario": area="Cliente"
        sql = "UPDATE users SET name=%s, email=%s, phone=%s, position=%s, role=%s, area=%s, company_id=%s"
        params = [name, email, phone, position, role, area, company_id]
        if password:
            if len(password) < 10:
                flash("La nueva contraseña debe tener al menos 10 caracteres.", "error")
                return redirect(url_for("users"))
            sql += ", password_hash=%s"
            params.append(generate_password_hash(password))
        sql += " WHERE id=%s"
        params.append(user_id)
        try:
            c.execute(sql, params)
            c.commit()
            extras.refresh_code_on_role_change(c, user_id, user["role"], role)
            extras.assign_codes(c)
            flash("Usuario actualizado correctamente.", "success")
        except psycopg2.IntegrityError:
            c.rollback()
            flash("No se pudo actualizar el usuario. Revisa los datos.", "error")
    return redirect(url_for("users"))

@app.route("/users/<int:user_id>/toggle",methods=["POST"])
@roles_required("Administrador")
def toggle_user(user_id):
    c=db()
    if user_id==g.user["id"]: flash("No puedes deshabilitar tu propia cuenta.","error")
    else:
        target=c.execute("SELECT * FROM users WHERE id=%s",(user_id,)).fetchone()
        if target and target["role"]=="Administrador" and target["active"] and c.execute("SELECT COUNT(*) n FROM users WHERE role='Administrador' AND active").fetchone()["n"]<=1:
            flash("No puedes deshabilitar al último administrador activo.","error")
        elif target:
            c.execute("UPDATE users SET active=NOT active WHERE id=%s",(user_id,)); c.commit(); flash("Estado de la cuenta actualizado.","success")
    return redirect(url_for("users"))

def purge_user(c, uid):
    """Elimina la cuenta de la base de datos conservando el historial: los tickets, notas y sesiones remotas
    siguen existiendo con el nombre de la persona escrito (ya no apuntan a una cuenta). Devuelve (ok, mensaje)."""
    if uid == g.user["id"]:
        return False, "No puedes eliminar tu propia cuenta."
    t = c.execute("SELECT id, name, role, active, tech_code FROM users WHERE id=%s", (uid,)).fetchone()
    if not t:
        return False, "Usuario no encontrado."
    if t["role"] == "Administrador" and t["active"] and \
            c.execute("SELECT COUNT(*) n FROM users WHERE role='Administrador' AND active").fetchone()["n"] <= 1:
        return False, "No puedes eliminar al último administrador activo."
    label = f"{t['name']} ({t['tech_code']})" if t["tech_code"] else t["name"]
    # tickets que tenía asignados: quedan sin responsable y se deja constancia en su historial
    for r in c.execute("SELECT id FROM tickets WHERE assigned_to=%s", (uid,)).fetchall():
        c.execute("INSERT INTO ticket_updates(ticket_id,user_id,user_name,note,status) VALUES(%s,%s,%s,%s,NULL)",
                  (r["id"], g.user["id"], g.user["name"], f"La cuenta de {label} fue eliminada; el ticket quedó sin responsable."))
    c.execute("UPDATE tickets SET assigned_to=NULL WHERE assigned_to=%s", (uid,))
    c.execute("UPDATE tickets SET creator_name=COALESCE(creator_name,%s), created_by=NULL WHERE created_by=%s", (t["name"], uid))
    c.execute("UPDATE ticket_updates SET user_name=COALESCE(user_name,%s), user_id=NULL WHERE user_id=%s", (t["name"], uid))
    c.execute("UPDATE remote_sessions SET technician_name=COALESCE(technician_name,%s), technician_id=NULL WHERE technician_id=%s", (t["name"], uid))
    c.execute("UPDATE remote_sessions SET approved_by=NULL WHERE approved_by=%s", (uid,))
    c.execute("UPDATE remote_events SET user_name=COALESCE(user_name,%s), user_id=NULL WHERE user_id=%s", (t["name"], uid))
    c.execute("UPDATE remote_hosts SET created_by=NULL WHERE created_by=%s", (uid,))
    c.execute("UPDATE leads SET assigned_to=NULL WHERE assigned_to=%s", (uid,))
    c.execute("UPDATE presence SET user_id=NULL WHERE user_id=%s", (uid,))
    c.execute("UPDATE page_views SET user_id=NULL WHERE user_id=%s", (uid,))
    c.execute("DELETE FROM notifications WHERE target_user_id=%s", (uid,))
    c.execute("UPDATE notifications SET actor_id=NULL WHERE actor_id=%s", (uid,))
    c.execute("DELETE FROM notif_state WHERE user_id=%s", (uid,))
    c.execute("DELETE FROM push_tokens WHERE user_id=%s", (uid,))
    c.execute("DELETE FROM users WHERE id=%s", (uid,))
    c.commit()
    return True, f"Cuenta de {t['name']} eliminada. Su historial en los tickets se conserva."

@app.route("/users/demo",methods=["POST"])
@roles_required("Administrador")
def load_demo_data():
    """Agrega empresas, clientes, técnicos y tickets de ejemplo (una sola vez)."""
    import demo_data
    resumen, clave = demo_data.load_demo(db(), extras.assign_codes)
    if resumen.get("ya_existian"):
        flash("Los datos de demostración ya estaban cargados.", "error")
    else:
        flash(f"Se agregaron {resumen['empresas']} empresas, {resumen['clientes']} clientes, {resumen['tecnicos']} técnicos y "
              f"{resumen['tickets']} tickets con historial. Contraseña de las cuentas @{demo_data.DEMO_DOMAIN}: {clave} (guárdala ahora; no se vuelve a mostrar).", "success")
    return redirect(url_for("users"))

@app.route("/users/<int:user_id>/purge",methods=["POST"])
@roles_required("Administrador")
def purge_user_route(user_id):
    if request.form.get("confirm") != "ELIMINAR":
        flash("Escribe ELIMINAR para confirmar.", "error")
        return redirect(url_for("users"))
    ok, msg = purge_user(db(), user_id)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("users"))

@app.route("/areas",methods=["GET","POST"])
@roles_required("Administrador","Soporte TI")
def areas_view():
    c=db()
    if request.method=="POST":
        tid=to_int(request.form.get("ticket_id")); raw_tech=request.form.get("assigned_to") or None; tech=to_int(raw_tech); area=request.form.get("area","Soporte TI")
        ticket=c.execute("SELECT id FROM tickets WHERE id=%s",(tid,)).fetchone()
        if not ticket: flash("Ticket no encontrado.","error")
        elif area not in AREAS: flash("Área no válida.","error")
        elif raw_tech and (tech is None or not c.execute("SELECT id FROM users WHERE id=%s AND active AND role='Técnico' AND area=%s",(tech,area)).fetchone()):
            flash("El técnico debe estar activo y pertenecer al área seleccionada.","error")
        else:
            c.execute("UPDATE tickets SET area=%s,assigned_to=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(area,tech,tid))
            _log_assignment(c, tid, area, tech)
            c.commit(); flash("Área y responsable actualizados.","success")
        return redirect(url_for("areas_view"))
    rows=c.execute("SELECT t.*,COALESCE(u.name,t.creator_name,'Cuenta eliminada') creator,tech.name technician FROM tickets t LEFT JOIN users u ON u.id=t.created_by LEFT JOIN users tech ON tech.id=t.assigned_to ORDER BY EXTRACT(YEAR FROM t.created_at) DESC, t.id DESC").fetchall()
    technicians=c.execute("SELECT id,name,role,area,tech_code FROM users WHERE active AND role='Técnico' ORDER BY name").fetchall()
    return render_template("areas.html",tickets=rows,technicians=technicians)

@app.route("/perfil")
@login_required
def profile():
    """La antigua «Mi información» se unió a Mi perfil."""
    return redirect(url_for("extras.staff_profile", uid=g.user["id"]))

def _directory_rows():
    role = g.user["role"]

    if role == "Usuario":
        company_id = g.user["company_id"]

        if company_id:
            rows = db().execute("""
                SELECT DISTINCT
                    u.id, u.tech_code, u.name, u.email, u.phone, u.position,
                    u.role, u.area, c.name company
                FROM tickets t
                JOIN users u ON u.id = t.assigned_to
                LEFT JOIN companies c ON c.id = t.company_id
                WHERE t.company_id = %s
                  AND u.active
                  AND u.role = 'Técnico'
                ORDER BY u.name
            """, (company_id,)).fetchall()
        else:
            rows = []

        client_view = True

    elif role == "Técnico":
        rows = db().execute("""
            SELECT DISTINCT
                u.id, u.tech_code, u.name, u.email, u.phone, u.position,
                u.role, u.area, c.name company
            FROM users u
            LEFT JOIN companies c ON c.id = u.company_id
            WHERE u.active
              AND u.company_id IN (
                  SELECT DISTINCT t.company_id
                  FROM tickets t
                  WHERE t.assigned_to = %s
                    AND t.company_id IS NOT NULL
              )
            ORDER BY c.name NULLS FIRST, u.name
        """, (g.user["id"],)).fetchall()
        client_view = False

    elif role in ("Administrador", "Soporte TI"):
        rows = db().execute("""
            SELECT
                u.id, u.tech_code, u.name, u.email, u.phone, u.position,
                u.role, u.area, c.name company
            FROM users u
            LEFT JOIN companies c ON c.id = u.company_id
            WHERE u.active
            ORDER BY c.name NULLS FIRST, u.name
        """).fetchall()
        client_view = False

    else:
        abort(403)

    return rows, client_view


@app.route("/contacto")
@login_required
def contacto():
    rows, client_view = _directory_rows()
    contact_companies = sorted({r["company"] for r in rows if r["company"]})
    return render_template(
        "contact.html",
        contacts=rows,
        contact_companies=contact_companies,
        client_view=client_view
    )


# ===== API REST /api/v1 (inicio) =====
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import jsonify

TICKET_SQL = ("SELECT t.*,COALESCE(u.name,t.creator_name,'Cuenta eliminada') creator,co.name company,tech.name technician FROM tickets t "
              "LEFT JOIN users u ON u.id=t.created_by LEFT JOIN companies co ON co.id=t.company_id "
              "LEFT JOIN users tech ON tech.id=t.assigned_to")

def _ser(): return URLSafeTimedSerializer(app.secret_key, salt="api-token")

def _user_json(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"],
            "area": u["area"], "company_id": u["company_id"], "tech_code": u.get("tech_code")}

APP_BLOCKED_ROLE = "Usuario"
APP_BLOCKED_MSG = "La app es solo para el personal técnico. Ingresa a la página web con tu cuenta de cliente."


def api_auth(fn):
    @wraps(fn)
    def w(*a, **k):
        h = request.headers.get("Authorization", "")
        if not h.startswith("Bearer "): return jsonify(error="No autenticado"), 401
        try: uid = _ser().loads(h[7:], max_age=60*60*24*7)["uid"]
        except (BadSignature, SignatureExpired): return jsonify(error="Sesión vencida"), 401
        user = db().execute("SELECT * FROM users WHERE id=%s AND active", (uid,)).fetchone()
        if not user: return jsonify(error="Cuenta inactiva"), 401
        if user["role"] == APP_BLOCKED_ROLE: return jsonify(error=APP_BLOCKED_MSG), 403
        g.user = user
        return fn(*a, **k)
    return w


def _scope():
    u = g.user

    if u["role"] == "Usuario":
        # igual que en la web: el cliente ve los tickets de su empresa (si no tiene empresa, solo los que creó)
        if u["company_id"]:
            return ("(t.company_id = %s OR t.created_by = %s)", [u["company_id"], u["id"]])
        return ("t.created_by = %s", [u["id"]])

    if u["role"] == "Técnico":
        return ("t.assigned_to = %s", [u["id"]])

    return ("1=1", [])


def _get_ticket(tid):
    w, a = _scope()
    return db().execute(TICKET_SQL + f" WHERE t.id=%s AND {w}", [tid] + a).fetchone()

@app.after_request
def api_cors(resp):
    if request.path.startswith("/api/"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp

@app.post("/api/v1/login")
def api_login():
    d = request.get_json(silent=True) or {}
    email = (d.get("email") or "").strip().lower()
    user = db().execute("SELECT * FROM users WHERE email=%s AND active", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], d.get("password") or ""):
        return jsonify(error="Correo o contraseña incorrectos"), 401
    if user["role"] == APP_BLOCKED_ROLE:
        return jsonify(error=APP_BLOCKED_MSG), 403
    return jsonify(token=_ser().dumps({"uid": user["id"]}), user=_user_json(user))

@app.get("/api/v1/directory")
@api_auth
def api_directory():
    rows, client_view = _directory_rows()
    return jsonify(contacts=[dict(r) for r in rows], client_view=client_view)


@app.post("/api/v1/me/password")
@api_auth
def api_me_password():
    d = request.get_json(silent=True) or {}
    cur, new = d.get("current") or "", d.get("new") or ""
    row = db().execute("SELECT password_hash FROM users WHERE id=%s", (g.user["id"],)).fetchone()
    if not check_password_hash(row["password_hash"], cur): return jsonify(error="La contraseña actual no es correcta"), 400
    if len(new) < 10: return jsonify(error="La nueva contraseña debe tener al menos 10 caracteres"), 400
    db().execute("UPDATE users SET password_hash=%s WHERE id=%s", (generate_password_hash(new), g.user["id"])); db().commit()
    return jsonify(ok=True)


@app.get("/api/v1/me")
@api_auth
def api_me(): return jsonify(_user_json(g.user))

@app.get("/api/v1/dashboard")
@api_auth
def api_dashboard():
    w, a = _scope()
    rows = db().execute(f"SELECT t.status s,COUNT(*) n FROM tickets t WHERE {w} GROUP BY t.status", a).fetchall()
    counts = {s: 0 for s in STATUSES}
    counts.update({r["s"]: r["n"] for r in rows})
    return jsonify(counts=counts)

@app.get("/api/v1/tickets")
@api_auth
def api_tickets():
    w, a = _scope()
    sql = TICKET_SQL + f" WHERE {w}"
    q = request.args.get("q", "").strip(); status = request.args.get("status", "")
    if q: sql += " AND (t.title ILIKE %s OR CAST(t.id AS TEXT)=%s)"; a += ["%" + q + "%", search_id(q)]
    if status in STATUSES: sql += " AND t.status=%s"; a.append(status)
    elif status == "finalizados": sql += " AND t.status IN ('Resuelto','Cerrado')"
    sql += " ORDER BY EXTRACT(YEAR FROM t.created_at) DESC, t.id DESC LIMIT 100"
    return jsonify([with_code(r) for r in db().execute(sql, a).fetchall()])

@app.get("/api/v1/tickets/<int:tid>")
@api_auth
def api_ticket(tid):
    t = _get_ticket(tid)
    if not t: return jsonify(error="No encontrado"), 404
    ups = db().execute("SELECT tu.note,tu.status,tu.created_at,COALESCE(u.name,tu.user_name,'Cuenta eliminada') AS name FROM ticket_updates tu "
                       "LEFT JOIN users u ON u.id=tu.user_id WHERE tu.ticket_id=%s ORDER BY tu.created_at DESC, tu.id DESC", (tid,)).fetchall()
    return jsonify(ticket=with_code(t), updates=[dict(r) for r in ups])

@app.post("/api/v1/tickets")
@api_auth
def api_ticket_create():
    d = request.get_json(silent=True) or {}
    title = (d.get("title") or "").strip(); desc = (d.get("description") or "").strip()
    if not title or not desc: return jsonify(error="Asunto y descripción son obligatorios"), 400
    u = g.user; client = u["role"] == "Usuario"
    priority = "Media" if client else d.get("priority", "Media")
    if priority not in PRIORITIES: priority = "Media"
    category = "Soporte usuario" if client else d.get("category", "Soporte usuario")
    if category not in CATEGORIES: category = "Soporte usuario"
    raw_company = None if client else d.get("company_id")
    company_id = u["company_id"] if client else to_int(raw_company)
    if client and not company_id: return jsonify(error="Tu cuenta no está vinculada a una empresa"), 400
    c = db()
    if (raw_company and company_id is None) or (company_id and not c.execute("SELECT id FROM companies WHERE id=%s", (company_id,)).fetchone()):
        return jsonify(error="Empresa no válida"), 400
    rh, sh = SLA[priority]; now = datetime.utcnow()
    cur = c.execute("INSERT INTO tickets(title,description,category,priority,company_id,created_by,area,response_due,solution_due) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, created_at",
                    (title, desc, category, priority, company_id, u["id"], "Soporte TI",
                     (now + timedelta(hours=rh)).isoformat(timespec="seconds"),
                     (now + timedelta(hours=sh)).isoformat(timespec="seconds")))
    row = cur.fetchone(); new_id = row["id"]
    c.execute("UPDATE tickets SET creator_name=%s WHERE id=%s", (u["name"], new_id))
    c.execute("INSERT INTO ticket_updates(ticket_id,user_id,user_name,note,status) VALUES(%s,%s,%s,%s,%s)", (new_id, u["id"], u["name"], "Ticket creado.", "Abierto"))
    _notify_new_ticket(c, new_id, title, priority, row["created_at"])
    c.commit()
    return jsonify(id=new_id, code=ticket_code(new_id, row["created_at"])), 201

@app.post("/api/v1/tickets/<int:tid>/notes")
@api_auth
def api_ticket_note(tid):
    t = _get_ticket(tid)
    if not t: return jsonify(error="No encontrado"), 404
    d = request.get_json(silent=True) or {}
    note = (d.get("note") or "").strip()
    if not note: return jsonify(error="La nota es obligatoria"), 400
    status = t["status"]
    if g.user["role"] in ("Administrador", "Soporte TI", "Técnico") and d.get("status") in STATUSES:
        status = d["status"]
    resolved = datetime.utcnow().isoformat(timespec="seconds") if status in ("Resuelto", "Cerrado") else None
    c = db()
    c.execute("INSERT INTO ticket_updates(ticket_id,user_id,user_name,note,status) VALUES(%s,%s,%s,%s,%s)", (tid, g.user["id"], g.user["name"], note, status))
    c.execute("UPDATE tickets SET status=%s,updated_at=CURRENT_TIMESTAMP,resolved_at=COALESCE(%s,resolved_at) WHERE id=%s", (status, resolved, tid))
    _notify_followup(c, tid, t["status"], status, note)
    c.commit()
    return jsonify(ok=True), 201

# ===== API MOVIL: ADMINISTRACION =====

def api_error(message, status=400):
    return jsonify(error=message), status


@app.post("/api/v1/users/<int:user_id>/delete")
@api_auth
def api_users_delete(user_id):
    if g.user["role"] != "Administrador":
        return api_error(
            "Solo el administrador puede retirar usuarios", 403
        )

    if user_id == g.user["id"]:
        return api_error(
            "No puedes retirar tu propia cuenta", 403
        )

    c = db()

    target = c.execute(
        "SELECT id, role, active FROM users WHERE id = %s",
        (user_id,)
    ).fetchone()

    if not target:
        return api_error("Usuario no encontrado", 404)

    # Evita desactivar al último administrador activo.
    if target["role"] == "Administrador" and target["active"]:
        active_admins = c.execute("""
            SELECT COUNT(*) AS n
            FROM users
            WHERE role = 'Administrador' AND active
        """).fetchone()["n"]

        if active_admins <= 1:
            return api_error(
                "No puedes retirar al último administrador activo", 403
            )

    c.execute(
        "UPDATE users SET active = FALSE WHERE id = %s",
        (user_id,)
    )
    c.commit()

    return jsonify(
        ok=True,
        message="Usuario retirado. Su historial se conserva."
    )

@app.post("/api/v1/users/<int:user_id>/purge")
@api_auth
def api_users_purge(user_id):
    if g.user["role"] != "Administrador":
        return api_error("Solo el administrador puede eliminar cuentas", 403)
    ok, msg = purge_user(db(), user_id)
    return (jsonify(ok=True, message=msg) if ok else api_error(msg, 400))

@app.get("/api/v1/users")
@api_auth
def api_users_list():
    if g.user["role"] != "Administrador":
        return api_error("No tienes permisos para gestionar usuarios", 403)

    rows = db().execute("""
        SELECT u.id, u.name, u.email, u.role, u.area, u.active::int AS active,
               u.created_at, u.company_id, u.phone, u.position, u.tech_code, c.name AS company
        FROM users u
        LEFT JOIN companies c ON c.id = u.company_id
        ORDER BY u.name
    """).fetchall()

    return jsonify([dict(row) for row in rows])


@app.post("/api/v1/users")
@api_auth
def api_users_create():
    if g.user["role"] != "Administrador":
        return api_error("Solo el administrador puede crear usuarios", 403)

    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    email = (d.get("email") or "").strip().lower()
    password = d.get("password") or ""
    phone = (d.get("phone") or "").strip()
    role = d.get("role", "Usuario")
    area = d.get("area", "Cliente")
    raw_company = d.get("company_id") or None
    company_id = to_int(raw_company)

    if not name or not email or len(password) < 10:
        return api_error("Ingresa nombre, correo y contraseña de mínimo 10 caracteres")

    if role not in ROLES:
        return api_error("El rol seleccionado no es válido")

    if role == "Usuario":
        area = "Cliente"
        if not company_id:
            return api_error("Selecciona una empresa para el usuario")
    elif role == "Técnico" and area not in AREAS:
        return api_error("Selecciona un área válida para el técnico")
    elif role in ("Administrador", "Soporte TI") and area not in AREAS:
        return api_error("Selecciona un área válida")

    if raw_company:
        if company_id is None or not db().execute(
            "SELECT id FROM companies WHERE id=%s", (company_id,)
        ).fetchone():
            return api_error("La empresa seleccionada no existe")

    c = db()
    try:
        cur = c.execute("""
            INSERT INTO users
                (name, email, password_hash, role, area, company_id, phone)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            name, email, generate_password_hash(password),
            role, area, company_id, phone
        ))
        c.commit()
        return jsonify(ok=True, id=cur.fetchone()["id"]), 201
    except psycopg2.IntegrityError:
        c.rollback()
        return api_error("Ese correo ya está registrado")


@app.post("/api/v1/users/<int:user_id>/edit")
@api_auth
def api_users_edit(user_id):
    if g.user["role"] != "Administrador":
        return api_error("No tienes permisos para editar usuarios", 403)

    d = request.get_json(silent=True) or {}
    c = db()
    target = c.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
    if not target:
        return api_error("Usuario no encontrado", 404)

    name = (d.get("name") or target["name"]).strip()
    email = (d.get("email") or target["email"]).strip().lower()
    phone = (d.get("phone") if "phone" in d else target["phone"] or "").strip()
    position = (d.get("position") if "position" in d else target["position"] or "").strip()
    role = d.get("role") or target["role"]
    area = (d.get("area") or target["area"] or "Cliente").strip()
    if "company_id" in d:
        raw_company = d.get("company_id") or None
        company_id = to_int(raw_company)
        if raw_company and (company_id is None or not c.execute("SELECT id FROM companies WHERE id=%s", (company_id,)).fetchone()):
            return api_error("La empresa seleccionada no existe")
    else:
        company_id = target["company_id"]
    password = d.get("password")

    if not name or not email:
        return api_error("Nombre y correo son obligatorios")

    if role not in ROLES:
        return api_error("El rol no es válido")

    if user_id == g.user["id"] and role != "Administrador":
        return api_error("No puedes quitarte tu propio rol administrativo", 403)

    if c.execute("SELECT id FROM users WHERE email=%s AND id!=%s", (email, user_id)).fetchone():
        return api_error("Ese correo ya está registrado en otra cuenta")

    if role == "Usuario":
        area = "Cliente"
    elif area not in AREAS:
        return api_error("El área no es válida")

    sql = "UPDATE users SET name=%s, email=%s, phone=%s, position=%s, role=%s, area=%s, company_id=%s"
    params = [name, email, phone, position, role, area, company_id]
    if password:
        if len(password) < 10:
            return api_error("La contraseña debe tener al menos 10 caracteres")
        sql += ", password_hash=%s"
        params.append(generate_password_hash(password))
    sql += " WHERE id=%s"
    params.append(user_id)

    try:
        c.execute(sql, params)
        c.commit()
        extras.refresh_code_on_role_change(c, user_id, target["role"], role)
        extras.assign_codes(c)
    except psycopg2.IntegrityError:
        c.rollback()
        return api_error("No fue posible actualizar el usuario")
    return jsonify(ok=True)


@app.post("/api/v1/users/<int:user_id>/toggle")
@api_auth
def api_users_toggle(user_id):
    if g.user["role"] != "Administrador":
        return api_error("No tienes permisos para cambiar cuentas", 403)

    if user_id == g.user["id"]:
        return api_error("No puedes deshabilitar tu propia cuenta", 403)

    c = db()
    target = c.execute(
        "SELECT id, role, active FROM users WHERE id=%s", (user_id,)
    ).fetchone()

    if not target:
        return api_error("Usuario no encontrado", 404)

    if target["role"] == "Administrador" and target["active"]:
        active_admins = c.execute("""
            SELECT COUNT(*) AS n FROM users
            WHERE role='Administrador' AND active
        """).fetchone()["n"]

        if active_admins <= 1:
            return api_error("No puedes deshabilitar al último administrador activo", 403)

    c.execute(
        "UPDATE users SET active=%s WHERE id=%s",
        (not target["active"], user_id)
    )
    c.commit()
    return jsonify(ok=True, active=not bool(target["active"]))


# ===== API MOVIL: EMPRESAS =====

@app.get("/api/v1/companies")
@api_auth
def api_companies_list():
    if g.user["role"] not in ("Administrador", "Soporte TI"):
        return api_error("No tienes permisos para consultar empresas", 403)

    rows = db().execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM users u
                WHERE u.company_id=c.id AND u.active) AS active_users,
               (SELECT COUNT(*) FROM tickets t
                WHERE t.company_id=c.id) AS ticket_count
        FROM companies c
        ORDER BY c.name
    """).fetchall()

    return jsonify([dict(row) for row in rows])


@app.post("/api/v1/companies")
@api_auth
def api_companies_create():
    if g.user["role"] not in ("Administrador", "Soporte TI"):
        return api_error("No tienes permisos para registrar empresas", 403)

    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    email = (d.get("email") or "").strip().lower()
    nit = (d.get("nit") or "").strip() or None

    if not name or not email:
        return api_error("El nombre y correo son obligatorios")

    c = db()

    if nit and c.execute(
        "SELECT id FROM companies WHERE nit=%s", (nit,)
    ).fetchone():
        return api_error("Ya existe una empresa con ese NIT")

    try:
        cur = c.execute("""
            INSERT INTO companies
                (name, nit, email, phone, address, city, contact_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            name, nit, email,
            (d.get("phone") or "").strip(),
            (d.get("address") or "").strip(),
            (d.get("city") or "").strip(),
            (d.get("contact_name") or "").strip()
        ))
        c.commit()
        return jsonify(ok=True, id=cur.fetchone()["id"]), 201
    except psycopg2.IntegrityError:
        c.rollback()
        return api_error("No fue posible registrar la empresa")


@app.post("/api/v1/companies/<int:company_id>/edit")
@api_auth
def api_companies_edit(company_id):
    if g.user["role"] not in ("Administrador", "Soporte TI"):
        return api_error("No tienes permisos para editar empresas", 403)

    c = db()
    company = c.execute("SELECT * FROM companies WHERE id=%s", (company_id,)).fetchone()
    if not company:
        return api_error("Empresa no encontrada", 404)

    d = request.get_json(silent=True) or {}
    name = (d.get("name") or company["name"]).strip()
    email = (d.get("email") or company["email"]).strip().lower()
    nit = (d.get("nit") if "nit" in d else company["nit"] or "").strip() or None

    if not name or not email:
        return api_error("El nombre y correo son obligatorios")

    if nit and c.execute("SELECT id FROM companies WHERE nit=%s AND id!=%s", (nit, company_id)).fetchone():
        return api_error("Ya existe otra empresa con ese NIT")

    try:
        c.execute("""
            UPDATE companies
            SET name=%s, nit=%s, email=%s, phone=%s, address=%s, city=%s, contact_name=%s
            WHERE id=%s
        """, (
            name, nit, email,
            (d.get("phone") if "phone" in d else company["phone"] or "").strip(),
            (d.get("address") if "address" in d else company["address"] or "").strip(),
            (d.get("city") if "city" in d else company["city"] or "").strip(),
            (d.get("contact_name") if "contact_name" in d else company["contact_name"] or "").strip(),
            company_id
        ))
        c.commit()
        return jsonify(ok=True)
    except psycopg2.IntegrityError:
        c.rollback()
        return api_error("No fue posible actualizar la empresa")


# ===== API MOVIL: ASIGNACION DE TECNICOS =====

@app.get("/api/v1/assignments")
@api_auth
def api_assignments_list():
    if g.user["role"] not in ("Administrador", "Soporte TI"):
        return api_error("No tienes permisos para gestionar asignaciones", 403)

    tickets = db().execute("""
        SELECT t.id, t.created_at, t.title, t.status, t.priority, t.area,
               t.assigned_to, co.name AS company,
               creator.name AS creator,
               tech.name AS technician
        FROM tickets t
        JOIN users creator ON creator.id=t.created_by
        LEFT JOIN companies co ON co.id=t.company_id
        LEFT JOIN users tech ON tech.id=t.assigned_to
        ORDER BY EXTRACT(YEAR FROM t.created_at) DESC, t.id DESC
        LIMIT 200
    """).fetchall()

    technicians = db().execute("""
        SELECT id, name, area, tech_code
        FROM users
        WHERE active AND role='Técnico'
        ORDER BY name
    """).fetchall()

    return jsonify(
        tickets=[with_code(row) for row in tickets],
        technicians=[dict(row) for row in technicians],
        areas=AREAS
    )


@app.post("/api/v1/assignments")
@api_auth
def api_assignments_update():
    if g.user["role"] not in ("Administrador", "Soporte TI"):
        return api_error("No tienes permisos para asignar tickets", 403)

    d = request.get_json(silent=True) or {}
    ticket_id = to_int(d.get("ticket_id"))
    area = d.get("area", "Soporte TI")
    raw_tech = d.get("technician_id") or None
    technician_id = to_int(raw_tech)

    if not ticket_id:
        return api_error("Debes indicar el ticket")

    if area not in AREAS:
        return api_error("El área seleccionada no es válida")

    c = db()
    ticket = c.execute(
        "SELECT id FROM tickets WHERE id=%s", (ticket_id,)
    ).fetchone()

    if not ticket:
        return api_error("Ticket no encontrado", 404)

    if raw_tech:
        technician = c.execute("""
            SELECT id FROM users
            WHERE id=%s AND active AND role='Técnico' AND area=%s
        """, (technician_id, area)).fetchone() if technician_id else None

        if not technician:
            return api_error(
                "El técnico debe estar activo y pertenecer al área seleccionada"
            )

    c.execute("""
        UPDATE tickets
        SET area=%s, assigned_to=%s, updated_at=CURRENT_TIMESTAMP
        WHERE id=%s
    """, (area, technician_id, ticket_id))

    _log_assignment(c, ticket_id, area, technician_id)

    c.commit()
    return jsonify(ok=True)

# ===== API REST /api/v1 (fin) =====

# Módulos adicionales: actividad en vivo, chatbot, soporte remoto SSH y descarga de la app
extras.setup(app, db, api_auth, roles_required, ticket_code)
payroll.setup(app, db, roles_required)
catalog.setup(app, db, roles_required)

@app.errorhandler(psycopg2.DataError)
def bad_data(e):
    db().rollback()
    if request.path.startswith("/api/"): return jsonify(error="Datos no válidos"), 400
    return render_template("message.html",title="Datos no válidos",message="Alguno de los datos enviados no es válido."),400

@app.errorhandler(403)
def forbidden(e): return render_template("message.html",title="Acceso restringido",message="Tu rol no tiene permisos para ver esta interfaz."),403
@app.errorhandler(404)
def not_found(e): return render_template("message.html",title="No encontrado",message="No encontramos la página o el registro solicitado."),404

if __name__=="__main__": app.run(host="0.0.0.0", debug=os.environ.get("FLASK_DEBUG")=="1")
