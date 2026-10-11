"""Regresión de la transición Contratación → Onboarding sin duplicados (2026-10-08).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_contratacion_onboarding.py

Base desechable, SIN red: Dropbox Sign se reemplaza por un falso (crear, sign_url, consulta de estado, PDF final);
WhatsApp, correo y OpenAI sin configurar. Secuencia principal (lo que pidió el usuario):
  candidato con documentos 100 % Aprobados y contrato firmado en Contratación (con «This request has already been
  signed» del proveedor interceptado) → pasa a Onboarding → NO se le piden documentos de nuevo (solo bienvenida) → NO se
  le pide firmar el contrato de nuevo (la tarea nace Realizada) → se registra el alta IMSS y se confirma el ingreso
  DESDE LA FICHA → la recomendación sugiere dar de alta → alta sobre el MISMO expediente → se cierra el proceso.
Además: % único (tablero = Onboarding = ficha), falla de red con mensaje limpio (detalle solo en bitácora), contrato
OMITIDO en Contratación → «Omitida» en Onboarding sin bloquear, contrato físico adjuntado en Contratación, documentos
parcialmente entregados → solo se pide lo que falta, y asignar responsable avisa solo a esa persona.
"""

import io
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_dir = tempfile.mkdtemp(prefix="rh_contr_onb_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "co.db").replace("\\", "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["DROPBOX_SIGN_API_KEY"] = "llave-falsa-de-prueba"
os.environ["DROPBOX_SIGN_CLIENT_ID"] = "cliente-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-contr-onb"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.fechas import TZ_ORG  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    TIPO_CONTRATO_FIRMADO, Bitacora, Colaborador, Cuenta, Expediente, FirmaDocumento, Postulacion, Usuario, UsuarioCuenta, Vacante,
)
from app.routers.candidatos import _abrir_expediente, _crear_candidato, crear_postulacion  # noqa: E402
from app.services import dropbox_sign as dsign  # noqa: E402
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


def pdf_firmado() -> bytes:
    from fpdf import FPDF

    p = FPDF()
    p.add_page()
    p.set_font("Helvetica", size=12)
    p.cell(0, 10, "Contrato firmado (prueba)")
    return bytes(p.output())


# ---------------- Dropbox Sign falso ----------------
PROVEEDOR = {"sign_url": {}, "estado": {}}


def _crear(pdf, nombre, titulo, asunto, mensaje, firmantes, metadata, zonas=None, indice_por_rol=None):
    n = len(PROVEEDOR["estado"]) + 1
    sr = f"sr-{n}"
    PROVEEDOR["estado"][sr] = {"completa": False, "cancelada": False, "firmados": []}
    return {"signature_request_id": sr, "signatures": [
        {"signature_id": f"{sr}-rh", "correo": firmantes[0]["correo"].lower(), "nombre": firmantes[0]["nombre"]},
        {"signature_id": f"{sr}-cand", "correo": firmantes[1]["correo"].lower(), "nombre": firmantes[1]["nombre"]}]}


def _sign_url(signature_id):
    comportamiento = PROVEEDOR["sign_url"].get(signature_id, "ok")
    if comportamiento == "ya_firmado":
        raise dsign.FirmaError("Dropbox Sign respondió 400: {\"error\": {\"error_msg\": \"This request has already been signed\"}}", 400)
    if comportamiento == "red":
        raise dsign.FirmaError("HTTPSConnectionPool(host='api.hellosign.com'): Read timed out.", None)
    return f"https://app.hellosign.com/editor/embeddedSign?signature_id={signature_id}"


dsign.crear_solicitud_embebida = _crear
dsign.sign_url = _sign_url
dsign.consultar_solicitud = lambda sr: dict(PROVEEDOR["estado"][sr])
dsign.descargar_pdf = lambda sr: pdf_firmado()

RUTA = [
    {"id": "condiciones", "tipo": "condiciones", "nombre": "Condiciones de contratación", "etapa": "Contratación"},
    {"id": "carta-contrato", "tipo": "carta_contrato", "nombre": "Carta intención / contrato", "etapa": "Contratación",
     "depende_de": ["condiciones"]},
    {"id": "documentos-ingreso", "tipo": "documentos", "nombre": "Documentos de ingreso", "etapa": "Onboarding"},
    {"id": "alta", "tipo": "alta", "nombre": "Alta como colaborador", "etapa": "Onboarding", "depende_de": ["documentos-ingreso"]},
]

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    obtener(db).modo_prueba = False
    cu = Cuenta(nombre="Contratación SA", nombre_comercial="Contratación SA", razon_social="Contratación SA de CV", estado="Activa", slug="contr-sa")
    db.add(cu)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cu.id))
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").first()
    v.cuenta_id = cu.id
    v.proceso = sproc.proceso_para_vacante(db, cu.id, {}, {"pasos": RUTA, "etapas": {}})
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin
    H = {"X-Cuenta-Id": str(cu.id)}

    def candidato(nombre: str, correo: str, aprobados: int = 6) -> Postulacion:
        persona = _crear_candidato(db, cu.id, nombre, "Formulario", False, correo=correo, telefono="")
        p = crear_postulacion(db, persona, v, cu.id, "formulario", consentimiento=True)
        p.etapa = "Contratación"
        e = _abrir_expediente(db, p, admin)
        db.flush()
        for i, d in enumerate(e.documentos):
            if i < aprobados:
                d.estado, d.revisado_por, d.archivo, d.nombre_archivo = "recibido", admin.nombre, f"{_dir}/doc{i}.pdf", f"doc{i}.pdf"
        e.puesto, e.sueldo, e.tipo_contratacion = "Auxiliar", "$12,000 mensuales", "Tiempo indeterminado"
        e.fecha_ingreso = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        e.condiciones_guardadas_en = datetime.now(timezone.utc)
        p.analisis = {**(p.analisis or {}), "propuesta": {"estado": "aceptada", "canal": "rh"}}  # 2026-10-10: propuesta aceptada
        db.commit()
        return p

    def recargar(codigo: str) -> Postulacion:
        db.expire_all()
        return db.query(Postulacion).filter(Postulacion.codigo == codigo).first()

    def seg(codigo: str) -> dict:
        r = client.get(f"/procesos/postulaciones/{codigo}", headers=H)
        assert r.status_code == 200, r.text
        return r.json()

    def paso(s: dict, pid: str) -> dict:
        return next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == pid)

    def tareas(exp_id: int) -> dict:
        return {t["clave"]: t for t in client.get(f"/onboarding/expedientes/{exp_id}/tareas", headers=H).json()}

    def iniciar(exp_id: int) -> dict:
        res = client.get(f"/onboarding/expedientes/{exp_id}/resumen", headers=H).json()
        r = client.post(f"/onboarding/expedientes/{exp_id}/iniciar", headers=H, json={"documentos": res["configuracion"]["documentos"]})
        assert r.status_code == 200, r.text
        return r.json()

    # ================================================================== secuencia principal
    print("\n--- 1. Contratación: documentos 100 % y contrato firmado (con «already signed» interceptado) ---")
    pa = candidato("Ana Completa", "ana.completa@correo.mx")
    PA, EA = pa.codigo, pa.expediente.id
    check(pa.expediente.progreso == 100, "expediente al 100 % de obligatorios Aprobados")
    r = client.post(f"/firmas/expedientes/{EA}", headers=H, json={"documento": "contrato"})
    check(r.status_code == 200 and r.json()["signUrl"], "se crea la solicitud de firma del contrato y RH recibe su liga")
    f = db.query(FirmaDocumento).filter(FirmaDocumento.expediente_id == EA).one()
    sr = f.signature_request_id
    # RH ya firmó en otra pestaña: el proveedor contesta «already signed» al pedir la liga
    PROVEEDOR["estado"][sr]["firmados"] = [f"{sr}-rh"]
    PROVEEDOR["sign_url"][f"{sr}-rh"] = "ya_firmado"
    r = client.post(f"/firmas/{f.id}/sign-url", headers=H)
    check(r.status_code == 200 and r.json()["signUrl"] is None and r.json()["yaFirmado"],
          "«This request has already been signed» NO es error: se consulta el estado real y RH queda «firmó»")
    check(next(x for x in r.json()["firmantes"] if x["rol"] == "rh")["estado"] == "firmado", "el estado local del firmante se actualizó")
    # el candidato firma, pero el webhook nunca llega: al abrir su liga el proveedor dice «already signed»
    PROVEEDOR["estado"][sr] = {"completa": True, "cancelada": False, "firmados": [f"{sr}-rh", f"{sr}-cand"]}
    PROVEEDOR["sign_url"][f"{sr}-cand"] = "ya_firmado"
    token = pa.expediente.token
    publicas = client.get(f"/firmas/publica/{token}").json()["firmas"]
    check(publicas[0]["puedoFirmar"] is True, "antes de sincronizar el candidato todavía ve «Firmar»")
    r = client.post(f"/firmas/publica/{token}/{f.id}/sign-url")
    check(r.status_code == 200 and r.json()["yaFirmado"] and r.json()["signUrl"] is None and "Ya firmaste" in r.json()["mensaje"],
          "al candidato se le muestra un estado limpio («Ya firmaste»), nunca el error del proveedor")
    publicas = client.get(f"/firmas/publica/{token}").json()["firmas"]
    check(publicas[0]["yoFirme"] and not publicas[0]["puedoFirmar"], "la acción «Firmar» desaparece para quien ya firmó")
    db.expire_all()
    f = db.get(FirmaDocumento, f.id)
    pa = recargar(PA)
    check(f.estado == "descargada" and any(d.interno and d.tipo == TIPO_CONTRATO_FIRMADO and d.archivo for d in pa.expediente.documentos),
          "solicitud completa: el PDF final se guardó en el expediente (documento interno)")
    check(paso(seg(PA), "carta-contrato")["estado"] == "completada", "la actividad de contrato de Contratación queda Completada")

    print("\n--- 2. Falla de red: mensaje limpio al candidato y reintento; el detalle solo en logs ---")
    pb = candidato("Beto Red", "beto.red@correo.mx")
    r = client.post(f"/firmas/expedientes/{pb.expediente.id}", headers=H, json={"documento": "contrato"})
    fb = db.query(FirmaDocumento).filter(FirmaDocumento.expediente_id == pb.expediente.id).one()
    PROVEEDOR["sign_url"][f"{fb.signature_request_id}-cand"] = "red"
    r = client.post(f"/firmas/publica/{pb.expediente.token}/{fb.id}/sign-url")
    check(r.status_code == 503 and "Intenta de nuevo" in r.json()["detail"] and "timed out" not in r.json()["detail"]
          and "hellosign" not in r.json()["detail"], "sin red → 503 con mensaje limpio (el candidato ve «Reintentar»)")
    check(db.query(Bitacora).filter(Bitacora.accion == "firma_error_tecnico").count() >= 1, "el detalle técnico quedó en bitácora interna")
    PROVEEDOR["sign_url"][f"{fb.signature_request_id}-cand"] = "ok"
    r = client.post(f"/firmas/publica/{pb.expediente.token}/{fb.id}/sign-url")
    check(r.status_code == 200 and r.json()["signUrl"], "el reintento funciona en cuanto vuelve la red")

    print("\n--- 3. Pasa a Onboarding: sin pedir documentos ni contrato otra vez ---")
    s = seg(PA)
    check(s["recomendacion"] and "contrataci" not in s["recomendacion"]["texto"].lower(),
          f"en Contratación la recomendación es el pendiente actual, no «Avanzar a contratación» ({s['recomendacion']['texto']})")
    docs_antes = {d.tipo: (d.estado, d.revisado_por) for d in pa.expediente.documentos if not d.interno}
    r = iniciar(EA)
    check(r["solicitudDocumentos"] == [] and r["documentosPorSolicitar"] == [], "expediente al 100 %: NO se envía la liga de documentos")
    check(r["bienvenida"] != [] and db.query(Bitacora).filter(Bitacora.accion == "bienvenida_onboarding_enviada").count() == 1,
          "solo se dispara la bienvenida con instrucciones de ingreso")
    check(db.query(Bitacora).filter(Bitacora.accion == "documentos_solicitados", Bitacora.entidad_id == PA).count() == 0,
          "ninguna solicitud de documentos en la bitácora")
    pa = recargar(PA)
    check(pa.etapa == "Onboarding" and pa.expediente.id == EA and db.query(Expediente).filter(Expediente.postulacion_id == pa.id).count() == 1,
          "mismo expediente (no se duplica)")
    check({d.tipo: (d.estado, d.revisado_por) for d in pa.expediente.documentos if not d.interno} == docs_antes,
          "los documentos aprobados se reutilizan tal cual")
    t = tareas(EA)
    check(t["contrato_firmado"]["estado"] == "realizada" and t["contrato_firmado"]["accion"] is None,
          "«Contrato firmado» nace Realizada: apunta al MISMO contrato de Contratación (no se pide firmar de nuevo)")

    print("\n--- 4. Un solo porcentaje ---")
    tarjeta = next(x for x in client.get("/candidatos", headers=H).json() if x["id"] == PA)
    ficha = client.get(f"/candidatos/{PA}", headers=H).json()
    tablero = client.get(f"/onboarding/expedientes/{EA}/estado", headers=H).json()
    check(tarjeta["expediente_pct"] == ficha["expedienteProgreso"] == tablero["documentos"]["pct"] == 100,
          "tarjeta del tablero = ficha = Onboarding (100 %)")

    print("\n--- 5. Tareas resueltas DESDE LA FICHA ---")
    s = seg(PA)
    check(s["siguienteAccion"]["tipo"] == "tarea" and s["siguienteAccion"]["tarea"] == t["alta_imss_nomina"]["id"],
          f"acción principal = la tarea pendiente ({s['siguienteAccion']['texto']})")
    check(s["recomendacion"]["texto"] == "Falta registrar el alta IMSS / nómina", "la recomendación es el pendiente actual")
    check(t["alta_imss_nomina"]["accion"]["clave"] == "registrar_tarea" and t["confirmar_ingreso"]["accion"]["clave"] == "confirmar_ingreso",
          "cada tarea trae su acción directa (Registrar alta / Confirmar ingreso)")
    r = client.patch(f"/onboarding/tareas/{t['alta_imss_nomina']['id']}", headers=H, json={"responsable": admin.nombre})
    check(r.status_code == 200 and r.json()["avisoResponsable"]["destinatario"] == admin.nombre,
          "asignar responsable avisa SOLO a esa persona (correo; sin Resend queda registrado como no enviado)")
    r = client.patch(f"/onboarding/tareas/{t['alta_imss_nomina']['id']}", headers=H, json={"estado": "realizada", "notas": "NSS dado de alta"})
    check(r.status_code == 200 and r.json()["estado"] == "realizada", "«Registrar alta IMSS / nómina» = registro manual")
    s = seg(PA)
    check(s["siguienteAccion"]["tipo"] == "tarea" and s["siguienteAccion"]["texto"] == "Confirmar ingreso", "→ siguiente: Confirmar ingreso")
    hoy = datetime.now(TZ_ORG).date().isoformat()  # día de la organización (la API rechaza fechas futuras en MX)
    r = client.post(f"/onboarding/expedientes/{EA}/confirmar-ingreso", headers=H, json={"fecha_real": hoy})
    check(r.status_code == 200, "ingreso confirmado desde la ficha")

    print("\n--- 6. Recomendación: dar de alta → alta → cierre ---")
    s = seg(PA)
    check(s["siguienteAccion"]["tipo"] == "alta" and s["recomendacion"]["texto"] == "Lista para dar de alta",
          "con la última tarea resuelta, la recomendación sugiere dar de alta")
    r = client.post(f"/contratacion/expedientes/{EA}/alta", headers=H, json={})
    check(r.status_code == 200, f"alta como colaborador ({r.status_code} {r.text[:200]})")
    pa = recargar(PA)
    check(db.query(Colaborador).filter(Colaborador.cuenta_id == cu.id).count() == 1 and pa.expediente.id == EA,
          "un solo colaborador, sobre el mismo expediente")
    s = seg(PA)
    check(s["siguienteAccion"]["tipo"] == "cerrar_onboarding", "después del alta la acción es «Cerrar Onboarding»")
    r = client.post(f"/onboarding/expedientes/{EA}/cerrar", headers=H)
    check(r.status_code == 200, "Onboarding cerrado")
    s = seg(PA)
    check(s["siguienteAccion"]["tipo"] == "fin" and s["recomendacion"]["texto"] == "Proceso completo", "el proceso queda completo")

    # ================================================================== variantes
    print("\n--- 7. Contrato OMITIDO en Contratación → «Omitida» en Onboarding (no bloquea) ---")
    pc = candidato("Caro Omitida", "caro.omitida@correo.mx")
    r = client.post(f"/procesos/postulaciones/{pc.codigo}/pasos/carta-contrato/omitir", headers=H,
                    json={"motivo": "Contrato colectivo: se firma con el sindicato"})
    check(r.status_code == 200, "RH omite la actividad de contrato con autorización")
    iniciar(pc.expediente.id)
    t = tareas(pc.expediente.id)
    check(t["contrato_firmado"]["estado"] == "cancelada" and t["contrato_firmado"]["omitida"]
          and "sindicato" in t["contrato_firmado"]["motivoCancelacion"], "la tarea aparece «Omitida» con el motivo de Contratación")
    s = seg(pc.codigo)
    check(s["siguienteAccion"]["tipo"] == "tarea" and s["siguienteAccion"]["tarea"] != t["contrato_firmado"]["id"],
          "no vuelve a bloquear: la acción principal salta a la siguiente tarea")
    r = client.post(f"/procesos/postulaciones/{pc.codigo}/pasos/carta-contrato/reactivar", headers=H)
    check(r.status_code == 200 and tareas(pc.expediente.id)["contrato_firmado"]["estado"] == "pendiente",
          "si RH reactiva la actividad, la tarea vuelve a Pendiente")

    print("\n--- 8. Contrato físico adjuntado en Contratación ---")
    pd = candidato("Dani Físico", "dani.fisico@correo.mx")
    r = client.post(f"/onboarding/expedientes/{pd.expediente.id}/contrato-firmado", headers=H,
                    files={"archivo": ("contrato.pdf", io.BytesIO(pdf_firmado()), "application/pdf")})
    check(r.status_code == 200 and r.json()["tarea"] is None, "se acepta el PDF firmado en Contratación (antes 409)")
    check(paso(seg(pd.codigo), "carta-contrato")["estado"] == "completada", "completa la actividad de contrato")
    db.refresh(pd)
    check(pd.etapa == "Onboarding", "documentos firmados → el candidato pasa SOLO a Onboarding (2026-10-09)")
    check(tareas(pd.expediente.id)["contrato_firmado"]["estado"] == "realizada", "y en Onboarding la tarea ya llega Realizada")

    print("\n--- 9. Expediente parcial: solo se pide lo que falta ---")
    pe = candidato("Eli Parcial", "eli.parcial@correo.mx", aprobados=5)
    falta = [d.tipo for d in pe.expediente.documentos if not d.aprobado and not d.interno]
    client.post(f"/onboarding/expedientes/{pe.expediente.id}/contrato-firmado", headers=H,
                files={"archivo": ("contrato.pdf", io.BytesIO(pdf_firmado()), "application/pdf")})
    # 2026-10-09: el contrato firmado inicia el Onboarding SOLO (misma lógica que «Iniciar Onboarding»)
    pe = recargar(pe.codigo)
    from app.services import onboarding as onb_srv

    check(pe.etapa == "Onboarding" and [d.tipo for d in onb_srv.documentos_por_solicitar(pe.expediente)] == falta
          and not db.query(Bitacora).filter_by(accion="bienvenida_onboarding_enviada", entidad_id=str(pe.expediente.id)).count(),
          f"inicio automático: solo queda por pedir {falta}, sin bienvenida de expediente completo")
    check(all(d.solicitado_en is None for d in pe.expediente.documentos if d.aprobado), "lo ya aprobado nunca se marca como solicitado")
    db.close()

print(f"\n{OK} comprobaciones OK")
