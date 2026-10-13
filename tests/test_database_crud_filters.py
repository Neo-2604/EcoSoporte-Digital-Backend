"""Pruebas de edición (empresas, usuarios, tickets) y filtros, sobre PostgreSQL.

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
class DatabaseCrudFiltersTests(unittest.TestCase):
    def setUp(self):
        conn = psycopg2.connect(TEST_URL)
        conn.autocommit = True
        conn.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.close()
        application.app.config.update(TESTING=True, DATABASE_URL=TEST_URL, SECRET_KEY="test-secret-crud")
        application._schema_ready = False
        with application.app.app_context():
            application.init_db()
            c = application.db()
            c.execute("INSERT INTO companies(id, name, nit, email, phone, city) VALUES(1, 'Empresa Inicial', '900111222', 'empresa1@test.com', '3001002030', 'Bogota')")
            c.execute("INSERT INTO companies(id, name, nit, email) VALUES(2, 'Empresa Secundaria', '900333444', 'empresa2@test.com')")

            c.execute("INSERT INTO users(id, name, email, password_hash, role, area, active, company_id, phone) VALUES(1, 'Admin Test', 'admin@test.com', %s, 'Administrador', 'Soporte TI', TRUE, NULL, '3000000000')", (generate_password_hash("adminpass123"),))
            c.execute("INSERT INTO users(id, name, email, password_hash, role, area, active, company_id, phone) VALUES(2, 'Cliente Test', 'cliente@test.com', %s, 'Usuario', 'Cliente', TRUE, 1, '3001112222')", (generate_password_hash("clientpass123"),))
            c.execute("INSERT INTO users(id, name, email, password_hash, role, area, active, company_id, phone) VALUES(3, 'Tech Test', 'tech@test.com', %s, 'Técnico', 'Soporte TI', TRUE, NULL, '3003334444')", (generate_password_hash("techpass1234"),))
            c.execute("INSERT INTO users(id, name, email, password_hash, role, area, active, company_id, phone) VALUES(4, 'Tech Dos', 'tech2@test.com', %s, 'Técnico', 'Soporte TI', TRUE, NULL, '3005556666')", (generate_password_hash("techpass5678"),))

            c.execute("INSERT INTO tickets(id, title, description, category, priority, status, company_id, created_by, assigned_to, area) VALUES(1, 'Problema Impresora', 'No imprime en red', 'Hardware', 'Alta', 'Abierto', 1, 2, 3, 'Soporte TI')")
            c.execute("INSERT INTO tickets(id, title, description, category, priority, status, company_id, created_by, assigned_to, area) VALUES(2, 'Falla de Software', 'Error en ERP', 'Software', 'Baja', 'Resuelto', 1, 2, 3, 'Aplicaciones')")
            c.commit()
        self.client = application.app.test_client()

    def login_as(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def row(self, sql, *params):
        with application.app.app_context():
            return application.db().execute(sql, params).fetchone()

    # ---- edición (administrador) ----
    def test_company_editing(self):
        self.login_as(1)
        response = self.client.post("/companies/1/edit", data={
            "name": "Empresa Actualizada", "nit": "900111222-9", "email": "contacto@empresa1.com",
            "phone": "6015551234", "address": "Calle 100 # 15-20", "city": "Medellin", "contact_name": "Juan Perez"
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        co = self.row("SELECT * FROM companies WHERE id=1")
        self.assertEqual(co["name"], "Empresa Actualizada")
        self.assertEqual(co["city"], "Medellin")
        self.assertEqual(co["contact_name"], "Juan Perez")

    def test_user_editing(self):
        self.login_as(1)
        response = self.client.post("/users/2/edit", data={
            "name": "Cliente Nombre Nuevo", "email": "nuevo_cliente@test.com", "phone": "3109998877",
            "position": "Gerente TI", "role": "Usuario", "area": "Cliente", "company_id": "1", "password": ""
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        u = self.row("SELECT * FROM users WHERE id=2")
        self.assertEqual(u["name"], "Cliente Nombre Nuevo")
        self.assertEqual(u["email"], "nuevo_cliente@test.com")
        self.assertEqual(u["position"], "Gerente TI")

    def test_user_editing_rejects_invalid_company(self):
        self.login_as(1)
        response = self.client.post("/users/2/edit", data={
            "name": "X", "email": "x@test.com", "role": "Usuario", "area": "Cliente", "company_id": "abc"
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.row("SELECT name FROM users WHERE id=2")["name"], "Cliente Test")

    def test_users_page_shows_company_and_position(self):
        self.login_as(1)
        response = self.client.get("/users")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Empresa Inicial", response.data)

    def test_ticket_editing(self):
        self.login_as(1)
        response = self.client.post("/tickets/1/edit", data={
            "title": "Problema Impresora Editado", "description": "Se verificó cable de red y fuente",
            "category": "Periféricos", "priority": "Crítica", "status": "En proceso", "area": "Infraestructura",
            "company_id": "1", "assigned_to": "3"
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        t = self.row("SELECT * FROM tickets WHERE id=1")
        self.assertEqual(t["title"], "Problema Impresora Editado")
        self.assertEqual(t["priority"], "Crítica")
        self.assertEqual(t["status"], "En proceso")
        self.assertEqual(t["area"], "Infraestructura")

    def test_admin_can_reassign_and_clear_technician(self):
        self.login_as(1)
        data = {"title": "Problema Impresora", "description": "No imprime en red", "company_id": "1", "assigned_to": "4"}
        self.client.post("/tickets/1/edit", data=data)
        self.assertEqual(self.row("SELECT assigned_to FROM tickets WHERE id=1")["assigned_to"], 4)
        data["assigned_to"] = ""
        self.client.post("/tickets/1/edit", data=data)
        self.assertIsNone(self.row("SELECT assigned_to FROM tickets WHERE id=1")["assigned_to"])

    # ---- seguridad: cada rol solo cambia lo que le corresponde ----
    def test_customer_cannot_move_ticket_to_other_company_or_change_status(self):
        self.login_as(2)  # cliente de la empresa 1, creador del ticket 1
        response = self.client.post("/tickets/1/edit", data={
            "title": "Nuevo asunto", "description": "Nueva descripción", "priority": "Media",
            "status": "Cerrado", "area": "Seguridad", "category": "Redes",
            "company_id": "2", "assigned_to": "4"
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        t = self.row("SELECT * FROM tickets WHERE id=1")
        self.assertEqual(t["title"], "Nuevo asunto")      # lo permitido sí cambia
        self.assertEqual(t["priority"], "Media")
        self.assertEqual(t["company_id"], 1)               # lo demás se ignora
        self.assertEqual(t["assigned_to"], 3)
        self.assertEqual(t["status"], "Abierto")
        self.assertEqual(t["area"], "Soporte TI")
        self.assertEqual(t["category"], "Hardware")

    def test_technician_cannot_change_company_or_assignment(self):
        self.login_as(3)  # técnico asignado al ticket 1
        self.client.post("/tickets/1/edit", data={
            "title": "Problema Impresora", "description": "No imprime en red", "status": "En proceso",
            "company_id": "2", "assigned_to": "4", "area": "Seguridad"
        }, follow_redirects=True)
        t = self.row("SELECT * FROM tickets WHERE id=1")
        self.assertEqual(t["status"], "En proceso")        # el técnico sí puede cambiar el estado
        self.assertEqual(t["company_id"], 1)
        self.assertEqual(t["assigned_to"], 3)
        self.assertEqual(t["area"], "Soporte TI")

    def test_technician_cannot_edit_unassigned_ticket(self):
        self.login_as(4)
        self.assertEqual(self.client.get("/tickets/1/edit").status_code, 403)

    def test_admin_cannot_assign_non_technician(self):
        self.login_as(1)
        self.client.post("/tickets/1/edit", data={
            "title": "Problema Impresora", "description": "No imprime en red", "company_id": "1", "assigned_to": "2"
        })
        self.assertEqual(self.row("SELECT assigned_to FROM tickets WHERE id=1")["assigned_to"], 3)

    # ---- filtros ----
    def test_ticket_filters(self):
        self.login_as(1)
        r1 = self.client.get("/tickets?priority=Alta")
        self.assertIn(b"Problema Impresora", r1.data)
        self.assertNotIn(b"Falla de Software", r1.data)

        r2 = self.client.get("/tickets?area=Aplicaciones")
        self.assertIn(b"Falla de Software", r2.data)
        self.assertNotIn(b"Problema Impresora", r2.data)

        r3 = self.client.get("/tickets?status=Resuelto")
        self.assertIn(b"Falla de Software", r3.data)
        self.assertNotIn(b"Problema Impresora", r3.data)

        r4 = self.client.get("/tickets?q=Impresora")
        self.assertIn(b"Problema Impresora", r4.data)
        self.assertNotIn(b"Falla de Software", r4.data)

        r5 = self.client.get("/tickets?q=impresora")  # sin distinguir mayúsculas
        self.assertIn(b"Problema Impresora", r5.data)

        r6 = self.client.get("/tickets?company_id=abc")  # id inválido: sin error, sin resultados
        self.assertEqual(r6.status_code, 200)
        self.assertNotIn(b"Problema Impresora", r6.data)


if __name__ == "__main__":
    unittest.main()
