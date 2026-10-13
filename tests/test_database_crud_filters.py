import os
import tempfile
import unittest
from werkzeug.security import generate_password_hash
import app as application

class DatabaseCrudFiltersTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_ecosoporte.db")
        application.app.config.update(TESTING=True, DATABASE=self.db_path, SECRET_KEY="test-secret-crud")
        with application.app.app_context():
            application.init_db()
            c = application.db()
            c.execute("INSERT INTO companies(id, name, nit, email, phone, city) VALUES(1, 'Empresa Inicial', '900111222', 'empresa1@test.com', '3001002030', 'Bogota')")
            c.execute("INSERT INTO companies(id, name, nit, email) VALUES(2, 'Empresa Secundaria', '900333444', 'empresa2@test.com')")

            c.execute("INSERT INTO users(id, name, email, password_hash, role, area, active, company_id, phone) VALUES(1, 'Admin Test', 'admin@test.com', ?, 'Administrador', 'Soporte TI', 1, NULL, '3000000000')", (generate_password_hash("adminpass123"),))
            c.execute("INSERT INTO users(id, name, email, password_hash, role, area, active, company_id, phone) VALUES(2, 'Cliente Test', 'cliente@test.com', ?, 'Usuario', 'Cliente', 1, 1, '3001112222')", (generate_password_hash("clientpass123"),))
            c.execute("INSERT INTO users(id, name, email, password_hash, role, area, active, company_id, phone) VALUES(3, 'Tech Test', 'tech@test.com', ?, 'Técnico', 'Soporte TI', 1, NULL, '3003334444')", (generate_password_hash("techpass1234"),))

            c.execute("INSERT INTO tickets(id, title, description, category, priority, status, company_id, created_by, assigned_to, area) VALUES(1, 'Problema Impresora', 'No imprime en red', 'Hardware', 'Alta', 'Abierto', 1, 2, 3, 'Soporte TI')")
            c.execute("INSERT INTO tickets(id, title, description, category, priority, status, company_id, created_by, assigned_to, area) VALUES(2, 'Falla de Software', 'Error en ERP', 'Software', 'Baja', 'Resuelto', 1, 2, 3, 'Aplicaciones')")
            c.commit()
        self.client = application.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def login_as(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def test_company_editing(self):
        self.login_as(1)
        response = self.client.post("/companies/1/edit", data={
            "name": "Empresa Actualizada",
            "nit": "900111222-9",
            "email": "contacto@empresa1.com",
            "phone": "6015551234",
            "address": "Calle 100 # 15-20",
            "city": "Medellin",
            "contact_name": "Juan Perez"
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with application.app.app_context():
            co = application.db().execute("SELECT * FROM companies WHERE id=1").fetchone()
            self.assertEqual(co["name"], "Empresa Actualizada")
            self.assertEqual(co["city"], "Medellin")
            self.assertEqual(co["contact_name"], "Juan Perez")

    def test_user_editing(self):
        self.login_as(1)
        response = self.client.post("/users/2/edit", data={
            "name": "Cliente Nombre Nuevo",
            "email": "nuevo_cliente@test.com",
            "phone": "3109998877",
            "position": "Gerente TI",
            "role": "Usuario",
            "area": "Cliente",
            "company_id": "1",
            "password": ""
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with application.app.app_context():
            u = application.db().execute("SELECT * FROM users WHERE id=2").fetchone()
            self.assertEqual(u["name"], "Cliente Nombre Nuevo")
            self.assertEqual(u["email"], "nuevo_cliente@test.com")
            self.assertEqual(u["position"], "Gerente TI")

    def test_ticket_editing(self):
        self.login_as(1)
        response = self.client.post("/tickets/1/edit", data={
            "title": "Problema Impresora Editado",
            "description": "Se verificó cable de red y fuente",
            "category": "Periféricos",
            "priority": "Crítica",
            "status": "En proceso",
            "area": "Infraestructura",
            "company_id": "1",
            "assigned_to": "3"
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with application.app.app_context():
            t = application.db().execute("SELECT * FROM tickets WHERE id=1").fetchone()
            self.assertEqual(t["title"], "Problema Impresora Editado")
            self.assertEqual(t["priority"], "Crítica")
            self.assertEqual(t["status"], "En proceso")
            self.assertEqual(t["area"], "Infraestructura")

    def test_ticket_filters(self):
        self.login_as(1)
        # Filter by priority
        r1 = self.client.get("/tickets?priority=Alta")
        self.assertIn(b"Problema Impresora", r1.data)
        self.assertNotIn(b"Falla de Software", r1.data)

        # Filter by area
        r2 = self.client.get("/tickets?area=Aplicaciones")
        self.assertIn(b"Falla de Software", r2.data)
        self.assertNotIn(b"Problema Impresora", r2.data)

        # Filter by status
        r3 = self.client.get("/tickets?status=Resuelto")
        self.assertIn(b"Falla de Software", r3.data)
        self.assertNotIn(b"Problema Impresora", r3.data)

        # Search query
        r4 = self.client.get("/tickets?q=Impresora")
        self.assertIn(b"Problema Impresora", r4.data)
        self.assertNotIn(b"Falla de Software", r4.data)

if __name__ == "__main__":
    unittest.main()
