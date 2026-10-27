import unittest

import app as application
from tests import test_business as tb
from tests.test_business import BusinessBase


class MobileApiBase(BusinessBase):
    def hdr(self, uid):
        with application.app.app_context():
            return {"Authorization": "Bearer " + application._ser().dumps({"uid": uid})}

    def get(self, uid, path):
        return self.client.get("/api/v1" + path, headers=self.hdr(uid))

    def post(self, uid, path, data):
        return self.client.post("/api/v1" + path, json=data, headers=self.hdr(uid))


class PerformanceApi(MobileApiBase):
    approve_basics = tb.PayrollTests.approve_basics
    add_tickets = tb.PayrollTests.add_tickets

    def setUp(self):
        super().setUp()
        self.approve_basics(); self.add_tickets(basic=30, medium=10)
        self.add_tickets_other = None
        self.login(1)
        self.client.post("/nomina/generar", data=dict(periodo="2026-09"))
        self.sql("UPDATE payroll_settlements SET status='Aprobado' WHERE user_id=4")

    def test_technician_sees_only_own(self):
        d = self.get(4, "/performance?periodo=2026-09").get_json()
        self.assertEqual([t["id"] for t in d["team"]], [4])
        self.assertTrue(d["show_money"])
        self.assertTrue(all(s["user_id"] == 4 for s in d["settlements"])); self.assertTrue(d["settlements"])
        self.assertGreater(d["team"][0]["bonus"]["amount"], 0)

    def test_support_sees_performance_without_money(self):
        d = self.get(3, "/performance?periodo=2026-09").get_json()
        self.assertFalse(d["show_money"]); self.assertEqual(d["settlements"], [])
        self.assertTrue(len(d["team"]) >= 2)
        self.assertTrue(all(t["bonus"] is None or t["bonus"]["amount"] is None for t in d["team"]))

    def test_admin_sees_everything(self):
        d = self.get(1, "/performance?periodo=2026-09").get_json()
        self.assertTrue(d["show_money"]); self.assertTrue(len(d["team"]) >= 2)

    def test_clients_blocked_and_unauthenticated(self):
        self.assertEqual(self.get(5, "/performance").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/performance").status_code, 401)

    def test_settlement_detail_permissions(self):
        sid = self.sql("SELECT id FROM payroll_settlements WHERE user_id=4")[0]["id"]
        d = self.get(4, f"/payroll/settlements/{sid}").get_json()["settlement"]
        self.assertEqual(d["user_id"], 4); self.assertTrue(d["lines"])
        self.assertEqual(self.get(7, f"/payroll/settlements/{sid}").status_code, 403)   # otro técnico
        self.assertEqual(self.get(3, f"/payroll/settlements/{sid}").status_code, 403)   # Soporte TI sin dinero
        self.sql("UPDATE payroll_settlements SET status='Pendiente de revisión' WHERE id=%s", (sid,))
        self.assertEqual(self.get(4, f"/payroll/settlements/{sid}").status_code, 403)   # pendiente: solo admin
        self.assertEqual(self.get(1, f"/payroll/settlements/{sid}").status_code, 200)
        self.assertEqual(self.get(1, "/payroll/settlements/99999").status_code, 404)


class CatalogQuotesApi(MobileApiBase):
    def make_quote(self):
        self.client.post("/catalogo/cotizar", data=dict(tb.CatalogTests.VALID, item_id="1"))
        return self.sql("SELECT id FROM quote_requests ORDER BY id DESC LIMIT 1")[0]["id"]

    def test_catalog_lists_only_published_with_code(self):
        self.assertEqual(self.get(4, "/catalog").get_json()["items"], [])
        self.sql("UPDATE catalog_items SET published=TRUE WHERE id=1")
        items = self.get(4, "/catalog").get_json()["items"]
        self.assertEqual(len(items), 1); self.assertEqual(items[0]["code"], "CAT-0001")
        self.assertEqual(len(self.get(4, "/catalog?q=cat-1").get_json()["items"]), 1)
        self.assertEqual(self.get(4, "/catalog?q=zzzz").get_json()["items"], [])

    def test_quotes_only_for_admin_and_support(self):
        qid = self.make_quote()
        self.assertEqual(self.get(4, "/quotes").status_code, 403)
        self.assertEqual(self.get(5, "/quotes").status_code, 403)
        d = self.get(3, "/quotes").get_json()
        self.assertEqual(len(d["quotes"]), 1); self.assertTrue(d["quotes"][0]["code"].startswith("COT-"))
        self.assertEqual(self.get(1, f"/quotes/{qid}").status_code, 200)
        self.assertEqual(self.get(1, "/quotes/9999").status_code, 404)

    def test_quote_update_rules_and_events(self):
        qid = self.make_quote()
        self.assertEqual(self.post(4, f"/quotes/{qid}", {"status": "En revisión"}).status_code, 403)
        self.assertEqual(self.post(3, f"/quotes/{qid}", {"status": "Inventado"}).status_code, 400)
        self.assertEqual(self.post(3, f"/quotes/{qid}", {"status": "Cotización enviada"}).status_code, 400)   # sin valor
        self.assertEqual(self.post(3, f"/quotes/{qid}", {"status": "Cotización enviada", "quoted_amount": "-5"}).status_code, 400)
        r = self.post(3, f"/quotes/{qid}", {"status": "Cotización enviada", "quoted_amount": "250000", "note": "Nota interna"})
        self.assertEqual(r.status_code, 200)
        q = self.sql("SELECT status, quoted_amount FROM quote_requests WHERE id=%s", (qid,))[0]
        self.assertEqual((q["status"], q["quoted_amount"]), ("Cotización enviada", 250000))
        ev = self.sql("SELECT note, public FROM quote_events WHERE quote_id=%s ORDER BY id", (qid,))
        self.assertTrue(any(e["note"] == "Nota interna" and not e["public"] for e in ev))   # la nota interna no es pública
        self.assertTrue(any(e["public"] and "Estado" in e["note"] for e in ev))


class StatsApi(MobileApiBase):
    def test_stats_shape_and_permissions(self):
        for uid in (1, 3):
            d = self.get(uid, "/stats").get_json()
            for k in ("total", "by_status", "last30", "forecast", "risk", "sla", "recurrent", "recommendations"):
                self.assertIn(k, d)
            self.assertEqual(len(d["last30"]), 30)
        self.assertEqual(self.get(4, "/stats").status_code, 403)
        self.assertEqual(self.get(6, "/stats").status_code, 403)

    def test_stats_reports_insufficient_data_instead_of_inventing(self):
        d = self.get(1, "/stats").get_json()
        self.assertFalse(d["forecast"]["ready"]); self.assertFalse(d["sla"]["ready"])


if __name__ == "__main__":
    unittest.main()
