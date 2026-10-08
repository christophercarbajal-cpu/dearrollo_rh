"""Regresión de la continuidad de la ruta (2026-10-08). Base desechable, SIN claves reales (canales y proveedor simulados).

    .venv/Scripts/python.exe scripts/verificar_excepcion_referencias.py

Cadena completa:  Entrevista Red Human < mínimo → bloqueo («Confirmar descarte» / «Continuar por decisión de RH») → RH
continúa con motivo → se libera SIN tocar el score ni omitir → el motor avanza (avance automático de la Cuenta) →
«Solicitar referencias» → el candidato entrega contactos → «Pendiente de revisión» + aviso al responsable → el
responsable registra la revisión → actividad aprobada.
Además: ruta automática (demo-grupak) dispara sola la solicitud de referencias; captura manual de contactos verificados;
psicometría sin correo = «Falta correo para enviar la prueba» (no «Error de envío») con «Agregar correo» como acción
principal, y al guardar el correo el envío se retoma SOLO y sin duplicados.
"""

import asyncio
import itertools
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_dir = tempfile.mkdtemp(prefix="rh_continuidad_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "continuidad.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD", "PSICOMETRICAS_WEBHOOK_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-continuidad"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import app.services.correo as correo_srv  # noqa: E402
import app.services.notificaciones as notif  # noqa: E402
import app.services.whatsapp as wa  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Bitacora, Cuenta, Evaluacion, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import motor_ruta  # noqa: E402
from app.services import proceso as sproc  # noqa: E402
from app.services import psicometricas as psi  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402
from app.services.entrevistas import crear_entrevista_para_candidato  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


ENVIOS = []
_ids = itertools.count(1)


async def _wa(telefono, texto, *a, **k):
    ENVIOS.append(("whatsapp", telefono, texto))
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "wa_id": f"wamid.C{next(_ids)}"}


async def _wa_boton(telefono, texto, boton, url, *a, **k):
    return await _wa(telefono, f"{texto} {url}")


async def _correo(destino, asunto, html, *a, **k):
    ENVIOS.append(("correo", destino, asunto))
    return {"enviado": True, "proveedor": "resend", "detalle": "ok"}


wa.enviar_mensaje = notif.enviar_mensaje = _wa
wa.enviar_con_boton = _wa_boton
correo_srv.enviar_correo = notif.enviar_correo = _correo


class R:
    def __init__(self, status, datos=None):
        self.status_code, self._d, self.content, self.text, self.headers = status, datos, b"", "", {}

    def json(self):
        return self._d


LLAMADAS_PROVEEDOR = []

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = admin.telefono or "5599990000"
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    cuenta = Cuenta(nombre="Continuidad SA", nombre_comercial="Continuidad", estado="Activa")
    auto = Cuenta(nombre="Grupak Demo", nombre_comercial="Grupak", estado="Activa", slug="demo-grupak")
    db.add_all([cuenta, auto])
    db.flush()
    db.add_all([UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id), UsuarioCuenta(usuario_id=admin.id, cuenta_id=auto.id)])
    obtener(db).modo_prueba = False
    vacs = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).all()[:2]
    vacs[0].cuenta_id, vacs[1].cuenta_id = cuenta.id, auto.id
    for v in vacs:
        v.cliente_id, v.proceso, v.responsable_id = None, {}, admin.id
    db.commit()
    V, VA = vacs[0].codigo, vacs[1].codigo
    actual = {"c": cuenta}
    app.dependency_overrides[cuenta_actual] = lambda: actual["c"]
    AUTO = {e: {"avance_automatico": e in ("Prefiltro", "Entrevista IA", "Entrevista Humana")}
            for e in ("Prefiltro", "Entrevista IA", "Entrevista Humana", "Contratación", "Onboarding")}
    PASOS = [
        {"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
        {"id": "entrevista", "tipo": "entrevista_agente", "etapa": "Entrevista IA", "regla": {"tipo": "calificacion", "minimo": 70}},
        {"id": "refs", "tipo": "referencias", "etapa": "Entrevista Humana"},
        {"id": "cond", "tipo": "condiciones", "etapa": "Contratación"},
    ]
    r = client.put(f"/procesos/vacantes/{V}", json={"pasos": PASOS, "etapas": AUTO})
    assert r.status_code == 200, r.text
    tel = iter(range(5586000000, 5586999999))

    def nueva(nombre, vac=V, correo=None):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)),
                                             "correo": f"{nombre.split()[0].lower()}@cont.mx" if correo is None else correo,
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

    # ================= 1. Entrevista bajo el mínimo → excepción de RH =================
    print("\n--- 1. Resultado inferior al mínimo → «Continuar por decisión de RH» ---")
    P = nueva("Iván Intermedio")
    p = post(P)
    p.etapa = "Entrevista IA"
    e, _ = crear_entrevista_para_candidato(db, p, "prueba")
    e.estado = "evaluada"
    e.evaluacion = {"score_entrevista": 68, "match_perfil": 90, "calif_experiencia": 7, "calif_comunicacion": 7,
                    "fortalezas": ["Puntual"], "riesgos": [], "recomendacion": "revision"}
    db.commit()
    asyncio.run(sproc.avanzar_seguro(db, post(P)))
    s = seg(P)
    x = paso(s, "entrevista")
    check(x["estadoUnificado"] == "no_aprobada" and x["score"] == 68, "entrevista 68/100 con mínimo 70 → «No aprobada»")
    check(s["bloqueo"] and s["bloqueo"]["paso"] == "entrevista" and post(P).etapa == "Entrevista IA",
          "la ruta se DETIENE con un bloqueo explícito (también fuera de demo-grupak); el candidato sigue en su etapa")
    check(s["siguienteAccion"]["tipo"] == "bloqueo" and s["recomendacion"]["texto"] == "Decisión de RH pendiente",
          "acción principal y recomendación salen del MISMO bloqueo (nunca un «Registrar resultado» genérico)")
    check(any(m["clave"] == "continuar_excepcion" and m["texto"] == "Continuar por decisión de RH" for m in x["menu"]),
          "la actividad ofrece «Continuar por decisión de RH» en su «…»")
    check(client.post(f"/procesos/postulaciones/{P}/pasos/entrevista/excepcion", json={"motivo": "corto"}).status_code == 400,
          "sin motivo suficiente → 400")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/entrevista/excepcion", json={"motivo": "Experiencia probada en planta; RH decide continuar"})
    check(r.status_code == 200, "RH continúa con motivo")
    s = r.json()["proceso"]
    x = paso(s, "entrevista")
    check(x["score"] == 68 and x["resultado"] == "no_favorable" and x["estado"] == "completada" and x["estado"] != "omitida",
          "el score reprobatorio (68) y el resultado se CONSERVAN; la actividad NO queda omitida")
    check(x["estadoUnificado"] == "aprobada_excepcion" and x["excepcionRH"]["por"] == admin.nombre and x["cumpleRegla"],
          "estado «Continúa por decisión de RH» con quién lo decidió; ya no bloquea")
    check(not s["bloqueo"] and not s["descarteSugerido"], "el bloqueo se liberó")
    check(post(P).etapa == "Entrevista Humana", "el motor se recalculó en el momento y AVANZÓ (avance automático de la Cuenta)")
    integ = client.get(f"/candidatos/{P}").json().get("resultadoIntegral") or {}
    v = next((y for y in integ.get("validaciones", []) if "Entrevista" in y.get("nombre", "")), {})
    check(v.get("estado") == "observaciones" and "decisión de RH" in v.get("resultado", ""),
          "evaluación integral: la entrevista cuenta «con observaciones» (decisión de RH), sin esconder el 68/100")
    check(db.query(Bitacora).filter(Bitacora.accion == "proceso_excepcion_rh").count() == 1, "queda en bitácora")
    check(client.post(f"/procesos/postulaciones/{P}/pasos/entrevista/excepcion", json={"motivo": "Otra vez lo mismo, por favor"}).status_code == 409,
          "no se aplica dos veces")

    # ================= 2. Referencias sin bloqueos =================
    print("\n--- 2. Referencias laborales: solicitar → entregar → revisar ---")
    s = seg(P)
    x = paso(s, "refs")
    check(x["accion"]["texto"] == "Solicitar referencias" and s["siguienteAccion"]["accion"]["clave"] == "iniciar_evaluacion"
          and "Solicitar referencias" in s["siguienteAccion"]["texto"],
          "sin enviar: la acción PRINCIPAL es «Solicitar referencias»")
    tel_p = post(P).telefono
    ENVIOS.clear()
    r = client.post(f"/procesos/postulaciones/{P}/pasos/refs/iniciar", json={})
    check(r.status_code == 200 and r.json()["iniciada"] and any(z[1] == tel_p for z in ENVIOS), "solicitud enviada al candidato")
    x = paso(seg(P), "refs")
    check(x["estadoUnificado"] == "esperando_referencias" and x["estadoUnificadoTexto"] == "Esperando referencias del candidato",
          "estado «Esperando referencias del candidato»")
    check(any(m["clave"] == "reenviar_candidato" and m["texto"] == "Reenviar al candidato" for m in x["menu"]),
          "ya enviada: «Reenviar al candidato» vive en el «…»")
    ev = db.query(Evaluacion).filter(Evaluacion.postulacion_id == post(P).id, Evaluacion.paso_id == "refs").one()
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/publica/referencias/{ev.referencias_token}", json={"referencias": [
        {"nombre": "Laura Jefa", "empresa": "Acme", "relacion": "Jefa directa", "telefono": "5512345678"},
        {"nombre": "Pedro Par", "empresa": "Beta", "correo": "pedro@beta.mx"}]})
    check(r.status_code == 200 and (any(z[1] == admin.correo for z in ENVIOS) or any(z[1] == admin.telefono for z in ENVIOS)),
          "el candidato entrega sus contactos → se notifica al responsable")
    s = seg(P)
    x = paso(s, "refs")
    check(x["estadoUnificado"] == "pendiente_revision" and x["estadoUnificadoTexto"] == "Pendiente de revisión", "estado «Pendiente de revisión»")
    check(s["siguienteAccion"]["tipo"] == "paso" and s["siguienteAccion"]["accion"]["clave"] == "registrar_resultado"
          and "Registrar revisión de referencias" in s["siguienteAccion"]["texto"],
          "acción principal específica: «Registrar revisión de referencias»")
    check(sum(1 for m in x["menu"] if "registrar" in m["clave"] or (m.get("accion") or {}).get("clave") == "registrar_resultado") == 0,
          "sin duplicar «Registrar resultado» en el «…» (ya es el botón principal)")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/refs/resultado", data={"conclusion": "favorable", "comentarios": "Ambas recomiendan",
                                                                               "realizada_por": "RH"})
    s = r.json()["proceso"]
    check(r.status_code == 200 and paso(s, "refs")["estadoUnificado"] == "aprobada", "el responsable registra la revisión → «Aprobada»")
    check(post(P).etapa == "Contratación", "la ruta continúa sola hasta Contratación (avance automático de la Cuenta)")

    # ================= 3. Ruta automática + captura manual =================
    print("\n--- 3. Ruta automática: referencias se solicitan solas · captura manual ---")
    actual["c"] = auto
    PASOS_AUTO = [{"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
                  {"id": "refs", "tipo": "referencias", "etapa": "Prefiltro"}]
    assert client.put(f"/procesos/vacantes/{VA}", json={"pasos": PASOS_AUTO, "etapas": AUTO}).status_code == 200
    PA = nueva("Ana Automática", VA)
    asyncio.run(motor_ruta.procesar(db, post(PA)))
    x = paso(seg(PA), "refs")
    check(x["evaluacion"] and x["estadoUnificado"] == "esperando_referencias",
          "demo-grupak: al habilitarse, la solicitud de referencias se dispara SOLA")
    asyncio.run(motor_ruta.procesar(db, post(PA)))
    check(db.query(Evaluacion).filter(Evaluacion.postulacion_id == post(PA).id, Evaluacion.tipo == "referencias").count() == 1,
          "el motor no la duplica")
    r = client.post(f"/procesos/postulaciones/{PA}/pasos/refs/resultado", data={
        "conclusion": "favorable", "realizada_por": "RH por teléfono",
        "referencias": '[{"nombre": "Marta Gómez", "empresa": "Gamma", "telefono": "55 4444 3333", "dictamen": "favorable", "comentario": "Excelente"}]'})
    ev = db.query(Evaluacion).filter(Evaluacion.postulacion_id == post(PA).id, Evaluacion.tipo == "referencias").one()
    db.refresh(ev)
    check(r.status_code == 200 and ev.estado == "con_resultado" and ev.referencias[0]["nombre"] == "Marta Gómez"
          and ev.referencias[0]["telefono"] == "5544443333" and ev.referencias[0]["dictamen"] == "favorable",
          "«Registrar resultado» captura los CONTACTOS verificados fuera del sistema (no solo Favorable/No favorable)")
    actual["c"] = cuenta

    # ================= 4. Psicometría sin correo =================
    print("\n--- 4. Psicometría detenida por falta de correo → «Agregar correo» → se retoma sola ---")
    GER = client.post("/evaluaciones/pruebas", json={"nombre": "Batería Operativa", "tipo": "bateria", "id_proveedor": "1,7",
                                                     "activa": True, "modo": "integrada", "proveedor": "Psicométricas.mx"}).json()["id"]
    PASOS_PSI = [{"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
                 {"id": "psico", "tipo": "psicometrica", "etapa": "Prefiltro", "pruebas": [GER]}]
    VP = vacs[0].codigo
    assert client.put(f"/procesos/vacantes/{VP}", json={"pasos": PASOS_PSI, "etapas": AUTO}).status_code == 200
    settings.psicometricas_token, settings.psicometricas_password = "T" * 20, "P" * 20
    orig_post = psi.httpx.post
    psi.httpx.post = lambda url, data=None, timeout=None: (LLAMADAS_PROVEEDOR.append(data), R(200, {"status": "200", "clave": "1-CONT-0001"}))[1]
    try:
        PS = nueva("Sergio Sincorreo", VP, correo="")
        r = client.post(f"/procesos/postulaciones/{PS}/pasos/psico/iniciar", json={})
        check(r.status_code == 200 and r.json()["faltan"] == ["correo"] and not LLAMADAS_PROVEEDOR,
              "sin correo: no se llama al proveedor (saldo intacto) ni se crea nada")
        s = seg(PS)
        x = paso(s, "psico")
        check(x["estadoUnificado"] == "falta_correo" and x["estadoUnificadoTexto"] == "Falta correo para enviar la prueba" and not x["error"],
              "estado «Falta correo para enviar la prueba» (NO «Error de envío»)")
        check(s["siguienteAccion"]["accion"]["clave"] == "agregar_correo" and s["siguienteAccion"]["texto"] == "Agregar correo",
              "la acción principal es «Agregar correo»")
        tarjeta = next(y for y in client.get("/candidatos").json() if y["id"] == PS)
        check(tarjeta.get("psychometric_alert") in (None, "falta_correo"), "el tablero no lo reporta como error de envío")
        r = client.patch(f"/candidatos/{PS}/contacto", json={"correo": "sergio@cont.mx", "reanudar_paso": "psico"})
        re = r.json().get("psicometriaReanudada") or []
        check(r.status_code == 200 and re and re[0]["ok"] and len(LLAMADAS_PROVEEDOR) == 1,
              "al guardar el correo, el envío de la psicometría se RETOMA solo (una sola alta en el proveedor)")
        x = paso(seg(PS), "psico")
        check(x["evaluacion"] and x["estadoUnificado"] == "esperando_candidato" and not x["faltaCorreo"], "…y queda «Esperando candidato»")
        client.patch(f"/candidatos/{PS}/contacto", json={"correo": "sergio.r@cont.mx", "reanudar_paso": "psico"})
        check(len(LLAMADAS_PROVEEDOR) == 1 and db.query(Evaluacion).filter(Evaluacion.postulacion_id == post(PS).id).count() == 1,
              "editar el correo otra vez NO duplica la prueba ni vuelve a llamar al proveedor")
    finally:
        psi.httpx.post = orig_post
        settings.psicometricas_token = settings.psicometricas_password = ""
    db.close()

print(f"\n🎉 Continuidad de la ruta: excepción de RH, referencias de punta a punta y psicometría sin correo — {OK} verificaciones OK")
