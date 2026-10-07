"""Verificación de los 6 bugs reportados el 2026-09-17 (modo demo, base desechable):

1. Liga de videollamada UNA sola vez (aunque el modelo la copie en su respuesta).
2. Entrevista IA: transcript persistido en vivo, /finalizar usa el más completo, cierre por inactividad
   evalúa con lo que hay, «Evaluar con lo que hay» para RH.
3/4. Descartar candidato con expediente abierto (Contratación/Onboarding) cancela el expediente.
5. Expediente visible en Onboarding (payload trae expedienteId + documentos pendientes).
6. Portal por Cuenta: Eliminada no sale, `?cuenta=` aísla, homónimas de otras Cuentas se explican.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_bugs_2026_09_17.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_bugs_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "bugs.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-bugs"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Cuenta, Entrevista, Expediente, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
import app.routers.candidatos as rc  # noqa: E402
import app.routers.entrevistas as re_  # noqa: E402
from app.services import ia  # noqa: E402

OK = 0
ENVIOS = []


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _fake(tel, texto="", *a, **k):
    ENVIOS.append((tel, texto))
    return {"enviado": True, "proveedor": "meta"}


rc._enviar_whatsapp = lambda p, texto, canal: _fake(p.telefono, texto)
re_.enviar_mensaje = _fake


def nueva_vacante(client, titulo, publicar=True):
    r = client.post("/vacantes", json={
        "titulo": titulo, "descripcion": "Descripción", "generar_si_falta": False, "publicar": publicar,
        "ubicacion_estado": "Jalisco", "ubicacion_municipio": "Zapopan",
        "sueldo_desde": 9000, "sueldo_hasta": 11000, "sueldo_periodicidad": "mensual",
    })
    assert r.status_code == 201, r.text
    return r.json()


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    ca = Cuenta(nombre="Grupo CARBE", nombre_comercial="Grupo CARBE", estado="Activa")
    cb = Cuenta(nombre="Otra Cuenta", nombre_comercial="Otra Empresa", estado="Activa")
    db.add_all([ca, cb])
    db.flush()
    db.add_all([UsuarioCuenta(usuario_id=admin.id, cuenta_id=ca.id), UsuarioCuenta(usuario_id=admin.id, cuenta_id=cb.id)])
    for v in db.query(Vacante).all():
        v.cuenta_id = ca.id
    db.commit()
    from app.seed import rellenar_slugs_cuentas
    rellenar_slugs_cuentas(db)
    db.refresh(ca); db.refresh(cb)
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: ca

    # ================= 1. Liga única =================
    print("\n--- 1. Liga de videollamada una sola vez ---")
    v1 = nueva_vacante(client, "Cajero Bug1")
    r = client.post("/candidatos", json={"nombre": "Liga Uno", "telefono": "5511110001", "vacante": v1["id"], "consentimiento": True, "fuente": "RH"})
    P1 = r.json()["id"]
    p1 = db.query(Postulacion).filter(Postulacion.codigo == P1).one()

    def _agenda_falsa(nombre, titulo, historial, *, db, candidato, nota=""):
        from app.services.entrevistas import crear_entrevista_para_candidato
        e, _ = crear_entrevista_para_candidato(db, candidato, "agente-ia")
        liga = f"http://app.test/entrevista/{e.token}"
        t = ia.TurnoPrefiltro(respuesta=f"¡Listo! Tu entrevista quedó para mañana a las 10:00. Aquí está tu liga: {liga} ¡Éxito!", clasificacion_lista=False)
        t.cita_fecha_hora = "2026-09-18T10:00:00-06:00"
        t.cita_liga = liga
        return t, True

    ia.agenda_turno = _agenda_falsa
    ENVIOS.clear()
    res = asyncio.run(rc._procesar_turno_agenda(db, p1, [{"rol": "user", "texto": "mañana a las 10"}], "whatsapp"))
    texto = res["respuesta"]
    check(texto.count("http") == 1, f"la respuesta trae UNA sola liga (antes 2): «{texto[-80:]}»")
    check(texto.endswith(res["cita"]["liga"]) and "Aquí está tu liga:" in texto, "la liga es la real del sistema y el texto del modelo se conserva sin la URL")
    check(rc._sin_ligas("Hola https://a.b/x  \n\n\n\nfin") == "Hola\n\nfin", "_sin_ligas limpia URL y espacios sobrantes")

    # ================= 2. Persistencia de la Entrevista IA =================
    print("\n--- 2. Entrevista IA: transcript en vivo y cierre por inactividad ---")
    db.expire_all()
    e1 = db.query(Entrevista).filter(Entrevista.postulacion_id == p1.id).order_by(Entrevista.id.desc()).first()
    tok = e1.token
    assert client.post(f"/entrevistas/publica/{tok}/consentimiento", json={"acepta": True}).status_code == 200
    r = client.post(f"/entrevistas/publica/{tok}/sesion", json={"modo": "texto"})
    check(r.status_code == 200, "sesión iniciada (texto)")
    db.expire_all()
    e1 = db.query(Entrevista).get(e1.id)
    check(e1.estado == "en_curso" and e1.ultima_actividad_en is not None, "al iniciar se estampa ultima_actividad_en")
    transcript = [{"rol": "assistant", "texto": "Hola, cuéntame de tu experiencia"}]
    for i in range(6):
        transcript.append({"rol": "user", "texto": f"Respuesta número {i} con contenido suficiente sobre mi experiencia laboral en caja y atención."})
        transcript.append({"rol": "assistant", "texto": f"Pregunta {i + 1}"})
    r = client.post(f"/entrevistas/publica/{tok}/transcript", json={"transcript": transcript})
    check(r.status_code == 200 and r.json()["turnos"] == len(transcript), "POST /transcript guarda el transcript en vivo")
    r = client.post(f"/entrevistas/publica/{tok}/transcript", json={"transcript": []})
    check(r.json()["turnos"] == len(transcript), "un historial vacío tardío NO borra lo guardado")
    # /finalizar con transcript vacío (stopStreaming vació el historial) → usa el del servidor y EVALÚA
    r = client.post(f"/entrevistas/publica/{tok}/finalizar", json={"transcript": [], "cierre": "desconexion"})
    check(r.status_code == 200 and r.json()["estado"] == "evaluada", f"/finalizar con transcript vacío usa el sincronizado → {r.json()['estado']} (antes: interrumpida sin_respuestas)")
    check(bool(r.json()["evaluacion"]) and r.json()["evaluacion"].get("recomendacion"), "la evaluación quedó guardada (recomendación + match)")
    db.expire_all()
    p1 = db.query(Postulacion).get(p1.id)
    check(p1.etapa == "Entrevista IA", "la postulación se queda en Filtro Red Human con su resultado integral")
    r = client.get(f"/candidatos/{P1}")
    ficha = r.json()
    check(ficha["entrevistaStatus"]["estado"] == "evaluada" and ficha["evaluacionIntegral"] is True, "la ficha muestra la Entrevista Red Human evaluada y la Evaluación integral")

    # cierre por inactividad
    v2 = nueva_vacante(client, "Almacenista Bug2")
    r = client.post("/candidatos", json={"nombre": "Pestaña Cerrada", "telefono": "5511110002", "vacante": v2["id"], "consentimiento": True, "fuente": "RH"})
    P2 = r.json()["id"]
    r = client.post("/entrevistas", json={"candidato": P2, "avisar_whatsapp": False})
    tok2 = r.json()["token"]
    client.post(f"/entrevistas/publica/{tok2}/consentimiento", json={"acepta": True})
    client.post(f"/entrevistas/publica/{tok2}/sesion", json={"modo": "texto"})
    client.post(f"/entrevistas/publica/{tok2}/transcript", json={"transcript": transcript})
    db.expire_all()
    e2 = db.query(Entrevista).filter(Entrevista.token == tok2).one()
    check(asyncio.run(re_.cerrar_entrevistas_inactivas()) == 0, "con actividad reciente el job no cierra nada")
    e2.ultima_actividad_en = datetime.now(timezone.utc) - timedelta(minutes=re_.INACTIVIDAD_ENTREVISTA_MIN + 1)
    db.commit()
    check(asyncio.run(re_.cerrar_entrevistas_inactivas()) == 1, "pasados 15 min sin actividad el job cierra la entrevista")
    db.expire_all()
    e2 = db.query(Entrevista).get(e2.id)
    check(e2.estado == "evaluada" and e2.cierre == "desconexion", f"…y la evalúa con el transcript sincronizado (estado={e2.estado}, cierre={e2.cierre})")

    # «Evaluar con lo que hay»
    v3 = nueva_vacante(client, "Chofer Bug2b")
    r = client.post("/candidatos", json={"nombre": "Pocas Respuestas", "telefono": "5511110003", "vacante": v3["id"], "consentimiento": True, "fuente": "RH"})
    P3 = r.json()["id"]
    r = client.post("/entrevistas", json={"candidato": P3, "avisar_whatsapp": False})
    tok3, cod3 = r.json()["token"], r.json()["id"]
    client.post(f"/entrevistas/publica/{tok3}/consentimiento", json={"acepta": True})
    client.post(f"/entrevistas/publica/{tok3}/sesion", json={"modo": "texto"})
    corto = [{"rol": "assistant", "texto": "Hola"}, {"rol": "user", "texto": "Tengo tres años de experiencia como chofer repartidor en la zona."}]
    r = client.post(f"/entrevistas/publica/{tok3}/finalizar", json={"transcript": corto, "cierre": "desconexion"})
    check(r.json()["estado"] == "interrumpida", "una respuesta + desconexión → interrumpida (regla previa intacta)")
    r = client.get(f"/candidatos/{P3}")
    check(r.json()["entrevistaStatus"]["turnosUtiles"] == 1 and r.json()["entrevistaStatus"]["accionSiguiente"] == "reintentar", "la ficha expone turnosUtiles=1 → botón «Evaluar con lo que hay»")
    r = client.post(f"/entrevistas/{cod3}/evaluar")
    check(r.status_code == 200 and r.json()["estado"] == "evaluada", "RH evalúa con lo que hay → evaluada")
    r = client.post(f"/entrevistas/{cod3}/evaluar")
    check(r.status_code == 409, "no se puede forzar dos veces (409)")

    # ================= 3/4/5. Descartar con expediente + Expediente en Onboarding =================
    print("\n--- 3/4/5. Descartar desde Contratación/Onboarding y expediente visible ---")
    r = client.patch(f"/candidatos/{P1}/etapa", json={"etapa": "Contratación", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["expedienteId"], "Contratación abre el expediente")
    # Onboarding v2 (2026-09-28): a Onboarding solo se entra con «Iniciar Onboarding» o con Modo Prueba activo
    from app.services.configuracion import obtener as _obtener_cfg  # noqa: E402

    _cfg = _obtener_cfg(db)
    _antes = _cfg.modo_prueba
    _cfg.modo_prueba = True
    db.commit()
    r = client.patch(f"/candidatos/{P1}/etapa", json={"etapa": "Onboarding", "manual": True, "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    _cfg.modo_prueba = _antes
    db.commit()
    ficha = r.json()
    check(ficha["etapa"] == "Onboarding" and ficha["expedienteId"], "en Onboarding la ficha sigue trayendo expedienteId (pestaña Expediente)")
    r = client.get(f"/contratacion/expedientes/{ficha['expedienteId']}")
    pend = [d for d in r.json()["documentos"] if d["estado"] != "recibido"]
    check(len(pend) >= 6, f"el expediente lista {len(pend)} documentos pendientes en Onboarding")
    r = client.post(f"/candidatos/{P1}/decision", json={"accion": "descartar", "comentario": ""})
    check(r.status_code == 400, "descartar con expediente exige motivo (400)")
    exp_id = ficha["expedienteId"]
    r = client.post(f"/candidatos/{P1}/decision", json={"accion": "descartar", "comentario": "No se presentó a firmar"})
    check(r.status_code == 200 and r.json()["activa"] is False, "descartar desde Onboarding cierra la postulación (antes 409)")
    db.expire_all()
    check(db.query(Expediente).get(exp_id) is None, "el expediente abierto se canceló en la misma decisión")
    check(db.query(Postulacion).get(p1.id).motivo_cierre == "descartado", "motivo_cierre = descartado")
    r = client.get("/contratacion/expedientes")
    check(all(x["expedienteId"] != exp_id for x in r.json()), "ya no aparece en el tablero de Onboarding")

    # ================= 6. Portal por Cuenta =================
    print("\n--- 6. Portal: eliminadas no salen; aislamiento por Cuenta; homónimas ---")
    vA = nueva_vacante(client, "Abogado Fiscalista")
    app.dependency_overrides[cuenta_actual] = lambda: cb
    vB = nueva_vacante(client, "Abogado Fiscalista")
    app.dependency_overrides[cuenta_actual] = lambda: ca
    r = client.get(f"/vacantes/{vA['id']}")
    check(r.json()["homonimasOtrasCuentas"] and r.json()["homonimasOtrasCuentas"][0]["cuenta"] == "Otra Cuenta", "el detalle avisa que hay una homónima publicada en otra Cuenta")
    assert client.delete(f"/vacantes/{vA['id']}").status_code == 200
    publicas = client.get("/vacantes/publicas").json()
    ids = [x["id"] for x in publicas]
    check(vA["id"] not in ids, "la vacante eliminada NO sale en el portal global")
    check(vB["id"] in ids and all("cuentaSlug" in x for x in publicas), "la homónima de la otra Cuenta sí (y cada fila trae su Cuenta)")
    check(ca.slug == "grupo-carbe" and cb.slug == "otra-empresa", f"slugs de Cuenta generados ({ca.slug}, {cb.slug})")
    r = client.get(f"/vacantes/publicas?cuenta={ca.slug}")
    check(r.status_code == 200 and all(x["cuentaId"] == ca.id for x in r.json()) and vB["id"] not in [x["id"] for x in r.json()], "portal por Cuenta (?cuenta=grupo-carbe) NO muestra la vacante de la otra Cuenta")
    r = client.get(f"/vacantes/publicas?cuenta={cb.id}")
    check([x["id"] for x in r.json()] == [vB["id"]], "?cuenta=<id> también funciona")
    r = client.get(f"/vacantes/publicas/cuenta?cuenta={ca.slug}")
    check(r.json()["nombre"] == "Grupo CARBE", "encabezado público de la Cuenta")
    check(client.get("/vacantes/publicas?cuenta=no-existe").status_code == 404, "portal de una Cuenta inexistente → 404")
    cb.estado = "Eliminada"
    db.commit()
    check(vB["id"] not in [x["id"] for x in client.get("/vacantes/publicas").json()], "una Cuenta eliminada no publica en el portal global")
    r = client.get("/cuentas")
    check(any(x.get("portalUrl", "").endswith("/portal?cuenta=grupo-carbe") for x in r.json()), "Configuración → Cuentas expone la liga del portal por Cuenta")

    db.close()

print(f"\n🎉 Bugs 2026-09-17 verificados: {OK} comprobaciones OK.")
