"""Cambios 55 y 56 (2026-10-15). Base desechable; Psicométricas.mx y Meta SIMULADOS (sin red).

    PYTHONIOENCODING=utf-8 python scripts/verificar_cambios55_56.py

Cambio 55 — editor de procesos y catálogos:
  1. Catálogo por etapa: sin «Otra actividad» en Prefiltro / Filtro Red Human, sin «Psicometría digital» en Filtro humano,
     sin «Capacitación» en Contratación.
  2. Orden fijo (backend): Prefiltro = Solicitud → Prefiltro WhatsApp → Documentos; Filtro Red Human = Llamada → Entrevista
     por WhatsApp → Entrevista con avatar → Psicometría digital → Capacitación; «Contrato y firma» al final de Contratación;
     una actividad por etapa (salvo «Otra actividad»); una sola Solicitud web.
  3. Motor del editor (`lib/orden-proceso.ts`, en Node): sin flechas en Prefiltro ni Filtro Red Human ni «Contrato y
     firma»; lo nuevo al final (Contratación: antes de «Contrato y firma»); menú sin repetidos y Solicitudes excluyentes.
  4. Migración de procesos existentes (`proceso.migrar_cambio55`).
Cambio 56 — Psicometría digital:
  5. Escalas por prueba y puntajes del proveedor; modalidad «Informativa» / «Con mínimos por escala» en la actividad.
  6. Al atrapar el término (webhook de Psicométricas.mx): Informativa → Completada y avance automático; mínimos cumplidos →
     avance; mínimos NO cumplidos → «No cumple parámetro», la ruta se detiene para RH.
  7. Recordatorio UNA vez a las 24 h por WhatsApp con plantilla.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
APP_DIR = RAIZ.parent / "red-human-app"
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import _temporal  # noqa: E402 — la base temporal se borra al terminar (pase o falle)
_dir = _temporal.directorio("rh_c55_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "c55.db").replace(chr(92), "/")
os.environ["ARCHIVOS_DIR"] = str(Path(_dir) / "archivos")
for k in ("OPENAI_API_KEY", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "PSICOMETRICAS_TOKEN", "DROPBOX_SIGN_API_KEY"):
    os.environ[k] = ""
os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["META_WHATSAPP_TOKEN"] = "token-falso"
os.environ["META_PHONE_NUMBER_ID"] = "123"
os.environ["META_APP_SECRET"] = "secreto-falso"
os.environ["ADMIN_PASSWORD"] = "prueba-c55"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ACTIVIDADES_POR_ETAPA, Bitacora, Cuenta, Evaluacion, PlantillaProceso, Postulacion, Usuario, Vacante  # noqa: E402
from app.routers import webhooks_proveedores as wp  # noqa: E402
from app.routers.candidatos import _crear_candidato, crear_postulacion  # noqa: E402
from app.services import evaluaciones as sev  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import psicometricas as psi  # noqa: E402
from app.services import whatsapp  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
ENVIADOS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _meta_falso(cuerpo):
    ENVIADOS.append(cuerpo)
    if cuerpo.get("type") == "text":
        return {"enviado": False, "proveedor": "meta", "detalle": "131047: Re-engagement message", "codigo": 131047}
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "mensaje_id": f"wamid.{len(ENVIADOS)}"}


whatsapp._meta_post = _meta_falso


def error_de(pasos):
    try:
        sproc.normalizar_pasos(pasos, previos=[])
    except sproc.ErrorProceso as e:
        return e.mensaje
    return None


with TestClient(app) as client:
    db = SessionLocal()
    obtener(db).modo_prueba = False
    db.commit()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    app.dependency_overrides[usuario_actual] = lambda: admin

    # ================= 1. catálogo por etapa =================
    print("\n--- 1. Catálogo por etapa ---")
    check("otra" not in ACTIVIDADES_POR_ETAPA["Prefiltro"] and "otra" not in ACTIVIDADES_POR_ETAPA["Entrevista IA"]
          and "otra" in ACTIVIDADES_POR_ETAPA["Entrevista Humana"], "«Otra actividad» fuera de Prefiltro y Filtro Red Human")
    check("psicometrica" not in ACTIVIDADES_POR_ETAPA["Entrevista Humana"] and "psicometrica" in ACTIVIDADES_POR_ETAPA["Entrevista IA"],
          "«Psicometría digital» solo en Filtro Red Human")
    check("induccion" not in ACTIVIDADES_POR_ETAPA["Contratación"], "«Capacitación / Inducción» fuera de Contratación")
    ops = client.get("/procesos/opciones").json()
    check(ops["ordenFijo"]["Prefiltro"] == ["solicitud_sin_cv", "solicitud_con_cv", "prefiltro_whatsapp", "documentos"]
          and ops["escalasPsicometria"]["1"] == ["D", "I", "S", "C"] and "3" not in ops["escalasPsicometria"],
          "/procesos/opciones trae el orden fijo y las escalas por prueba (IPV sin escalas = solo informativa)")

    # ================= 2. orden fijo y reglas al guardar =================
    print("\n--- 2. Orden fijo (backend) ---")
    pasos = sproc.normalizar_pasos([
        {"id": "doc", "tipo": "documentos", "etapa": "Prefiltro"}, {"id": "wa", "tipo": "prefiltro_whatsapp", "etapa": "Prefiltro"},
        {"id": "sol", "tipo": "solicitud_sin_cv", "etapa": "Prefiltro"},
        {"id": "cap", "tipo": "induccion", "etapa": "Entrevista IA"}, {"id": "psi", "tipo": "psicometrica", "etapa": "Entrevista IA"},
        {"id": "ava", "tipo": "entrevista_agente", "etapa": "Entrevista IA"}, {"id": "ewa", "tipo": "entrevista_whatsapp", "etapa": "Entrevista IA"},
        {"id": "lla", "tipo": "llamada_agente", "etapa": "Entrevista IA"},
        {"id": "cc", "tipo": "carta_contrato", "etapa": "Contratación"}, {"id": "of", "tipo": "oferta", "etapa": "Contratación"},
    ], previos=[])
    ids = [x["id"] for x in pasos]
    check(ids == ["sol", "wa", "doc", "lla", "ewa", "ava", "psi", "cap", "of", "cc"], f"orden forzado al guardar → {ids}")
    check(next(x for x in pasos if x["id"] == "psi")["evaluacion_psicometria"] == {"modalidad": "informativa"},
          "la Psicometría digital nueva nace «Informativa»")
    check(error_de([{"id": "a", "tipo": "entrevista_humana", "nombre": "Jefe", "etapa": "Entrevista Humana"},
                    {"id": "b", "tipo": "entrevista_humana", "nombre": "Sindicato", "etapa": "Entrevista Humana"}]) is None,
          "«una por etapa» es regla del menú: una ruta ya armada con dos entrevistas humanas (Fletes) se sigue aceptando")
    check(error_de([{"id": "a", "tipo": "otra", "nombre": "Uno", "etapa": "Entrevista Humana"},
                    {"id": "b", "tipo": "otra", "nombre": "Dos", "etapa": "Entrevista Humana"}]) is None, "«Otra actividad» sí puede repetirse")
    check("una sola solicitud" in (error_de([{"id": "a", "tipo": "solicitud_sin_cv"}, {"id": "b", "tipo": "solicitud_con_cv"}]) or ""),
          "Solicitud web sin CV y con CV son excluyentes")
    check("no puede ir en" in (error_de([{"id": "a", "tipo": "otra", "nombre": "X", "etapa": "Prefiltro"}]) or ""),
          "«Otra actividad» nueva en Prefiltro → 400")
    check("no puede ir en" in (error_de([{"id": "a", "tipo": "psicometrica", "etapa": "Entrevista Humana"}]) or ""),
          "«Psicometría digital» nueva en Filtro humano → 400")
    check("mínimo" in (error_de([{"id": "a", "tipo": "psicometrica", "etapa": "Entrevista IA",
                                  "evaluacion_psicometria": {"modalidad": "minimos", "minimos": {}}}]) or ""),
          "«Con mínimos por escala» exige al menos un mínimo")

    # ================= 3. motor del editor (Node) =================
    print("\n--- 3. Motor del editor (lib/orden-proceso.ts) ---")
    programa = r"""
const ts = require(process.argv[1] + '/node_modules/typescript');
const fs = require('fs');
const src = fs.readFileSync(process.argv[1] + '/lib/orden-proceso.ts', 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const m = { exports: {} }; new Function('module', 'exports', js)(m, m.exports); const o = m.exports;
const P = (id, tipo, etapa, extra) => Object.assign({ id, tipo, etapa }, extra || {});
let pasos = [P('sol', 'solicitud_web', 'Prefiltro'), P('eh', 'entrevista_humana', 'Entrevista Humana'), P('of', 'oferta', 'Contratación'),
             P('cc', 'carta_contrato', 'Contratación'), P('ob', 'onboarding', 'Onboarding'), P('ava', 'entrevista_agente', 'Entrevista IA')];
const r = {};
r.flechas = {pre: o.conFlechas(P('x', 'prefiltro_whatsapp', 'Prefiltro')), ia: o.conFlechas(P('x', 'psicometrica', 'Entrevista IA')),
             cc: o.conFlechas(P('cc', 'carta_contrato', 'Contratación')), of: o.conFlechas(P('of', 'oferta', 'Contratación')),
             eh: o.conFlechas(P('eh', 'entrevista_humana', 'Entrevista Humana')), ob: o.conFlechas(P('ob', 'onboarding', 'Onboarding'))};
const et = (l, e) => l.filter(x => x.etapa === e).map(x => x.id);
r.contratacion = et(o.insertarSugerido(pasos, P('doc', 'documentos', 'Contratación')), 'Contratación');
r.humano = et(o.insertarSugerido(pasos, P('med', 'medica', 'Entrevista Humana')), 'Entrevista Humana');
r.onboarding = et(o.insertarSugerido(pasos, P('alta', 'alta', 'Onboarding')), 'Onboarding');
r.ia = et(o.insertarSugerido(pasos, P('lla', 'llamada_agente', 'Entrevista IA')), 'Entrevista IA');
r.iaPsi = et(o.insertarSugerido(pasos, P('psi', 'psicometrica', 'Entrevista IA')), 'Entrevista IA');
r.preDocs = et(o.insertarSugerido(pasos, P('doc', 'documentos', 'Prefiltro')), 'Prefiltro');
r.fijo = o.aplicarOrdenFijo([P('d', 'documentos', 'Prefiltro'), P('w', 'prefiltro_whatsapp', 'Prefiltro'), P('s', 'solicitud_web', 'Prefiltro', {con_cv: true}),
                             P('cc', 'carta_contrato', 'Contratación'), P('of', 'oferta', 'Contratación')]).map(x => x.id);
const cat = ['solicitud_sin_cv', 'solicitud_con_cv', 'prefiltro_whatsapp', 'documentos'];
r.menuPre = o.disponibles('Prefiltro', cat, pasos);
r.menuPreSinSolicitud = o.disponibles('Prefiltro', cat, pasos.filter(x => x.id !== 'sol'));
r.menuHumano = o.disponibles('Entrevista Humana', ['entrevista_humana', 'medica', 'otra'], [...pasos, P('o1', 'otra', 'Entrevista Humana')]);
console.log(JSON.stringify(r));
"""
    salida = subprocess.run([shutil.which("node"), "-e", programa, str(APP_DIR)], capture_output=True, text=True, encoding="utf-8")
    check(salida.returncode == 0, f"el motor del editor corre en Node ({salida.stderr[:200]})")
    r = json.loads(salida.stdout.strip().splitlines()[-1])
    check(r["flechas"] == {"pre": False, "ia": False, "cc": False, "of": True, "eh": True, "ob": True},
          f"sin flechas en Prefiltro, Filtro Red Human ni «Contrato y firma»; con flechas en lo demás ({r['flechas']})")
    check(r["contratacion"] == ["of", "doc", "cc"], f"Contratación: lo nuevo justo antes de «Contrato y firma» ({r['contratacion']})")
    check(r["humano"] == ["eh", "med"] and r["onboarding"] == ["ob", "alta"], "Filtro humano y Onboarding: lo nuevo al final")
    check(r["ia"] == ["lla", "ava"] and r["iaPsi"] == ["ava", "psi"] and r["preDocs"] == ["sol", "doc"], "etapas fijas: cada una en su lugar")
    check(r["fijo"] == ["s", "w", "d", "of", "cc"], f"Prefiltro: Solicitud → Prefiltro WhatsApp → Documentos ({r['fijo']})")
    check(r["menuPre"] == ["prefiltro_whatsapp", "documentos"], f"con una Solicitud web, el menú oculta ambas solicitudes ({r['menuPre']})")
    check(r["menuPreSinSolicitud"] == ["solicitud_sin_cv", "solicitud_con_cv", "prefiltro_whatsapp", "documentos"], "al borrar la Solicitud web vuelven a aparecer las dos")
    check(r["menuHumano"] == ["medica", "otra"], f"el menú oculta lo que ya está en la etapa, salvo «Otra actividad» ({r['menuHumano']})")
    editor = (APP_DIR / "components/dashboard/procesos/editor-proceso.tsx").read_text(encoding="utf-8")
    check("disponiblesEn(e.valor, catalogoEtapa, pasos)" in editor and "Con mínimos por escala" in editor,
          "el editor usa el menú filtrado y ofrece la modalidad de la psicometría")

    # ================= 4. migración de procesos existentes =================
    print("\n--- 4. Migración (Cambio 55) ---")
    cuenta = Cuenta(nombre="C55", nombre_comercial="Empresa C55", estado="Activa", slug="c55")
    db.add(cuenta)
    db.flush()
    viejos = sproc.normalizar_pasos([
        {"id": "sol", "tipo": "solicitud_sin_cv"}, {"id": "otra-pre", "tipo": "otra", "nombre": "Llamar", "etapa": "Prefiltro"},
        {"id": "otra-ia", "tipo": "otra", "nombre": "Revisar", "etapa": "Entrevista IA"},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"},
        {"id": "psi", "tipo": "psicometrica", "etapa": "Entrevista Humana"},
        {"id": "cap", "tipo": "induccion", "etapa": "Contratación", "modalidad_capacitacion": "plataforma"},
        {"id": "cc", "tipo": "carta_contrato", "etapa": "Contratación"},
    ])
    pl = PlantillaProceso(cuenta_id=cuenta.id, nombre="Vieja", pasos=viejos, etapas=sproc.normalizar_etapas({}), version=1,
                          creado_por="x", actualizada_por="x")
    db.add(pl)
    db.commit()
    db.query(Bitacora).filter(Bitacora.accion == sproc.MARCA_CAMBIO55).delete()
    db.commit()
    res = sproc.migrar_cambio55(db)
    db.commit()
    pl = db.get(PlantillaProceso, pl.id)
    por_etapa = {e: [x["id"] for x in pl.pasos if x["etapa"] == e] for e in ("Prefiltro", "Entrevista IA", "Entrevista Humana", "Contratación")}
    check(pl.id in res["plantillas"] and pl.version == 2, "la plantilla se migra (sube versión)")
    check(por_etapa["Prefiltro"] == ["sol"] and por_etapa["Entrevista IA"] == ["psi"], f"«Otra» sale de Prefiltro/Filtro RH; la psicometría pasa a Filtro RH {por_etapa}")
    check(por_etapa["Entrevista Humana"][:2] == ["otra-pre", "otra-ia"] and por_etapa["Entrevista Humana"][-1] == "cap",
          f"«Otra» al INICIO de Filtro humano y la Capacitación al FINAL {por_etapa['Entrevista Humana']}")
    check(por_etapa["Contratación"] == ["cc"] and next(x for x in pl.pasos if x["id"] == "cap")["modalidad_capacitacion"] == "presencial",
          "Contratación sin Capacitación; en Filtro humano la Capacitación es presencial")
    check(sproc.migrar_cambio55(db).get("yaAplicada"), "la migración es idempotente")

    # ================= 5-6. psicometría digital: modalidades y avance =================
    print("\n--- 5-6. Psicometría digital ---")
    check(psi.escalas_de("1,7") == ["D", "I", "S", "C", "CI"] and psi.escalas_de("3") == [], "escalas por prueba (IPV no devuelve escalas)")
    datos = {"resultados": [{"prueba": "Cleaver", "factores": [{"factor": "D", "puntaje": 12}, {"factor": "I", "puntaje": "8"}]},
                            {"prueba": "Terman", "CI": 104}]}
    check(psi.puntajes_por_escala(datos) == {"d": 12.0, "i": 8.0, "ci": 104.0}, f"puntajes leídos del proveedor → {psi.puntajes_por_escala(datos)}")
    check(psi.evaluar_minimos(datos, {"D": 10, "CI": 90})["cumple"] and not psi.evaluar_minimos(datos, {"I": 10})["cumple"]
          and not psi.evaluar_minimos(datos, {"S": 5})["cumple"], "mínimos: cumple / abajo del mínimo / escala sin dato")

    def escenario(nombre, conf, tel):
        v = Vacante(cuenta_id=cuenta.id, codigo=f"VAC-{9500 + len(nombre) * 10 + ord(nombre[0]) % 10}", titulo=f"Analista {nombre}",
                    estado="Publicada", slug=f"analista-{nombre}", descripcion="x")
        db.add(v)
        db.flush()
        v.proceso = sproc.proceso_para_vacante(db, cuenta.id, {}, {"pasos": [
            {"id": "sol", "tipo": "solicitud_sin_cv"},
            {"id": "psico", "tipo": "psicometrica", "etapa": "Entrevista IA", "evaluacion_psicometria": conf},
            {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"}]})
        c = _crear_candidato(db, cuenta.id, f"Ana {nombre}", "Portal", False, telefono=tel)
        p = crear_postulacion(db, c, v, cuenta.id, "web", consentimiento=True)
        p.etapa = "Entrevista IA"
        ev = sev.nueva(p, cuenta.id, admin.nombre, admin.id, tipo="psicometrica", forma="integrada", paso_id="psico",
                       proveedor="Psicométricas.mx", id_proveedor="1,7", nombre="Cleaver + Terman", paso_integrada="enviada")
        db.add(ev)
        db.flush()
        sev.asignar_codigo(ev)
        ev.clave_proveedor = f"CLV-{nombre}"
        ev.enviada_en = datetime.now(timezone.utc)
        db.commit()
        return p, ev

    from fpdf import FPDF

    _pdf = FPDF()
    _pdf.add_page()
    _pdf.set_font("Helvetica", size=12)
    _pdf.cell(0, 10, "Informe de prueba")
    PDF = bytes(_pdf.output())
    orig = (psi.consultar_candidato, psi.terminado, psi.resultado_json, psi.resultado_pdf)
    psi.consultar_candidato = lambda clave: [{"fecha_fin": "2026-10-15"}]
    psi.terminado = lambda filas: True
    psi.resultado_json = lambda clave: datos
    psi.resultado_pdf = lambda clave: PDF
    try:
        for nombre, conf, tel, avanza, texto in (
                ("info", {"modalidad": "informativa"}, "5561000001", True, None),
                ("ok", {"modalidad": "minimos", "minimos": {"D": 10, "CI": 90}}, "5561000002", True, None),
                ("falla", {"modalidad": "minimos", "minimos": {"I": 10, "CI": 90}}, "5561000003", False, "No cumple parámetro")):
            p, ev = escenario(nombre, conf, tel)
            wp._procesar_psicometricas(ev.clave_proveedor, "termina_prueba", {"clave": ev.clave_proveedor})
            db.expire_all()
            p, ev = db.get(Postulacion, p.id), db.get(Evaluacion, ev.id)
            x = next(y for y in sproc.estado_pasos(p) if y["id"] == "psico")
            if avanza:
                check(ev.estado == "con_resultado" and ev.conclusion == "favorable" and x["estado"] == "completada" and x["estadoUnificado"] in ("aprobada", "completada")
                      and p.etapa == "Entrevista Humana",
                      f"{conf['modalidad']}{' (mínimos cumplidos)' if nombre == 'ok' else ''}: el webhook la marca Completada y el candidato "
                      f"avanza solo ({x['estadoUnificado']}, {p.etapa})")
            else:
                check(x["estadoUnificado"] == "no_aprobada" and x["estadoUnificadoTexto"] == texto and "I: 8" in x["detalle"]
                      and p.etapa == "Entrevista IA" and p.activa,
                      f"mínimos NO cumplidos → «{x['estadoUnificadoTexto']}» ({x['detalle']}); la ruta se detiene (sigue activo)")
                check(sproc.bloqueo_no_aprobada(p, sproc.estado_pasos(p)) is not None, "RH decide: avanzar por decisión de RH o descartar")
                check(any(d["nombre"].endswith(".pdf") or "pdf" in (d.get("mime") or "") for d in ev.adjuntos or []),
                      "el informe del proveedor queda en el expediente")
    finally:
        psi.consultar_candidato, psi.terminado, psi.resultado_json, psi.resultado_pdf = orig

    # ================= 7. recordatorio a las 24 h =================
    print("\n--- 7. Recordatorio 24 h ---")
    from app.services.recordatorios_psicometria import revisar_recordatorios_psicometria

    p, ev = escenario("rec", {"modalidad": "informativa"}, "5561000004")
    ev.enviada_en = datetime.now(timezone.utc) - timedelta(hours=23)
    db.commit()
    check(asyncio.run(revisar_recordatorios_psicometria()) == 0, "antes de 24 h no sale nada")
    ev = db.get(Evaluacion, ev.id)
    ev.enviada_en = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()
    antes = len(ENVIADOS)
    check(asyncio.run(revisar_recordatorios_psicometria()) == 1, "a las 24 h sin completarse → un recordatorio")
    plantillas = [c["template"]["name"] for c in ENVIADOS[antes:] if c.get("type") == "template" and c["to"] == "525561000004"]
    check(plantillas == ["recordatorio_psicometria_rh"], f"sale por WhatsApp con su plantilla ({plantillas})")
    db.expire_all()
    ev = db.get(Evaluacion, ev.id)
    ev.recordatorio_psicometria_en = datetime.now(timezone.utc) - timedelta(days=3)
    db.commit()
    check(asyncio.run(revisar_recordatorios_psicometria()) == 0, "una sola vez (aunque pasen más días)")

    app.dependency_overrides.clear()
    db.close()

print(f"\n✅ {OK} comprobaciones")
