"""Regresión del motor de ruta automatizado (Bloque 2, 2026-10-08) — SOLO demo-grupak.

    .venv/Scripts/python.exe scripts/verificar_motor_ruta.py

Base desechable, sin red: Psicométricas.mx sin llaves (modo simulado), WhatsApp/correo sin configurar.
Cubre: prefiltro web resuelto contra la vacante (Cumple → avanza; 2026-10-08 prefiltro conversacional: excluyente →
postulación CERRADA con su motivo y «Continuar por decisión de RH» disponible; Parcial → el bot lo pregunta (nunca se
aprueba solo); Parcial sin teléfono para preguntar → «Revisar prefiltro» y RH aprueba), disparo automático de la psicometría y la Entrevista Red
Human al habilitarse (una sola vez aunque el motor corra de nuevo), actividades habilitadas por completado (no por
columna), score propio de la entrevista (no el del CV), «No aprobada» bajo el mínimo → descarte sugerido + evaluación
integral No apto, vocabulario único de estados y aislamiento: otra Cuenta con la MISMA ruta no se mueve sola.
"""

import asyncio
import json
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

_dir = tempfile.mkdtemp(prefix="rh_motor_ruta_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "motor.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "PSICOMETRICAS_TOKEN",
          "PSICOMETRICAS_PASSWORD", "PSICOMETRICAS_USUARIO", "TELEGRAM_BOT_TOKEN"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-motor-ruta"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Entrevista, Evaluacion, Postulacion, PruebaPsicometrica, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import evaluacion_integral, motor_ruta  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


PREGUNTAS = [
    {"pregunta": "¿Tienes secundaria terminada?", "tipo": "si_no", "valida": "Secundaria", "respuesta_esperada": "Sí", "descarta": True,
     "opciones": ["Sí", "No", "Parcial"]},
    {"pregunta": "¿Puedes rolar turnos?", "tipo": "si_no", "valida": "Rolar turnos", "respuesta_esperada": "Sí", "descarta": True,
     "opciones": ["Sí", "No", "Parcial"]},
    {"pregunta": "¿Tienes experiencia en almacén?", "tipo": "si_no", "valida": "Almacén", "respuesta_esperada": "Sí", "descarta": False,
     "opciones": ["Sí", "No", "Parcial"]},
]


def ruta(bateria_id):
    return [
        {"id": "prefiltro", "tipo": "prefiltro_web", "nombre": "Prefiltro", "etapa": "Prefiltro"},
        {"id": "bateria", "tipo": "psicometrica", "nombre": "Batería psicométrica", "etapa": "Entrevista IA", "pruebas": [bateria_id]},
        {"id": "entrevista_red_human", "tipo": "entrevista_agente", "nombre": "Entrevista Red Human", "etapa": "Entrevista IA"},
        {"id": "entrevista-lider", "tipo": "entrevista_humana", "nombre": "Entrevista con líder", "etapa": "Entrevista Humana"},
        {"id": "propuesta", "tipo": "condiciones", "nombre": "Propuesta", "etapa": "Contratación"},
    ]


def respuestas(*valores):
    return json.dumps([{"pregunta": q["pregunta"], "respuesta": v} for q, v in zip(PREGUNTAS, valores)])


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    obtener(db).modo_prueba = False
    cuentas, vacantes = {}, {}
    base = db.query(Vacante).filter(Vacante.estado == "Publicada").limit(2).all()
    for slug, v in zip(("demo-grupak", "manual-sa"), base):
        cu = Cuenta(nombre=slug, nombre_comercial=slug, razon_social=slug, estado="Activa", slug=slug)
        db.add(cu)
        db.flush()
        db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cu.id))
        bat = PruebaPsicometrica(cuenta_id=cu.id, clave=f"BAT-{cu.id}", nombre="Batería Ayudante", modo="integrada",
                                 proveedor="Psicométricas.mx", id_proveedor="1,5", tipo="bateria", activa=True)
        db.add(bat)
        db.flush()
        v.cuenta_id = cu.id
        v.preguntas_filtro = PREGUNTAS
        v.proceso = sproc.proceso_para_vacante(db, cu.id, {}, {"pasos": ruta(bat.id), "etapas": {
            e: {"avance_automatico": True} for e in ("Prefiltro", "Entrevista IA", "Entrevista Humana")}})
        cuentas[slug], vacantes[slug] = cu, v
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    HG = {"X-Cuenta-Id": str(cuentas["demo-grupak"].id)}
    HM = {"X-Cuenta-Id": str(cuentas["manual-sa"].id)}

    def postular(slug, nombre, tel, correo, resp):
        r = client.post("/candidatos/postular", data={"vacante": vacantes[slug].codigo, "nombre": nombre, "telefono": tel,
                                                      "correo": correo, "consentimiento": "true", "respuestas": resp})
        assert r.status_code == 201, r.text
        return r.json()["postulacion"]

    def seg(codigo, h=HG):
        return client.get(f"/procesos/postulaciones/{codigo}", headers=h).json()

    def paso(s, pid):
        return next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == pid)

    print("\n--- 1. Cumple → aprueba, avanza y dispara solo ---")
    PA = postular("demo-grupak", "Ana Cumple", "5512347001", "ana@correo.mx", respuestas("Sí", "Sí", "No"))
    db.expire_all()
    pa = db.query(Postulacion).filter(Postulacion.codigo == PA).first()
    check(pa.analisis["prefiltro_web"]["resultado"] == "cumple" and pa.estado == "cumple",
          "prefiltro web resuelto contra la vacante: Cumple (un «No» en un criterio NO excluyente no descarta)")
    check(pa.etapa == "Entrevista IA", "la tarjeta avanzó sola a Filtro Red Human")
    s = seg(PA)
    check(paso(s, "prefiltro")["estadoUnificado"] == "aprobada", "Prefiltro → «Aprobada»")
    evs = db.query(Evaluacion).filter(Evaluacion.postulacion_id == pa.id, Evaluacion.tipo == "psicometrica").all()
    check(len(evs) == 1 and evs[0].paso_id == "bateria", "la psicometría se disparó sola, ligada a su actividad")
    check(len(pa.entrevistas) == 1, "la Entrevista Red Human se creó sola (liga para el candidato)")
    check(paso(s, "entrevista_red_human")["estadoUnificado"] == "error",
          "sin WhatsApp ni correo configurados la liga no llegó → la actividad queda en «Error» (nunca silencioso)")
    envios = pa.analisis["motor_ruta"]["envios"]
    check(set(envios) == {"bateria", "entrevista_red_human"} and all(x["ok"] for x in envios.values()),
          "cada disparo queda reclamado en `analisis.motor_ruta`")
    asyncio.run(motor_ruta.barrido())
    db.expire_all()
    pa = db.query(Postulacion).filter(Postulacion.codigo == PA).first()
    check(db.query(Evaluacion).filter(Evaluacion.postulacion_id == pa.id).count() == 1 and len(pa.entrevistas) == 1,
          "el job vuelve a correr: no duplica la psicometría ni la entrevista")
    lider = paso(s, "entrevista-lider")
    check(not lider["disponible"] and lider["espera"].startswith("Falta completar:") and "Se habilita" not in lider["espera"],
          "la siguiente etapa dice qué actividad falta (no «Se habilita en …»)")
    check(lider["automatica"] is False and paso(s, "bateria")["automatica"] is True, "automáticas vs. manuales marcadas")

    print("\n--- 2. Entrevista Red Human: score PROPIO y mínimo excluyente ---")
    e = pa.entrevistas[0]
    e.estado = "evaluada"
    e.evaluacion = {"score_entrevista": 68, "match_perfil": 95, "calif_experiencia": 9, "calif_comunicacion": 9,
                    "fortalezas": ["Comunicación clara"], "riesgos": [], "recomendacion": "revision"}
    db.commit()
    asyncio.run(sproc.avanzar_seguro(db, pa))
    s = seg(PA)
    x = paso(s, "entrevista_red_human")
    check(x["score"] == 68 and "68/100" in x["detalle"], "el score de la entrevista sale de la entrevista (68), no de la afinidad (95)")
    check(x["estadoUnificado"] == "no_aprobada" and x["estadoUnificadoTexto"] == "No aprobada", "68 con mínimo 70 → «No aprobada»")
    check(s["descarteSugerido"] and s["descarteSugerido"]["paso"] == "entrevista_red_human", "→ «Descarte sugerido» con el motivo")
    db.expire_all()
    pa = db.query(Postulacion).filter(Postulacion.codigo == PA).first()
    check(pa.activa and pa.etapa == "Entrevista IA", "…pero la postulación sigue ACTIVA y no avanza (RH confirma el descarte)")
    integral = evaluacion_integral.calcular(pa)
    vr = next(v for v in integral["validaciones"] if v["clave"] == "entrevista_red_human")
    check(vr["estado"] == "no_apto" and integral["estado"] == "no_apto", "evaluación integral: entrevista No aprobada → No apto")
    tarjeta = client.get(f"/candidatos/{PA}", headers=HG).json()
    check(tarjeta["suggested_discard"]["paso"] == "entrevista_red_human", "la tarjeta del tablero trae el descarte sugerido")

    print("\n--- 3. Criterio indispensable incumplido → «Descartado» con su motivo; RH lo revierte con «Reactivar» ---")
    PB = postular("demo-grupak", "Beto No", "5512347002", "beto@correo.mx", respuestas("Sí", "No", "Sí"))
    db.expire_all()
    pb = db.query(Postulacion).filter(Postulacion.codigo == PB).first()
    check(pb.estado == "no_cumple" and "Rolar turnos" in pb.analisis["prefiltro_web"]["motivo"], "No cumple con el criterio indispensable como motivo")
    check(not pb.activa and pb.motivo_cierre == "descartado" and pb.etapa == "Prefiltro"
          and any(h.get("evento") == "descartado" and "Rolar turnos" in h.get("motivo", "") for h in pb.historial),
          "el agente decide: «Descartado» (mismo cierre que RH) con su motivo, y se queda en su columna")
    s = seg(PB)
    check(not s["bloqueo"] and paso(s, "prefiltro")["estadoUnificado"] == "no_aprobada", "sin «Continuar por decisión de RH»: Prefiltro «No aprobada»")
    check("Rolar turnos" in client.get(f"/candidatos/{PB}", headers=HG).json()["motivoDescarte"], "la tarjeta muestra el motivo del descarte")
    check(not db.query(Evaluacion).filter(Evaluacion.postulacion_id == pb.id).count() and not pb.entrevistas,
          "descartada por el prefiltro no se dispara nada más")
    check(client.post(f"/candidatos/{PB}/reactivar", headers=HG, json={"motivo": "corto"}).status_code == 400, "«Reactivar» exige motivo")
    r = client.post(f"/candidatos/{PB}/reactivar", headers=HG, json={"motivo": "Acordó turno fijo matutino con el supervisor"})
    db.expire_all()
    pb = db.query(Postulacion).filter(Postulacion.codigo == PB).first()
    check(r.status_code == 200 and pb.activa and pb.etapa == "Entrevista IA" and len(pb.entrevistas) == 1,
          "«Reactivar»: vuelve activa, retoma la ruta (avanza sola y sale la liga de la entrevista)")
    check(any(h.get("evento") == "reactivada" and h.get("usuario") == admin.nombre for h in pb.historial)
          and paso(seg(PB), "prefiltro")["estadoUnificado"] == "aprobada_excepcion", "queda en el historial; el prefiltro continúa por decisión de RH")
    check(client.post(f"/candidatos/{PB}/reactivar", headers=HG, json={"motivo": "Segunda vez sin sentido"}).status_code == 409,
          "una postulación activa no se reactiva")

    print("\n--- 4. «Parcial» en un indispensable nunca aprueba ---")
    PP = postular("demo-grupak", "Pau Parcial", "5512347005", "pau@correo.mx", respuestas("Parcial", "Sí", "Sí"))
    s = seg(PP)
    x = paso(s, "prefiltro")
    check(x["estadoUnificado"] == "esperando_candidato" and "Secundaria" in x["espera"] and s["etapaActual"] == "Prefiltro",
          "con teléfono → «Esperando candidato»: el bot le preguntará solo lo que falta")
    print("\n--- 4b. Indeterminado sin teléfono para preguntar → el agente decide (Puntos por validar) ---")
    PC = postular("demo-grupak", "Caro Parcial", "", "caro@correo.mx", respuestas("Parcial", "Sí", "Sí"))
    s = seg(PC)
    db.expire_all()
    pc = db.query(Postulacion).filter(Postulacion.codigo == PC).first()
    check(s["etapaActual"] == "Entrevista IA" and pc.activa and not any(x.get("revisarPrefiltro") for e in s["etapas"] for x in e["pasos"]),
          "sin «Revisar prefiltro»: Parcial sin teléfono → el agente lo pasa y la ruta sigue sola")
    check(any("Secundaria" in t for t in pc.analisis.get("puntos_validar_prefiltro") or []),
          "lo no concluyente queda en «Puntos por validar»")
    check(client.post(f"/procesos/postulaciones/{PC}/prefiltro/aprobar", headers=HG, json={}).status_code == 410,
          "«Aprobar prefiltro» ya no existe (410)")

    print("\n--- 5. Aislamiento: otra Cuenta con la MISMA ruta ---")
    PM = postular("manual-sa", "Mario Manual", "5512347004", "mario@correo.mx", respuestas("Sí", "Sí", "Sí"))
    db.expire_all()
    pm = db.query(Postulacion).filter(Postulacion.codigo == PM).first()
    check("prefiltro_web" not in (pm.analisis or {}) and "motor_ruta" not in (pm.analisis or {}), "no se resuelve el prefiltro ni se dispara nada")
    check(pm.etapa == "Prefiltro" and not pm.entrevistas and not db.query(Evaluacion).filter(Evaluacion.postulacion_id == pm.id).count(),
          "se queda en Prefiltro con su avance manual")
    s = seg(PM, HM)
    check(not s["rutaAutomatica"] and s["descarteSugerido"] is None, "sin ruta automática ni descarte sugerido")
    check(paso(s, "entrevista-lider")["espera"].startswith("Se habilita en"), "conserva la habilitación por columna")

    print("\n--- 6. Vocabulario único de estados ---")
    VALIDOS = {"sin_iniciar", "esperando_candidato", "esperando_referencias", "esperando_consentimiento", "esperando_evaluador",
               "pendiente_resultado", "en_curso", "pendiente_revision", "completada", "aprobada", "no_aprobada", "omitida", "error",
               "aprobada_excepcion", "pendiente_agendar"}
    todos = [x for c, h in ((PA, HG), (PB, HG), (PC, HG), (PP, HG), (PM, HM)) for e in seg(c, h)["etapas"] for x in e["pasos"]]
    check(all(x["estadoUnificado"] in VALIDOS for x in todos), "toda actividad usa una de las 8 etiquetas")
    db.close()

print(f"\n{OK} comprobaciones OK")
