"""Regresión de Dropbox Sign + Psicométricas.mx + bug «Expediente no encontrado» (2026-09-29).

    .venv/Scripts/python.exe scripts/verificar_firmas_psicometricas.py

Base desechable. Los proveedores se SIMULAN (sin red): se verifican los parámetros exactos que se les mandan, la
verificación HMAC de Dropbox Sign, la idempotencia de los webhooks y la degradación sin llaves.
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_dir = tempfile.mkdtemp(prefix="rh_firmas_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "firmas.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY",
          "DROPBOX_SIGN_API_KEY", "DROPBOX_SIGN_CLIENT_ID", "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "PSICOMETRICAS_USUARIO",
          "PSICOMETRICAS_WEBHOOK_SECRET", "PSICOMETRICAS_URL_CANDIDATO"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-firmas"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Documento, Evaluacion, EventoEvaluacion, Expediente, FirmaDocumento, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import dropbox_sign as dsign  # noqa: E402
from app.services import psicometricas as psi  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
PDF = b"%PDF-1.4\n" + b"%" * 900 + b"\n%%EOF\n"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def evento(tipo, sr_id, firmas=None, relacionado="", llave=None):
    t = "1727600000"
    h = hmac.new((llave or settings.dropbox_sign_api_key).encode(), f"{t}{tipo}".encode(), hashlib.sha256).hexdigest()
    return {"event": {"event_time": t, "event_type": tipo, "event_hash": h, "event_metadata": {"related_signature_id": relacionado}},
            "signature_request": {"signature_request_id": sr_id, "signatures": firmas or []}}


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    A = Cuenta(nombre="Principal", nombre_comercial="Principal", razon_social="Principal SA", estado="Activa")
    B = Cuenta(nombre="Carga masiva", nombre_comercial="Agencia", razon_social="Agencia SA", estado="Activa")
    otra = Cuenta(nombre="Ajena", nombre_comercial="Ajena", estado="Activa")
    db.add_all([A, B, otra])
    db.flush()
    for uc in db.query(UsuarioCuenta).filter(UsuarioCuenta.usuario_id == admin.id).all():
        db.delete(uc)
    db.add_all([UsuarioCuenta(usuario_id=admin.id, cuenta_id=A.id), UsuarioCuenta(usuario_id=admin.id, cuenta_id=B.id)])
    admin.cuenta_predeterminada_id = A.id
    for v in db.query(Vacante).all():
        v.cuenta_id = B.id
    obtener(db).modo_prueba = False
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    H = {"X-Cuenta-Id": str(B.id)}

    print("\n--- 1. Bug «Expediente no encontrado» (ligas sin cabecera de Cuenta) ---")
    vac = client.get("/vacantes", headers=H).json()[0]
    r = client.post("/candidatos", headers=H, json={"nombre": "Masiva Uno", "telefono": "5511223344", "correo": "masiva@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    EXP = client.patch(f"/candidatos/{P}/etapa", headers=H, json={"etapa": "Contratación", "manual": True}).json()["expedienteId"]
    client.patch(f"/candidatos/{P}/condiciones-contratacion", headers=H, json={"puesto": "Cajero", "sueldo": "$10,000", "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-11-02"})
    check(client.get(f"/contratacion/expedientes/{EXP}/carta-intencion").status_code == 404, "reproducción: sin cabecera (como un <iframe>) caía en la Cuenta predeterminada → 404")
    check(client.get(f"/contratacion/expedientes/{EXP}/carta-intencion?cuenta_id={B.id}").status_code == 200, "con ?cuenta_id la carta se genera (200)")
    check(client.get(f"/contratacion/expedientes/{EXP}/contrato?cuenta_id={B.id}").status_code == 409, "…y el contrato llega a su validación real (409 por documentos), ya no a 404")
    check(client.get(f"/contratacion/expedientes/{EXP}/carta-intencion?cuenta_id={otra.id}").status_code == 403, "una Cuenta ajena por la URL → 403 (mismo control que la cabecera)")
    # red de seguridad: postulación en Contratación SIN expediente
    r = client.post("/candidatos", headers=H, json={"nombre": "Masiva Dos", "telefono": "5511223355", "correo": "masiva2@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    p2 = db.query(Postulacion).filter(Postulacion.codigo == P2).one()
    p2.etapa = "Contratación"
    db.commit()
    check(client.get(f"/candidatos/{P2}", headers=H).json().get("expedienteId") is None, "simulación: llegó a Contratación sin expediente")
    r = client.post(f"/candidatos/{P2}/expediente", headers=H)
    check(r.status_code == 200 and r.json()["expedienteId"], "POST /candidatos/{codigo}/expediente lo crea para ESA postulación")
    check(client.post(f"/candidatos/{P2}/expediente", headers=H).json()["expedienteId"] == r.json()["expedienteId"], "idempotente: no crea otro")
    p2 = db.query(Postulacion).filter(Postulacion.codigo == P2).one()
    db.refresh(p2)
    p2.consentimiento = False
    exp2 = p2.expediente
    p2.expediente = None
    db.delete(exp2)
    db.commit()
    check(client.post(f"/candidatos/{P2}/expediente", headers=H).status_code == 409, "sin consentimiento (LFPDPPP) no se abre expediente")

    print("\n--- 2. Dropbox Sign: degradación sin llaves ---")
    check(client.get("/firmas/estado", headers=H).json()["configurado"] is False, "sin DROPBOX_SIGN_API_KEY/CLIENT_ID: configurado=false")
    check(client.post(f"/firmas/expedientes/{EXP}", headers=H, json={"documento": "carta"}).status_code == 503, "crear firma → 503 claro (la UI sigue con la vista previa del PDF)")
    check(client.post("/api/webhooks/dropbox", json=evento("callback_test", "x", llave="z")).status_code == 503, "webhook sin configurar → 503")

    print("\n--- 3. Dropbox Sign: solicitud incrustada con dos firmantes ---")
    settings.dropbox_sign_api_key = "llave-prueba-123"
    settings.dropbox_sign_client_id = "cliente-abc"
    LLAMADAS = {"crear": [], "sign_url": [], "pdf": []}

    def _crear(pdf, nombre_archivo, titulo, asunto, mensaje, firmantes, metadata, zonas=None, indice_por_rol=None):
        LLAMADAS["crear"].append({"pdf": pdf[:4], "bytes": pdf, "firmantes": firmantes, "metadata": metadata, "nombre": nombre_archivo,
                                  "zonas": zonas, "indice": indice_por_rol})
        n = len(LLAMADAS["crear"])
        return {"signature_request_id": f"sr-{n}", "signatures": [{"signature_id": f"sig-{n}-{i}", "correo": f["correo"].lower(), "nombre": f["nombre"]} for i, f in enumerate(firmantes)]}

    dsign.crear_solicitud_embebida = _crear
    dsign.sign_url = lambda sid: (LLAMADAS["sign_url"].append(sid), f"https://app.hellosign.com/editor/embeddedSign?signature_id={sid}")[1]
    dsign.descargar_pdf = lambda sr: (LLAMADAS["pdf"].append(sr), PDF)[1]
    est = client.get("/firmas/estado", headers=H).json()
    check(est["configurado"] and est["clientId"] == "cliente-abc", "con llaves: configurado y client_id para el modal")
    r = client.post(f"/firmas/expedientes/{EXP}", headers=H, json={"documento": "carta"})
    F = r.json()
    check(r.status_code == 200 and F["signUrl"].endswith("sig-1-0") and F["estado"] == "enviada", "crea la solicitud y regresa el sign_url de RH (modal incrustado)")
    c = LLAMADAS["crear"][0]
    check(c["pdf"] == b"%PDF" and [f["correo"] for f in c["firmantes"]] == [admin.correo, "masiva@correo.mx"] and c["metadata"]["documento"] == "carta",
          "manda el PDF real de la carta con RH + candidato como firmantes")
    check([x["rol"] for x in F["firmantes"]] == ["rh", "candidato"], "firmantes: representante de RH y candidato")

    print("\n--- 3b. Marca blanca: firmas SOBRE la última página (sin «Signature page» de Dropbox) ---")
    import io as _io  # noqa: E402

    from pypdf import PdfReader  # noqa: E402

    from app.services.dropbox_sign import campos_de_zonas  # noqa: E402

    zonas = c["zonas"]
    paginas = len(PdfReader(_io.BytesIO(c["bytes"])).pages)
    check(zonas and {z["pagina"] for z in zonas} == {paginas}, f"todas las zonas de firma están en la ÚLTIMA página del PDF ({paginas})")
    check(sorted((z["rol"], z["tipo"]) for z in zonas) == [("candidato", "fecha"), ("candidato", "firma"), ("empresa", "fecha"), ("empresa", "firma")],
          "firma + fecha para la empresa (RH) y para el candidato")
    check(c["indice"] == {"empresa": 0, "candidato": 1}, "índice de firmante explícito: 0 = RH (primer firmante), 1 = candidato")
    check(all(0 <= z["x"] and z["x"] + z["ancho"] <= 612 and 0 <= z["y"] and z["y"] + z["alto"] <= 792 for z in zonas), "coordenadas dentro de la hoja carta (612 × 792 pt)")
    firma_emp = next(z for z in zonas if z["rol"] == "empresa" and z["tipo"] == "firma")
    firma_can = next(z for z in zonas if z["rol"] == "candidato" and z["tipo"] == "firma")
    check(firma_emp["y"] == firma_can["y"] and firma_emp["x"] + firma_emp["ancho"] < firma_can["x"], "las dos firmas lado a lado, sin encimarse")
    campos = campos_de_zonas(zonas, c["indice"])
    check(len(campos) == 4 and {x.signer for x in campos} == {0, 1} and all(x.page == paginas for x in campos)
          and {x.type for x in campos} == {"signature", "date_signed"}, "se arman los form_fields_per_document del SDK (signature / date_signed, con page)")
    texto = "".join(pg.extract_text() for pg in PdfReader(_io.BytesIO(c["bytes"])).pages)
    check("Acepto: Masiva Uno" in texto, "la carta trae la línea de aceptación del candidato")
    r = client.post(f"/firmas/expedientes/{EXP}", headers=H, json={"documento": "carta"})
    check(r.json()["reutilizada"] and len(LLAMADAS["crear"]) == 1, "pulsar otra vez REUTILIZA la solicitud viva (no manda doble)")
    check(client.post(f"/firmas/expedientes/{EXP}", headers=H, json={"documento": "contrato"}).status_code == 409, "el contrato exige el 100 % de documentos Aprobados")
    check(client.post(f"/firmas/expedientes/{EXP}", headers=H, json={"documento": "otro"}).status_code == 400, "documento inválido → 400")
    otro_rh = Usuario(correo="otra.rh@empresa.mx", nombre="Otra RH", rol="Usuario", hash_pass="x", activo=True)
    db.add(otro_rh)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=otro_rh.id, cuenta_id=B.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: otro_rh
    check(client.post(f"/firmas/{F['id']}/sign-url", headers=H).status_code == 403, "nadie firma por otra persona: otra RH no obtiene el sign_url del representante")
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    tok = db.get(Expediente, EXP).token
    pub = client.get(f"/firmas/publica/{tok}").json()
    check(pub["configurado"] and pub["firmas"][0]["documento"] == "Carta de intención" and not pub["firmas"][0]["yoFirme"], "el candidato ve el documento por firmar en su liga")
    r = client.post(f"/firmas/publica/{tok}/{F['id']}/sign-url")
    check(r.status_code == 200 and r.json()["signUrl"].endswith("sig-1-1"), "el candidato obtiene SU sign_url (firma en nuestra interfaz)")
    check(client.post(f"/firmas/publica/token-falso/{F['id']}/sign-url").status_code == 404, "token de expediente inválido → 404")

    print("\n--- 4. Webhook /api/webhooks/dropbox ---")
    malo = evento("signature_request_signed", "sr-1")
    malo["event"]["event_hash"] = "0" * 64
    check(client.post("/api/webhooks/dropbox", data={"json": json.dumps(malo)}).status_code == 401, "firma HMAC inválida → 401, no se procesa")
    r = client.post("/api/webhooks/dropbox", data={"json": json.dumps(evento("callback_test", ""))})
    check(r.status_code == 200 and r.text == "Hello API Event Received", "callback_test → «Hello API Event Received»")
    r = client.post("/api/webhooks/dropbox", data={"json": json.dumps(evento("signature_request_signed", "sr-1", relacionado="sig-1-1"))})
    db.expire_all()
    f = db.query(FirmaDocumento).filter(FirmaDocumento.signature_request_id == "sr-1").one()
    check(r.text == "Hello API Event Received" and {x["rol"]: x["estado"] for x in f.firmantes} == {"rh": "pendiente", "candidato": "firmado"}, "signature_request_signed marca al firmante")
    check(client.post(f"/firmas/publica/{tok}/{F['id']}/sign-url").status_code == 409, "quien ya firmó no vuelve a firmar")
    todos = [{"signature_id": "sig-1-0", "status_code": "signed"}, {"signature_id": "sig-1-1", "status_code": "signed"}]
    client.post("/api/webhooks/dropbox", data={"json": json.dumps(evento("signature_request_all_signed", "sr-1", todos))})
    db.expire_all()
    f = db.query(FirmaDocumento).filter(FirmaDocumento.signature_request_id == "sr-1").one()
    doc = db.get(Documento, f.documento_id) if f.documento_id else None
    check(f.estado == "descargada" and doc and doc.interno and doc.tipo == "Carta de intención firmada" and doc.archivo,
          "all_signed → descarga el PDF final y lo guarda en el expediente (documento interno)")
    client.post("/api/webhooks/dropbox", data={"json": json.dumps(evento("signature_request_downloadable", "sr-1", todos))})
    db.expire_all()
    check(len(LLAMADAS["pdf"]) == 1 and db.query(Documento).filter(Documento.expediente_id == EXP, Documento.tipo == "Carta de intención firmada").count() == 1,
          "reintento / downloadable: idempotente (no duplica ni vuelve a descargar)")
    x = client.get(f"/contratacion/expedientes/{EXP}", headers=H).json()
    check(all(d["nombre"] != "Carta de intención firmada" for d in x["documentos"] if not d.get("interno")) and "Carta de intención firmada" not in x["noAprobados"],
          "el PDF firmado no suma ni resta al porcentaje (interno)")
    # contrato firmado por Dropbox → tarea «Contrato firmado» realizada
    cfg = obtener(db)
    cfg.modo_prueba = True
    db.commit()
    res = client.get(f"/onboarding/expedientes/{EXP}/resumen", headers=H).json()
    client.post(f"/onboarding/expedientes/{EXP}/iniciar", headers=H, json={"documentos": res["configuracion"]["documentos"], "notificar_responsables": False, "solicitar_documentos": False})
    r = client.post(f"/firmas/expedientes/{EXP}", headers=H, json={"documento": "contrato"})
    check(r.status_code == 200 and LLAMADAS["crear"][-1]["metadata"]["documento"] == "contrato", "contrato mandado a firma (Modo Prueba)")
    cz = LLAMADAS["crear"][-1]
    pag_c = len(PdfReader(_io.BytesIO(cz["bytes"])).pages)
    ultima = PdfReader(_io.BytesIO(cz["bytes"])).pages[-1].extract_text()
    check({z["pagina"] for z in cz["zonas"]} == {pag_c} and "firman de conformidad" in " ".join(ultima.split()),
          "contrato: firmas en la última página junto a la cláusula de cierre (nunca una hoja de firmas suelta)")
    sr2 = f"sr-{len(LLAMADAS['crear'])}"
    client.post("/api/webhooks/dropbox", data={"json": json.dumps(evento("signature_request_downloadable", sr2))})
    tareas = {t["clave"]: t for t in client.get(f"/onboarding/expedientes/{EXP}/tareas", headers=H).json()}
    check(tareas["contrato_firmado"]["estado"] == "realizada" and client.get(f"/contratacion/expedientes/{EXP}", headers=H).json()["contrato"] == "Firmado",
          "contrato firmado por Dropbox Sign → tarea «Contrato firmado» Realizada")
    cfg.modo_prueba = False
    db.commit()

    print("\n--- 5. Psicométricas.mx ---")
    r = client.post("/evaluaciones/pruebas", headers=H, json={"clave": "PSI-CLEAVER", "nombre": "Cleaver", "modo": "integrada", "proveedor": "Psicometricas.mx", "id_proveedor": "1, 7"})
    PR = r.json()["id"]
    r = client.post("/candidatos", headers=H, json={"nombre": "Eva Psico", "telefono": "5599887766", "correo": "eva@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    PE = r.json()["id"]
    # Evaluaciones unificadas (2026-09-29): «Usar proveedor integrado» = forma integrada con la prueba del catálogo
    NUEVA = {"tipo": "psicometrica", "forma": "integrada", "prueba_id": PR}

    def ev_de(codigo):
        return next(x for x in client.get(f"/evaluaciones/postulaciones/{PE}", headers=H).json() if x["codigo"] == codigo)

    ev1 = client.post(f"/evaluaciones/postulaciones/{PE}", headers=H, json=NUEVA).json()["evaluacion"]
    r = client.post(f"/evaluaciones/{ev1['id']}/enviar", headers=H)
    check(r.status_code == 200 and r.json()["evaluacion"]["pasoIntegrada"] == "enviada" and r.json()["evaluacion"]["claveProveedor"] is None,
          "sin llaves: modo Integrada simulado como antes (degradación)")
    settings.psicometricas_token = "T" * 20
    settings.psicometricas_usuario = "P" * 20  # compatibilidad: sin PSICOMETRICAS_PASSWORD se usa USUARIO como Password
    check(psi.configurado(), "con PSICOMETRICAS_TOKEN + PSICOMETRICAS_USUARIO: configurado")
    ENVIADO = []

    class R:
        def __init__(self, status, datos=None, contenido=b""):
            self.status_code, self._d, self.content = status, datos, contenido

        def json(self):
            if self._d is None:
                raise ValueError("sin json")
            return self._d

    ESTADO = {"fecha_fin": None}
    orig_post, orig_get = psi.httpx.post, psi.httpx.get
    psi.httpx.post = lambda url, data=None, timeout=None: (ENVIADO.append((url, data)), R(200, {"status": "200", "clave": "1-EUPQ-0116-164", "msg": "Candidato agregado correctamente."}))[1]

    def _get(url, params=None, timeout=None):
        if url.endswith("consultaCandidato"):
            return R(200, [{"clave": params["Clave"], "id_prueba": 1, "estatus": 2, "fecha_fin": ESTADO["fecha_fin"]},
                           {"clave": params["Clave"], "id_prueba": 7, "estatus": 2, "fecha_fin": ESTADO["fecha_fin"]}])
        if params.get("Pdf") == "true":
            return R(200, None, PDF)
        return R(200, {"cleaver": {"D": 12, "I": 8}, "terman": {"ci": 105}})

    psi.httpx.get = _get
    ev2 = client.post(f"/evaluaciones/postulaciones/{PE}", headers=H, json=NUEVA).json()["evaluacion"]
    r = client.post(f"/evaluaciones/{ev2['id']}/enviar", headers=H)
    url, datos = ENVIADO[-1]
    check(url == "https://admin.psicometricas.mx/api/agregaCandidato" and datos["Token"] == "T" * 20 and datos["Password"] == "P" * 20
          and datos["Candidate"] == "Eva Psico" and datos["Email"] == "eva@correo.mx" and datos["Tests"] == "1,7" and datos["Lang"] == "Mx" and datos["Vacancy"],
          "agregaCandidato con Token, Password, Candidate, Email, Vacancy, Tests «1,7» y Lang Mx")
    e = r.json()["evaluacion"]
    check(e["claveProveedor"] == "1-EUPQ-0116-164" and e["estado"] == "pendiente" and e["pasoIntegrada"] == "enviada",
          "guarda la clave y queda Pendiente (paso Enviada)")
    check(e["urlCandidatoProveedor"] is None, "su API no regresa liga: sin PSICOMETRICAS_URL_CANDIDATO solo se muestra la clave (no se inventa)")
    settings.psicometricas_url_candidato = "https://evaluacion.ejemplo.mx/acceso/{clave}"
    check(ev_de(ev2["id"])["urlCandidatoProveedor"] == "https://evaluacion.ejemplo.mx/acceso/1-EUPQ-0116-164", "con la plantilla configurada se arma la liga")
    check(client.post(f"/evaluaciones/{ev2['id']}/integracion/avanzar", headers=H).status_code == 409, "conectada al proveedor: ya no se simula a mano")
    settings.psicometricas_webhook_secret = "secreto-xyz"
    check(client.post("/api/webhooks/psicometricas?secreto=malo", json={"clave": "1-EUPQ-0116-164", "type": "termina_prueba"}).status_code == 401, "webhook con secreto incorrecto → 401")
    r = client.post("/api/webhooks/psicometricas?secreto=secreto-xyz", json={"clave": "1-EUPQ-0116-164", "type": "termina_prueba", "nombre_prueba": "Cleaver"})
    db.expire_all()
    e2 = db.query(Evaluacion).filter(Evaluacion.codigo == ev2["id"]).one()
    check(r.status_code == 200 and e2.estado == "pendiente", "aviso NO confirmado por su API (sin fecha_fin): no se guarda nada (anti-falsificación)")
    ESTADO["fecha_fin"] = "2026-09-29 10:00:00"
    client.post("/api/webhooks/psicometricas?secreto=secreto-xyz", json={"clave": "1-EUPQ-0116-164", "type": "termina_prueba"})
    db.expire_all()
    e2 = db.query(Evaluacion).filter(Evaluacion.codigo == ev2["id"]).one()
    check(e2.estado == "con_resultado" and e2.paso_integrada == "resultado_recibido" and len(e2.adjuntos) == 1 and e2.resultado_json.get("terman"),
          "confirmado (fecha_fin) → descarga JSON + PDF y queda «Con resultado» (Resultado recibido · Sin conclusión)")
    check(e2.registrada_por == "Psicométricas.mx (automático)" and e2.registrada_via == "proveedor" and e2.realizada_por
          and db.query(EventoEvaluacion).filter(EventoEvaluacion.evaluacion_id == e2.id, EventoEvaluacion.canal == "proveedor").count() >= 2,
          "trazabilidad: quién/cuándo (autor = proveedor, captura vía proveedor) y pasos en el historial")
    client.post("/api/webhooks/psicometricas?secreto=secreto-xyz", json={"clave": "1-EUPQ-0116-164", "type": "termina_prueba"})
    db.expire_all()
    e2 = db.query(Evaluacion).filter(Evaluacion.codigo == ev2["id"]).one()
    check(len(e2.adjuntos) == 1 and client.get(f"/evaluaciones/{ev2['id']}/adjuntos/{e2.adjuntos[0]['id']}", headers=H).status_code == 200,
          "reintento idempotente y el informe PDF se descarga")
    r = client.post(f"/evaluaciones/{ev2['id']}/resultado", headers=H, data={"conclusion": "favorable", "modo": "complementar", "version": str(e2.resultado_version)})
    check(r.status_code == 200 and r.json()["evaluacion"]["conclusion"] == "favorable", "RH agrega su conclusión (HITL) sobre el resultado del proveedor")
    psi.httpx.post = lambda url, data=None, timeout=None: R(401, {"code": "1001", "msg": "Token inválido"})
    ev3 = client.post(f"/evaluaciones/postulaciones/{PE}", headers=H, json=NUEVA).json()["evaluacion"]
    r = client.post(f"/evaluaciones/{ev3['id']}/enviar", headers=H)
    check(r.status_code == 502 and "1001" in r.json()["detail"], "credencial rechazada → 502 con el motivo del proveedor")
    e3 = ev_de(ev3["id"])
    check(e3["estado"] == "pendiente" and e3["pasoIntegrada"] == "asignada" and not e3["claveProveedor"], "…y la evaluación no cambió")
    try:
        psi.tests_de("Cleaver")
        check(False, "tests_de debe exigir IDs numéricos")
    except psi.PsicometricasError:
        check(True, "el identificador del proveedor debe ser el ID numérico de la prueba (1 = Cleaver…)")
    psi.httpx.post, psi.httpx.get = orig_post, orig_get

    print("\n--- 6. Sin secretos en el código ---")
    for ruta in ("app/services/dropbox_sign.py", "app/services/psicometricas.py", "app/routers/firmas.py", "app/routers/webhooks_proveedores.py", "app/config.py"):
        texto = (RAIZ / ruta).read_text(encoding="utf-8")
        check("llave-prueba" not in texto and "T" * 20 not in texto, f"{ruta}: sin llaves embebidas")
    db.close()

print(f"\n🎉 Firmas + Psicométricas + bug de expediente: {OK} verificaciones OK")
