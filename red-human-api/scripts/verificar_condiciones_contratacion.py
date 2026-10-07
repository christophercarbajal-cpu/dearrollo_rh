"""Verificación BLOQUE 2 (2026-09-20): ficha de contratación.
- «Tiempo determinado» → duración (número + unidad) y fecha de término CALCULADA (ingreso + duración).
- «Empresa contratante» → SOLO razones sociales configuradas en la Cuenta (Cuenta + Clientes); texto libre → 400;
  vacío → la de la Cuenta (predeterminada).
- «Guardar condiciones» = PATCH puro: sin mensajes, sin colaborador, sin cambio de etapa.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_condiciones_contratacion.py
"""

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_b2c_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "b2c.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-b2c"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cliente, Colaborador, Cuenta, Mensaje, Usuario, UsuarioCuenta, Vacante, calcular_fecha_termino  # noqa: E402
from app.routers import contratacion as rcont  # noqa: E402
from app.services import notificaciones as sn  # noqa: E402
from app.services import whatsapp as sw  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIOS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _fake_wa(tel, texto, *a, **k):
    ENVIOS.append(("wa", tel, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": 200}


async def _fake_correo(destino, asunto, html, adjuntos=None):
    ENVIOS.append(("correo", destino, asunto))
    return {"enviado": True, "proveedor": "resend", "detalle": 200}


_disparar_real = sn.disparar


async def _disparar_espia(*a, **k):
    ENVIOS.append(("disparar", a[1] if len(a) > 1 else k.get("evento")))
    return await _disparar_real(*a, **k)


sw.enviar_mensaje = _fake_wa
rcont.enviar_mensaje = _fake_wa
rcont.enviar_correo = _fake_correo
sn.disparar = _disparar_espia

print("\n--- 0. Cálculo de fecha de término ---")
f = lambda d: d.date().isoformat()  # noqa: E731
ing = datetime(2026, 1, 31, tzinfo=timezone.utc)
check(f(calcular_fecha_termino(ing, 1, "meses")) == "2026-02-28", "31 ene + 1 mes = 28 feb (calendario)")
check(f(calcular_fecha_termino(datetime(2026, 9, 20, tzinfo=timezone.utc), 90, "días")) == "2026-12-19", "20 sep + 90 días = 19 dic")
check(f(calcular_fecha_termino(datetime(2024, 2, 29, tzinfo=timezone.utc), 1, "años")) == "2025-02-28", "29 feb 2024 + 1 año = 28 feb 2025")
check(f(calcular_fecha_termino(datetime(2026, 3, 15, tzinfo=timezone.utc), 6, "meses")) == "2026-09-15", "15 mar + 6 meses = 15 sep")
check(calcular_fecha_termino(None, 3, "meses") is None and calcular_fecha_termino(ing, 0, "meses") is None and calcular_fecha_termino(ing, 3, "semanas") is None, "sin fecha / duración 0 / unidad inválida → None")

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Grupo CARBE", nombre_comercial="Grupo Carbe", razon_social="Grupo Carbe S.A. de C.V.", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    db.add(Cliente(cuenta_id=cuenta.id, nombre="Distribuidora Norte", razon_social="Distribuidora del Norte S. de R.L.", estado="Activo"))
    db.add(Cliente(cuenta_id=cuenta.id, nombre="Cliente inactivo", razon_social="Inactiva S.A.", estado="Inactivo"))
    db.add(Cliente(cuenta_id=cuenta.id, nombre="Sin razón", razon_social="", estado="Activo"))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    cfg = obtener(db)
    cfg.modo_prueba = False
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- 1. Razones sociales de la Cuenta ---")
    r = client.get("/cuentas/actual/razones-sociales")
    check(r.status_code == 200, "GET /cuentas/actual/razones-sociales")
    rs = r.json()
    check(rs[0]["razonSocial"] == "Grupo Carbe S.A. de C.V." and rs[0]["origen"] == "cuenta" and rs[0]["predeterminada"], "la primera es la razón social de la Cuenta (predeterminada)")
    check([x["razonSocial"] for x in rs] == ["Grupo Carbe S.A. de C.V.", "Distribuidora del Norte S. de R.L."], "solo Clientes ACTIVOS con razón social capturada; nada más")

    print("\n--- 2. Guardar condiciones = PATCH puro ---")
    vac = client.get("/vacantes").json()[0]
    r = client.post("/candidatos", json={"nombre": "Laura Méndez", "telefono": "5512345678", "correo": "laura@correo.mx", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["etapa"] == "Contratación", "candidato en Contratación con expediente abierto")
    ENVIOS.clear()
    n_msgs = db.query(Mensaje).count()
    n_cols = db.query(Colaborador).count()
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={
        "puesto": "Auxiliar", "sueldo": "$12,000 mensuales", "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-10-01",
        "ubicacion": "Querétaro", "jefe_directo": "Ana", "instrucciones_ingreso": "9:00 en recepción",
    })
    check(r.status_code == 200, f"PATCH condiciones (indeterminado) → 200 ({r.status_code}: {r.text[:120]})")
    cond = r.json()["expedienteCondiciones"]
    check(cond["empresa"] == "Grupo Carbe S.A. de C.V.", "empresa vacía → precarga la razón social de la Cuenta")
    check(cond["duracionContrato"] is None and cond["duracionUnidad"] == "" and cond["fechaTermino"] is None, "sin vigencia en Tiempo indeterminado")
    check(cond["completas"], "condiciones completas (puesto, sueldo, tipo, fecha)")
    check(r.json()["etapa"] == "Contratación", "la etapa NO cambia al guardar")
    db.expire_all()
    check(db.query(Mensaje).count() == n_msgs and not [e for e in ENVIOS if e[0] in ("wa", "correo", "disparar")], "no salió ningún mensaje ni notificación")
    check(db.query(Colaborador).count() == n_cols, "no se creó ningún colaborador")

    print("\n--- 3. Tiempo determinado: duración + fecha de término calculada ---")
    base = {"puesto": "Auxiliar", "sueldo": "$12,000 mensuales", "fecha_ingreso": "2026-10-01", "ubicacion": "Querétaro", "jefe_directo": "Ana"}
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={**base, "tipo_contratacion": "Tiempo determinado"})
    check(r.status_code == 400 and "duración" in r.json()["detail"], "Tiempo determinado sin duración → 400")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={**base, "tipo_contratacion": "Tiempo determinado", "duracion_contrato": 3, "duracion_unidad": "semanas"})
    check(r.status_code == 400 and "unidad" in r.json()["detail"], "unidad inválida → 400")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={**base, "tipo_contratacion": "Tiempo determinado", "duracion_contrato": 3, "duracion_unidad": "meses"})
    check(r.status_code == 200, "3 meses → 200")
    cond = r.json()["expedienteCondiciones"]
    check(cond["duracionContrato"] == 3 and cond["duracionUnidad"] == "meses" and (cond["fechaTermino"] or "").startswith("2027-01-01"), "fecha de término = 1 oct 2026 + 3 meses = 1 ene 2027")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={**base, "tipo_contratacion": "Tiempo determinado", "duracion_contrato": 45, "duracion_unidad": "días"})
    check((r.json()["expedienteCondiciones"]["fechaTermino"] or "").startswith("2026-11-15"), "45 días → 15 nov 2026")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={**base, "tipo_contratacion": "Tiempo determinado", "duracion_contrato": 1, "duracion_unidad": "años"})
    check((r.json()["expedienteCondiciones"]["fechaTermino"] or "").startswith("2027-10-01"), "1 año → 1 oct 2027")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={**base, "tipo_contratacion": "Por obra o proyecto", "duracion_contrato": 9, "duracion_unidad": "meses"})
    cond = r.json()["expedienteCondiciones"]
    check(cond["duracionContrato"] is None and cond["fechaTermino"] is None, "al cambiar a otro tipo se limpia la vigencia aunque venga duración")

    print("\n--- 4. Empresa contratante: solo razones sociales de la Cuenta ---")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={**base, "tipo_contratacion": "Tiempo indeterminado", "empresa": "Mi empresa inventada SA"})
    check(r.status_code == 400 and "razón social" in r.json()["detail"], "texto libre → 400")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={**base, "tipo_contratacion": "Tiempo indeterminado", "empresa": "Inactiva S.A."})
    check(r.status_code == 400, "razón social de un Cliente inactivo → 400")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={**base, "tipo_contratacion": "Tiempo indeterminado", "empresa": "distribuidora del norte s. de r.l."})
    check(r.status_code == 200 and r.json()["expedienteCondiciones"]["empresa"] == "Distribuidora del Norte S. de R.L.", "razón social de un Cliente activo → se guarda normalizada")

    print("\n--- 5. Carta y contrato reflejan la empresa y la vigencia ---")
    r = client.patch(f"/candidatos/{P}/condiciones-contratacion", json={**base, "tipo_contratacion": "Tiempo determinado", "duracion_contrato": 6, "duracion_unidad": "meses", "empresa": "Distribuidora del Norte S. de R.L."})
    exp_id = r.json()["expedienteId"]
    r = client.get(f"/contratacion/expedientes/{exp_id}/carta-intencion")
    check(r.status_code == 200 and r.content[:4] == b"%PDF", "carta de intención PDF")
    from app.models import Expediente

    db.expire_all()
    d = rcont._datos_carta_intencion(db.get(Expediente, exp_id))
    check(d["empresa"] == "Distribuidora del Norte S. de R.L." and d["duracion"] == "6 meses" and d["fecha_termino"] == "1 de abril de 2027", "datos de carta/contrato: empresa elegida + duración + término calculado")
    cfg = obtener(db)
    cfg.modo_prueba = True
    db.commit()
    r = client.get(f"/contratacion/expedientes/{exp_id}/contrato")
    check(r.status_code == 200 and r.content[:4] == b"%PDF", "contrato PDF con cláusula de duración (Modo Prueba para saltar el 100 %)")
    db.close()

print(f"\n🎉 Bloque 2 (condiciones de contratación) verificado: {OK} comprobaciones OK.")
