"""Verificación «arranque en vivo» (2026-09-15): sin datos de ejemplo por defecto, limpieza de seed con baja
lógica, colaboradores (perfil, baja, reactivar, eliminar), tablero de Onboarding sin fantasmas y contador
real del agente de WhatsApp. Base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_arranque_en_vivo.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_vivo_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "vivo.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["SEMBRAR_DEMO"] = "false"  # arranque en vivo: SIN datos de ejemplo
os.environ["ADMIN_PASSWORD"] = "prueba-vivo"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Colaborador, Cuenta, Mensaje, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402
from app import seed  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


PDF_MIN = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"

with TestClient(app) as client:
    db = SessionLocal()
    print("\n--- Sin datos de ejemplo por defecto ---")
    check(db.query(Vacante).count() == 0 and db.query(Candidato).count() == 0, "base nueva sin SEMBRAR_DEMO → 0 vacantes y 0 candidatos de ejemplo")
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    check(admin is not None, "…pero el administrador inicial sí se crea")
    cuenta = Cuenta(nombre="Cuenta Viva", nombre_comercial="Viva", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    cfg = obtener(db)
    cfg.modo_prueba = True
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    r = client.get("/vacantes")
    check(r.status_code == 200 and r.json() == [], "GET /vacantes → lista 100% vacía")
    r = client.get("/candidatos")
    check(r.json() == [], "GET /candidatos → vacío")
    r = client.get("/contratacion/expedientes")
    check(r.json() == [], "GET /contratacion/expedientes → vacío")

    print("\n--- Limpieza del seed (baja lógica) ---")
    # Simula una base que SÍ fue sembrada (como la del cliente) y luego se limpia.
    from app.config import settings
    settings.sembrar_demo = True
    seed.sembrar(db)
    db.commit()
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
    db.commit()
    r = client.get("/vacantes")
    check(len(r.json()) == 6, "base sembrada: 6 vacantes de ejemplo visibles")
    sys.path.insert(0, str(RAIZ / "scripts"))
    import limpiar_datos_demo
    import builtins
    builtins.input = lambda *_a, **_k: "SI"
    limpiar_datos_demo.SessionLocal = SessionLocal
    check(limpiar_datos_demo.main(False) == 0, "limpiar_datos_demo dry-run OK")
    check(limpiar_datos_demo.main(True) == 0, "limpiar_datos_demo --forzar aplicado")
    db.expire_all()
    r = client.get("/vacantes")
    check(r.json() == [], "tras limpiar: GET /vacantes vacío (baja lógica, no borrado)")
    check(db.query(Vacante).count() == 6 and all(v.estado == "Eliminada" for v in db.query(Vacante).all()), "las 6 filas siguen en la base como Eliminada")
    r = client.get("/candidatos?mostrar_cerradas=true")
    check(r.json() == [], "tras limpiar: Kanban vacío incluso con «Mostrar cerradas»")
    check(db.query(Candidato).count() >= 11 and all(c.eliminado_en for c in db.query(Candidato).all()), "los candidatos de ejemplo quedan con eliminado_en (fila conservada)")
    check(limpiar_datos_demo.main(True) == 0, "segunda corrida: nada que limpiar (idempotente)")

    print("\n--- Colaboradores: perfil, baja, reactivar, eliminar ---")
    r = client.post("/vacantes", json={"titulo": "Cajero Vivo", "descripcion": "x", "generar_si_falta": False})
    VAC = r.json()["id"]
    r = client.post("/candidatos", json={"nombre": "Nora Nueva", "telefono": "5511110000", "correo": "nora@x.mx", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    r = client.patch(f"/candidatos/{P}/etapa?forzar_prueba=true", json={"etapa": "Contratación"})
    EXP = r.json()["expedienteId"]
    client.patch(f"/candidatos/{P}/condiciones-contratacion", json={"puesto": "Cajero", "sueldo": "$9,000", "tipo_contratacion": "Indeterminado", "fecha_ingreso": "2026-10-01", "ubicacion": "Centro", "jefe_directo": "Laura", "instrucciones_ingreso": "Llega a las 9."})
    client.post(f"/contratacion/expedientes/{EXP}/documentos", data={"tipo": "CURP"}, files={"archivo": ("curp.pdf", PDF_MIN, "application/pdf")})
    r = client.post(f"/contratacion/expedientes/{EXP}/alta?forzar_prueba=true", json={})
    COL = r.json()["colaborador"]["id"]
    r = client.get(f"/colaboradores/{COL}")
    d = r.json()
    check(r.status_code == 200 and d["nombre"] == "Nora Nueva" and d["tipoContratacion"] == "Indeterminado" and d["instruccionesIngreso"] == "Llega a las 9.", "GET /colaboradores/{codigo} → perfil completo (condiciones + instrucciones)")
    check(d["expediente"] and d["expediente"]["expedienteId"] == EXP and any(x["nombre"] == "CURP" and x["tieneArchivo"] for x in d["expediente"]["documentos"]), "el perfil trae el expediente con sus documentos")
    check(d["candidatoOrigen"]["nombre"] == "Nora Nueva" and d["vacante"]["codigo"] == VAC, "candidato de origen y vacante")
    r = client.post(f"/colaboradores/{COL}/baja", json={"motivo": "Renuncia"})
    check(r.status_code == 200 and r.json()["activo"] is False and r.json()["bajaMotivo"] == "Renuncia" and r.json()["bajaPor"] == admin.nombre, "baja → inactivo con motivo, fecha y quién")
    r = client.get("/colaboradores")
    check(any(c["id"] == COL and c["estatus"] == "Inactivo" for c in r.json()), "sigue en la lista como Inactivo (historial conservado)")
    r = client.post(f"/colaboradores/{COL}/baja", json={})
    check(r.status_code == 409, "baja dos veces → 409")
    r = client.post(f"/colaboradores/{COL}/reactivar")
    check(r.status_code == 200 and r.json()["activo"] is True and r.json()["bajaEn"] is None, "reactivar → activo de nuevo")
    r = client.delete(f"/colaboradores/{COL}")
    check(r.status_code == 200, "DELETE /colaboradores/{codigo} → eliminación lógica")
    r = client.get("/colaboradores")
    check(not any(c["id"] == COL for c in r.json()), "ya no aparece en la lista")
    r = client.get("/colaboradores/clientes")
    check(r.json() == [], "…ni en los conteos del filtro por Cliente")
    r = client.get(f"/colaboradores/{COL}")
    check(r.status_code == 404, "GET del eliminado → 404")
    check(db.query(Colaborador).filter_by(codigo=COL).one().eliminado_en is not None, "la fila se conserva con eliminado_en")
    r = client.delete(f"/candidatos/{P}")
    check(r.status_code == 200, "con el colaborador eliminado, el candidato de origen ya se puede eliminar")

    print("\n--- Onboarding sin fantasmas ---")
    def a_contratacion(nombre, tel):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": tel, "vacante": VAC, "consentimiento": True, "fuente": "RH"})
        p = r.json()["id"]
        client.patch(f"/candidatos/{p}/etapa?forzar_prueba=true", json={"etapa": "Contratación"})
        return p
    PA = a_contratacion("Activo Uno", "5522220001")
    PB = a_contratacion("Descartado Dos", "5522220002")
    PC = a_contratacion("Eliminado Tres", "5522220003")
    client.post(f"/contratacion/expedientes/{client.get(f'/candidatos/{PB}').json()['expedienteId']}/cancelar", json={"motivo": "x"}) if False else None
    # PB: se descarta (cierra la postulación); el expediente abierto no debe salir en el tablero
    from app.models import Postulacion
    pb = db.query(Postulacion).filter_by(codigo=PB).one()
    pb.cerrar("descartado")
    db.commit()
    client.delete(f"/candidatos/{PC}")
    r = client.get("/contratacion/expedientes")
    nombres = [e["nombre"] for e in r.json()]
    check("Activo Uno" in nombres and "Descartado Dos" not in nombres and "Eliminado Tres" not in nombres, f"Onboarding solo lista expedientes vivos: {nombres}")

    print("\n--- Contador real del agente ---")
    r = client.get("/candidatos/agente/actividad")
    check(r.status_code == 200 and r.json()["prefiltrando"] == 0, "sin conversaciones → prefiltrando=0")
    r = client.post("/candidatos", json={"nombre": "Chat Activo", "telefono": "5533330001", "vacante": VAC, "consentimiento": True, "fuente": "WhatsApp"})
    p1 = db.query(Postulacion).filter_by(codigo=r.json()["id"]).one()
    p1.candidato.wa_id = "5215533330001"
    db.add(Mensaje(candidato_id=p1.candidato_id, postulacion_id=p1.id, rol="user", texto="hola", canal="whatsapp", enviado=True))
    r = client.post("/candidatos", json={"nombre": "Chat Viejo", "telefono": "5533330002", "vacante": VAC, "consentimiento": True, "fuente": "WhatsApp"})
    p2 = db.query(Postulacion).filter_by(codigo=r.json()["id"]).one()
    p2.candidato.wa_id = "5215533330002"
    db.add(Mensaje(candidato_id=p2.candidato_id, postulacion_id=p2.id, rol="user", texto="hola", canal="whatsapp", enviado=True, creado_en=datetime.now(timezone.utc) - timedelta(days=3)))
    db.commit()
    r = client.get("/candidatos/agente/actividad")
    check(r.json()["prefiltrando"] == 1 and r.json()["enPrefiltro"] == 2, f"1 con sesión de WhatsApp activa (24 h), 2 en Prefiltro → {r.json()}")

print(f"\n🎉 Arranque en vivo verificado: {OK} comprobaciones OK.")
