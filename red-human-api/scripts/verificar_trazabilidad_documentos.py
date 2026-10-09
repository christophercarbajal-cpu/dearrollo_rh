"""Verificación BLOQUES 3 y 5 (2026-09-20).
B3 — Trazabilidad de documentos: cada documento requerido registra cuándo/por qué canal se SOLICITÓ
(primera solicitud + historial con recordatorios) y cuándo/por dónde se RECIBIÓ (WhatsApp, liga pública,
RH, físico), con estado simple Pendiente / Recibido para la pestaña «CV y documentos».
B5 — El candidato nunca desaparece: subir/recibir documentos (WhatsApp, liga, RH), solicitar, recordar,
marcar recibido y guardar condiciones NUNCA cambian la etapa; solo «Enviar a Onboarding» (mover_etapa) lo mueve.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_trazabilidad_documentos.py
"""

import io
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_b3t_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "b3t.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-b3t"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Documento, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import agenda, whatsapp  # noqa: E402
from app.services import notificaciones as notif  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIADOS = []
PDF_MIN = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"


import itertools as _it
_WAMID = _it.count(1)  # wamid único por mensaje simulado (el webhook deduplica reintentos)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def webhook_meta(client, telefono: str, texto: str = "", tipo: str = "text", media: dict = None, wamid: str = "wamid.1"):
    m = {"from": telefono, "id": f"{wamid}.{next(_WAMID)}", "type": tipo}
    if tipo == "text":
        m["text"] = {"body": texto}
    else:
        m[tipo] = {**(media or {}), **({"caption": texto} if texto else {})}
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"display_phone_number": "5215500000000"},
        "contacts": [{"profile": {"name": "Karla Demo"}, "wa_id": telefono}],
        "messages": [m],
    }}]}]}
    return client.post("/webhooks/whatsapp", json=payload)


async def _fake_enviar_mensaje(telefono, texto, *a, **k):
    ENVIADOS.append({"telefono": telefono, "texto": texto})
    return {"enviado": True, "proveedor": "meta", "detalle": 200, "wa_id": f"wamid.out.{len(ENVIADOS)}"}


async def _fake_enviar_lista(telefono, *a, **k):
    return {"enviado": True, "proveedor": "meta"}


async def _fake_descargar_media(media_id):
    return {"ok": True, "contenido": PDF_MIN, "mime": "application/pdf", "extension": "pdf", "filename": f"whatsapp_{media_id}.pdf", "tamano": len(PDF_MIN)}


import app.routers.candidatos as rc  # noqa: E402
import app.routers.contratacion as rcont  # noqa: E402
import app.routers.webhooks as rw  # noqa: E402

rw.enviar_mensaje = _fake_enviar_mensaje
rw.enviar_lista_interactiva = _fake_enviar_lista
rw.descargar_media = _fake_descargar_media
rc.enviar_mensaje = _fake_enviar_mensaje
rcont.enviar_mensaje = _fake_enviar_mensaje
agenda.enviar_mensaje = _fake_enviar_mensaje
notif.enviar_mensaje = _fake_enviar_mensaje
whatsapp.enviar_mensaje = _fake_enviar_mensaje

TEL = "5215577778888"

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta Demo", nombre_comercial="Demo RH", estado="Activa", whatsapp_comunicacion="5500000000", whatsapp_exclusivo=True)
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
    cfg = obtener(db)
    cfg.modo_prueba = True  # Modo Prueba total: el archivo queda «recibido» sin OCR
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- 1. Expediente nuevo: documentos sin trazabilidad ---")
    vac = client.get("/vacantes").json()[0]
    r = client.post("/candidatos", json={"nombre": "Karla Demo", "telefono": "5577778888", "correo": "karla@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    EXP = r.json()["expedienteId"]
    check(r.status_code == 200 and EXP, "candidato en Contratación con expediente")
    e = client.get(f"/contratacion/expedientes/{EXP}").json()
    check(all(d["estadoSimple"] == "Pendiente" and d["solicitadoEn"] is None and d["recibidoEn"] is None and d["solicitudes"] == [] for d in e["documentos"]), "todos Pendiente, sin solicitud ni recepción")

    print("\n--- 2. Solicitar documentos → fecha/hora + canal en cada pendiente ---")
    ENVIADOS.clear()
    r = client.post(f"/candidatos/{P}/solicitar-documentos", json={"notificar": {"candidato_whatsapp": True, "candidato_correo": False}})
    check(r.status_code == 200 and r.json()["candidato"]["etapa"] == "Contratación", "solicitar documentos → 200 y la etapa sigue en Contratación (B5)")
    e = client.get(f"/contratacion/expedientes/{EXP}").json()
    check(all(d["solicitadoEn"] and d["solicitadoCanal"] == "whatsapp" for d in e["documentos"]), "cada documento registra solicitud con fecha/hora y canal WhatsApp")
    check(all(len(d["solicitudes"]) == 1 and d["solicitudes"][0]["tipo"] == "solicitud" and d["solicitudes"][0]["por"] == admin.nombre for d in e["documentos"]), "historial: 1 solicitud, con quién la mandó")
    primera = e["documentos"][0]["solicitadoEn"]

    print("\n--- 3. Recordatorio → se acumula en el historial, la primera solicitud no cambia ---")
    r = client.post(f"/contratacion/expedientes/{EXP}/recordatorio", json={"notificar": {"candidato_whatsapp": True, "candidato_correo": False}})
    check(r.status_code == 200, "recordatorio del expediente → 200")
    e = r.json()["expediente"]
    d0 = e["documentos"][0]
    check(d0["solicitadoEn"] == primera and len(d0["solicitudes"]) == 2 and d0["solicitudes"][1]["tipo"] == "recordatorio", "primera solicitud intacta + recordatorio en el historial")
    r = client.post(f"/candidatos/{P}/recordatorio-documentos", json={"notificar": {"candidato_whatsapp": True, "candidato_correo": False}})
    check(r.status_code == 200 and r.json()["candidato"]["etapa"] == "Contratación", "recordatorio desde la ficha → etapa intacta (B5)")
    e = client.get(f"/contratacion/expedientes/{EXP}").json()
    check(len(e["documentos"][0]["solicitudes"]) == 3, "ambos recordatorios (expediente y ficha) quedan en el historial")

    print("\n--- 4. Recepción por WhatsApp → Recibido + fecha/hora + canal ---")
    p = db.query(Postulacion).filter_by(codigo=P).one()
    p.candidato.wa_id = TEL
    p.candidato.postulacion_conversacion_id = p.id
    db.commit()
    r = webhook_meta(client, TEL, "mi INE", tipo="document", media={"id": "media-1", "mime_type": "application/pdf", "filename": "ine.pdf"}, wamid="wamid.doc1")
    check(r.status_code == 200 and r.json().get("documento") == "Identificación oficial", "INE por WhatsApp adjuntada")
    e = client.get(f"/contratacion/expedientes/{EXP}").json()
    ine = next(d for d in e["documentos"] if d["nombre"] == "Identificación oficial")
    check(ine["estadoSimple"] == "Recibido" and ine["recibidoEn"] and ine["recibidoCanal"] == "whatsapp", f"INE: Recibido, con fecha/hora y canal whatsapp (estado interno {ine['estado']})")
    check(ine["solicitadoEn"] == primera and ine["solicitadoCanal"] == "whatsapp", "la INE conserva su trazabilidad de solicitud")
    db.expire_all()
    check(db.query(Postulacion).filter_by(codigo=P).one().etapa == "Contratación", "B5: recibir por WhatsApp NO cambia la etapa")
    # un recordatorio posterior ya no toca los recibidos
    client.post(f"/contratacion/expedientes/{EXP}/recordatorio", json={"notificar": {"candidato_whatsapp": True, "candidato_correo": False}})
    e = client.get(f"/contratacion/expedientes/{EXP}").json()
    ine = next(d for d in e["documentos"] if d["nombre"] == "Identificación oficial")
    curp = next(d for d in e["documentos"] if d["nombre"] == "CURP")
    check(len(ine["solicitudes"]) == 3 and len(curp["solicitudes"]) == 4, "los recordatorios solo se acumulan en los documentos aún pendientes")

    print("\n--- 5. Recepción por liga pública y por RH ---")
    token = db.query(Postulacion).filter_by(codigo=P).one().expediente.token
    r = client.post(f"/expedientes/publica/{token}/documentos", data={"tipo": "CURP"}, files={"archivo": ("curp.pdf", io.BytesIO(PDF_MIN), "application/pdf")})
    check(r.status_code == 200, f"CURP por la liga pública ({r.status_code})")
    r = client.post(f"/contratacion/expedientes/{EXP}/documentos", data={"tipo": "Comprobante de domicilio"}, files={"archivo": ("luz.pdf", io.BytesIO(PDF_MIN), "application/pdf")})
    check(r.status_code == 200, f"Comprobante por RH desde el tablero ({r.status_code})")
    e = client.get(f"/contratacion/expedientes/{EXP}").json()
    curp = next(d for d in e["documentos"] if d["nombre"] == "CURP")
    comp = next(d for d in e["documentos"] if d["nombre"] == "Comprobante de domicilio")
    check(curp["estadoSimple"] == "Recibido" and curp["recibidoCanal"] == "liga", "CURP: recibido por «liga»")
    check(comp["estadoSimple"] == "Recibido" and comp["recibidoCanal"] == "rh", "Comprobante: recibido por «rh»")
    db.expire_all()
    check(db.query(Postulacion).filter_by(codigo=P).one().etapa == "Contratación", "B5: liga pública y RH tampoco cambian la etapa")

    print("\n--- 6. Marcar recibido en físico / rechazar ---")
    pend = next(d for d in e["documentos"] if d["estadoSimple"] == "Pendiente")
    r = client.post(f"/contratacion/expedientes/{EXP}/documentos/estado", json={"tipo": pend["nombre"], "estado": "recibido", "recibido_fisico": True, "notas": "Entregó copia en oficina"})
    check(r.status_code == 200, f"«{pend['nombre']}» marcado recibido en físico")
    d = next(x for x in r.json()["documentos"] if x["nombre"] == pend["nombre"])
    check(d["estadoSimple"] == "Recibido" and d["recibidoEn"] and d["recibidoCanal"] == "fisico", "recepción física con fecha/hora y canal «fisico»")
    r = client.post(f"/contratacion/expedientes/{EXP}/documentos/estado", json={"tipo": "CURP", "estado": "rechazado", "notas": "Ilegible"})
    d = next(x for x in r.json()["documentos"] if x["nombre"] == "CURP")
    check(d["estadoSimple"] == "Rechazado" and d["recibidoEn"] is None, "rechazar limpia la recepción (vuelve a quedar por recibir)")
    db.expire_all()
    check(db.query(Postulacion).filter_by(codigo=P).one().etapa == "Contratación", "B5: la revisión manual tampoco cambia la etapa")

    print("\n--- 7. B5: solo «Enviar a Onboarding» mueve; el candidato sigue visible ---")
    kan = client.get("/candidatos").json()
    check(any(x["id"] == P and x["etapa"] == "Contratación" for x in kan), "el candidato sigue en el Kanban en Contratación")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={"puesto": "Cajera", "sueldo": "$10,000", "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-10-01"})
    check(r.status_code == 200 and r.json()["etapa"] == "Contratación", "guardar condiciones no mueve la etapa")
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Onboarding", "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["etapa"] == "Onboarding", "«Enviar a Onboarding» (PATCH /etapa) es la única forma de pasar a Onboarding")
    kan = client.get("/candidatos").json()
    check(any(x["id"] == P and x["etapa"] == "Onboarding" for x in kan), "…y sigue visible en el Kanban, ahora en Onboarding")
    # Audit estático: nadie más escribe la etapa desde el flujo de documentos
    import inspect as _insp

    fuente_cont = _insp.getsource(rcont)
    fuente_web = _insp.getsource(rw._recibir_documento_whatsapp)
    check(fuente_cont.count(".etapa = ") == 1 and "def cancelar" in fuente_cont.split(".etapa = ")[0][-3000:], "contratacion.py solo escribe la etapa en «cancelar» (acción explícita de RH)")
    check(".etapa = " not in fuente_web and ".etapa = " not in _insp.getsource(rcont._registrar_documento) and ".etapa = " not in _insp.getsource(rcont._sincronizar_estado), "ni el webhook ni _registrar_documento ni _sincronizar_estado tocan la etapa")
    db.close()

print(f"\n🎉 Bloques 3 y 5 (trazabilidad de documentos / el candidato nunca desaparece) verificados: {OK} comprobaciones OK.")
