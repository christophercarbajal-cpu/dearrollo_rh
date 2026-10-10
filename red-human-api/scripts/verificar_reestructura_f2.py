"""Verificación REESTRUCTURA · FASE 2 (2026-09-16): prefiltro dual (IA genera web + puntos críticos WhatsApp;
respuestas web guardadas; comparación Web vs WhatsApp → la IA pregunta, no descarta) y control manual de RH
(«Mover a otra etapa» sin bloqueos, «Omitida manualmente» con usuario/fecha/motivo, relación Candidato-Vacante
firme en WhatsApp). Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_reestructura_f2.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_rf2_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "rf2.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["SEMBRAR_DEMO"] = "false"
os.environ["ADMIN_PASSWORD"] = "prueba-rf2"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Postulacion, Usuario, UsuarioCuenta  # noqa: E402
from app.routers.candidatos import comparar_web_vs_whatsapp  # noqa: E402
from app.services import ia  # noqa: E402
import app.routers.candidatos as rc  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


ENVIOS = []


async def _fake_wa(telefono, texto):
    ENVIOS.append({"telefono": telefono, "texto": texto})
    return {"enviado": True, "proveedor": "meta", "detalle": 200}


rc.enviar_mensaje = _fake_wa

# --- prefiltro_turno simulado: devuelve respuestas_extraidas controladas por el test ---
TURNO = {"respuestas": [], "cerrar": False, "estado": "cumple", "nota": ""}
_orig_turno = ia.prefiltro_turno


def _turno_simulado(titulo, requisitos, preguntas, historial, **kw):
    TURNO["nota"] = kw.get("nota", "")
    return ia.TurnoPrefiltro(
        respuesta="Gracias, sigo.", clasificacion_lista=TURNO["cerrar"], estado=TURNO["estado"] if TURNO["cerrar"] else None,
        evidencia="simulado" if TURNO["cerrar"] else None,
        respuestas_extraidas=[ia.RespuestaCriterio(**r) for r in TURNO["respuestas"]],
    ), True


rc.ia.prefiltro_turno = _turno_simulado

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta RF2", nombre_comercial="RF2", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- Prefiltro dual: la IA genera web + puntos críticos WhatsApp ---")
    r = client.post("/vacantes/generar", json={"titulo": "Cajero(a) de sucursal", "area": "Operaciones", "seniority": "Junior", "ubicacion": "Zapopan, Jalisco",
                                                "requisitos_indispensables": ["Secundaria terminada", "Manejo de efectivo", "Disponibilidad de fin de semana"],
                                                # 2026-10-09: solo se genera lo que está en la ruta (aquí web + WhatsApp)
                                                "proceso": {"pasos": [{"id": "pw", "tipo": "prefiltro_web", "etapa": "Prefiltro"}, {"id": "pwa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"}]}})
    check(r.status_code == 200 and len(r.json()["preguntas_filtro"]) >= 3, f"generar: prefiltro web con {len(r.json()['preguntas_filtro'])} preguntas")
    wa = r.json()["preguntas_filtro_whatsapp"]
    # 2026-10-09: con web + WhatsApp en la ruta, WhatsApp RECONFIRMA cada indispensable con un dato concreto (cerrado)
    check(len(wa) == 3 and all(q["tipo"] in ("numero", "opcion") and q.get("reconfirma") for q in wa),
          f"generar: prefiltro WhatsApp = {len(wa)} reconfirmaciones cerradas de los indispensables")
    check(not {q["pregunta"] for q in r.json()["preguntas_filtro"]} & {w["pregunta"] for w in wa} and wa[0]["pregunta"].startswith("Para confirmar"),
          "las de WhatsApp piden el dato concreto, no copian la lista web")
    r = client.post("/vacantes", json={"titulo": "Cajero RF2", "descripcion": "x", "requisitos_indispensables": ["Manejo de efectivo", "Disponibilidad de fin de semana"], "publicar": True, "generar_si_falta": True,
                                       "proceso": {"pasos": [{"id": "pw", "tipo": "prefiltro_web", "etapa": "Prefiltro"}, {"id": "pwa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"}]}})
    VAC = r.json()["id"]
    client.post(f"/vacantes/{VAC}/publicar", json={"plataformas": ["WhatsApp", "Portal"]})
    v = client.get(f"/vacantes/{VAC}").json()
    check(len(v["criteriosWhatsapp"]) >= 1 and len(v["criterios"]) >= 2, "al crear con generar_si_falta la vacante guarda ambas listas")
    slug = v["slug"]

    print("\n--- Respuestas web guardadas y comparación Web vs WhatsApp ---")
    preg_web = v["criterios"][0]["pregunta"]
    r = client.post("/candidatos/postular", data={"vacante": slug, "nombre": "Ana Dual", "telefono": "5511110001", "consentimiento": "true",
                                                  "respuestas": json.dumps([{"pregunta": preg_web, "respuesta": "Sí"}, {"pregunta": "¿Cuántos años de experiencia tienes?", "respuesta": "2-3 años"}])})
    check(r.status_code in (200, 201), "postulación web con respuestas")
    P = r.json()["postulacion"]
    p = db.query(Postulacion).filter_by(codigo=P).one()
    check(p.analisis.get("respuestas_web") and p.analisis["respuestas_web"][0]["respuesta"] == "Sí", "las respuestas del formulario web quedan en analisis.respuestas_web (antes se tiraban)")
    # unidad: comparación
    inc = comparar_web_vs_whatsapp({"respuestas_web": [{"pregunta": preg_web, "respuesta": "Sí"}], "respuestas_prefiltro": [{"criterio": v["criterios"][0]["valida"], "pregunta": preg_web, "respuesta": "No", "cumple": False}]}, v["criterios"])
    check(len(inc) == 1 and inc[0]["web"] == "Sí" and inc[0]["whatsapp"] == "No" and inc[0]["aclarada"] is False, "comparar_web_vs_whatsapp detecta «Sí» (web) vs «No» (WhatsApp) sobre el mismo criterio")
    inc2 = comparar_web_vs_whatsapp({"respuestas_web": [{"pregunta": preg_web, "respuesta": "Sí"}], "respuestas_prefiltro": [{"criterio": v["criterios"][0]["valida"], "pregunta": preg_web, "respuesta": "Sí, claro", "cumple": True}]}, v["criterios"])
    check(inc2 == [], "respuestas consistentes → sin inconsistencias")

    # retro 2026-10-09: prefiltro conversacional en todas las Cuentas — el «Sí» del formulario se reconfirma con un DATO
    # concreto; si el dato lo contradice es «Inconsistencia» y RH revisa (nunca descarta)
    ENVIOS.clear()
    r = client.post(f"/candidatos/{P}/prefiltro", json={"texto": "Hola"})
    check(r.status_code == 200 and v["criterios"][1]["pregunta"] in r.json()["respuesta"],
          "el chat pregunta primero el indispensable que el formulario no respondió")
    r = client.post(f"/candidatos/{P}/prefiltro", json={"texto": "Sí"})
    check("Para confirmar" in r.json()["respuesta"], "el «Sí» del formulario se RECONFIRMA en el chat pidiendo un dato concreto")
    r = client.post(f"/candidatos/{P}/prefiltro", json={"texto": "No"})
    db.expire_all()
    p = db.query(Postulacion).filter_by(codigo=P).one()
    check(p.activa and p.estado == "cumple" and p.prefiltro_completo,
          "el dato contradice el formulario → NO descarta ni va a revisión: el agente decide y la ruta sigue (especificación 2026-10-10)")
    check(len(p.analisis["inconsistencias"]) == 1 and p.analisis["inconsistencias"][0]["web"] == "Sí", "la inconsistencia quedó registrada (formulario vs chat)")
    r2 = client.get(f"/candidatos/{P}")
    check(len(r2.json()["inconsistencias"]) == 1, "la ficha expone la inconsistencia")
    check("necesitamos" not in r.json()["respuesta"], "al candidato no se le comunica ningún rechazo")

    print("\n--- Control manual de RH: mover libre + «Omitida manualmente» ---")
    r = client.post("/candidatos", json={"nombre": "Beto Manual", "telefono": "5522220002", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    r = client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Contratación"})
    check(r.status_code == 200 or r.status_code == 409, f"flujo normal desde Prefiltro a Contratación (sin manual) → {r.status_code}")
    r = client.post("/candidatos", json={"nombre": "Caro Manual", "telefono": "5522220003", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P3 = r.json()["id"]
    r = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Entrevista Humana"})
    check(r.status_code == 409, "sin manual: Entrevista Humana sigue exigiendo el flujo de agenda (409)")
    r = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Entrevista Humana", "manual": True, "omitir_obligatorios": True, "comentario": "el cliente ya la entrevistó"})
    check(r.status_code == 200 and r.json()["etapa"] == "Entrevista Humana", "manual=true: RH mueve a Entrevista Humana sin agendar")
    om = r.json()["actividadesOmitidas"]
    check([o["actividad"] for o in om] == ["Prefiltro", "Entrevista IA"] and all(o["usuario"] == admin.nombre and o["fecha"] and o["motivo"] == "el cliente ya la entrevistó" for o in om),
          f"lo saltado quedó como «Omitida manualmente» con usuario, fecha y motivo: {[o['actividad'] for o in om]}")
    # Onboarding v2 (2026-09-28): a Onboarding solo se entra con «Iniciar Onboarding»; el salto manual directo
    # queda bloqueado salvo con Modo Prueba activo (ahí sigue abriendo el expediente sin bloquear).
    r = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Onboarding", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 409 and "Iniciar Onboarding" in r.json()["detail"], "manual sin Modo Prueba: el salto directo a Onboarding pide «Iniciar Onboarding» (Onboarding v2)")
    from app.services.configuracion import obtener as _obtener_cfg  # noqa: E402

    _cfg = _obtener_cfg(db)
    _antes = _cfg.modo_prueba
    _cfg.modo_prueba = True
    db.commit()
    r = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Onboarding", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    _cfg.modo_prueba = _antes
    db.commit()
    check(r.status_code == 200 and r.json()["etapa"] == "Onboarding" and r.json()["expedienteId"], "manual con Modo Prueba: salto directo a Onboarding abre el expediente y no bloquea")
    check([o["actividad"] for o in r.json()["actividadesOmitidas"]][-2:] == ["Entrevista Humana", "Contratación"], "segundo salto agrega sus propias omisiones")
    r = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Prefiltro", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["etapa"] == "Prefiltro", "manual: también permite regresar de etapa")
    db.expire_all()
    p3 = db.query(Postulacion).filter_by(codigo=P3).one()
    check(p3.candidato.postulacion_conversacion_id == p3.id and p3.vacante_id is not None, "la relación Candidato-Vacante y la conversación de WhatsApp quedan firmes tras mover")

    # fallo de WhatsApp al forzar Entrevista IA → no bloquea
    async def _explota(*a, **k):
        raise RuntimeError("Meta caída")
    rc.enviar_mensaje = _explota
    r = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Entrevista IA", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["etapa"] == "Entrevista IA", "con WhatsApp caído, mover a Entrevista IA sigue funcionando (200)")
    rc.enviar_mensaje = _fake_wa

print(f"\n🎉 Reestructura F2 verificada: {OK} comprobaciones OK.")
