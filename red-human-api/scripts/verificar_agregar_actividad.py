"""Regresión de «Agregar actividad» configurada en un paso (2026-10-08). Base desechable, SIN claves reales.

    .venv/Scripts/python.exe scripts/verificar_agregar_actividad.py

1. Precarga: lo que define la vacante (evaluador responsable, batería) llega lleno y marcado «de la vacante».
2. Validación condicional (las mismas reglas que el formulario): si falta un campo del tipo/forma elegido → 400 y NO se
   crea nada.
3. Guardar inserta UNA actividad (etapa actual) con su configuración; «Iniciar al guardar» la inicia; sin él queda
   «Lista para iniciar» y el botón «Iniciar» ejecuta lo guardado SIN pedir nada.
4. Médica: examen solicitado en las instrucciones y consentimiento automático. Referencias: cantidad y datos pedidos
   llegan a la liga del candidato y se validan.
5. «Registrar evaluación ya realizada»: resultado (dictamen, score) en la misma actividad, sin evaluación duplicada; la
   médica no se puede registrar así (consentimiento LFPDPPP).
6. Si a «Iniciar» le falta un dato crítico, pide SOLO ese dato y no duplica.
"""

import itertools
import json
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

_dir = tempfile.mkdtemp(prefix="rh_agregar_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "agregar.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "ANAM_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN",
          "PSICOMETRICAS_TOKEN", "PSICOMETRICAS_PASSWORD"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-agregar"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import app.services.correo as correo_srv  # noqa: E402
import app.services.notificaciones as notif  # noqa: E402
import app.services.whatsapp as wa  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Evaluacion, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services.configuracion import obtener  # noqa: E402

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
    return {"enviado": True, "proveedor": "meta", "detalle": "ok", "wa_id": f"wamid.A{next(_ids)}"}


async def _wa_boton(telefono, texto, boton, url, *a, **k):
    return await _wa(telefono, f"{texto} {url}")


async def _correo(destino, asunto, html, *a, **k):
    ENVIOS.append(("correo", destino, asunto))
    return {"enviado": True, "proveedor": "resend", "detalle": "ok"}


wa.enviar_mensaje = notif.enviar_mensaje = _wa
wa.enviar_con_boton = _wa_boton
correo_srv.enviar_correo = notif.enviar_correo = _correo

with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    admin.telefono = admin.telefono or "5599990000"
    for dep in (usuario_actual, usuario_decisor):
        app.dependency_overrides[dep] = lambda: admin
    cuenta = Cuenta(nombre="Agregar SA", nombre_comercial="Agregar", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    obtener(db).modo_prueba = False
    v = db.query(Vacante).filter(Vacante.estado == "Publicada").order_by(Vacante.id).first()
    v.cuenta_id, v.cliente_id, v.proceso, v.responsable_id = cuenta.id, None, {}, admin.id
    db.commit()
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    GER = client.post("/evaluaciones/pruebas", json={"nombre": "Batería Ventas", "tipo": "bateria", "id_proveedor": "1,7", "activa": True,
                                                     "modo": "integrada", "proveedor": "Psicométricas.mx"}).json()["id"]
    PASOS = [
        {"id": "solicitud", "tipo": "solicitud_web", "etapa": "Prefiltro"},
        {"id": "eh", "tipo": "entrevista_humana", "etapa": "Entrevista Humana", "responsable": {"tipo": "usuario", "usuario_id": admin.id}},
        {"id": "psico", "tipo": "psicometrica", "etapa": "Entrevista Humana", "pruebas": [GER]},
    ]
    assert client.put(f"/procesos/vacantes/{v.codigo}", json={"pasos": PASOS}).status_code == 200
    tel = iter(range(5587000000, 5587999999))

    def nueva(nombre):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)), "correo": f"{nombre.split()[0].lower()}@agr.mx",
                                             "vacante": v.codigo, "consentimiento": True, "fuente": "RH"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def agregar(codigo, datos):
        return client.post(f"/procesos/postulaciones/{codigo}/actividades", data={"datos": json.dumps(datos)})

    def seg(codigo):
        return client.get(f"/procesos/postulaciones/{codigo}").json()

    def paso(s, pid):
        return next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == pid)

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    def evs(codigo, paso_id=None):
        q = db.query(Evaluacion).filter(Evaluacion.postulacion_id == post(codigo).id)
        return (q.filter(Evaluacion.paso_id == paso_id) if paso_id else q).all()

    def n_pasos(codigo):
        return len(post(codigo).proceso["pasos"])

    P = nueva("Paula Precarga")
    etapa0 = post(P).etapa

    print("\n--- 1. Precarga desde la vacante ---")
    pre = client.get(f"/procesos/postulaciones/{P}/actividades/precarga?tipo=entrevista_humana").json()
    check(pre["config"]["evaluador"] == {"tipo": "interno", "usuario_id": admin.id} and pre["origen"]["evaluador"] == "vacante",
          "entrevista humana: el entrevistador responsable de la vacante llega precargado (marcado «de la vacante»)")
    pre = client.get(f"/procesos/postulaciones/{P}/actividades/precarga?tipo=psicometrica").json()
    check(pre["config"]["prueba_ids"] == [GER] and pre["config"]["forma"] == "integrada", "psicométrica: la batería de la vacante llega precargada")
    pre = client.get(f"/procesos/postulaciones/{P}/actividades/precarga?tipo=referencias").json()
    check(pre["config"]["evaluador"]["usuario_id"] == admin.id and pre["config"]["referencias"]["cantidad"] >= 1,
          "referencias: responsable de verificar = responsable de la vacante; cantidad y datos por defecto")
    check(client.get(f"/procesos/postulaciones/{P}/actividades/precarga?tipo=medica").json()["puedeRegistrarRealizada"] is False,
          "médica: no se ofrece «ya realizada» (consentimiento por liga)")

    print("\n--- 2. Validación condicional (nada se crea si falta algo) ---")
    antes = n_pasos(P)
    casos = [
        ({"tipo": "medica", "config": {"evaluador": {"tipo": "externo", "nombre": "Dr. X", "correo": "x@clinica.mx"}}}, "examen"),
        ({"tipo": "psicometrica", "config": {"forma": "integrada"}}, "prueba"),
        ({"tipo": "psicometrica", "config": {"forma": "liga_otro_sistema", "liga_externa_candidato": "no-es-url"}}, "liga"),
        ({"tipo": "tecnica", "config": {"forma": "asignada", "evaluador": {"tipo": "externo", "nombre": "Ing"}}}, "correo"),
        ({"tipo": "otra", "config": {"forma": "registro_directo"}}, "nombre"),
        ({"tipo": "entrevista_humana", "ya_realizada": True, "resultado": {"comentarios": "bien"}}, "conclusión"),
    ]
    for datos, palabra in casos:
        r = agregar(P, datos)
        check(r.status_code == 400 and palabra in r.json()["detail"].lower() and n_pasos(P) == antes,
              f"{datos['tipo']}: falta «{palabra}» → 400 y no se crea la actividad")
    r = agregar(P, {"tipo": "medica", "ya_realizada": True, "resultado": {"conclusion": "apto"}})
    check(r.status_code == 409 and n_pasos(P) == antes, "médica «ya realizada» → 409 (consentimiento LFPDPPP), nada creado")

    print("\n--- 3. Guardar = UNA actividad configurada · «Lista para iniciar» · «Iniciar» sin reconfigurar ---")
    CITA = {"fecha": "2026-12-15", "hora": "10:00", "modalidad": "Presencial", "direccion": "Av. Reforma 100, CDMX"}  # 2026-10-10: entrevista humana, médica y técnica presenciales se agendan para iniciarse
    r = agregar(P, {"tipo": "entrevista_humana", "nombre": "Entrevista con gerente", "config": {
        "evaluador": {"tipo": "interno", "usuario_id": admin.id}, "cita": CITA, "iniciar_al_guardar": False}})
    pid = r.json()["paso"]["id"]
    x = paso(r.json()["proceso"], pid)
    check(r.status_code == 201 and n_pasos(P) == antes + 1 and x["etapa"] == etapa0 and post(P).etapa == etapa0,
          "una sola actividad nueva, en la etapa ACTUAL; el candidato no se mueve")
    check(x["estadoUnificado"] == "lista_para_iniciar" and x["configurada"] and not evs(P, pid),
          "sin «Iniciar al guardar»: queda «Lista para iniciar» con su configuración (todavía sin evaluación)")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/{pid}/iniciar", json={})
    e = evs(P, pid)
    check(r.status_code == 200 and r.json()["iniciada"] and not r.json().get("faltan") and len(e) == 1
          and e[0].evaluador_usuario_id == admin.id and e[0].nombre == "Entrevista con gerente",
          "«Iniciar» ejecuta lo guardado sin pedir nada (mismo entrevistador y nombre)")
    client.post(f"/procesos/postulaciones/{P}/pasos/{pid}/iniciar", json={})
    check(len(evs(P, pid)) == 1, "«Iniciar» otra vez no duplica")
    r = agregar(P, {"tipo": "tecnica", "config": {"forma": "liga_otro_sistema", "liga_externa_candidato": "https://pruebas.mx/caso",
                                                  "instrucciones": "Caso práctico de ventas", "iniciar_al_guardar": True}})
    pid_t = r.json()["paso"]["id"]
    e = evs(P, pid_t)
    check(r.status_code == 201 and r.json()["iniciada"] and len(e) == 1 and e[0].forma == "liga_otro_sistema"
          and e[0].liga_externa_candidato == "https://pruebas.mx/caso" and e[0].instrucciones == "Caso práctico de ventas",
          "«Iniciar al guardar»: técnica con liga externa iniciada en el mismo paso, con su configuración")

    print("\n--- 4. Médica y referencias configuradas ---")
    ENVIOS.clear()
    r = agregar(P, {"tipo": "medica", "config": {"evaluador": {"tipo": "externo", "nombre": "Dra. Salud", "correo": "dra@clinica.mx"},
                                                 "examen": "Examen general + antidoping", "cita": CITA, "iniciar_al_guardar": True}})
    pid_m = r.json()["paso"]["id"]
    e = evs(P, pid_m)
    check(r.json()["iniciada"] and e[0].consentimiento == "pendiente" and "Examen solicitado: Examen general + antidoping" in e[0].instrucciones,
          "médica: examen solicitado en las instrucciones y consentimiento pendiente")
    check(any("consentimiento" in z[2].lower() for z in ENVIOS) and not any(z[1] == "dra@clinica.mx" for z in ENVIOS),
          "el consentimiento sale solo al candidato; al médico nada todavía")
    r = agregar(P, {"tipo": "referencias", "config": {"evaluador": {"tipo": "interno", "usuario_id": admin.id},
                                                      "referencias": {"cantidad": 3, "datos": ["telefono", "puesto"]}, "iniciar_al_guardar": True}})
    e = evs(P, r.json()["paso"]["id"])[0]
    vista = client.get(f"/evaluaciones/publica/referencias/{e.referencias_token}").json()
    check(vista["minimo"] == 3 and vista["datos"] == ["telefono", "puesto"], "referencias: la liga del candidato pide 3 con teléfono y puesto")
    dos = [{"nombre": "Uno Uno", "empresa": "A", "puesto": "Jefe", "telefono": "5511111111"},
           {"nombre": "Dos Dos", "empresa": "B", "puesto": "Par", "telefono": "5522222222"}]
    check(client.post(f"/evaluaciones/publica/referencias/{e.referencias_token}", json={"referencias": dos}).status_code == 400,
          "con menos de las pedidas → 400")
    sin_puesto = dos + [{"nombre": "Tres Tres", "empresa": "C", "telefono": "5533333333"}]
    r = client.post(f"/evaluaciones/publica/referencias/{e.referencias_token}", json={"referencias": sin_puesto})
    check(r.status_code == 400 and "puesto" in r.json()["detail"], "falta un dato pedido (puesto) → 400")
    completas = dos + [{"nombre": "Tres Tres", "empresa": "C", "puesto": "Gerente", "telefono": "5533333333"}]
    check(client.post(f"/evaluaciones/publica/referencias/{e.referencias_token}", json={"referencias": completas}).status_code == 200,
          "con lo pedido completo → se guardan")

    print("\n--- 5. «Registrar evaluación ya realizada» ---")
    antes = len(evs(P))
    r = agregar(P, {"tipo": "socioeconomica", "nombre": "Estudio socioeconómico (papel)", "ya_realizada": True,
                    "resultado": {"conclusion": "favorable", "score": 85, "realizada_por": "Agencia ABC", "comentarios": "Visita domiciliaria"}})
    pid_s = r.json()["paso"]["id"]
    e = evs(P, pid_s)
    check(r.status_code == 201 and r.json()["yaRealizada"] and len(e) == 1 and len(evs(P)) == antes + 1, "una sola evaluación, ya con resultado")
    check(e[0].estado == "con_resultado" and e[0].conclusion == "favorable" and e[0].realizada_por == "Agencia ABC"
          and (e[0].resultado_json or {}).get("score") == 85 and "Score: 85/100" in e[0].comentarios,
          "dictamen, score, quién la aplicó y comentarios quedan en esa actividad")
    check(paso(seg(P), pid_s)["estado"] == "completada", "la actividad queda completada (no «lista para iniciar»)")

    print("\n--- 6. A «Iniciar» le falta un dato crítico → pide SOLO ese ---")
    r = agregar(P, {"tipo": "tecnica", "config": {"forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
                                                  "cita": CITA, "iniciar_al_guardar": False}})
    pid_x = r.json()["paso"]["id"]
    p = post(P)
    proc = json.loads(json.dumps(p.proceso))
    for y in proc["pasos"]:
        if y["id"] == pid_x:
            y["config"].pop("evaluador")
            y["responsable"] = {"tipo": "externo", "nombre": ""}
    p.proceso = proc
    db.commit()
    r = client.post(f"/procesos/postulaciones/{P}/pasos/{pid_x}/iniciar", json={})
    check(r.json()["iniciada"] is False and r.json()["faltan"] == ["evaluador"] and not evs(P, pid_x),
          "pide SOLO el evaluador (no nombre, tipo ni responsable) y no crea nada")
    r = client.post(f"/procesos/postulaciones/{P}/pasos/{pid_x}/iniciar", json={"evaluador": {"tipo": "externo", "nombre": "Ing. Ruiz", "correo": "ruiz@x.mx"}})
    check(r.json()["iniciada"] and len(evs(P, pid_x)) == 1 and evs(P, pid_x)[0].forma == "asignada",
          "con ese dato se inicia con el resto de lo guardado (forma asignada) y sin duplicar")
    db.close()

print(f"\n🎉 Agregar actividad configurada en un paso — {OK} verificaciones OK")
