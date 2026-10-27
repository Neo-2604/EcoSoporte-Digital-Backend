"""Pruebas de nómina/desempeño y del catálogo/cotizaciones: permisos, privacidad, cálculos y validaciones."""
import os
import unittest
from datetime import datetime

import psycopg2
from werkzeug.security import generate_password_hash
import app as application
import payroll

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
PERIOD = "2026-09"


@unittest.skipUnless("test" in TEST_URL.lower(), "Define TEST_DATABASE_URL (con 'test' en el nombre)")
class BusinessBase(unittest.TestCase):
    def setUp(self):
        conn = psycopg2.connect(TEST_URL); conn.autocommit = True
        conn.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"); conn.close()
        os.environ.pop("ADMIN_EMAIL", None); os.environ.pop("ADMIN_PASSWORD", None); os.environ.pop("PAYROLL_ALLOW_SELF_APPROVAL", None)
        application.app.config.update(TESTING=True, DATABASE_URL=TEST_URL, SECRET_KEY="test-secret")
        application._schema_ready = False
        with application.app.app_context():
            application.init_db()
            c = application.db()
            c.execute("INSERT INTO companies(id,name,email) VALUES(1,'Alfa','a@example.test'),(2,'Beta','b@example.test')")
            h = generate_password_hash("password-test")
            rows = [(1, "Admin Uno", "Administrador", None), (2, "Admin Dos", "Administrador", None), (3, "Soporte", "Soporte TI", None),
                    (4, "Tecnico Uno", "Técnico", None), (5, "Cliente Alfa", "Usuario", 1), (6, "Cliente Beta", "Usuario", 2),
                    (7, "Tecnico Dos", "Técnico", None)]
            for uid, n, r, co in rows:
                c.execute("INSERT INTO users(id,name,email,password_hash,role,area,active,company_id) VALUES(%s,%s,%s,%s,%s,'Soporte TI',TRUE,%s)",
                          (uid, n, f"u{uid}@example.test", h, r, co))
            c.commit()
        self.client = application.app.test_client()

    def login(self, uid):
        with self.client.session_transaction() as s:
            s.clear()
            if uid: s["user_id"] = uid

    def sql(self, q, a=()):
        with application.app.app_context():
            c = application.db(); cur = c.execute(q, a)
            out = cur.fetchall() if cur.description else None
            c.commit(); return out


class PayrollTests(BusinessBase):
    def approve_basics(self):
        """Admin 1 aprueba parámetros iniciales; admin 1 propone salario del técnico 4 y admin 2 lo aprueba."""
        self.login(1)
        pid = self.sql("SELECT id FROM payroll_params ORDER BY id LIMIT 1")[0]["id"]
        self.client.post(f"/nomina/configuracion/params/{pid}/aprobar")
        self.client.post("/nomina/configuracion", data=dict(kind="salary", user_id="4", base_salary="2000000", effective_from="2026-01-01", transport="1"))
        sid = self.sql("SELECT id FROM payroll_salaries ORDER BY id DESC LIMIT 1")[0]["id"]
        self.login(2)
        self.client.post(f"/nomina/configuracion/salary/{sid}/aprobar")

    def add_tickets(self, basic=30, medium=10, reopen=0, uid=4):
        n = 0
        for lvl, count in (("Básico", basic), ("Medio", medium)):
            for _ in range(count):
                n += 1
                self.sql("""INSERT INTO tickets(title,description,category,priority,status,company_id,created_by,assigned_to,area,complexity,
                            created_at,resolved_at,solution_due) VALUES(%s,'d','Red','Media','Resuelto',1,5,%s,'Soporte TI',%s,
                            '2026-09-01 08:00:00','2026-09-10 10:00:00','2026-09-12 10:00:00')""", (f"T{n}", uid, lvl))
        ids = [r["id"] for r in self.sql("SELECT id FROM tickets ORDER BY id")]
        for tid in ids[:reopen]:
            self.sql("INSERT INTO ticket_updates(ticket_id,user_id,note,status,created_at) VALUES(%s,1,'reabierto','En proceso','2026-09-11 09:00:00')", (tid,))

    def test_bulk_link_authorize_and_export(self):
        self.login(1)
        self.assertIn("No hay liquidaciones", self.client.get("/nomina/exportar.xlsx", follow_redirects=True).get_data(as_text=True))
        self.client.post("/nomina/configuracion/masivo/vincular")
        n = len(self.sql("SELECT id FROM users WHERE active AND role IN ('Técnico','Soporte TI','Administrador')"))
        self.assertEqual(len(self.sql("SELECT id FROM payroll_salaries")), n)
        self.client.post("/nomina/configuracion/masivo/vincular")  # idempotente
        self.assertEqual(len(self.sql("SELECT id FROM payroll_salaries")), n)
        self.client.post("/nomina/configuracion/masivo/autorizar")  # mismo admin: no puede autoaprobar
        self.assertEqual(len(self.sql("SELECT id FROM payroll_salaries WHERE status='Aprobado'")), 0)
        self.login(2)
        self.client.post("/nomina/configuracion/masivo/autorizar")
        self.assertEqual(len(self.sql("SELECT id FROM payroll_salaries WHERE status='Aprobado'")), n)
        self.assertEqual(len(self.sql("SELECT id FROM payroll_params WHERE status='Aprobado'")), 1)
        self.client.post("/nomina/generar", data=dict(periodo="2026-10"))
        self.assertEqual(len(self.sql("SELECT id FROM payroll_settlements")), n)
        r = self.client.get("/nomina/exportar.xlsx?periodo=2026-10")
        self.assertEqual(r.status_code, 200); self.assertIn("spreadsheet", r.mimetype)
        self.login(4)
        self.assertEqual(self.client.post("/nomina/configuracion/masivo/autorizar").status_code, 403)

    def test_default_params_are_pending_and_block_settlements(self):
        self.assertEqual(self.sql("SELECT status FROM payroll_params")[0]["status"], payroll.PARAM_PENDING)
        self.login(1)
        self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM payroll_settlements")[0]["n"], 0)

    def test_access_by_role(self):
        for uid, expect in ((5, 403), (6, 403)):
            self.login(uid); self.assertEqual(self.client.get("/nomina/").status_code, expect)
        self.login(None); self.assertEqual(self.client.get("/nomina/").status_code, 302)
        self.login(3)
        self.assertEqual(self.client.get("/nomina/").status_code, 200)
        for url in ("/nomina/configuracion", "/nomina/exportar.csv"):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post("/nomina/generar", data=dict(periodo=PERIOD)).status_code, 403)
        self.login(4); self.assertEqual(self.client.get("/nomina/").status_code, 200)
        self.assertEqual(self.client.get("/nomina/configuracion").status_code, 403)

    def test_support_view_has_no_money(self):
        self.approve_basics(); self.add_tickets()
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        self.login(3)
        html = self.client.get(f"/nomina/?periodo={PERIOD}").get_data(as_text=True)
        self.assertNotIn("2.000.000", html); self.assertNotIn("2.349.095", html)
        self.assertEqual(self.client.get("/nomina/liquidacion/1").status_code, 403)

    def test_two_person_rule_for_salary_and_params(self):
        self.login(1)
        self.client.post("/nomina/configuracion", data=dict(kind="salary", user_id="4", base_salary="2000000", effective_from="2026-01-01"))
        sid = self.sql("SELECT id FROM payroll_salaries")[0]["id"]
        self.client.post(f"/nomina/configuracion/salary/{sid}/aprobar")
        self.assertEqual(self.sql("SELECT status FROM payroll_salaries")[0]["status"], payroll.PARAM_PENDING)
        self.login(2); self.client.post(f"/nomina/configuracion/salary/{sid}/aprobar")
        self.assertEqual(self.sql("SELECT status FROM payroll_salaries")[0]["status"], payroll.PARAM_APPROVED)

    def test_input_validation(self):
        self.login(1)
        r = self.client.post("/nomina/configuracion", data=dict(kind="salary", user_id="4", base_salary="-5", effective_from="2026-01-01"), follow_redirects=True)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM payroll_salaries")[0]["n"], 0)
        self.client.post("/nomina/configuracion", data=dict(kind="salary", user_id="5", base_salary="1000000", effective_from="2026-01-01"))
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM payroll_salaries")[0]["n"], 0, "un cliente no puede tener salario")
        bad = {k: "1" for k, *_ in payroll.PARAM_FIELDS}; bad["min_quality"] = "150"; bad["kind"] = "params"
        self.client.post("/nomina/configuracion", data=bad)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM payroll_params")[0]["n"], 1)

    def test_bonus_calculation(self):
        self.approve_basics(); self.add_tickets(basic=30, medium=10)  # 30 + 15 = 45 pts; 10 sobre la meta; tope 20
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        s = self.sql("SELECT * FROM payroll_settlements")[0]
        self.assertEqual(s["points"], 45); self.assertEqual(s["bonus_points"], 10); self.assertEqual(s["bonus_amount"], 100000)
        self.assertEqual(s["transport_aid"], 249095); self.assertEqual(s["total"], 2000000 + 249095 + 100000)
        self.assertEqual(s["status"], payroll.PENDING); self.assertEqual(s["quality"], 1.0); self.assertEqual(s["ans"], 1.0)
        self.assertIsNone(s["satisfaction"])

    def test_bonus_cap_and_below_goal(self):
        self.approve_basics(); self.add_tickets(basic=80, medium=0)  # 80 pts -> 45 extra, tope 20
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        self.assertEqual(self.sql("SELECT bonus_amount FROM payroll_settlements")[0]["bonus_amount"], 200000)
        self.setUp(); self.approve_basics(); self.add_tickets(basic=20, medium=0)
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        self.assertEqual(self.sql("SELECT bonus_amount FROM payroll_settlements")[0]["bonus_amount"], 0)

    def test_quality_gate_blocks_bonus(self):
        self.approve_basics(); self.add_tickets(basic=30, medium=10, reopen=5)  # 87,5 % < 90 %
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        s = self.sql("SELECT * FROM payroll_settlements")[0]
        self.assertAlmostEqual(s["quality"], 0.875); self.assertEqual(s["bonus_amount"], 0)

    def test_sla_gate_and_satisfaction(self):
        self.approve_basics(); self.add_tickets(basic=30, medium=10)
        self.sql("UPDATE tickets SET solution_due='2026-09-10 09:00:00' WHERE id<=10")  # 10 fuera de plazo: ANS 75 %
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        self.assertEqual(self.sql("SELECT bonus_amount FROM payroll_settlements")[0]["bonus_amount"], 0)
        self.sql("UPDATE tickets SET solution_due='2026-09-12 10:00:00'"); self.sql("UPDATE tickets SET satisfaction=2 WHERE id<=5")
        self.client.post("/nomina/liquidacion/1/recalcular")
        s = self.sql("SELECT * FROM payroll_settlements")[0]
        self.assertEqual(s["satisfaction"], 2.0); self.assertEqual(s["bonus_amount"], 0, "satisfacción baja con suficientes respuestas")
        self.sql("UPDATE tickets SET satisfaction=NULL WHERE id<=2")  # solo 3 respuestas: aún evalúa (mínimo 3)
        self.sql("UPDATE tickets SET satisfaction=NULL WHERE id<=3")  # 2 respuestas: no se penaliza
        self.client.post("/nomina/liquidacion/1/recalcular")
        self.assertEqual(self.sql("SELECT bonus_amount FROM payroll_settlements")[0]["bonus_amount"], 100000)

    def test_complexity_validation_and_exclusion(self):
        self.approve_basics(); self.add_tickets(basic=40, medium=0)  # 40 pts => 5 extra
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        self.assertEqual(self.sql("SELECT bonus_amount FROM payroll_settlements")[0]["bonus_amount"], 50000)
        ids = [r["id"] for r in self.sql("SELECT id FROM tickets ORDER BY id")]
        data = {f"cx_{i}": "Básico" for i in ids}; data[f"cx_{ids[0]}"] = "Complejo"; data[f"ex_{ids[1]}"] = "1"; data[f"ex_{ids[2]}"] = "1"
        self.client.post("/nomina/liquidacion/1/complejidad", data=data)  # +1 (complejo) -2 excluidos = 39 pts
        s = self.sql("SELECT * FROM payroll_settlements")[0]
        self.assertEqual(s["points"], 39); self.assertEqual(s["bonus_amount"], 40000)  # 4 pts sobre la meta

    def test_workflow_authorization_and_privacy(self):
        self.approve_basics(); self.add_tickets()
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        self.client.post("/nomina/liquidacion/1/aprobar")  # mismo administrador que la generó
        self.assertEqual(self.sql("SELECT status FROM payroll_settlements")[0]["status"], payroll.PENDING)
        self.login(4); self.assertEqual(self.client.get("/nomina/liquidacion/1").status_code, 403, "técnico no ve borradores")
        self.login(2); self.client.post("/nomina/liquidacion/1/pagar", data=dict(payment_ref="x1234"))
        self.assertEqual(self.sql("SELECT status FROM payroll_settlements")[0]["status"], payroll.PENDING, "no se paga sin aprobar")
        self.client.post("/nomina/liquidacion/1/aprobar")
        self.assertEqual(self.sql("SELECT status FROM payroll_settlements")[0]["status"], payroll.APPROVED)
        self.client.post("/nomina/liquidacion/1/pagar", data=dict(payment_ref=""))
        self.assertEqual(self.sql("SELECT status FROM payroll_settlements")[0]["status"], payroll.APPROVED, "exige referencia")
        self.client.post("/nomina/liquidacion/1/pagar", data=dict(payment_ref="TRF-2026-0001"))
        self.assertEqual(self.sql("SELECT status FROM payroll_settlements")[0]["status"], payroll.PAID)
        self.client.post("/nomina/liquidacion/1/recalcular")
        self.assertEqual(self.sql("SELECT status FROM payroll_settlements")[0]["status"], payroll.PAID)
        self.login(4); self.assertEqual(self.client.get("/nomina/liquidacion/1").status_code, 200)
        self.login(7); self.assertEqual(self.client.get("/nomina/liquidacion/1").status_code, 403, "otro técnico no ve la liquidación ajena")
        body = self.client.get("/nomina/").get_data(as_text=True); self.assertNotIn("2.349.095", body)
        self.assertGreaterEqual(self.sql("SELECT COUNT(*) n FROM notifications WHERE target_user_id=4")[0]["n"], 2)
        self.assertGreaterEqual(self.sql("SELECT COUNT(*) n FROM payroll_audit")[0]["n"], 4)

    def test_export_and_formula_injection(self):
        self.approve_basics(); self.add_tickets()
        self.sql("UPDATE users SET name='=HYPERLINK(1)' WHERE id=4")
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        r = self.client.get(f"/nomina/exportar.csv?periodo={PERIOD}")
        self.assertEqual(r.status_code, 200); txt = r.get_data(as_text=True)
        self.assertIn("2349095", txt); self.assertIn("'=HYPERLINK", txt)
        x = self.client.get("/nomina/exportar.xlsx"); self.assertEqual(x.status_code, 200); self.assertTrue(x.data.startswith(b"PK"))
        self.assertEqual(self.client.get("/nomina/exportar.pdf").status_code, 404)

    def test_filters(self):
        self.approve_basics(); self.add_tickets()
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        self.assertIn("Tecnico Uno", self.client.get(f"/nomina/?periodo={PERIOD}&tecnico=4&estado=Pendiente de revisión").get_data(as_text=True))
        self.assertEqual(self.client.get("/nomina/?periodo=basura&tecnico=x&estado=zzz").status_code, 200)

    def test_client_rating(self):
        self.add_tickets(basic=1, medium=0)
        tid = self.sql("SELECT id FROM tickets")[0]["id"]
        self.login(6); self.assertEqual(self.client.post(f"/tickets/{tid}/calificar", data=dict(score="5")).status_code, 403)
        self.login(5)
        self.client.post(f"/tickets/{tid}/calificar", data=dict(score="9")); self.assertIsNone(self.sql("SELECT satisfaction FROM tickets")[0]["satisfaction"])
        self.client.post(f"/tickets/{tid}/calificar", data=dict(score="4")); self.assertEqual(self.sql("SELECT satisfaction FROM tickets")[0]["satisfaction"], 4)
        self.client.post(f"/tickets/{tid}/calificar", data=dict(score="1")); self.assertEqual(self.sql("SELECT satisfaction FROM tickets")[0]["satisfaction"], 4)
        self.assertIn("Calificación registrada", self.client.get(f"/tickets/{tid}").get_data(as_text=True))

    def test_delete_user_keeps_payroll_history(self):
        self.approve_basics(); self.add_tickets()
        self.login(1); self.client.post("/nomina/generar", data=dict(periodo=PERIOD))
        self.client.post("/usuarios/4/eliminar", data=dict(confirm="ELIMINAR"))
        s = self.sql("SELECT user_name FROM payroll_settlements")
        self.assertEqual(len(s), 1); self.assertEqual(s[0]["user_name"], "Tecnico Uno")


class CatalogTests(BusinessBase):
    VALID = dict(name="Ana Pérez", email="Ana@Example.test", phone="300 123 4567", message="Necesito mantenimiento de 10 equipos.", consent="1")

    def publish_first(self):
        self.sql("UPDATE catalog_items SET published=TRUE WHERE id=1")
        return 1

    def test_seed_is_draft_without_prices(self):
        rows = self.sql("SELECT published, price, price_mode FROM catalog_items")
        self.assertEqual(len(rows), 7); self.assertTrue(all(not r["published"] and r["price"] is None and r["price_mode"] == "Cotización" for r in rows))
        self.assertNotIn("Soporte remoto", self.client.get("/catalogo/").get_data(as_text=True))
        self.assertEqual(self.client.get("/catalogo/1").status_code, 404)

    def test_public_listing_filters_and_detail(self):
        self.publish_first()
        html = self.client.get("/catalogo/").get_data(as_text=True)
        self.assertIn("Diagnóstico básico", html); self.assertIn("Requiere cotización", html)
        self.assertNotIn("Soporte remoto", html)
        self.assertNotIn("Diagnóstico básico", self.client.get("/catalogo/?q=zzzz").get_data(as_text=True))
        self.assertEqual(self.client.get("/catalogo/1").status_code, 200)

    def test_admin_panel_permissions_and_publish(self):
        for uid in (3, 4, 5):
            self.login(uid); self.assertEqual(self.client.get("/catalogo/admin").status_code, 403)
            self.assertEqual(self.client.post("/catalogo/admin/1/publicar").status_code, 403)
        self.login(1); self.assertEqual(self.client.get("/catalogo/admin").status_code, 200)
        self.client.post("/catalogo/admin/1/publicar"); self.assertTrue(self.sql("SELECT published FROM catalog_items WHERE id=1")[0]["published"])
        self.client.post("/catalogo/admin/1/publicar"); self.assertFalse(self.sql("SELECT published FROM catalog_items WHERE id=1")[0]["published"])

    def test_create_edit_validation(self):
        self.login(1)
        bad = self.client.post("/catalogo/admin/nuevo", data=dict(kind="Servicio", name="Plan X", price_mode="Precio fijo", price="", sort_order="1"))
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.post("/catalogo/admin/nuevo", data=dict(kind="Hack", name="Plan X", price_mode="Cotización")).status_code, 400)
        ok = self.client.post("/catalogo/admin/nuevo", data=dict(kind="Producto", name="Memoria RAM 16 GB", price_mode="Desde", price="120.000", price_unit="unidad",
                                                                  published="1", benefits="Más rápido\nGarantía", sort_order="5"))
        self.assertEqual(ok.status_code, 302)
        row = self.sql("SELECT * FROM catalog_items WHERE name='Memoria RAM 16 GB'")[0]
        self.assertEqual(row["price"], 120000); self.assertTrue(row["published"])
        self.login(None); page = self.client.get(f"/catalogo/{row['id']}").get_data(as_text=True)
        self.assertIn("Desde $120.000 unidad", page); self.assertIn("Garantía", page)
        self.login(1); self.client.post(f"/catalogo/admin/{row['id']}", data=dict(kind="Producto", name="Memoria RAM 32 GB", price_mode="Cotización"))
        self.assertEqual(self.sql("SELECT name, price FROM catalog_items WHERE id=%s", (row["id"],))[0]["price"], None)

    def test_quote_validation(self):
        self.publish_first(); self.login(None)
        for patch in ({"consent": ""}, {"email": "no-es-correo"}, {"name": "A"}, {"message": "corto"}, {"phone": "12"}):
            r = self.client.post("/catalogo/cotizar", data={**self.VALID, "item_id": "1", **patch}); self.assertEqual(r.status_code, 400, patch)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM quote_requests")[0]["n"], 0)
        self.client.post("/catalogo/cotizar", data={**self.VALID, "item_id": "2"})  # no publicado
        self.assertEqual(self.sql("SELECT item_id FROM quote_requests")[0]["item_id"], None)
        r = self.client.post("/catalogo/cotizar", data={**self.VALID, "item_id": "1", "website": "bot"}); self.assertEqual(r.status_code, 302)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM quote_requests")[0]["n"], 1)

    def test_quote_flow_tracking_and_privacy(self):
        self.publish_first(); self.login(None)
        r = self.client.post("/catalogo/cotizar", data={**self.VALID, "item_id": "1"}); self.assertEqual(r.status_code, 302)
        q = self.sql("SELECT * FROM quote_requests")[0]
        self.assertEqual(q["email"], "ana@example.test"); self.assertEqual(q["phone"], "3001234567"); self.assertTrue(q["consent"])
        code = f"COT-{q['created_at'][:4]}-{q['id']:04d}"
        self.assertIn(code, r.headers["Location"])
        # seguimiento anónimo: exige código y correo correctos, sin revelar si existe
        bad = self.client.post("/catalogo/seguimiento", data=dict(code=code, email="otra@example.test"))
        self.assertIn("No encontramos", bad.get_data(as_text=True))
        self.assertEqual(self.client.get(f"/catalogo/seguimiento/{q['id']}").status_code, 404)
        self.assertEqual(self.client.get(f"/catalogo/seguimiento/{q['id']}?email=otra@example.test").status_code, 404)
        ok = self.client.post("/catalogo/seguimiento", data=dict(code=code, email="ANA@example.test")); self.assertEqual(ok.status_code, 302)
        self.assertEqual(self.client.get(f"/catalogo/seguimiento/{q['id']}?email=ana@example.test").status_code, 200)
        # la gestión interna es solo para Administrador y Soporte TI
        for uid in (4, 5):
            self.login(uid); self.assertEqual(self.client.get("/catalogo/cotizaciones").status_code, 403)
            self.assertEqual(self.client.get(f"/catalogo/cotizaciones/{q['id']}").status_code, 403)
        self.login(3); self.assertEqual(self.client.get("/catalogo/cotizaciones").status_code, 200)
        self.assertGreaterEqual(self.sql("SELECT COUNT(*) n FROM notifications WHERE kind='cotizacion'")[0]["n"], 1)

    def test_logged_in_requester_sees_only_own_and_gets_notified(self):
        self.publish_first(); self.login(5)
        self.client.post("/catalogo/cotizar", data={**self.VALID, "email": "cli@example.test", "item_id": "1"})
        qid = self.sql("SELECT id FROM quote_requests")[0]["id"]
        self.assertIn("Mis solicitudes", self.client.get("/catalogo/seguimiento").get_data(as_text=True))
        self.assertEqual(self.client.get(f"/catalogo/seguimiento/{qid}").status_code, 200)
        self.login(6); self.assertEqual(self.client.get(f"/catalogo/seguimiento/{qid}").status_code, 404)
        self.assertNotIn("Mis solicitudes", self.client.get("/catalogo/seguimiento").get_data(as_text=True))
        self.login(3)
        self.client.post(f"/catalogo/cotizaciones/{qid}", data=dict(status="Cotización enviada", note="x"))
        self.assertEqual(self.sql("SELECT status FROM quote_requests")[0]["status"], "Nueva", "exige valor cotizado")
        self.client.post(f"/catalogo/cotizaciones/{qid}", data=dict(status="Cotización enviada", quoted_amount="450.000", note="Adjuntamos propuesta", public="1"))
        q = self.sql("SELECT * FROM quote_requests")[0]; self.assertEqual(q["status"], "Cotización enviada"); self.assertEqual(q["quoted_amount"], 450000)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM notifications WHERE target_user_id=5")[0]["n"], 1)
        self.login(5); self.assertIn("$450.000", self.client.get(f"/catalogo/seguimiento/{qid}").get_data(as_text=True))

    def test_amount_hidden_until_sent_and_internal_notes_private(self):
        self.publish_first(); self.login(None)
        self.client.post("/catalogo/cotizar", data={**self.VALID, "item_id": "1"})
        qid = self.sql("SELECT id FROM quote_requests")[0]["id"]
        self.login(3)
        self.client.post(f"/catalogo/cotizaciones/{qid}", data=dict(status="En revisión", quoted_amount="999000", note="nota interna secreta"))
        self.login(None)
        page = self.client.get(f"/catalogo/seguimiento/{qid}?email=ana@example.test").get_data(as_text=True)
        self.assertNotIn("999.000", page); self.assertNotIn("nota interna secreta", page)

    def test_rate_limit_by_email(self):
        self.publish_first(); self.login(None)
        for _ in range(7): self.client.post("/catalogo/cotizar", data={**self.VALID, "item_id": "1"})
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM quote_requests")[0]["n"], 5)

    def test_item_code_visible_and_searchable(self):
        self.publish_first(); self.login(None)
        html = self.client.get("/catalogo/").get_data(as_text=True); self.assertIn("CAT-0001", html)
        for q in ("CAT-0001", "cat1", "#1", "cat-0001"):
            r = self.client.get(f"/catalogo/?q={q.replace('#', '%23')}").get_data(as_text=True)
            self.assertIn("Diagnóstico básico", r, q)
        self.assertNotIn("Diagnóstico básico", self.client.get("/catalogo/?q=CAT-0002").get_data(as_text=True), "el 2 no está publicado")
        self.login(1); a = self.client.get("/catalogo/admin?q=CAT-0003").get_data(as_text=True)
        self.assertIn("Mantenimiento correctivo", a); self.assertNotIn("Plan mensual básico", a)

    def test_xss_is_escaped(self):
        self.publish_first(); self.login(None)
        self.client.post("/catalogo/cotizar", data={**self.VALID, "name": "<script>alert(1)</script>", "item_id": "1"})
        qid = self.sql("SELECT id FROM quote_requests")[0]["id"]; self.login(1)
        self.assertNotIn("<script>alert(1)</script>", self.client.get(f"/catalogo/cotizaciones/{qid}").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()


import analytics


class ExportAllTests(BusinessBase):
    def test_full_export_admin_only_and_no_secrets(self):
        import io, openpyxl
        for uid in (3, 4, 5):
            self.login(uid); self.assertEqual(self.client.get("/estadisticas/datos.xlsx").status_code, 403)
        self.login(None); self.assertEqual(self.client.get("/estadisticas/datos.xlsx").status_code, 302)
        self.login(1); r = self.client.get("/estadisticas/datos.xlsx"); self.assertEqual(r.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(r.data))
        self.assertIn("Tickets", wb.sheetnames); self.assertIn("Liquidaciones", wb.sheetnames); self.assertIn("Catalogo", wb.sheetnames)
        users = list(wb["Usuarios"].iter_rows(values_only=True)); self.assertEqual(len(users) - 1, 7)
        flat = " ".join(str(v) for ws in wb for row in ws.iter_rows(values_only=True) for v in row if v is not None)
        self.assertNotIn("pbkdf2", flat); self.assertNotIn("scrypt", flat)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM payroll_audit WHERE action='exportacion_total'")[0]["n"], 1)


class AnalyticsTests(BusinessBase):
    def add(self, title, desc="d", cat="Hardware", pri="Media", area="Soporte TI", status="Abierto", co=1, created="2026-10-20 08:00:00", due=None, res=None, assigned=4):
        self.sql("""INSERT INTO tickets(title,description,category,priority,status,company_id,created_by,assigned_to,area,created_at,solution_due,resolved_at)
                    VALUES(%s,%s,%s,%s,%s,%s,5,%s,%s,%s,%s,%s)""", (title, desc, cat, pri, status, co, assigned, area, created, due, res))

    def test_permissions(self):
        for uid in (4, 5, 6):
            self.login(uid); self.assertEqual(self.client.get("/estadisticas").status_code, 403)
        for uid in (1, 3):
            self.login(uid); self.assertEqual(self.client.get("/estadisticas").status_code, 200)
        self.login(None); self.assertEqual(self.client.get("/estadisticas").status_code, 302)

    def test_insufficient_data_is_declared_not_simulated(self):
        self.add("Impresora no imprime")
        self.login(1); html = self.client.get("/estadisticas?titulo=impresora").get_data(as_text=True)
        self.assertEqual(html.count("Datos insuficientes"), 3)  # ANS histórico, carga y sugerencia
        self.assertIn("Faltan datos", html); self.assertNotIn(">Activa<", html)

    def test_similar_tickets_respect_company_isolation(self):
        for co in (1, 2):
            self.add("Impresora de contabilidad no imprime documentos", "La impresora muestra error de papel", co=co)
        rows = analytics.load(self._conn(), None, 3650)
        self.assertEqual(analytics.recurrent_groups(rows), [], "mismas palabras pero distinta empresa: no se agrupan")
        self.add("Impresora de contabilidad no imprime nada", "Error de papel en la impresora", co=1)
        groups = analytics.recurrent_groups(analytics.load(self._conn(), None, 3650))
        self.assertEqual(len(groups), 1); self.assertEqual(groups[0]["size"], 2); self.assertEqual(groups[0]["company"], "Alfa")

    def _conn(self):
        ctx = application.app.app_context(); ctx.push(); self.addCleanup(ctx.pop)
        return application.db()

    def test_suggestion_needs_history_and_explains(self):
        self.login(1)
        for i in range(35):
            self.add(f"Wifi lento en la oficina {i}", "La red inalámbrica va lenta", cat="Redes", pri="Alta", status="Resuelto", created="2026-08-01 08:00:00")
        html = self.client.get("/estadisticas?titulo=Wifi muy lento&descripcion=la red va lenta").get_data(as_text=True)
        self.assertIn("Redes", html); self.assertIn("Por qué", html); self.assertIn("wifi", html)
        self.assertIn("Alta", html)
        none = self.client.get("/estadisticas?titulo=xyzqw abcde").get_data(as_text=True)
        self.assertIn("No hay tickets históricos suficientemente parecidos", none)

    def test_sla_risk_rules(self):
        from datetime import datetime, timedelta
        n = datetime.utcnow(); f = "%Y-%m-%d %H:%M:%S"
        self.add("Vencido", created=(n - timedelta(hours=30)).strftime(f), due=(n - timedelta(hours=2)).strftime(f), assigned=None)
        self.add("Casi", created=(n - timedelta(hours=9)).strftime(f), due=(n + timedelta(hours=1)).strftime(f))
        self.add("Holgado", created=(n - timedelta(hours=1)).strftime(f), due=(n + timedelta(hours=40)).strftime(f))
        self.add("Resuelto", status="Resuelto", due=(n - timedelta(hours=5)).strftime(f))
        risk = analytics.sla_risk(analytics.load(self._conn(), None, 3650))
        self.assertEqual([r["level"] for r in risk], ["Vencido", "Alto"])
        self.assertIn("Sin técnico asignado", risk[0]["why"])

    def test_forecast_requires_weeks_and_runs(self):
        from datetime import datetime, timedelta
        self.add("Uno", created=(datetime.utcnow() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"))
        self.assertFalse(analytics.forecast(analytics.load(self._conn(), None, 3650))["ready"])
        for w in range(10):
            for k in range(3 + w // 3):
                self.add(f"T{w}-{k}", created=(datetime.utcnow() - timedelta(weeks=w)).strftime("%Y-%m-%d %H:%M:%S"))
        fc = analytics.forecast(analytics.load(self._conn(), None, 3650))
        self.assertTrue(fc["ready"]); self.assertGreater(fc["next_week"], 0); self.assertLessEqual(fc["low"], fc["next_week"])
