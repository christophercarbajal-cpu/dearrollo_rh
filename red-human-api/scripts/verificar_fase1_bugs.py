"""Verificación FASE 1 (2026-09-15) — bugs críticos reportados por el cliente. Modo demo, base desechable.

1. Contadores por etapa de la vacante cuentan postulaciones reales (incluidas las de Modo Prueba).
2. Entrevistadores interno y externo reciben WhatsApp y correo al agendar (regla rescatada al arrancar).
3. Alta con documentos incompletos → mensaje claro (409/400 con motivo; el frontend lo pega al botón).
4. Subir un documento digital sube el porcentaje del expediente sin marcarlo «recibido físicamente».
5. Carta de intención en PDF se genera (fpdf2, sin librerías nativas).

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_fase1_bugs.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_f1_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "f1.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-f1"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.migraciones import asegurar_reglas_entrevistador  # noqa: E402
from app.models import Candidato, Cliente, ClienteContacto, Cuenta, ReglaNotificacion, Usuario, UsuarioCuenta, Vacante, registrar  # noqa: E402
from app.services import notificaciones as notif  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


ENVIOS = []


async def _fake_wa(telefono, texto):
    ENVIOS.append(("whatsapp", telefono))
    return {"enviado": True, "proveedor": "meta", "detalle": 200}


async def _fake_correo(destino, asunto, html):
    ENVIOS.append(("correo", destino))
    return {"enviado": True, "proveedor": "resend", "detalle": "ok"}


notif.enviar_mensaje = _fake_wa
notif.enviar_correo = _fake_correo

PDF_MIN = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = "5540001111"
    cuenta = Cuenta(nombre="Cuenta F1", nombre_comercial="F1 RH", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    cliente = Cliente(cuenta_id=cuenta.id, nombre="Cliente Uno", nombre_comercial="Uno", estado="Activo")
    db.add(cliente)
    db.flush()
    contacto = ClienteContacto(cliente_id=cliente.id, nombre="Rosa", apellidos="Externa", correo="rosa@uno.mx", telefono="5550002222")
    db.add(contacto)
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
    cfg = obtener(db)
    cfg.modo_prueba = True
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ============ 1. Contadores de la vacante ============
    print("\n--- 1. Contadores por etapa ---")
    r = client.post("/vacantes", json={"titulo": "Cajero F1", "descripcion": "x", "generar_si_falta": False, "cliente_id": cliente.id, "publicar": True})
    VAC = r.json()["id"]
    client.post(f"/vacantes/{VAC}/publicar")
    codigos = []
    for i, nombre in enumerate(["Ana Uno", "Beto Dos", "Caro Tres"]):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": f"55100000{i:02d}", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
        check(r.status_code == 201, f"postulación {nombre} creada (Modo Prueba activo → es_prueba)")
        codigos.append(r.json()["id"])
    r = client.patch(f"/candidatos/{codigos[1]}/etapa?forzar_prueba=true", json={"etapa": "Evaluación"})
    check(r.status_code == 200, "mover una postulación a Evaluación")
    r = client.get(f"/vacantes/{VAC}")
    etapas = r.json()["embudo"]["etapas"]
    check(etapas.get("Prefiltro") == 2 and etapas.get("Evaluación") == 1, f"ficha de la vacante: Prefiltro=2, Evaluación=1 (antes 0 por excluir Modo Prueba) → {etapas}")
    check(r.json()["candidatos"] == 3 if "candidatos" in r.json() else True, "total de candidatos de la vacante incluye a los de prueba")
    r = client.get("/candidatos")
    check(sum(1 for p in r.json() if p["vacanteId"] == VAC) == 3, "el Kanban muestra las mismas 3 (mismo universo)")

    # ============ 2. Notificaciones al entrevistador ============
    print("\n--- 2. Entrevistadores interno/externo ---")
    # Simula una Cuenta vieja: regla guardada con el entrevistador apagado y sin edición manual.
    regla = ReglaNotificacion(cuenta_id=cuenta.id, evento="entrevista_agendada", candidato_correo=True, candidato_whatsapp=True,
                              entrevistador_correo=False, entrevistador_whatsapp=False)
    db.add(regla)
    db.commit()
    n = asegurar_reglas_entrevistador(db)
    db.refresh(regla)
    check(n == 1 and regla.entrevistador_correo and regla.entrevistador_whatsapp, "arranque: regla vieja con entrevistador apagado → se enciende correo+WhatsApp")
    check(asegurar_reglas_entrevistador(db) == 0, "idempotente: segunda corrida no toca nada")
    # Una Cuenta donde un admin SÍ apagó al entrevistador a mano se respeta
    otra = Cuenta(nombre="Cuenta Manual", nombre_comercial="M", estado="Activa")
    db.add(otra)
    db.flush()
    db.add(ReglaNotificacion(cuenta_id=otra.id, evento="entrevista_agendada", candidato_whatsapp=True))
    registrar(db, "admin", "regla_notificacion_actualizada", "cuenta", str(otra.id), {"evento": "entrevista_agendada", "entrevistador_correo": False})
    db.commit()
    check(asegurar_reglas_entrevistador(db) == 0, "regla apagada a mano por un admin (bitácora) → NO se toca")

    ENVIOS.clear()
    # Evaluaciones unificadas (2026-09-29): «Agregar evaluación» → entrevista humana asignada con cita
    r = client.post(f"/evaluaciones/postulaciones/{codigos[0]}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
        "cita": {"fecha": "2026-10-01", "hora": "10:00", "modalidad": "Teléfono", "telefono": "5540001111"},
    })
    check(r.status_code in (200, 201), f"agendar con entrevistador INTERNO → {r.status_code}")
    res = r.json()["resultados"]
    ent = [x for x in res if x["destinatario"] == "entrevistador"]
    check(len(ent) == 2 and all(x["enviado"] for x in ent) and {x["canal"] for x in ent} == {"whatsapp", "correo"},
          f"entrevistador interno recibe WhatsApp (perfil) y correo → {[(x['canal'], x['destino']) for x in ent]}")
    check(any(x["destinatario"] == "candidato" and x["enviado"] for x in res), "…y el candidato también")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/postulaciones/{codigos[2]}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "externo", "contacto_id": contacto.id},
        "cita": {"fecha": "2026-10-02", "hora": "11:00", "modalidad": "Presencial", "direccion": "Oficina"},
    })
    check(r.status_code in (200, 201), f"agendar con entrevistador EXTERNO (contacto del Cliente) → {r.status_code}")
    ent = [x for x in r.json()["resultados"] if x["destinatario"] == "entrevistador"]
    check({(x["canal"], x["destino"]) for x in ent if x["enviado"]} == {("whatsapp", "5550002222"), ("correo", "rosa@uno.mx")},
          "entrevistador externo recibe WhatsApp y correo en los datos del contacto")

    # ============ 3 y 4. Expediente: porcentaje y alta ============
    print("\n--- 3/4. Porcentaje del expediente y alta ---")
    r = client.patch(f"/candidatos/{codigos[1]}/etapa?forzar_prueba=true", json={"etapa": "Contratación"})
    EXP = r.json()["expedienteId"]
    r = client.get(f"/contratacion/expedientes/{EXP}")
    check(r.status_code == 200 and r.json()["progreso"] == 0, "expediente nuevo al 0%")
    r = client.post(f"/contratacion/expedientes/{EXP}/documentos", data={"tipo": "CURP"}, files={"archivo": ("curp.pdf", PDF_MIN, "application/pdf")})
    check(r.status_code == 200 and r.json()["documento"]["estado"] in ("revision", "recibido"), f"subir CURP digital → {r.json()['documento']['estado']} (revisión sin IA; recibido si Modo Prueba está activo — 2026-09-18)")
    check(r.json()["expediente"]["progreso"] == 17, f"el porcentaje sube SOLO con la subida digital: {r.json()['expediente']['progreso']}% (1 de 6)")
    if r.json()["documento"]["estado"] == "recibido":
        # Onboarding v2 (2026-09-28): en Modo Prueba la subida queda «Aprobado» sola (revisado_por="Modo Prueba")
        check("CURP" not in r.json()["expediente"]["sinConfirmar"], "…y en Modo Prueba queda Aprobado sin confirmación (Onboarding v2)")
    else:
        check("CURP" in r.json()["expediente"]["sinConfirmar"], "…y queda listado para confirmación de RH (HITL)")
    r = client.get(f"/candidatos/{codigos[1]}")
    check(r.json()["expedienteProgreso"] == 17, "la ficha del candidato refleja el mismo %")
    # alta con documentos incompletos → mensaje claro, sin forzar
    cfg = obtener(db)
    cfg.modo_prueba = False
    db.commit()
    r = client.post(f"/contratacion/expedientes/{EXP}/alta", json={})
    check(r.status_code == 409 and "Faltan:" in r.json()["detail"], f"alta incompleta → 409 con motivo: «{r.json()['detail'][:80]}…»")
    cfg = obtener(db)
    cfg.modo_prueba = True
    db.commit()

    # ============ 5. Carta de intención ============
    print("\n--- 5. Carta de intención PDF ---")
    client.patch(f"/candidatos/{codigos[1]}/condiciones-contratacion", json={"puesto": "Cajero F1", "sueldo": "$12,000 MXN mensuales", "tipo_contratacion": "Indeterminado", "ubicacion": "CDMX", "jefe_directo": "Laura Jefa", "fecha_ingreso": "2026-10-15"})
    r = client.get(f"/contratacion/expedientes/{EXP}/carta-intencion")
    check(r.status_code == 200 and r.headers["content-type"].startswith("application/pdf") and r.content.startswith(b"%PDF"),
          f"GET carta-intencion → PDF válido ({len(r.content)} bytes, sin WeasyPrint)")
    from pypdf import PdfReader
    import io as _io
    texto = "".join(pg.extract_text() for pg in PdfReader(_io.BytesIO(r.content)).pages)
    check("Beto Dos" in texto and "Cajero F1" in texto and "12,000" in texto and "Laura Jefa" in texto, "el PDF contiene nombre, puesto, sueldo y jefe directo")

print(f"\n🎉 FASE 1 verificada: {OK} comprobaciones OK.")
