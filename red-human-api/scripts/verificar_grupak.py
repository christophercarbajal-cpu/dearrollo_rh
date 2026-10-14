"""Grupak · VAC-1156 (2026-10-13): proceso «Operativo básico» y reglas de avance por recomendación. Base desechable; IA,
Meta y Bot API SIMULADOS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_grupak.py

1. `scripts/configurar_grupak.py` (la función `configurar`): Cuenta/Cliente Grupak, proceso «Operativo básico» sin
   Contratación ni Onboarding, vacante con enfoque operativo, entrevista base y prefiltro de WhatsApp fijo. Idempotente.
2. El bot se presenta UNA sola vez: «Soy Red Human, el agente virtual de Grupak.» (aviso de privacidad o saludo).
3. Entrevista fija: «nunca he trabajado» → «¿Has hecho algún trabajo, aunque sea por tu cuenta o ayudando a alguien?»;
   cierre EXACTO «Con esto terminamos, [nombre]. Gracias por tu tiempo. El equipo de Recursos Humanos de Grupak te
   contactará para agendar una entrevista.» sin dirección ni fecha y sin un segundo aviso de estado.
4. Recomendable / Con reservas → Filtro humano con la Entrevista humana «Pendiente de agendar»; No recomendable → se queda.
5. Entrevista humana aprobada y sin más actividades → la postulación cierra como «Contratado».
"""

import os
import sys
import tempfile
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
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import entrevista_whatsapp_fija as ewf  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

import configurar_grupak as cg  # noqa: E402
from _respuestas_prefiltro import respuesta_pendiente  # noqa: E402

OK = 0
ENVIADOS = []
PRESENTACION = "Soy Red Human, el agente virtual de Grupak."
CIERRE = ("Con esto terminamos, {n}. Gracias por tu tiempo. El equipo de Recursos Humanos de Grupak te contactará para "
          "agendar una entrevista.")


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    db.commit()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()

    # ================= 1. configuración =================
    print("\n--- 1. configurar_grupak ---")
    v = cg.configurar(db)
    db.commit()
    check(not cg.validar(db, v), "la configuración pasa la validación del script")
    cuenta = db.get(Cuenta, v.cuenta_id)
    pasos = v.proceso["pasos"]
    check([sproc.clave_catalogo(x) for x in pasos] == cg.TIPOS_ESPERADOS
          and {x["etapa"] for x in pasos} == {"Prefiltro", "Entrevista IA", "Entrevista Humana"},
          "«Operativo básico»: solicitud sin CV → prefiltro web → prefiltro WhatsApp | entrevista WhatsApp | entrevista humana")
    check(v.enfoque_entrevista == "operativo" and ewf.vista(v)["esBase"], "entrevista base operativa por WhatsApp, sin preguntas agregadas")
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

    def chat(p, texto):
        r = client.post(f"/candidatos/{p.codigo}/prefiltro", json={"texto": texto})
        assert r.status_code == 200, r.text
        return r.json().get("respuesta") or ""

    def postular(nombre, tel):
        c = _crear_candidato(db, cuenta.id, nombre, "Portal", False, telefono=tel)
        p = crear_postulacion(db, c, v, cuenta.id, "web", consentimiento=True)
        db.commit()
        return p

    def pasar_prefiltro(p):
        primera = chat(p, "Hola")
        for _ in range(20):
            db.refresh(p)
            if pconv.estado(p).get("resultado"):
                break
            chat(p, respuesta_pendiente(p))
        db.refresh(p)
        return primera

    def entrevista(p):
        return next(x for x in p.entrevistas if x.tipo == "whatsapp")

    # ================= 2. presentación única =================
    print("\n--- 2. presentación ---")
    p1 = postular("Pedro Primero", "5581110001")
    r = chat(p1, "Hola")
    check(r.startswith(f"Hola, Pedro. {PRESENTACION} "), f"saludo con la presentación exacta → «{r[:80]}…»")
    db.refresh(p1)
    r2 = chat(p1, "Hola")
    check(PRESENTACION not in r2, "un nuevo «Hola» repite la pregunta pendiente sin volver a presentarse")
    p0 = postular("Lucía Aviso", "5581110000")
    pconv.marcar_presentado(p0)  # lo que hace el webhook al mandar el aviso de privacidad (que ya trae la presentación)
    db.commit()
    from app.routers.webhooks import _texto_aviso_privacidad

    check(PRESENTACION in _texto_aviso_privacidad("Lucía", v), "el aviso de privacidad trae la presentación")
    r = chat(p0, "Sí")
    check(PRESENTACION not in r and r.startswith("Hola, Lucía. ¿"), f"tras el aviso, el prefiltro NO se presenta otra vez → «{r[:60]}»")

    # ================= 3-4. entrevista fija: «nunca he trabajado», cierre y avance =================
    print("\n--- 3. entrevista por WhatsApp ---")
    pa = postular("Ana Avanza", "5581110002")
    pasar_prefiltro(pa)
    check(pa.etapa == "Entrevista IA" and pconv.estado(pa).get("resultado") == "cumple", "prefiltro aprobado → Filtro Red Human")
    transcript = entrevista(pa).transcript or []
    check(sum(PRESENTACION in (m.get("texto") or "") for m in transcript) == 0, "la entrevista no se vuelve a presentar")
    r = chat(pa, "No, nunca he trabajado")
    check(r == ewf.PREGUNTA_SIN_EXPERIENCIA, f"«nunca he trabajado» → «{r}»")
    r = chat(pa, "Sí, ayudaba a mi tío en su bodega")
    check(r == "¿Qué hacías ahí?", f"trabajo informal: sigue con qué hacía → «{r}»")
    for t in ("Cargaba y acomodaba cajas", "2 años"):
        r = chat(pa, t)
    check(r == "¿Por qué saliste?", f"→ «{r}»")
    for t in ("Porque cerró la bodega", "No", "Sí", "Sí", "Sí"):
        r = chat(pa, t)
    check(r == "¿Cuándo podrías empezar?", f"confirmaciones → inicio «{r}»")
    antes = len(ENVIADOS)
    r = chat(pa, "El lunes")
    check(r == CIERRE.format(n="Ana"), f"cierre exacto → «{r}»")
    check(not any(x in r.lower() for x in ("dirección", "fecha", "calle", "hora")), "el cierre no lleva dirección ni fecha")
    db.expire_all()
    pa = db.get(Postulacion, pa.id)
    op = (entrevista(pa).evaluacion or {}).get("operativa") or {}
    check(op.get("recomendacion") in ("recomendable", "con_reservas"), f"dictamen «{op.get('recomendacion')}»")
    check(pa.etapa == "Entrevista Humana" and pa.activa, f"Recomendable/Con reservas → avanza a Filtro humano ({pa.etapa})")
    eh = next(x for x in sproc.estado_pasos(pa) if x["tipo"] == "entrevista_humana")
    check(eh.get("estadoUnificado") == "pendiente_agendar", f"Entrevista humana «Pendiente de agendar» ({eh.get('estadoUnificado')})")
    textos = [c.get("text", {}).get("body", "") for c in ENVIADOS[antes:] if c.get("type") == "text"]
    check(not any("Siguiente paso" in t for t in textos), "sin un segundo aviso de estado después del cierre")

    print("\n--- 4. No recomendable no avanza ---")
    pn = postular("Nora Nunca", "5581110003")
    pasar_prefiltro(pn)
    chat(pn, "No, nunca he trabajado")
    r = chat(pn, "No, nunca")
    check(r != ewf.PREGUNTA_SIN_EXPERIENCIA, f"la repregunta sale una sola vez → «{r}»")
    for t in ("Sí", "No, es muy poco", "Sí"):
        r = chat(pn, t)
    r = chat(pn, "En un mes")
    check(r == CIERRE.format(n="Nora"), "mismo cierre exacto")
    db.expire_all()
    pn = db.get(Postulacion, pn.id)
    op = (entrevista(pn).evaluacion or {}).get("operativa") or {}
    check(op.get("recomendacion") == "no_recomendable", f"sin experiencia + Puntos por validar → «No recomendable» ({op.get('recomendacion')})")
    check(pn.etapa == "Entrevista IA" and pn.activa, "No recomendable → se queda en Filtro Red Human para decisión de RH")

    # ================= 5. contratación directa =================
    print("\n--- 5. contratación directa ---")
    manana = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    r = client.post(f"/evaluaciones/postulaciones/{pa.codigo}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "paso_id": eh["id"],
        "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "cita": {"fecha": manana, "hora": "10:00", "modalidad": "Presencial", "direccion": "Av. Constitución 100, Monterrey"},
    })
    check(r.status_code == 201, f"RH programa la entrevista humana ({r.status_code} {r.text[:160]})")
    ev = r.json()["evaluacion"]
    db.expire_all()
    check(db.get(Postulacion, pa.id).activa, "con la cita programada sigue activa")
    r = client.post(f"/evaluaciones/{ev['codigo']}/resultado", data={"conclusion": "avanzar", "comentarios": "Buena actitud", "version": "0"})
    check(r.status_code == 200, f"el entrevistador aprueba ({r.status_code} {r.text[:160]})")
    db.expire_all()
    pa = db.get(Postulacion, pa.id)
    check(not pa.activa and pa.motivo_cierre == "contratado" and pa.etapa == "Entrevista Humana",
          f"sin más actividades → «Contratado» (activa={pa.activa}, motivo={pa.motivo_cierre})")
    check(any(h.get("evento") == "contratado_directo" for h in pa.historial or []), "queda en el historial")

    app.dependency_overrides.clear()
    db.close()

print(f"\n✅ {OK} comprobaciones")
