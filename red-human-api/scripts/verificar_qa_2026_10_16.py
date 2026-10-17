"""Correcciones de QA y nueva lógica del agente (2026-10-16). Base desechable; Meta SIMULADA.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_qa_2026_10_16.py

Bloque 1 (ruta Masivos sin documentos iniciales):
  1. Oferta: el «Sí» del candidato en el chat registra «Oferta aceptada» y la ruta sigue de inmediato a «Contrato y firma»
     (el webhook elige la postulación con la oferta aunque otra espere respuesta; nunca manda liga de entrevista).
  2. Contrato sin correo: el firmante se identifica con el teléfono (identificador técnico); ya no hay 409 por correo.
  3. Mensajes de la oferta: «Mensaje 1» y «Mensaje 2» exactos, sueldo sin «Desde».
Bloque 3 (agente):
  6. Prefiltro: la web solo registra los requisitos (sin sueldo, puesto ni ubicación); la expectativa ya no existe; con
     sueldo publicado «El sueldo es… ¿Te interesa?» — un «no» se confirma y, confirmado, descarta; «Mostrar sueldo» apagado
     → no se pregunta; un «No» de la web se retoma con una pregunta abierta.
  7. Entrevista fija por WhatsApp: máximo 2 trabajos (el segundo con otras palabras), «¿En cuántos trabajos…?», confirmación
     final en UN mensaje → «No interesado» con motivo (nunca descarta), fecha de inicio, «Ficha de candidato».
  Notas de voz: se transcriben y el turno sigue como texto.
"""

import asyncio
import os
import secrets
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
_dir = _temporal.directorio("rh_qa1016_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "qa1016.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-qa1016"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Entrevista, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers import webhooks  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion, procesar_prefiltro  # noqa: E402
from app.services import entrevista_whatsapp_fija as ewf  # noqa: E402
from app.services import oferta as soferta  # noqa: E402
from app.services import prefiltro_conversacional as pconv  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import solicitud_web as ssw  # noqa: E402
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


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="QA1016", nombre_comercial="Logística QA", estado="Activa", slug="qa1016")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ================= Bloque 1 · oferta, contrato y mensajes =================
    print("\n--- 1 y 3. Oferta: mensajes exactos y «Sí» en el chat ---")
    check(soferta.sueldo_exacto("Desde $12,000 MXN mensuales") == "$12,000 MXN mensuales" and soferta.sueldo_exacto("12000") == "$12,000 MXN mensuales"
          and soferta.sueldo_exacto("A convenir") == "", "el sueldo de la oferta va con la cifra exacta (sin «Desde»)")
    check(soferta.clasificar_respuesta("Sí, acepto") is True and soferta.clasificar_respuesta("claro que sí") is True
          and soferta.clasificar_respuesta("de acuerdo 👍") is True and soferta.clasificar_respuesta("No, gracias") is False
          and soferta.clasificar_respuesta("ok") is None, "«Sí» y afirmaciones similares aceptan; «ok» solo no es aceptación")
    vo = Vacante(codigo="VAC-QA1", titulo="Ayudante general", estado="Publicada", cuenta_id=cuenta.id, slug="ayudante-qa1",
                 sueldo="Desde $12,000 MXN mensuales", ubicacion="Zapopan, Jalisco", requisitos="Disponibilidad para rolar turnos")
    vp = Vacante(codigo="VAC-QA2", titulo="Almacenista", estado="Publicada", cuenta_id=cuenta.id, slug="almacenista-qa2",
                 sueldo="$10,000 MXN mensuales", requisitos="Secundaria terminada · Disponibilidad para rolar turnos")
    db.add_all([vo, vp])
    db.flush()
    vo.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [{"tipo": "solicitud_sin_cv"},
                                                                         {"tipo": "oferta", "etapa": "Contratación"},
                                                                         {"tipo": "carta_contrato", "etapa": "Contratación"}]})
    vp.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": sproc.ruta_base("masivos_sin_documentos")["pasos"]})
    db.commit()
    tel = "5530400001"
    c = _crear_candidato(db, cuenta.id, "Oscar Oferta", "Portal", False, telefono=tel)
    po = crear_postulacion(db, c, vo, cuenta.id, "web", consentimiento=True)
    db.commit()
    r = client.patch(f"/candidatos/{po.codigo}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True,
                                                              "comentario": "Prueba de la oferta por WhatsApp"})
    assert r.status_code == 200, r.text
    otra = crear_postulacion(db, c, vp, cuenta.id, "web", consentimiento=True)  # otra postulación ESPERANDO respuesta (prefiltro)
    db.commit()
    db.refresh(otra)
    check(otra.espera_respuesta, "la otra postulación de la persona también espera respuesta (prefiltro en curso)")
    datos = client.get(f"/candidatos/{po.codigo}/oferta").json()["datos"]
    desde = len(ENVIADOS)
    r = client.post(f"/candidatos/{po.codigo}/oferta", json={"datos": {**datos, "fecha_ingreso": "2026-11-03",
                                                                       "tipo_contratacion": "Contrato de planta", "jefe_directo": "Laura Jefa"}})
    liga = r.json()["liga"]
    check(r.status_code == 200 and r.json()["texto"] == (f"¡Buenas noticias, Oscar! Fuiste seleccionado para el puesto. Revisa aquí tu "
                                                         f"sueldo, horario y fecha de inicio para confirmar tu contratación: {liga} ¡Gracias!"),
          "Mensaje 1 exacto")
    m2 = textos_a(tel, desde)
    check(m2 and m2[-1] == ("Hola Oscar, ¡felicidades! Esta es tu propuesta formal en Logística QA: Puesto: Ayudante general "
                            "Sueldo: $12,000 MXN mensuales Ubicación: Zapopan, Jalisco. Fecha de ingreso: 3 de noviembre de 2026 "
                            "Tipo de puesto: Contrato de planta Jefe directo: Laura Jefa. ¿Estás de acuerdo? Responde Sí para "
                            "confirmar tu lugar y enviarte tu contrato para firma."), f"Mensaje 2 exacto, sin «Desde» → {m2[-1:]}")
    db.expire_all()
    po = db.get(Postulacion, po.id)
    check(soferta.esperando_respuesta(po) and po.espera_respuesta, "la oferta enviada espera la respuesta del candidato")
    desde = len(ENVIADOS)
    res = asyncio.run(webhooks.procesar_entrante(db, {"telefono": "52" + tel, "texto": "Sí", "nombre": "Oscar", "wa_id": "wamid.qa-si",
                                                      "tipo": "text", "id_seleccionado": "", "numero_receptor": "", "canal": "whatsapp"}))
    db.expire_all()
    po = db.get(Postulacion, po.id)
    check(res.get("postulacion") == po.codigo, f"el «Sí» va a la postulación con la oferta, no a la otra ({res.get('postulacion')})")
    check(soferta.estado(po).get("respuesta") == "acepta" and soferta.estado(po).get("canal") == "whatsapp", "«Oferta aceptada» por WhatsApp")
    pasos = {x["tipo"]: x for x in sproc.estado_pasos(po)}
    check(pasos["oferta"]["estado"] == "completada" and pasos["oferta"]["cumpleRegla"], "la actividad «Oferta laboral» queda cumplida")
    check(pasos["carta_contrato"]["estado"] in ("pendiente", "en_curso") and po.etapa == "Contratación" and po.activa,
          f"la ruta sigue de inmediato a «Contrato y firma» ({pasos['carta_contrato']['estado']})")
    nuevos = textos_a(tel, desde)
    check(nuevos and "contrato" in nuevos[-1].lower() and not any("entrevista" in t.lower() or "otra postulación" in t.lower() for t in nuevos),
          f"el candidato recibe el siguiente paso (contrato) y nunca la liga de entrevista → {nuevos}")

    print("\n--- 2. Contrato sin correo ---")
    from app.routers import firmas

    fuente = (RAIZ / "app/routers/firmas.py").read_text(encoding="utf-8")
    check("El candidato no tiene correo" not in fuente, "ya no existe el candado «El candidato no tiene correo» al firmar")
    e = po.expediente
    check(e is not None and not (po.correo or (po.candidato.correo if po.candidato else "")), "el candidato no tiene correo")
    ident = firmas.correo_firmante_candidato(e)
    check(ident.endswith("@" + firmas.DOMINIO_FIRMANTE_SIN_CORREO) and po.codigo.lower() in ident,
          f"sin correo, el firmante se identifica con un identificador técnico del expediente ({ident})")

    # ================= Bloque 3 · prefiltro =================
    print("\n--- 6. Prefiltro: web solo requisitos; el chat no pregunta el sueldo ---")
    web = ssw.criterios_web(vp)
    check(not any(q.get("dato") == "sueldo" or q.get("expectativa") or "sueldo" in (q.get("pregunta") or "").lower()
                  or "terminal" in (q.get("pregunta") or "").lower() or (q.get("pregunta") or "").startswith("El puesto es") for q in web),
          f"la solicitud web no pregunta sueldo, puesto ni ubicación → {[q.get('pregunta') for q in web]}")
    crit = pconv.criterios_de(vp)
    # 2026-10-17 (Ficha de Nueva vacante): el prefiltro de WhatsApp ya no pregunta «El sueldo es… ¿Te interesa?»
    check(not any(x.get("dato") in ("sueldo", "interes") for x in crit), "con sueldo publicado el chat ya NO pregunta el sueldo")
    check(not any(x.get("expectativa") for x in crit), "la expectativa de sueldo ya no existe")
    vp.mostrar_sueldo = False
    db.flush()
    check(not any(x.get("dato") == "sueldo" for x in pconv.criterios_de(vp)), "«Mostrar sueldo» apagado → el chat no pregunta el sueldo")
    vp.mostrar_sueldo = True
    db.commit()

    def nuevo(nombre, telefono):
        cc = _crear_candidato(db, cuenta.id, nombre, "Portal", False, telefono=telefono)
        pp = crear_postulacion(db, cc, vp, cuenta.id, "web", consentimiento=True)
        db.commit()
        return pp

    def chat(pp, t):
        db.expire_all()
        pp = db.get(Postulacion, pp.id)
        return asyncio.run(procesar_prefiltro(db, pp, t, "whatsapp")).get("respuesta") or ""

    def hasta_colonia(pp):
        r = chat(pp, "Hola")
        for _ in range(8):
            if "colonia" in r.lower():
                return r
            check("¿Te interesa?" not in r, f"el chat nunca pregunta «… ¿Te interesa?» → «{r}»")
            r = chat(pp, "Terminé la secundaria" if "estudios" in r.lower() else "Sí puedo")
        return r

    pc = nuevo("Carla Continúa", "5530400003")
    r = hasta_colonia(pc)
    db.expire_all()
    pc = db.get(Postulacion, pc.id)
    check(pc.activa and "colonia" in r.lower(), f"de los requisitos pasa directo a la colonia (sin sueldo) → «{r}»")

    print("\n--- 6. Un «No» de la web se retoma con una pregunta abierta ---")
    conf = {"tipo": "excluyente", "respuesta": "No", "pregunta": "¿Puedes rolar turnos?"}
    t = pconv.texto_confirmacion({"dato": "requisito", "pregunta": "¿Puedes rolar turnos?"}, conf)
    check(t == "En tu solicitud indicaste que no puedes rolar turnos. Cuéntame, ¿cuál es tu situación con eso?", f"pregunta abierta → «{t}»")

    print("\n--- Notas de voz ---")
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"contacts": [{"profile": {"name": "Carla"}}], "metadata": {"display_phone_number": "1"},
                                                 "messages": [{"from": "525530400003", "id": "wamid.voz", "type": "audio",
                                                               "audio": {"id": "media-voz", "mime_type": "audio/ogg; codecs=opus"}}]}}]}]}
    m = whatsapp.parsear_webhook(payload)
    check(m and m["tipo"] == "audio" and (m.get("media") or {}).get("id") == "media-voz", "el webhook trae el medio de la nota de voz")
    from app.services import ia

    # 2026-10-17 (QA): la descarga REAL (`whatsapp.descargar_media`) contra un Graph simulado — antes la prueba sustituía la
    # descarga completa y escondía que el formato de audio se rechazaba («formato no admitido»)
    from app.config import settings as _cfg

    llamadas_graph = []

    class _Resp:
        def __init__(self, status, datos=None, contenido=b"", cabeceras=None):
            self.status_code, self._datos, self.content, self.headers = status, datos or {}, contenido, cabeceras or {}
            self.text = ""

        def json(self):
            return self._datos

    class _GraphFalso:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, **k):
            llamadas_graph.append((url, (headers or {}).get("Authorization", "")))
            if url.endswith("/media-voz"):
                return _Resp(200, {"url": "https://lookaside.fbsbx.com/voz.ogg", "mime_type": "audio/ogg; codecs=opus"})
            return _Resp(200, contenido=b"OggS-audio-real", cabeceras={"content-type": "audio/ogg"})

        async def post(self, url, **k):
            return _Resp(200, {"messages": [{"id": "wamid.salida"}]})

    previo = (_cfg.whatsapp_provider, _cfg.meta_whatsapp_token, whatsapp.httpx.AsyncClient)
    recibido = {}
    _transcribir_real = ia.transcribir_audio

    def _transcribe(contenido, mime=""):
        recibido.update({"contenido": contenido, "mime": mime})
        return "Vivo en la colonia Centro de Zapopan"

    _cfg.whatsapp_provider, _cfg.meta_whatsapp_token = "meta", "token-de-prueba"
    whatsapp.httpx.AsyncClient = _GraphFalso
    ia.transcribir_audio = _transcribe
    try:
        d = asyncio.run(whatsapp.descargar_media("media-voz", "audio/ogg; codecs=opus"))
        check(not d.get("ok") and "formato no admitido" in d.get("detalle", ""), "sin `aceptar_audio` la descarga sigue siendo solo PDF/imagen")
        m["texto"] = ""
        asyncio.run(webhooks.procesar_entrante(db, m))
    finally:
        _cfg.whatsapp_provider, _cfg.meta_whatsapp_token, whatsapp.httpx.AsyncClient = previo
    check(any(u.endswith("/media-voz") and a == "Bearer token-de-prueba" for u, a in llamadas_graph),
          "se pide a Graph la URL del audio con el media_id y el token de acceso")
    check(recibido.get("contenido") == b"OggS-audio-real" and recibido.get("mime") == "audio/ogg",
          f"el audio REAL descargado llega a la transcripción ({recibido})")
    db.expire_all()
    pc = db.get(Postulacion, pc.id)
    ultimo_user = next((x.texto for x in reversed(pc.mensajes) if x.rol == "user"), "")
    check(ultimo_user == "Vivo en la colonia Centro de Zapopan", f"la nota de voz se transcribe y entra como respuesta → «{ultimo_user}»")
    check("colonia centro" in str(((pc.analisis or {}).get("prefiltro_datos") or {}).get("zona", "")).lower(),
          "la respuesta transcrita se registró (colonia)")

    # Whisper: el modelo por defecto y el archivo que se manda a OpenAI (cliente simulado, sin red)
    from types import SimpleNamespace

    pedido = {}

    def _crear(**kw):
        pedido.update(kw)
        return SimpleNamespace(text="  Tengo   la secundaria terminada ")

    cliente_previo = ia._client
    ia._client = lambda: SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=_crear)))
    try:
        texto_w = _transcribir_real(b"OggS-audio-real", "audio/ogg; codecs=opus")
    finally:
        ia._client = cliente_previo
    check(_cfg.openai_transcribe_model == "whisper-1" and pedido.get("model") == "whisper-1",
          f"se transcribe con Whisper ({pedido.get('model')})")
    check(pedido.get("file") == ("nota.ogg", b"OggS-audio-real") and pedido.get("language") == "es" and texto_w == "Tengo la secundaria terminada",
          f"Whisper recibe el audio como .ogg en español y regresa el texto limpio («{texto_w}»)")

    # si la transcripción falla → se le pide que escriba su respuesta
    ia.transcribir_audio = lambda contenido, mime="": ""

    async def _descarga_ok(media_id, mime="", aceptar_audio=False):
        return {"ok": True, "contenido": b"OggS", "mime": "audio/ogg"}

    webhooks.descargar_media = _descarga_ok
    m2 = {**m, "wa_id": "wamid.voz2", "texto": ""}
    r = asyncio.run(webhooks.procesar_entrante(db, m2))
    check(r.get("accion") == "audio_no_soportado" and "¿Me escribes tu respuesta?" in (r.get("respuesta") or ""),
          f"transcripción fallida → se le pide escribir su respuesta ({r.get('respuesta')!r})")

    # ================= Bloque 3 · entrevista fija =================
    print("\n--- 7. Entrevista fija por WhatsApp ---")

    def entrevista(pp):
        e_ = Entrevista(codigo=f"E-QA{pp.id}", token=secrets.token_hex(8), candidato_id=pp.candidato_id, postulacion_id=pp.id,
                        tipo="whatsapp", estado="en_curso", guion={})
        db.add(e_)
        db.commit()
        return e_

    pe = nuevo("Ernesto Entrevista", "5530400004")
    e1 = entrevista(pe)
    preguntas = [ewf.primera(e1)]
    for t in ("No", "3 meses", "Bodegas del Norte", "Ayudante", "Cargaba cajas", "2 años", "Terminó mi contrato", "Sí",
              "Tienda La Esquina", "Cajero", "Cobraba", "1 año", "Me cambié de casa", "2"):
        msg, fin = ewf.turno(e1, t)
        preguntas.append(msg)
    import re as _re

    def sin_acuse(m):
        # 2026-10-17: cada pregunta va precedida de un acuse breve («Gracias.», «Perfecto, Ernesto.»…)
        return _re.sub(r"^(Gracias|Perfecto|Entendido)(, [^.]+)?\. ", "", m)

    preguntas = [sin_acuse(x) for x in preguntas]
    check(preguntas[:3] == ["¿Trabajas actualmente?", "¿Cuánto tiempo llevas sin trabajar?", "¿Cuál fue tu último trabajo?"],
          f"A) situación actual → B) historial ({preguntas[:3]})")
    segunda = preguntas[8:13]
    check(segunda == ["¿Y antes de Bodegas del Norte, en qué empresa estuviste?", "¿Qué cargo tenías en Tienda La Esquina?",
                      "¿A qué te dedicabas en Tienda La Esquina?", "¿Cuánto duraste en Tienda La Esquina?",
                      "¿Qué te hizo dejar Tienda La Esquina?"] and not set(segunda) & set(preguntas[2:7]),
          f"segunda vuelta con OTRAS palabras y el nombre de la empresa (sin repetir las del primer trabajo) → {segunda}")
    check(preguntas[13] == ewf.PREGUNTA_CUANTOS, f"máximo 2 trabajos y después «¿En cuántos trabajos…?» → «{preguntas[13]}»")
    # 2026-10-17: el bloque D ya no confirma puesto, sueldo ni ubicación — solo la fecha de inicio
    check(preguntas[14] == "¿Cuándo podrías empezar?", f"D) solo «¿Cuándo podrías empezar?» → «{preguntas[14]}»")
    msg, fin = ewf.turno(e1, "El lunes")
    check(fin and msg in ("Gracias, Ernesto. Revisaremos tu información y te avisaremos por aquí.",
                          "Gracias, Ernesto. En un momento te envío el siguiente paso."), f"cierre según la ruta → «{msg}»")
    f = ewf.ficha(e1)
    check(f["A"]["trabajaActualmente"] == "No" and f["A"]["tiempoSinTrabajar"] == "3 meses" and len(f["B"]["trabajos"]) == 2
          and f["B"]["trabajosUltimos3Anios"] == "2" and f["D"]["fechaInicio"] == "El lunes",
          "Ficha de candidato: A, B (2 trabajos + cuántos) y D (fecha de inicio)")

    # conversación ANTERIOR con la confirmación final pendiente: sigue funcionando (motivo, «No interesado», nunca descarta)
    pf = nuevo("Fer Sueldo", "5530400005")
    e2 = entrevista(pf)
    ewf.primera(e2)
    msg, _ = ewf.turno(e2, "Nunca he trabajado")
    check(sin_acuse(msg) == "¿Cuándo podrías empezar?", f"sin experiencia → directo a la fecha de inicio → «{msg}»")
    est = ewf._estado(e2)
    est["pendiente"] = "conf_final"
    ewf._guardar(e2, est)
    db.commit()
    msg, fin = ewf.turno(e2, "No")
    check(msg == ewf.PREGUNTA_MOTIVO_NO and not fin, f"legado: «No» a la confirmación anterior → el motivo → «{msg}»")
    msg, fin = ewf.turno(e2, "Pagan muy poco")
    check(fin and ewf.ficha(e2)["D"]["motivoNoInteres"] == "Sueldo" and msg == ewf.cierre_no_interesado(pf),
          "legado: motivo clasificado (Sueldo) y cierre sin prometer entrevista")
    ev = ewf.evaluar(e2, pf, vp)
    check(ev["recomendacion"] == "no_recomendable" and ev["recomendacion_texto"] == "No interesado", "legado: «No interesado» (nunca descarta)")
    db.refresh(pf)
    check(pf.activa, "«No interesado» NO descarta")

    db.close()

print(f"\n✅ {OK} comprobaciones OK")
