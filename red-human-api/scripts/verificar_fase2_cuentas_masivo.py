"""Verificación FASE 2 (2026-09-15): Cuentas (baja lógica, restaurar, predeterminada) y cargas masivas
(/auth/usuarios/masivo, /clientes/masivo, /plantillas/masivo) con CSV y Excel. Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_fase2_cuentas_masivo.py
"""

import io
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_f2_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "f2.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-f2"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Usuario, UsuarioCuenta, Vacante  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def csv_bytes(texto: str) -> bytes:
    return ("﻿" + texto).encode("utf-8")


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    ca = Cuenta(nombre="Cuenta A", nombre_comercial="A", estado="Activa")
    cb = Cuenta(nombre="Cuenta B", nombre_comercial="B", estado="Activa")
    db.add_all([ca, cb])
    db.flush()
    db.add_all([UsuarioCuenta(usuario_id=admin.id, cuenta_id=ca.id), UsuarioCuenta(usuario_id=admin.id, cuenta_id=cb.id)])
    for v in db.query(Vacante).all():
        v.cuenta_id = ca.id
    for c in db.query(Candidato).all():
        c.cuenta_id = ca.id
        for p in c.postulaciones:
            p.cuenta_id = ca.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    actual = {"c": ca}
    app.dependency_overrides[cuenta_actual] = lambda: actual["c"]

    # ================= Cuentas =================
    print("\n--- Cuentas: eliminar / restaurar / predeterminada ---")
    r = client.delete(f"/cuentas/{ca.id}")
    check(r.status_code == 409 and "operando" in r.json()["detail"], "no se puede eliminar la Cuenta actual → 409")
    r = client.post(f"/cuentas/{cb.id}/predeterminada")
    check(r.status_code == 200 and r.json()["cuentaPredeterminadaId"] == cb.id, "marcar B como predeterminada")
    r = client.get("/auth/yo")
    check(r.json()["cuentaPredeterminadaId"] == cb.id, "/auth/yo expone cuentaPredeterminadaId")
    r = client.get("/cuentas")
    check([c["esPredeterminada"] for c in r.json()] == [False, True], "GET /cuentas marca esPredeterminada")
    # cuenta_actual real: sin cabecera, con varias Cuentas → la predeterminada
    app.dependency_overrides.pop(cuenta_actual)
    from app.deps import cuenta_actual as _ca_real
    from fastapi import Request
    r = client.get("/cuentas/actual")
    check(r.status_code == 200 and r.json()["id"] == cb.id, "cuenta_actual sin cabecera X-Cuenta-Id → cae en la predeterminada (antes 400)")
    app.dependency_overrides[cuenta_actual] = lambda: actual["c"]

    r = client.delete(f"/cuentas/{cb.id}")
    check(r.status_code == 200 and r.json()["cuenta"]["estado"] == "Eliminada", "eliminar B (baja lógica) → estado Eliminada")
    db.expire_all()
    check(db.get(Cuenta, cb.id) is not None and db.get(Cuenta, cb.id).eliminada_por == admin.nombre, "la fila sigue en la base con quién la eliminó")
    db.refresh(admin)
    check(admin.cuenta_predeterminada_id is None, "al eliminarla deja de ser la predeterminada")
    r = client.get("/cuentas")
    check([c["id"] for c in r.json()] == [ca.id], "GET /cuentas ya no la lista…")
    r = client.get("/cuentas?incluir_eliminadas=true")
    check(any(c["id"] == cb.id and c["estado"] == "Eliminada" for c in r.json()), "…salvo con incluir_eliminadas=true")
    r = client.get("/auth/yo")
    check([c["id"] for c in r.json()["cuentas"]] == [ca.id], "el selector de Cuentas (/auth/yo) tampoco la muestra")
    r = client.delete(f"/cuentas/{cb.id}")
    check(r.status_code == 409, "eliminar dos veces → 409")
    r = client.post(f"/cuentas/{cb.id}/predeterminada")
    check(r.status_code == 409, "una eliminada no puede ser predeterminada → 409")
    actual["c"] = cb
    r = client.delete(f"/cuentas/{ca.id}")
    check(r.status_code == 409 and ("única" in r.json()["detail"] or "operando" in r.json()["detail"]), "no se puede quedar sin Cuenta activa → 409")
    actual["c"] = ca
    r = client.post(f"/cuentas/{cb.id}/restaurar")
    check(r.status_code == 200 and r.json()["estado"] == "Activa", "restaurar B → Activa de nuevo")

    # ================= Cargas masivas =================
    print("\n--- Carga masiva: usuarios ---")
    r = client.get("/auth/usuarios/masivo/plantilla")
    check(r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "correo,nombre" in r.text, "CSV de ejemplo de usuarios")
    csv_us = csv_bytes(
        "Correo;Nombre;Puesto;Teléfono;Rol;Password\n"
        "ana@demo.mx;Ana López;Reclutadora;5512345678;Usuario;\n"
        "beto@demo.mx;Beto Ruiz;Gerente;5598765432;Administrador;ClaveSegura1!\n"
        "malo;Sin Correo;;;Usuario;\n"
        "ana@demo.mx;Ana Duplicada;;;Usuario;\n"
    )
    r = client.post("/auth/usuarios/masivo", files={"archivo": ("usuarios.csv", csv_us, "text/csv")})
    check(r.status_code == 201, f"POST /auth/usuarios/masivo → {r.status_code}")
    d = r.json()
    check(d["creados"] == 2 and d["errores"] == 2, f"2 creados, 2 con error (correo inválido, duplicado): {[f['error'] for f in d['fallas']]}")
    check(any(f["passwordTemporal"] for f in d["filas"] if f["correo"] == "ana@demo.mx"), "sin password → se genera temporal y se regresa")
    check(next(f for f in d["filas"] if f["correo"] == "beto@demo.mx")["rol"] == "Administrador" and not next(f for f in d["filas"] if f["correo"] == "beto@demo.mx")["passwordTemporal"], "con password propia no se regresa; rol Administrador respetado")
    check({f["fila"] for f in d["fallas"]} == {4, 5}, "los errores traen el número de fila del archivo (4 y 5)")
    r = client.get("/auth/usuarios")
    check({u["correo"] for u in r.json()} >= {"ana@demo.mx", "beto@demo.mx"}, "los usuarios quedaron vinculados a la Cuenta actual")

    print("\n--- Carga masiva: clientes (Excel) ---")
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["nombre", "razon_social", "nombre_comercial", "estado", "contacto_nombre", "contacto_apellidos", "contacto_correo", "contacto_telefono"])
    ws.append(["Tiendas Sol", "Tiendas Sol SA", "Sol", "Activo", "Rosa", "Pérez", "rosa@sol.mx", "5511112222"])
    ws.append(["Farmacia Luna", "", "", "Inactivo", "", "", "", ""])
    ws.append(["Tiendas Sol", "", "", "Activo", "", "", "", ""])  # duplicado
    ws.append(["Sin Contacto Válido", "", "", "Activo", "Pepe", "", "", ""])  # contacto sin correo/teléfono
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/clientes/masivo", files={"archivo": ("clientes.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    check(r.status_code == 201, f"POST /clientes/masivo (xlsx) → {r.status_code}")
    d = r.json()
    check(d["creados"] == 2 and d["errores"] == 2, f"2 creados, 2 con error: {[f['error'] for f in d['fallas']]}")
    r = client.get("/clientes")
    sol = next((c for c in r.json() if c["nombre"] == "Tiendas Sol"), None)
    check(sol is not None and sol["contactos"] == 1, "Tiendas Sol creado con su contacto")
    check(any(c["nombre"] == "Farmacia Luna" and c["estado"] == "Inactivo" for c in r.json()), "estado Inactivo respetado")
    check(not any(c["nombre"] == "Sin Contacto Válido" for c in r.json()), "fila con error NO deja el cliente a medias (savepoint)")

    print("\n--- Carga masiva: plantillas ---")
    csv_pl = csv_bytes(
        "nombre,cliente,titulo,area,ubicacion,modalidad,sueldo_desde,sueldo_hasta,sueldo_moneda,sueldo_periodicidad,seniority,requisitos,requisitos_deseables,responsabilidades,beneficios,descripcion,enfoque_entrevista\n"
        "Cajero base,Tiendas Sol,Cajero(a),Operaciones,CDMX,Presencial,9000,11000,MXN,mensual,Junior,Secundaria · Efectivo,Retail | Inglés básico,Cobro | Arqueo,Vales | Seguro,Atención en caja.,profesional\n"
        "Sin cliente,,Vendedor,Ventas,GDL,Híbrido,,,,,Junior,,,,,,profesional_personal\n"
        "Cliente inexistente,Empresa Fantasma,X,,,Presencial,,,,,,,,,,,profesional\n"
        "Enfoque malo,,X,,,Presencial,,,,,,,,,,,ultra\n"
    )
    r = client.post("/plantillas/masivo", files={"archivo": ("plantillas.csv", csv_pl, "text/csv")})
    check(r.status_code == 201, f"POST /plantillas/masivo → {r.status_code}")
    d = r.json()
    check(d["creados"] == 2 and d["errores"] == 2, f"2 creadas, 2 con error: {[f['error'] for f in d['fallas']]}")
    r = client.get("/plantillas")
    caj = next((p for p in r.json() if p["nombre"] == "Cajero base"), None)
    check(caj is not None and caj["clienteId"] == sol["id"] and caj["beneficios"] == ["Vales", "Seguro"], "plantilla ligada al Cliente por nombre; listas separadas por « | »")
    check("9,000" in caj["sueldo"] and "11,000" in caj["sueldo"], f"sueldo estructurado → texto derivado: «{caj['sueldo']}»")
    check(any(p["nombre"] == "Sin cliente" and p["clienteId"] is None and p["enfoqueEntrevista"] == "profesional_personal" for p in r.json()), "plantilla General con enfoque profesional_personal")

    r = client.post("/plantillas/masivo", files={"archivo": ("x.pdf", b"%PDF-1.4 nada", "application/pdf")})
    check(r.status_code == 415, "formato no admitido → 415")
    r = client.post("/clientes/masivo", files={"archivo": ("vacio.csv", b"", "text/csv")})
    check(r.status_code == 422, "archivo vacío → 422")

print(f"\n🎉 FASE 2 verificada: {OK} comprobaciones OK.")
