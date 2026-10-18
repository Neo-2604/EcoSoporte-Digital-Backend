"""Datos de demostración: más empresas, usuarios, técnicos y tickets con historial coherente.

Es idempotente: si ya existen las empresas de demostración no vuelve a cargar nada.
Se puede usar desde la web (Usuarios y roles → «Cargar datos de demostración») o por consola:
    DATABASE_URL=... python demo_data.py
Las cuentas de demostración usan el dominio @demo.ecosoporte.co y la contraseña se genera al cargar.
"""
import os
import random
import secrets
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

DEMO_DOMAIN = "demo.ecosoporte.co"

COMPANIES = [
    ("Distribuidora Andina S.A.S.", "Bogotá", "Camila Rojas"),
    ("Clínica Santa Aurora", "Medellín", "Julián Pardo"),
    ("Agroexportadora del Valle", "Cali", "Marcela Ortiz"),
    ("Textiles La Ceiba", "Barranquilla", "Andrés Camargo"),
    ("Colegio Nueva Generación", "Bucaramanga", "Paola Mejía"),
    ("Constructora Horizonte", "Bogotá", "Sergio Valencia"),
    ("Farmacias Vida Plena", "Cartagena", "Natalia Herrera"),
    ("Logística Express 24", "Pereira", "Diego Alarcón"),
]
CLIENT_NAMES = [
    "Laura Gómez", "Mateo Rincón", "Valentina Cruz", "Santiago Duarte", "Isabela Mora", "Felipe Salazar",
    "Daniela Pineda", "Nicolás Cárdenas", "Sofía Beltrán", "Tomás Quintero", "Mariana Lozano", "Esteban Vargas",
    "Juliana Franco", "Camilo Arango", "Lucía Medina", "Alejandro Peña",
]
TECHS = [  # nombre, área, cargo
    ("Carlos Andrés Rozo Gallego", "Soporte TI", "Técnico de soporte N2"),
    ("Diana Marcela Pérez Torres", "Soporte TI", "Técnica de mesa de ayuda"),
    ("Jorge Iván Castaño Ruiz", "Infraestructura", "Técnico de redes"),
    ("Paula Andrea Vélez Soto", "Seguridad", "Analista de seguridad"),
    ("Hernán David Ospina Cano", "Aplicaciones", "Técnico de aplicaciones"),
    ("Luz Marina Aguirre Prieto", "Datos", "Técnica de datos y respaldo"),
]
TITLES = {  # categoría → asuntos posibles; el área sale de la categoría
    "Hardware": ["Laptop no enciende", "Pantalla con líneas verticales", "Teclado con teclas sin respuesta", "Equipo se apaga solo"],
    "Software": ["Excel se cierra al abrir un archivo", "Licencia de Office vencida", "Falla la actualización de Windows", "Instalar programa de diseño"],
    "Redes": ["Sin internet en la sede principal", "WiFi intermitente en el piso 2", "La VPN no conecta", "Cambio de punto de red"],
    "Seguridad": ["Correo sospechoso recibido", "Cuenta bloqueada por intentos fallidos", "Instalar antivirus corporativo", "Revisión de accesos"],
    "Aplicaciones": ["El ERP responde lento al facturar", "Error al exportar el reporte mensual", "Solicitud de acceso al CRM", "Módulo de inventario no abre"],
    "Periféricos": ["Impresora con papel atascado", "Escáner no reconocido", "Mouse inalámbrico sin conexión", "Cambio de tóner"],
    "Sistemas": ["Servidor de archivos sin espacio", "Copia de seguridad fallida", "Crear usuario nuevo en el dominio", "Restaurar carpeta compartida"],
    "Soporte usuario": ["Capacitación en herramientas de oficina", "Cambio de contraseña", "Configurar correo en el móvil", "Ayuda con videollamadas"],
}
AREA_OF = {"Hardware": "Soporte TI", "Software": "Soporte TI", "Periféricos": "Soporte TI", "Soporte usuario": "Soporte TI",
           "Redes": "Infraestructura", "Sistemas": "Infraestructura", "Seguridad": "Seguridad", "Aplicaciones": "Aplicaciones"}
NOTES_PROGRESS = ["Revisión inicial realizada en sitio.", "Se solicitó información adicional al usuario.",
                  "Se aplicó la corrección y se está probando.", "Pendiente de repuesto o autorización del cliente."]
NOTES_DONE = ["Problema solucionado y verificado con el usuario.", "Caso resuelto; se dejó documentado el procedimiento."]
SLA = {"Crítica": (1, 4), "Alta": (4, 8), "Media": (8, 24), "Baja": (24, 48)}


def _label(name, code):
    return f"{name} ({code})" if code else name


def load_demo(c, assign_codes, password=None, tickets=64, seed=7):
    """Carga los datos. `c` es la conexión de app.py (execute con %s). Devuelve (resumen, contraseña usada)."""
    if c.execute("SELECT 1 AS x FROM companies WHERE nit LIKE 'DEMO-%%' LIMIT 1").fetchone():
        return {"empresas": 0, "clientes": 0, "tecnicos": 0, "tickets": 0, "ya_existian": True}, None
    for tabla in ("companies", "users", "tickets", "ticket_updates"):  # por si hubo filas con ID explícito (semillas)
        c.execute(f"SELECT setval(pg_get_serial_sequence('{tabla}','id'), COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM {tabla}")
    rnd = random.Random(seed)
    password = password or secrets.token_urlsafe(10) + "9a"
    pw_hash = generate_password_hash(password)

    company_ids = []
    for i, (name, city, contact) in enumerate(COMPANIES, 1):
        slug = name.lower().split()[0].replace(".", "")
        cid = c.execute(
            "INSERT INTO companies(name,nit,email,phone,address,city,contact_name) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (name, f"DEMO-9{i:02d}00{i}-{i}", f"contacto@{slug}.demo.ecosoporte.co", f"601 55{i}0 1{i}0{i}",
             f"Calle {10 + i} # {20 + i}-{30 + i}", city, contact)).fetchone()["id"]
        company_ids.append(cid)

    clients = {}  # empresa → [(id, nombre)]
    for i, name in enumerate(CLIENT_NAMES):
        cid = company_ids[i % len(company_ids)]
        email = f"{name.lower().replace(' ', '.').translate(str.maketrans('áéíóúñ', 'aeioun'))}@{DEMO_DOMAIN}"
        uid = c.execute(
            "INSERT INTO users(name,email,password_hash,role,area,company_id,phone) VALUES(%s,%s,%s,'Usuario','Cliente',%s,%s) RETURNING id",
            (name, email, pw_hash, cid, f"300 {100 + i:03d} {2000 + i * 7}")).fetchone()["id"]
        clients.setdefault(cid, []).append((uid, name))

    techs = {}  # área → [(id, nombre)]
    for i, (name, area, position) in enumerate(TECHS):
        email = f"{name.split()[0].lower()}.{name.split()[-2].lower()}@{DEMO_DOMAIN}".translate(str.maketrans("áéíóúñ", "aeioun"))
        uid = c.execute(
            "INSERT INTO users(name,email,password_hash,role,area,phone,position) VALUES(%s,%s,%s,'Técnico',%s,%s,%s) RETURNING id",
            (name, email, pw_hash, area, f"310 {200 + i:03d} {4000 + i * 11}", position)).fetchone()["id"]
        techs.setdefault(area, []).append((uid, name))
    assign_codes(c)  # ID legible (TEC-CG-01…)

    code_of = {r["id"]: r["tech_code"] for r in c.execute("SELECT id, tech_code FROM users WHERE email LIKE %s", (f"%@{DEMO_DOMAIN}",)).fetchall()}
    admin = c.execute("SELECT id, name FROM users WHERE role IN ('Administrador','Soporte TI') AND active ORDER BY (role='Administrador') DESC, id LIMIT 1").fetchone()

    now = datetime.utcnow()
    created_tickets = 0
    cats = list(TITLES)
    for n in range(tickets):
        cid = rnd.choice(company_ids)
        creator_id, creator = rnd.choice(clients[cid])
        cat = rnd.choice(cats)
        title = rnd.choice(TITLES[cat])
        priority = rnd.choices(["Baja", "Media", "Alta", "Crítica"], [3, 5, 3, 1])[0]
        area = AREA_OF[cat]
        created = now - timedelta(days=rnd.randint(0, 110), hours=rnd.randint(0, 20), minutes=rnd.randint(0, 59))
        status = rnd.choices(["Abierto", "En proceso", "En espera", "Resuelto", "Cerrado"], [3, 3, 2, 4, 3])[0]
        # los tickets recién creados sin técnico siguen "Abiertos"; el resto ya tiene responsable de su área
        tech = rnd.choice(techs[area]) if (status != "Abierto" or rnd.random() < 0.4) and techs.get(area) else None
        if tech is None and status != "Abierto":
            status = "Abierto"
        rh, sh = SLA[priority]
        resolved = created + timedelta(hours=rnd.randint(2, sh + 6)) if status in ("Resuelto", "Cerrado") else None
        updated = resolved or (created + timedelta(hours=rnd.randint(1, 30)))
        tid = c.execute(
            "INSERT INTO tickets(title,description,category,priority,status,company_id,created_by,creator_name,assigned_to,area,"
            "created_at,updated_at,response_due,solution_due,resolved_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (title, f"{title}. Reportado por {creator}; requiere atención de {area}.", cat, priority, status, cid, creator_id, creator,
             tech[0] if tech else None, area, created, updated, created + timedelta(hours=rh), created + timedelta(hours=sh), resolved)).fetchone()["id"]

        def note(text, st, who_id, who_name, at):
            c.execute("INSERT INTO ticket_updates(ticket_id,user_id,user_name,note,status,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
                      (tid, who_id, who_name, text, st, at))
        note("Ticket creado.", "Abierto", creator_id, creator, created)
        if tech:
            t = created + timedelta(minutes=rnd.randint(10, 90))
            note(f"Asignado a {_label(tech[1], code_of.get(tech[0]))}; área {area}.", None, admin["id"], admin["name"], t)
            if status in ("En proceso", "En espera", "Resuelto", "Cerrado"):
                t += timedelta(hours=rnd.randint(1, 8))
                note(rnd.choice(NOTES_PROGRESS), "En proceso", tech[0], tech[1], t)
            if status == "En espera":
                note("Pendiente de repuesto o autorización del cliente.", "En espera", tech[0], tech[1], t + timedelta(hours=2))
            if status in ("Resuelto", "Cerrado"):
                note(rnd.choice(NOTES_DONE), "Resuelto", tech[0], tech[1], resolved)
            if status == "Cerrado":
                note("Ticket cerrado tras confirmación del cliente.", "Cerrado", admin["id"], admin["name"], resolved + timedelta(hours=3))
        created_tickets += 1
    c.commit()
    return {"empresas": len(company_ids), "clientes": len(CLIENT_NAMES), "tecnicos": len(TECHS), "tickets": created_tickets, "ya_existian": False}, password


if __name__ == "__main__":  # uso por consola
    import app as application
    with application.app.app_context():
        application.init_db()
        resumen, clave = load_demo(application.db(), application.extras.assign_codes, os.environ.get("DEMO_PASSWORD"))
        print(resumen)
        if clave:
            print("Contraseña de las cuentas de demostración:", clave)
