"""Grupak · VAC-1156 (2026-10-13; reestructura 2026-10-15): proceso «Operativo básico». Base desechable; IA, Meta y Bot
API SIMULADOS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_grupak.py

1. `scripts/configurar_grupak.py` (la función `configurar`): Cuenta/Cliente Grupak, proceso «Operativo básico» SIN Prefiltro
   web ni Prefiltro por WhatsApp (Solicitud web sin CV → Entrevista por WhatsApp Operativa → Entrevista humana (agenda RH) →
   Contrato y firma → Confirmación de ingreso); «Unas preguntas» = rolar turnos y cargar material pesado (NO descartan) +
   edad + colonia + interés + sueldo. Idempotente.
2. La entrevista por WhatsApp arranca sola al recibir la solicitud: sin ventana de 24 h sale la plantilla de inicio
   (presenta al bot: «Soy Red Human, el agente virtual de Grupak.»); su «Sí» → transición + «¿Trabajas actualmente?».
3. Cierre EXACTO «Con esto terminamos, [nombre]. Gracias por tu tiempo. El equipo de Recursos Humanos de Grupak te
   contactará para agendar una entrevista.» sin dirección, sin repetir puesto ni sueldo (ya se dijeron en la solicitud) y sin
   un segundo aviso de estado. «Nunca he trabajado» → «Sin experiencia laboral».
4. Avanzar / Avanzar con reservas → Filtro humano con la Entrevista humana «Pendiente de agendar»; No avanzar → se queda.
5. Entrevista humana aprobada → la ruta sigue a Contratación (ya no hay contratación directa: la ruta trae Contratación).
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_grupak_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "grupak.db").replace(chr(92), "/")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-grupak"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, PlantillaProceso, Postulacion, Usuario, Vacante  # noqa: E402
from app.routers.webhooks import procesar_entrante  # noqa: E402
from app.serial import vacante_dict  # noqa: E402
from app.services import entrevista_whatsapp_fija as ewf  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

import configurar_grupak as cg  # noqa: E402

OK = 0
ENVIADOS = []
VENTANA = set()
PRESENTACION = "Soy Red Human, el agente virtual de Grupak."


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    if cuerpo.get("type") == "text" and cuerpo.get("to") not in VENTANA:
        return {"enviado": False, "proveedor": "meta", "detalle": "131047: Re-engagement message", "codigo": 131047}
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso


def _msg(tel, cuerpo):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Candidato"}, "wa_id": f"52{tel}"}],
        "messages": [{"from": f"52{tel}", "id": f"wamid.in.{tel}.{len(ENVIADOS)}.{id(cuerpo)}", **cuerpo}]}}]}]}


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    db.commit()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()

    def chat(tel, texto):
        VENTANA.add("52" + tel)
        antes = len(ENVIADOS)
        asyncio.run(procesar_entrante(db, whatsapp.parsear_webhook(_msg(tel, {"type": "text", "text": {"body": texto}}))))
        db.expire_all()
        salida = [c["text"]["body"] for c in ENVIADOS[antes:] if c.get("type") == "text" and c["to"] == "52" + tel]
        return salida[-1] if salida else ""

    # ================= 1. configuración =================
    print("\n--- 1. configurar_grupak ---")
    v = cg.configurar(db)
    db.commit()
    check(not cg.validar(db, v), f"la configuración pasa la validación del script {cg.validar(db, v)}")
    cuenta = db.get(Cuenta, v.cuenta_id)
    pasos = v.proceso["pasos"]
    check([sproc.clave_catalogo(x) for x in pasos] == cg.TIPOS_ESPERADOS
          and not any(x["tipo"] in ("prefiltro_web", "prefiltro_whatsapp") for x in pasos),
          "«Operativo básico»: solicitud sin CV → entrevista WhatsApp → entrevista humana → contrato → ingreso (sin prefiltros)")
    check(v.enfoque_entrevista == "operativo" and ewf.vista(v)["esBase"], "entrevista base operativa por WhatsApp, sin preguntas agregadas")
    criterios = vacante_dict(v)["criterios"]
    check(not any(c.get("descarta") for c in criterios) and {c["pregunta"] for c in criterios} >= set(cg.FILTROS.values()),
          f"«Unas preguntas»: rolar turnos y cargar pesado NO descartan → {[c['pregunta'] for c in criterios]}")
    n_pl = db.query(PlantillaProceso).filter(PlantillaProceso.cuenta_id == cuenta.id, PlantillaProceso.nombre == cg.NOMBRE_PROCESO).count()
    v2 = cg.configurar(db)
    db.commit()
    check(v2.id == v.id and n_pl == 1 and db.query(Vacante).filter(Vacante.cuenta_id == cuenta.id, Vacante.titulo == "Ayudante general").count() == 1,
          "idempotente: correrlo dos veces no duplica proceso ni vacante")
    v.estado = "Publicada"
    db.commit()

    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    def postular(nombre, tel, cambios=None):
        resp = {c["pregunta"]: ("10000" if c.get("expectativa") else  # 2026-10-16: la expectativa de sueldo
                                {"numero": "28", "abierta": "Col. Mitras Centro, Monterrey"}.get(c["tipo"], "Sí")) for c in criterios}
        resp.update(cambios or {})
        r = client.post("/candidatos/postular", data={"vacante": v.slug, "nombre": nombre, "telefono": tel, "consentimiento": "true",
                                                      "respuestas": json.dumps([{"pregunta": q, "respuesta": a} for q, a in resp.items()])})
        assert r.status_code in (200, 201), r.text
        db.expire_all()
        return db.query(Postulacion).filter(Postulacion.codigo == r.json()["postulacion"]).one()

    def entrevista(p):
        return next(x for x in p.entrevistas if x.tipo == "whatsapp")

    # ================= 2. arranque por plantilla y presentación única =================
    print("\n--- 2. arranque ---")
    antes = len(ENVIADOS)
    pa = postular("Ana Avanza", "5581110002")
    tpl = [c for c in ENVIADOS[antes:] if c.get("type") == "template" and c["to"] == "525581110002"]
    check(tpl and tpl[0]["template"]["name"] == "inicio_entrevista_rh", "sin ventana de 24 h, la entrevista abre con la plantilla de inicio")
    check(pa.etapa == "Entrevista IA" and entrevista(pa).estado == "programada", "la ruta pasa sola a Filtro Red Human; la entrevista espera su «Sí»")
    r = chat("5581110002", "Sí, empezar ahora")
    check(r == "Listo, Ana. Ahora te haré unas preguntas sobre tu experiencia. ¿Trabajas actualmente?", f"transición → «{r}»")
    pa = db.get(Postulacion, pa.id)
    check(sum(PRESENTACION in (m.texto or "") for m in pa.mensajes if m.rol == "assistant") <= 1, "el bot se presenta una sola vez")

    # ================= 3-4. entrevista fija, cierre y avance =================
    print("\n--- 3. entrevista por WhatsApp ---")
    for t in ("Sí", "Bodegas del Norte", "Ayudante general", "Cargaba y acomodaba cajas", "2 años", "Quiero un mejor horario"):
        r = chat("5581110002", t)
    check(r == "¿Tuviste otro trabajo antes de ese?", f"historial → «{r}»")
    r = chat("5581110002", "No")
    check(r == "¿Cuándo podrías empezar?", f"puesto, sueldo y ubicación ya se dijeron en la solicitud → solo el inicio («{r}»)")
    antes = len(ENVIADOS)
    r = chat("5581110002", "El lunes")
    check(r == cg.CIERRE.format(nombre="Ana"), f"cierre exacto → «{r}»")
    check(not any(x in r.lower() for x in ("dirección", "fecha", "calle", "hora", "sueldo", "$")), "el cierre no lleva dirección, fecha ni sueldo")
    db.expire_all()
    pa = db.get(Postulacion, pa.id)
    op = (entrevista(pa).evaluacion or {}).get("operativa") or {}
    check(op.get("recomendacion") in ("recomendable", "con_reservas"), f"dictamen «{op.get('recomendacion')}»")
    check(pa.etapa == "Entrevista Humana" and pa.activa, f"Avanzar / Avanzar con reservas → Filtro humano ({pa.etapa})")
    eh = next(x for x in sproc.estado_pasos(pa) if x["tipo"] == "entrevista_humana")
    check(eh.get("estadoUnificado") == "pendiente_agendar", f"Entrevista humana «Pendiente de agendar» ({eh.get('estadoUnificado')})")
    textos = [c.get("text", {}).get("body", "") for c in ENVIADOS[antes:] if c.get("type") == "text"]
    check(not any("Siguiente paso" in t for t in textos), "sin un segundo aviso de estado después del cierre")
    r = chat("5581110002", "Gracias")
    check(r == "Con gusto, Ana.", f"«Gracias» → «{r}»")

    print("\n--- 4. Sin experiencia y con puntos → No avanzar ---")
    pn = postular("Nora Nunca", "5581110003", {cg.FILTROS[cg.TURNOS]: "No"})
    check(pn.activa and any("rolar turnos" in x for x in pn.analisis.get("puntos_validar_prefiltro") or []),
          "«No» a rolar turnos NO descarta: queda en Puntos por validar")
    chat("5581110003", "Sí, empezar ahora")
    r = chat("5581110003", "Nunca he trabajado")
    check(r == "¿Cuándo podrías empezar?", f"«Nunca» → bloque C directo → «{r}»")
    r = chat("5581110003", "En un mes")
    check(r == cg.CIERRE.format(nombre="Nora"), "mismo cierre exacto")
    db.expire_all()
    pn = db.get(Postulacion, pn.id)
    op = (entrevista(pn).evaluacion or {}).get("operativa") or {}
    check(ewf.historial(entrevista(pn))[0]["empresa"] == ewf.SIN_EXPERIENCIA, "historial: «Sin experiencia laboral»")
    check(op.get("recomendacion") == "no_recomendable", f"sin experiencia + Puntos por validar → «No avanzar» ({op.get('recomendacion')})")
    check(pn.etapa == "Entrevista IA" and pn.activa, "No avanzar → se queda en Filtro Red Human para decisión de RH")

    # ================= 5. entrevista humana → Contratación =================
    print("\n--- 5. entrevista humana ---")
    manana = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    r = client.post(f"/evaluaciones/postulaciones/{pa.codigo}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "paso_id": eh["id"],
        "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "cita": {"fecha": manana, "hora": "10:00", "modalidad": "Presencial", "direccion": "Av. Constitución 100, Monterrey"},
    })
    check(r.status_code == 201, f"RH programa la entrevista humana ({r.status_code} {r.text[:160]})")
    ev = r.json()["evaluacion"]
    r = client.post(f"/evaluaciones/{ev['codigo']}/resultado", data={"conclusion": "avanzar", "comentarios": "Buena actitud", "version": "0"})
    check(r.status_code == 200, f"el entrevistador aprueba ({r.status_code} {r.text[:160]})")
    db.expire_all()
    pa = db.get(Postulacion, pa.id)
    check(pa.activa and pa.etapa == "Contratación", f"la ruta sigue a Contratación (activa={pa.activa}, etapa={pa.etapa})")

    app.dependency_overrides.clear()
    db.close()

print(f"\n✅ {OK} comprobaciones")
