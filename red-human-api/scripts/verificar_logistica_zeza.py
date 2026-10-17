"""Prueba de aceptación «Logística Zeza» (2026-10-17, Cambios WhatsApp · entrevista operativa y agendamiento del avatar).
Base desechable; Meta SIMULADA (ningún mensaje sale). Las plantillas HSM de Meta NO se tocan.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_logistica_zeza.py

1. Entrevista por WhatsApp (operativa, ruta A: después sigue la entrevista humana que agenda el candidato, automática):
   acuse breve antes de cada pregunta, nombre en los cambios de bloque, el NOMBRE de la empresa en lugar de «ahí»; si trabaja
   actualmente ese es el trabajo 1; máximo 2 trabajos; una pregunta del candidato se responde y se repite la pregunta con
   contexto; «5» se aclara una vez con opciones; el bloque D solo pregunta «¿Cuándo podrías empezar?»; cierre «Gracias,
   [nombre]. En un momento te envío el siguiente paso.» y el motor manda la autoagenda.
2. Ruta B (después sigue la entrevista humana que agenda RH): frustración → «Una disculpa, [nombre]…», sin segundo trabajo,
   directo al bloque C; cierre «Revisaremos tu información y te avisaremos por aquí.»; el «Ok» posterior → «¡Gracias,
   [nombre]! Que tengas buen día.» una sola vez. «Benito Juárez» en la colonia se aclara una vez.
3. Ruta C (Entrevista con avatar): invitación con el nombre, sin liga; vago → una aclaración → vago → la liga «entra cuando
   puedas» (pipeline «Link enviado»); día y hora → «Listo, quedó …» con la liga en la misma conversación (pipeline «Entrevista
   agendada · …»). Sin recordatorios ni cupos.
"""

import asyncio
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_zeza_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "zeza.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-zeza"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion, procesar_prefiltro  # noqa: E402
from app.services import agenda_chat  # noqa: E402
from app.services import entrevista_whatsapp_fija as ewf  # noqa: E402
from app.services import entrevistas as sent  # noqa: E402
from app.services import preguntas_filtro as pfu  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIADOS = []


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


def textos_a(tel10: str, desde: int = 0) -> list:
    return [c.get("text", {}).get("body", "") for c in ENVIADOS[desde:] if c.get("type") == "text" and c.get("to") == "52" + tel10]


def plantillas_a(tel10: str) -> list:
    return [c.get("template", {}).get("name") for c in ENVIADOS if c.get("type") == "template" and c.get("to") == "52" + tel10]


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Zeza", nombre_comercial="Logística Zeza", estado="Activa", slug="logistica-zeza")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    def vacante(codigo, titulo, pasos):
        v = Vacante(codigo=codigo, titulo=titulo, estado="Publicada", cuenta_id=cuenta.id, slug=codigo.lower(),
                    sueldo="$11,000 MXN mensuales", ubicacion="Ciudad de México", ubicacion_estado="Ciudad de México",
                    requisitos="Disponibilidad para rolar turnos", modalidad="Presencial", enfoque_entrevista="operativo")
        db.add(v)
        db.flush()
        v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": pasos})
        pfu.sincronizar(v)
        db.commit()
        return v

    MASIVOS = sproc.ruta_base("masivos_sin_documentos")["pasos"]
    entrevista_op = next(x for x in MASIVOS if x["tipo"] == "entrevista_whatsapp")
    # Ruta A: después de la entrevista sigue una entrevista humana que AGENDA EL CANDIDATO (autoagenda, la dispara el motor)
    va = vacante("VAC-ZZ1", "Ayudante de almacén", [
        {"tipo": "solicitud_web"}, {"tipo": "prefiltro_whatsapp"}, {**entrevista_op},
        {"tipo": "entrevista_humana", "etapa": "Entrevista Humana", "tipo_agenda": "candidato"},
        {"tipo": "carta_contrato", "etapa": "Contratación"}])
    from app.models import Ubicacion
    from app.services import ubicaciones as subic

    cedis = Ubicacion(cuenta_id=cuenta.id, nombre="CEDIS Vallejo", direccion="Calz. Vallejo 1000, Azcapotzalco, CDMX",
                      ubicacion_estado="Ciudad de México", ubicacion_municipio="Azcapotzalco",
                      horarios_citas=[{"dias": [0, 1, 2, 3, 4, 5, 6], "horas": ["09:00", "12:00", "16:00"], "cupo": 10}])
    db.add(cedis)
    db.flush()
    subic.asignar(db, va, [cedis.id])
    db.commit()
    vb = vacante("VAC-ZZ2", "Montacarguista", MASIVOS)
    vc = vacante("VAC-ZZ3", "Supervisor de almacén", [
        {"tipo": "solicitud_web"}, {"tipo": "prefiltro_whatsapp"}, {"tipo": "entrevista_agente", "enfoque": "operativo"},
        {"tipo": "carta_contrato", "etapa": "Contratación"}])

    def postular(v, nombre, tel):
        c = _crear_candidato(db, cuenta.id, nombre, "Portal", False, telefono=tel)
        p = crear_postulacion(db, c, v, cuenta.id, "web", consentimiento=True)
        db.commit()
        return p

    def chat(p, texto):
        db.expire_all()
        p = db.get(Postulacion, p.id)
        r = asyncio.run(procesar_prefiltro(db, p, texto, "whatsapp"))
        return r.get("respuesta") or ""

    def pasar_prefiltro(p, colonia="Colonia Roma, Cuauhtémoc, CDMX"):
        r = chat(p, "Hola")
        for _ in range(8):
            db.expire_all()
            pp = db.get(Postulacion, p.id)
            if pp.prefiltro_completo:
                return r
            t = r.lower()
            r = chat(p, colonia if "colonia" in t else "30 minutos" if "tiempo" in t or "tardar" in t else "Sí, sin problema")
        return r

    # =================================================== 1. Ruta A: entrevista operativa por WhatsApp
    print("\n--- 1. Entrevista operativa por WhatsApp (ruta A: después la entrevista humana que agenda el candidato) ---")
    pa = postular(va, "Ana López", "5541000001")
    pasar_prefiltro(pa)
    db.expire_all()
    pa = db.get(Postulacion, pa.id)
    e = sent.entrevista_whatsapp_activa(pa)
    check(e is not None and ewf.aplica(e), "al aprobar el prefiltro empieza la entrevista fija por WhatsApp")
    preguntas = [s for s in textos_a("5541000001") if "Ahora te haré unas preguntas" in s]
    check(preguntas and preguntas[-1].endswith("¿Trabajas actualmente?"), f"transición + «¿Trabajas actualmente?» ({preguntas[-1:]})")
    r = chat(pa, "Sí")
    check(r == "Gracias, Ana. ¿En qué empresa trabajas?", f"trabaja actualmente → el trabajo 1 es el actual, acuse con nombre al cambiar de bloque: «{r}»")
    r = chat(pa, "En Bodegas del Valle")
    check(r == "Perfecto. ¿Qué puesto tienes en Bodegas del Valle?", f"acuse + nombre de la empresa (nunca «ahí»): «{r}»")
    r = chat(pa, "¿Para qué quieres saber eso?")
    check(r.startswith("Es para conocer tu experiencia") and r.endswith("Te preguntaba: ¿Qué puesto tienes en Bodegas del Valle?"),
          f"pregunta del candidato → responde y repite la pregunta con contexto: «{r}»")
    r = chat(pa, "Almacenista")
    check(r == "Entendido. ¿Qué haces en Bodegas del Valle?", f"«{r}»")
    r = chat(pa, "Surto pedidos y acomodo mercancía")
    check(r == "Gracias. ¿Cuánto tiempo llevas en Bodegas del Valle?", f"«{r}»")
    r = chat(pa, "5")
    check(r == "¿5 años o 5 meses?", f"ambigüedad («5») → aclara UNA vez con opciones: «{r}»")
    r = chat(pa, "años")
    check(r == "Perfecto. ¿Por qué quieres dejar Bodegas del Valle?", f"«{r}»")
    est = ewf._estado(db.get(Postulacion, pa.id).entrevistas[-1])
    check((est.get("trabajos") or [{}])[0].get("duracion") == "5 años", f"la aclaración completa el dato ({(est.get('trabajos') or [{}])[0].get('duracion')})")
    r = chat(pa, "Busco crecer y un horario fijo")
    check(r == "Entendido. ¿Tuviste otro trabajo antes de Bodegas del Valle?", f"«{r}»")
    r = chat(pa, "Sí")
    check(r == "Gracias. ¿Y antes de Bodegas del Valle, en qué empresa estuviste?", f"segundo trabajo con otras palabras y el nombre: «{r}»")
    r = chat(pa, "Oxxo")
    check(r == "Perfecto. ¿Qué cargo tenías en Oxxo?", f"«{r}»")
    for t in ("Cajero", "Cobraba y acomodaba", "1 año"):
        r = chat(pa, t)
    check(r == "Perfecto. ¿Qué te hizo dejar Oxxo?", f"«{r}»")
    r = chat(pa, "Me cambié de casa y me quedaba lejos")
    check(r == "Entendido. ¿En cuántos trabajos has estado en los últimos 3 años?" and "otro trabajo" not in r,
          f"máximo 2 trabajos: no pregunta por un tercero: «{r}»")
    r = chat(pa, "2")
    check(r == "Gracias, Ana. ¿Cuándo podrías empezar?" and "te confirmo" not in r and "El sueldo es" not in r,
          f"bloque D: solo «¿Cuándo podrías empezar?» (sin confirmar puesto, sueldo ni ubicación): «{r}»")
    desde = len(ENVIADOS)
    r = chat(pa, "El lunes")
    check(r == "Gracias, Ana. En un momento te envío el siguiente paso.", f"cierre: sigue una actividad automática → «{r}»")
    db.expire_all()
    pa = db.get(Postulacion, pa.id)
    salidos = textos_a("5541000001", desde)
    check(len(salidos) >= 2 and salidos[0] == "Gracias, Ana. En un momento te envío el siguiente paso.",
          f"…y el siguiente paso (la autoagenda de la entrevista) sale solo ({[s[:70] for s in salidos]})")
    r = chat(pa, "Ok")
    check("¿Qué día puedes venir?" in r, f"el «Ok» lo atiende la actividad que sigue (la autoagenda espera el día): «{r[:70]}»")
    check(not any(x in s for s in textos_a("5541000001") for x in ("Con esto terminamos", "te contactará para")),
          "ya no sale el cierre anterior («Con esto terminamos… te contactará…»)")

    # =================================================== 2. Ruta B: frustración y revisión humana
    print("\n--- 2. Ruta B (sigue la entrevista humana): frustración, revisión humana, «Benito Juárez» ---")
    pb = postular(vb, "Beto Ruiz", "5541000002")
    r = chat(pb, "Hola")
    for _ in range(6):
        if "colonia" in r.lower():
            break
        r = chat(pb, "Sí, sin problema")
    check("colonia" in r.lower(), f"llega a la colonia: «{r}»")
    r = chat(pb, "Benito Juárez")
    check(r.endswith("¿Te refieres a Benito Juárez en la Ciudad de México o en Nuevo León?"),
          f"municipio ambiguo → aclara UNA vez con opciones (primero el estado de la vacante): «{r}»")
    r = chat(pb, "En la Ciudad de México")
    db.expire_all()
    zona = (db.get(Postulacion, pb.id).analisis.get("prefiltro_datos") or {}).get("zona", "")
    check("Benito Juárez" in zona and "Ciudad de México" in zona, f"la zona queda completa ({zona!r})")
    pasar_prefiltro(pb)
    r = chat(pb, "No")  # ¿Trabajas actualmente? → no
    r = chat(pb, "3 meses")
    check(r.split(" ")[0] in ("Gracias,", "Perfecto,", "Entendido,") and ", Beto." in r and r.endswith("¿Cuál fue tu último trabajo?"),
          f"no trabaja → acuse con nombre (cambio de bloque) y su último trabajo: «{r}»")
    r = chat(pb, "Ferretería López")
    r = chat(pb, "Ya te dije todo, son muchas preguntas")
    check(r.startswith("Una disculpa, Beto.") and "otro trabajo" not in r and "¿Cuándo podrías empezar?" in r,
          f"frustración → disculpa, sin segundo trabajo, directo a lo que sigue: «{r}»")
    r = chat(pb, "Mañana mismo")
    check(r == "Gracias, Beto. Revisaremos tu información y te avisaremos por aquí.", f"cierre: sigue una revisión humana → «{r}»")
    r = chat(pb, "Ok")
    check(r == "¡Gracias, Beto! Que tengas buen día.", f"«Ok» después del cierre → despedida simple, sin repetir el cierre: «{r}»")
    r = chat(pb, "ok gracias")
    check(r == "", "un segundo «ok» ya no recibe respuesta")

    # =================================================== 3. Ruta C: agendamiento de la entrevista con avatar
    print("\n--- 3. Entrevista con avatar: agendamiento por chat (sin recordatorios ni cupos) ---")
    pc = postular(vc, "Carla Díaz", "5541000003")
    pasar_prefiltro(pc)
    db.expire_all()
    pc = db.get(Postulacion, pc.id)
    salidos = textos_a("5541000003")
    invit = ("¡Pasaste a la siguiente etapa, Carla! Es una entrevista por videollamada de unos 10 minutos. ¿Cuándo te queda bien? "
             "Si quieres, puede ser ahora mismo.")
    check(invit in salidos and not any("/entrevista/" in s for s in salidos), f"invitación exacta con el nombre y SIN liga ({salidos[-1:]})")
    r = chat(pc, "luego veo")
    check(r.startswith("¿Qué día y a qué hora te queda bien?") and "/entrevista/" not in r, f"vago → pregunta día y hora UNA vez: «{r}»")
    r = chat(pc, "no sé, cuando pueda")
    db.expire_all()
    pc = db.get(Postulacion, pc.id)
    check(r.startswith("Aquí está tu link; entra cuando puedas: ") and "/entrevista/" in r, f"sigue vago → la liga: «{r}»")
    paso_av = next(x for x in sproc.estado_pasos(pc) if x["tipo"] == "entrevista_agente")
    check(paso_av["estadoUnificadoTexto"] == "Link enviado", f"pipeline «Link enviado» ({paso_av['estadoUnificadoTexto']})")

    pd = postular(vc, "Diego Mora", "5541000004")
    pasar_prefiltro(pd)
    r = chat(pd, "el lunes a las 10 am")
    db.expire_all()
    pd = db.get(Postulacion, pd.id)
    check(r.startswith("Listo, quedó ") and "a las 10:00 a.m." in r and "Aquí está tu link" in r and "/entrevista/" in r
          and "recordatorio" not in r, f"día y hora → «Listo, quedó …» con la liga en la misma conversación, sin recordatorio: «{r}»")
    paso_av = next(x for x in sproc.estado_pasos(pd) if x["tipo"] == "entrevista_agente")
    check(paso_av["estadoUnificadoTexto"].startswith("Entrevista agendada · lunes ") and paso_av["estadoUnificadoTexto"].endswith("10:00 a.m."),
          f"pipeline «{paso_av['estadoUnificadoTexto']}»")
    pe = postular(vc, "Elena Paz", "5541000005")
    pasar_prefiltro(pe)
    r = chat(pe, "ahorita")
    check(r.startswith("Aquí está tu link para entrar ahora mismo: ") and "/entrevista/" in r, f"«ahora» → la liga de inmediato: «{r}»")
    from app import main as _main

    jobs = [j.id for j in getattr(_main, "scheduler").get_jobs()] if hasattr(_main, "scheduler") else []
    check("recordatorio_videollamadas" not in jobs, "no hay job de recordatorios de la videollamada")
    from app.config import settings as _cfg

    existentes = {str(v) for k, v in vars(_cfg).items() if k.startswith("meta_plantilla") and isinstance(v, str) and v}
    enviadas = {c.get("template", {}).get("name") for c in ENVIADOS if c.get("type") == "template"}
    check(enviadas <= existentes, f"solo se usan las plantillas HSM ya configuradas, sin tocarlas ({sorted(enviadas)})")
    db.close()

print(f"\nTodo OK ({OK} comprobaciones)")
