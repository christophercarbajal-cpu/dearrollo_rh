"""Verificación BLOQUE 3 (2026-09-19, cambios Raúl): condiciones de contratación con empresa, carta (envío por
WhatsApp/correo con PDF adjunto), contrato solo con expediente al 100 %, y «alta perfecta» (Colaborador toma
estrictamente las condiciones finales guardadas + snapshot inmutable). Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_bloque3_contratacion.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_b3_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "b3.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-b3"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Colaborador, Cuenta, Expediente, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers import contratacion as rcont  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
CORREOS = []
WA = []
PDF_MIN = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _fake_correo(destino, asunto, html, adjuntos=None):
    CORREOS.append((destino, asunto, html, adjuntos or []))
    return {"enviado": True, "proveedor": "resend", "detalle": 200}


async def _fake_wa(tel, texto):
    WA.append((tel, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": 200}


rcont.enviar_correo = _fake_correo
rcont.enviar_mensaje = _fake_wa

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Grupo CARBE", nombre_comercial="Grupo Carbe", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    cfg = obtener(db)
    cfg.modo_prueba = False
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    vac = client.get("/vacantes").json()[0]
    r = client.post("/candidatos", json={"nombre": "Laura Méndez", "telefono": "5512345678", "correo": "laura@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    EXP = r.json()["expedienteId"]

    print("\n--- 1. Flujo lineal: sin condiciones no hay contrato ni alta ---")
    r = client.get(f"/candidatos/{P}")
    check(r.json()["expedienteCondiciones"]["completas"] is False, "condiciones incompletas al abrir el expediente")
    check(client.get(f"/contratacion/expedientes/{EXP}/contrato").status_code == 409, "contrato bloqueado (expediente al 0 % / sin condiciones)")
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Onboarding", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    client.post(f"/contratacion/expedientes/{EXP}/documentos", data={"tipo": "CURP"}, files={"archivo": ("curp.pdf", PDF_MIN, "application/pdf")})  # con un adjunto, el gate que sigue es el de condiciones
    r = client.post(f"/contratacion/expedientes/{EXP}/alta", json={})
    check(r.status_code == 409 and "condiciones" in r.json()["detail"].lower(), "alta bloqueada hasta capturar las condiciones (Modo Prueba apagado)")

    print("\n--- 2. Capturar condiciones → guardadas con empresa ---")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={
        "puesto": "Abogada Fiscalista Sr.", "sueldo": "$32,000 mensuales netos", "tipo_contratacion": "Indeterminado", "fecha_ingreso": "2026-10-01",
        "ubicacion": "Zapopan, Jalisco", "jefe_directo": "Mariana López", "empresa": "", "instrucciones_ingreso": "Llega 8:45 a recepción.",
    })
    check(r.status_code == 200 and r.json()["expedienteCondiciones"]["completas"] is True, "condiciones guardadas y completas")
    cond = r.json()["expedienteCondiciones"]
    check(cond["empresa"] and cond["guardadasEn"], f"empresa por default = la razón social de la Cuenta (B2) («{cond['empresa']}») y fecha de captura")

    print("\n--- 3. Carta: vista previa + WhatsApp + correo con PDF adjunto ---")
    r = client.get(f"/contratacion/expedientes/{EXP}/carta-intencion")
    check(r.status_code == 200 and r.content[:4] == b"%PDF", "carta PDF con las condiciones")
    WA.clear(); CORREOS.clear()
    r = client.post(f"/contratacion/expedientes/{EXP}/carta-intencion/enviar", json={"canal": "whatsapp"})
    check(r.status_code == 200 and r.json()["enviado"] is True and WA and "Abogada Fiscalista Sr." in WA[-1][1] and "/expediente/" in WA[-1][1], "WhatsApp con las condiciones y la liga a su expediente")
    r = client.post(f"/contratacion/expedientes/{EXP}/carta-intencion/enviar", json={"canal": "correo"})
    check(r.status_code == 200 and CORREOS and CORREOS[-1][0] == "laura@correo.mx" and CORREOS[-1][3] and CORREOS[-1][3][0]["filename"] == "carta-intencion.pdf" and CORREOS[-1][3][0]["content"][:4] == b"%PDF", "correo HTML con el PDF adjunto")
    db.expire_all()
    e = db.query(Expediente).get(EXP)
    r = client.get(f"/expedientes/publica/{e.token}")
    check(r.json()["cartaDisponible"] is True and client.get(f"/expedientes/publica/{e.token}/carta-intencion").content[:4] == b"%PDF", "el candidato descarga la carta desde su liga pública")

    print("\n--- 4. Contrato solo con documentos completos ---")
    check(client.get(f"/contratacion/expedientes/{EXP}/contrato").status_code == 409, "contrato bloqueado con documentos pendientes")
    for d in list(e.documentos):
        if d.obligatorio:
            client.post(f"/contratacion/expedientes/{EXP}/documentos", data={"tipo": d.tipo}, files={"archivo": (f"{d.tipo}.pdf", PDF_MIN, "application/pdf")})
            client.post(f"/contratacion/expedientes/{EXP}/documentos/estado", json={"tipo": d.tipo, "estado": "recibido"})  # confirmación de RH
    r = client.get(f"/contratacion/expedientes/{EXP}")
    check(r.json()["progreso"] == 100, "expediente al 100 %")
    r = client.get(f"/contratacion/expedientes/{EXP}/contrato")
    check(r.status_code == 200 and r.content[:4] == b"%PDF" and len(r.content) > 2500, "contrato PDF generado con las condiciones finales")

    print("\n--- 5. Alta perfecta: Colaborador con las condiciones finales + snapshot inmutable ---")
    # Onboarding v2 (2026-09-28): a Onboarding se entra con «Iniciar Onboarding» y el alta exige «Confirmar ingreso»
    # 2026-10-09: «Firmar documentos» (aquí en papel: RH sube el PDF firmado) → el candidato pasa SOLO a Onboarding
    r = client.post(f"/onboarding/expedientes/{EXP}/contrato-firmado", files={"archivo": ("firmados.pdf", PDF_MIN, "application/pdf")})
    check(r.status_code == 200 and client.get(f"/candidatos/{P}").json()["etapa"] == "Onboarding",
          f"documentos firmados → Onboarding automático ({r.status_code} {r.text[:160]})")
    r = client.post(f"/contratacion/expedientes/{EXP}/alta", json={})
    check(r.status_code == 409 and "Confirmar ingreso" in r.json()["detail"], "sin «Confirmar ingreso» no hay alta (Onboarding v2)")
    client.post(f"/onboarding/expedientes/{EXP}/confirmar-ingreso", json={"fecha_real": __import__("datetime").date.today().isoformat()})
    r = client.post(f"/contratacion/expedientes/{EXP}/alta", json={"notificar": {"candidato_whatsapp": False, "candidato_correo": False}})
    check(r.status_code == 200, f"alta autorizada ({r.status_code})")
    db.expire_all()
    col = db.query(Colaborador).filter(Colaborador.expediente_id == EXP).one()
    check(col.puesto == "Abogada Fiscalista Sr." and col.salario == "$32,000 mensuales netos" and col.tipo_contratacion == "Indeterminado", "puesto, sueldo y tipo de contrato = los guardados (no los de la vacante)")
    check(col.ubicacion == "Zapopan, Jalisco" and col.jefe_directo == "Mariana López" and col.empresa == cond["empresa"] and col.fecha_ingreso is not None, "ubicación, jefe, empresa y fecha de ingreso completos")
    snap = col.condiciones_ingreso
    check(snap["puesto"] == col.puesto and snap["sueldo"] == col.salario and snap["alta_por"] == admin.nombre and snap["alta_en"] and snap["expediente"] == EXP, "snapshot inmutable de ingreso con quién/cuándo")
    r = client.get(f"/colaboradores/{col.codigo}")
    check(r.status_code == 200 and r.json()["tipoContratacion"] == "Indeterminado" and r.json()["condicionesIngreso"]["sueldo"] == col.salario, "la API expone tipoContratacion y condicionesIngreso")

    db.close()

print(f"\n🎉 Bloque 3 verificado: {OK} comprobaciones OK.")
