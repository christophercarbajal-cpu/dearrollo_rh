"""Verificación de los ajustes tras pruebas en producción (2026-09-18):
1. Resend: remitente estrictamente @redhuman.mx (aunque RESEND_FROM traiga el sandbox).
3. Entrevista Humana: `advertencias` en el JSON cuando el correo no salió (texto del toast).
4. Modo Prueba TOTAL: alta sin exigir expediente; subida de documentos sin IA → válido; prompt de comprobante
   de domicilio a nombre de terceros.
5. Eliminar colaborador → desaparecen sus asignaciones de capacitación y bajan los contadores.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_ajustes_prod_2026_09_18.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_aj_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "aj.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-aj"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AsignacionCurso, Colaborador, Cuenta, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import correo as scorreo  # noqa: E402
from app.services import ia  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
PDF_MIN = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"


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
    cuenta = Cuenta(nombre="Cuenta AJ", nombre_comercial="AJ", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    cfg = obtener(db)
    db.commit()  # obtener() puede crear la fila: liberar el lock de SQLite antes de usar el cliente

    # ================= 1. Remitente =================
    print("\n--- 1. Remitente Resend ---")
    settings.resend_from = "Red Human AI <onboarding@resend.dev>"
    check(scorreo.remitente() == "Red Human AI <notificaciones@redhuman.mx>", "RESEND_FROM sandbox → se ignora y se usa notificaciones@redhuman.mx")
    settings.resend_from = "Entrevistas Red Human <entrevistas@redhuman.mx>"
    check(scorreo.remitente() == "Entrevistas Red Human <entrevistas@redhuman.mx>", "un remitente @redhuman.mx configurado se respeta")
    settings.resend_from = ""
    check(scorreo.remitente().endswith("@redhuman.mx>"), "sin RESEND_FROM → default @redhuman.mx")

    # ================= 3. Advertencias en el response =================
    print("\n--- 3. Advertencia de correo fallido en el JSON ---")
    vac = client.get("/vacantes").json()[0]
    r = client.post("/candidatos", json={"nombre": "Aviso Persona", "telefono": "5512121212", "correo": "aviso@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Evaluación", "manual": True})
    # Evaluaciones unificadas (2026-09-29): la entrevista humana se asigna con la pantalla única «Agregar evaluación»
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "cita": {"fecha": "2026-10-01", "hora": "10:00", "modalidad": "Teléfono"},
        "notificar": {"cliente_correo": False, "cliente_whatsapp": False},
    })
    check(r.status_code == 201 and "advertencias" in r.json(), "el response trae `advertencias`")
    adv = r.json()["advertencias"]
    check(any(a.startswith("Evaluación asignada, pero el correo falló. Verifica la API Key o el Dominio") for a in adv), f"texto del toast presente ({len(adv)} avisos)")

    # ================= 4. Modo Prueba TOTAL =================
    print("\n--- 4. Modo Prueba total ---")
    cfg.modo_prueba = True
    db.commit()
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Contratación", "manual": True})
    EXP = r.json()["expedienteId"]
    r = client.post(f"/contratacion/expedientes/{EXP}/documentos", data={"tipo": "Identificación oficial"}, files={"archivo": ("cualquier.pdf", PDF_MIN, "application/pdf")})
    check(r.status_code == 200 and r.json()["documento"]["estado"] == "recibido" and "Modo Prueba" in r.json()["documento"]["notas"], "Modo Prueba: subir un PDF lo marca válido sin OCR/IA")
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Onboarding", "manual": True})
    r = client.post(f"/contratacion/expedientes/{EXP}/alta", json={"puesto": "Cajero"})
    check(r.status_code == 200, f"Modo Prueba: alta directa con expediente incompleto y sin confirmación de RH ({r.status_code})")
    cfg.modo_prueba = False
    db.commit()
    r = client.post("/candidatos", json={"nombre": "Estricto Persona", "telefono": "5513131313", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    # Onboarding v2 (2026-09-28): sin Modo Prueba a Onboarding solo se entra con «Iniciar Onboarding»; el
    # expediente nace en Contratación y ahí se prueba que el alta lo sigue exigiendo.
    r = client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Contratación", "manual": True})
    EXP2 = r.json()["expedienteId"]
    r = client.post(f"/contratacion/expedientes/{EXP2}/alta", json={"puesto": "Cajero"})
    check(r.status_code in (400, 409), "sin Modo Prueba: el alta sigue exigiendo el expediente")
    r = client.post(f"/contratacion/expedientes/{EXP2}/documentos", data={"tipo": "Identificación oficial"}, files={"archivo": ("cualquier.pdf", PDF_MIN, "application/pdf")})
    check(r.status_code == 200 and r.json()["documento"]["estado"] == "revision", "sin Modo Prueba (y sin IA): el documento queda en revisión, no válido")
    import inspect as _insp  # noqa: E402
    fuente = _insp.getsource(ia.validar_documento)
    check("COMPROBANTES DE DOMICILIO" in fuente and "tercero" in fuente and "coincide_titular=null" in fuente, "prompt: comprobante de domicilio válido a nombre de terceros (solo legitimidad)")

    # ================= 5. Colaborador eliminado → capacitación =================
    print("\n--- 5. Eliminar colaborador limpia Capacitación ---")
    col = db.query(Colaborador).filter(Colaborador.cuenta_id == cuenta.id, Colaborador.eliminado_en.is_(None)).order_by(Colaborador.id.desc()).first()
    check(col is not None, f"colaborador dado de alta ({col.codigo if col else '-'})")
    r = client.post("/capacitacion/generar", data={"tema": "Inducción", "duracion_horas": "1", "contexto": ""})
    CUR = r.json()["id"]
    client.patch(f"/capacitacion/{CUR}/publicar")
    r = client.post(f"/capacitacion/{CUR}/asignar", json={"colaborador_ids": [col.codigo], "externos": [{"nombre": "Ext", "correo": "e@x.mx"}], "notificar": False})
    check(r.status_code == 201 and len(r.json()["asignaciones"]) == 2, "curso asignado al colaborador y a un externo")
    check(client.get(f"/capacitacion/{CUR}").json()["asignados"] == 2, "contador «asignados» = 2")
    r = client.delete(f"/colaboradores/{col.codigo}")
    check(r.status_code == 200 and r.json()["asignacionesCursoEliminadas"] == 1, "DELETE colaborador elimina su asignación de curso")
    check(client.get(f"/capacitacion/{CUR}").json()["asignados"] == 1, "contador «asignados» baja a 1 al instante")
    tablero = client.get("/capacitacion/asignaciones").json()
    check(all(a["colaboradorId"] != col.codigo for a in tablero) and len([a for a in tablero if a["cursoId"] == CUR]) == 1, "Seguimiento ya no muestra al colaborador eliminado")
    check(db.query(AsignacionCurso).filter(AsignacionCurso.colaborador_id == col.id).count() == 0, "sin filas huérfanas en la base")
    # cascada física del modelo
    db.expire_all()
    col2 = Colaborador(cuenta_id=cuenta.id, codigo="COL-TEST-CASCADA", nombre="Cascada", puesto="x", activo=True)
    db.add(col2); db.flush()
    from app.models import Curso  # noqa: E402
    curso = db.query(Curso).filter(Curso.codigo == CUR).one()
    db.add(AsignacionCurso(codigo="ASG-TEST-1", curso_id=curso.id, tipo="colaborador", colaborador_id=col2.id, token="tok-test-1"))
    db.commit()
    db.delete(col2)
    db.commit()
    check(db.query(AsignacionCurso).filter(AsignacionCurso.codigo == "ASG-TEST-1").count() == 0, "cascade='all, delete-orphan': borrar físicamente al colaborador borra sus asignaciones")

    db.close()

print(f"\n🎉 Ajustes de producción verificados: {OK} comprobaciones OK.")
