"""Regresión del Tablero de control principal (2026-09-28): datos REALES y aislamiento estricto por Cuenta.

    .venv/Scripts/python.exe scripts/verificar_tablero.py

Dos Cuentas con datos distintos: cada tablero cuenta SOLO lo suyo (candidatos activos, colaboradores, fuentes,
recientes, Onboarding con tareas atrasadas y avance, evaluaciones por estado (modelo unificado), ciclos de
Desempeño activos y Clima). Una Cuenta vacía muestra ceros / series vacías, nunca datos inventados.
"""

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

_dir = tempfile.mkdtemp(prefix="rh_tab_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "tab.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-tab"
os.environ["SEMBRAR_DEMO"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Colaborador, Cuenta, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    A = Cuenta(nombre="Cuenta A", nombre_comercial="Empresa A", razon_social="Empresa A SA", estado="Activa")
    B = Cuenta(nombre="Cuenta B", nombre_comercial="Empresa B", razon_social="Empresa B SA", estado="Activa")
    V = Cuenta(nombre="Cuenta vacía", nombre_comercial="Vacía", estado="Activa")
    db.add_all([A, B, V])
    db.flush()
    for c in (A, B, V):
        db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=c.id))
    va = Vacante(codigo="VAC-A1", titulo="Cajero A", cuenta_id=A.id, estado="Publicada", slug="cajero-a")
    vb = Vacante(codigo="VAC-B1", titulo="Chofer B", cuenta_id=B.id, estado="Publicada", slug="chofer-b")
    db.add_all([va, vb])
    db.add(Colaborador(codigo="COL-A1", cuenta_id=A.id, nombre="Ana A", puesto="Cajero A", area="Ventas"))
    db.add(Colaborador(codigo="COL-A2", cuenta_id=A.id, nombre="Beto A", puesto="Cajero A", area="Ventas", activo=False))
    for i in range(3):
        db.add(Colaborador(codigo=f"COL-B{i}", cuenta_id=B.id, nombre=f"Col B{i}", puesto="Chofer B"))
    obtener(db).modo_prueba = False
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin

    def en(cuenta):
        app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ---------- Cuenta A ----------
    en(A)
    P = {}
    for nombre, tel, fuente in (("Lucía A", "5510000001", "RH"), ("Mario A", "5510000002", "RH"), ("Nora A", "5510000003", "RH")):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": tel, "correo": f"{tel}@a.mx", "vacante": "VAC-A1", "consentimiento": True, "fuente": fuente})
        P[nombre] = r.json()["id"]
    client.patch(f"/candidatos/{P['Mario A']}/etapa", json={"etapa": "Evaluación", "manual": True})
    # evaluaciones (modelo unificado): una Pendiente, una Realizada · Resultado pendiente y una Con resultado
    client.post(f"/evaluaciones/postulaciones/{P['Mario A']}", json={"tipo": "referencias", "forma": "registro_directo"})
    ev = client.post(f"/evaluaciones/postulaciones/{P['Mario A']}", json={"tipo": "tecnica", "forma": "registro_directo"}).json()["evaluacion"]
    client.post(f"/evaluaciones/{ev['id']}/realizada")
    ev3 = client.post(f"/evaluaciones/postulaciones/{P['Mario A']}", json={"tipo": "socioeconomica", "forma": "registro_directo"}).json()["evaluacion"]
    client.post(f"/evaluaciones/{ev3['id']}/resultado", data={"comentarios": "Visita realizada", "version": "0"})
    # onboarding activo con una tarea atrasada
    exp = client.patch(f"/candidatos/{P['Nora A']}/etapa", json={"etapa": "Contratación", "manual": True}).json()["expedienteId"]
    client.patch(f"/candidatos/{P['Nora A']}/condiciones-contratacion", json={"puesto": "Cajero A", "sueldo": "$10,000", "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-12-01"})
    res = client.get(f"/onboarding/expedientes/{exp}/resumen").json()
    r = client.post(f"/onboarding/expedientes/{exp}/iniciar", json={"documentos": res["configuracion"]["documentos"], "notificar_responsables": False, "solicitar_documentos": False})
    check(r.status_code == 200, f"Cuenta A: Onboarding iniciado ({r.status_code})")
    client.post(f"/onboarding/expedientes/{exp}/tareas", json={"nombre": "Credencial", "dias": -500})
    # desempeño: un ciclo en curso con 1 persona
    c = client.post("/desempeno/ciclos", json={"nombre": "Cajeros 2026", "periodo": "2026-S2", "equipo": "Cajeros",
                                              "criterios": [{"tipo": "medible", "nombre": "Ventas", "unidad": "$", "meta": 100, "sentido": "mayor_es_mejor"}]}).json()
    client.post(f"/desempeno/ciclos/{c['id']}/participantes", json={"colaborador_ids": ["COL-A1"]})
    check(client.post(f"/desempeno/ciclos/{c['id']}/iniciar").json()["estado"] == "en_curso", "Cuenta A: ciclo de Desempeño en curso")

    # ---------- Cuenta B ----------
    en(B)
    for i in range(5):
        client.post("/candidatos", json={"nombre": f"Persona B{i}", "telefono": f"55200000{i:02d}", "vacante": "VAC-B1", "consentimiento": True, "fuente": "RH"})

    print("\n--- 1. Tablero de la Cuenta A: solo sus datos ---")
    en(A)
    t = client.get("/metricas/tablero").json()
    check(t["cuentaId"] == A.id, "el tablero es el de la Cuenta de la sesión")
    k = t["kpis"]
    check(k["candidatosActivos"] == 3, f"candidatos activos en el pipeline = 3 (no los 5 de B): {k['candidatosActivos']}")
    check(k["colaboradoresActivos"] == 1, f"colaboradores activos = 1 (el dado de baja no cuenta; los 3 de B tampoco): {k['colaboradoresActivos']}")
    check(k["vacantesPublicadas"] == 1, "vacantes publicadas de la Cuenta")
    hoy = date.today().isoformat()
    dia_hoy = next((d for d in t["actividad"] if d["fecha"] == hoy), None) or t["actividad"][-1]
    check(len(t["actividad"]) == 7 and dia_hoy["candidatos"] == 3, f"actividad real de 7 días; hoy 3 postulaciones nuevas ({t['actividad'][-1]})")
    check(sum(f["value"] for f in t["fuentes"]) == 3, f"fuentes = postulaciones activas de A ({t['fuentes']})")
    check({r["nombre"] for r in t["recientes"]} == {"Lucía A", "Mario A", "Nora A"}, "candidatos recientes solo de A")
    o = t["onboarding"]
    check(o["activos"] == 1 and o["tareasAtrasadas"] == 1 and o["avancePromedio"] is not None and 0 <= o["avancePromedio"] <= 100,
          f"Onboarding: 1 activo, 1 tarea atrasada, avance promedio real ({o})")
    e = t["evaluaciones"]
    check(e["pendientes"] == 1 and e["realizadasSinResultado"] == 1 and e["conResultado"] == 1 and e["nuevosResultados"] == 0,
          f"evaluaciones: 1 pendiente, 1 con resultado pendiente, 1 con resultado (capturado por RH: no es «nuevo») ({e})")
    d = t["desempeno"]
    check(d["activos"] == 1 and d["personasIncluidas"] == 1 and d["personasCompletadas"] == 0 and d["avance"] == 0,
          f"Desempeño: 1 ciclo activo, 0 de 1 completadas ({d['activos']}, {d['avance']} %)")
    check(t["clima"] == {"abiertas": 0, "borradores": 0, "cerradas": 0}, "Clima: sin mediciones")
    check(t["tiempoContratacion"]["serie"] == [] and t["tiempoContratacion"]["promedio"] is None, "sin altas: el tiempo de contratación viene vacío (nunca inventado)")

    print("\n--- 2. Tablero de la Cuenta B ---")
    en(B)
    t = client.get("/metricas/tablero").json()
    check(t["kpis"]["candidatosActivos"] == 5 and t["kpis"]["colaboradoresActivos"] == 3, "B ve sus 5 candidatos y 3 colaboradores")
    check(t["onboarding"]["activos"] == 0 and t["onboarding"]["tareasAtrasadas"] == 0 and t["onboarding"]["avancePromedio"] is None, "B no ve el Onboarding de A")
    check(t["evaluaciones"]["pendientes"] == 0 and t["evaluaciones"]["conResultado"] == 0, "B no ve las evaluaciones de A")
    check(t["desempeno"]["activos"] == 0, "B no ve los ciclos de A")
    check(all(r["nombre"].startswith("Persona B") for r in t["recientes"]), "recientes solo de B")

    print("\n--- 3. Cuenta vacía: ceros, nunca mocks ---")
    en(V)
    t = client.get("/metricas/tablero").json()
    check(t["kpis"] == {"candidatosActivos": 0, "candidatosNuevos7d": 0, "colaboradoresActivos": 0, "altas30d": 0, "vacantesPublicadas": 0}, "KPIs en cero")
    check(t["fuentes"] == [] and t["recientes"] == [] and all(x["candidatos"] == 0 and x["entrevistas"] == 0 for x in t["actividad"]), "series vacías")
    check(t["pendientesRH"] == [], "sin pendientes inventados")

    print("\n--- 4. Alta real → contrataciones y tiempo de contratación ---")
    en(A)
    cfg = obtener(db)
    cfg.modo_prueba = True
    db.commit()
    r = client.post(f"/contratacion/expedientes/{exp}/alta", json={"notificar": {"candidato_whatsapp": False, "candidato_correo": False}})
    check(r.status_code == 200, f"alta en Modo Prueba ({r.status_code})")
    cfg.modo_prueba = False
    db.commit()
    t = client.get("/metricas/tablero").json()
    check(t["kpis"]["altas30d"] == 1 and t["kpis"]["colaboradoresActivos"] == 2, "la contratación suma a altas (30 días) y a colaboradores")
    check(len(t["tiempoContratacion"]["serie"]) == 1 and t["tiempoContratacion"]["promedio"] == 0, f"tiempo de contratación real ({t['tiempoContratacion']})")
    check(t["kpis"]["candidatosActivos"] == 2, "la contratada ya no cuenta como candidata activa del pipeline")
    en(B)
    check(client.get("/metricas/tablero").json()["kpis"]["altas30d"] == 0, "la alta de A no aparece en B")

    print("\n--- 5. Sin mocks en el frontend ---")
    app_dir = RAIZ.parent / "red-human-app"
    pagina = (app_dir / "app" / "dashboard" / "page.tsx").read_text(encoding="utf-8")
    datos = (app_dir / "lib" / "data.ts").read_text(encoding="utf-8")
    import re as _re  # noqa: E402

    imports_valor = [l for l in pagina.splitlines() if "@/lib/data" in l and not _re.match(r"\s*import type ", l)]
    check(not imports_valor and "fetchTablero" in pagina, "el Tablero ya no importa datos de lib/data (usa /metricas/tablero; solo tipos)")
    for mock in ("export const kpis", "export const actividadData", "export const fuentesData", "export const tiempoContratacion", "export const funnelData", "export const candidatos:"):
        check(mock not in datos, f"eliminado de lib/data.ts: {mock}")
    check("Math.random" not in pagina, "sin generadores aleatorios")

    db.close()

print(f"\n🎉 Tablero de control: {OK} verificaciones OK")
