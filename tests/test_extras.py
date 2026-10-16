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
