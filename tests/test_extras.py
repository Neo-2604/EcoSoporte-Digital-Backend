"""Pruebas de los módulos: actividad, chatbot, solicitudes y soporte remoto (PostgreSQL de pruebas)."""
import os
import unittest

import psycopg2
from werkzeug.security import generate_password_hash
import app as application

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")


@unittest.skipUnless("test" in TEST_URL.lower(), "Define TEST_DATABASE_URL (con 'test' en el nombre)")
class ExtrasTests(unittest.TestCase):
    def setUp(self):
        conn = psycopg2.connect(TEST_URL); conn.autocommit = True
        conn.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"); conn.close()
        os.environ.pop("ADMIN_EMAIL", None); os.environ.pop("ADMIN_PASSWORD", None)
        application.app.config.update(TESTING=True, DATABASE_URL=TEST_URL, SECRET_KEY="test-secret")
        application._schema_ready = False
        with application.app.app_context():
            application.init_db()
            c = application.db()
            c.execute("INSERT INTO companies(id,name,email) VALUES(1,'Alfa','a@example.test')")
            h = generate_password_hash("password-test")
            for i, (n, r) in enumerate([("Admin", "Administrador"), ("Soporte", "Soporte TI"), ("Tec", "Técnico"), ("Cli", "Usuario")], 1):
                c.execute("INSERT INTO users(id,name,email,password_hash,role,area,active,company_id) VALUES(%s,%s,%s,%s,%s,'Soporte TI',TRUE,%s)",
                          (i, n, f"u{i}@example.test", h, r, 1 if r == "Usuario" else None))
            c.execute("INSERT INTO tickets(id,title,description,category,priority,status,company_id,created_by,assigned_to,area) VALUES(101,'T','D','Red','Media','En proceso',1,4,3,'Soporte TI')")
            c.commit()
        self.client = application.app.test_client()

    def login(self, uid):
        with self.client.session_transaction() as s:
            s["user_id"] = uid

    def test_chatbot_saves_lead_and_support_sees_it(self):
        t = self.client.post("/api/chat/start").get_json()["token"]
        for txt in ["Quiero comprar componentes", "Memoria RAM 16GB", "Ana Pérez", "3001234567", "ana@example.test"]:
            r = self.client.post("/api/chat/message", json={"token": t, "text": txt})
            self.assertEqual(r.status_code, 200)
        self.login(2)
        self.assertEqual(self.client.get("/solicitudes").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/leads").status_code, 200)

    def test_roles_are_enforced(self):
        self.login(4)
        for url in ["/solicitudes", "/actividad", "/remoto", "/app-tecnicos"]:
            self.assertIn(self.client.get(url).status_code, (302, 403), url)
        self.login(1)
        for url in ["/solicitudes", "/actividad", "/remoto", "/remoto/equipos", "/app-tecnicos", "/dashboard", "/politicas"]:
            self.assertIn(self.client.get(url).status_code, (200, 302), url)

    def test_presence_and_activity(self):
        r = self.client.post("/api/v1/ping", json={"sid": "abcdefgh12345", "kind": "web", "path": "/", "view": True})
        self.assertEqual(r.status_code, 200)
        self.login(1)
        self.assertEqual(self.client.get("/api/v1/activity").status_code, 200)
        self.assertEqual(self.client.get("/actividad").status_code, 200)

    def test_remote_flow(self):
        self.login(2)
        r = self.client.post("/api/v1/remote/hosts", json={"name": "PC-01", "address": "10.0.0.5", "ssh_user": "soporte", "company_id": 1})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        hid = self.client.get("/api/v1/remote/hosts").get_json()[0]["id"]
        self.login(3)
        sid = self.client.post("/api/v1/remote/sessions", json={"host_id": hid, "ticket_id": 101, "reason": "No hay red en el equipo"}).get_json()["id"]
        self.assertEqual(self.client.post(f"/api/v1/remote/sessions/{sid}/start").status_code, 400)
        self.login(2)
        self.assertEqual(self.client.post(f"/api/v1/remote/sessions/{sid}/approve").status_code, 200)
        self.login(3)
        self.assertEqual(self.client.post(f"/api/v1/remote/sessions/{sid}/start").status_code, 200)
        self.assertEqual(self.client.get(f"/remoto/{sid}").status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/remote/sessions/{sid}/close", json={"summary": "Se renovó la IP del equipo"}).status_code, 200)
        self.assertEqual(self.client.get("/remoto").status_code, 200)

    def test_remote_escalation_and_bad_host(self):
        self.login(1)
        self.assertEqual(self.client.post("/api/v1/remote/hosts", json={"name": "x", "address": "a b;rm", "ssh_user": "root"}).status_code, 400)

    def test_politicas_dentro_de_la_sesion_y_un_solo_nuevo_ticket(self):
        self.assertEqual(self.client.get("/mi-cuenta/politicas").status_code, 302)  # sin sesión → login
        for uid in (1, 4):
            self.login(uid)
            r = self.client.get("/mi-cuenta/politicas")
            self.assertEqual(r.status_code, 200)
            html = r.get_data(as_text=True)
            self.assertIn("Soporte remoto (SSH)", html)
            self.assertIn('id="profileBtn"', html)  # sigue el menú de la sesión
            for url in ("/dashboard", "/tickets"):
                page = self.client.get(url).get_data(as_text=True)
                self.assertEqual(page.count("Crear ticket"), 0, url)
                self.assertEqual(page.count("Nuevo ticket"), 1, url)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless("test" in TEST_URL.lower(), "Define TEST_DATABASE_URL (con 'test' en el nombre)")
class ProfileTests(ExtrasTests):
    def setUp(self):
        super().setUp()
        with application.app.app_context():
            c = application.db()
            c.execute("UPDATE users SET name='Daniela Granados Pérez', phone='3001112233' WHERE id=3")
            c.execute("UPDATE users SET tech_code=NULL")
            c.commit()
            import extras; extras.assign_codes(c)
            c.execute("UPDATE users SET document_id='123456', address='Calle 1 # 2-3' WHERE id=3"); c.commit()

    def test_ids_are_readable_and_unique(self):
        with application.app.app_context():
            codes = {r["name"]: r["tech_code"] for r in application.db().execute("SELECT name, tech_code FROM users").fetchall()}
        self.assertTrue(codes["Daniela Granados Pérez"].startswith("TEC-DG-"))
        self.assertTrue(codes["Admin"].startswith("ADM-"))
        self.assertIsNone(codes["Cli"])

    def test_private_data_only_admin_and_self(self):
        self.login(1)  # admin
        self.assertIn("123456", self.client.get("/perfil/3").get_data(as_text=True))
        self.login(3)  # el propio técnico
        self.assertIn("123456", self.client.get("/perfil/3").get_data(as_text=True))
        self.login(2)  # Soporte TI: sin datos personales
        html = self.client.get("/perfil/3").get_data(as_text=True)
        self.assertNotIn("123456", html); self.assertNotIn("Calle 1", html)
        self.assertEqual(self.client.get("/api/v1/profiles/3").status_code, 200)
        self.assertNotIn("document_id", self.client.get("/api/v1/profiles/3").get_json())

    def test_client_sees_only_contact_data_of_assigned_tech(self):
        self.login(4)
        r = self.client.get("/api/v1/profiles/3").get_json()
        self.assertEqual(set(r) - {"tickets", "stats"}, {"id", "name", "email", "phone", "area", "level"})
        self.assertEqual(self.client.get("/perfil/2").status_code, 403)   # no asignado a su empresa
        self.assertEqual(self.client.get("/perfil/1").status_code, 403)

    def test_edit_permissions_and_password_change(self):
        self.login(2)
        self.assertEqual(self.client.post("/perfil/3/editar", data={"city": "X"}).status_code, 403)
        self.login(3)
        self.client.post("/perfil/3/editar", data={"city": "Bogotá", "specialties": "Redes"})
        with application.app.app_context():
            self.assertEqual(application.db().execute("SELECT city FROM users WHERE id=3").fetchone()["city"], "Bogotá")
        self.client.post("/cuenta/seguridad", data={"current": "password-test", "new": "nueva-clave-segura", "repeat": "nueva-clave-segura"})
        r = application.app.test_client().post("/login", data={"email": "u3@example.test", "password": "nueva-clave-segura"})
        self.assertEqual(r.status_code, 302)


@unittest.skipUnless("test" in TEST_URL.lower(), "Define TEST_DATABASE_URL (con 'test' en el nombre)")
class AppApiTests(ExtrasTests):
    def token(self, email):
        return self.client.post("/api/v1/login", json={"email": email, "password": "password-test"}).get_json()["token"]

    def test_directory_and_password_and_app_ping(self):
        h = {"Authorization": "Bearer " + self.token("u3@example.test")}
        r = self.client.get("/api/v1/directory", headers=h)
        self.assertEqual(r.status_code, 200); self.assertIn("contacts", r.get_json())
        r = self.client.post("/api/v1/me/password", json={"current": "mala", "new": "nueva-clave-segura"}, headers=h)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/v1/me/password", json={"current": "password-test", "new": "nueva-clave-segura"}, headers=h)
        self.assertEqual(r.status_code, 200)
        # la app reporta presencia con su token y el administrador la ve como "app"
        ha = {"Authorization": "Bearer " + self.token("u1@example.test")}
        self.assertEqual(self.client.post("/api/v1/ping", json={"sid": "app-abcdefgh1234", "kind": "app", "path": "/tickets", "view": True}, headers=ha).status_code, 200)
        act = self.client.get("/api/v1/activity", headers=ha).get_json()
        self.assertEqual(act["online_app"], 1)
        self.assertEqual(act["online_users"][0]["name"], "Admin")


@unittest.skipUnless("test" in TEST_URL.lower(), "Define TEST_DATABASE_URL (con 'test' en el nombre)")
class ManagementTests(AppApiTests):
    def q(self, sql, *a):
        with application.app.app_context():
            return application.db().execute(sql, a).fetchall()

    def test_new_ticket_and_connection_notify_admin(self):
        self.login(4)
        self.client.post("/tickets/new", data={"title": "Falla de red", "description": "No hay internet", "category": "Redes", "priority": "Alta"})
        h = {"Authorization": "Bearer " + self.token("u1@example.test")}
        d = self.client.get("/api/v1/notifications", headers=h).get_json()
        self.assertTrue(any(n["kind"] == "ticket" and "Falla de red" in n["title"] and "Cli" in (n["body"] or "") for n in d["items"]), d)
        self.assertGreaterEqual(d["unread"], 1)
        # conexión: un técnico entra desde la app y el administrador recibe el aviso
        ht = {"Authorization": "Bearer " + self.token("u3@example.test")}
        self.client.post("/api/v1/ping", json={"sid": "tec-app-abcdefgh1", "kind": "app", "path": "/", "view": True}, headers=ht)
        d = self.client.get("/api/v1/notifications", headers=h).get_json()
        self.assertTrue(any(n["kind"] == "conexion" and "Tec" in n["title"] and "la app" in n["body"] for n in d["items"]), d)
        self.client.post("/api/v1/notifications/read", json={}, headers=h)
        self.assertEqual(self.client.get("/api/v1/notifications", headers=h).get_json()["unread"], 0)
        # el cliente no ve notificaciones de administración
        hc = {"Authorization": "Bearer " + self.token("u4@example.test")}
        self.assertEqual(self.client.get("/api/v1/notifications", headers=hc).get_json()["items"], [])

    def test_assignment_history_uses_names_and_notifies_technician(self):
        self.login(1)
        self.client.post("/areas", data={"ticket_id": 101, "area": "Soporte TI", "assigned_to": ""})
        self.client.post("/areas", data={"ticket_id": 101, "area": "Soporte TI", "assigned_to": 3})
        notes = [r["note"] for r in self.q("SELECT note FROM ticket_updates WHERE ticket_id=101 ORDER BY id")]
        self.assertTrue(any("responsable Tec" in n for n in notes), notes)
        self.assertFalse(any("responsable ID" in n for n in notes), notes)
        ht = {"Authorization": "Bearer " + self.token("u3@example.test")}
        d = self.client.get("/api/v1/notifications", headers=ht).get_json()
        self.assertTrue(any(n["kind"] == "asignacion" for n in d["items"]), d)

    def test_delete_user_keeps_ticket_history(self):
        self.login(1)
        self.client.post("/tickets/101", data={"note": "Reviso el equipo", "status": "En proceso"})
        self.login(3)
        self.client.post("/tickets/101", data={"note": "Cambié el cable", "status": "En proceso"})
        self.login(1)
        r = self.client.post("/users/3/purge", data={"confirm": "no"}, follow_redirects=True)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM users WHERE id=3")[0]["n"], 1)
        self.client.post("/users/3/purge", data={"confirm": "ELIMINAR"})
        self.assertEqual(self.q("SELECT COUNT(*) n FROM users WHERE id=3")[0]["n"], 0)
        t = self.q("SELECT assigned_to FROM tickets WHERE id=101")[0]
        self.assertIsNone(t["assigned_to"])
        r = self.client.get("/tickets/101")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Cambié el cable", html); self.assertIn("Tec", html)
        # eliminar al creador del ticket tampoco rompe el listado ni el detalle
        self.client.post("/users/4/purge", data={"confirm": "ELIMINAR"})
        self.assertEqual(self.client.get("/tickets").status_code, 200)
        self.assertIn("Cli", self.client.get("/tickets/101").get_data(as_text=True))
        # no se puede eliminar la propia cuenta ni al último administrador
        self.client.post("/users/1/purge", data={"confirm": "ELIMINAR"})
        self.assertEqual(self.q("SELECT COUNT(*) n FROM users WHERE id=1")[0]["n"], 1)
        # un técnico no puede eliminar cuentas
        self.login(2)
        self.assertIn(self.client.post("/users/2/purge", data={"confirm": "ELIMINAR"}).status_code, (302, 403))

    def test_admin_edits_technician_email_and_data(self):
        self.login(1)
        self.client.post("/perfil/3/editar", data={"name": "Técnico Nuevo", "email": "NUEVO@Example.test", "area": "Redes", "phone": "3001112233"})
        r = self.q("SELECT name,email,area,phone FROM users WHERE id=3")[0]
        self.assertEqual((r["name"], r["email"], r["area"], r["phone"]), ("Técnico Nuevo", "nuevo@example.test", "Redes", "3001112233"))
        self.client.post("/perfil/3/editar", data={"email": "u1@example.test"})  # duplicado
        self.assertEqual(self.q("SELECT email FROM users WHERE id=3")[0]["email"], "nuevo@example.test")
        self.login(2)  # Soporte TI no edita identidad
        self.assertEqual(self.client.post("/perfil/3/editar", data={"email": "x@example.test"}).status_code, 403)
        h = {"Authorization": "Bearer " + self.token("u1@example.test")}
        self.assertEqual(self.client.post("/api/v1/profiles/3/edit", json={"email": "otro@example.test"}, headers=h).status_code, 200)

    def test_top_menu_by_role(self):
        self.login(1)
        h = self.client.get("/dashboard").get_data(as_text=True)
        for txt in ("Operación", "Clientes", "Actividad en vivo", "Usuarios y roles", "notifBtn"):
            self.assertIn(txt, h)
        self.assertNotIn('class="sidebar"', h)
        self.login(2); h = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Operación", h); self.assertNotIn("Usuarios y roles", h); self.assertNotIn("Actividad en vivo", h)
        self.login(3); h = self.client.get("/dashboard").get_data(as_text=True)
        self.assertNotIn("Operación", h); self.assertIn("Soporte remoto", h)
        self.login(4); h = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Mis técnicos", h); self.assertNotIn("notifBtn", h)

    def test_demo_data_loads_once(self):
        self.login(1)
        self.client.post("/users/demo")
        n = self.q("SELECT COUNT(*) n FROM tickets")[0]["n"]
        self.assertGreater(n, 60)
        self.assertGreaterEqual(self.q("SELECT COUNT(*) n FROM companies")[0]["n"], 9)
        # el historial coincide con el técnico asignado: la nota «Asignado a …» nombra al técnico actual
        bad = self.q("SELECT t.id FROM tickets t JOIN users u ON u.id=t.assigned_to WHERE t.id<>101 AND NOT EXISTS "
                     "(SELECT 1 FROM ticket_updates tu WHERE tu.ticket_id=t.id AND tu.note LIKE '%%'||u.name||'%%')")
        self.assertEqual(bad, [])
        self.client.post("/users/demo")
        self.assertEqual(self.q("SELECT COUNT(*) n FROM tickets")[0]["n"], n)
