"""Verificación de punta a punta de la Fase 2 (Candidato persona + Postulacion) — SIN tocar
OpenAI ni Meta: corre en modo demo (sin claves) sobre una base SQLite desechable.

Cubre: migración de datos legado, Kanban por postulación, dos postulaciones de la misma persona,
ruteo de WhatsApp por contexto de conversación (+ pregunta cuando es ambiguo), reaplicar tras
descarte, un expediente por postulación, reinicio de prueba, métricas y borrado de prueba.

Uso (desde red-human-api/):
    .venv/Scripts/python.exe scripts/verificar_fase2.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Modo demo total + base desechable ANTES de importar app.* (settings se lee al importar).
_dir = tempfile.mkdtemp(prefix="rh_fase2_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "fase2.db").replace("\\", "/")
os.environ["OPENAI_API_KEY"] = ""
os.environ["WHATSAPP_PROVIDER"] = ""
os.environ["META_WHATSAPP_TOKEN"] = ""
os.environ["META_PHONE_NUMBER_ID"] = ""
os.environ["ANAM_API_KEY"] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-fase2"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Candidato, ConfiguracionSistema, Cuenta, Expediente, Mensaje, Postulacion, Usuario, Vacante,
)

OK = 0


import itertools as _it
_WAMID = _it.count(1)  # wamid único por mensaje simulado (el webhook deduplica reintentos)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def meta_texto(tel: str, texto: str, nombre: str = "Prueba Fase Dos") -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": nombre}, "wa_id": tel}],
        "messages": [{"from": tel, "id": f"wamid.{next(_WAMID)}.{texto[:8]}", "type": "text", "text": {"body": texto}}],
    }}]}]}


def meta_boton_plantilla(tel: str, texto: str, receptor: str = "5215550000000", nombre: str = "Prueba Fase Dos") -> dict:
    """Respuesta a un botón de respuesta rápida de una PLANTILLA (Meta la manda como type 'button',
    no 'interactive'); `receptor` = número de WhatsApp Business que recibió el mensaje."""
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"display_phone_number": receptor, "phone_number_id": "1"},
        "contacts": [{"profile": {"name": nombre}, "wa_id": tel}],
        "messages": [{"from": tel, "id": f"wamid.{next(_WAMID)}.btn.{texto[:8]}", "type": "button",
                      "button": {"payload": texto, "text": texto}, "context": {"from": receptor, "id": "wamid.tpl"}}],
    }}]}]}


def meta_lista(tel: str, id_opcion: str, titulo: str = "") -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": "Prueba Fase Dos"}, "wa_id": tel}],
        "messages": [{"from": tel, "id": f"wamid.{next(_WAMID)}.{id_opcion}", "type": "interactive",
                      "interactive": {"type": "list_reply", "list_reply": {"id": id_opcion, "title": titulo or id_opcion}}}],
    }}]}]}


with TestClient(app) as client:
    db = SessionLocal()

    # ---------- 1. Migración: la semilla legado quedó convertida a Postulaciones ----------
    cands = db.query(Candidato).all()
    check(all(len(c.postulaciones) == 1 for c in cands), f"migración: {len(cands)} candidatos de semilla, 1 postulación cada uno")
    c8801 = db.query(Candidato).filter_by(codigo="C-8801").one()
    p8801 = c8801.postulaciones[0]
    check(p8801.etapa == c8801.etapa and p8801.score == c8801.score and p8801.vacante_id == c8801.vacante_id,
          "migración: etapa/score/vacante copiados a la postulación")
    check(all(m.postulacion_id == p8801.id for m in c8801.mensajes) and len(p8801.mensajes) == 6,
          "migración: los 6 mensajes de la charla cuelgan de la postulación")
    check(all(e.postulacion_id is not None for e in db.query(Expediente).all()), "migración: todos los expedientes ligados a su postulación")
    check(all(p.codigo == f"P-{8800 + p.id}" for p in db.query(Postulacion).all()), "códigos P-#### salen del id de la postulación")
    check(c8801.postulacion_conversacion_id == p8801.id, "migración: puntero de conversación apunta a la postulación inicial")

    # ---------- Cuenta única + sesión simulada ----------
    cuenta = Cuenta(nombre_comercial="Cuenta Prueba", estado="Activa")
    db.add(cuenta)
    db.flush()
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in cands:
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
    db.commit()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ---------- 2. Kanban: una tarjeta por postulación ----------
    r = client.get("/candidatos")
    check(r.status_code == 200, "GET /candidatos responde 200")
    tarjetas = r.json()
    check(len(tarjetas) == db.query(Postulacion).count() and all(t["id"].startswith("P-") for t in tarjetas),
          f"Kanban: {len(tarjetas)} tarjetas = postulaciones, ids P-####")
    check(all(t["candidatoCodigo"].startswith("C-") and t["candidato"]["codigo"] == t["candidatoCodigo"] for t in tarjetas),
          "cada tarjeta trae la persona (candidatoCodigo / candidato)")
    r = client.get(f"/candidatos/{tarjetas[0]['id']}")
    check(r.status_code == 200 and "historialPostulaciones" in r.json(), "GET /candidatos/P-#### (detalle) responde")
    r = client.get("/candidatos/C-8801")
    check(r.status_code == 200 and r.json()["id"] == p8801.codigo, "GET /candidatos/C-#### (compat) resuelve a su postulación")

    # ---------- 3. Misma persona, dos vacantes → dos postulaciones ----------
    v1 = db.query(Vacante).filter_by(codigo="VAC-1042").one()
    v2 = db.query(Vacante).filter_by(codigo="VAC-1041").one()
    TEL = "5591234567"
    # retro 2026-10-09: ruta Masivos (prefiltro y Entrevista Red Human en el mismo chat de WhatsApp)
    from app.services import proceso as _sproc

    ruta_mas = _sproc.ruta_base("masivos_sin_documentos")
    for vv in (v1, v2):
        _r = client.put(f"/procesos/vacantes/{vv.codigo}", json={"pasos": ruta_mas["pasos"], "etapas": ruta_mas["etapas"]})
        assert _r.status_code == 200, _r.text
    r1 = client.post("/candidatos/postular", data={"vacante": v1.slug, "nombre": "Prueba Fase Dos", "telefono": TEL, "consentimiento": "true"})
    check(r1.status_code == 201 and r1.json()["postulacionNueva"], "POST /postular vacante 1 → postulación nueva")
    r2 = client.post("/candidatos/postular", data={"vacante": v2.slug, "nombre": "Prueba Fase Dos", "telefono": TEL, "consentimiento": "true"})
    check(r2.status_code == 201 and r2.json()["postulacionNueva"] and not r2.json()["nuevo"],
          "POST /postular vacante 2 → misma persona, postulación nueva")
    P1, P2 = r1.json()["postulacion"], r2.json()["postulacion"]
    C = r1.json()["candidato"]
    check(P1 != P2 and r2.json()["candidato"] == C, f"{C} tiene {P1} y {P2}")
    r3 = client.post("/candidatos/postular", data={"vacante": v2.slug, "nombre": "Prueba Fase Dos", "telefono": TEL, "consentimiento": "true"})
    check(r3.json()["postulacion"] == P2 and not r3.json()["postulacionNueva"], "reaplicar a una vacante ACTIVA reutiliza la postulación")
    tarjetas = {t["id"]: t for t in client.get("/candidatos").json()}
    check(tarjetas[P1]["yaAplicoAntes"] and tarjetas[P1]["totalPostulaciones"] == 2 and tarjetas[P2]["puesto"] == v2.titulo,
          "Kanban muestra 2 tarjetas de la misma persona con 'ya aplicó antes'")

    # ---------- 4. WhatsApp: contexto de conversación + preguntar (B1: solo el candidato mueve el puntero) ----------
    db.expire_all()
    persona = db.query(Candidato).filter_by(codigo=C).one()
    p1 = db.query(Postulacion).filter_by(codigo=P1).one()
    p2 = db.query(Postulacion).filter_by(codigo=P2).one()
    check(len(p1.mensajes) == 1 and len(p2.mensajes) == 1 and persona.postulacion_conversacion_id is None,
          "B1: las 2 plantillas de inicio (salientes) quedaron en su postulación pero NO movieron el puntero")
    WA = "521" + TEL
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA, "Hola, ya vi su mensaje"))
    check(r.json().get("accion") == "elegir_postulacion", "webhook: sin contexto y 2 postulaciones esperando → PREGUNTA con lista")
    r = client.post("/webhooks/whatsapp", json=meta_lista(WA, P2, v2.titulo[:24]))
    check(r.json().get("postulacion") == P2, f"webhook: eligió {P2} en la lista → el turno se procesa ahí")
    db.expire_all()
    check(persona.postulacion_conversacion_id == p2.id, "selección explícita en la lista fija el puntero")
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA, "Sí tengo experiencia en ventas"))
    check(r.json().get("postulacion") == P2, f"webhook: el siguiente mensaje sigue en {P2} (conversación en curso)")
    db.expire_all()
    ult = db.query(Mensaje).filter(Mensaje.rol == "user").order_by(Mensaje.id.desc()).first()
    check(ult.postulacion_id == p2.id and ult.candidato_id == persona.id, "el mensaje del candidato quedó ligado a esa postulación y a la persona")

    # RH manda un mensaje PROACTIVO sobre P1 (forzar Entrevista IA → aviso de apto por WhatsApp)
    r = client.patch(f"/candidatos/{P1}/etapa", json={"etapa": "Entrevista IA", "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["etapa"] == "Entrevista IA", f"RH fuerza Entrevista IA en {P1}: sale un mensaje proactivo por WhatsApp")
    db.expire_all()
    check(p1.mensajes[-1].rol == "assistant" and persona.postulacion_conversacion_id == p2.id,
          "B1: el mensaje saliente de RH sobre P1 NO secuestró la conversación (sigue en P2)")
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA, "Sí, sin problema con los horarios"))
    check(r.json().get("postulacion") == P2, "B1: la respuesta del candidato sigue enrutándose a P2, no a la postulación que RH tocó")

    # Notificación de RH (recordatorio de documentos etc.) tampoco mueve el puntero: se prueba
    # el helper directo, que es el único camino de escritura de mensajes salientes.
    from app.routers.candidatos import guardar_mensaje
    guardar_mensaje(db, p1, "assistant", "Recordatorio proactivo de RH", "whatsapp")
    db.commit()
    db.expire_all()
    check(persona.postulacion_conversacion_id == p2.id, "B1: guardar_mensaje(rol=assistant) no mueve el puntero")

    persona.postulacion_conversacion_id = None
    db.commit()
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA, "Sigo interesado"))
    check(r.json().get("accion") == "elegir_postulacion", "webhook: puntero perdido y 2 esperando (P1 en agenda, P2 en prefiltro) → vuelve a preguntar")
    r = client.post("/webhooks/whatsapp", json=meta_lista(WA, P1, v1.titulo[:24]))
    check(r.json().get("postulacion") == P1, f"webhook: eligió {P1} → el turno se procesa ahí")
    db.expire_all()
    check(persona.postulacion_conversacion_id == p1.id, "el puntero de conversación cambió a la elegida")
    db.refresh(p1)
    db.refresh(p2)
    check(all(m.postulacion_id == p1.id for m in p1.mensajes) and all(m.postulacion_id == p2.id for m in p2.mensajes) and len(p2.mensajes) >= 5,
          "historial del chat por postulación (no se mezcla entre P1 y P2)")

    # ---------- 5. Descartar y reaplicar → postulación nueva, la vieja queda histórica ----------
    r = client.post(f"/candidatos/{P1}/decision", json={"accion": "descartar", "comentario": "prueba"})
    check(r.status_code == 200 and r.json()["activa"] is False and r.json()["motivoCierre"] == "descartado", "descartar cierra la postulación")
    r = client.post("/candidatos/postular", data={"vacante": v1.slug, "nombre": "Prueba Fase Dos", "telefono": TEL, "consentimiento": "true"})
    P3 = r.json()["postulacion"]
    check(P3 not in (P1, P2) and r.json()["postulacionNueva"], f"reaplicar tras descarte crea {P3}; {P1} queda como historial")
    det = client.get(f"/candidatos/{P3}").json()
    check(any(h["id"] == P1 and not h["activa"] for h in det["historialPostulaciones"]), "el detalle lista la postulación descartada en el historial")
    check(client.get(f"/candidatos/{P1}").json()["activa"] is False, "la postulación cerrada sigue consultable")

    # ---------- 6. Un expediente por postulación (P5) ----------
    r = client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Contratación", "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["expedienteId"], f"{P2} → Contratación abre expediente")
    exp_p2 = r.json()["expedienteId"]
    r = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Contratación", "omitir_obligatorios": True, "comentario": "Prueba: omisión autorizada de la ruta"})
    check(r.status_code == 200 and r.json()["expedienteId"] and r.json()["expedienteId"] != exp_p2, f"{P3} → Contratación abre OTRO expediente")
    db.expire_all()
    e1 = db.get(Expediente, exp_p2)
    check(e1.candidato_id == persona.id and e1.postulacion.codigo == P2, "el primer expediente conserva candidato_id y su postulación (bug de relación uno-a-uno corregido)")
    r = client.get("/contratacion/expedientes")
    check(r.status_code == 200 and all(x["candidatoId"].startswith("P-") for x in r.json()), "GET /contratacion/expedientes: candidatoId = postulación")

    # ---------- 7. Reiniciar prueba (Punto 8) ----------
    cfg = db.get(ConfiguracionSistema, 1) or ConfiguracionSistema(id=1)
    cfg.modo_prueba = True
    db.add(cfg)
    db.commit()
    r = client.post(f"/candidatos/{P3}/reiniciar")
    check(r.status_code == 200 and r.json()["nueva"] != P3 and r.json()["anterior"] == P3, "reiniciar cierra la actual y crea una limpia")
    P4 = r.json()["nueva"]
    db.expire_all()
    check(persona.telefono == TEL and persona.postulacion_conversacion_id != db.query(Postulacion).filter_by(codigo=P4).one().id,
          "reiniciar no toca el teléfono ni fija el puntero (B1)")
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA, "Hola de nuevo"))
    check(r.json().get("postulacion") == P4, f"el siguiente mensaje del candidato se enruta a {P4} (única que espera respuesta) y la fija")
    cfg.modo_prueba = False
    db.commit()

    # ---------- 8. Métricas y vacantes ----------
    r = client.get("/metricas/pipeline")
    check(r.status_code == 200 and r.json()["embudo"][0]["valor"] == db.query(Postulacion).filter_by(es_prueba=False).count(),
          "GET /metricas/pipeline cuenta postulaciones")
    r = client.get("/vacantes")
    check(r.status_code == 200 and any(v["id"] == "VAC-1042" for v in r.json()), "GET /vacantes responde con conteos por postulación")
    r = client.get("/candidatos", params={"candidato": C})
    check(len(r.json()) == 2 and all(t["activa"] for t in r.json()), f"B4: por defecto ?candidato={C} regresa solo sus 2 postulaciones activas")
    r = client.get("/candidatos", params={"candidato": C, "mostrar_cerradas": "true"})
    check(len(r.json()) == 4, "B4: ?mostrar_cerradas=true regresa las 4 (2 cerradas incluidas)")
    r = client.get("/candidatos", params={"candidato": C, "activa": "false"})
    check(len(r.json()) == 2 and all(not t["activa"] for t in r.json()), "filtro explícito ?activa=false regresa solo las 2 cerradas")
    r = client.get("/candidatos")
    check(all(t["activa"] for t in r.json()), "B4: el Kanban completo sin parámetros no trae ninguna cerrada")

    # ---------- 9. Nuevo número por WhatsApp → menú de vacantes, elige, prefiltro demo ----------
    WA2 = "5215587654321"
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA2, "Hola", nombre="Otra Persona"))
    check(r.json().get("accion") == "menu_vacantes", "número nuevo → persona + postulación sin vacante → menú")
    r = client.post("/webhooks/whatsapp", json=meta_lista(WA2, "VAC-1042", v1.titulo[:24]))
    check(r.json().get("accion") == "aviso_privacidad_enviado", "elige vacante en el menú → NO es consentimiento: se manda el aviso de privacidad")
    Pw = r.json()["postulacion"]
    pw = db.query(Postulacion).filter_by(codigo=Pw).one()
    check(pw.vacante_id == v1.id and not pw.consentimiento, "la vacante quedó asignada pero sin consentimiento")
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA2, "quiero la vacante de ventas, sin duda", nombre="Otra Persona"))
    check(r.json().get("accion") == "aviso_privacidad_enviado", "un texto sin palabra de aceptación ('vacante', 'sin') vuelve a mandar el aviso")
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA2, "Sí, acepto", nombre="Otra Persona"))
    check(r.json().get("accion") == "prefiltro_iniciado", "'Sí, acepto' → consentimiento registrado y prefiltro iniciado")
    db.expire_all()
    check(pw.consentimiento and pw.consentimiento_fecha is not None, "consentimiento quedó en la postulación con fecha")
    for txt in ("Sí", "Sí, 3 años", "Sí vivo en Guadalajara"):
        r = client.post("/webhooks/whatsapp", json=meta_texto(WA2, txt, nombre="Otra Persona"))
    db.refresh(pw)
    check(pw.prefiltro_completo and pw.estado == "cumple" and pw.etapa == "Entrevista IA" and pw.etapa in [t["etapa"] for t in client.get("/candidatos").json()],
          "prefiltro demo completo → Zero-Touch clasificó y la tarjeta está en una columna real del Kanban")

    # ---------- 10. Borrado de prueba ----------
    persona.es_prueba = True
    db.commit()
    r = client.post("/candidatos/prueba/eliminar")
    check(r.status_code == 200 and r.json()["candidatos"] == 1 and r.json()["postulaciones"] == 4, "eliminar prueba borra persona + sus 4 postulaciones en cascada")
    check(db.query(Postulacion).filter_by(codigo=P2).first() is None and db.query(Expediente).get(exp_p2) is None, "…y sus expedientes/mensajes")

    # ---------- 11. Liga pública de entrevista (regresión: se rompió con Fase 2 y se corrigió en Fase 4) ----------
    p_ent = db.query(Postulacion).filter(Postulacion.vacante_id.isnot(None), Postulacion.activa.is_(True)).first()
    r = client.post("/entrevistas", json={"candidato": p_ent.codigo, "avisar_whatsapp": False})
    check(r.status_code == 201, "agendar entrevista IA")
    tok = r.json()["token"]
    r = client.get(f"/entrevistas/publica/{tok}")
    check(r.status_code == 200 and r.json()["puesto"] == p_ent.vacante.titulo, "GET /entrevistas/publica/{token}: puesto desde la postulación")
    client.post(f"/entrevistas/publica/{tok}/consentimiento", json={"acepta": True})
    r = client.post(f"/entrevistas/publica/{tok}/sesion")
    check(r.status_code == 200 and r.json()["modo"] == "texto", "POST /sesion (modo texto)")
    r = client.post(f"/entrevistas/publica/{tok}/turno", json={"texto": "sí, listo"})
    check(r.status_code == 200 and r.json()["respuesta"], "POST /turno responde")
    for _ in range(10):
        r = client.post(f"/entrevistas/publica/{tok}/turno", json={"texto": "Trabajé tres años en caja y atención al cliente, resolviendo devoluciones y arqueos diarios."})
        if r.json()["terminada"]:
            break
    r = client.post(f"/entrevistas/publica/{tok}/finalizar", json={"cierre": "texto"})
    check(r.status_code == 200 and r.json()["estado"] == "evaluada" and r.json()["cierre"] == "texto", "POST /finalizar evalúa y registra el cierre")

    # ---------- 12. Bug urgente (2026-09-11): botón "Sí, empezar ahora" de la plantilla tras postular por web ----------
    # Causa A: con 2 Cuentas activas el webhook respondía 500 a Meta y el candidato no recibía nada.
    # Causa B: en Modo Prueba, la respuesta caía en la persona de la prueba anterior (búsqueda por wa_id primero).
    cfg = db.get(ConfiguracionSistema, 1)
    cfg.modo_prueba = True
    db.commit()
    WA3 = "5213399887766"
    TEL3 = "3399887766"
    r = client.post("/candidatos/postular", data={"vacante": v1.slug, "nombre": "Botón Plantilla", "telefono": TEL3, "consentimiento": "true"})
    check(r.status_code == 201 and r.json()["postulacionNueva"], "web: postulación nueva (Modo Prueba) → se manda la plantilla de inicio")
    PB1 = r.json()["postulacion"]
    r = client.post("/webhooks/whatsapp", json=meta_boton_plantilla(WA3, "Sí, empezar ahora"))
    check(r.status_code == 200 and r.json().get("accion") == "turno_prefiltro" and r.json().get("postulacion") == PB1 and r.json().get("respuesta"),
          f"botón de plantilla (type 'button') → arranca el prefiltro en {PB1} y responde")

    # Causa B: segunda postulación web de prueba con el MISMO teléfono → persona nueva sin wa_id
    r = client.post("/candidatos/postular", data={"vacante": v1.slug, "nombre": "Botón Plantilla", "telefono": TEL3, "consentimiento": "true"})
    check(r.status_code == 201 and r.json()["postulacion"] != PB1, "web (Modo Prueba): segunda postulación con el mismo teléfono → persona/postulación nuevas")
    PB2 = r.json()["postulacion"]
    r = client.post("/webhooks/whatsapp", json=meta_boton_plantilla(WA3, "Sí, empezar ahora"))
    check(r.status_code == 200 and r.json().get("postulacion") == PB2,
          f"Causa B corregida: la respuesta al botón va a la postulación NUEVA {PB2}, no a la vieja {PB1}")
    cfg.modo_prueba = False
    db.commit()

    # Causa A: segunda Cuenta activa (Configuración → Cuentas) — antes: 500 en todo mensaje entrante
    cuenta_b = Cuenta(nombre="Cuenta B", nombre_comercial="Empresa B", estado="Activa", whatsapp_comunicacion="5511112222", whatsapp_exclusivo=True)  # 2026-09-17: el ruteo por número exige la marca Premium
    db.add(cuenta_b)
    db.commit()
    r = client.post("/webhooks/whatsapp", json=meta_boton_plantilla(WA3, "Sí, empezar ahora"))
    check(r.status_code == 200 and r.json().get("postulacion") == PB2,
          "Causa A corregida: con 2 Cuentas activas y sin número coincidente → Cuenta más antigua, 200 y responde (antes 500)")
    r = client.post("/webhooks/whatsapp", json=meta_boton_plantilla("5215511223344", "Hola", receptor="5511112222", nombre="Persona B"))
    check(r.status_code == 200 and db.query(Candidato).filter_by(wa_id="5215511223344").one().cuenta_id == cuenta_b.id,
          "ruteo por número: el mensaje que recibió el WhatsApp de la Cuenta B se asigna a la Cuenta B")
    cuenta_b.estado = "Inactiva"
    db.commit()

    db.close()

print(f"\n🎉 Fase 2 verificada: {OK} comprobaciones OK.")
