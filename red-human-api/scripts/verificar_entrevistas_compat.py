"""Crash de la sección «Entrevistas» (2026-10-12). Base desechable, SIN red.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_entrevistas_compat.py

Causa: la vista leía `estadoEntrevista[e.estado].tone` y `calif_experiencia.toFixed()` sin protección. Una entrevista
«cancelada» (estado nuevo del 2026-10-11, p. ej. la videollamada cancelada por error) o una evaluación con campos nulos
(entrevista operativa sin puntaje, evaluaciones viejas) la tumbaban. Cubre: `GET /entrevistas` y `/entrevistas/metricas`
responden con la evaluación SIEMPRE tipada (números, listas, recomendación válida) y la página cubre todos los estados de
`models.ESTADOS_ENTREVISTA` con un respaldo para cualquier otro.
"""

import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_entrevistas_compat_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "compat.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-compat"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ESTADOS_ENTREVISTA, Cuenta, Entrevista, Postulacion, Usuario  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    app.dependency_overrides[usuario_actual] = lambda: admin
    p = db.query(Postulacion).first()
    cuenta = Cuenta(nombre="Compat SA", nombre_comercial="Compat", estado="Activa")
    db.add(cuenta)
    db.flush()
    p.cuenta_id = cuenta.id
    p.candidato.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    def entrevista(estado, evaluacion=None):
        e = Entrevista(codigo=f"ENT-{secrets.token_hex(3)}", candidato_id=p.candidato_id, postulacion_id=p.id,
                       token=secrets.token_hex(16), estado=estado, evaluacion=evaluacion, tipo="whatsapp")
        db.add(e)
        db.commit()
        return e.codigo

    cancelada = entrevista("cancelada")
    # entrevista operativa (sin puntaje) y una evaluación vieja a la que le faltan campos
    operativa = entrevista("evaluada", {"resumen": "Operativa", "recomendacion": "no_avanzar", "score_entrevista": None,
                                         "calif_experiencia": None, "match_perfil": None,
                                         "operativa": {"recomendacion": "con_reservas", "faltan": ["? Inicio ≤ 14 días"]}})
    vieja = entrevista("evaluada", {"resumen": "Vieja", "recomendacion": "otra"})

    r = client.get("/entrevistas")
    check(r.status_code == 200, f"GET /entrevistas responde ({r.status_code})")
    por = {x["id"]: x for x in r.json()}
    check(por[cancelada]["estado"] == "cancelada" and por[cancelada]["evaluacion"] is None, "la entrevista cancelada viaja sin evaluación")
    for cod in (operativa, vieja):
        ev = por[cod]["evaluacion"]
        check(isinstance(ev["calif_experiencia"], float) and isinstance(ev["calif_comunicacion"], float) and isinstance(ev["match_perfil"], int)
              and ev["recomendacion"] in ("avanzar", "revision", "no_avanzar") and isinstance(ev["fortalezas"], list) and isinstance(ev["riesgos"], list),
              f"{cod}: la evaluación sale tipada aunque le falten campos o vengan nulos")
    check(por[operativa]["evaluacion"]["operativa"]["recomendacion"] == "con_reservas" and por[operativa]["evaluacion"]["score_entrevista"] is None,
          "la recomendación operativa (sin puntaje) se conserva")
    r = client.get("/entrevistas/metricas")
    check(r.status_code == 200 and isinstance(r.json()["match_promedio"], int), "las métricas no truenan con un match nulo")

    pagina = (RAIZ.parent / "red-human-app" / "app" / "dashboard" / "entrevistas" / "page.tsx").read_text(encoding="utf-8")
    mapa = pagina[pagina.index("const estadoEntrevista"):pagina.index("};", pagina.index("const estadoEntrevista"))]
    faltan = [e for e in ESTADOS_ENTREVISTA if not re.search(rf"\b{e}:", mapa)]
    check(not faltan, f"la página cubre todos los estados de la entrevista (faltan: {faltan})")
    check("estadoEntrevista[e.estado]" not in pagina and ".toFixed(" not in pagina.replace("v.toFixed(1)", ""),
          "la página usa respaldos (estado desconocido, números nulos): ningún acceso directo que truene")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — compatibilidad de la sección Entrevistas")
