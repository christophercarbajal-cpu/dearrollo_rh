"""Cambios 36 y 38 (2026-10-14). Base desechable; IA y conectores externos SIMULADOS (sin red).

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_cambios36_38.py

Cambio 36: «El proceso manda» (la vacante nunca modifica el proceso; con candidatos el proceso se bloquea; el campo de
documentos solo con «Documentos» en la ruta), motor de publicación multicanal (estados por canal, ligas con ?canal=, Modo
Demo sin requests externos, Pausar/Reanudar/Cerrar, Editar sincroniza) y la UI en dos bloques.
Cambio 38: una sola etiqueta en la tarjeta (recomendación → prefiltro), vocabulario Recomendable / Con reservas / No
recomendable y migración de lo guardado.
"""

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
_dir = tempfile.mkdtemp(prefix="rh_c36_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c36.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY",
          "PUBLICADOR_OCC_URL", "PUBLICADOR_LINKEDIN_URL", "PUBLICADOR_INDEED_URL", "PUBLICADOR_COMPUTRABAJO_URL"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "simulado"
os.environ["ADMIN_PASSWORD"] = "prueba-c36"
os.environ["SEMBRAR_DEMO"] = "true"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cuenta, Entrevista, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.serial import etiqueta_tablero  # noqa: E402
from app.services import evaluacion_integral as sei  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import publicador  # noqa: E402
from app.services import vocabulario  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
APP = RAIZ.parent / "red-human-app"
LLAMADAS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


# Conector externo simulado: registra cada llamada; «falla» = 503
RESPUESTA = {"falla": False}
_AsyncOriginal = httpx.AsyncClient


class _Conector(_AsyncOriginal):
    def __init__(self, *a, **k):
        def manejar(req: httpx.Request) -> httpx.Response:
            import json

            cuerpo = json.loads(req.content or b"{}")
            LLAMADAS.append((str(req.url), cuerpo.get("accion"), cuerpo.get("canal"), cuerpo.get("vacante", {}).get("liga")))
            if RESPUESTA["falla"]:
                return httpx.Response(503, json={"error": "servicio en mantenimiento"})
            return httpx.Response(200, json={"url": f"https://occ.example/empleo/{cuerpo['vacante']['codigo']}"})

        k["transport"] = httpx.MockTransport(manejar)
        super().__init__(*a, **k)


httpx.AsyncClient = _Conector
MASIVOS = sproc.ruta_base("masivos_sin_documentos")["pasos"]


def _doc_previo(x: dict) -> bool:
    """«Documentos» ANTES de Contratación (la de «Documentos de ingreso» de Contratación es de la ruta base)."""
    return x["tipo"] == "documentos" and x.get("etapa") not in ("Contratación", "Onboarding")
CON_DOCS = sproc.ruta_base("masivos_con_documentos")["pasos"]
FICHA = {"titulo": "Ayudante de almacén", "area": "Operaciones", "seniority": "Junior", "ubicacion_estado": "Nuevo León",
         "ubicacion_municipio": "Monterrey", "modalidad": "Presencial", "sueldo_desde": 9500, "sueldo_periodicidad": "mensual",
         "descripcion": "Acomodo de mercancía.", "requisitos_indispensables": ["Secundaria terminada"], "responsabilidades": ["Acomodar"]}

with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    db.commit()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="C36", nombre_comercial="Logística C36", estado="Activa", slug="c36-real")
    demo = Cuenta(nombre="C36 demo", nombre_comercial="Demo C36", estado="Activa", slug="demo-grupak")
    if db.query(Cuenta).filter_by(slug="demo-grupak").first():
        demo = db.query(Cuenta).filter_by(slug="demo-grupak").first()
    else:
        db.add(demo)
    db.add(cuenta)
    db.flush()
    for c in (cuenta, demo):
        db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=c.id))
    db.commit()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    actual = {"c": cuenta}
    app.dependency_overrides[cuenta_actual] = lambda: actual["c"]

    # ===================================================== 1. El proceso manda
    print("\n--- 1. El proceso manda ---")
    r = client.post("/vacantes", json={**FICHA, "proceso": {"pasos": MASIVOS},
                                       "documentos_solicitados": [{"nombre": "INE (frente y reverso)"}]})
    check(r.status_code == 201, f"alta ({r.status_code})")
    v1 = db.query(Vacante).filter_by(codigo=r.json()["id"]).one()
    check(not any(_doc_previo(x) for x in v1.proceso["pasos"]),
          "la vacante pide documentos pero su ruta no trae «Documentos»: NO se inyecta la actividad (la vacante no modifica el proceso)")
    p_doc = crear_postulacion(db, _crear_candidato(db, cuenta.id, "Ana Uno", "Portal", False, telefono="5512360001"), v1, cuenta.id,
                              "web", consentimiento=True)
    db.commit()
    check(not any(_doc_previo(x) for x in p_doc.proceso["pasos"]), "tampoco en la ruta congelada del candidato")
    r = client.post("/vacantes", json={**FICHA, "titulo": "Chofer C36", "proceso": {"pasos": CON_DOCS},
                                       "documentos_solicitados": [{"nombre": "INE (frente y reverso)"}]})
    v2 = db.query(Vacante).filter_by(codigo=r.json()["id"]).one()
    pasos_doc = [x for x in v2.proceso["pasos"] if _doc_previo(x)]
    check(len(pasos_doc) == 1 and pasos_doc[0].get("documentos_vacante"),
          "con «Documentos» en la ruta, ESA actividad recoge los documentos de la vacante (sin duplicar)")
    det = client.get(f"/vacantes/{v1.codigo}").json()
    check(det["procesoBloqueado"].startswith("No se puede cambiar: la vacante ya tiene candidatos") and det["candidatosTotales"] == 1,
          "con candidatos, el detalle trae el proceso BLOQUEADO con su motivo")
    r = client.patch(f"/vacantes/{v1.codigo}", json={"proceso": {"pasos": CON_DOCS}})
    check(r.status_code == 409 and "No se puede cambiar" in r.json()["detail"], f"PATCH del proceso con candidatos → 409 ({r.status_code})")
    r = client.put(f"/procesos/vacantes/{v1.codigo}", json={"pasos": CON_DOCS})
    check(r.status_code == 409, f"también por /procesos/vacantes ({r.status_code})")
    r = client.patch(f"/vacantes/{v2.codigo}", json={"proceso": {"pasos": MASIVOS}})
    check(r.status_code == 200 and not r.json()["procesoBloqueado"], "sin candidatos el proceso sí se cambia")
    r = client.patch(f"/vacantes/{v1.codigo}", json={"descripcion": "Acomodo y surtido."})
    check(r.status_code == 200, "editar lo demás de una vacante con candidatos sigue permitido")

    # ===================================================== 2. Motor de publicación
    print("\n--- 2. Publicación multicanal ---")
    conectados = [c["canal"] for c in client.get("/vacantes/canales").json()]
    check(conectados == ["portal", "whatsapp", "google"], f"sin conectores ni demo: solo los canales internos ({conectados})")
    r = client.post(f"/vacantes/{v1.codigo}/publicar", json={"canales": ["portal", "whatsapp", "occ"]})
    dist = r.json()["distribucion"]
    check(r.status_code == 200 and dist["portal"]["estado"] == "publicada" and dist["portal"]["liga"].endswith(f"?canal=portal")
          and dist["whatsapp"]["liga"].endswith("?canal=whatsapp"), "Portal y WhatsApp «Publicada» con su liga ?canal=")
    check(dist["occ"]["estado"] == "error" and dist["occ"]["estadoTexto"].startswith("Error: OCC Mundial no está conectado"),
          f"un canal no conectado queda «Error: motivo» sin frenar a los demás ({dist['occ']['estadoTexto']})")
    check(not LLAMADAS, "ninguna llamada externa sin conector")

    settings.publicador_occ_url, settings.publicador_occ_token = "https://conector.example/occ", "tok"
    conectados = [c["canal"] for c in client.get("/vacantes/canales").json()]
    check("occ" in conectados, "con su conector configurado OCC aparece conectado")
    r = client.post(f"/vacantes/{v1.codigo}/publicar", json={"canales": ["occ"]})
    occ = r.json()["distribucion"]["occ"]
    check(occ["estado"] == "publicada" and occ["liga"].startswith("https://occ.example/") and LLAMADAS[-1][1] == "publicar"
          and LLAMADAS[-1][3].endswith("?canal=occ"), f"OCC «Publicada» con la liga que regresó el conector (la vacante viaja con ?canal=occ)")
    client.patch(f"/vacantes/{v1.codigo}", json={"descripcion": "Acomodo, surtido y limpieza."})
    check(LLAMADAS[-1][1] == "actualizar", "«Editar» una vacante publicada se SINCRONIZA en las plataformas")
    RESPUESTA["falla"] = True
    r = client.post(f"/vacantes/{v1.codigo}/publicar", json={"canales": ["occ"]})
    check(r.json()["distribucion"]["occ"]["estadoTexto"].startswith("Error: OCC Mundial respondió 503"), "falla del canal → «Error: motivo»")
    RESPUESTA["falla"] = False
    client.post(f"/vacantes/{v1.codigo}/publicar", json={"canales": ["occ"]})

    print("\n--- 2b. Pausar · Reanudar · Cerrar ---")
    r = client.post(f"/vacantes/{v1.codigo}/pausar")
    dist = r.json()["distribucion"]
    check(r.status_code == 200 and r.json()["estado"] == "Pausada" and dist["portal"]["estado"] == "pausada"
          and LLAMADAS[-1][1] == "pausar", "Pausar: la vacante y sus canales quedan en pausa (se retira de las plataformas)")
    app.dependency_overrides.pop(cuenta_actual, None)
    r = client.get(f"/vacantes/slug/{v1.slug}")
    check(r.status_code == 410 and "en pausa" in r.json()["detail"], "la liga muestra el aviso de pausa")
    r = client.post("/candidatos/postular", data={"vacante": v1.slug, "nombre": "Beto Pausa", "telefono": "5512360009", "consentimiento": "true"})
    check(r.status_code == 410, "una vacante en pausa no recibe postulaciones")
    public = [x["id"] for x in client.get("/vacantes/publicas").json()]
    check(v1.codigo not in public, "sale del portal")
    app.dependency_overrides[cuenta_actual] = lambda: actual["c"]
    r = client.post(f"/vacantes/{v1.codigo}/reanudar")
    check(r.json()["estado"] == "Publicada" and r.json()["distribucion"]["occ"]["estado"] == "publicada" and LLAMADAS[-1][1] == "reanudar",
          "Reanudar la vuelve a publicar donde estaba")

    print("\n--- 2c. ?canal= en la postulación ---")
    app.dependency_overrides.pop(cuenta_actual, None)
    r = client.post("/candidatos/postular", data={"vacante": v1.slug, "nombre": "Carla Canal", "telefono": "5512360002",
                                                  "consentimiento": "true", "canal": "occ"})
    app.dependency_overrides[cuenta_actual] = lambda: actual["c"]
    pc = db.query(Postulacion).filter_by(codigo=r.json()["postulacion"]).one()
    check(r.status_code == 201 and pc.analisis.get("canal_origen") == "occ" and pc.candidato.fuente == "OCC Mundial",
          "la postulación desde la liga ?canal=occ registra su canal de origen")
    r = client.post(f"/vacantes/{v1.codigo}/cerrar", json={})
    check(r.json()["estado"] == "Cerrada" and r.json()["distribucion"]["occ"]["estado"] == "cerrada" and LLAMADAS[-1][1] == "cerrar",
          "Cerrar (definitivo) la retira de todos sus canales")
    settings.publicador_occ_url = settings.publicador_occ_token = ""

    print("\n--- 2d. Modo Demo: todo simulado, sin requests externos ---")
    actual["c"] = demo
    antes = len(LLAMADAS)
    canales_demo = client.get("/vacantes/canales").json()
    check({"occ", "linkedin", "indeed", "computrabajo"} <= {c["canal"] for c in canales_demo}
          and all(c["simulado"] for c in canales_demo if not c["interno"]), "en una Cuenta demo todos los canales aparecen (externos simulados)")
    r = client.post("/vacantes", json={**FICHA, "titulo": "Montacarguista demo", "proceso": {"pasos": MASIVOS}, "publicar": True,
                                       "canales": [c["canal"] for c in canales_demo]})
    dist = r.json()["distribucion"]
    check(r.status_code == 201 and all(e["estado"] == "publicada" for e in dist.values()) and len(dist) == len(canales_demo)
          and dist["linkedin"]["liga"].endswith("?canal=linkedin") and dist["linkedin"].get("simulado"),
          "Publicar en demo: todos «Publicada» con su liga ?canal=, simulados")
    check(len(LLAMADAS) == antes, "Modo Demo: CERO requests externos")
    actual["c"] = cuenta

    # ===================================================== 3. Cambio 38
    print("\n--- 3. Cambio 38: vocabulario y etiqueta única ---")
    check([sei.ESTADOS_INTEGRAL[k] for k in ("apto", "con_observaciones", "no_apto")] == ["Recomendable", "Con reservas", "No recomendable"]
          and sei.ESTADOS_INTEGRAL_OPERATIVO == sei.ESTADOS_INTEGRAL, "evaluación integral: Recomendable / Con reservas / No recomendable en todos los perfiles")
    pf = SimpleNamespace(entrevistas=[SimpleNamespace(estado="evaluada", evaluacion={"recomendacion": "revision"})])
    check(etiqueta_tablero(pf)["texto"] == "Con reservas", "la tarjeta corporativa ya no dice «Apto con observaciones»")
    p_m = crear_postulacion(db, _crear_candidato(db, cuenta.id, "Dora Migra", "Portal", False, telefono="5512360003"), v2, cuenta.id,
                            "web", consentimiento=True)
    p_m.analisis = {"resultado_integral": {"texto": "Apto con observaciones"}, "historial_resultados": ["Apto", "No apto"],
                    "dictamen_medico": "Apto", "evaluacion_medica": {"tipo": "medica", "texto": "No apto"}}
    p_m.historial = [{"evento": "resultado", "estado": "Apto con observaciones"}]
    db.commit()
    check(vocabulario.con_vocabulario_anterior(db) >= 4, "antes de migrar quedan estados con el vocabulario anterior")
    r = vocabulario.migrar(db, aplicar=True, forzar=True)
    db.commit()
    db.refresh(p_m)
    check(vocabulario.con_vocabulario_anterior(db) == 0 and p_m.analisis["resultado_integral"]["texto"] == "Con reservas"
          and p_m.analisis["historial_resultados"] == ["Recomendable", "No recomendable"] and p_m.historial[0]["estado"] == "Con reservas",
          "migración: ningún «Apto con observaciones» como recomendación global")
    check(p_m.analisis["dictamen_medico"] == "Apto" and p_m.analisis["evaluacion_medica"]["texto"] == "No apto",
          "«Apto / No apto» se conserva SOLO en la evaluación médica / socioeconómica")

    # ===================================================== 4. Frontend
    print("\n--- 4. Frontend ---")
    form = (APP / "components/dashboard/vacantes/formulario-contenido.tsx").read_text(encoding="utf-8")
    check('data-bloque="1"' in form and 'data-bloque="2"' in form and re.search(r"\{generado && \(\s*<section ref=\{bloque2\}", form),
          "UI en Bloque 1 y Bloque 2; el Bloque 2 solo aparece después de generar")
    check("scrollIntoView" in form and "Volver a generar" in form and "firmaBloque1" in form,
          "al generar baja al Bloque 2; si se edita el Bloque 1 después, «Volver a generar»")
    check('label="Enfoque de entrevista"' not in form and "<PerfilOperativoCampo" not in form and "avisos.map" not in form,
          "sin selector de enfoque ni de oficio y sin avisos de «Cumplimiento»")
    check("Sin capturar: no se publican prestaciones" in form and "{conDocumentos && (" in form
          and "reqRuta.etapasDocumentos.length > 0" in form, "errores en línea; «Documentos y fotos» solo si la ruta trae «Documentos»")
    check(all(t in form for t in ('titulo="Responsabilidades"', 'titulo="Requisitos"', 'titulo="Preguntas base"', 'titulo="Entrevista"',
                                  'titulo="Textos por plataforma"', "Descarta si")), "Bloque 2: responsabilidades, requisitos, base, entrevista y textos")
    sec = (APP / "components/dashboard/procesos/seccion-proceso-vacante.tsx").read_text(encoding="utf-8")
    check("disabled={Boolean(bloqueado)}" in sec and "predeterminada.pasos" in sec, "proceso preseleccionado y bloqueado con candidatos")
    pub = (APP / "components/dashboard/vacantes/publicar-en.tsx").read_text(encoding="utf-8")
    pag = (APP / "app/dashboard/vacantes/page.tsx").read_text(encoding="utf-8")
    check("setMarcados((l ?? []).map((c) => c.canal))" in pub and "<PanelPublicarEn" in pag and "sticky bottom-0" in pag
          and re.search(r"\{generado && \(\s*<Button className=\"flex-1\" onClick=\{\(\) => guardar\(true\)\}", pag),
          "«Publicar en» con todo marcado; «Guardar borrador» siempre y «Publicar» solo después de generar")
    check("pausarVacante" in pag and "reanudarVacante" in pag and "Cerrar vacante (definitivo)" in pag, "menú «…»: Pausar/Reanudar y Cerrar")
    apl = (APP / "app/aplicar/[slug]/formulario-aplicar.tsx").read_text(encoding="utf-8")
    check('get("canal")' in apl and "canal: canalLiga" in apl, "/aplicar manda el ?canal= de su liga")
    kb = (APP / "components/dashboard/candidatos/tablero-kanban.tsx").read_text(encoding="utf-8")
    check("Cumple perfil\"" not in kb and kb.count("data-etiqueta-tarjeta") == 1 and "<Chip clase={filtro[1]}" not in kb,
          "la tarjeta tiene UNA sola etiqueta y ya no muestra «Cumple perfil»")
    cuerpo = kb[kb.index("export function etiquetaTarjeta"):]
    check(cuerpo.index("c.card_label?.texto") < cuerpo.index("c.filter_status ? FILTRO"), "jerarquía: recomendación final y, si no hay, prefiltro")

print(f"\n✅ Cambios 36 y 38: {OK} verificaciones OK")
