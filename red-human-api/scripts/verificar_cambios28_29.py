"""Cambios 28 y 29 (2026-10-12). Base desechable; IA, Bot API y Meta SIMULADAS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambios28_29.py

Bloque 1 (Cambio 28): catálogo ESTRICTO de actividades por etapa («Otra actividad» en todas); «Documentos» y «Carta de
intención» sin «(opcional)»; sin casilla «Obligatoria»; descripción fija por etapa; «Partir de:» al crear un proceso.
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
_dir = tempfile.mkdtemp(prefix="rh_c28_29_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c28.db").replace(chr(92), "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "RESEND_API_KEY",
          "TELEGRAM_WEBHOOK_SECRET", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:PRUEBA-token-falso"
os.environ["TELEGRAM_BOT_USERNAME"] = "RedHumanPruebaBot"
os.environ["ADMIN_PASSWORD"] = "prueba-c28"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ACTIVIDADES_POR_ETAPA, Cuenta, Usuario, UsuarioCuenta  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
APP_DIR = RAIZ.parent / "red-human-app"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def leer(rel):
    return (APP_DIR / rel).read_text(encoding="utf-8")


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="C28", nombre_comercial="Operaciones MX", estado="Activa", slug="c28")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ================= Bloque 1 · Cambio 28: estructura del editor y plantillas =================
    print("\n--- Bloque 1 · catálogo estricto por etapa ---")
    op = client.get("/procesos/opciones").json()
    etapas = {e["valor"]: e for e in op["etapas"]}
    check(all("otra" in e["actividades"] for e in op["etapas"]), "«Otra actividad» se ofrece en todas las etapas")
    check(etapas["Prefiltro"]["actividades"] == list(ACTIVIDADES_POR_ETAPA["Prefiltro"])
          and "entrevista_humana" not in etapas["Prefiltro"]["actividades"]
          and "condiciones" not in etapas["Entrevista IA"]["actividades"]
          and "alta" in etapas["Onboarding"]["actividades"] and "alta" not in etapas["Contratación"]["actividades"],
          "cada etapa ofrece solo su catálogo")
    check(etapas["Prefiltro"]["descripcion"] == "Solicitud y primeras preguntas."
          and all(e["descripcion"] for e in op["etapas"]), "cada etapa trae su descripción fija")
    nombres = {t["valor"]: t["texto"] for t in op["tiposPaso"]}
    check(nombres["documentos"] == "Documentos" and nombres["carta_intencion"] == "Carta de intención"
          and not any("(opcional)" in t for t in nombres.values()), "«Documentos» y sin «(opcional)» en el catálogo")
    check(all(t.get("obligatorio", True) for t in op["tiposPaso"]), "toda actividad nace obligatoria")

    r = client.post("/procesos/plantillas", json={"nombre": "Mal armado", "pasos": [
        {"tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"}, {"tipo": "condiciones", "etapa": "Prefiltro"}]})
    check(r.status_code == 400 and "no puede ir en" in r.json()["detail"], f"una actividad fuera de su etapa → 400 ({r.status_code})")
    r = client.post("/procesos/plantillas", json={"nombre": "Con otra", "pasos": [
        {"tipo": "otra", "etapa": "Prefiltro", "nombre": "Llamar al candidato"}, {"tipo": "otra", "etapa": "Onboarding"}]})
    check(r.status_code == 201, f"«Otra actividad» en cualquier etapa ({r.status_code})")
    viejo = [{"id": "cv", "tipo": "analisis_cv", "etapa": "Entrevista IA"}]
    check(sproc.normalizar_pasos(viejo, previos=viejo)[0]["etapa"] == "Entrevista IA",
          "una actividad ya guardada fuera del catálogo nuevo se conserva (rutas previas)")
    check(len(sproc.normalizar_pasos(viejo)) == 1, "las copias congeladas se siguen normalizando sin el catálogo estricto")

    print("\n--- Bloque 1 · «Partir de:» ---")
    partir = {o["clave"]: o for o in op["partirDe"]}
    check([o["nombre"] for o in op["partirDe"]] == ["Masivos sin documentos", "Masivos con documentos", "Corporativos", "En blanco"],
          "«Partir de:» ofrece Masivos sin documentos, Masivos con documentos, Corporativos y En blanco")
    check(not partir["blanco"]["pasos"] and any(p["tipo"] == "analisis_cv" for p in partir["corporativo"]["pasos"])
          and any(p["id"] == "documentos-iniciales" for p in partir["masivos_con_documentos"]["pasos"]),
          "cada opción carga sus actividades")
    r = client.post("/procesos/plantillas", json={"nombre": "Masivos propio", "pasos": partir["masivos_sin_documentos"]["pasos"]})
    check(r.status_code == 201, f"un proceso creado a partir de una ruta base se guarda ({r.status_code})")

    ed = leer("components/dashboard/procesos/editor-proceso.tsx")
    pl = leer("components/dashboard/procesos/seccion-plantillas-proceso.tsx")
    check("Obligatoria" not in ed.split("*/", 1)[1] and "e.descripcion" in ed and "e.actividades" in ed,
          "el editor no tiene casilla «Obligatoria», muestra la descripción y usa el catálogo de la etapa")
    check("Partir de:" in pl and "opciones.partirDe" in pl, "el modal de nuevo proceso empieza con «Partir de:»")

    app.dependency_overrides.clear()
    db.close()

print(f"\n🎉 Cambios 28 y 29: {OK} comprobaciones OK")
