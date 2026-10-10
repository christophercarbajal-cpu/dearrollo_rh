"""Regresión del prefiltro conversacional y la limpieza de la ruta de demo-grupak (2026-10-08).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_prefiltro_conversacional.py

Base desechable, SIN red: WhatsApp (Meta) y la Bot API de Telegram se reemplazan por falsos que registran cada envío;
sin OpenAI (la clasificación es determinista). Cubre la cadena completa:
  1. Limpieza de ruta: «Persona bajo la lluvia» sale de la plantilla y de la vacante; el candidato que la esperaba queda
     liberado con «Ajuste de ruta de demostración» (idempotente; otra Cuenta no se toca).
  2. Web + chat en UNA entidad: un «Parcial» del formulario nunca aprueba; el bot pregunta SOLO lo que falta, una
     pregunta por mensaje; un saludo/reconexión repite la pregunta pendiente EXACTA; Ubicación solo con ubicación
     configurada; NSS y Experiencia se registran sin excluir.
  3. Resolución al vuelo: Cumple → «Tu perfil es compatible… entrevista con Red Human: <liga>» (sin pedir agendar
     videollamada); respuesta ambigua → aclaración; indispensable incumplido → postulación cerrada con el motivo exacto
     y mensaje de cierre.
  4. «Continuar por decisión de RH»: reabre conservando las respuestas, registra quién autorizó y manda SOLA la liga
     de la entrevista por el canal conectado (aquí, Telegram).
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

_dir = tempfile.mkdtemp(prefix="rh_prefiltro_conv_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "pconv.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "META_APP_SECRET", "ANAM_API_KEY",
          "RESEND_API_KEY", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "PSICOMETRICAS_USUARIO", "TELEGRAM_WEBHOOK_SECRET",
          "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:PRUEBA-token-falso"
os.environ["TELEGRAM_BOT_USERNAME"] = "RedHumanPruebaBot"
os.environ["ADMIN_PASSWORD"] = "prueba-prefiltro-conv"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bitacora, Cuenta, PlantillaProceso, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers import webhooks  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import ajustes_demo, telegram  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp as swa  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402
from app.services.mensajeria import en_conversacion  # noqa: E402

OK = 0
WA = []  # cuerpos enviados a Meta
TG = []  # (metodo, json) enviados a la Bot API


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    WA.append(cuerpo)
    return {"enviado": True, "proveedor": "meta", "detalle": "", "wa_id": f"wamid.prueba.{len(WA)}"}


async def _tg_falso(metodo, json=None, data=None, files=None):
    TG.append((metodo, json or {}))
    return {"ok": True, "result": {"message_id": len(TG)}}


settings.whatsapp_provider = "meta"
swa._meta_post = _meta_falso
telegram.llamar = _tg_falso


def textos_wa(tel10, desde=0):
    return [c["text"]["body"] for c in WA[desde:] if c.get("type") == "text" and c.get("to", "").endswith(tel10)]


def textos_tg(chat, desde=0):
    return [j.get("text", "") for (m, j) in TG[desde:] if m == "sendMessage" and str(j.get("chat_id")) == str(chat)]


RUTA = [
    {"id": "prefiltro", "tipo": "prefiltro_web", "nombre": "Prefiltro", "etapa": "Prefiltro"},
    {"id": "persona-lluvia", "tipo": "otra", "nombre": "Persona bajo la lluvia", "etapa": "Entrevista IA", "responsable": {"tipo": "rh"}},
    {"id": "entrevista_red_human", "tipo": "entrevista_agente", "nombre": "Entrevista profunda Red Human", "etapa": "Entrevista IA"},
    {"id": "entrevista-lider", "tipo": "entrevista_humana", "nombre": "Entrevista con líder", "etapa": "Entrevista Humana"},
    {"id": "propuesta", "tipo": "condiciones", "nombre": "Propuesta", "etapa": "Contratación"},
]
ETAPAS = {e: {"avance_automatico": True} for e in ("Prefiltro", "Entrevista IA", "Entrevista Humana")}
SEMBRADAS = [{"pregunta": "¿Cumples con: Secundaria terminada?", "tipo": "si_no", "valida": "Secundaria terminada", "respuesta_esperada": "Sí",
              "descarta": True, "opciones": ["Sí", "No", "Parcial"]},
             {"pregunta": "¿Cumples con: Disponibilidad para rolar turnos?", "tipo": "si_no", "valida": "Disponibilidad para rolar turnos",
              "respuesta_esperada": "Sí", "descarta": True, "opciones": ["Sí", "No", "Parcial"]}]


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    obtener(db).modo_prueba = False
    base = db.query(Vacante).filter(Vacante.estado == "Publicada").limit(3).all()
    cuentas = {}
    for slug in ("demo-grupak", "otra-cuenta"):
        cu = Cuenta(nombre=slug, nombre_comercial=slug.title(), razon_social=slug, estado="Activa", slug=slug, canal_mensajeria="ambos")
        db.add(cu)
        db.flush()
        db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cu.id))
        cuentas[slug] = cu
    cu = cuentas["demo-grupak"]
    pl = PlantillaProceso(cuenta_id=cu.id, nombre="Ruta Demo Grupak", pasos=sproc.normalizar_pasos(RUTA),
                          etapas=sproc.normalizar_etapas(ETAPAS), version=1, predeterminada=True, activa=True)
    pl_otra = PlantillaProceso(cuenta_id=cuentas["otra-cuenta"].id, nombre="Ruta con lluvia", pasos=sproc.normalizar_pasos(RUTA),
                               etapas=sproc.normalizar_etapas(ETAPAS), version=1, activa=True)
    db.add_all([pl, pl_otra])
    db.flush()
    ayudante, sin_ubic, otra_v = base
    for v, cuenta in ((ayudante, cu), (sin_ubic, cu), (otra_v, cuentas["otra-cuenta"])):
        v.cuenta_id = cuenta.id
        v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": RUTA, "etapas": ETAPAS})
    ayudante.titulo = "Ayudante general"
    ayudante.preguntas_filtro = [dict(q) for q in SEMBRADAS]
    ayudante.ubicacion, ayudante.ubicacion_estado, ayudante.ubicacion_municipio, ayudante.modalidad = (
        "Monterrey, Nuevo León", "Nuevo León", "Monterrey", "Presencial")
    sin_ubic.titulo = "Auxiliar de limpieza"
    sin_ubic.preguntas_filtro = [dict(q) for q in ajustes_demo.PREGUNTAS_AYUDANTE]
    sin_ubic.ubicacion = sin_ubic.ubicacion_estado = sin_ubic.ubicacion_municipio = ""
    # candidato de la demo detenido por «Persona bajo la lluvia» (Filtro Red Human, obligatoria sin hacer)
    persona = _crear_candidato(db, cu.id, "Lalo Lluvia", "WhatsApp", False, correo="lalo@demo.invalid", telefono="")
    bloqueado = crear_postulacion(db, persona, ayudante, cu.id, "whatsapp", consentimiento=True)
    bloqueado.etapa = "Entrevista IA"
    persona2 = _crear_candidato(db, cuentas["otra-cuenta"].id, "Olga Otra", "WhatsApp", False, correo="olga@demo.invalid", telefono="")
    otra_p = crear_postulacion(db, persona2, otra_v, cuentas["otra-cuenta"].id, "whatsapp", consentimiento=True)
    otra_p.etapa = "Entrevista IA"
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    H = {"X-Cuenta-Id": str(cu.id)}

    def recargar(codigo):
        db.expire_all()
        return db.query(Postulacion).filter(Postulacion.codigo == codigo).first()

    def seg(codigo):
        return client.get(f"/procesos/postulaciones/{codigo}", headers=H).json()

    def paso(s, pid):
        return next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == pid)

    # ------------------------------------------------------------------ 1. limpieza de ruta
    print("\n--- 1. Ruta demo sin «Persona bajo la lluvia» ---")
    check(any(x["id"] == "persona-lluvia" for x in sproc.faltantes(sproc.estado_pasos(bloqueado), "Entrevista IA", "Entrevista Humana")),
          "antes: el candidato está detenido por «Persona bajo la lluvia»")
    previo = ajustes_demo.ajustar(db, aplicar=False)
    db.rollback()
    check(previo == {"plantillas": 1, "vacantes": 2, "postulaciones_liberadas": 1, "criterios_ayudante": 1},
          f"la simulación cuenta lo que cambiaría sin escribir nada ({previo})")
    r = ajustes_demo.ajustar(db)
    db.commit()
    db.expire_all()
    pl, ayudante, otra_v = db.get(PlantillaProceso, pl.id), db.get(Vacante, ayudante.id), db.get(Vacante, otra_v.id)
    check(not any(x["id"] == "persona-lluvia" for x in pl.pasos) and pl.version == 2, "la plantilla de demo-grupak ya no la trae (versión 2)")
    check(not any(x["id"] == "persona-lluvia" for x in ayudante.proceso["pasos"]), "la copia de la vacante tampoco")
    bloqueado = recargar(bloqueado.codigo)
    d = (bloqueado.proceso_estado or {}).get("persona-lluvia", {}).get("omitida") or {}
    check(d.get("motivo") == "Ajuste de ruta de demostración", "el candidato detenido la tiene OMITIDA con «Ajuste de ruta de demostración»")
    check(not any(x["id"] == "persona-lluvia" for x in sproc.faltantes(sproc.estado_pasos(bloqueado), "Entrevista IA", "Entrevista Humana")),
          "la compuerta ya no la exige: el motor lo deja avanzar")
    check([q["clave"] for q in ayudante.preguntas_filtro] == ["secundaria", "turnos", "ubicacion", "nss_fiscal", "experiencia"],
          "«Ayudante general» toma los criterios del prefiltro conversacional")
    check(ajustes_demo.ajustar(db) == {"plantillas": 0, "vacantes": 0, "postulaciones_liberadas": 0, "criterios_ayudante": 0},
          "idempotente: correrlo de nuevo no cambia nada")
    db.rollback()
    check(any(x["id"] == "persona-lluvia" for x in db.get(PlantillaProceso, pl_otra.id).pasos)
          and any(x["id"] == "persona-lluvia" for x in otra_v.proceso["pasos"])
          and not (recargar(otra_p.codigo).proceso_estado or {}), "otra Cuenta con la misma actividad no se toca")

    criterios = {c["id"]: c for c in pconv.criterios_de(ayudante)}
    check([c["indispensable"] for c in criterios.values()] == [True, True, False, False, False],
          "Secundaria y Turnos indispensables; Ubicación, NSS/situación fiscal y Experiencia solo se registran")
    check("Monterrey" in criterios["ubicacion"]["chat"], "Ubicación se pregunta con la ubicación de la vacante")
    check("ubicacion" not in {c["id"] for c in pconv.criterios_de(db.get(Vacante, sin_ubic.id))},
          "vacante SIN ubicación configurada → no se pregunta")

    # ------------------------------------------------------------------ 2-3. WhatsApp: web + chat, aprobado
    print("\n--- 2. Web con «Parcial» + chat por WhatsApp → aprobado y liga de la entrevista ---")
    import json as _json

    resp = _json.dumps([{"pregunta": "¿Terminaste la secundaria?", "respuesta": "Parcial"},
                        {"pregunta": "¿Tienes disponibilidad para rolar turnos?", "respuesta": "Sí"}])
    r = client.post("/candidatos/postular", data={"vacante": ayudante.codigo, "nombre": "Ana Web", "telefono": "5512349001",
                                                  "correo": "ana.web@correo.mx", "consentimiento": "true", "respuestas": resp})
    check(r.status_code == 201, "postulación web recibida")
    PA = r.json()["postulacion"]
    pa = recargar(PA)
    check(pa.activa and pa.etapa == "Prefiltro" and not (pa.analisis or {}).get("prefiltro_web"),
          "un «Parcial» en un indispensable NO aprueba (ni descarta) el prefiltro")
    x = paso(seg(PA), "prefiltro")
    check(x["estadoUnificado"] == "esperando_candidato" and "Secundaria terminada" in x["espera"],
          "el paso dice exactamente qué falta: «Esperando respuesta del candidato: Secundaria terminada»")

    def wa(texto, tel="5215512349001"):
        antes = len(WA)
        with en_conversacion("whatsapp", tel):
            asyncio.run(webhooks.procesar_entrante(db, {"telefono": tel, "texto": texto, "nombre": "Ana Web", "wa_id": f"w{len(WA)}",
                                                        "tipo": "text", "id_seleccionado": "", "numero_receptor": "", "canal": "whatsapp"}))
        return textos_wa(tel[-10:], antes)

    salida = wa("Hola, ya llené el formulario")
    check(len(salida) == 1 and salida[0].endswith("¿Terminaste la secundaria?") and "formulario" in salida[0],
          "el bot pregunta SOLO lo que falta, una pregunta por mensaje (turnos ya venía del formulario)")
    salida = wa("hola")
    check(len(salida) == 1 and salida[0].endswith("\n\n¿Terminaste la secundaria?") and salida[0].startswith("Retomemos"),
          "reconexión / saludo → repite la pregunta pendiente EXACTA")
    salida = wa("Terminé la prepa")
    pa = recargar(PA)
    # retro 2026-10-09: el chat SOLO pregunta indispensables — ubicación, NSS y experiencia no se preguntan
    check(pa.activa and pa.analisis["prefiltro_web"]["resultado"] == "cumple" and pa.prefiltro_completo,
          "prepa cumple secundaria y turnos venía «Sí» del formulario → aprobado al vuelo; lo no indispensable NO se pregunta")
    check(pa.etapa == "Entrevista IA" and len(pa.entrevistas) == 1, "avanza solo a Filtro Red Human y se crea la sala de la entrevista")
    liga = f"{settings.app_url}/entrevista/{pa.entrevistas[0].token}"
    check(len(salida) == 1 and salida[0].endswith(liga) and "compatible" not in salida[0].lower(),
          f"UN mensaje con la liga de la entrevista, sin «Tu perfil es compatible» ni promesas: «{salida[0][:90]}…»")
    ent = pa.analisis[pconv.ENTIDAD]["respuestas"]
    check(ent["turnos"]["fuente"] == "web" and ent["secundaria"]["fuente"] == "whatsapp" and "nss_fiscal" not in ent
          and "experiencia" not in ent, "web y chat alimentan la MISMA entidad (con su fuente)")
    check(paso(seg(PA), "prefiltro")["estadoUnificado"] == "aprobada", "Prefiltro «Aprobada» en la ruta")
    salida = wa("ok, gracias")
    check(len(salida) == 1 and liga in salida[0], "si escribe de nuevo, se le recuerda la liga de su entrevista")
    todos = textos_wa("5512349001")
    check(not any("videollamada" in t.lower() or "agendar" in t.lower() or "disponibilidad tienes" in t.lower() for t in todos),
          "con entrevista por liga NUNCA se pide agendar videollamada")
    check(recargar(PA).videollamada_agendada_en is None and not recargar(PA).espera_respuesta, "sin agenda que coordinar por chat")

    # ------------------------------------------------------------------ 3. Telegram: ambigua + reconexión + cierre
    print("\n--- 3. Telegram (mismo flujo): aclaración, /start y cierre por indispensable ---")
    CHAT = 7700123

    def tg(**campos):
        uid = 900000 + len(TG) * 7 + len(campos)
        msg = {"update_id": uid, "message": {"message_id": uid, "chat": {"id": CHAT, "type": "private"},
                                             "from": {"id": CHAT, "first_name": "Toño", "last_name": "Telegram"}, **campos}}
        antes = len(TG)
        r = client.post("/api/webhooks/telegram", json=msg, headers={"X-Telegram-Bot-Api-Secret-Token": telegram.secreto_webhook()})
        assert r.status_code == 200, r.text
        return textos_tg(CHAT, antes)

    tg(text="Hola")
    tg(contact={"phone_number": "+52 1 55 1234 9002", "user_id": CHAT, "first_name": "Toño"})
    tg(text=f"/start vac_{ayudante.codigo}")
    salida = tg(text="Sí, acepto")
    db.expire_all()
    pt = (db.query(Postulacion).filter(Postulacion.vacante_id == ayudante.id, Postulacion.codigo != PA)
          .order_by(Postulacion.id.desc()).first())
    check(pt is not None and pt.consentimiento and salida and salida[-1].endswith("¿Terminaste la secundaria?"),
          "por Telegram corre el MISMO flujo: consentimiento → primera pregunta")
    check(sum(1 for t in salida if "?" in t) == 1, "una sola pregunta por mensaje")
    salida = tg(text="sí")
    check(len(salida) == 1 and salida[0].endswith("¿Tienes disponibilidad para rolar turnos?"), "→ Turnos")
    salida = tg(text="No entiendo")
    check(len(salida) == 1 and salida[0].startswith("Te lo pregunto de otra forma") and "rolar turnos" in salida[0].lower(),
          "«No entiendo» → reformula la MISMA pregunta (no la toma como «No»)")
    check(recargar(pt.codigo).activa, "…y no cierra la postulación")
    salida = tg(text="Solo puedo en la mañana")
    check(len(salida) == 1 and salida[0].startswith("Para no equivocarme") and "rolar turnos" in salida[0],
          "respuesta ambigua → pide aclaración antes de decidir")
    check(recargar(pt.codigo).activa, "…y todavía no decide")
    salida = tg(text="/start")
    check(len(salida) == 1 and salida[0].endswith("\n\n¿Tienes disponibilidad para rolar turnos?") and salida[0].startswith("Retomemos"),
          "/start retoma la pregunta pendiente EXACTA (no la cuenta como respuesta)")
    salida = tg(text="No")
    pt = recargar(pt.codigo)
    esperado = ("Gracias por tu interés. Para esta vacante necesitamos disponibilidad para rolar turnos, por lo que en esta ocasión "
                "no continuaremos con tu postulación.")
    check(salida == [esperado], "mensaje de cierre con el requisito exacto, por Telegram")
    check(not pt.activa and pt.motivo_cierre == "descartado" and pt.estado == "no_cumple",
          "el agente decide: «Descartado» (mismo cierre que el descarte de RH)")
    check(pt.analisis["prefiltro_web"]["motivo"] == "No cumple el requisito indispensable: Disponibilidad para rolar turnos"
          and any(h.get("evento") == "descartado" for h in pt.historial), "motivo exacto en el prefiltro y en el historial")
    check(not any("¿" in t for t in salida), "no se pregunta nada más después de un indispensable incumplido")

    # ------------------------------------------------------------------ 4. excepción de RH
    print("\n--- 4. «Reactivar» (menú «…») → retoma la ruta y manda la liga sola ---")
    s = seg(pt.codigo)
    check(not s["bloqueo"], "una postulación descartada ya no ofrece «Continuar por decisión de RH» (la sustituye «Reactivar»)")
    check(client.post(f"/candidatos/{pt.codigo}/reactivar", headers=H, json={"motivo": "corto"}).status_code == 400,
          "sin motivo suficiente → 400 (no reactiva)")
    check(not recargar(pt.codigo).activa, "…y la postulación sigue cerrada")
    antes = len(TG)
    r = client.post(f"/candidatos/{pt.codigo}/reactivar", headers=H, json={"motivo": "Acordó con el supervisor el turno fijo matutino"})
    check(r.status_code == 200, "RH la reactiva")
    pt = recargar(pt.codigo)
    check(pt.activa and not pt.motivo_cierre and pt.etapa == "Entrevista IA", "se reabre y la ruta sigue sola a Filtro Red Human")
    exc = pt.proceso_estado["prefiltro"]["excepcion"]
    check(exc["por"] == admin.nombre and "turno fijo" in exc["motivo"], "registra quién autorizó y por qué")
    check(pt.analisis[pconv.ENTIDAD]["respuestas"]["turnos"]["respuesta"] == "No"
          and pt.analisis["prefiltro_web"]["resultado"] == "no_cumple", "las respuestas y el resultado originales se CONSERVAN")
    nuevos = textos_tg(CHAT, antes)
    liga = f"{settings.app_url}/entrevista/{pt.entrevistas[0].token}" if pt.entrevistas else "¿sin entrevista?"
    check(len(pt.entrevistas) == 1 and len(nuevos) == 1 and liga in nuevos[0] and "continúa" in nuevos[0],
          "la liga de la entrevista sale SOLA por el canal conectado (Telegram), sin esperas")
    check(paso(seg(pt.codigo), "prefiltro")["estadoUnificado"] == "aprobada_excepcion", "Prefiltro «Continúa por decisión de RH»")
    check(db.query(Bitacora).filter(Bitacora.accion == "postulacion_reactivada", Bitacora.entidad_id == pt.codigo).count() == 1
          and any(h.get("evento") == "reactivada" for h in recargar(pt.codigo).historial), "queda en la bitácora y en el historial")

    # ------------------------------------------------------------------ 5. aislamiento
    print("\n--- 5. Aislamiento ---")
    otra_v.preguntas_filtro = [dict(q) for q in ajustes_demo.PREGUNTAS_AYUDANTE]
    db.commit()
    check(not pconv.aplica(recargar(otra_p.codigo)), "otra Cuenta cuya ruta no trae «Prefiltro por WhatsApp»: el bot no prefiltra")
    op = recargar(otra_p.codigo)
    op.proceso = {**op.proceso, "pasos": op.proceso["pasos"] + [{"id": "pw", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"}]}
    db.commit()
    check(pconv.aplica(recargar(otra_p.codigo)), "retro 2026-10-09: con «Prefiltro por WhatsApp» en la ruta aplica en CUALQUIER Cuenta")
    op = recargar(otra_p.codigo)
    op.prefiltro_completo = True
    db.commit()
    check(not pconv.aplica(recargar(otra_p.codigo)), "…pero una postulación que ya terminó su prefiltro anterior lo conserva")

    # ------------------------------------------------------------------ 6. reglas de la retro (unidad, autopercepción)
    print("\n--- 6. Retro: «8» sin unidad, autopercepciones ---")
    from types import SimpleNamespace

    vx = SimpleNamespace(preguntas_filtro=[
        {"pregunta": "¿Tienes al menos 2 años de experiencia en almacén?", "valida": "2 años de experiencia en almacén", "tipo": "si_no",
         "respuesta_esperada": "Sí", "descarta": True},
        {"pregunta": "¿Te consideras una persona organizada?", "valida": "Organizado", "tipo": "si_no", "respuesta_esperada": "Sí",
         "descarta": True}],
        preguntas_filtro_whatsapp=[{"pregunta": "Para confirmar: ¿cuánto tiempo de experiencia en almacén tienes?", "tipo": "numero",
                                    "reconfirma": "2 años de experiencia en almacén", "opciones": ["Menos de 1 año", "1 a 2 años", "Más de 2 años"],
                                    "opciones_validas": ["Más de 2 años"], "minimo": 2}],
        modalidad="Presencial", ubicacion="", ubicacion_municipio="", ubicacion_estado="")
    cs = pconv.criterios_de(vx)
    check(not any("organizad" in c["pregunta"].lower() for c in cs), "una autopercepción («¿Te consideras organizada?») nunca se pregunta")
    rc = next(c for c in cs if c.get("reconfirma"))
    check(pconv.numero_sin_unidad(rc, "8") == "8" and pconv.numero_sin_unidad(rc, "8 años") is None,
          "«8» sin unidad en una pregunta de tiempo → hay que aclarar «¿8 años o 8 meses?»")
    check(pconv.clasificar_dato(rc, "8 meses") == "no" and pconv.clasificar_dato(rc, "3 años") == "si", "con la unidad, el dato se evalúa")
    from app.services import ia as _ia

    check(_ia.es_autopercepcion("¿Eres una persona responsable?") and not _ia.es_autopercepcion("¿Cuentas con INE vigente?"),
          "detector de autopercepciones")
    check("compatible" not in _ia.sin_promesas("¡Gracias! Tu perfil es compatible. ¿Qué hacías en tu último trabajo?").lower(),
          "el agente nunca dice «Tu perfil es compatible» (filtro en código)")
    db.close()

print(f"\n{OK} comprobaciones OK")
