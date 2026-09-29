"""Verificación de la Fase 7A (2026-09-12) — flujo de Entrevista Humana (sobre Evaluaciones unificadas desde 2026-09-29): entrevistador interno desde
el perfil del Usuario, entrevistador externo desde los contactos del Cliente (+ Otro), contactos del
Cliente seleccionables al notificar, y corrección del correo (defaults encendidos + resultado visible
por canal). Modo demo (sin OpenAI/Meta/Resend), base SQLite desechable.

Uso (desde red-human-api/):
    .venv/Scripts/python.exe scripts/verificar_entrevista_humana.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_eh_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "eh.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-eh"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Candidato, Cliente, ClienteContacto, Cuenta, Evaluacion, NotificacionEnviada, ReglaNotificacion, Usuario, UsuarioCuenta, Vacante,
)

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
    cuenta = Cuenta(nombre="Cuenta EH", nombre_comercial="Reclutadora Centro", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    cliente = Cliente(cuenta_id=cuenta.id, nombre="Tiendas Sol", nombre_comercial="Sol Retail", estado="Activo")
    otro_cliente = Cliente(cuenta_id=cuenta.id, nombre="Otro SA", estado="Activo")
    db.add_all([cliente, otro_cliente])
    db.flush()
    k1 = ClienteContacto(cliente_id=cliente.id, nombre="Paola", apellidos="Ruiz", puesto="Gerente", correo="paola@sol.mx", telefono="3311112222")
    k2 = ClienteContacto(cliente_id=cliente.id, nombre="Marco", apellidos="Díaz", puesto="RH", correo="marco@sol.mx", telefono="")
    k3 = ClienteContacto(cliente_id=cliente.id, nombre="Sin", apellidos="Correo", puesto="", correo="", telefono="3300000000")
    k_ajeno = ClienteContacto(cliente_id=otro_cliente.id, nombre="Ajeno", correo="ajeno@otro.mx", telefono="")
    db.add_all([k1, k2, k3, k_ajeno])
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
        v.cliente_id = cliente.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta

    # ---------- 1. Perfil del usuario: WhatsApp se captura y se expone ----------
    r = client.post("/auth/usuarios", json={"correo": "lucia@rh.mx", "nombre": "Lucía Entrevista", "puesto": "Reclutadora", "telefono": "33 4455 6677", "rol": "Usuario", "password": "Clave-segura-99!"})
    check(r.status_code == 201 and r.json()["telefono"] == "3344556677", "POST /auth/usuarios captura el WhatsApp (normalizado a 10 dígitos)")
    lucia_id = r.json()["id"]
    r = client.patch(f"/auth/usuarios/{lucia_id}", json={"telefono": "+52 1 33 9999 8888"})
    check(r.status_code == 200 and r.json()["telefono"] == "3399998888", "PATCH /auth/usuarios actualiza el WhatsApp")
    r = client.get("/auth/entrevistadores")
    lu = next(x for x in r.json() if x["id"] == lucia_id)
    check(r.status_code == 200 and lu["correo"] == "lucia@rh.mx" and lu["telefono"] == "3399998888", "GET /auth/entrevistadores regresa nombre, correo y WhatsApp del perfil")
    r = client.post(f"/cuentas/{cuenta.id}/usuarios", json={"correo": "nuevo@rh.mx", "nombre": "Nuevo Usuario", "telefono": "5511112222"})
    check(r.status_code == 201 and r.json()["usuario"]["telefono"] == "5511112222", "POST /cuentas/{id}/usuarios acepta y expone el WhatsApp")

    # ---------- 2. Defaults de la regla: nace encendida ----------
    # Evaluaciones unificadas (2026-09-29): la entrevista humana se asigna con «Agregar evaluación» y avisa con el
    # evento evaluacion_asignada (sustituye a entrevista_agendada).
    reglas = client.get("/notificaciones/reglas").json()
    ra = next(x for x in reglas if x["evento"] == "evaluacion_asignada")
    check(ra["candidatoCorreo"] and ra["candidatoWhatsapp"] and ra["entrevistadorCorreo"] and ra["entrevistadorWhatsapp"] and not ra["clienteCorreo"],
          "siembra perezosa: evaluacion_asignada nace con correo+WhatsApp para candidato y evaluador")
    regla_db = db.query(ReglaNotificacion).filter_by(cuenta_id=cuenta.id, evento="evaluacion_asignada").one()
    regla_db.cliente_correo = True
    db.commit()

    # postulación con correo y teléfono
    persona = db.query(Candidato).filter_by(codigo="C-8801").one()
    persona.correo = "cand@correo.mx"
    p = persona.postulaciones_activas[-1]
    p.consentimiento = True
    db.commit()
    P = p.codigo
    r = client.get(f"/candidatos/{P}")
    check(r.json().get("clienteIdVacante") == cliente.id and r.json().get("clienteVacante") == "Tiendas Sol", "postulacion_dict expone clienteIdVacante")

    def enviados(desde):
        return db.query(NotificacionEnviada).filter(NotificacionEnviada.id > desde).all()

    def agendar(evaluador, fecha, modalidad="Teléfono", notificar=None, **cita):
        cuerpo = {"tipo": "entrevista_humana", "forma": "asignada", "evaluador": evaluador,
                  "cita": {"fecha": fecha, "hora": "11:00", "modalidad": modalidad, **cita}}
        if notificar is not None:
            cuerpo["notificar"] = notificar
        return client.post(f"/evaluaciones/postulaciones/{P}", json=cuerpo)

    def ultima():
        db.expire_all()
        return db.query(Evaluacion).order_by(Evaluacion.id.desc()).first()

    # ---------- 3. Interno: correo Y WhatsApp del perfil, resultado visible ----------
    base = db.query(NotificacionEnviada).count()
    r = agendar({"tipo": "interno", "usuario_id": lucia_id}, "2026-10-01", notificar={"cliente_correo": False, "cliente_whatsapp": False})
    check(r.status_code == 201 and "resultados" in r.json() and "candidato" in r.json() and "evaluacion" in r.json(), "programar (interno) responde {evaluacion, resultados, candidato}")
    res = r.json()["resultados"]
    pares = {(x["destinatario"], x["canal"]): x for x in res}
    check(set(pares) == {("candidato", "whatsapp"), ("candidato", "correo"), ("entrevistador", "whatsapp"), ("entrevistador", "correo")},
          "punto 5: al programar salen correo Y WhatsApp a candidato y evaluador")
    check(pares[("entrevistador", "correo")]["destino"] == "lucia@rh.mx" and pares[("entrevistador", "whatsapp")]["destino"] == "3399998888",
          "interno: correo y WhatsApp tomados del perfil del Usuario, no capturados")
    check(pares[("candidato", "correo")]["destino"] == "cand@correo.mx" and pares[("candidato", "whatsapp")]["destino"] == persona.telefono,
          "candidato: correo y WhatsApp de su ficha")
    check(all(not x["enviado"] for x in res) and "RESEND_API_KEY" in pares[("candidato", "correo")]["detalle"] and "WHATSAPP_PROVIDER" in pares[("candidato", "whatsapp")]["detalle"],
          "resultado visible: en demo cada canal explica por qué no salió (antes: silencio)")
    env = enviados(base)
    check(len(env) == 4 and all(e.evento == "evaluacion_asignada" for e in env), "NotificacionEnviada registra los 4 envíos")
    ev = ultima()
    check(ev.evaluador_tipo == "interno" and ev.evaluador_usuario_id == lucia_id and ev.evaluador_contacto_id is None, "evaluador interno guarda usuario_id")
    check(r.json()["evaluacion"]["evaluador"]["contactoId"] is None and "whatsapp" in r.json()["evaluacion"]["evaluador"], "serializador expone contactoId/whatsapp del evaluador")

    # ---------- 4. Externo desde contacto del Cliente + contactos seleccionados ----------
    r = agendar({"tipo": "externo", "contacto_id": k1.id}, "2026-10-02", "Videollamada", liga_videollamada="https://meet.x/abc",
                notificar={"cliente_correo": True, "cliente_whatsapp": True, "cliente_contactos_ids": [k1.id]})
    check(r.status_code == 201, "programar (externo por contacto del Cliente)")
    ev = ultima()
    check(ev.evaluador_tipo == "externo" and ev.evaluador_contacto_id == k1.id and ev.evaluador_nombre == "Paola Ruiz" and ev.evaluador_correo == "paola@sol.mx"
          and ev.evaluador_whatsapp == "3311112222", "externo por contacto: nombre/correo/WhatsApp del contacto y contacto_id guardado (nada capturado)")
    cli = [x for x in r.json()["resultados"] if x["destinatario"] == "cliente"]
    check(sorted(x["destino"] for x in cli) == ["3311112222", "paola@sol.mx"], "punto 4: solo el contacto marcado recibe (correo y WhatsApp); los demás no")
    check(r.json()["evaluacion"]["evaluador"]["contactoId"] == k1.id, "ficha muestra contactoId")

    # cliente sin ids → todos los contactos (comportamiento Fase D)
    r = agendar({"tipo": "externo", "contacto_id": k1.id}, "2026-10-03", notificar={"cliente_correo": True, "cliente_whatsapp": False})
    cli = [x for x in r.json()["resultados"] if x["destinatario"] == "cliente"]
    check(r.status_code == 201 and sorted(x["destino"] for x in cli) == ["", "marco@sol.mx", "paola@sol.mx"], "sin cliente_contactos_ids → todos los contactos del Cliente (uno sin correo queda 'sin dato de contacto')")
    check(any(x["destino"] == "" and "sin dato de contacto" in x["detalle"] for x in cli), "contacto sin correo → detalle 'sin dato de contacto' visible")

    # lista vacía explícita → ninguno
    r = agendar({"tipo": "externo", "contacto_id": k1.id}, "2026-10-04", notificar={"cliente_correo": True, "cliente_contactos_ids": []})
    check(r.status_code == 201 and not [x for x in r.json()["resultados"] if x["destinatario"] == "cliente"], "cliente_contactos_ids=[] → Cliente activo pero ningún contacto → no manda")

    # validaciones (especificación, sección 4: nombre obligatorio y al menos correo o WhatsApp)
    r = agendar({"tipo": "externo", "contacto_id": k_ajeno.id}, "2026-10-05")
    check(r.status_code == 400, "contacto de OTRO Cliente → 400")
    r = agendar({"tipo": "externo", "contacto_id": k3.id}, "2026-10-05")
    check(r.status_code == 201 and ultima().evaluador_whatsapp == "3300000000", "contacto con solo WhatsApp → válido (al menos uno de correo o WhatsApp)")
    k4 = ClienteContacto(cliente_id=cliente.id, nombre="Sin", apellidos="Datos", correo="", telefono="")
    db.add(k4)
    db.commit()
    r = agendar({"tipo": "externo", "contacto_id": k4.id}, "2026-10-05")
    check(r.status_code == 400 and "correo o WhatsApp" in r.json()["detail"], "contacto sin correo ni WhatsApp → 400 con instrucción")
    r = agendar({"tipo": "externo", "nombre": "Otro"}, "2026-10-05")
    check(r.status_code == 400, "«+ Nuevo evaluador» sin correo ni WhatsApp → 400")
    r = agendar({"tipo": "externo", "nombre": "Otro Externo", "correo": "otro@ext.mx", "whatsapp": "5566778899"}, "2026-10-06",
                notificar={"cliente_correo": False, "cliente_whatsapp": False})
    ev = ultima()
    nuevo = db.get(ClienteContacto, ev.evaluador_contacto_id) if ev.evaluador_contacto_id else None
    check(r.status_code == 201 and ev.evaluador_correo == "otro@ext.mx" and ev.evaluador_whatsapp == "5566778899" and nuevo and nuevo.cliente_id == cliente.id,
          "«+ Nuevo evaluador» funciona y queda guardado como contacto reutilizable del Cliente")
    pares = {(x["destinatario"], x["canal"]): x for x in r.json()["resultados"]}
    check(pares[("entrevistador", "correo")]["destino"] == "otro@ext.mx" and pares[("entrevistador", "whatsapp")]["destino"] == "5566778899", "externo nuevo: se le notifica por correo y WhatsApp")

    # vacante sin Cliente → cliente_contactos_ids se ignora en silencio
    p.vacante.cliente_id = None
    db.commit()
    r = agendar({"tipo": "interno", "usuario_id": lucia_id}, "2026-10-07", notificar={"cliente_correo": True, "cliente_contactos_ids": [k1.id]})
    check(r.status_code == 201 and not [x for x in r.json()["resultados"] if x["destinatario"] == "cliente"], "vacante sin Cliente → nada al cliente aunque manden ids (punto 26)")
    r = agendar({"tipo": "externo", "contacto_id": k1.id}, "2026-10-08")
    check(r.status_code == 400, "vacante sin Cliente: elegir contacto → 400 (solo «+ Nuevo evaluador»)")
    n_contactos = db.query(ClienteContacto).count()
    r = agendar({"tipo": "externo", "nombre": "Sin Cliente", "correo": "sc@ext.mx"}, "2026-10-09")
    check(r.status_code == 201 and db.query(ClienteContacto).count() == n_contactos, "vacante sin Cliente: «+ Nuevo evaluador» funciona sin guardar contacto")

    db.close()

print(f"\n🎉 Entrevista Humana (Fase 7A) verificada: {OK} comprobaciones OK.")
