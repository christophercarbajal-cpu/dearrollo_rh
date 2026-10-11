"""Citas presenciales (2026-10-10, Cambio 2). Base desechable, SIN red.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_citas_presenciales.py

Cubre: entrevista humana, médica, técnica y psicometría física sin cita quedan «Pendiente de agendar» (no se inicia ni se
avisa); la cita guarda «Hasta», la liga de mapa autogenerada y hasta 5 adjuntos (imágenes/PDF ≤ 10 MB) que salen tal cual
con el aviso fijo al candidato; reprogramar avisa a candidato y evaluador.
"""

import itertools
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

_dir = tempfile.mkdtemp(prefix="rh_citas_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "citas.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-citas"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import app.services.correo as correo_srv  # noqa: E402
import app.services.notificaciones as notif  # noqa: E402
import app.services.whatsapp as wa  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Evaluacion, NotificacionEnviada, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
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


ENVIOS, DOCS, CORREOS = [], [], []
_ids = itertools.count(1)


async def _wa(telefono, texto, *a, **k):
    ENVIOS.append(("whatsapp", telefono, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "wa_id": f"wamid.C{next(_ids)}"}


async def _wa_boton(telefono, texto, boton, url, *a, **k):
    return await _wa(telefono, f"{texto} {url}")


async def _doc(telefono, contenido, filename, caption="", mime="application/pdf", cuenta_id=None):
    DOCS.append((telefono, filename, mime, len(contenido)))
    return {"enviado": True, "proveedor": "meta", "detalle": "ok"}


async def _correo(destino, asunto, html, adjuntos=None, *a, **k):
    CORREOS.append((destino, asunto, html, [x["filename"] for x in adjuntos or []]))
    ENVIOS.append(("correo", destino, asunto))
    return {"enviado": True, "proveedor": "resend", "detalle": "ok"}


wa.enviar_mensaje = notif.enviar_mensaje = _wa
wa.enviar_con_boton = _wa_boton
wa.enviar_documento = _doc
correo_srv.enviar_correo = notif.enviar_correo = _correo

PDF = b"%PDF-1.4\n" + b"0" * 900
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 900
DOCX = bytes.fromhex("504b0304") + b"0" * 900
CITA = {"fecha": "2026-12-15", "hora": "09:00", "hasta": "11:00", "modalidad": "Presencial",
        "direccion": "Av. Insurgentes Sur 1000, Ciudad de México"}

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = admin.telefono or "5599990000"
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    cuenta = Cuenta(nombre="Citas SA", nombre_comercial="Citas", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    obtener(db).modo_prueba = False
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id, v.cliente_id, v.proceso, v.responsable_id, v.titulo = cuenta.id, None, {}, admin.id, "Almacenista"
    db.commit()
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    PASOS = [
        {"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana", "responsable": {"tipo": "usuario", "usuario_id": admin.id}},
    ]
    assert client.put(f"/procesos/vacantes/{v.codigo}", json={"pasos": PASOS}).status_code == 200
    tel = iter(range(5586000000, 5586999999))

    def nueva(nombre):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)), "correo": f"{nombre.split()[0].lower()}@citas.mx",
                                             "vacante": v.codigo, "consentimiento": True, "fuente": "RH"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def agregar(codigo, datos, adjuntos=()):
        files = [("adjuntos_cita", (n, b, m)) for n, b, m in adjuntos]
        return client.post(f"/procesos/postulaciones/{codigo}/actividades", data={"datos": json.dumps(datos)}, files=files or None)

    def seg(codigo):
        return client.get(f"/procesos/postulaciones/{codigo}").json()

    def paso(s, pid):
        return next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == pid)

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    def evs(codigo, paso_id=None):
        q = db.query(Evaluacion).filter(Evaluacion.postulacion_id == post(codigo).id)
        return (q.filter(Evaluacion.paso_id == paso_id) if paso_id else q).all()

    EV = {"tipo": "interno", "usuario_id": admin.id}

    # ================= 1. Pendiente de agendar =================
    print("\n--- 1. Sin «Programar cita» → Pendiente de agendar (no se inicia ni se avisa) ---")
    P = nueva("Ana Agenda")
    tel_p = post(P).telefono
    ENVIOS.clear()
    x = paso(seg(P), "eh")
    check(x["estadoUnificado"] == "pendiente_agendar" and x["estadoUnificadoTexto"] == "Pendiente de agendar",
          "entrevista humana de la ruta sin cita → «Pendiente de agendar»")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/eh/iniciar", json={})
    check(r.json()["iniciada"] is False and r.json()["faltan"] == ["cita"] and r.json()["pendienteAgendar"] and not evs(P, "eh") and not ENVIOS,
          "«Iniciar» sin cita: pide SOLO la cita, no crea la evaluación y no avisa a nadie")
    casos = [("entrevista_humana", {"evaluador": EV}), ("medica", {"evaluador": {"tipo": "externo", "nombre": "Dra. Salud", "correo": "dra@clinica.mx"},
                                                                    "examen": "General"}),
             ("tecnica", {"forma": "asignada", "evaluador": EV}), ("psicometria_fisica", {})]
    for tipo, config in casos:
        ENVIOS.clear()
        r = agregar(P, {"tipo": tipo, "config": {**config, "iniciar_al_guardar": True}})
        pid = r.json()["paso"]["id"]
        x = paso(seg(P), pid)
        check(r.status_code == 201 and r.json().get("pendienteAgendar") and x["estadoUnificado"] == "pendiente_agendar"
              and not evs(P, pid) and not ENVIOS, f"{tipo} guardada sin cita → «Pendiente de agendar», sin evaluación ni avisos")
    check(paso(seg(P), pid)["accion"]["texto"] == "Programar cita", "su acción principal es «Programar cita»")
    r = agregar(P, {"tipo": "tecnica", "config": {"forma": "liga_otro_sistema", "liga_externa_candidato": "https://caso.mx/1", "iniciar_al_guardar": True}})
    check(r.status_code == 201 and r.json()["iniciada"], "una técnica EN LÍNEA (liga de otro sistema) no requiere cita")

    # ================= 2. Cita completa: Hasta, mapa, adjuntos =================
    print("\n--- 2. Cita con «Hasta», liga de mapa y adjuntos ---")
    r = agregar(P, {"tipo": "entrevista_humana", "config": {"evaluador": EV, "cita": {**CITA, "hasta": "08:30"}, "iniciar_al_guardar": True}})
    check(r.status_code == 400 and "Hasta" in r.json()["detail"], "«Hasta» antes de la hora de inicio → 400")
    seis = [(f"a{i}.pdf", PDF, "application/pdf") for i in range(6)]
    r = agregar(P, {"tipo": "entrevista_humana", "config": {"evaluador": EV, "cita": CITA, "iniciar_al_guardar": True}}, seis)
    check(r.status_code == 400 and "5 adjuntos" in r.json()["detail"], "más de 5 adjuntos → 400")
    r = agregar(P, {"tipo": "entrevista_humana", "config": {"evaluador": EV, "cita": CITA, "iniciar_al_guardar": True}},
                [("guia.docx", DOCX, "application/octet-stream")])
    check(r.status_code == 415, "un Word no se acepta como adjunto de cita (solo imágenes o PDF)")
    grande = [("grande.pdf", b"%PDF-1.4\n" + b"0" * (10 * 1024 * 1024 + 10), "application/pdf")]
    r = agregar(P, {"tipo": "entrevista_humana", "config": {"evaluador": EV, "cita": CITA, "iniciar_al_guardar": True}}, grande)
    check(r.status_code == 413, "un adjunto de más de 10 MB → 413")
    ENVIOS.clear(); DOCS.clear(); CORREOS.clear()
    r = agregar(P, {"tipo": "tecnica", "config": {"forma": "asignada", "evaluador": EV, "cita": CITA,
                                                  "instrucciones": "Trae zapato de seguridad.", "iniciar_al_guardar": True}},
                [("croquis.png", PNG, "image/png"), ("indicaciones.pdf", PDF, "application/pdf")])
    pid_t = r.json()["paso"]["id"]
    e = evs(P, pid_t)
    check(r.status_code == 201 and r.json()["iniciada"] and len(e) == 1, "con cita se inicia en el mismo paso")
    e = e[0]
    check(e.cita_hasta is not None and e.cita_hasta > e.cita_fecha_hora, "«Hasta» guardado como fin del rango")
    check(e.cita_mapa == "https://www.google.com/maps/search/?api=1&query=Av.+Insurgentes+Sur+1000%2C+Ciudad+de+M%C3%A9xico",
          "liga de mapa AUTOGENERADA con la dirección")
    check(len(e.cita_adjuntos) == 2 and all(os.path.isfile(a["archivo"]) for a in e.cita_adjuntos), "2 adjuntos guardados con la cita")
    texto = next((t for c, d, t in ENVIOS if c == "whatsapp" and d == tel_p), "")
    check("de 9:00 a.m. a 11:00 a.m." in texto and "hora de Ciudad de México" in texto and "Av. Insurgentes Sur 1000" in texto
          and e.cita_mapa in texto and "Te enviamos 2 archivos" in texto and "zapato de seguridad" in texto,
          "aviso FIJO al candidato: fecha, rango de hora, lugar, liga de mapa, indicaciones y adjuntos")
    check(not any(w in texto.lower() for w in ("felicidades", "compatible", "seguro pasas")), "el aviso no felicita ni promete avance")
    check(sorted(d[1] for d in DOCS if d[0] == tel_p) == ["croquis.png", "indicaciones.pdf"], "los adjuntos salen tal cual por WhatsApp")
    correo_cand = next((c for c in CORREOS if c[0].startswith("ana@")), None)
    check(correo_cand is not None and sorted(correo_cand[3]) == ["croquis.png", "indicaciones.pdf"] and "Cómo llegar" in correo_cand[2],
          "…y como adjuntos del correo (con botón «Cómo llegar»)")
    check(any(c[0] == admin.correo for c in CORREOS), "el evaluador también recibe su aviso")
    vista = client.get(f"/evaluaciones/{e.codigo}").json()["evaluacion"]["cita"]
    check(vista["hasta"] and vista["mapa"] == e.cita_mapa and len(vista["adjuntos"]) == 2 and "archivo" not in vista["adjuntos"][0],
          "la ficha ve rango, mapa y adjuntos (sin rutas de disco)")
    aid = vista["adjuntos"][0]["id"]
    check(client.get(f"/evaluaciones/{e.codigo}/cita/adjuntos/{aid}").status_code == 200, "el adjunto se puede abrir")

    # ================= 3. Pendiente → Programar cita (completar) con adjuntos previos =================
    print("\n--- 3. «Programar cita» sobre una actividad pendiente ---")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/eh/cita/adjuntos", files=[("archivos", ("mapa.pdf", PDF, "application/pdf"))])
    check(r.status_code == 200 and len(r.json()["adjuntos"]) == 1, "adjuntos antes de iniciar quedan en la actividad")
    ENVIOS.clear(); DOCS.clear()
    r = client.post(f"/procesos/postulaciones/{P}/pasos/eh/iniciar", json={"cita": CITA})
    e_eh = evs(P, "eh")
    check(r.json()["iniciada"] and len(e_eh) == 1 and len(e_eh[0].cita_adjuntos) == 1 and any(d[1] == "mapa.pdf" for d in DOCS),
          "con la cita se inicia y el adjunto previo sale con el aviso")

    # ================= 4. Reprogramar avisa a ambos =================
    print("\n--- 4. Reprogramar ---")
    db.query(NotificacionEnviada).delete()
    db.commit()
    ENVIOS.clear()
    r = client.patch(f"/evaluaciones/{e.codigo}", json={"cita": {**CITA, "hora": "12:00", "hasta": "13:30"}})
    check(r.status_code == 200, "PATCH con otra hora")
    destinatarios = {n.destinatario_tipo for n in db.query(NotificacionEnviada).filter(NotificacionEnviada.evento == "evaluacion_reprogramada").all()}
    check({"candidato", "entrevistador"} <= destinatarios, "la reprogramación avisa al candidato y al evaluador")
    texto = next((t for c, d, t in ENVIOS if c == "whatsapp" and d == tel_p), "")
    check("cambiamos" in texto.lower() and "de 12:00 p.m. a 1:30 p.m." in texto, "el candidato recibe los nuevos datos con su rango de hora")
    r = client.delete(f"/evaluaciones/{e.codigo}/cita/adjuntos/{aid}")
    db.expire_all()
    check(r.status_code == 200 and len(db.get(Evaluacion, e.id).cita_adjuntos) == 1, "un adjunto se puede quitar (deja de salir en avisos futuros)")

    # ================= 5. Psicometría física con cita =================
    print("\n--- 5. Psicometría física ---")
    ENVIOS.clear()
    r = agregar(P, {"tipo": "psicometria_fisica", "config": {"cita": CITA, "iniciar_al_guardar": True}})
    e_pf = evs(P, r.json()["paso"]["id"])
    check(r.json()["iniciada"] and e_pf and e_pf[0].forma == "registro_directo" and e_pf[0].cita_fecha_hora is not None
          and any(d == tel_p and "psicometría" in t for c, d, t in ENVIOS), "con cita se agenda (captura manual) y se avisa al candidato")
    check(citas.requiere_cita({"tipo": "psicometrica", "modalidad": "fisica"}) and not citas.requiere_cita({"tipo": "psicometrica"}),
          "la psicometría DIGITAL nunca pide cita")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — citas presenciales")
