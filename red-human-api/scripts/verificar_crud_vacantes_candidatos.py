"""Verificación CRUD (2026-09-15): editar vacante (PATCH completo), eliminar vacante y eliminar candidato con
baja LÓGICA sin romper relaciones. Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_crud_vacantes_candidatos.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_crud_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "crud.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-crud"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Entrevista, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
import app.routers.webhooks as rw  # noqa: E402

OK = 0


import itertools as _it
_WAMID = _it.count(1)  # wamid único por mensaje simulado (el webhook deduplica reintentos)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _fake_wa(*a, **k):
    return {"enviado": True, "proveedor": "meta"}


rw.enviar_mensaje = _fake_wa
rw.enviar_lista_interactiva = _fake_wa


def webhook(client, tel, texto):
    return client.post("/webhooks/whatsapp", json={"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Nuevo"}, "wa_id": tel}],
        "messages": [{"from": tel, "id": f"wamid.{next(_WAMID)}.{texto[:6]}", "type": "text", "text": {"body": texto}}],
    }}]}]})


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta CRUD", nombre_comercial="CRUD", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ================= 1. Editar vacante =================
    print("\n--- 1. PATCH /vacantes/{codigo} (edición completa) ---")
    r = client.post("/vacantes", json={"titulo": "Cajero Original", "descripcion": "x", "generar_si_falta": False, "publicar": True,
                                       "ubicacion_estado": "Jalisco", "ubicacion_municipio": "Zapopan", "sueldo_desde": 9000, "sueldo_hasta": 11000, "sueldo_periodicidad": "mensual"})
    VAC = r.json()["id"]
    client.post(f"/vacantes/{VAC}/publicar", json={"plataformas": ["WhatsApp"]})
    cambios = {
        "titulo": "Cajero Editado", "area": "Operaciones", "seniority": "Junior", "modalidad": "Híbrido",
        "ubicacion_estado": "Nuevo León", "ubicacion_municipio": "Monterrey",
        "sueldo_desde": 12000, "sueldo_hasta": 14000, "sueldo_moneda": "MXN", "sueldo_periodicidad": "quincenal",
        "requisitos": "Secundaria · Manejo de efectivo", "descripcion": "Descripción editada",
        "responsabilidades": ["Cobrar", "Arqueo"], "requisitos_deseables": ["Inglés"], "beneficios": ["Vales"],
        "preguntas_filtro": [{"pregunta": "¿Tienes INE vigente?", "tipo": "si_no", "valida": "", "respuesta_esperada": "Sí", "descarta": True}],
        "preguntas_filtro_whatsapp": [{"pregunta": "¿Vives en Monterrey?", "tipo": "si_no", "valida": "", "respuesta_esperada": "Sí", "descarta": False}],
        "enfoque_entrevista": "profesional_personal", "texto_bolsa": "Texto bolsa editado", "avisos_cumplimiento": ["aviso 1"],
        "resumen": "Resumen editado", "perfil_ideal": "Perfil editado", "palabras_clave": ["caja"], "texto_whatsapp": "WA editado",
    }
    r = client.patch(f"/vacantes/{VAC}", json=cambios)
    check(r.status_code == 200, f"PATCH /vacantes → {r.status_code}")
    v = r.json()
    check(v["titulo"] == "Cajero Editado" and v["area"] == "Operaciones" and v["modalidad"] == "Híbrido" and v["seniority"] == "Junior", "título, área, modalidad y seniority editados")
    check(v["ubicacion"] == "Monterrey, Nuevo León" and v["ubicacionEstado"] == "Nuevo León", "ubicación estructurada editada → texto derivado")
    check("12,000" in v["sueldo"] and "quincenal" in v["sueldo"], f"sueldo estructurado editado → texto derivado «{v['sueldo']}»")
    check(v["criterios"][0]["pregunta"] == "¿Tienes INE vigente?" and v["criteriosWhatsapp"][0]["pregunta"] == "¿Vives en Monterrey?", "preguntas de prefiltro web y WhatsApp editadas")
    check(v["enfoqueEntrevista"] == "profesional_personal" and v["textoBolsa"] == "Texto bolsa editado" and v["avisosCumplimiento"] == ["aviso 1"], "enfoque, texto de bolsa y avisos editados")
    check(v["responsabilidades"] == ["Cobrar", "Arqueo"] and v["beneficios"] == ["Vales"] and v["requisitosDeseables"] == ["Inglés"], "listas editadas")
    check(v["estado"] == "Publicada", "editar no cambia el estado de publicación")
    r = client.patch(f"/vacantes/{VAC}", json={"estado": "Eliminada"})
    check(r.status_code == 400, "PATCH estado=Eliminada → 400 (solo vía DELETE)")

    # ================= 2. Eliminar vacante (soft) =================
    print("\n--- 2. DELETE /vacantes/{codigo} (baja lógica) ---")
    for i, nombre in enumerate(["Ana Uno", "Beto Dos"]):
        client.post("/candidatos", json={"nombre": nombre, "telefono": f"55000000{i:02d}", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    r = client.get(f"/vacantes/{VAC}")
    check(sum(r.json()["embudo"]["etapas"].values()) == 2, "la vacante tiene 2 postulaciones activas")
    r = client.get("/vacantes/publicas")
    check(any(x["id"] == VAC for x in r.json()), "antes: aparece en el portal público")
    r = client.delete(f"/vacantes/{VAC}")
    check(r.status_code == 200 and r.json()["vacante"]["estado"] == "Eliminada" and r.json()["postulacionesCerradas"] == 2, "DELETE → Eliminada, 2 postulaciones activas cerradas")
    db.expire_all()
    vac = db.query(Vacante).filter_by(codigo=VAC).one()
    check(vac.eliminada_en is not None and vac.eliminada_por == admin.nombre and len(vac.postulaciones) == 2, "fila conservada con fecha/quién; las postulaciones siguen ligadas (nada se borró)")
    check(all(not p.activa and p.motivo_cierre == "vacante_eliminada" for p in vac.postulaciones), "postulaciones cerradas con motivo vacante_eliminada")
    r = client.get("/vacantes")
    check(not any(x["id"] == VAC for x in r.json()), "GET /vacantes ya no la lista (tableros activos)")
    r = client.get("/vacantes?incluir_eliminadas=true")
    check(any(x["id"] == VAC and x["estado"] == "Eliminada" for x in r.json()), "…salvo con incluir_eliminadas=true")
    r = client.get("/vacantes/publicas")
    check(not any(x["id"] == VAC for x in r.json()), "portal público: ya no aparece")
    r = client.get(f"/vacantes/slug/{vac.slug}")
    check(r.status_code == 410, "liga pública /aplicar → 410")
    r = client.get("/candidatos")
    check(not any(p["vacanteId"] == VAC for p in r.json()), "Kanban: sus postulaciones ya no salen (cerradas)")
    r = client.get(f"/vacantes/{VAC}")
    check(r.status_code == 200 and r.json()["estado"] == "Eliminada" and r.json()["eliminadaPor"] == admin.nombre, "GET /{codigo} sigue respondiendo (ficha explica que fue eliminada)")
    r = client.patch(f"/vacantes/{VAC}", json={"titulo": "X"})
    check(r.status_code == 409, "editar una eliminada → 409")
    r = client.post(f"/vacantes/{VAC}/publicar", json={"plataformas": ["WhatsApp"]})
    check(r.status_code == 409, "publicar una eliminada → 409")
    r = client.delete(f"/vacantes/{VAC}")
    check(r.status_code == 409, "eliminar dos veces → 409")
    r = client.get("/metricas/pipeline")
    check(r.status_code == 200, "métricas siguen respondiendo (sin contar la eliminada)")
    r = client.post(f"/vacantes/{VAC}/restaurar")
    check(r.status_code == 200 and r.json()["estado"] == "Borrador" and r.json()["eliminadaEn"] is None, "restaurar → vuelve como Borrador")
    # el menú de WhatsApp no ofrece eliminadas (solo Publicadas)
    r = client.post("/vacantes", json={"titulo": "Para Borrar", "descripcion": "x", "generar_si_falta": False, "publicar": True})
    VAC2 = r.json()["id"]
    client.post(f"/vacantes/{VAC2}/publicar", json={"plataformas": ["WhatsApp"]})
    client.delete(f"/vacantes/{VAC2}")
    menu = rw._vacantes_publicadas(db, cuenta.id)
    check(all(x.codigo != VAC2 for x in menu), "el menú de vacantes de WhatsApp no la ofrece")

    # ================= 3. Eliminar candidato (soft) =================
    print("\n--- 3. DELETE /candidatos/{codigo} (baja lógica de la persona) ---")
    r = client.post("/vacantes", json={"titulo": "Vendedor", "descripcion": "x", "generar_si_falta": False, "publicar": True})
    VAC3 = r.json()["id"]
    client.post(f"/vacantes/{VAC3}/publicar", json={"plataformas": ["WhatsApp"]})
    r = client.post("/candidatos", json={"nombre": "Carla Tres", "telefono": "5533334444", "correo": "carla@x.mx", "vacante": VAC3, "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    C = r.json()["candidatoCodigo"]
    r = client.post("/entrevistas", json={"candidato": P, "avisar_whatsapp": False})
    ENT = r.json()["id"]
    # segunda postulación de la misma persona a otra vacante
    r = client.post("/candidatos", json={"nombre": "Carla Tres", "telefono": "5533334444", "correo": "carla@x.mx", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    check(r.json()["candidatoCodigo"] == C, "misma persona, dos postulaciones")

    r = client.delete(f"/candidatos/{P}")
    check(r.status_code == 200 and r.json()["candidato"] == C and sorted(r.json()["postulacionesCerradas"]) == sorted([P, P2]), "DELETE por P-#### elimina a la PERSONA y cierra sus 2 postulaciones")
    db.expire_all()
    c = db.query(Candidato).filter_by(codigo=C).one()
    check(c.eliminado_en is not None and c.eliminado_por == admin.nombre, "fila conservada con fecha/quién")
    check(all(not p.activa and p.motivo_cierre == "eliminado" for p in c.postulaciones), "postulaciones cerradas con motivo eliminado")
    check(db.query(Entrevista).filter_by(codigo=ENT).one().postulacion.codigo == P, "la entrevista sigue ligada a su postulación (historial intacto)")
    r = client.get("/candidatos?mostrar_cerradas=true")
    check(not any(p["candidatoCodigo"] == C for p in r.json()), "Kanban: ni con «Mostrar cerradas» aparece la persona eliminada")
    r = client.get(f"/candidatos/{P}")
    check(r.status_code == 404, "GET /candidatos/{P} → 404")
    r = client.get(f"/candidatos/{C}")
    check(r.status_code == 404, "GET /candidatos/{C} → 404")
    r = client.delete(f"/candidatos/{P}")
    check(r.status_code == 404, "eliminar dos veces → 404")
    # dedupe: al ingresar de nuevo el mismo teléfono nace una persona nueva (no se resucita la eliminada)
    r = client.post("/candidatos", json={"nombre": "Carla Nueva", "telefono": "5533334444", "vacante": VAC3, "consentimiento": True, "fuente": "RH"})
    check(r.status_code == 201 and r.json()["candidatoCodigo"] != C, "reingreso con el mismo teléfono → persona NUEVA (la eliminada no se reutiliza)")
    # webhook: mismo criterio
    r = webhook(client, "5215533334444", "hola")
    db.expire_all()
    check(r.status_code == 200 and r.json().get("candidato") != C, "WhatsApp con el mismo número → no cae en la persona eliminada")
    # persona ya colaboradora activa → no se puede eliminar
    r = client.post("/candidatos", json={"nombre": "Dora Alta", "telefono": "5599990000", "vacante": VAC3, "consentimiento": True, "fuente": "RH"})
    P4 = r.json()["id"]
    from app.services.configuracion import obtener
    cfg = obtener(db); cfg.modo_prueba = True; db.commit()
    r = client.patch(f"/candidatos/{P4}/etapa?forzar_prueba=true", json={"etapa": "Contratación"})
    EXP = r.json()["expedienteId"]
    pdf = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"
    client.post(f"/contratacion/expedientes/{EXP}/documentos", data={"tipo": "CURP"}, files={"archivo": ("curp.pdf", pdf, "application/pdf")})
    r = client.post(f"/contratacion/expedientes/{EXP}/alta?forzar_prueba=true", json={})
    check(r.status_code == 200, "persona dada de alta como colaborador")
    r = client.delete(f"/candidatos/{P4}")
    check(r.status_code == 404 or r.status_code == 409, f"eliminar a un colaborador activo → bloqueado ({r.status_code})")

print(f"\n🎉 CRUD verificado: {OK} comprobaciones OK.")
