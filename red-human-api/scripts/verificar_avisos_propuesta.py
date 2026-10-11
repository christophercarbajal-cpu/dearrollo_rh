"""Avisos de estado, inactividad y propuesta por WhatsApp (2026-10-10, Cambio 3). Base desechable, SIN red.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_avisos_propuesta.py

Cubre: UN aviso neutral al completar una actividad con el «Siguiente paso» (máx. 1 por día; nunca «felicidades»), fin
de selección neutral y «Pendiente: capturar condiciones», aviso de inactividad a los 3 días hábiles (una vez por periodo),
propuesta precargada → «¿Aceptas? Responde sí o no» → «sí» pide SOLO los documentos que faltan / «no» avisa a RH (nunca
descarta), postulaciones viejas silenciosas, y la entrevista IA sin alertas de escolaridad/documentos.
"""

import asyncio
import itertools
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_dir = tempfile.mkdtemp(prefix="rh_avisos_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "avisos.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-avisos"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import app.services.correo as correo_srv  # noqa: E402
import app.services.notificaciones as notif  # noqa: E402
import app.services.whatsapp as wa  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bitacora, Cuenta, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import procesar_prefiltro  # noqa: E402
from app.services import avisos_estado, ia, propuesta  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


ENVIOS, CORREOS = [], []
_ids = itertools.count(1)


async def _wa(telefono, texto, *a, **k):
    ENVIOS.append((telefono, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "wa_id": f"wamid.P{next(_ids)}"}


async def _wa_boton(telefono, texto, boton, url, *a, **k):
    return await _wa(telefono, f"{texto} {url}")


async def _correo(destino, asunto, html, *a, **k):
    CORREOS.append((destino, asunto, html))
    return {"enviado": True, "proveedor": "resend", "detalle": "ok"}


wa.enviar_mensaje = notif.enviar_mensaje = _wa
import app.routers.candidatos as cand_router  # noqa: E402

cand_router.enviar_mensaje = _wa
wa.enviar_con_boton = _wa_boton
correo_srv.enviar_correo = notif.enviar_correo = _correo


def a(tel):
    return [t for d, t in ENVIOS if d == tel]


PROHIBIDAS = ("felicidades", "felicitaciones", "buenas noticias", "avanzas", "compatible", "seleccionad", "🎉")

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    cuenta = Cuenta(nombre="Avisos SA", nombre_comercial="Avisos Comercial", estado="Activa", correo_comunicacion="rh@avisos.mx")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    obtener(db).modo_prueba = False
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id, v.cliente_id, v.proceso, v.responsable_id = cuenta.id, None, {}, admin.id
    v.titulo, v.sueldo, v.ubicacion = "Almacenista", "$11,000 mensuales", "Tultitlán, Estado de México"
    db.commit()
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    PASOS = [
        {"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana"},
        {"id": "tec", "tipo": "tecnica", "etapa": "Entrevista Humana"},
        {"id": "condiciones", "tipo": "condiciones", "etapa": "Contratación"},
        {"id": "docs", "tipo": "documentos", "etapa": "Contratación", "depende_de": ["condiciones"]},
    ]
    assert client.put(f"/procesos/vacantes/{v.codigo}", json={"pasos": PASOS}).status_code == 200
    tel = iter(range(5583000000, 5583999999))

    def nueva(nombre):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)), "correo": f"{nombre.split()[0].lower()}@avisos.mx",
                                             "vacante": v.codigo, "consentimiento": True, "fuente": "RH"})
        assert r.status_code == 201, r.text
        codigo = r.json()["id"]
        rr = client.patch(f"/candidatos/{codigo}/etapa", json={"etapa": "Entrevista Humana", "manual": True, "omitir_obligatorios": True,
                                                              "comentario": "Prueba: arranca en Filtro humano"})
        assert rr.status_code == 200, rr.text
        return codigo

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    def seg(codigo):
        return client.get(f"/procesos/postulaciones/{codigo}").json()

    def paso(codigo, pid):
        return next(x for e in seg(codigo)["etapas"] for x in e["pasos"] if x["id"] == pid)

    def resultado(codigo, pid, conclusion="avanzar", quien="Laura Jefa"):
        r = client.post(f"/procesos/postulaciones/{codigo}/pasos/{pid}/resultado", data={"conclusion": conclusion, "realizada_por": quien})
        assert r.status_code == 200, r.text

    # ================= 1. Aviso de estado al completar una actividad =================
    print("\n--- 1. Un aviso neutral con el «Siguiente paso» (máximo 1 por día) ---")
    P = nueva("Ana Avisos")
    t = post(P).telefono
    asyncio.run(avisos_estado.revisar(db, post(P)))  # primera foto: nunca avisa de golpe
    db.commit()
    ENVIOS.clear()
    resultado(P, "eh")
    msgs = a(t)
    check(len(msgs) == 1 and "registramos tu entrevista" in msgs[0] and "Siguiente paso: tu evaluación técnica" in msgs[0],
          f"al completar la entrevista: UN mensaje con el siguiente paso («{msgs[0] if msgs else ''}»)")
    check(not any(w in msgs[0].lower() for w in PROHIBIDAS), "sin «felicidades» ni promesas de avance")
    ENVIOS.clear()
    resultado(P, "tec", "favorable")
    check(post(P).etapa == "Contratación" and not a(t), "otra actividad el MISMO día (y fin de selección) → sin segundo aviso de estado")

    # ================= 2. Fin de selección =================
    print("\n--- 2. Fin de selección neutral → «Pendiente: capturar condiciones» ---")
    P2 = nueva("Beto Fin")
    t2 = post(P2).telefono
    asyncio.run(avisos_estado.revisar(db, post(P2)))
    db.commit()
    p2 = post(P2)
    p2.analisis = {**(p2.analisis or {}), "avisos_estado": {**p2.analisis["avisos_estado"], "completadas": ["eh"]}}
    db.commit()
    resultado(P2, "eh")
    ENVIOS.clear()
    resultado(P2, "tec", "favorable")
    msgs = a(t2)
    check(post(P2).etapa == "Contratación" and len(msgs) == 1 and "terminó la etapa de selección" in msgs[0]
          and "propuesta de trabajo" in msgs[0] and not any(w in msgs[0].lower() for w in PROHIBIDAS),
          f"al llegar a Contratación: mensaje de fin NEUTRAL («{msgs[0] if msgs else ''}»)")
    x = paso(P2, "condiciones")
    check(x["estadoUnificado"] == "pendiente_condiciones" and x["estadoUnificadoTexto"] == "Pendiente: capturar condiciones"
          and x["accion"]["texto"] == "Enviar propuesta", "la actividad queda «Pendiente: capturar condiciones» con «Enviar propuesta»")

    # ================= 3. Propuesta por WhatsApp =================
    print("\n--- 3. Propuesta precargada → «¿Aceptas? Responde sí o no» ---")
    pre = client.get(f"/candidatos/{P2}/propuesta").json()
    d = pre["datos"]
    check(d["puesto"] == "Almacenista" and d["sueldo"] == "$11,000 mensuales" and d["ubicacion"] == "Tultitlán, Estado de México"
          and d["jefe_directo"] == "Laura Jefa" and pre["origen"]["jefe_directo"] == "entrevista_humana",
          "precarga: puesto, sueldo y ubicación de la vacante; jefe de la entrevista humana")
    r = client.post(f"/candidatos/{P2}/propuesta", json={"puesto": d["puesto"], "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-11-03"})
    check(r.status_code == 400 and "sueldo" in r.json()["detail"], "sin sueldo no se envía la propuesta")
    ENVIOS.clear()
    cuerpo = {**{k: d[k] for k in ("puesto", "sueldo", "ubicacion", "jefe_directo")}, "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-11-03"}
    r = client.post(f"/candidatos/{P2}/propuesta", json=cuerpo)
    msgs = a(t2)
    check(r.status_code == 200 and msgs and "• Puesto: Almacenista" in msgs[-1] and "• Sueldo: $11,000 mensuales" in msgs[-1]
          and "• Jefe directo: Laura Jefa" in msgs[-1] and msgs[-1].endswith("¿Aceptas? Responde sí o no."),
          "la propuesta sale con sus datos y «¿Aceptas? Responde sí o no.»")
    check(paso(P2, "condiciones")["estado"] == "en_curso", "la actividad espera la respuesta del candidato")
    # deja 2 documentos aprobados: el «sí» debe pedir SOLO los que faltan
    p2 = post(P2)
    for doc in p2.expediente.obligatorios[:2]:
        doc.estado, doc.revisado_por, doc.archivo, doc.nombre_archivo = "recibido", admin.nombre, f"{_dir}/x.pdf", "x.pdf"
    db.commit()
    aprobados = [doc.tipo for doc in post(P2).expediente.obligatorios[:2]]
    ENVIOS.clear()
    asyncio.run(procesar_prefiltro(db, post(P2), "ok", "whatsapp"))
    check(propuesta.estado(post(P2))["estado"] == "enviada" and "Responde sí o no" in a(t2)[-1], "«ok» no es una aceptación: se repite la pregunta")
    ENVIOS.clear()
    asyncio.run(procesar_prefiltro(db, post(P2), "Sí, acepto", "whatsapp"))
    p2 = post(P2)
    check(propuesta.estado(p2)["estado"] == "aceptada" and paso(P2, "condiciones")["estado"] == "completada", "«sí» → propuesta aceptada")
    liga = next((m for m in a(t2) if "Súbelos" in m), "")
    check(liga and not any(t in liga for t in aprobados) and all(t in liga for t in p2.expediente.pendientes),
          "…y se piden SOLO los documentos de ingreso que faltan")
    check(not any(w in " ".join(a(t2)).lower() for w in PROHIBIDAS), "ningún mensaje del flujo felicita ni promete")

    # «no» → avisa a RH, nunca descarta
    P3 = nueva("Carla No")
    p3 = post(P3)
    p3.etapa = "Contratación"
    db.commit()
    from app.routers.candidatos import _abrir_expediente

    _abrir_expediente(db, post(P3), admin)
    db.commit()
    client.post(f"/candidatos/{P3}/propuesta", json=cuerpo)
    CORREOS.clear()
    asyncio.run(procesar_prefiltro(db, post(P3), "no, gracias", "whatsapp"))
    p3 = post(P3)
    check(propuesta.estado(p3)["estado"] == "rechazada" and p3.activa, "«no» → rechazada y la postulación sigue activa (RH decide)")
    check(any(c[0] == admin.correo and "no aceptó la propuesta" in c[2] for c in CORREOS), "RH recibe el aviso del rechazo")
    check(paso(P3, "condiciones")["estadoUnificado"] == "no_aprobada", "la actividad queda «No aprobada» (no descarta)")
    r = client.post(f"/candidatos/{P3}/propuesta/respuesta", json={"acepta": True})
    check(r.status_code == 409, "sin propuesta pendiente no se registra otra respuesta")

    # ================= 4. Inactividad =================
    print("\n--- 4. Inactividad: 3 días hábiles ---")
    lunes = datetime(2026, 10, 12, 15, tzinfo=timezone.utc)
    check(avisos_estado.dias_habiles_entre(datetime(2026, 10, 9, 15, tzinfo=timezone.utc), lunes) == 1, "viernes → lunes = 1 día hábil")
    check(avisos_estado.dias_habiles_entre(datetime(2026, 10, 7, 15, tzinfo=timezone.utc), lunes) == 3, "miércoles → lunes = 3 días hábiles")
    P4 = nueva("Diana Quieta")
    p4 = post(P4)
    hace = datetime.now(timezone.utc) - timedelta(days=7)
    p4.ultima_actividad_en = p4.etapa_desde = p4.creado_en = hace
    p4.analisis = {**(p4.analisis or {}), "avisos_estado": {"completadas": [], "etapa": p4.etapa, "inactividad_ref": hace.isoformat()}}
    db.commit()
    ENVIOS.clear()
    texto = asyncio.run(avisos_estado.revisar_inactividad_postulacion(db, post(P4)))
    check(texto == "Tu proceso para Almacenista en Avisos Comercial sigue activo. Estamos esperando: la revisión del equipo de RH."
          and a(post(P4).telefono) == [texto], f"aviso exacto de inactividad («{texto}»)")
    check(asyncio.run(avisos_estado.revisar_inactividad_postulacion(db, post(P4))) is None, "no se repite mientras no haya movimiento")
    P5 = nueva("Elsa Reciente")
    check(asyncio.run(avisos_estado.revisar_inactividad_postulacion(db, post(P5))) is None, "con movimiento reciente no se avisa")

    # ================= 5. Datos existentes y otras reglas =================
    print("\n--- 5. Datos existentes y entrevista IA ---")
    P6 = nueva("Fer Viejo")
    p6 = post(P6)
    p6.analisis = {k: x for k, x in (p6.analisis or {}).items() if k != "avisos_estado"}
    db.commit()
    resultado(P6, "eh")
    ENVIOS.clear()
    check(not a(post(P6).telefono), "una postulación que ya estaba en proceso solo toma la foto: no recibe avisos de golpe")
    p6 = post(P6)
    p6.etapa = "Contratación"
    db.commit()
    _abrir_expediente(db, post(P6), admin)
    e6 = post(P6).expediente
    e6.puesto, e6.sueldo, e6.tipo_contratacion, e6.fecha_ingreso = "Almacenista", "$11,000", "Tiempo indeterminado", datetime(2026, 11, 3, tzinfo=timezone.utc)
    e6.condiciones_guardadas_en = datetime.now(timezone.utc)
    db.query(Bitacora).filter(Bitacora.accion == "propuesta_legado_2026_10_10").delete()
    db.commit()
    n_legado = propuesta.marcar_legado(db)
    db.commit()
    check(n_legado >= 1 and propuesta.estado(post(P6)).get("estado") == "aceptada" and propuesta.estado(post(P6)).get("legado") is True
          and paso(P6, "condiciones")["estado"] == "completada", "condiciones guardadas ANTES del flujo nuevo → se conservan cumplidas (legado)")
    ev = ia.evaluar_entrevista("Almacenista", "Secundaria", [{"rol": "user", "texto": "Trabajé 3 años en almacén haciendo inventarios."}])[0]
    ev2 = ia._aplicar_inconsistencias(ia.EvaluacionEntrevista(
        resumen="x", fortalezas=["a"], riesgos=["No acreditó su escolaridad (secundaria)", "Falta su INE", "Validar manejo de montacargas"],
        calif_experiencia=7, calif_comunicacion=7, match_perfil=70, score_entrevista=70, recomendacion="revision", evidencia="x",
        faltante=["Certificado de estudios"]))
    check(ev2.riesgos == ["Validar manejo de montacargas"] and ev2.faltante == [] and isinstance(ev.riesgos, list),
          "la entrevista IA no deja alertas de escolaridad ni documentos (se validan en Documentos)")
    check(notif._mensaje("candidato_apto", "candidato", "whatsapp", post(P), None, "", {}) is None,
          "«candidato_apto» ya no le escribe al candidato (el aviso de estado lo sustituye)")
    db.close()

print(f"\n🎉 {OK} comprobaciones OK — avisos de estado y propuesta")
