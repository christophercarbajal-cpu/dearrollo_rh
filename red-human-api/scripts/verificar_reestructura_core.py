"""Regresión de la reestructura del core (2026-10-09). Base desechable, SIN red (Dropbox Sign nunca se llama).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_reestructura_core.py

1. Rutas base: «Masivos sin/con documentos iniciales» y «Corporativos»; las de 2026-10-06 se desactivan UNA vez (sin
   borrarse) y la predeterminada pasa a su equivalente; en una Cuenta demo editar una ruta base la DUPLICA.
2. Catálogo único: avance automático siempre (sin interruptor) y dependencias calculadas por el catálogo.
3. Firmas: modo Demo (Cuenta demo) = firma en la plataforma con PDF con marca de agua y SIN Dropbox Sign; al firmar
   ambos, la ruta sale SOLA a Onboarding sin volver a pedir documentos aprobados. Modo Papel = PDF unido para imprimir
   + subir el firmado → Onboarding solo.
4. Estados: con evaluador y cita no dice «Esperando entrevistador»; «Avanzar» del entrevistador no espera revisión de
   RH; en Onboarding nada de una etapa anterior aparece «En curso» y no hay «Avanzar a contratación».
"""

import base64
import io
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

_dir = tempfile.mkdtemp(prefix="rh_core_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "core.db").replace("\\", "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET",
          "DROPBOX_SIGN_API_KEY", "DROPBOX_SIGN_CLIENT_ID"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-core"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual, usuario_admin, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    TIPO_CONTRATO_FIRMADO, Bitacora, Cuenta, Evaluacion, FirmaDocumento, PlantillaProceso, Postulacion, Usuario, UsuarioCuenta, Vacante,
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


def _nunca(*a, **k):
    raise AssertionError("Dropbox Sign NO debe llamarse en modo Demo/Papel")


dsign.crear_solicitud_embebida = _nunca
dsign.sign_url = _nunca


def png_firma() -> str:
    from PIL import Image  # pillow viene con fpdf2

    im = Image.new("RGB", (240, 80), "white")
    for x in range(20, 220):
        im.putpixel((x, 40 + (x % 15)), (10, 10, 80))
    b = io.BytesIO()
    im.save(b, format="PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def pdf_simple() -> bytes:
    from fpdf import FPDF

    p = FPDF()
    p.add_page()
    p.set_font("Helvetica", size=12)
    p.cell(0, 10, "Documentos firmados en papel (prueba)")
    return bytes(p.output())


RUTA = [
    {"id": "condiciones", "tipo": "condiciones", "nombre": "Condiciones de contratación", "etapa": "Contratación"},
    {"id": "carta-contrato", "tipo": "carta_contrato", "nombre": "Firmar documentos", "etapa": "Contratación"},
    {"id": "documentos-ingreso", "tipo": "documentos", "nombre": "Documentos de ingreso", "etapa": "Onboarding"},
    {"id": "alta", "tipo": "alta", "nombre": "Alta como colaborador", "etapa": "Onboarding"},
]

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    if not admin.correo:
        admin.correo = "admin@redhuman.mx"
    obtener(db).modo_prueba = False
    demo = Cuenta(nombre="Demo GrupPak", nombre_comercial="GrupPak", razon_social="GrupPak SA de CV", estado="Activa", slug="demo-grupak")
    real = Cuenta(nombre="Real SA", nombre_comercial="Real", razon_social="Real SA de CV", estado="Activa", slug="real-sa")
    db.add_all([demo, real])
    db.flush()
    db.add_all([UsuarioCuenta(usuario_id=admin.id, cuenta_id=demo.id), UsuarioCuenta(usuario_id=admin.id, cuenta_id=real.id)])
    db.commit()
    for dep in (usuario_actual, usuario_decisor, usuario_admin):
        app.dependency_overrides[dep] = lambda: admin

    # ================= 1. Rutas base =================
    print("\n--- 1. Rutas base nuevas y retiro de las anteriores ---")
    sproc.asegurar_rutas_base(db, demo.id)
    sproc.asegurar_rutas_base(db, real.id)
    vieja = PlantillaProceso(cuenta_id=real.id, nombre="Masivos", pasos=sproc.ruta_base("masivos_con_documentos")["pasos"], etapas={},
                             version=1, predeterminada=True, activa=True, ruta_base="masivos")
    db.add(vieja)
    db.query(Bitacora).filter(Bitacora.accion == "rutas_base_2026_10_09").delete()
    db.commit()
    r = sproc.retirar_rutas_base_anteriores(db)
    db.commit()
    nueva_pred = db.query(PlantillaProceso).filter_by(cuenta_id=real.id, predeterminada=True).one()
    check(r["desactivadas"] >= 1 and not db.get(PlantillaProceso, vieja.id).activa and nueva_pred.ruta_base == "masivos_con_documentos",
          "la ruta base anterior se desactiva (no se borra) y su predeterminada pasa a «Masivos con documentos iniciales»")
    check(sproc.retirar_rutas_base_anteriores(db).get("yaAplicada"), "el retiro corre una sola vez")
    H_DEMO, H_REAL = {"X-Cuenta-Id": str(demo.id)}, {"X-Cuenta-Id": str(real.id)}
    pls = {p["rutaBase"]: p for p in client.get("/procesos/plantillas", headers=H_DEMO).json() if p.get("rutaBase")}
    check(set(pls) == {"masivos_sin_documentos", "masivos_con_documentos", "corporativo"},
          "rutas base: Masivos sin documentos iniciales · Masivos con documentos iniciales · Corporativos")
    base = pls["corporativo"]
    r = client.patch(f"/procesos/plantillas/{base['id']}", headers=H_DEMO, json={"pasos": base["pasos"][:-1] + [dict(base["pasos"][-1], nombre="Alta (editada)")]})
    check(r.status_code == 200 and r.json()["id"] != base["id"] and r.json()["nombre"].endswith("(copia)") and r.json()["rutaBase"] == "",
          "Cuenta demo: editar una ruta base la DUPLICA")
    original = next(p for p in client.get("/procesos/plantillas", headers=H_DEMO).json() if p["id"] == base["id"])
    check(original["version"] == base["version"] and original["pasos"][-1]["nombre"] == "Confirmación de ingreso", "…y la original queda intacta")
    pr = next(p for p in client.get("/procesos/plantillas", headers=H_REAL).json() if p.get("rutaBase") == "corporativo")
    r = client.patch(f"/procesos/plantillas/{pr['id']}", headers=H_REAL, json={"pasos": pr["pasos"]})
    check(r.json()["id"] == pr["id"], "Cuenta normal: la ruta base se edita en sitio (sin copia)")

    # ================= 2. Catálogo único =================
    print("\n--- 2. Catálogo único ---")
    etapas = sproc.normalizar_etapas({"Prefiltro": {"avance_automatico": False}})
    check(etapas["Prefiltro"]["avance_automatico"] and not etapas["Contratación"]["avance_automatico"],
          "avance automático siempre (lo guardado se ignora); Contratación/Onboarding no avanzan por interruptor")
    pasos = sproc.normalizar_pasos([dict(x, depende_de=[]) for x in RUTA])
    check(next(x for x in pasos if x["id"] == "carta-contrato")["depende_de"] == ["condiciones"]
          and next(x for x in pasos if x["id"] == "alta")["depende_de"] == ["documentos-ingreso"],
          "las dependencias salen del catálogo (Firmar documentos espera condiciones; Alta espera documentos)")

    v = db.query(Vacante).filter(Vacante.estado == "Publicada").first()
    v2 = db.query(Vacante).filter(Vacante.estado == "Publicada", Vacante.id != v.id).first()

    def candidato(cuenta, vac, nombre, correo):
        vac.cuenta_id = cuenta.id
        vac.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": RUTA, "etapas": {}})
        db.commit()
        persona = _crear_candidato(db, cuenta.id, nombre, "Formulario", False, correo=correo, telefono="")
        p = crear_postulacion(db, persona, vac, cuenta.id, "formulario", consentimiento=True)
        p.etapa = "Contratación"
        e = _abrir_expediente(db, p, admin)
        db.flush()
        for i, d in enumerate(e.documentos):
            d.estado, d.revisado_por, d.archivo, d.nombre_archivo = "recibido", admin.nombre, f"{_dir}/doc{i}.pdf", f"doc{i}.pdf"
        e.puesto, e.sueldo, e.tipo_contratacion = "Auxiliar", "$12,000 mensuales", "Tiempo indeterminado"
        e.fecha_ingreso = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=3)
        e.condiciones_guardadas_en = datetime.now(timezone.utc)
        p.analisis = {**(p.analisis or {}), "propuesta": {"estado": "aceptada", "canal": "rh"}}  # 2026-10-10: propuesta aceptada
        db.commit()
        return p.codigo, e.id, e.token

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    # ================= 3. Firmas · modo Demo =================
    print("\n--- 3. «Firmar documentos» en modo Demo ---")
    check(client.get("/firmas/modo", headers=H_DEMO).json()["modo"] == "demo", "Cuenta demo → modo de firma Demo por defecto")
    check(client.get("/firmas/modo", headers=H_REAL).json()["modo"] == "papel", "sin Dropbox Sign configurado → Papel")
    PD, ED, TOK = candidato(demo, v, "Diana Demo", "diana@demo.mx")
    s = client.get(f"/procesos/postulaciones/{PD}", headers=H_DEMO).json()
    paso_firma = next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == "carta-contrato")
    check("Firmar documentos" in paso_firma["espera"], f"la actividad pide «Firmar documentos» ({paso_firma['espera']})")
    r = client.post(f"/firmas/expedientes/{ED}/documentos", headers=H_DEMO)
    check(r.status_code == 200 and r.json()["modo"] == "demo" and r.json()["documento"] == "documentos" and r.json()["signUrl"] is None,
          "«Firmar documentos» crea UNA solicitud (carta + contrato) en modo Demo, sin URL del proveedor")
    FD = r.json()["id"]
    check(client.post(f"/firmas/expedientes/{ED}/documentos", headers=H_DEMO).json()["id"] == FD, "una solicitud viva se reutiliza")
    r = client.get(f"/firmas/publica/{TOK}")
    check(r.json()["firmas"][0]["modo"] == "demo" and r.json()["firmas"][0]["puedoFirmar"], "el portal del candidato ve la firma Demo pendiente")
    r = client.post(f"/firmas/{FD}/demo/firmar", headers=H_DEMO, json={"texto": "x"})
    check(r.status_code == 400, "firma vacía o muy corta → 400 con mensaje claro")
    r = client.post(f"/firmas/{FD}/demo/firmar", headers=H_DEMO, json={"texto": admin.nombre})
    check(r.status_code == 200 and not r.json()["completa"] and post(PD).etapa == "Contratación", "RH firma escribiendo su nombre; falta el candidato")
    check(client.post(f"/firmas/{FD}/demo/firmar", headers=H_DEMO, json={"texto": admin.nombre}).status_code == 409, "RH no firma dos veces")
    r = client.post(f"/firmas/publica/{TOK}/{FD}/demo/firmar", json={"imagen": png_firma()})
    check(r.status_code == 200 and r.json()["completa"], "el candidato dibuja su firma en su liga → documentos firmados")
    f = db.get(FirmaDocumento, FD)
    db.refresh(f)
    check(f.estado == "descargada" and f.documento_id, "el PDF final quedó guardado en el expediente")
    pd = post(PD)
    doc = next(d for d in pd.expediente.documentos if d.interno and d.tipo == TIPO_CONTRATO_FIRMADO)
    from pypdf import PdfReader

    texto = "".join(pg.extract_text() or "" for pg in PdfReader(doc.archivo).pages)
    check("DEMOSTRACI" in texto and "SIN VALIDEZ LEGAL" in texto, "el PDF lleva la marca de agua de demostración")
    check(pd.etapa == "Onboarding", "al completarse «Firmar documentos» el candidato pasa SOLO a Onboarding")
    check(db.query(Bitacora).filter_by(accion="onboarding_iniciado_por_firma", entidad_id=str(ED)).count() == 1, "queda en bitácora como inicio automático")
    check(not db.query(Bitacora).filter_by(accion="documentos_solicitados").count(), "expediente al 100 %: no se vuelve a pedir ningún documento")
    tareas = client.get(f"/onboarding/expedientes/{ED}/tareas", headers=H_DEMO).json()
    check(next(t for t in tareas if t["clave"] == "contrato_firmado")["estado"] == "realizada", "la tarea «Contrato firmado» nace Realizada")
    check(client.post(f"/firmas/publica/{TOK}/{FD}/demo/firmar", json={"texto": "Diana"}).json()["yaFirmado"], "volver a firmar: estado limpio, nunca un error")

    # ================= 3b. Firmas · modo Papel =================
    print("\n--- 3b. «Firmar documentos» en modo Papel ---")
    PP, EP, _ = candidato(real, v2, "Pablo Papel", "pablo@real.mx")
    r = client.post(f"/firmas/expedientes/{EP}/documentos", headers=H_REAL)
    check(r.status_code == 200 and r.json()["modo"] == "papel" and r.json()["pdf"].endswith("/documentos.pdf"), "modo Papel → PDF para imprimir")
    r = client.get(f"/firmas/expedientes/{EP}/documentos.pdf", headers=H_REAL)
    check(r.status_code == 200 and r.content[:4] == b"%PDF" and len(PdfReader(io.BytesIO(r.content)).pages) >= 2, "el PDF une carta de intención y contrato")
    r = client.post(f"/onboarding/expedientes/{EP}/contrato-firmado", headers=H_REAL, files={"archivo": ("firmados.pdf", pdf_simple(), "application/pdf")})
    check(r.status_code == 200 and post(PP).etapa == "Onboarding", "RH sube el PDF firmado → el candidato pasa solo a Onboarding")

    # ================= 4. Estados de la ruta =================
    print("\n--- 4. Estados de la ruta ---")
    s = client.get(f"/procesos/postulaciones/{PD}", headers=H_DEMO).json()
    check(not s.get("recomendacion") or "contrataci" not in (s["recomendacion"].get("texto") or "").lower(), "en Onboarding no hay «Avanzar a contratación»")
    ficha = client.get(f"/candidatos/{PD}", headers=H_DEMO).json()
    check(ficha.get("recomendacionRedHuman") in (None, "No avanzar"), "la recomendación de Red Human de etapas anteriores no se muestra en Onboarding")
    from app.services.proceso import _cuello_evaluacion

    ev = Evaluacion(tipo="entrevista_humana", forma="asignada", estado="pendiente", consentimiento="no_requerido",
                    evaluador_nombre="Ana Entrevistadora", evaluador_correo="ana@x.mx", cita_fecha_hora=datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc))
    c = _cuello_evaluacion(ev)
    check(c["clave"] == "pendiente_resultado" and c["texto"].startswith("Programada") and "Esperando" not in c["texto"],
          f"con evaluador y cita: «{c['texto']}» (nunca «Esperando entrevistador»)")
    ev.evaluador_nombre, ev.evaluador_correo = "", ""
    check(_cuello_evaluacion(ev)["texto"] == "Falta asignar entrevistador", "sin evaluador: «Falta asignar entrevistador»")
    paso = {"id": "eh", "tipo": "entrevista_humana", "regla": {"tipo": "validacion"}}
    ev2 = Evaluacion(tipo="entrevista_humana", forma="asignada", estado="con_resultado", conclusion="avanzar", consentimiento="no_requerido",
                     realizada_por="Ana Entrevistadora", codigo="EVA-1")
    r = sproc._paso_evaluacion(paso, ev2)
    check(r["cumple"] and "revisión de RH" not in r["espera"], "«Avanzar» del entrevistador cumple sin esperar la revisión de RH")
    ev2.conclusion = "favorable"
    check(not sproc._paso_evaluacion(paso, ev2)["cumple"], "otra conclusión con regla de validación sigue esperando a RH")
    p = post(PD)
    p.proceso = {**p.proceso, "pasos": [{"id": "pref", "tipo": "prefiltro_whatsapp", "nombre": "Prefiltro", "etapa": "Prefiltro", "obligatorio": False,
                                         "depende_de": [], "responsable": {"tipo": "red_human"}, "regla": {"tipo": "validacion"}, "plazo_dias": None},
                                        *p.proceso["pasos"]]}
    db.commit()
    s = client.get(f"/procesos/postulaciones/{PD}", headers=H_DEMO).json()
    pref = next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == "pref")
    check(pref["estadoUnificado"] == "superada" and pref["estadoUnificadoTexto"] != "En curso" and not pref["disponible"],
          f"en Onboarding una actividad de Prefiltro sin completar aparece «{pref['estadoUnificadoTexto']}», no «En curso»")

    db.close()

print(f"\n🎉 Reestructura del core: {OK} verificaciones OK")
