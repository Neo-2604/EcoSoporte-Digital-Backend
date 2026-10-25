"""
EcoSoporte Digital · Estadísticas de tickets con analítica asistida (sin servicios externos ni costos).

Todo se calcula con los datos REALES de PostgreSQL, en el servidor, y cada resultado indica su origen:
  · Regla      — regla fija y explicable (p. ej. «vence en menos del 20 % del plazo»).
  · Estadística — método estadístico simple (similitud de texto TF-IDF, regresión lineal, promedios).
Cuando no hay datos suficientes el módulo lo dice y NO muestra resultados inventados: los mínimos están en MIN_*.
Las sugerencias no se aplican solas: las decide el equipo. Para un modelo entrenado (p. ej. Vertex AI) este módulo
deja listas la medición de datos y la interfaz; no se activa ningún servicio de Google Cloud.
"""
import math, re, unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, g, render_template, request

bp = Blueprint("analytics", __name__)
_db = _roles = None

MIN_LABELED = 30      # tickets históricos para sugerir categoría/prioridad/área
MIN_WEEKS = 6         # semanas de historia para predecir carga
MIN_SLA = 10          # tickets resueltos con plazo para hablar de cumplimiento histórico
SIM_RECURRENT = 0.55  # similitud mínima para considerar tickets recurrentes
SIM_SUGGEST = 0.12    # similitud mínima del vecino más cercano para sugerir
OPEN = ("Abierto", "En proceso", "En espera")
STOP = set("""de la el en y a los las un una unos unas por para con sin del al lo que se su sus es son fue ser esta este estos estas mi tu nos
me le les muy mas pero como cuando donde ya no si o u hay tiene tienen tengo hacer favor ayuda necesito quiero problema falla""".split())


def guard(*roles):
    def deco(fn):
        @wraps(fn)
        def w(*a, **k): return _roles(*roles)(fn)(*a, **k)
        return w
    return deco


def tokens(text):
    t = unicodedata.normalize("NFD", (text or "").lower())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return [w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in STOP]


class Tfidf:
    def __init__(self, docs):
        self.n = len(docs)
        df = Counter()
        for d in docs: df.update(set(d))
        self.idf = {w: math.log((self.n + 1) / (c + 1)) + 1 for w, c in df.items()}

    def vec(self, toks):
        tf = Counter(toks); v = {w: (1 + math.log(c)) * self.idf.get(w, math.log(self.n + 1) + 1) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}


def cosine(a, b):
    if len(a) > len(b): a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def _code(t):
    return f"TCK-{str(t['created_at'])[:4]}-{t['id']:04d}"


def _parse(ts):
    return datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S") if ts else None


def load(c, company_id, days):
    where, args = ["t.created_at>=%s"], [(datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")]
    if company_id: where.append("t.company_id=%s"); args.append(company_id)
    return c.execute(f"""SELECT t.id,t.title,t.description,t.category,t.priority,t.area,t.status,t.company_id,t.assigned_to,t.created_at,
                         t.resolved_at,t.solution_due,co.name company FROM tickets t LEFT JOIN companies co ON co.id=t.company_id
                         WHERE {' AND '.join(where)} ORDER BY t.id""", args).fetchall()


def recurrent_groups(rows):
    """Tickets similares de la MISMA empresa (el aislamiento entre empresas se respeta): componentes con similitud ≥ umbral."""
    rows = rows[-300:]
    if len(rows) < 2: return []
    toks = [tokens(f"{r['title']} {r['title']} {r['description']}") for r in rows]
    model = Tfidf(toks); vecs = [model.vec(t) for t in toks]
    parent = list(range(len(rows)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    best = {}
    for i in range(len(rows)):
        for j in range(i):
            if rows[i]["company_id"] != rows[j]["company_id"]: continue
            s = cosine(vecs[i], vecs[j])
            if s >= SIM_RECURRENT:
                parent[find(i)] = find(j); best[(j, i)] = s
    groups = defaultdict(list)
    for i in range(len(rows)): groups[find(i)].append(i)
    out = []
    for idx in groups.values():
        if len(idx) < 2: continue
        shared = Counter()
        for i in idx: shared.update(set(toks[i]))
        common = [w for w, n in shared.most_common(6) if n >= 2][:4]
        scores = [s for (a, b), s in best.items() if a in idx and b in idx]
        out.append(dict(size=len(idx), company=rows[idx[0]]["company"] or "Sin empresa", terms=common,
                        similarity=round(sum(scores) / len(scores), 2) if scores else None,
                        tickets=[dict(code=_code(rows[i]), id=rows[i]["id"], title=rows[i]["title"], status=rows[i]["status"]) for i in idx[-5:]]))
    return sorted(out, key=lambda g: -g["size"])[:8]


def suggest(history, title, description):
    """Vecinos más cercanos (TF-IDF). Devuelve dict con sugerencias y su explicación, o motivo de datos insuficientes."""
    if len(history) < MIN_LABELED:
        return dict(ready=False, reason=f"Se necesitan al menos {MIN_LABELED} tickets históricos y hay {len(history)}.")
    docs = [tokens(f"{h['title']} {h['title']} {h['description']}") for h in history]
    model = Tfidf(docs); q = model.vec(tokens(f"{title} {title} {description}"))
    if not q: return dict(ready=False, reason="Escribe un título o descripción con más detalle.")
    sims = sorted(((cosine(q, model.vec(d)), h, d) for d, h in zip(docs, history)), key=lambda x: -x[0])[:5]
    sims = [s for s in sims if s[0] > 0]
    if not sims or sims[0][0] < SIM_SUGGEST:
        return dict(ready=False, reason="No hay tickets históricos suficientemente parecidos para sugerir con fiabilidad.")
    out = dict(ready=True, similar=[dict(code=_code(h), title=h["title"], score=round(s, 2), category=h["category"]) for s, h, _ in sims])
    total = sum(s for s, *_ in sims)
    for field in ("category", "priority", "area"):
        votes = Counter()
        for s, h, _ in sims: votes[h[field]] += s
        val, w = votes.most_common(1)[0]
        out[field] = dict(value=val, confidence=round(w / total, 2))
    qset = set(q); shared = Counter()
    for s, h, d in sims: shared.update(qset & set(d))
    out["terms"] = [w for w, _ in shared.most_common(5)]
    return out


def weekly_counts(rows, weeks=12):
    today = datetime.utcnow().date(); monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    cnt = Counter()
    for r in rows:
        d = _parse(r["created_at"]).date(); cnt[d - timedelta(days=d.weekday())] += 1
    return [(s, cnt.get(s, 0)) for s in starts]


def forecast(rows):
    if not rows: return dict(ready=False, reason="Aún no hay tickets en el periodo.")
    first = min(_parse(r["created_at"]) for r in rows)
    weeks_of_history = (datetime.utcnow() - first).days // 7 + 1
    if weeks_of_history < MIN_WEEKS:
        return dict(ready=False, reason=f"Se necesitan {MIN_WEEKS} semanas de historial y hay {weeks_of_history}.")
    series = [n for _, n in weekly_counts(rows, 9)[:-1]]  # sin la semana en curso (incompleta)
    n = len(series); xs = range(n); mx, my = (n - 1) / 2, sum(series) / n
    sxx = sum((x - mx) ** 2 for x in xs) or 1
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, series)) / sxx
    pred = max(0.0, my + slope * (n - mx))
    resid = math.sqrt(sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, series)) / max(1, n - 2))
    return dict(ready=True, next_week=round(pred, 1), low=max(0, round(pred - resid, 1)), high=round(pred + resid, 1),
                avg=round(my, 1), slope=round(slope, 2), method="Regresión lineal sobre las últimas 8 semanas completas")


def sla_risk(rows):
    now = datetime.utcnow(); out = []
    for r in rows:
        if r["status"] not in OPEN or not r["solution_due"]: continue
        due, created = _parse(r["solution_due"]), _parse(r["created_at"])
        total = max(1.0, (due - created).total_seconds()); left = (due - now).total_seconds(); ratio = left / total
        level = "Vencido" if left < 0 else ("Alto" if ratio < 0.2 else ("Medio" if ratio < 0.5 else None))
        if not level: continue
        why = [f"Quedan {max(0, left) / 3600:.1f} h de {total / 3600:.0f} h del plazo" if left >= 0 else f"Superó el plazo hace {(-left) / 3600:.1f} h",
               f"Prioridad {r['priority']}"]
        if not r["assigned_to"]: why.append("Sin técnico asignado")
        if r["status"] == "En espera": why.append("Está en espera")
        out.append(dict(code=_code(r), id=r["id"], title=r["title"], level=level, why=why, ratio=ratio))
    order = {"Vencido": 0, "Alto": 1, "Medio": 2}
    return sorted(out, key=lambda x: (order[x["level"]], x["ratio"]))[:10]


def sla_history(rows):
    done = [r for r in rows if r["resolved_at"] and r["solution_due"]]
    if len(done) < MIN_SLA: return dict(ready=False, reason=f"Se necesitan {MIN_SLA} tickets resueltos con plazo y hay {len(done)}.")
    ok = sum(1 for r in done if r["resolved_at"] <= r["solution_due"])
    by = defaultdict(lambda: [0, 0])
    for r in done:
        by[r["category"]][0] += 1; by[r["category"]][1] += r["resolved_at"] <= r["solution_due"]
    return dict(ready=True, pct=round(100 * ok / len(done), 1), n=len(done),
                by=[(k, v[0], round(100 * v[1] / v[0])) for k, v in sorted(by.items(), key=lambda kv: -kv[1][0])])


def recommendations(rows):
    recs, now = [], datetime.utcnow()
    unassigned = [r for r in rows if r["status"] in OPEN and not r["assigned_to"]]
    if unassigned: recs.append(("Regla", f"Hay {len(unassigned)} tickets abiertos sin técnico asignado.", "Asígnalos desde Áreas y asignaciones para proteger el ANS."))
    cut1, cut2 = now - timedelta(days=28), now - timedelta(days=56)
    recent, prev = Counter(), Counter()
    for r in rows:
        d = _parse(r["created_at"])
        if d >= cut1: recent[r["category"]] += 1
        elif d >= cut2: prev[r["category"]] += 1
    for cat, n in recent.most_common():
        p = prev.get(cat, 0)
        if n >= 5 and n >= 1.5 * max(p, 1):
            recs.append(("Estadística", f"«{cat}» pasó de {p} a {n} tickets en las últimas 4 semanas.", "Revisa si hay una causa común y evalúa mantenimiento preventivo o capacitación."))
    return recs


def breakdown(rows, key, top=8):
    return Counter(r[key] or "—" for r in rows).most_common(top)


@bp.route("/estadisticas")
@guard("Administrador", "Soporte TI")
def index():
    c = _db()
    days = request.args.get("dias", "90"); days = int(days) if days in ("30", "90", "180", "365") else 90
    cid = request.args.get("empresa", ""); cid = int(cid) if cid.isdigit() else None
    rows = load(c, cid, days)
    hist = load(c, cid, 730)
    title, desc = request.args.get("titulo", "").strip()[:160], request.args.get("descripcion", "").strip()[:1500]
    sug = suggest(hist, title, desc) if (title or desc) else None
    daily = Counter(_parse(r["created_at"]).strftime("%m-%d") for r in rows)
    today = datetime.utcnow().date()
    last30 = [((today - timedelta(days=29 - i)).strftime("%m-%d"), 0) for i in range(30)]
    last30 = [(d, daily.get(d, 0)) for d, _ in last30]
    companies = c.execute("SELECT id,name FROM companies ORDER BY name").fetchall()
    readiness = [("Sugerencias de categoría, prioridad y área", len(hist), MIN_LABELED, "tickets"),
                 ("Predicción de carga", min(len({_parse(r['created_at']).date() - timedelta(days=_parse(r['created_at']).weekday()) for r in hist}), 99), MIN_WEEKS, "semanas con tickets"),
                 ("Cumplimiento histórico de ANS", sum(1 for r in hist if r["resolved_at"] and r["solution_due"]), MIN_SLA, "tickets resueltos con plazo")]
    return render_template("analytics.html", rows=rows, days=days, cid=cid, companies=companies, last30=last30, by_status=breakdown(rows, "status"),
                           by_cat=breakdown(rows, "category"), by_pri=breakdown(rows, "priority"), by_area=breakdown(rows, "area"),
                           groups=recurrent_groups(rows), fc=forecast(hist), risk=sla_risk(load(c, cid, 365)), sla=sla_history(rows),
                           recs=recommendations(hist), sug=sug, title=title, desc=desc, readiness=readiness, min_sim=SIM_RECURRENT)


def setup(app, db, roles_required):
    global _db, _roles
    _db, _roles = db, roles_required
    app.register_blueprint(bp)
