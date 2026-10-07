"""Verificación BLOQUE 4 (2026-09-20): `Postulacion.etapa` es la ÚNICA fuente de los contadores.
Tarjeta de la vacante (`candidatos`, `embudo.etapas`), pipeline global (`/metricas/pipeline` → `por_etapa`) y
lista de candidatos (`GET /candidatos`, con `vacante=` / `etapa=`) deben coincidir EXACTAMENTE, con
postulaciones cerradas, personas eliminadas y Modo Prueba de por medio. Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_contadores_etapas.py
"""

import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_b4c_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "b4c.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-b4c"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import conteos  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

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
    cuenta = Cuenta(nombre="Cuenta B4", nombre_comercial="B4 RH", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
    cfg = obtener(db)
    cfg.modo_prueba = False
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    vacs = client.get("/vacantes").json()
    V1, V2 = vacs[0]["id"], vacs[1]["id"]

    def alta(nombre, tel, vac):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": tel, "vacante": vac, "consentimiento": True, "fuente": "RH"})
        assert r.status_code in (200, 201), r.text
        return r.json()["id"]

    print("\n--- 1. Escenario con ruido: cerradas, eliminadas y Modo Prueba ---")
    P = {}
    P["a"] = alta("Ana Uno", "5511110001", V1)        # Prefiltro
    P["b"] = alta("Beto Dos", "5511110002", V1)       # → Entrevista Humana
    P["c"] = alta("Caro Tres", "5511110003", V1)      # → Contratación
    P["d"] = alta("Dani Cuatro", "5511110004", V1)    # → descartada (cerrada)
    P["e"] = alta("Eli Cinco", "5511110005", V1)      # → persona eliminada
    P["f"] = alta("Fer Seis", "5511110006", V2)       # otra vacante, Prefiltro
    client.patch(f"/candidatos/{P['b']}/etapa", json={"etapa": "Entrevista Humana", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    client.patch(f"/candidatos/{P['c']}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    r = client.post(f"/candidatos/{P['d']}/decision", json={"accion": "descartar", "comentario": "No cumple"})
    check(r.status_code == 200, "una postulación descartada (cerrada)")
    r = client.delete(f"/candidatos/{P['e']}")
    check(r.status_code == 200, "una persona eliminada (baja lógica)")
    # Modo Prueba: una postulación es_prueba cuenta en el Kanban y, por lo tanto, en todos los contadores
    cfg.modo_prueba = True
    db.commit()
    P["g"] = alta("Gus Prueba", "5511110007", V1)
    cfg.modo_prueba = False
    db.commit()
    db.expire_all()
    pg = db.query(Postulacion).filter_by(codigo=P["g"]).one()
    check(pg.es_prueba is True, "una postulación de Modo Prueba (es_prueba)")

    print("\n--- 2. Vacante: total == suma del embudo == Kanban filtrado ---")
    v1 = client.get(f"/vacantes/{V1}").json()
    kan_v1 = client.get(f"/candidatos?vacante={V1}").json()
    embudo = v1["embudo"]["etapas"]
    check(v1["candidatos"] == 4, f"tarjeta: 4 activos (a, b, c, prueba) — ni descartada ni eliminada ({v1['candidatos']})")
    check(sum(embudo.values()) == v1["candidatos"], f"suma del embudo == total de la tarjeta ({embudo})")
    check(len(kan_v1) == v1["candidatos"], f"Kanban filtrado por vacante == total de la tarjeta ({len(kan_v1)})")
    conteo_kan = Counter(x["etapa"] for x in kan_v1)
    check(dict(conteo_kan) == embudo, f"por etapa, Kanban == embudo ({dict(conteo_kan)})")
    for etapa, n in embudo.items():
        lista = client.get(f"/candidatos?vacante={V1}&etapa={etapa}").json()
        check(len(lista) == n and all(x["etapa"] == etapa and x["vacanteId"] == V1 for x in lista), f"clic en «{etapa}» ({n}) → GET /candidatos?vacante={V1}&etapa=… regresa exactamente {n}")
    listado = next(x for x in client.get("/vacantes").json() if x["id"] == V1)
    check(listado["candidatos"] == v1["candidatos"] and listado["embudo"]["etapas"] == embudo, "el listado de vacantes usa los mismos números que el detalle")

    print("\n--- 3. Pipeline global == Kanban completo ---")
    pipe = {k: v for k, v in client.get("/metricas/pipeline").json()["candidatos"]["por_etapa"].items() if v}  # el pipeline rellena con 0 las etapas vacías
    kan = client.get("/candidatos").json()
    check(dict(Counter(x["etapa"] for x in kan)) == pipe, f"por_etapa del pipeline == Kanban sin filtros ({pipe})")
    check(sum(pipe.values()) == len(kan), "total del pipeline == tarjetas del Kanban")
    v2 = client.get(f"/vacantes/{V2}").json()
    suma_vacantes = Counter()
    for vx in client.get("/vacantes").json():
        suma_vacantes.update(vx["embudo"]["etapas"])
    check(dict(suma_vacantes) == pipe, "la suma de los embudos de todas las vacantes == pipeline global")
    check(conteos.por_etapa(db, cuenta.id) == pipe and conteos.por_etapa(db, cuenta.id, db.query(Vacante).filter_by(codigo=V1).one().id) == embudo, "services.conteos es la función única detrás de todo")

    print("\n--- 4. Mover etapa / cerrar → todos los contadores cambian juntos ---")
    # Onboarding v2 (2026-09-28): a Onboarding solo se entra con «Iniciar Onboarding» o con Modo Prueba activo
    cfg.modo_prueba = True
    db.commit()
    check(client.patch(f"/candidatos/{P['a']}/etapa", json={"etapa": "Onboarding", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"}).status_code == 200, "Modo Prueba: mover directo a Onboarding")
    cfg.modo_prueba = False
    db.commit()
    r = client.post(f"/candidatos/{P['b']}/decision", json={"accion": "descartar", "comentario": "Declinó"})
    v1 = client.get(f"/vacantes/{V1}").json()
    kan_v1 = client.get(f"/candidatos?vacante={V1}").json()
    pipe = {k: v for k, v in client.get("/metricas/pipeline").json()["candidatos"]["por_etapa"].items() if v}
    check(v1["candidatos"] == 3 and v1["embudo"]["etapas"].get("Onboarding") == 1 and "Entrevista Humana" not in v1["embudo"]["etapas"], "tarjeta: 3 activos, uno en Onboarding, ninguno en Entrevista Humana")
    check(len(kan_v1) == 3 and dict(Counter(x["etapa"] for x in kan_v1)) == v1["embudo"]["etapas"], "Kanban de la vacante sigue igual a la tarjeta")
    check(dict(Counter(x["etapa"] for x in client.get("/candidatos").json())) == pipe, "pipeline global sigue igual al Kanban")
    check(len(client.get(f"/candidatos?vacante={V1}&mostrar_cerradas=true").json()) == 5, "«Mostrar cerradas» agrega las 2 descartadas (la eliminada nunca sale)")
    db.close()

print(f"\n🎉 Bloque 4 (contadores unificados por etapa) verificado: {OK} comprobaciones OK.")
