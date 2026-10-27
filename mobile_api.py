"""API móvil (/api/v1) de las pantallas nuevas: desempeño y nómina, catálogo, cotizaciones y estadísticas.

Reutiliza los cálculos y las reglas de permisos de payroll, catalog y analytics (no los duplica). Solo lectura salvo la
actualización de cotizaciones por el personal. La aprobación y el pago de nómina siguen haciéndose en la web."""
import json
from collections import Counter
from datetime import datetime, timedelta

from flask import abort, g, jsonify, request

import analytics
import catalog
import extras
import payroll

STAFF = ("Administrador", "Soporte TI")


def _role_guard(*roles):
    def deco(fn):
        def w(*a, **k):
            if g.user["role"] not in roles:
                return jsonify(error="No tienes permiso para esta sección."), 403
            return fn(*a, **k)
        w.__name__ = fn.__name__
        return w
    return deco


def _num(v):
    return None if v is None else round(float(v), 4)


def _perf_json(item, show_money):
    p, b, u = item["perf"], item["bonus"], item["user"]
    out = dict(id=u["id"], name=u["name"], tech_code=u["tech_code"], tickets=p["count"], excluded=p["excluded"], points=_num(p["points"]),
               quality=_num(p["quality"]), ans=_num(p["ans"]), satisfaction=_num(p["satisfaction"]), ratings=p["ratings"],
               bonus=None)
    if b:
        out["bonus"] = dict(eligible=b["eligible"], bonus_points=_num(b["bonus_points"]), amount=b["amount"] if show_money else None,
                            conditions=[dict(label=l, value=v, rule=r, ok=bool(ok)) for l, v, r, ok in b["conditions"]])
    return out


def _settlement_json(s, detail=False):
    out = dict(id=s["id"], period=s["period"], user_id=s["user_id"], user_name=s["user_name"], user_code=s["user_code"], status=s["status"],
               base_salary=s["base_salary"], transport_aid=s["transport_aid"], bonus_amount=s["bonus_amount"], total=s["total"],
               points=_num(s["points"]), tickets=s["tickets_count"], quality=_num(s["quality"]), ans=_num(s["ans"]),
               satisfaction=_num(s["satisfaction"]), bonus_points=_num(s["bonus_points"]), approved_name=s["approved_name"],
               paid_at=s["paid_at"], payment_ref=s["payment_ref"])
    if detail:
        data = json.loads(s["breakdown"] or "{}")
        b = data.get("bonus") or {}
        out["lines"] = [dict(label=a, value=b_, note=c) for a, b_, c in b.get("lines", [])]
        out["conditions"] = [dict(label=l, value=v, rule=r, ok=bool(ok)) for l, v, r, ok in b.get("conditions", [])]
    return out


def _quote_json(q, events=None):
    out = dict(id=q["id"], code=catalog.code(q), item_name=q["item_name"], name=q["name"], email=q["email"], phone=q["phone"],
               company=q["company"], city=q["city"], quantity=q["quantity"], message=q["message"], status=q["status"],
               quoted_amount=q["quoted_amount"], created_at=q["created_at"])
    if events is not None:
        out["events"] = [dict(user_name=e["user_name"], status=e["status"], note=e["note"], public=bool(e["public"]), created_at=e["created_at"])
                         for e in events]
    return out


def setup(app, db, api_auth):
    P = "/api/v1"

    # ------------------------------------------------------------ desempeño y nómina
    @app.get(f"{P}/performance")
    @api_auth
    @_role_guard("Administrador", "Soporte TI", "Técnico")
    def api_performance():
        c, role = db(), g.user["role"]
        period = request.args.get("periodo", "").strip()
        if not payroll.valid_period(period): period = payroll.current_period()
        team, params = payroll._team_performance(c, period, None, role, g.user["id"])
        show_money = role in ("Administrador", "Técnico")
        rows = payroll._settlement_rows(c, None, None, "", role, g.user["id"]) if show_money else []
        periods = sorted({r["period"] for r in rows} | {period}, reverse=True)
        return jsonify(
            period=period, periods=periods, role=role, show_money=show_money,
            params=None if not params else dict(goal_points=_num(params["goal_points"]), bonus_per_point=params["bonus_per_point"],
                                                max_bonus_points=_num(params["max_bonus_points"]), min_quality=_num(params["min_quality"]),
                                                min_ans=_num(params["min_ans"]), min_satisfaction=_num(params["min_satisfaction"])),
            team=[_perf_json(t, show_money) for t in team],
            settlements=[_settlement_json(s) for s in rows[:36]], legal_note=payroll.LEGAL_NOTE)

    @app.get(f"{P}/payroll/settlements/<int:sid>")
    @api_auth
    @_role_guard("Administrador", "Técnico")
    def api_settlement(sid):
        s = db().execute("SELECT * FROM payroll_settlements WHERE id=%s", (sid,)).fetchone()
        if not s: return jsonify(error="No encontrado"), 404
        if g.user["role"] == "Técnico" and not (s["user_id"] == g.user["id"] and s["status"] in (payroll.APPROVED, payroll.PAID)):
            return jsonify(error="No tienes permiso para ver esta liquidación."), 403
        return jsonify(settlement=_settlement_json(s, detail=True))

    # ------------------------------------------------------------ catálogo (lectura para el personal)
    @app.get(f"{P}/catalog")
    @api_auth
    def api_catalog():
        c = db()
        q = request.args.get("q", "").strip()[:80]
        where, args = ["published"], []
        if q: where.append(catalog._search_clause(q, args))
        items = c.execute(f"SELECT * FROM catalog_items WHERE {' AND '.join(where)} ORDER BY sort_order, name", args).fetchall()
        return jsonify(items=[dict(id=i["id"], code=catalog.item_code(i["id"]), kind=i["kind"], category=i["category"], name=i["name"],
                                   summary=i["summary"], description=i["description"], price=catalog.price_label(i)) for i in items])

    # ------------------------------------------------------------ cotizaciones (Administrador y Soporte TI)
    @app.get(f"{P}/quotes")
    @api_auth
    @_role_guard(*STAFF)
    def api_quotes():
        c = db(); status = request.args.get("estado", "")
        where, args = ["1=1"], []
        if status in catalog.QUOTE_STATUSES: where.append("status=%s"); args.append(status)
        rows = c.execute(f"SELECT * FROM quote_requests WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 100", args).fetchall()
        counts = {s: 0 for s in catalog.QUOTE_STATUSES}
        for r in c.execute("SELECT status, COUNT(*) n FROM quote_requests GROUP BY status").fetchall(): counts[r["status"]] = r["n"]
        return jsonify(quotes=[_quote_json(r) for r in rows], counts=counts, statuses=catalog.QUOTE_STATUSES)

    @app.get(f"{P}/quotes/<int:qid>")
    @api_auth
    @_role_guard(*STAFF)
    def api_quote(qid):
        c = db(); q = c.execute("SELECT * FROM quote_requests WHERE id=%s", (qid,)).fetchone()
        if not q: return jsonify(error="No encontrado"), 404
        events = c.execute("SELECT * FROM quote_events WHERE quote_id=%s ORDER BY id", (qid,)).fetchall()
        return jsonify(quote=_quote_json(q, events), statuses=catalog.QUOTE_STATUSES)

    @app.post(f"{P}/quotes/<int:qid>")
    @api_auth
    @_role_guard(*STAFF)
    def api_quote_update(qid):
        c = db(); q = c.execute("SELECT * FROM quote_requests WHERE id=%s", (qid,)).fetchone()
        if not q: return jsonify(error="No encontrado"), 404
        d = request.get_json(silent=True) or {}
        status = d.get("status", q["status"])
        note = str(d.get("note") or "").strip()[:600]
        public = d.get("public") is True
        if status not in catalog.QUOTE_STATUSES: return jsonify(error="Estado no válido"), 400
        amount = None
        if d.get("quoted_amount") not in (None, ""):
            try: amount = int(float(str(d["quoted_amount"]).replace(".", "").replace(",", ".")))
            except ValueError: amount = -1
            if not 1 <= amount <= 1_000_000_000: return jsonify(error="El valor cotizado debe ser un número positivo."), 400
        if status == "Cotización enviada" and not (amount or q["quoted_amount"]):
            return jsonify(error="Para marcarla como «Cotización enviada» indica el valor cotizado."), 400
        now = catalog._now()
        c.execute("UPDATE quote_requests SET status=%s,quoted_amount=COALESCE(%s,quoted_amount),assigned_to=%s,updated_at=%s WHERE id=%s",
                  (status, amount, g.user["id"], now, qid))
        if status != q["status"]:
            c.execute("INSERT INTO quote_events(quote_id,user_name,status,note,public,created_at) VALUES(%s,%s,%s,%s,TRUE,%s)",
                      (qid, g.user["name"], status, f"Estado: {status}", now))
        if note:
            c.execute("INSERT INTO quote_events(quote_id,user_name,status,note,public,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
                      (qid, g.user["name"], status, note, public, now))
        if q["user_id"] and status != q["status"]:
            extras.notify(c, "cotizacion", f"Tu cotización {catalog.code(q)} ahora está: {status}", None, g.user["id"], g.user["name"], q["user_id"])
        c.commit()
        return jsonify(ok=True)

    # ------------------------------------------------------------ estadísticas (Administrador y Soporte TI)
    @app.get(f"{P}/stats")
    @api_auth
    @_role_guard(*STAFF)
    def api_stats():
        c = db()
        days = request.args.get("dias", "90"); days = int(days) if days in ("30", "90", "180", "365") else 90
        rows, hist = analytics.load(c, None, days), analytics.load(c, None, 730)
        today = datetime.utcnow().date()
        daily = Counter(analytics._parse(r["created_at"]).date() for r in rows)
        last30 = [dict(day=(today - timedelta(days=29 - i)).strftime("%m-%d"), n=daily.get(today - timedelta(days=29 - i), 0)) for i in range(30)]
        def bd(key): return [dict(label=k, n=n) for k, n in analytics.breakdown(rows, key)]
        sla = analytics.sla_history(rows)
        if sla.get("ready"): sla = dict(sla, by=[dict(category=k, n=n, pct=p) for k, n, p in sla["by"]])
        return jsonify(
            days=days, total=len(rows), by_status=bd("status"), by_category=bd("category"), by_priority=bd("priority"), by_area=bd("area"),
            last30=last30, forecast=analytics.forecast(hist), risk=[{k: v for k, v in r.items() if k != "ratio"} for r in analytics.sla_risk(analytics.load(c, None, 365))],
            sla=sla, recurrent=analytics.recurrent_groups(rows),
            recommendations=[dict(kind=k, text=t, action=a) for k, t, a in analytics.recommendations(hist)])
