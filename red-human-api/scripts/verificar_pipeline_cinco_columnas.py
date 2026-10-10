"""Verificación del pipeline de CINCO columnas (2026-10-01).

1) Columnas: Prefiltro → Filtro Red Human («Entrevista IA») → Filtro humano («Entrevista Humana») → Contratación →
   Onboarding. «Evaluación» ya no existe (el pipeline y el embudo traen 5 claves; un cliente viejo que mande
   «Evaluación» cae en Filtro Red Human).
2) Migración (`migraciones.migrar_pipeline_cinco_columnas`): Evaluación → Filtro Red Human; etapas previas con
   entrevista humana creada → Filtro humano; Contratación/Onboarding nunca retroceden; historial y expediente se
   conservan; contadores recalculados; idempotente y la excepción se aplica una sola vez.
3) Botón único «Agregar evaluación»: crear una entrevista humana mueve a Filtro humano (desde Prefiltro / Filtro Red
   Human); cualquier otra evaluación o sus resultados no mueven; nunca retrocede.
4) Descartar: la postulación se queda en su columna con «No cumple», motivo e historial; «Mostrar cerradas» la ve.
5) Evaluación integral como resultado: «No apto» obligatorio manda sobre un score alto; «Pendiente» con score
   parcial (nunca cero); «Revisado por: Red Human» / «Revisado por: [nombre]» / «Pendiente de revisión».
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_pipeline_cinco_columnas.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_p5c_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "p5c.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY",
          "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-p5c"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.migraciones import MARCA_PIPELINE_CINCO_COLUMNAS, migrar_pipeline_cinco_columnas  # noqa: E402
from app.models import ETAPAS_CANDIDATO, Bitacora, Candidato, Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import conteos  # noqa: E402
from app.services import notificaciones as sn  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _envio(*a, **k):
    return {"enviado": True, "proveedor": "prueba", "detalle": "ok"}


sn.enviar_mensaje = _envio
sn.enviar_correo = _envio
import app.services.correo as _scorreo  # noqa: E402
import app.services.whatsapp as _swa  # noqa: E402

_scorreo.enviar_correo = _envio
_swa.enviar_mensaje = _envio


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta P5C", nombre_comercial="P5C RH", estado="Activa", correo_comunicacion="rh@p5c.mx")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id, p.consentimiento = cuenta.id, True
    # especificación 2026-10-10: el respaldo de la cascada ya es «Masivos sin documentos»; esta prueba usa la ruta con
    # Entrevista con avatar (agenda) y Análisis de CV, así que sus vacantes y candidatos sembrados toman «Corporativos».
    from app.models import Postulacion as _Post
    from app.services import proceso as _sproc

    _corp = _sproc.ruta_base("corporativo")
    for _v in db.query(Vacante).all():
        if not (_v.proceso or {}).get("pasos"):
            _v.proceso = _sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": _corp["pasos"]})
    for _p in db.query(_Post).all():
        if (_p.proceso or {}).get("origen") == "base":
            _p.proceso = None
            _sproc.congelar(_p, _p.vacante, db)
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    VAC = client.get("/vacantes").json()[0]["id"]
    tel = iter(range(5530000001, 5530000100))

    def nueva(nombre, omitir=True):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)), "correo": f"{nombre.split()[0].lower()}@p5c.mx",
                                             "vacante": VAC, "consentimiento": True, "fuente": "RH"})
        assert r.status_code == 201, r.text
        if omitir:
            omitir_previos(r.json()["id"])
        return r.json()["id"]

    def omitir_previos(codigo):
        """2026-10-06: toda postulación tiene ruta. Esta verificación es de la regla del pipeline, no de la ruta: RH
        omite (con permiso) los obligatorios de Prefiltro y Filtro Red Human para aislarla."""
        # Primero se apaga el avance automático de su copia (desde 2026-10-07 omitir corre el avance de la ruta)
        db.expire_all()
        pp = db.query(Postulacion).filter_by(codigo=codigo).one()
        pp.proceso = {**pp.proceso, "etapas": {e: {"avance_automatico": False} for e in pp.proceso.get("etapas", {})}}
        db.commit()
        seg = client.get(f"/procesos/postulaciones/{codigo}").json()
        for e in seg.get("etapas", []):
            if e["etapa"] in ("Prefiltro", "Entrevista IA"):
                for x in e["pasos"]:
                    if x["obligatorio"] and x["estado"] in ("pendiente", "en_curso"):
                        client.post(f"/procesos/postulaciones/{codigo}/pasos/{x['id']}/omitir",
                                    json={"motivo": "Prueba: fuera del alcance de esta verificación"})
        db.expire_all()

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    def entrevista_humana(codigo):
        return client.post(f"/evaluaciones/postulaciones/{codigo}", json={"tipo": "entrevista_humana", "forma": "registro_directo"})

    # ---------------- 1. Columnas ----------------
    print("\n--- 1. Cinco columnas ---")
    check(ETAPAS_CANDIDATO == ["Prefiltro", "Entrevista IA", "Entrevista Humana", "Contratación", "Onboarding"], "ETAPAS_CANDIDATO = 5 columnas en orden")
    pipe = client.get("/metricas/pipeline").json()
    check(list(pipe["candidatos"]["por_etapa"]) == ETAPAS_CANDIDATO, "el pipeline de métricas trae exactamente las 5 columnas (sin Evaluación)")
    P0 = nueva("Olga Legado")
    r = client.patch(f"/candidatos/{P0}/etapa", json={"etapa": "Evaluación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["etapa"] == "Entrevista IA", "un cliente viejo que manda «Evaluación» cae en Filtro Red Human")
    r = client.get("/candidatos", params={"etapa": "Filtro Red Human"})
    check(any(x["id"] == P0 for x in r.json()), "el filtro ?etapa= acepta el nombre visible «Filtro Red Human»")

    # ---------------- 2. Agregar evaluación ----------------
    print("\n--- 2. Botón único «Agregar evaluación» ---")
    PA = nueva("Ana Prefiltro")
    r = client.post(f"/evaluaciones/postulaciones/{PA}", json={"tipo": "medica", "forma": "registro_directo"})
    check(r.status_code == 201 and r.json()["movidaAFiltroHumano"] is False,
          "crear una evaluación médica no es «mover por entrevista humana» (la ruta avanza sola si no quedan obligatorios, 2026-10-09)")
    r = entrevista_humana(PA)
    p = post(PA)
    movio = r.json()["movidaAFiltroHumano"]
    check(r.status_code == 201 and p.etapa == "Entrevista Humana",
          "con la entrevista humana el candidato queda en Filtro humano (la mueve ella o, sin obligatorios previos, la ruta sola)")
    check(any(h.get("evento") in ("movida_por_entrevista_humana", "avance_automatico") for h in p.historial or []),
          "el movimiento queda como nota en el historial")
    if movio:
        check([o["actividad"] for o in p.actividades_omitidas or []] == ["Prefiltro", "Entrevista IA"], "lo saltado queda como «Omitida» con motivo")
    EH_A = r.json()["evaluacion"]["codigo"]
    r = client.post(f"/evaluaciones/{EH_A}/resultado", data={"conclusion": "avanzar", "comentarios": "Muy bien", "version": "0"})
    check(r.status_code == 200 and post(PA).etapa in ("Entrevista Humana", "Contratación"), "«Avanzar» cumple la actividad; la ruta avanza sola si no quedan obligatorios (2026-10-09)")

    PC = nueva("Carlos Contratado")
    client.patch(f"/candidatos/{PC}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    r = entrevista_humana(PC)
    check(r.status_code == 201 and post(PC).etapa == "Contratación", "una entrevista humana nueva nunca regresa a quien ya está en Contratación")

    # ---------------- 3. Descartar ----------------
    print("\n--- 3. No cumple se queda en su columna ---")
    PD = nueva("Diego Descartado")
    client.patch(f"/candidatos/{PD}/etapa", json={"etapa": "Entrevista IA", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    r = client.post(f"/candidatos/{PD}/decision", json={"accion": "descartar", "comentario": "Sin licencia de manejo"})
    d = r.json()
    check(r.status_code == 200 and d["etapa"] == "Entrevista IA" and d["activa"] is False and d["estado"] == "no_cumple",
          "descartar deja la tarjeta en Filtro Red Human con estado «No cumple»")
    check(d["motivoDescarte"] == "Sin licencia de manejo" and any(h.get("evento") == "descartado" for h in d["historial"]), "conserva motivo e historial")
    check(all(x["id"] != PD for x in client.get("/candidatos").json()), "por defecto (filtro existente) no se muestra")
    visibles = client.get("/candidatos", params={"mostrar_cerradas": True}).json()
    check(any(x["id"] == PD and x["etapa"] == "Entrevista IA" for x in visibles), "con «Mostrar cerradas» aparece en su misma columna")
    r = client.patch(f"/candidatos/{PD}/etapa", json={"etapa": "Entrevista IA", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["activa"] is True, "RH puede reabrirla en la misma columna")

    # ---------------- 4. Evaluación integral ----------------
    print("\n--- 4. Evaluación integral (resultado) ---")
    PI = nueva("Ivonne Integral", omitir=False)  # la evaluación integral toma de la ruta sus validaciones obligatorias
    ri = client.get(f"/candidatos/{PI}").json()["resultadoIntegral"]
    check(ri["estado"] == "pendiente" and ri["score"] is None, "sin calificaciones: Pendiente y score None (nunca cero)")
    p = post(PI)
    p.score, p.analisis = 92, {"requisitos_cumplidos": ["Experiencia en ventas"]}
    db.commit()
    ri = client.get(f"/candidatos/{PI}").json()["resultadoIntegral"]
    cv = next(v for v in ri["validaciones"] if v["clave"] == "analisis_cv")
    ent = next(v for v in ri["validaciones"] if v["clave"] == "entrevista_red_human")
    check(ri["estado"] == "pendiente" and ri["score"] == 92 and ri["scoreParcial"] is True, "faltan validaciones: Pendiente con score PARCIAL 92")
    check(cv["revisadoPor"] == "Revisado por: Red Human" and ent["revisadoPor"] == "Pendiente de revisión", "«Revisado por: Red Human» y «Pendiente de revisión»")
    r = entrevista_humana(PI)
    EH = r.json()["evaluacion"]["codigo"]
    client.post(f"/evaluaciones/{EH}/resultado", data={"conclusion": "no_avanzar", "comentarios": "No encaja", "version": "0"})
    ri = client.get(f"/candidatos/{PI}").json()["resultadoIntegral"]
    check(ri["estado"] == "no_apto" and ri["score"] == 92, "entrevista humana (obligatoria) No avanzar → «No apto» aunque el score sea 92")
    tarjeta = next(x for x in client.get("/candidatos").json() if x["id"] == PI)
    check(tarjeta["resultadoIntegral"]["estado"] == "no_apto", "la tarjeta del Kanban trae el mismo resultado integral")

    PK = nueva("Karla Apta")
    p = post(PK)
    p.score, p.analisis = 81, {"fortalezas_cv": ["Liderazgo"]}
    db.commit()
    EK = entrevista_humana(PK).json()["evaluacion"]["codigo"]  # desde Prefiltro: la Entrevista Red Human queda omitida
    client.post(f"/evaluaciones/{EK}/resultado", data={"conclusion": "avanzar", "comentarios": "", "version": "0"})
    ri = client.get(f"/candidatos/{PK}").json()["resultadoIntegral"]
    eh = next(v for v in ri["validaciones"] if v["codigo"] == EK)
    check(eh["estado"] == "aprobada" and eh["revisadoPor"] != "Pendiente de revisión",
          "«Avanzar» del entrevistador ya no espera la revisión de RH (2026-10-09)")
    client.post(f"/evaluaciones/{EK}/revisar", json={"conclusion": "avanzar"})
    ri = client.get(f"/candidatos/{PK}").json()["resultadoIntegral"]
    eh = next(v for v in ri["validaciones"] if v["codigo"] == EK)
    check(eh["revisadoPor"] == f"Revisado por: {admin.nombre}", "«Revisado por: [nombre de la persona]»")
    check(ri["estado"] == "apto" and ri["score"] == 81 and ri["scoreParcial"] is False, "todo completo y aprobado → Apto con score 81 (no parcial)")

    # ---------------- 5. Migración ----------------
    print("\n--- 5. Migración a 5 columnas ---")
    PM1 = nueva("Marta Evaluacion")          # Evaluación sin entrevista humana → Filtro Red Human
    PM2 = nueva("Mario Evaluacion EH")       # Evaluación con entrevista humana → Filtro humano
    PM3 = nueva("Mónica Prefiltro EH")       # Prefiltro con entrevista humana → Filtro humano
    PM4 = nueva("Memo Contratacion EH")      # Contratación con entrevista humana → se queda
    PM5 = nueva("Mila Cerrada")              # Evaluación descartada → Filtro Red Human (cerrada)
    for cod in (PM2, PM3):
        entrevista_humana(cod)
    client.patch(f"/candidatos/{PM4}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    entrevista_humana(PM4)
    expediente_pm4 = post(PM4).expediente.id
    for cod, etapa in ((PM1, "Evaluación"), (PM2, "Evaluación"), (PM3, "Prefiltro"), (PM5, "Evaluación")):
        post(cod).etapa = etapa
        db.flush()  # post() hace expire_all: sin flush se perdería el cambio
    post(PM5).cerrar("descartado")
    db.flush()
    db.query(Bitacora).filter(Bitacora.accion == MARCA_PIPELINE_CINCO_COLUMNAS).delete()  # base desechable: «primer arranque»
    db.commit()
    antes = conteos.por_etapa(db, cuenta.id)
    check(antes.get("Evaluación") == 2, "antes de migrar hay postulaciones activas en «Evaluación»")
    simulado = migrar_pipeline_cinco_columnas(db, aplicar=False)
    check(post(PM1).etapa == "Evaluación" and len(simulado["postulaciones"]) >= 4, "aplicar=False solo cuenta, no cambia nada")
    res = migrar_pipeline_cinco_columnas(db)
    db.commit()
    check(post(PM1).etapa == "Entrevista IA", "Evaluación → Filtro Red Human")
    check(post(PM2).etapa == "Entrevista Humana", "Evaluación con entrevista humana → Filtro humano")
    check(post(PM3).etapa == "Entrevista Humana", "Prefiltro con entrevista humana → Filtro humano")
    check(post(PM4).etapa == "Contratación" and post(PM4).expediente and post(PM4).expediente.id == expediente_pm4,
          "Contratación no retrocede y conserva su expediente")
    check(post(PM5).etapa == "Entrevista IA" and post(PM5).activa is False and post(PM5).motivo_cierre == "descartado", "una cerrada se mapea y sigue cerrada")
    check(any(h.get("evento") == "etapa_migrada" for h in post(PM1).historial or []), "cada movimiento agrega una nota al historial")
    check(res["a_filtro_humano"] >= 2 and res["excepcion_aplicada"] is True, "resumen de la migración")
    despues = conteos.por_etapa(db, cuenta.id)
    check("Evaluación" not in despues and sum(despues.values()) == sum(antes.values()), "contadores recalculados: sin «Evaluación» y mismo total")
    pipe = client.get("/metricas/pipeline").json()["candidatos"]["por_etapa"]
    check(pipe["Entrevista IA"] == despues.get("Entrevista IA", 0), "el embudo del tablero coincide con los contadores")
    # idempotente y la excepción solo una vez: RH regresa a PM3 a Filtro Red Human y un segundo arranque lo respeta
    client.patch(f"/candidatos/{PM3}/etapa", json={"etapa": "Entrevista IA", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    res2 = migrar_pipeline_cinco_columnas(db)
    db.commit()
    check(post(PM3).etapa == "Entrevista IA" and not res2["postulaciones"], "segunda corrida: no deshace la decisión de RH (idempotente)")

    # ---- el script independiente (scripts/migrar_pipeline_cinco_columnas.py) sobre la MISMA base ----
    import subprocess
    PM6 = nueva("Nora Script")
    entrevista_humana(PM6)
    for cod, etapa in ((PM1, "Evaluación"), (PM6, "Prefiltro")):
        post(cod).etapa = etapa
        db.flush()
    db.query(Bitacora).filter(Bitacora.accion == MARCA_PIPELINE_CINCO_COLUMNAS).delete()
    db.commit()
    script = str(RAIZ / "scripts" / "migrar_pipeline_cinco_columnas.py")
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8")
    sim = subprocess.run([sys.executable, script], cwd=str(RAIZ), env=entorno, capture_output=True, text=True, encoding="utf-8")
    db.expire_all()
    check(sim.returncode == 0 and "Simulación" in sim.stdout and post(PM1).etapa == "Evaluación",
          "script sin --forzar: muestra el plan y no escribe")
    apl = subprocess.run([sys.executable, script, "--forzar"], cwd=str(RAIZ), env=entorno, capture_output=True, text=True, encoding="utf-8")
    db.expire_all()
    check(apl.returncode == 0 and "Contadores DESPUÉS" in apl.stdout, f"script --forzar aplica y muestra contadores {apl.stderr[-300:]}")
    check(post(PM1).etapa == "Entrevista IA" and post(PM6).etapa == "Entrevista Humana" and post(PM4).etapa == "Contratación",
          "script: Evaluación → Filtro Red Human, previa con entrevista humana → Filtro humano, Contratación intacta")
    check("Evaluación" not in conteos.por_etapa(db, cuenta.id), "script: contadores sin «Evaluación»")

    db.close()

print(f"\n{OK} verificaciones OK")
