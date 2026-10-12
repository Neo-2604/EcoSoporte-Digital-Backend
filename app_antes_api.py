"""Aplica la API /api/v1 a app.py automaticamente.

Uso (en la carpeta donde estan app.py y api_v1_para_pegar.py):
    python aplicar_api.py

Hace una copia de seguridad (app_antes_api.py), inserta la API antes de
@app.errorhandler(403) y deja app.run con host 0.0.0.0.
"""
import re
import shutil
import sys
from pathlib import Path

APP = Path("app.py")
API = Path("api_v1_para_pegar.py")
BACKUP = Path("app_antes_api.py")
MARK_START = "# ===== API REST /api/v1 (inicio) ====="
MARK_END = "# ===== API REST /api/v1 (fin) ====="

for f in (APP, API):
    if not f.exists():
        sys.exit(f"No encuentro {f}. Ejecuta este script en la carpeta que contiene app.py y api_v1_para_pegar.py.")

src = APP.read_text(encoding="utf-8")
if MARK_START in src or "/api/v1/login" in src:
    sys.exit("app.py ya tiene la API. No se hizo ningun cambio.")

api_code = API.read_text(encoding="utf-8")
m = re.search(r"^from itsdangerous import", api_code, flags=re.M)
if not m:
    sys.exit("api_v1_para_pegar.py no tiene la linea 'from itsdangerous import ...'.")
api_code = api_code[m.start():].rstrip() + "\n"

anchor = "@app.errorhandler(403)"
if src.count(anchor) != 1:
    sys.exit("No encontre una unica linea '@app.errorhandler(403)' en app.py. No se hizo ningun cambio.")

old_run = 'if __name__=="__main__": app.run(debug=os.environ.get("FLASK_DEBUG")=="1")'
new_run = 'if __name__=="__main__": app.run(host="0.0.0.0", debug=os.environ.get("FLASK_DEBUG")=="1")'
if old_run not in src:
    print("Aviso: no encontre la ultima linea app.run exacta; cambiala a mano por:\n  " + new_run)
else:
    src = src.replace(old_run, new_run)

shutil.copyfile(APP, BACKUP)
block = f"{MARK_START}\n{api_code}{MARK_END}\n\n"
src = src.replace(anchor, block + anchor)
APP.write_text(src, encoding="utf-8")
print(f"Listo. Copia de seguridad: {BACKUP}. API agregada a {APP}.")