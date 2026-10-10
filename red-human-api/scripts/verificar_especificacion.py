"""Regresión de la «Red Human — Especificación para desarrollo» (2026-10-10). Base desechable, SIN red ni OpenAI.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_especificacion.py

1. Catálogo: EXACTAMENTE 22 actividades con los nombres y el orden de la sección 4; «Solicitud y revisión de documentos»
   unifica las de documentos; «Psicometría física» se guarda como psicometría física (nunca va al proveedor); «Carta de
   intención (opcional)» nace opcional.
2. Decisión del prefiltro: no existe «Revisar prefiltro» ni «Aprobar prefiltro» (410); los que estaban en revisión
   siguen ACTIVOS y el agente decide con su siguiente mensaje (migración una sola vez); «Reactivar» solo para descartados
   o sin respuesta.
"""

import asyncio
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

_dir = tempfile.mkdtemp(prefix="rh_especificacion_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "especificacion.db").replace("\\", "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET",
          "DROPBOX_SIGN_API_KEY", "DROPBOX_SIGN_CLIENT_ID"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-especificacion"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bitacora, Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
CATALOGO = ["Solicitud web", "Análisis de CV", "Prefiltro web", "Prefiltro WhatsApp", "Llamada Red Human",
            "Entrevista Red Human por WhatsApp", "Entrevista Red Human con avatar", "Psicometría digital", "Psicometría física",
            "Entrevista humana", "Evaluación médica", "Evaluación técnica o práctica", "Estudio socioeconómico",
            "Referencias laborales", "Solicitud y revisión de documentos", "Propuesta y aceptación", "Carta de intención (opcional)",
            "Contrato y firma", "Capacitación / Inducción", "Tareas de onboarding", "Confirmación de ingreso", "Otra actividad"]


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
    obtener(db).modo_prueba = False
    cuenta = Cuenta(nombre="Especificación SA", nombre_comercial="Especificación", razon_social="Especificación SA de CV",
                    estado="Activa", slug="especificacion-sa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    H = {"X-Cuenta-Id": str(cuenta.id)}

    # ================= 1. Catálogo definitivo =================
    print("\n--- 1. Catálogo de 22 actividades ---")
    tipos = client.get("/procesos/opciones", headers=H).json()["tiposPaso"]
    check([t["texto"] for t in tipos] == CATALOGO, "el catálogo tiene EXACTAMENTE las 22 actividades, con sus nombres y en orden")
    check(not any(t["valor"] == "solicitud_documentos" for t in tipos),
          "«Solicitud y revisión de documentos» unifica las de documentos (la vieja ya no se ofrece)")
    check(next(t for t in tipos if t["valor"] == "carta_intencion")["obligatorio"] is False, "«Carta de intención (opcional)» nace opcional")
    pasos = sproc.normalizar_pasos([{"tipo": "psicometria_fisica", "etapa": "Entrevista Humana"},
                                    {"tipo": "carta_intencion", "etapa": "Contratación"},
                                    {"tipo": "solicitud_documentos", "etapa": "Prefiltro"}])
    fis = next(x for x in pasos if x["tipo"] == "psicometrica")
    check(fis["tipo"] == "psicometrica" and fis.get("modalidad") == "fisica" and fis["nombre"] == "Psicometría física" and not fis["pruebas"],
          "«Psicometría física» se guarda como psicometría con modalidad física (sin batería del proveedor)")
    check(next(x for x in pasos if x["tipo"] == "carta_intencion")["obligatorio"] is False, "la carta de intención es opcional en la ruta")
    check(any(x["tipo"] == "solicitud_documentos" for x in pasos), "las rutas ya guardadas con la actividad vieja siguen siendo válidas")

    # ================= 2. Decisión del prefiltro =================
    print("\n--- 2. El agente decide el prefiltro ---")
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").first()
    v.cuenta_id = cuenta.id
    c = _crear_candidato(db, cuenta.id, "Rita Revisión", "WhatsApp", False, correo="rita@correo.mx", telefono="5519990001")
    p = crear_postulacion(db, c, v, cuenta.id, "whatsapp", consentimiento=True)
    p.prefiltro_completo, p.estado = True, "revision"
    p.analisis = {"prefiltro_web": {"resultado": "revision", "motivo": "Respuesta no concluyente"},
                  pconv.ENTIDAD: {"resultado": "revision", "respuestas": {"c1": {"respuesta": "depende", "valor": None},
                                                                          "c2": {"respuesta": "Sí", "valor": "si", "fuente": "web"}}}}
    db.query(Bitacora).filter(Bitacora.accion == pconv.MARCA_LIBERACION).delete()
    db.commit()
    r = pconv.liberar_revisiones(db)
    db.commit()
    db.expire_all()
    p = db.query(Postulacion).filter_by(codigo=p.codigo).one()
    e = p.analisis[pconv.ENTIDAD]
    check(r["liberadas"] >= 1 and p.activa and not p.prefiltro_completo and p.estado == "pendiente" and "prefiltro_web" not in p.analisis,
          "migración: los que estaban en «Revisar prefiltro» siguen ACTIVOS, sin revisión pendiente")
    check("resultado" not in e and "c1" not in e["respuestas"] and e["respuestas"]["c2"]["valor"] == "si",
          "se conservan las respuestas válidas; la no concluyente se vuelve a preguntar")
    check(any(h.get("evento") == "prefiltro_revision_liberada" for h in p.historial), "queda en el historial")
    check(pconv.liberar_revisiones(db).get("yaAplicada"), "la migración corre una sola vez")
    check(client.post(f"/procesos/postulaciones/{p.codigo}/prefiltro/aprobar", headers=H, json={}).status_code == 410,
          "«Aprobar prefiltro» ya no existe (410)")

    print("\n--- 3. «Reactivar» ---")
    check(client.post(f"/candidatos/{p.codigo}/reactivar", headers=H, json={"motivo": "Motivo suficientemente largo"}).status_code == 409,
          "una postulación activa no se reactiva")
    p.cerrar("contratado")
    db.commit()
    check(client.post(f"/candidatos/{p.codigo}/reactivar", headers=H, json={"motivo": "Motivo suficientemente largo"}).status_code == 409,
          "solo descartados o sin respuesta (un contratado no)")
    p = db.query(Postulacion).filter_by(codigo=p.codigo).one()
    p.motivo_cierre = "sin_interes"
    db.commit()
    r = client.post(f"/candidatos/{p.codigo}/reactivar", headers=H, json={"motivo": "Volvió a escribir interesado"})
    db.expire_all()
    p = db.query(Postulacion).filter_by(codigo=p.codigo).one()
    check(r.status_code == 200 and p.activa and not p.motivo_cierre and any(h.get("evento") == "reactivada" for h in p.historial),
          "«Reactivar» a un candidato sin respuesta: vuelve activo y queda en el historial")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — especificación")
