"""Verificación FASE 5 (2026-09-15): filtro de Colaboradores por Cliente y bienvenida + instrucciones de
ingreso automáticas al dar de alta. Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_fase5_colaboradores.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_f5_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "f5.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-f5"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cliente, Cuenta, Expediente, NotificacionEnviada, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import notificaciones as notif  # noqa: E402
from app.services import whatsapp as _wa  # noqa: E402
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
    ENVIOS.append({"canal": "whatsapp", "destino": telefono, "texto": texto})
    return {"enviado": True, "proveedor": "meta", "detalle": 200}


async def _fake_correo(destino, asunto, html):
    ENVIOS.append({"canal": "correo", "destino": destino, "texto": html, "asunto": asunto})
    return {"enviado": True, "proveedor": "resend", "detalle": "ok"}


notif.enviar_mensaje = _fake_wa
notif.enviar_correo = _fake_correo
_wa.enviar_mensaje = _fake_wa
PDF_MIN = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta F5", nombre_comercial="F5 Reclutadora", estado="Activa", correo_comunicacion="rh@f5.mx", whatsapp_comunicacion="5500001111")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    sol = Cliente(cuenta_id=cuenta.id, nombre="Tiendas Sol", nombre_comercial="Sol Retail", estado="Activo")
    luna = Cliente(cuenta_id=cuenta.id, nombre="Farmacias Luna", nombre_comercial="Luna", estado="Activo")
    db.add_all([sol, luna])
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


    def contratar(nombre, tel, correo, vacante_id, instrucciones=""):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": tel, "correo": correo, "vacante": vacante_id, "consentimiento": True, "fuente": "RH"})
        P = r.json()["id"]
        r = client.patch(f"/candidatos/{P}/etapa?forzar_prueba=true", json={"etapa": "Contratación"})
        EXP = r.json()["expedienteId"]
        r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={
            "puesto": "Cajero", "sueldo": "$10,000 MXN mensuales", "tipo_contratacion": "Indeterminado",
            "fecha_ingreso": "2026-10-01", "ubicacion": "Sucursal Centro", "jefe_directo": "Laura Jefa",
            "instrucciones_ingreso": instrucciones,
        })
        assert r.status_code == 200, r.text
        client.post(f"/contratacion/expedientes/{EXP}/documentos", data={"tipo": "CURP"}, files={"archivo": ("curp.pdf", PDF_MIN, "application/pdf")})
        r = client.post(f"/contratacion/expedientes/{EXP}/alta?forzar_prueba=true", json={})
        assert r.status_code == 200, r.text
        return P, EXP, r.json()

    r = client.post("/vacantes", json={"titulo": "Cajero Sol", "descripcion": "x", "generar_si_falta": False, "cliente_id": sol.id, "mostrar_cliente_candidato": True})
    VAC_SOL = r.json()["id"]
    r = client.post("/vacantes", json={"titulo": "Auxiliar Luna", "descripcion": "x", "generar_si_falta": False, "cliente_id": luna.id})
    VAC_LUNA = r.json()["id"]
    r = client.post("/vacantes", json={"titulo": "Directo", "descripcion": "x", "generar_si_falta": False})
    VAC_DIR = r.json()["id"]

    # ================= Instrucciones de ingreso automáticas =================
    print("\n--- Bienvenida + instrucciones de ingreso al dar de alta ---")
    ENVIOS.clear()
    P1, EXP1, alta = contratar("Ana Sol", "5511111111", "ana@sol.mx", VAC_SOL, "Preséntate el lunes 9:00 en recepción con INE; pregunta por Laura.")
    r = client.get(f"/candidatos/{P1}")
    check(r.json()["expedienteCondiciones"]["instruccionesIngreso"].startswith("Preséntate"), "las instrucciones de ingreso se guardan en el expediente")
    envs = alta["notificaciones"]
    bienvenida = [e for e in envs if e["destinatario"] == "candidato" and e["enviado"]]
    check({e["canal"] for e in bienvenida} == {"whatsapp", "correo"}, f"al dar de alta salen WhatsApp Y correo al candidato automáticamente (sin tocar «Notificar»): {[(e['canal'], e['destino']) for e in bienvenida]}")
    wa = next(e for e in ENVIOS if e["canal"] == "whatsapp" and e["destino"] == "5511111111")
    for k in ("Bienvenido(a)", "Cajero", "Sol Retail", "1 de octubre", "Sucursal Centro", "Laura Jefa", "Preséntate el lunes", "rh@f5.mx"):
        check(k in wa["texto"], f"WhatsApp de bienvenida incluye «{k}»")
    co = next(e for e in ENVIOS if e["canal"] == "correo" and e["destino"] == "ana@sol.mx")
    check("instrucciones de ingreso" in co["asunto"] and "<table" in co["texto"] and "Preséntate el lunes" in co["texto"], "correo de bienvenida con tabla de datos e instrucciones")
    check(sum(1 for e in ENVIOS if e["canal"] == "whatsapp" and e["destino"] == "5511111111") == 1, "UN solo WhatsApp al candidato (el evento `contratacion` no lo duplica)")
    reg = db.query(NotificacionEnviada).filter_by(evento="instrucciones_ingreso").count()
    check(reg == 2, "quedan registrados en notificaciones_enviadas (evento instrucciones_ingreso)")

    # sin instrucciones capturadas → igual sale la bienvenida con los datos
    ENVIOS.clear()
    P2, EXP2, alta2 = contratar("Beto Luna", "5522222222", "beto@luna.mx", VAC_LUNA)
    wa = next(e for e in ENVIOS if e["canal"] == "whatsapp" and e["destino"] == "5522222222")
    check("Instrucciones para tu primer día" not in wa["texto"] and "Fecha de ingreso" in wa["texto"], "sin instrucciones capturadas → bienvenida solo con los datos de ingreso")

    # regla apagada por RH → no sale la bienvenida y `contratacion` conserva su comportamiento
    r = client.patch("/notificaciones/reglas/instrucciones_ingreso", json={"candidato_correo": False, "candidato_whatsapp": False, "entrevistador_correo": False, "entrevistador_whatsapp": False, "cliente_correo": False, "cliente_whatsapp": False})
    check(r.status_code == 200, "RH puede apagar la regla instrucciones_ingreso en Configuración → Notificaciones")
    ENVIOS.clear()
    P3, EXP3, alta3 = contratar("Caro Directa", "5533333333", "caro@dir.mx", VAC_DIR)
    check(db.query(NotificacionEnviada).filter_by(evento="instrucciones_ingreso", candidato_id=db.query(Candidato).filter_by(correo="caro@dir.mx").one().id).count() == 0, "con la regla apagada no se manda la bienvenida")
    check(any(e["canal"] == "whatsapp" and e["destino"] == "5533333333" for e in ENVIOS), "…y el evento `contratacion` sí le avisa al candidato (regla default de contratacion)")

    # ================= Filtro de colaboradores por Cliente =================
    print("\n--- Colaboradores: filtro por Cliente ---")
    r = client.get("/colaboradores")
    check(r.status_code == 200 and len(r.json()) == 3, "GET /colaboradores → 3 colaboradores")
    check(next(c for c in r.json() if c["nombre"] == "Ana Sol")["clienteNombre"] == "Tiendas Sol", "cada colaborador trae clienteId/clienteNombre")
    r = client.get(f"/colaboradores?cliente_id={sol.id}")
    check([c["nombre"] for c in r.json()] == ["Ana Sol"], "filtro cliente_id=Sol → solo Ana")
    r = client.get(f"/colaboradores?cliente_id={luna.id}")
    check([c["nombre"] for c in r.json()] == ["Beto Luna"], "filtro cliente_id=Luna → solo Beto")
    r = client.get("/colaboradores?cliente_id=0")
    check([c["nombre"] for c in r.json()] == ["Caro Directa"], "cliente_id=0 → contratados directo (sin Cliente)")
    r = client.get("/colaboradores/clientes")
    check(r.status_code == 200 and [(c["nombre"], c["colaboradores"]) for c in r.json()] == [("Farmacias Luna", 1), ("Tiendas Sol", 1), ("Directo (sin Cliente)", 1)], f"GET /colaboradores/clientes → opciones del filtro con conteo: {r.json()}")

print(f"\n🎉 FASE 5 verificada: {OK} comprobaciones OK.")
