"""Pruebas básicas de aislamiento entre empresas para EcoSoporte Digital (PostgreSQL).

Necesitan una base PostgreSQL de PRUEBAS, vacía y distinta a la real: se borra todo su contenido.
Por seguridad el nombre/URL debe contener la palabra "test".

  TEST_DATABASE_URL=postgresql://usuario:clave@localhost:5432/ecosoporte_test python -m unittest discover -s tests -v
"""
import os
import unittest

import psycopg2
from werkzeug.security import generate_password_hash
import app as application

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")


@unittest.skipUnless("test" in TEST_URL.lower(), "Define TEST_DATABASE_URL apuntando a una base PostgreSQL de pruebas (con 'test' en el nombre)")
class CompanyIsolationTests(unittest.TestCase):
    def setUp(self):
        conn = psycopg2.connect(TEST_URL)
        conn.autocommit = True
        conn.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.close()
        self.old_admin_email = os.environ.pop("ADMIN_EMAIL", None)
        self.old_admin_password = os.environ.pop("ADMIN_PASSWORD", None)
        application.app.config.update(TESTING=True, DATABASE_URL=TEST_URL, SECRET_KEY="test-secret")
        application._schema_ready = False
        with application.app.app_context():
            application.init_db()
            c = application.db()
            c.execute("INSERT INTO companies(id,name,email) VALUES(1,'Empresa Alfa','alfa@example.test')")
            c.execute("INSERT INTO companies(id,name,email) VALUES(2,'Empresa Beta','beta@example.test')")
            c.execute("INSERT INTO users(id,name,email,password_hash,role,area,active,company_id,phone) VALUES(1,'Cliente Alfa','cliente-a@example.test',%s,'Usuario','Cliente',TRUE,1,'3001111111')", (generate_password_hash("password-test-a"),))
            c.execute("INSERT INTO users(id,name,email,password_hash,role,area,active,company_id,phone) VALUES(2,'Cliente Beta','cliente-b@example.test',%s,'Usuario','Cliente',TRUE,2,'3002222222')", (generate_password_hash("password-test-b"),))
            c.execute("INSERT INTO users(id,name,email,password_hash,role,area,active,company_id,phone) VALUES(3,'Técnico Alfa','tecnico-a@example.test',%s,'Técnico','Soporte TI',TRUE,NULL,'3003333333')", (generate_password_hash("password-test-c"),))
            c.execute("INSERT INTO users(id,name,email,password_hash,role,area,active,company_id,phone) VALUES(4,'Técnico Beta','tecnico-b@example.test',%s,'Técnico','Soporte TI',TRUE,NULL,'3004444444')", (generate_password_hash("password-test-d"),))
            c.execute("INSERT INTO tickets(id,title,description,category,priority,status,company_id,created_by,assigned_to,area) VALUES(101,'Ticket Alfa','Detalle Alfa','Hardware','Media','Abierto',1,1,3,'Soporte TI')")
            c.execute("INSERT INTO tickets(id,title,description,category,priority,status,company_id,created_by,assigned_to,area) VALUES(202,'Ticket Beta','Detalle Beta','Hardware','Media','Abierto',2,2,4,'Soporte TI')")
            c.commit()
        self.client = application.app.test_client()

    def tearDown(self):
        if self.old_admin_email is not None:
            os.environ["ADMIN_EMAIL"] = self.old_admin_email
        if self.old_admin_password is not None:
            os.environ["ADMIN_PASSWORD"] = self.old_admin_password

    def login_as(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def test_customer_ticket_list_is_company_scoped(self):
        self.login_as(1)
        response = self.client.get("/tickets")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ticket Alfa", response.data)
        self.assertNotIn(b"Ticket Beta", response.data)

    def test_customer_cannot_open_other_company_ticket(self):
        self.login_as(1)
        self.assertEqual(self.client.get("/tickets/202").status_code, 403)
        self.assertEqual(self.client.get("/tickets/101").status_code, 200)

    def test_customer_directory_only_shows_own_assigned_technicians(self):
        self.login_as(1)
        response = self.client.get("/contacto")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"tecnico-a@example.test", response.data)
        self.assertNotIn(b"tecnico-b@example.test", response.data)

    def test_dashboard_does_not_leak_other_company_tickets(self):
        self.login_as(1)
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ticket Alfa", response.data)
        self.assertNotIn(b"Ticket Beta", response.data)


if __name__ == "__main__":
    unittest.main()
