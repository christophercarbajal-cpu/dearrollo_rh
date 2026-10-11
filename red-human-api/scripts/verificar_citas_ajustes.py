"""Citas: ajustes y mejoras tras la prueba del 2026-10-10. Base desechable, SIN red.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_citas_ajustes.py

Cubre: «📝 Indicaciones: …» y «👤 Pregunta por: …» en el aviso; editar una cita = UN solo aviso («Tu cita cambió») sin
reenviar adjuntos; nunca una cita en el pasado; «Hasta» estrictamente posterior; fotos como `image` y PDF como
`document` (Meta); programar otra cita para la misma actividad cancela la anterior y avisa al candidato; precarga con la
última cita de la vacante (sin fecha ni hora) y reutilización de sus adjuntos; liga de mapa con la ubicación de la vacante.
"""

import itertools
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_dir = tempfile.mkdtemp(prefix="rh_citas_aj_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "citas_aj.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-citas-aj"
os.environ["SEMBRAR_DEMO"] = "true"

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.services.correo as correo_srv  # noqa: E402
import app.services.notificaciones as notif  # noqa: E402
import app.services.whatsapp as wa  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Evaluacion, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import citas  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


ENVIOS, DOCS = [], []
_ids = itertools.count(1)


async def _wa(telefono, texto, *a, **k):
    ENVIOS.append(("whatsapp", telefono, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "wa_id": f"wamid.A{next(_ids)}"}


async def _wa_boton(telefono, texto, boton, url, *a, **k):
    return await _wa(telefono, f"{texto} {url}")


async def _doc(telefono, contenido, filename, caption="", mime="application/pdf", cuenta_id=None):
    DOCS.append((telefono, filename, mime))
    return {"enviado": True, "proveedor": "meta", "detalle": "ok"}


async def _correo(destino, asunto, html, adjuntos=None, *a, **k):
    ENVIOS.append(("correo", destino, asunto))
    return {"enviado": True, "proveedor": "resend", "detalle": "ok"}


enviar_documento_real = wa.enviar_documento
wa.enviar_mensaje = notif.enviar_mensaje = _wa
wa.enviar_con_boton = _wa_boton
wa.enviar_documento = _doc
correo_srv.enviar_correo = notif.enviar_correo = _correo

PDF = b"%PDF-1.4\n" + b"0" * 900
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 900
CITA = {"fecha": "2026-12-15", "hora": "09:00", "modalidad": "Presencial", "direccion": "Av. Vallarta 5000, Col. Centro"}

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = admin.telefono or "5599990000"
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    cuenta = Cuenta(nombre="Citas Aj SA", nombre_comercial="Citas Aj", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    obtener(db).modo_prueba = False
    vs = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).limit(2).all()
    v, v2 = vs[0], vs[1]
    for x in vs:
        x.cuenta_id, x.cliente_id, x.proceso, x.responsable_id = cuenta.id, None, {}, admin.id
    v.titulo, v.ubicacion_estado, v.ubicacion_municipio, v.ubicacion = "Almacenista", "Jalisco", "Zapopan", "Zapopan, Jalisco"
    db.commit()
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    PASOS = [
        {"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana", "responsable": {"tipo": "usuario", "usuario_id": admin.id}},
    ]
    for x in vs:
        assert client.put(f"/procesos/vacantes/{x.codigo}", json={"pasos": PASOS}).status_code == 200
    tel = iter(range(5587000000, 5587999999))

    def nueva(nombre, vac=None):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)), "correo": f"{nombre.split()[0].lower()}@citas.mx",
                                             "vacante": (vac or v).codigo, "consentimiento": True, "fuente": "RH"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def agregar(codigo, datos, adjuntos=()):
        files = [("adjuntos_cita", (n, b, m)) for n, b, m in adjuntos]
        return client.post(f"/procesos/postulaciones/{codigo}/actividades", data={"datos": json.dumps(datos)}, files=files or None)

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    def evs(codigo, paso_id=None):
        q = db.query(Evaluacion).filter(Evaluacion.postulacion_id == post(codigo).id)
        return (q.filter(Evaluacion.paso_id == paso_id) if paso_id else q).order_by(Evaluacion.id).all()

    def al(t, canal="whatsapp"):
        return [x for c, d, x in ENVIOS if c == canal and d == t]

    EV = {"tipo": "interno", "usuario_id": admin.id}

    # ================= 1 y 5. Indicaciones y «Pregunta por» =================
    print("\n--- 1 y 5. «📝 Indicaciones» y «👤 Pregunta por» ---")
    P = nueva("Bruno Citas")
    tel_p = post(P).telefono
    ENVIOS.clear(); DOCS.clear()
    r = agregar(P, {"tipo": "entrevista_humana", "config": {"evaluador": EV, "iniciar_al_guardar": True,
                                                           "cita": {**CITA, "instrucciones": "Pregunta en caseta por Recursos Humanos."}}},
                [("croquis.png", PNG, "image/png"), ("guia.pdf", PDF, "application/pdf")])
    check(r.status_code == 201 and r.json()["iniciada"], "cita con instrucciones y adjuntos: se inicia")
    pid = r.json()["paso"]["id"]
    e = evs(P, pid)[0]
    check(e.instrucciones == "Pregunta en caseta por Recursos Humanos.", "las instrucciones de la cita se guardan en la evaluación")
    texto = al(tel_p)[0]
    check("📝 Indicaciones: Pregunta en caseta por Recursos Humanos." in texto, "WhatsApp: «📝 Indicaciones: [texto]»")
    check(f"👤 Pregunta por: {admin.nombre}" in texto, "WhatsApp: «👤 Pregunta por: [entrevistador]» en el mensaje principal")
    check(len([d for d in DOCS if d[0] == tel_p]) == 2 and "Te enviamos 2 archivos" in texto, "el primer aviso lleva los 2 adjuntos")

    # ================= 11. Liga de mapa con la ubicación de la vacante =================
    print("\n--- 11. Geocoding: dirección + ubicación de la vacante ---")
    check(e.cita_direccion == "Av. Vallarta 5000, Col. Centro", "la dirección que escribió RH no cambia")
    check(e.cita_mapa.endswith("query=Av.+Vallarta+5000%2C+Col.+Centro%2C+Zapopan%2C+Jalisco"), "la liga de mapa concatena «Zapopan, Jalisco»")
    check(citas.direccion_para_mapa("Calle 5, Zapopan", "Zapopan, Jalisco") == "Calle 5, Zapopan, Jalisco"
          and citas.direccion_para_mapa("Calle 5, zapopan, JALISCO", "Zapopan, Jalisco") == "Calle 5, zapopan, JALISCO",
          "no repite lo que la dirección ya menciona (sin importar mayúsculas)")

    # ================= 2. Editar la cita: UN solo aviso y sin adjuntos =================
    print("\n--- 2. Editar la cita → un solo aviso «Tu cita cambió», sin adjuntos ---")
    ENVIOS.clear(); DOCS.clear()
    r = client.patch(f"/evaluaciones/{e.codigo}", json={"cita": {**CITA, "hora": "12:00", "hasta": "13:00"}})
    check(r.status_code == 200, "PATCH con otra hora")
    msgs = al(tel_p)
    check(len(msgs) == 1 and "tu cita cambió" in msgs[0].lower() and "de 12:00 p.m. a 1:00 p.m." in msgs[0],
          "el candidato recibe UN solo WhatsApp: «Tu cita cambió» + los datos nuevos")
    check(not [d for d in DOCS if d[0] == tel_p] and "Te enviamos" not in msgs[0], "en la reprogramación NO se reenvían los adjuntos")
    ENVIOS.clear(); DOCS.clear()
    r = client.post(f"/evaluaciones/{e.codigo}/reprogramar", json={"cita": {**CITA, "hora": "16:00"}})
    check(r.status_code == 200 and len(al(tel_p)) == 1 and not [d for d in DOCS if d[0] == tel_p], "«Reprogramar» también: un aviso, sin adjuntos")

    # ================= 3 y 4. Fecha en el pasado y «Hasta» =================
    print("\n--- 3 y 4. Validaciones de fecha y rango ---")
    r = agregar(P, {"tipo": "entrevista_humana", "config": {"evaluador": EV, "iniciar_al_guardar": True, "cita": {**CITA, "fecha": "2024-01-10"}}})
    check(r.status_code == 400 and "ya pasaron" in r.json()["detail"], "no se guarda una cita en el pasado (Agregar actividad)")
    r = client.patch(f"/evaluaciones/{e.codigo}", json={"cita": {**CITA, "fecha": "2024-01-10"}})
    check(r.status_code == 400 and "ya pasaron" in r.json()["detail"], "ni al editarla")
    r = client.post(f"/evaluaciones/{e.codigo}/reprogramar", json={"cita": {**CITA, "fecha": "2024-01-10"}})
    check(r.status_code == 400, "ni al reprogramarla")
    r = client.patch(f"/evaluaciones/{e.codigo}", json={"cita": {**CITA, "hora": "10:00", "hasta": "10:00"}})
    check(r.status_code == 400 and "Hasta" in r.json()["detail"], "«Hasta» igual a la hora → 400 (debe ser estrictamente posterior)")
    vencida = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(days=2)
    ev_db = db.get(Evaluacion, e.id)
    ev_db.cita_fecha_hora, ev_db.cita_hasta = vencida, None
    db.commit()
    from app import fechas

    local = fechas.local(vencida)
    r = client.patch(f"/evaluaciones/{e.codigo}", json={"cita": {**CITA, "fecha": local.strftime("%Y-%m-%d"), "hora": local.strftime("%H:%M"),
                                                                   "direccion": "Av. Vallarta 5010"}})
    check(r.status_code == 200, "editar otros datos de una cita ya vencida (misma fecha y hora) no se bloquea")

    # ================= 6. Fotos como imagen, PDF como documento =================
    print("\n--- 6. Meta: imagen vs documento ---")
    enviados = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"id": "MEDIA1"}

    class _Cli:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    async def _meta_post(payload):
        enviados.append(payload)
        return {"enviado": True, "proveedor": "meta", "detalle": "ok"}

    orig = (settings.whatsapp_provider, settings.meta_whatsapp_token, settings.meta_phone_number_id, wa.httpx.AsyncClient, wa._meta_post)
    settings.whatsapp_provider, settings.meta_whatsapp_token, settings.meta_phone_number_id = "meta", "x", "123"
    wa.httpx.AsyncClient, wa._meta_post = _Cli, _meta_post
    try:
        r1 = asyncio.run(enviar_documento_real("5511112222", PNG, "croquis.png", mime="image/png"))
        r2 = asyncio.run(enviar_documento_real("5511112222", PDF, "guia.pdf", mime="application/pdf"))
        r3 = asyncio.run(enviar_documento_real("5511112222", b"RIFF0000WEBP", "foto.webp", mime="image/webp"))
    finally:
        settings.whatsapp_provider, settings.meta_whatsapp_token, settings.meta_phone_number_id, wa.httpx.AsyncClient, wa._meta_post = orig
    check(enviados[0]["type"] == "image" and enviados[0]["image"]["id"] == "MEDIA1" and r1["formato"] == "imagen",
          "una foto PNG/JPG sale como tipo «image» (vista previa)")
    check(enviados[1]["type"] == "document" and enviados[1]["document"]["filename"] == "guia.pdf", "un PDF sale como «document»")
    check(enviados[2]["type"] == "document", "WEBP (Meta no lo acepta como imagen) sale como documento")

    # ================= 7. Programar otra cita cancela la anterior =================
    print("\n--- 7. Cancelación de la cita anterior de la misma actividad ---")
    Q = nueva("Carla Doble")
    tel_q = post(Q).telefono
    r = client.post(f"/procesos/postulaciones/{Q}/pasos/eh/iniciar", json={"cita": CITA})
    check(r.json()["iniciada"], "primera cita de la entrevista humana de la ruta")
    vieja = evs(Q, "eh")[0]
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/postulaciones/{Q}", json={"tipo": "entrevista_humana", "forma": "asignada", "evaluador": EV,
                                                             "cita": {**CITA, "fecha": "2026-12-16"}})
    check(r.status_code == 201, "«Agregar evaluación» con una cita nueva para la misma entrevista")
    db.expire_all()
    vieja = db.get(Evaluacion, vieja.id)
    nuevas = [x for x in evs(Q, "eh") if x.id != vieja.id]
    check(vieja.estado == "cancelada" and "Reemplazada" in vieja.motivo_estado, "la cita anterior se cancela sola")
    check(len(nuevas) == 1 and nuevas[0].estado == "pendiente", "la nueva queda ligada a la MISMA actividad")
    msgs = al(tel_q)
    check(len(msgs) == 2 and "cancelamos tu cita" in msgs[0].lower() and "quedó programada" in msgs[1],
          "el candidato recibe la cancelación y luego los datos de la nueva cita")
    r = client.post(f"/evaluaciones/postulaciones/{Q}", json={"tipo": "entrevista_humana", "forma": "asignada", "evaluador": EV,
                                                             "paso_id": "eh", "cita": {**CITA, "fecha": "2026-12-17"}})
    db.expire_all()
    vivas = [x for x in evs(Q, "eh") if x.estado != "cancelada"]
    check(r.status_code == 201 and len(vivas) == 1, "con paso explícito también: nunca dos citas vivas para la misma actividad")

    # ================= 8. Precarga con la última cita de la vacante =================
    print("\n--- 8. Precarga por vacante ---")
    S = nueva("Saúl Precarga")
    u = client.get(f"/evaluaciones/postulaciones/{S}/ultima-cita?tipo=entrevista_humana").json()
    c = u["cita"]
    check(c is not None and c["modalidad"] == "Presencial" and c["direccion"].startswith("Av. Vallarta")
          and c["evaluador"]["tipo"] == "interno" and c["evaluador"]["usuario_id"] == admin.id,
          "trae evaluador, modalidad y dirección de la última cita de la vacante")
    check("fecha" not in c and "hora" not in c, "nunca precarga fecha ni hora")
    check(u["ubicacionVacante"] == "Zapopan, Jalisco", "trae la ubicación de la vacante (para la liga de mapa)")
    con_adj = next(x for x in db.query(Evaluacion).filter(Evaluacion.vacante_id == v.id).all() if x.cita_adjuntos)
    ids = [a["id"] for a in con_adj.cita_adjuntos][:1]
    ENVIOS.clear(); DOCS.clear()
    tel_s = post(S).telefono
    r = client.post(f"/procesos/postulaciones/{S}/pasos/eh/iniciar",
                    json={"cita": {**CITA, "instrucciones": "Trae INE.", "adjuntos_previos": {"evaluacion": con_adj.codigo, "ids": ids}}})
    e_s = evs(S, "eh")[0]
    check(r.json()["iniciada"] and len(e_s.cita_adjuntos) == 1 and e_s.cita_adjuntos[0]["archivo"] == con_adj.cita_adjuntos[0]["archivo"]
          and e_s.cita_adjuntos[0]["id"] != ids[0], "los adjuntos de la última cita se reutilizan (copia con id nuevo)")
    check(len([d for d in DOCS if d[0] == tel_s]) == 1 and "📝 Indicaciones: Trae INE." in al(tel_s)[0], "y salen con el primer aviso")
    O = nueva("Otto Ajeno", v2)
    r = client.post(f"/procesos/postulaciones/{O}/pasos/eh/iniciar",
                    json={"cita": {**CITA, "adjuntos_previos": {"evaluacion": con_adj.codigo, "ids": ids}}})
    check(r.json()["iniciada"] and not evs(O, "eh")[0].cita_adjuntos, "adjuntos de OTRA vacante nunca se copian")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — citas: ajustes y mejoras")
