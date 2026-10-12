"""Pruebas básicas de aislamiento entre empresas para EcoSoporte Digital.

Ejecutar desde la raíz del proyecto con: python -m unittest discover -s tests -v
"""
import os
import sqlite3
import tempfile
import unittest

from werkzeug.security import generate_password_hash
import app as application


class CompanyIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_ecosoporte.db")
        self.old_admin_email = os.environ.pop("ADMIN_EMAIL", None)
        self.old_admin_password = os.environ.pop("ADMIN_PASSWORD", None)
        application.app.config.update(TESTING=True, DATABASE=self.db_path, SECRET_KEY="test-secret")
        with application.app.app_context():
            application.init_db()
            c = application.db()
            c.execute("INSERT INTO companies(id,name,email) VALUES(1,'Empresa Alfa','alfa@example.test')")
            c.execute("INSERT INTO companies(id,name,email) VALUES(2,'Empresa Beta','beta@example.test')")
            c.execute("INSERT INTO users(id,name,email,password_hash,role,area,active,company_id,phone) VALUES(1,'Cliente Alfa','cliente-a@example.test',?,'Usuario','Cliente',1,1,'3001111111')", (generate_password_hash("password-test-a"),))
            c.execute("INSERT INTO users(id,name,email,password_hash,role,area,active,company_id,phone) VALUES(2,'Cliente Beta','cliente-b@example.test',?,'Usuario','Cliente',1,2,'3002222222')", (generate_password_hash("password-test-b"),))
            c.execute("INSERT INTO users(id,name,email,password_hash,role,area,active,company_id,phone) VALUES(3,'Técnico Alfa','tecnico-a@example.test',?,'Técnico','Soporte TI',1,NULL,'3003333333')", (generate_password_hash("password-test-c"),))
            c.execute("INSERT INTO users(id,name,email,password_hash,role,area,active,company_id,phone) VALUES(4,'Técnico Beta','tecnico-b@example.test',?,'Técnico','Soporte TI',1,NULL,'3004444444')", (generate_password_hash("password-test-d"),))
            c.execute("INSERT INTO tickets(id,title,description,category,priority,status,company_id,created_by,assigned_to,area) VALUES(101,'Ticket Alfa','Detalle Alfa','Hardware','Media','Abierto',1,1,3,'Soporte TI')")
            c.execute("INSERT INTO tickets(id,title,description,category,priority,status,company_id,created_by,assigned_to,area) VALUES(202,'Ticket Beta','Detalle Beta','Hardware','Media','Abierto',2,2,4,'Soporte TI')")
            c.commit()
        self.client = application.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()
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
