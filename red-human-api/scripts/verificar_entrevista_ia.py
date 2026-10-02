"""Verificación de punta a punta de la Fase 4 (Entrevista IA) — modo demo (sin OpenAI ni Anam),
base SQLite desechable.

Cubre: hotfix de los endpoints públicos (caídos desde Fase 2), identidad de empresa (Punto 1),
nombre de ficha y datos concretos en el prompt (Punto 2), protocolo/lenguaje del prompt (Punto 3),
cierre verificable + interrumpida + reapertura (Punto 4), perfil profundo con evidencia (Punto 5) y
enfoque de entrevista de la vacante al guion/prompt/evaluador (Punto 6).

Uso (desde red-human-api/):
    .venv/Scripts/python.exe scripts/verificar_entrevista_ia.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_ent_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "ent.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-ent"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cliente, Cuenta, Entrevista, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.routers.candidatos import nombre_ficha  # noqa: E402
from app.routers.entrevistas import _system_prompt  # noqa: E402
from app.services import ia  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta Ent", nombre_comercial="Reclutadora Norte", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    cliente = Cliente(cuenta_id=cuenta.id, nombre="Tiendas Sol", nombre_comercial="Sol Retail", estado="Activo")
    db.add(cliente)
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ============ PUNTO 1 — identidad de la empresa ============
    r = client.post("/vacantes/generar", json={"titulo": "Cajero", "empresa": "TEXTO LIBRE", "cliente_id": cliente.id, "mostrar_cliente_candidato": True})
    check(r.status_code == 200 and r.json()["empresa"] == "Sol Retail" and "TEXTO LIBRE" not in r.text, "generar: ignora texto libre; Cliente visible → nombre comercial del Cliente")
    r = client.post("/vacantes/generar", json={"titulo": "Cajero", "cliente_id": cliente.id, "mostrar_cliente_candidato": False})
    check(r.json()["empresa"] == "Reclutadora Norte", "generar: Cliente oculto → nombre comercial de la Cuenta")
    r = client.post("/vacantes/generar", json={"titulo": "Cajero", "cliente_id": 9999})
    check(r.status_code == 400, "generar: Cliente de otra Cuenta/inexistente → 400")
    r = client.post("/vacantes", json={"titulo": "Cajero Sol", "empresa": "OTRO TEXTO", "cliente_id": cliente.id, "mostrar_cliente_candidato": True,
                                       "descripcion": "x", "generar_si_falta": False, "enfoque_entrevista": "profesional_personal"})
    check(r.status_code == 201 and r.json()["empresa"] == "Sol Retail" and r.json()["enfoqueEntrevista"] == "profesional_personal",
          "crear vacante: empresa = nombre resuelto (no texto libre) y enfoque de entrevista guardado")
    VAC = r.json()["id"]
    r = client.patch(f"/vacantes/{VAC}", json={"mostrar_cliente_candidato": False, "empresa": "HACK"})
    check(r.json()["empresa"] == "Reclutadora Norte", "editar: al ocultar el Cliente, empresa se recalcula (texto libre ignorado)")
    client.patch(f"/vacantes/{VAC}", json={"mostrar_cliente_candidato": True})
    r = client.post("/vacantes", json={"titulo": "X", "descripcion": "x", "generar_si_falta": False, "enfoque_entrevista": "ultra"})
    check(r.status_code == 400, "enfoque de entrevista inválido → 400 (solo 2 niveles)")

    # ============ PUNTO 6 — enfoque viaja a plantilla y a la entrevista ============
    r = client.post(f"/plantillas/desde-vacante/{VAC}", json={"nombre": "Plantilla Sol"})
    check(r.status_code == 201 and r.json()["enfoqueEntrevista"] == "profesional_personal", "guardar como plantilla conserva el enfoque")
    r = client.post("/plantillas", json={"nombre": "P2", "titulo": "T", "enfoque_entrevista": "nada"})
    check(r.status_code == 400, "plantilla con enfoque inválido → 400")

    # ============ HOTFIX + PUNTOS 2/3 — endpoints públicos y prompt ============
    r = client.post("/candidatos", json={"nombre": "Lucía Ramírez", "telefono": "5511223344", "vacante": VAC, "consentimiento": True, "fuente": "RH"})
    P = r.json()["id"]
    r = client.post("/entrevistas", json={"candidato": P, "avisar_whatsapp": False})
    check(r.status_code == 201, "agendar entrevista para la postulación")
    ENT = r.json()["id"]
    TOKEN = r.json()["token"]
    e = db.query(Entrevista).filter_by(codigo=ENT).one()
    check(len(e.guion.get("temas", [])) >= 6 and "visión de futuro" in " ".join(e.guion["temas"]).lower(), "guion por temas según enfoque profesional+personal")

    r = client.get(f"/entrevistas/publica/{TOKEN}")
    check(r.status_code == 200 and r.json()["puesto"] == "Cajero Sol" and r.json()["empresa"] == "Sol Retail" and r.json()["candidato"] == "Lucía",
          "HOTFIX: GET /publica responde (puesto y empresa desde la POSTULACIÓN, nombre de ficha)")
    prompt = _system_prompt(e)
    for k in ("Lucía", "Sol Retail", "Cajero Sol", "Lucía, no te escuché. ¿Comenzamos?", "hijos", "con quién vive", ia.DESPEDIDA_ENTREVISTA, "profesional_personal", "sin frases de transición"):
        check(k in prompt, f"prompt contiene «{k}»")
    check("Alma" not in prompt and "Eres Red Human" in prompt and "nunca como 'asistente virtual', 'agente de inteligencia artificial'" in prompt,
          "PARTE 2: el prompt se identifica como Red Human y prohíbe 'asistente virtual' / 'agente de inteligencia artificial'")
    check("Pregunta 1" in prompt and "1. " not in prompt.split("Temas a cubrir")[1].split("PROTOCOLO")[0], "prompt: prohíbe numerar y los temas no van numerados")
    check(prompt.count("Lucía") >= 2 and "Candidato WhatsApp" not in prompt, "prompt: nombre validado desde la ficha, sin placeholders")

    r = client.post(f"/entrevistas/publica/{TOKEN}/sesion")
    check(r.status_code == 403, "sesión sin consentimiento → 403")
    r = client.post(f"/entrevistas/publica/{TOKEN}/finalizar", json={"cierre": "manual"})
    check(r.status_code == 409, "finalizar una entrevista programada (no en curso) → 409")
    r = client.post(f"/entrevistas/publica/{TOKEN}/consentimiento", json={"acepta": True})
    check(r.status_code == 200, "consentimiento")
    r = client.post(f"/entrevistas/publica/{TOKEN}/sesion")
    INTRO = ("Hola, soy Red Human. Gracias por participar en el proceso para Cajero Sol. Vamos a conversar sobre tu "
             "experiencia, tus intereses y algunos aspectos relevantes para el puesto. ¿Comenzamos?")
    check(r.status_code == 200 and r.json()["modo"] == "texto" and r.json()["mensajes"][0]["texto"] == INTRO,
          "PARTE 2: POST /sesion responde con la introducción EXACTA (Red Human + nombre real de la vacante)")
    check(ia.mensaje_inicial_entrevista("") == INTRO.replace("para Cajero Sol", "para el puesto"), "introducción sin vacante → «el puesto»")
    db.expire_all()
    check(e.estado == "en_curso" and e.iniciada_en is not None, "sesión: estado en_curso e iniciada_en")

    # Protocolo de inicio en modo texto (demo): 'no' espera, 'sí' arranca
    r = client.post(f"/entrevistas/publica/{TOKEN}/turno", json={"texto": "un momento por favor"})
    check(r.status_code == 200 and r.json()["respuesta"] == ia.ESPERA_INICIO, "P3: /turno responde; respuesta negativa → espera sin repreguntar")
    r = client.post(f"/entrevistas/publica/{TOKEN}/turno", json={"texto": "sí, listo"})
    check(r.status_code == 200 and not r.json()["terminada"] and "Pregunta" not in r.json()["respuesta"], "afirmativo → primera pregunta de inmediato, sin numerar")
    terminada = False
    for i in range(10):
        r = client.post(f"/entrevistas/publica/{TOKEN}/turno", json={"texto": f"Trabajé tres años en caja y atención al cliente, resolviendo devoluciones y arqueos diarios. Turno {i}."})
        if r.json()["terminada"]:
            terminada = True
            break
    check(terminada and ia.DESPEDIDA_ENTREVISTA in r.json()["respuesta"], "modo texto: cierra solo con la despedida fija (marcador)")

    # ============ PUNTO 4 — cierre verificable ============
    r = client.post(f"/entrevistas/publica/{TOKEN}/finalizar", json={"cierre": "herramienta"})
    check(r.status_code == 200 and r.json()["estado"] == "evaluada" and r.json()["cierre"] == "herramienta", "finalizar: cierre 'herramienta' aceptado porque la despedida está en el transcript")
    check(r.json()["finalizadaEn"] and r.json()["turnosCandidato"] >= 3, "finalizar: registra finalizada_en y turnos del candidato")
    r = client.post(f"/entrevistas/publica/{TOKEN}/turno", json={"texto": "otra respuesta"})
    check(r.status_code == 403, "tras el cierre no se aceptan más respuestas")
    r = client.post(f"/entrevistas/publica/{TOKEN}/sesion")
    check(r.status_code == 409, "tras el cierre no se puede reabrir sesión desde la liga")
    r = client.post(f"/entrevistas/publica/{TOKEN}/finalizar", json={"cierre": "manual"})
    check(r.status_code == 200 and r.json()["cierre"] == "herramienta", "finalizar es idempotente y no pisa el cierre verificado")
    db.expire_all()
    p = db.query(Postulacion).filter_by(codigo=P).one()
    check(p.etapa == "Entrevista IA", "la postulación se queda en Filtro Red Human (ya no existe la columna Evaluación)")

    # ============ PUNTO 5 — perfil profundo ============
    ev = r.json()["evaluacion"]
    check(ev.get("perfil") and len(ev["perfil"]) == 10 and all(k in ev["perfil"] for k in ia.DIMENSIONES_PERFIL), "evaluación con perfil profundo de 10 dimensiones")
    check(all(("evidencia" in d and "evaluado" in d) for d in ev["perfil"].values()), "cada dimensión trae evaluado + evidencia")
    check(any(d["evaluado"] is False and not d["conclusion"] for d in ev["perfil"].values()), "las dimensiones no cubiertas quedan como no evaluadas, sin inventar")

    # ============ PUNTO 4 — reapertura explícita ============
    r = client.post(f"/entrevistas/{ENT}/reabrir", json={"motivo": "se cortó la llamada"})
    check(r.status_code == 200 and r.json()["estado"] == "programada" and r.json()["intentosPrevios"] == 1 and r.json()["mensajes"] == 0,
          "reabrir: archiva el intento y vuelve a programada")
    db.expire_all()
    check(p.etapa == "Entrevista IA" and e.intentos_previos[0]["evaluacion"].get("match_perfil") == 70, "reabrir: la postulación regresa a Entrevista IA y el intento archivado conserva la evaluación")
    # segundo intento: cierre por desconexión con pocos turnos → interrumpida, sin evaluar
    client.post(f"/entrevistas/publica/{TOKEN}/sesion")
    r = client.post(f"/entrevistas/publica/{TOKEN}/turno", json={"texto": "sí"})
    r = client.post(f"/entrevistas/publica/{TOKEN}/finalizar", json={"cierre": "desconexion"})
    check(r.status_code == 200 and r.json()["estado"] == "interrumpida" and r.json()["evaluacion"] is None, "desconexión con 1 solo turno → interrumpida, sin evaluación")
    db.expire_all()
    check(p.etapa == "Entrevista IA", "interrumpida no mueve la postulación")
    r = client.post(f"/entrevistas/publica/{TOKEN}/finalizar", json={"cierre": "marcador", "transcript": [{"rol": "user", "texto": "a"}, {"rol": "user", "texto": "b"}, {"rol": "assistant", "texto": "gracias"}]})
    check(r.json()["estado"] == "interrumpida", "interrumpida es terminal hasta que RH reabra")
    r = client.post(f"/entrevistas/{ENT}/reabrir", json={})
    client.post(f"/entrevistas/publica/{TOKEN}/sesion")
    r = client.post(f"/entrevistas/publica/{TOKEN}/finalizar", json={"cierre": "marcador", "transcript": [
        {"rol": "assistant", "texto": "¿listo?"}, {"rol": "user", "texto": "sí"}, {"rol": "assistant", "texto": "¿experiencia?"},
        {"rol": "user", "texto": "Trabajé tres años en caja y atención al cliente, resolviendo devoluciones y arqueos diarios."}, {"rol": "assistant", "texto": "¿decisiones?"}, {"rol": "user", "texto": "Trabajé tres años en caja y atención al cliente, resolviendo devoluciones y arqueos diarios."},
        {"rol": "assistant", "texto": "¿presión?"}, {"rol": "user", "texto": "Trabajé tres años en caja y atención al cliente, resolviendo devoluciones y arqueos diarios."}, {"rol": "assistant", "texto": "ok, gracias y adiós"}]})
    check(r.json()["estado"] == "evaluada" and r.json()["cierre"] == "manual", "cierre 'marcador' declarado SIN la despedida fija → el servidor lo degrada a 'manual' (no confía)")
    check(r.json()["intentosPrevios"] == 2, "dos intentos archivados")

    # ============ 2026-09-13 — validación del transcript en /finalizar ============
    r = client.post(f"/entrevistas/{ENT}/reabrir", json={})
    client.post(f"/entrevistas/publica/{TOKEN}/sesion")
    r = client.post(f"/entrevistas/publica/{TOKEN}/finalizar", json={"cierre": "manual", "transcript": [
        {"rol": "assistant", "texto": "Hola, ¿comenzamos?"}, {"rol": "user", "texto": "sí"}, {"rol": "assistant", "texto": "¿experiencia?"}]})
    check(r.status_code == 200 and r.json()["estado"] == "interrumpida" and r.json()["motivo"] == "sin_respuestas" and r.json()["evaluacion"] is None,
          "sin respuestas reales (solo 'sí') → interrumpida con motivo sin_respuestas, SIN evaluación ni score")
    db.expire_all()
    check(p.etapa == "Entrevista IA", "sin respuestas: la postulación no se mueve (acción siguiente: reintentar)")
    r = client.get(f"/candidatos/{p.codigo}")
    check(r.json().get("recomendacionRedHuman") == "Reintentar Entrevista Red Human" and r.json()["entrevistaStatus"]["accionSiguiente"] == "reintentar",
          "ficha: recomendación «Reintentar Entrevista Red Human» y status de la entrevista con acción reintentar")
    r = client.post(f"/entrevistas/publica/{TOKEN}/sesion")
    check(r.status_code == 409 and "reabrirla" in r.json()["detail"], "sesión sobre entrevista interrumpida → 409 con mensaje claro para el candidato")
    # parcial: 1-2 respuestas útiles → la IA (demo: heurística) dice que no alcanza
    r = client.post(f"/entrevistas/{ENT}/reabrir", json={})
    client.post(f"/entrevistas/publica/{TOKEN}/sesion")
    r = client.post(f"/entrevistas/publica/{TOKEN}/finalizar", json={"cierre": "manual", "transcript": [
        {"rol": "assistant", "texto": "¿comenzamos?"}, {"rol": "user", "texto": "sí"}, {"rol": "assistant", "texto": "¿experiencia?"},
        {"rol": "user", "texto": "Trabajé tres años en caja y atención al cliente, resolviendo devoluciones y arqueos diarios."}, {"rol": "assistant", "texto": "¿decisiones?"}]})
    check(r.status_code == 200 and r.json()["estado"] == "parcial" and r.json()["motivo"] == "parcial" and r.json()["evaluacion"].get("parcial") and "match_perfil" not in r.json()["evaluacion"],
          "contestó poco e insuficiente → Entrevista parcial, sin score integral, con temas faltantes")
    check(len(r.json()["evaluacion"].get("faltante", [])) >= 1, "parcial: lista de temas faltantes para RH")
    # corta pero suficiente (3 respuestas útiles en demo) → evaluada indicando lo que faltó
    r = client.post(f"/entrevistas/{ENT}/reabrir", json={})
    client.post(f"/entrevistas/publica/{TOKEN}/sesion")
    tr = [{"rol": "assistant", "texto": "¿comenzamos?"}, {"rol": "user", "texto": "sí"}]
    for i in range(3):
        tr += [{"rol": "assistant", "texto": f"pregunta {i}"}, {"rol": "user", "texto": "Trabajé tres años en caja y atención al cliente, resolviendo devoluciones y arqueos diarios."}]
    tr.append({"rol": "assistant", "texto": f"{ia.DESPEDIDA_ENTREVISTA}, Lucía. Gracias."})
    r = client.post(f"/entrevistas/publica/{TOKEN}/finalizar", json={"cierre": "marcador", "transcript": tr})
    check(r.status_code == 200 and r.json()["estado"] == "evaluada" and r.json()["evaluacion"]["match_perfil"] == 70 and len(r.json()["evaluacion"]["faltante"]) >= 1,
          "corta pero suficiente → evaluada e indica lo que faltó (`faltante`)")
    r = client.get(f"/candidatos/{p.codigo}")
    check(r.json()["evaluacionIntegral"] is True and r.json()["entrevistaStatus"]["estado"] == "evaluada", "ficha: evaluación integral disponible solo con entrevista válida")
    # aislamiento: reabrir desde otra cuenta
    otra = Cuenta(nombre="Otra", nombre_comercial="Otra", estado="Activa")
    db.add(otra)
    db.commit()
    app.dependency_overrides[cuenta_actual] = lambda: otra
    check(client.post(f"/entrevistas/{ENT}/reabrir", json={}).status_code == 404, "reabrir desde otra Cuenta → 404")
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ============ PUNTO 2 — nombre de ficha vs perfil de WhatsApp ============
    c = db.query(Candidato).filter_by(codigo="C-8801").one()
    c.wa_nombre = "Mafer Cool"
    db.commit()
    check(nombre_ficha(c.postulaciones[0]) == "María", "nombre_ficha: prefiere la ficha sobre el perfil de WhatsApp")
    c.nombre = "Candidato WhatsApp"
    db.commit()
    check(nombre_ficha(c.postulaciones[0]) == "Mafer", "nombre_ficha: con placeholder cae al perfil de WhatsApp")

    # ============ carta de intención (mismo hotfix) ============
    persona2 = db.query(Candidato).filter_by(codigo="C-8808").one()
    exp2 = persona2.expedientes[0]
    from app.routers.contratacion import _html_carta_intencion  # el endpoint exige WeasyPrint (librerías nativas)
    html = _html_carta_intencion(exp2)
    check(exp2.postulacion.vacante.titulo in html, "HOTFIX: la carta de intención se arma con la vacante de la postulación")

    db.close()

print(f"\n🎉 Entrevista IA (Fase 4) verificada: {OK} comprobaciones OK.")
