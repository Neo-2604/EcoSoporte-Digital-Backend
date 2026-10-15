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
