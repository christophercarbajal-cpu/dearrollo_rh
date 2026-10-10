"""Regresión de las RUTAS de proceso (2026-10-06). Base desechable y SIN claves externas (modo demo).

    .venv/Scripts/python.exe scripts/verificar_rutas_proceso.py

1. Rutas base precargadas y editables en cada Cuenta: Masivos (12 pasos), Corporativos sin psicometría (9) y
   Corporativos con psicometría (10), en el orden de etapas del documento y con avance automático encendido.
2. Cascada de asignación: proceso de la vacante → predeterminado de la Cuenta → «Corporativos sin psicometría»; la
   postulación guarda una COPIA estática (editar la plantilla después no la toca).
3. Migración de históricos: ninguna postulación sin ruta; no cambia etapas, no manda mensajes, no marca pasos como
   completados por la etapa (solo por registros reales) y es idempotente.
4. Ejecución: «En paralelo» / «Esperar a…», actividades ad hoc solo para un candidato, compuerta con omisión
   autorizada, y ninguna actividad terminada o con resultado se ve «Pendiente».
5. Avance de punta a punta (Corporativos): Prefiltro → Filtro Red Human → Filtro humano (automático) → Contratación →
   Onboarding → Alta como colaborador. Masivos: documentos por liga y validación ANTES de Contratación, sin aparecer en
   el tablero de Onboarding; los documentos de Onboarding nunca bloquean la entrada a esa etapa.
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_dir = tempfile.mkdtemp(prefix="rh_rutas_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "rutas.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-rutas"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import app.routers.contratacion as rcontratacion  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AsignacionCurso, Candidato, Cuenta, Curso, Entrevista, Evaluacion, Mensaje, PlantillaProceso, Postulacion, Usuario,
    UsuarioCuenta, Vacante,
)
from app.routers.candidatos import crear_postulacion  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0
PDF = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"
HOY = datetime.now().date().isoformat()  # día local (la fecha real no puede ser «futura»)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _correo_ok(destino, asunto, html, *a, **k):
    return {"enviado": True, "proveedor": "prueba", "detalle": "ok"}


rcontratacion.enviar_correo = _correo_ok  # la carta de intención «sale» por correo sin proveedor real


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin

    # ================= 0. Arranque: nadie sin ruta =================
    print("\n--- 0. Arranque ---")
    check(sproc.postulaciones_sin_ruta(db) == 0, "al arrancar no queda NINGUNA postulación sin ruta (demo sembrada)")
    check(all(sproc.tiene_proceso(p) and p.proceso.get("origen") == "base" for p in db.query(Postulacion).all()),
          "sin Cuentas configuradas, la demo recibe la ruta de respaldo en código")
    cuenta_demo = Cuenta(nombre="Demo Rutas", nombre_comercial="Demo", estado="Activa")
    db.add(cuenta_demo)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta_demo.id))
    check(sproc.asegurar_rutas_base_todas(db) == 3 and sproc.asegurar_rutas_base_todas(db) == 0, "rutas base sembradas una sola vez por Cuenta")
    db.commit()

    # ================= 1. Rutas base en una Cuenta nueva =================
    print("\n--- 1. Rutas base ---")
    app.dependency_overrides[cuenta_actual] = lambda: cuenta_demo
    r = client.post("/cuentas", json={"nombre": "Rutas SA", "nombre_comercial": "Rutas", "estado": "Activa"})
    check(r.status_code in (200, 201), f"Cuenta nueva ({r.status_code})")
    cuenta = db.get(Cuenta, r.json()["id"])
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    pls = {p["rutaBase"]: p for p in client.get("/procesos/plantillas").json() if p.get("rutaBase")}
    # 2026-10-09: tres rutas base nuevas (las de 2026-10-06 se desactivan una vez, sin borrarse)
    check(set(pls) == {"masivos_sin_documentos", "masivos_con_documentos", "corporativo"}, "la Cuenta nace con las tres rutas base editables")
    # retro 2026-10-09: rutas base corregidas — sin médica, psicometría, referencias, técnicas, inducción ni carta aparte
    check([len(pls[k]["pasos"]) for k in ("masivos_sin_documentos", "masivos_con_documentos", "corporativo")] == [9, 10, 10],
          "Masivos sin documentos 9 actividades · con documentos 10 · Corporativos 10")
    cola = ["Entrevista humana", "Propuesta y aceptación", "Documentos de ingreso", "Contrato y firma", "Tareas de onboarding",
            "Confirmación de ingreso"]
    check([p["nombre"] for p in pls["masivos_sin_documentos"]["pasos"]]
          == ["Solicitud web sin CV", "Prefiltro por WhatsApp", "Entrevista Red Human por WhatsApp"] + cola,
          "Masivos sin documentos: las 9 actividades en el orden de la retro")
    nombres = [p["nombre"] for p in pls["masivos_con_documentos"]["pasos"]]
    check(nombres == ["Solicitud web sin CV", "Prefiltro por WhatsApp", "Documentos iniciales", "Entrevista Red Human por WhatsApp"] + cola,
          "Masivos con documentos: «Documentos iniciales» es UNA actividad (liga + revisión)")
    check([p["nombre"] for p in pls["corporativo"]["pasos"]]
          == ["Solicitud web con CV", "Análisis de CV", "Prefiltro por WhatsApp", "Entrevista Red Human con avatar"] + cola,
          "Corporativos: solicitud con CV → Análisis de CV → prefiltro por WhatsApp → avatar → cierre")
    etapas_m = [p["etapa"] for p in pls["masivos_con_documentos"]["pasos"]]
    check(etapas_m == ["Prefiltro"] * 3 + ["Entrevista IA"] + ["Entrevista Humana"] + ["Contratación"] * 3 + ["Onboarding"] * 2,
          "Masivos con documentos: Prefiltro (3) → Filtro Red Human (1) → Filtro humano (1) → Contratación (3) → Onboarding (2)")
    prohibidos = {"medica", "psicometrica", "referencias", "tecnica", "induccion", "socioeconomica"}
    check(all(p["obligatorio"] and p["tipo"] not in prohibidos for pl in pls.values() for p in pl["pasos"]),
          "ninguna ruta base trae médica, psicometría, referencias, técnicas ni inducción; todo es obligatorio")
    check([p["tipo"] for p in pls["masivos_sin_documentos"]["pasos"]].count("entrevista_whatsapp") == 1
          and not any(p["tipo"] == "entrevista_agente" for p in pls["masivos_sin_documentos"]["pasos"]),
          "Masivos: Entrevista Red Human por WhatsApp (sin avatar)")
    check(not any(p["tipo"] in ("solicitud_documentos",) or (p["tipo"] == "documentos" and p["etapa"] == "Prefiltro")
                  for p in pls["masivos_sin_documentos"]["pasos"]), "Masivos sin documentos: no pide documentos antes de Contratación")
    check(pls["masivos_sin_documentos"]["predeterminada"] and sum(1 for p in pls.values() if p["predeterminada"]) == 1,
          "«Masivos sin documentos iniciales» nace como la predeterminada de la Cuenta")
    for k, pl in pls.items():
        auto = {e for e, c in pl["etapas"].items() if c["avance_automatico"]}
        check(auto == {"Prefiltro", "Entrevista IA", "Entrevista Humana"}, f"{pl['nombre']}: avance automático (sin interruptor)")
    ent = next(p for p in pls["masivos_sin_documentos"]["pasos"] if p["tipo"] == "entrevista_humana")
    check(ent["depende_de"] == [], "la entrevista humana no espera a nada de su etapa")
    check(next(p for p in pls["masivos_con_documentos"]["pasos"] if p["tipo"] == "documentos" and p["etapa"] == "Prefiltro")["depende_de"] == ["prefiltro-whatsapp"],
          "el catálogo define a qué espera cada actividad: Documentos iniciales espera al prefiltro")
    check(next(p for p in pls["corporativo"]["pasos"] if p["tipo"] == "carta_contrato")["depende_de"] == ["condiciones", "documentos-ingreso"],
          "Contrato y firma espera a la propuesta y a los documentos de ingreso")
    pasos_sin_deps = [dict(x, depende_de=[]) for x in pls["masivos_con_documentos"]["pasos"]]
    r = client.post("/procesos/plantillas", json={"nombre": "Sin dependencias capturadas", "pasos": pasos_sin_deps})
    check(r.status_code == 201 and next(p for p in r.json()["pasos"] if p["id"] == "documentos-iniciales")["depende_de"] == ["prefiltro-whatsapp"],
          "aunque el editor ya no mande dependencias, la API las recalcula desde el catálogo")
    r = client.patch(f"/procesos/plantillas/{pls['corporativo']['id']}", json={"nombre": "Corporativos (editada)"})
    check(r.status_code == 200 and r.json()["nombre"] == "Corporativos (editada)" and r.json()["rutaBase"] == "corporativo",
          "las rutas base se editan como cualquier plantilla")
    client.delete(f"/procesos/plantillas/{pls['masivos_con_documentos']['id']}")
    r = client.post("/procesos/plantillas/rutas-base")
    check(r.status_code == 200 and r.json()["creadas"] == 1 and len([p for p in client.get("/procesos/plantillas").json() if p.get("rutaBase") and p["activa"]]) == 3,
          "«Restaurar rutas base» reactiva la desactivada (sin duplicar)")

    # retro 2026-10-09: plantillas base YA guardadas — las que siguen como antes se corrigen una vez; las editadas se respetan
    from app.models import Bitacora as _Bit, PlantillaProceso as _PP

    r = client.post("/cuentas", json={"nombre": "Retro SA", "nombre_comercial": "Retro", "estado": "Activa"})
    cid = r.json()["id"]
    previa = lambda clave: sproc.normalizar_pasos([{"tipo": t, "etapa": e} for t, e in sproc.RUTAS_BASE_PREVIAS[clave]])
    pl_m = db.query(_PP).filter_by(cuenta_id=cid, ruta_base="masivos_sin_documentos").one()
    pl_c = db.query(_PP).filter_by(cuenta_id=cid, ruta_base="corporativo").one()
    pl_d = db.query(_PP).filter_by(cuenta_id=cid, ruta_base="masivos_con_documentos").one()
    pl_m.pasos, pl_m.predeterminada = previa("masivos_sin_documentos"), False
    pl_d.pasos, pl_d.predeterminada = previa("masivos_con_documentos"), True  # como la dejó el retiro de 2026-10-06
    pl_c.pasos = previa("corporativo") + [{"id": "extra", "tipo": "referencias", "nombre": "Referencias", "etapa": "Entrevista Humana",
                                          "obligatorio": False, "depende_de": [], "responsable": {"tipo": "rh"}, "regla": {"tipo": "dictamen"}}]
    editada = [x["tipo"] for x in pl_c.pasos]
    db.query(_Bit).filter(_Bit.accion == "rutas_base_retro_2026_10_09").delete()
    db.commit()
    res = sproc.corregir_rutas_base(db)
    db.commit()
    db.expire_all()
    pl_m, pl_c, pl_d = db.get(_PP, pl_m.id), db.get(_PP, pl_c.id), db.get(_PP, pl_d.id)
    check(res["corregidas"] >= 2 and [x["nombre"] for x in pl_m.pasos][2] == "Entrevista Red Human por WhatsApp" and pl_m.version == 2
          and any(x["nombre"] == "Documentos iniciales" for x in pl_d.pasos),
          "plantillas base guardadas sin editar → toman la configuración corregida (sube su versión)")
    check([x["tipo"] for x in pl_c.pasos] == editada, "una plantilla base que RH editó se respeta")
    check(pl_m.predeterminada and not pl_d.predeterminada, "la predeterminada pasa a «Masivos sin documentos iniciales»")
    check(sproc.corregir_rutas_base(db).get("yaAplicada"), "la corrección corre una sola vez")

    # ================= 2. Cascada de asignación =================
    print("\n--- 2. Cascada de asignación ---")
    vacs = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).all()[:4]
    for v in vacs:
        v.cuenta_id, v.cliente_id, v.proceso = cuenta.id, None, {}
    obtener(db).modo_prueba = False
    db.commit()
    V_SIN, V_MAS, V_PSI, V_X = [v.codigo for v in vacs]
    tel = iter(range(5583000000, 5583999999))

    def nueva(nombre, vac):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)), "correo": f"{nombre.split()[0].lower()}@rutas.mx",
                                             "vacante": vac, "consentimiento": True, "fuente": "RH"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def seg(codigo):
        return client.get(f"/procesos/postulaciones/{codigo}").json()

    def paso(s, pid):
        return next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == pid)

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    # la Cuenta nace con «Masivos sin documentos iniciales» predeterminada: se quita para probar el nivel 3
    client.patch(f"/procesos/plantillas/{pls['masivos_sin_documentos']['id']}", json={"predeterminada": False})
    P_BASE = nueva("Berta Base", V_SIN)
    s = seg(P_BASE)
    check(s["tieneProceso"] and s["origen"] == "base" and s["plantilla"] == "Masivos sin documentos iniciales",
          "vacante sin proceso y Cuenta sin predeterminado → «Masivos sin documentos iniciales» (nivel 3, especificación 2026-10-10)")
    client.patch(f"/procesos/plantillas/{pls['masivos_sin_documentos']['id']}", json={"predeterminada": True})
    P_CTA = nueva("Carla Cuenta", V_SIN)
    check(seg(P_CTA)["origen"] == "cuenta" and seg(P_CTA)["plantilla"] == "Masivos sin documentos iniciales",
          "con proceso predeterminado de la Cuenta → ese (nivel 2)")
    client.patch(f"/procesos/plantillas/{pls['masivos_sin_documentos']['id']}", json={"predeterminada": False})
    r = client.put(f"/procesos/vacantes/{V_MAS}", json={"plantilla_id": pls["masivos_con_documentos"]["id"]})
    check(r.status_code == 200, "la vacante copia la ruta Masivos")
    P_VAC = nueva("Vero Vacante", V_MAS)
    check(seg(P_VAC)["origen"] == "vacante" and seg(P_VAC)["plantilla"] == "Masivos con documentos iniciales", "con proceso de la vacante → ese (nivel 1)")
    antes = [x["nombre"] for x in post(P_BASE).proceso["pasos"]]
    pasos_edit = [dict(x) for x in pls["masivos_sin_documentos"]["pasos"]] + [{"tipo": "referencias", "etapa": "Entrevista Humana", "nombre": "Referencias", "obligatorio": False}]
    client.patch(f"/procesos/plantillas/{pls['masivos_sin_documentos']['id']}", json={"pasos": pasos_edit})
    check([x["nombre"] for x in post(P_BASE).proceso["pasos"]] == antes, "copia ESTÁTICA: editar la plantilla después no toca a la postulación")
    # postulación nacida sin vacante (menú de WhatsApp): ruta provisional que se reemplaza al elegir vacante
    c = db.query(Candidato).filter(Candidato.cuenta_id == cuenta.id).first()
    pv = crear_postulacion(db, c, None, cuenta.id, "whatsapp", consentimiento=True)
    check(pv.proceso.get("provisional") is True, "sin vacante todavía: ruta provisional (nunca sin ruta)")
    sproc.congelar(pv, db.query(Vacante).filter_by(codigo=V_MAS).one())
    check(pv.proceso.get("origen") == "vacante" and not pv.proceso.get("provisional"), "al elegir la vacante toma la ruta de esa vacante")
    db.rollback()

    # ================= 3. Ad hoc, compuerta y estados unificados =================
    print("\n--- 3. Ejecución ---")
    n_pasos_pl = len(client.get("/procesos/plantillas").json()[0]["pasos"])
    proc_vac = client.get(f"/procesos/vacantes/{V_MAS}").json()["proceso"]
    r = client.post(f"/procesos/postulaciones/{P_VAC}/pasos", json={"tipo": "tecnica", "nombre": "Prueba práctica de montacargas"})
    check(r.status_code == 201 and r.json()["paso"]["adhoc"] and not r.json()["paso"]["obligatorio"] and r.json()["paso"]["etapa"] == "Prefiltro",
          "actividad AD HOC: solo para este candidato, opcional y en su etapa actual")
    check(client.get(f"/procesos/vacantes/{V_MAS}").json()["proceso"]["pasos"] == proc_vac["pasos"]
          and len(client.get("/procesos/plantillas").json()[0]["pasos"]) == n_pasos_pl,
          "…sin tocar la vacante ni la plantilla")
    r = client.post(f"/evaluaciones/postulaciones/{P_VAC}", json={"tipo": "referencias", "forma": "registro_directo"})
    ev_ref = r.json()["evaluacion"]["codigo"]
    s = seg(P_VAC)
    adhoc = [x for e in s["etapas"] for x in e["pasos"] if x.get("adhoc")]
    check(any(x["tipo"] == "referencias" and x["evaluacion"] == ev_ref for x in adhoc),
          "«Agregar evaluación» fuera de la ruta queda como actividad ad hoc ligada a su evaluación")
    r = client.patch(f"/candidatos/{P_VAC}/etapa", json={"etapa": "Entrevista IA", "manual": True})
    check(r.status_code == 409 and "obligatorios" in r.json()["detail"], "compuerta: obligatorios sin cumplir → 409")
    rh = Usuario(correo="rh.sinpermiso@rutas.mx", nombre="RH Sin Permiso", rol="Usuario", activo=True, hash_pass="x")
    db.add(rh)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=rh.id, cuenta_id=cuenta.id))
    db.commit()
    app.dependency_overrides[usuario_decisor] = lambda: rh
    r = client.patch(f"/candidatos/{P_BASE}/etapa", json={"etapa": "Entrevista IA", "manual": True, "omitir_obligatorios": True,
                                                         "comentario": "El cliente lo pidió directo"})
    check(r.status_code == 403, "omitir obligatorios sin el permiso «Autorizar omisiones» → 403")
    app.dependency_overrides[usuario_decisor] = lambda: admin

    # ================= 4. Corporativos de punta a punta hasta el Alta =================
    print("\n--- 4. Corporativos: de Prefiltro al Alta ---")
    r = client.put(f"/procesos/vacantes/{V_SIN}", json={"plantilla_id": pls["corporativo"]["id"]})
    check(r.status_code == 200, "la vacante toma la ruta «Corporativos»")
    P = nueva("Diana Corporativa", V_SIN)  # ruta: Corporativos (de la vacante)
    s = seg(P)
    check(paso(s, "solicitud-web")["estado"] == "en_curso" and "cv" in paso(s, "solicitud-web")["espera"].lower(),
          "Solicitud web con CV: en curso hasta que el candidato comparta su CV")
    p = post(P)
    p.candidato.cv_datos = {"nombre": "Diana Corporativa", "experiencia": "Analista"}
    p.prefiltro_completo, p.estado, p.score = True, "cumple", 88
    p.analisis = {"respuestas_web": [{"pregunta": "¿Disponibilidad?", "respuesta": "Sí"}], "requisitos_cumplidos": ["Excel avanzado"]}
    db.commit()
    s = seg(P)
    check(all(paso(s, x)["estado"] == "completada" for x in ("solicitud-web", "analisis_cv", "prefiltro-whatsapp")),
          "Prefiltro de Corporativos: solicitud con CV, Análisis de CV y prefiltro por WhatsApp completados (registros reales)")
    asyncio.run(sproc.avanzar_si_corresponde(db, post(P)))
    check(post(P).etapa == "Entrevista IA", "prefiltro cumplido → avance AUTOMÁTICO a Filtro Red Human")
    p = post(P)
    db.add(Entrevista(codigo="ENT-RUTA", candidato_id=p.candidato_id, postulacion_id=p.id, token="tok-ruta", estado="evaluada",
                      evaluacion={"match_perfil": 86, "score_entrevista": 86, "recomendacion": "Avanzar"}, finalizada_en=datetime.now(timezone.utc)))
    db.commit()
    check(paso(seg(P), "entrevista_red_human")["estado"] == "completada", "Entrevista Red Human con avatar completada")
    asyncio.run(sproc.avanzar_si_corresponde(db, post(P)))
    check(post(P).etapa == "Entrevista Humana", "Filtro Red Human cumplido → avance automático a Filtro humano")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "entrevista_humana", "forma": "registro_directo"})
    EH = r.json()["evaluacion"]["codigo"]
    check(paso(seg(P), "entrevista-humana")["evaluacion"] == EH, "la entrevista humana queda ligada a su paso de la ruta")
    r = client.post(f"/evaluaciones/{EH}/resultado", data={"conclusion": "avanzar", "comentarios": "Excelente", "version": "0"})
    check(r.status_code == 200, "RH registra «Avanzar»")
    s = seg(P)
    eh = paso(s, "entrevista-humana")
    check(eh["estado"] == "completada" and eh["resultado"] == "favorable", "estado, resultado y siguiente acción se actualizan en la misma vista")
    check(post(P).etapa == "Contratación" and post(P).expediente is not None, "dictamen favorable → avance automático a Contratación (expediente abierto)")
    EXP = post(P).expediente.id
    check(client.post(f"/onboarding/expedientes/{EXP}/iniciar", json={"documentos": [{"tipo": "CURP"}]}).status_code == 409,
          "sin condiciones no se inicia el Onboarding")
    client.patch(f"/candidatos/{P}/condiciones-contratacion", json={"puesto": "Analista", "sueldo": "$25,000", "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": HOY})
    check(paso(seg(P), "condiciones")["estado"] == "completada", "Propuesta y aceptación: completada con las condiciones guardadas")
    r = client.post(f"/onboarding/expedientes/{EXP}/iniciar", json={"documentos": [{"tipo": "CURP"}]})
    check(r.status_code == 409 and "Documentos de ingreso" in r.json()["detail"] and "Contrato y firma" in r.json()["detail"],
          "Contratación: faltan «Documentos de ingreso» y «Contrato y firma» (obligatorios) → 409")
    s = seg(P)
    check(paso(s, "documentos-ingreso")["accion"]["clave"] == "solicitar_documentos"
          and paso(s, "documentos-ingreso")["accion"]["texto"] == "Solicitar documentos faltantes",
          "Documentos de ingreso: sin nada pedido, la acción es «Solicitar documentos faltantes»")
    check(not paso(s, "carta-contrato")["disponible"], "Contrato y firma espera a los documentos de ingreso")
    r = client.post(f"/contratacion/expedientes/{EXP}/carta-intencion/enviar", json={"canal": "correo"})
    check(paso(seg(P), "carta-contrato")["estado"] != "completada", "enviar solo la carta no completa «Contrato y firma»")
    for d in [x for x in client.get(f"/contratacion/expedientes/{EXP}").json()["documentos"] if x.get("obligatorio") and not x.get("interno")]:
        client.post(f"/contratacion/expedientes/{EXP}/documentos", data={"tipo": d["nombre"]}, files={"archivo": ("doc.pdf", PDF, "application/pdf")})
        client.post(f"/contratacion/expedientes/{EXP}/documentos/estado", json={"tipo": d["nombre"], "estado": "aprobado"})
    check(paso(seg(P), "documentos-ingreso")["estado"] == "completada", "Documentos de ingreso aprobados en Contratación")
    r = client.post(f"/firmas/expedientes/{EXP}/documentos")
    check(r.status_code == 200 and r.json()["modo"] == "papel", "«Firmar documentos» sin Dropbox Sign → modo Papel (PDF para imprimir)")
    r = client.post(f"/onboarding/expedientes/{EXP}/contrato-firmado", files={"archivo": ("firmados.pdf", PDF, "application/pdf")})
    check(r.status_code == 200 and post(P).etapa == "Onboarding", "Contrato y firma completo → Onboarding automático")
    check(client.post(f"/onboarding/expedientes/{EXP}/confirmar-ingreso", json={"fecha_real": HOY}).status_code == 200, "ingreso confirmado")
    for t in client.get(f"/onboarding/expedientes/{EXP}/tareas").json():
        if t["estado"] == "pendiente" and t.get("clave") not in ("contrato_firmado", "confirmar_ingreso"):
            client.patch(f"/onboarding/tareas/{t['id']}", json={"estado": "realizada"})
    check(paso(seg(P), "tareas-onboarding")["estado"] == "completada", "Tareas de onboarding completadas")
    r = client.post(f"/contratacion/expedientes/{EXP}/alta", json={"notificar": {"candidato_whatsapp": False, "candidato_correo": False}})
    check(r.status_code == 200, f"«Confirmación de ingreso» ({r.status_code} {r.text[:160]})")
    s = seg(P)
    check(paso(s, "alta")["estado"] == "completada", "la ruta llega a «Confirmación de ingreso» completada")
    check(all(x["estado"] in ("completada", "omitida") for e in s["etapas"] for x in e["pasos"] if x["obligatorio"]),
          "todos los pasos obligatorios de la ruta, de Prefiltro a la Confirmación de ingreso, quedaron completados")

    # ================= 5. Masivos: Documentos iniciales en Prefiltro =================
    print("\n--- 5. Masivos: Documentos iniciales en Prefiltro ---")
    PM = nueva("Mario Masivo", V_MAS)
    s = seg(PM)
    check(paso(s, "solicitud-web")["estado"] == "completada", "Solicitud web sin CV: completada al registrarse con consentimiento")
    check(paso(s, "documentos-iniciales")["espera"].startswith("Falta completar"), "Documentos iniciales espera al prefiltro")
    p = post(PM)
    p.prefiltro_completo, p.estado = True, "cumple"
    db.commit()
    s = seg(PM)
    check(paso(s, "documentos-iniciales")["accion"]["clave"] == "solicitar_documentos"
          and paso(s, "documentos-iniciales")["accion"]["texto"] == "Enviar liga de documentos",
          "con el prefiltro listo la acción de «Documentos iniciales» es «Enviar liga de documentos»")
    r = client.post(f"/candidatos/{PM}/solicitar-documentos", json={})
    check(r.status_code == 200 and post(PM).etapa == "Prefiltro" and post(PM).expediente is not None,
          "la liga abre el expediente con anticipación y NO cambia la etapa")
    exp_pm = post(PM).expediente.id
    check(not any(x.get("id") == exp_pm for x in client.get("/contratacion/expedientes").json()),
          "un expediente anticipado no aparece en el tablero de Onboarding")
    tok = post(PM).expediente.token
    r = client.post(f"/expedientes/publica/{tok}/documentos", data={"tipo": "Identificación oficial"}, files={"archivo": ("ine.pdf", PDF, "application/pdf")})
    check(r.status_code in (200, 201), f"el candidato sube su identificación por la liga ({r.status_code})")
    s = seg(PM)
    check(paso(s, "documentos-iniciales")["estado"] == "en_curso" and "identificación oficial" in paso(s, "documentos-iniciales")["espera"].lower()
          and paso(s, "documentos-iniciales")["accion"]["clave"] == "validar_documentos",
          "Documentos iniciales: en curso, dice exactamente qué falta aprobar y la acción pasa a «Validar documentos»")
    client.post(f"/contratacion/expedientes/{post(PM).expediente.id}/documentos/estado", json={"tipo": "Identificación oficial", "estado": "aprobado"})
    check(paso(seg(PM), "documentos-iniciales")["estado"] == "completada", "documento aprobado → Documentos iniciales completado")
    asyncio.run(sproc.avanzar_si_corresponde(db, post(PM)))
    check(post(PM).etapa == "Entrevista IA", "Prefiltro de Masivos completo → avance automático a Filtro Red Human")

    # ================= 6. Migración de históricos =================
    print("\n--- 6. Migración de candidatos históricos ---")
    vx = db.query(Vacante).filter_by(codigo=V_X).one()
    h1 = Postulacion(codigo="TMP-H1", candidato_id=c.id, vacante_id=vx.id, cuenta_id=cuenta.id, origen="formulario", etapa="Contratación",
                     consentimiento=True, proceso={})
    h2 = Postulacion(codigo="TMP-H2", candidato_id=c.id, vacante_id=vx.id, cuenta_id=cuenta.id, origen="formulario", etapa="Entrevista Humana",
                     consentimiento=True, proceso={})
    db.add_all([h1, h2])
    db.flush()
    h1.codigo, h2.codigo = f"P-{8800 + h1.id}", f"P-{8800 + h2.id}"
    db.commit()
    # h2: su entrevista humana real (con resultado) se registró antes de que existieran las rutas; después se cerró.
    r = client.post(f"/evaluaciones/postulaciones/{h2.codigo}", json={"tipo": "entrevista_humana", "forma": "registro_directo"})
    ev_h = r.json()["evaluacion"]["codigo"]
    client.post(f"/evaluaciones/{ev_h}/resultado", data={"conclusion": "avanzar", "comentarios": "Histórico", "version": "0"})
    db.expire_all()
    h2 = db.get(Postulacion, h2.id)
    h2.proceso, h2.proceso_estado, h2.activa, h2.motivo_cierre = {}, {}, False, "descartado"
    for e in db.query(Evaluacion).filter(Evaluacion.postulacion_id == h2.id):
        e.paso_id = ""
    db.commit()
    etapas_antes = {p.id: (p.etapa, p.activa) for p in db.query(Postulacion).all()}
    mensajes_antes = db.query(Mensaje).count()
    check(sproc.postulaciones_sin_ruta(db) == 2, "dos postulaciones históricas sin ruta (una activa en Contratación, una cerrada)")
    plan = sproc.asignar_rutas_faltantes(db, aplicar=False)
    check(plan["sin_ruta"] == 2 and plan["asignadas"] == 0 and sproc.postulaciones_sin_ruta(db) == 2, "simulación: cuenta sin escribir")
    r1 = sproc.asignar_rutas_faltantes(db)
    db.commit()
    check(r1["asignadas"] == 2 and sproc.postulaciones_sin_ruta(db) == 0, "migración: CERO postulaciones sin ruta (también las cerradas)")
    check({p.id: (p.etapa, p.activa) for p in db.query(Postulacion).all()} == etapas_antes, "ninguna etapa ni estado cambió")
    check(db.query(Mensaje).count() == mensajes_antes, "no se reenvió ningún mensaje ni solicitud")
    s1 = seg(h1.codigo)
    check(paso(s1, "entrevista-humana")["estado"] == "pendiente" and paso(s1, "prefiltro-whatsapp")["estado"] == "pendiente",
          "en Contratación SIN registros: sus pasos previos siguen pendientes (nada se completa por la etapa)")
    s2 = client.get(f"/procesos/postulaciones/{h2.codigo}").json()
    check(paso(s2, "entrevista-humana")["estado"] == "completada" and paso(s2, "entrevista-humana")["resultado"] == "favorable",
          "con su evaluación real con resultado: Completada · Favorable")
    db.expire_all()
    check(any(h.get("evento") == "ruta_asignada" for h in db.get(Postulacion, h1.id).historial or []), "queda una nota en el historial (sin borrar nada)")
    check(sproc.asignar_rutas_faltantes(db)["asignadas"] == 0, "idempotente: correrla otra vez no cambia nada")

    # ================= 7. Ninguna actividad terminada se ve «Pendiente» =================
    print("\n--- 7. Estados unificados ---")
    malas = []
    for pp in db.query(Postulacion).all():
        for x in sproc.estado_pasos(pp):
            if x["estado"] == "pendiente" and (x["resultado"] or x["cumpleRegla"]):
                malas.append((pp.codigo, x["nombre"]))
    check(not malas, f"ninguna actividad con resultado aparece «Pendiente» ({len(malas)})")

    # ================= 8. UX 2026-10-07: la etapa la define la ruta =================
    print("\n--- 8. Motor de avance + estados visibles ---")
    from app.models import Bitacora

    # 2026-10-08: vocabulario único con el cuello de botella real («Enviada» ya no es estado)
    VALIDOS = {"sin_iniciar", "esperando_candidato", "esperando_referencias", "esperando_consentimiento", "esperando_evaluador",
               "pendiente_resultado", "en_curso", "pendiente_revision", "completada", "aprobada", "no_aprobada", "omitida", "error"}
    P8 = nueva("Elsa Estados", V_SIN)
    s8 = seg(P8)
    check(all(x.get("estadoUnificado") in VALIDOS and x.get("estadoUnificadoTexto") for e in s8["etapas"] for x in e["pasos"]),
          "cada actividad trae UNA etiqueta visible del vocabulario único")
    check(paso(s8, "analisis_cv")["estadoUnificado"] == "sin_iniciar", "pendiente sin actividad → «Sin iniciar»")
    tarjeta = next(x for x in client.get("/candidatos").json() if x["id"] == P8)
    check(bool(tarjeta.get("siguienteActividad")) and tarjeta["siguienteActividad"]["estado"] in VALIDOS,
          "la tarjeta del tablero trae la siguiente actividad de la ruta")
    r = client.post(f"/evaluaciones/postulaciones/{P8}", json={"tipo": "referencias", "forma": "registro_directo"})
    ev8 = r.json()["evaluacion"]["codigo"]
    s8 = seg(P8)
    check(next(x for e in s8["etapas"] for x in e["pasos"] if x.get("evaluacion") == ev8)["estadoUnificado"] == "pendiente_resultado",
          "registro directo sin resultado → «Pendiente de resultado» (crear o copiar su liga nunca la marca «Enviada»)")
    for e in s8["etapas"]:
        if e["etapa"] == "Prefiltro":
            for x in e["pasos"]:
                if x["obligatorio"] and x["estado"] in ("pendiente", "en_curso"):
                    rr = client.post(f"/procesos/postulaciones/{P8}/pasos/{x['id']}/omitir", json={"motivo": "Prueba: ya se validó por otro medio"})
                    check(rr.status_code == 200, f"omitir «{x['nombre']}» con motivo")
    check(post(P8).etapa == "Entrevista IA", "omitir lo obligatorio de la etapa → la RUTA avanza sola (sin mover a mano)")
    r = client.delete(f"/candidatos/{P8}", params={"motivo": "Registro duplicado de prueba"})
    db.expire_all()
    b = db.query(Bitacora).filter(Bitacora.accion == "candidato_eliminado").order_by(Bitacora.id.desc()).first()
    check(r.status_code == 200 and b is not None and b.detalle.get("motivo") == "Registro duplicado de prueba",
          "eliminar candidato guarda el motivo en la bitácora")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — rutas de proceso")
