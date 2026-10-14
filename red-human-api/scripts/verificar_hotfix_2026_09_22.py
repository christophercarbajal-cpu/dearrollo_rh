"""Verificación HOTFIX 2026-09-22.
1) Candidatos «no cumple» del prefiltro siguen ACTIVOS y visibles: la API los regresa en `GET /candidatos`
   (el tablero ya no los esconde) y cuadran con los contadores de la vacante (B4).
2) `POST /capacitacion/{codigo}/asignar` NUNCA regresa 500: aunque el aviso por WhatsApp/correo truene
   (proveedor caído, curso legado sin modalidad/duración, detalle no serializable), la asignación se guarda
   y el error viaja en el resultado que ve RH.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_hotfix_2026_09_22.py
"""

import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_hf922_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "hf.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-hf"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Colaborador, Cuenta, Curso, Postulacion, Usuario, UsuarioCuenta, Vacante, registrar  # noqa: E402
from app.routers import capacitacion as rcap  # noqa: E402
from app.serial import texto_duracion_curso  # noqa: E402

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
    cuenta = Cuenta(nombre="Cuenta HF", nombre_comercial="HF RH", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    db.add(Colaborador(codigo="COL-1", cuenta_id=cuenta.id, nombre="Juan Pérez", correo="juan@correo.mx", telefono="5512345678", puesto="Cajero"))
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    print("\n--- 1. «No cumple» del prefiltro NO desaparece ---")
    vac = client.get("/vacantes").json()[0]
    codigos = []
    for i, nombre in enumerate(["Ana Apta", "Beto NoCumple", "Caro Revisión"]):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": f"551100000{i}", "vacante": vac["id"], "consentimiento": True, "fuente": "RH"})
        codigos.append(r.json()["id"])
    db.expire_all()
    for cod, estado in zip(codigos, ["cumple", "no_cumple", "revision"]):
        p = db.query(Postulacion).filter_by(codigo=cod).one()
        p.estado = estado  # recomendación del agente; la postulación sigue ACTIVA
    db.commit()
    lista = client.get("/candidatos").json()
    check(len(lista) == 3 and all(x["etapa"] == "Prefiltro" for x in lista), f"la API regresa las 3 postulaciones activas en Prefiltro ({len(lista)})")
    check(any(x["estado"] == "no_cumple" for x in lista), "la marcada «no cumple» sigue activa y visible en la lista")
    v1 = client.get(f"/vacantes/{vac['id']}").json()
    check(v1["candidatos"] == 3 and v1["embudo"]["etapas"].get("Prefiltro") == 3, f"contadores de la vacante = 3 en Prefiltro ({v1['embudo']['etapas']})")
    kan = Counter(x["etapa"] for x in client.get(f"/candidatos?vacante={vac['id']}").json())
    check(dict(kan) == v1["embudo"]["etapas"], "tarjetas == contadores (B4): ninguna se queda invisible")
    r = client.post(f"/candidatos/{codigos[1]}/decision", json={"accion": "descartar", "comentario": "RH decide"})
    check(r.status_code == 200, "RH sí puede descartarla (decisión humana)")
    check(len(client.get("/candidatos").json()) == 2 and len(client.get("/candidatos?mostrar_cerradas=true").json()) == 3, "ya descartada sale del tablero y solo vuelve con «Mostrar cerradas»")

    print("\n--- 2. Asignar curso nunca truena ---")
    C = client.post("/capacitacion/generar", data={"tema": "Uso del extintor", "duracion": "15 min", "modalidad": "instructor_ia"}).json()["id"]
    check(client.patch(f"/capacitacion/{C}/finalizar").status_code == 200, "curso finalizado")
    r = client.post(f"/capacitacion/{C}/asignar", json={"colaborador_ids": ["COL-1"], "notificar": True})
    check(r.status_code == 201, f"asignación normal a un colaborador ({r.status_code}: {r.text[:200]})")

    # el aviso truena (proveedor caído / atributo faltante) → la asignación se guarda igual
    real = rcap._notificar

    async def _explota(a):
        raise AttributeError("'NoneType' object has no attribute 'modalidad'")

    rcap._notificar = _explota
    db.add(Colaborador(codigo="COL-2", cuenta_id=cuenta.id, nombre="Ana López", correo="ana@correo.mx", telefono="5511112222", puesto="Auxiliar"))
    db.commit()
    r = client.post(f"/capacitacion/{C}/asignar", json={"colaborador_ids": ["COL-2"], "notificar": True})
    check(r.status_code == 201, f"si el aviso truena, la asignación responde 201 (era 500) — {r.status_code}")
    check(r.json()["asignaciones"] and r.json()["envios"][0].get("error", "").startswith("No se pudo enviar el aviso"), "el error del aviso viaja en el resultado para que RH lo vea")
    check(len(client.get(f"/capacitacion/{C}/asignaciones").json()) == 2, "las dos asignaciones quedaron guardadas")
    rcap._notificar = real

    print("\n--- 3. Cursos legado (columnas nuevas vacías) ---")
    c = db.query(Curso).filter(Curso.codigo == C).one()
    c.duracion_texto = ""
    c.modalidad = ""
    c.duracion_horas = 0
    db.commit()
    check(texto_duracion_curso(c) == "", "duración vacía no revienta la serialización")
    ficha = client.get(f"/capacitacion/{C}")
    check(ficha.status_code == 200 and ficha.json()["modalidad"] == "autoguiado", "la ficha del curso legado abre (modalidad por defecto)")
    db.add(Colaborador(codigo="COL-3", cuenta_id=cuenta.id, nombre="Luis Ramos", correo="luis@correo.mx", telefono="5511113333", puesto="Almacén"))
    db.commit()
    r = client.post(f"/capacitacion/{C}/asignar", json={"colaborador_ids": ["COL-3"], "notificar": True})
    check(r.status_code == 201, f"asignar un curso legado sin modalidad/duración también responde 201 ({r.status_code})")
    check(client.get("/capacitacion/asignaciones").status_code == 200, "el tablero de asignaciones sigue abriendo")

    print("\n--- 4. Bitácora a prueba de detalles raros ---")
    from datetime import datetime, timezone

    ev = registrar(db, admin.nombre, "prueba_detalle", "curso", C, {"cuando": datetime.now(timezone.utc), "obj": object()})
    db.commit()
    check(isinstance(ev.detalle["cuando"], str) and isinstance(ev.detalle["obj"], str), "un detalle con datetime/objeto se guarda como texto en vez de tumbar la acción")
    db.close()

print(f"\n🎉 Hotfixes 2026-09-22 verificados: {OK} comprobaciones OK.")
