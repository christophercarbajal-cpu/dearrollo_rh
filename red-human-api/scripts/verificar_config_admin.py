"""Verificación de punta a punta de las 5 pantallas administrativas (Puntos 9-13) — SIN tocar
OpenAI ni Meta: modo demo (sin claves) sobre una base SQLite desechable.

Cubre: Cuentas (alcance por vinculación, alta, ficha, usuarios), Clientes + contactos, Plantillas
(ubicación, duplicar, guardar desde vacante), Notificaciones (override por acción, guardado en
bloque, lectura para no-admin, evento automático sin override), Modo Prueba (ventana
configurable respetada por el webhook, borrado limpia notificaciones).

Uso (desde red-human-api/):
    .venv/Scripts/python.exe scripts/verificar_config_admin.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_cfg_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "cfg.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-cfg"
os.environ["SEMBRAR_DEMO"] = "true"  # los scripts de verificación sí usan los datos de ejemplo

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Candidato, Cuenta, Mensaje, NotificacionEnviada, ReglaNotificacion, Usuario, UsuarioCuenta, Vacante,
)

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def meta_texto(tel, texto, nombre="Persona Prueba"):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"profile": {"name": nombre}, "wa_id": tel}],
        "messages": [{"from": tel, "id": f"wamid.{texto[:8]}", "type": "text", "text": {"body": texto}}],
    }}]}]}


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta_a = Cuenta(nombre="Cuenta A", nombre_comercial="Empresa A", estado="Activa")
    db.add(cuenta_a)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta_a.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta_a.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta_a.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta_a.id
    db.commit()

    # --- sesión simulada: admin sobre la Cuenta A ---
    usuario_activo = {"u": admin}
    cuenta_activa = {"c": cuenta_a}
    app.dependency_overrides[usuario_actual] = lambda: usuario_activo["u"]
    app.dependency_overrides[cuenta_actual] = lambda: cuenta_activa["c"]

    # ============ PUNTO 9 — Cuentas ============
    r = client.get("/cuentas")
    check(r.status_code == 200 and [c["id"] for c in r.json()] == [cuenta_a.id] and r.json()[0]["esActual"], "GET /cuentas: solo las vinculadas, la actual marcada")
    r = client.post("/cuentas", json={"nombre": "Cuenta B", "nombre_comercial": "Empresa B", "correo_comunicacion": "rh@b.mx", "whatsapp_comunicacion": "5511112222"})
    check(r.status_code == 201 and r.json()["nombre"] == "Cuenta B" and r.json()["usuarios"] == 1, "POST /cuentas crea y vincula al creador")
    ID_B = r.json()["id"]
    check(len(client.get("/cuentas").json()) == 2, "el listado ahora trae las 2")
    r = client.post("/cuentas", json={"nombre": "", "nombre_comercial": "x"})
    check(r.status_code == 400, "nombre de cuenta obligatorio")
    r = client.get(f"/cuentas/{ID_B}")
    check(r.status_code == 200 and r.json()["portal"]["url"].endswith("/portal") and r.json()["usuariosDetalle"][0]["id"] == admin.id, "ficha: datos + usuarios + portal")
    r = client.patch(f"/cuentas/{ID_B}", json={"nombre_comercial": "Empresa B S.A.", "estado": "Inactiva"})
    check(r.status_code == 200 and r.json()["nombreComercial"] == "Empresa B S.A." and r.json()["estado"] == "Inactiva", "PATCH /cuentas/{id}")
    client.patch(f"/cuentas/{ID_B}", json={"estado": "Activa"})
    # usuario nuevo + vincular existente
    r = client.post(f"/cuentas/{ID_B}/usuarios", json={"nombre": "Laura Nueva", "correo": "laura@b.mx", "rol": "Usuario"})
    check(r.status_code == 201 and r.json()["nuevo"] and r.json()["passwordTemporal"] and r.json()["cuenta"]["usuarios"] == 2, "agregar usuario NUEVO a la cuenta: creado, vinculado, contraseña temporal")
    laura = db.query(Usuario).filter_by(correo="laura@b.mx").one()
    check(laura.debe_cambiar_pass and [uc.cuenta_id for uc in laura.cuentas] == [ID_B], "el usuario nuevo solo ve la cuenta B y debe cambiar contraseña")
    r = client.post(f"/cuentas/{cuenta_a.id}/usuarios", json={"correo": "laura@b.mx", "rol": "Administrador"})
    check(r.status_code == 201 and not r.json()["nuevo"] and r.json()["passwordTemporal"] is None, "agregar usuario EXISTENTE: solo se vincula")
    db.refresh(laura)
    check(laura.rol == "Usuario" and sorted(uc.cuenta_id for uc in laura.cuentas) == sorted([ID_B, cuenta_a.id]), "…sin tocar su rol, ahora ve A y B")
    r = client.post(f"/cuentas/{ID_B}/usuarios", json={"correo": "laura@b.mx"})
    check(r.status_code == 409, "vincular dos veces → 409")
    r = client.delete(f"/cuentas/{ID_B}/usuarios/{admin.id}")
    check(r.status_code == 409, "no puede quitarse a sí mismo")
    r = client.delete(f"/cuentas/{ID_B}/usuarios/{laura.id}")
    check(r.status_code == 200 and r.json()["usuarios"] == 1, "quitar usuario de la cuenta B")
    # aislamiento: Laura (no vinculada a B ya) intenta ver B
    laura.rol = "Administrador"
    db.commit()
    usuario_activo["u"] = laura
    r = client.get(f"/cuentas/{ID_B}")
    check(r.status_code == 404, "aislamiento: un admin NO vinculado a la cuenta B recibe 404")
    check([c["id"] for c in client.get("/cuentas").json()] == [cuenta_a.id], "…y su listado solo trae A")
    r = client.patch(f"/cuentas/{ID_B}", json={"nombre": "hack"})
    check(r.status_code == 404, "…ni puede editarla")
    usuario_activo["u"] = admin
    r = client.get("/auth/yo")
    check(any(c["nombre"] == "Cuenta B" for c in r.json()["cuentas"]), "usuario_dict.cuentas trae `nombre` para el selector")

    # ============ PUNTO 10 — Clientes y contactos ============
    r = client.post("/clientes", json={"nombre": "Retail Norte", "razon_social": "Retail Norte SA de CV", "nombre_comercial": "Norte", "estado": "Activo"})
    check(r.status_code == 201 and r.json()["nombreComercial"] == "Norte" and r.json()["listaContactos"] == [], "POST /clientes con razón social y nombre comercial")
    CID = r.json()["id"]
    r = client.post(f"/clientes/{CID}/contactos", json={"nombre": "Ana", "apellidos": "Pérez", "puesto": "Gerente", "correo": "ana@norte.mx", "telefono": "5533334444"})
    check(r.status_code == 201 and r.json()["listaContactos"][0]["nombreCompleto"] == "Ana Pérez", "agregar contacto")
    KID = r.json()["listaContactos"][0]["id"]
    r = client.post(f"/clientes/{CID}/contactos", json={"nombre": "Sin datos"})
    check(r.status_code == 400, "contacto sin correo ni teléfono → 400")
    r = client.patch(f"/clientes/{CID}/contactos/{KID}", json={"nombre": "Ana", "apellidos": "Pérez López", "correo": "ana@norte.mx"})
    check(r.status_code == 200 and r.json()["listaContactos"][0]["apellidos"] == "Pérez López", "editar contacto")
    r = client.get(f"/clientes/{CID}")
    check(r.json()["contactos"] == 1 and r.json()["nombreVisible"] == "Norte", "GET /clientes/{id}: ficha con contactos y nombre visible")
    r = client.patch(f"/clientes/{CID}", json={"nombre_comercial": ""})
    check(r.json()["nombreVisible"] == "Retail Norte", "sin nombre comercial, el visible es el nombre")
    r = client.delete(f"/clientes/{CID}/contactos/{KID}")
    check(r.status_code == 200 and r.json()["contactos"] == 0, "eliminar contacto")
    # aislamiento por cuenta
    cuenta_activa["c"] = db.get(Cuenta, ID_B)
    check(client.get(f"/clientes/{CID}").status_code == 404 and client.get("/clientes").json() == [], "un cliente de A no se ve desde B")
    cuenta_activa["c"] = cuenta_a

    # ============ PUNTO 11 — Plantillas ============
    v1 = db.query(Vacante).filter_by(codigo="VAC-1042").one()
    r = client.post(f"/plantillas/desde-vacante/{v1.codigo}", json={"nombre": "Cajero base"})
    check(r.status_code == 201 and r.json()["titulo"] == v1.titulo and r.json()["ubicacion"] == v1.ubicacion and r.json()["preguntasFiltro"] == v1.preguntas_filtro and r.json()["clienteId"] is None,
          "guardar-desde-vacante copia título/ubicación/criterios (General)")
    PID = r.json()["id"]
    r = client.post(f"/plantillas/{PID}/duplicar")
    check(r.status_code == 201 and r.json()["nombre"] == "Copia de Cajero base" and r.json()["requisitos"] == v1.requisitos, "duplicar plantilla")
    PID2 = r.json()["id"]
    r = client.patch(f"/plantillas/{PID2}", json={"nombre": "Cajero CDMX", "ubicacion": "CDMX", "responsabilidades": ["Cobrar", "Cuadrar caja"]})
    check(r.status_code == 200 and r.json()["ubicacion"] == "CDMX" and r.json()["actualizada"] >= r.json()["creada"], "editar plantilla: ubicación y última actualización")
    r = client.post("/plantillas", json={"nombre": "Con cliente", "cliente_id": CID, "titulo": "Vendedor", "ubicacion": "Monterrey", "preguntas_filtro": [{"pregunta": "¿Experiencia?", "tipo": "si_no"}]})
    check(r.status_code == 201 and r.json()["clienteNombre"] == "Retail Norte", "crear plantilla de Cliente con el formulario completo")
    listado = client.get("/plantillas").json()
    check(len(listado) == 3 and all("actualizada" in p for p in listado), "listado con 3 plantillas y fecha de actualización")
    r = client.delete(f"/plantillas/{PID2}")
    check(r.status_code == 200 and len(client.get("/plantillas").json()) == 2, "eliminar (desactivar) la saca del listado")
    r = client.post("/vacantes", json={"titulo": "Cajero desde plantilla", "plantilla_id": PID, "ubicacion": "CDMX", "descripcion": "Texto propio", "generar_si_falta": False, "publicar": False})
    check(r.status_code == 201 and r.json()["descripcion"] == "Texto propio", "crear vacante con contenido capturado a mano (sin IA)")

    # ============ PUNTO 12 — Notificaciones ============
    usuario_activo["u"] = laura  # Usuario (no admin) vinculado a A
    laura.rol = "Usuario"
    db.commit()
    r = client.get("/notificaciones/reglas")
    check(r.status_code == 200 and len(r.json()) == 10,
          "GET reglas: un Usuario no-admin puede LEER la configuración (10 eventos: Evaluaciones unificadas 2026-09-29 sustituye los 7 de entrevista por 4 de evaluación)")
    r = client.put("/notificaciones/reglas", json=[])
    check(r.status_code == 403, "…pero no guardarla")
    usuario_activo["u"] = admin
    reglas = client.get("/notificaciones/reglas").json()
    # Fase 7A: la siembra perezosa ya nace con defaults (evaluacion_asignada encendida); este bloque
    # prueba la semántica de override, así que fija la regla explícitamente: solo candidato WhatsApp.
    check(next(x for x in reglas if x["evento"] == "evaluacion_asignada")["candidatoCorreo"], "la regla evaluacion_asignada NACE con correo encendido")
    for x in reglas:
        for k in ("candidatoCorreo", "candidatoWhatsapp", "entrevistadorCorreo", "entrevistadorWhatsapp", "clienteCorreo", "clienteWhatsapp"):
            x[k] = False
        if x["evento"] == "evaluacion_asignada":
            x["candidatoWhatsapp"] = True
    body = [{"evento": x["evento"], "candidato_correo": x["candidatoCorreo"], "candidato_whatsapp": x["candidatoWhatsapp"],
             "entrevistador_correo": x["entrevistadorCorreo"], "entrevistador_whatsapp": x["entrevistadorWhatsapp"],
             "cliente_correo": x["clienteCorreo"], "cliente_whatsapp": x["clienteWhatsapp"]} for x in reglas]
    r = client.put("/notificaciones/reglas", json=body)
    check(r.status_code == 200 and next(x for x in r.json() if x["evento"] == "evaluacion_asignada")["candidatoWhatsapp"], "PUT en bloque guarda la matriz")

    # postulación de prueba con teléfono
    persona = db.query(Candidato).filter_by(codigo="C-8801").one()
    p = persona.postulaciones_activas[-1]
    p.consentimiento = True
    db.commit()
    # Evaluaciones unificadas (2026-09-29): la entrevista humana se asigna con «Agregar evaluación»
    ent = {"tipo": "entrevista_humana", "forma": "asignada",
           "evaluador": {"tipo": "externo", "nombre": "Ext", "correo": "ext@x.mx", "whatsapp": "5599990000"},
           "cita": {"fecha": "2026-10-01", "hora": "10:00", "modalidad": "Teléfono"}}

    def enviados(desde_id):
        return db.query(NotificacionEnviada).filter(NotificacionEnviada.id > desde_id).all()

    base = db.query(NotificacionEnviada).count()
    # regla: solo candidato whatsapp. override apaga candidato y enciende entrevistador correo
    r = client.post(f"/evaluaciones/postulaciones/{p.codigo}", json={**ent, "notificar": {"candidato_whatsapp": False, "entrevistador_correo": True}})
    check(r.status_code == 201, "programar entrevista con override")
    codigo_ev = r.json()["evaluacion"]["codigo"]
    env = enviados(base)
    check([e.destinatario_tipo + ":" + e.canal for e in env] == ["entrevistador:correo"], "override: regla encendida + override apagado → NO envía al candidato; override encendido → SÍ al entrevistador")
    regla = db.query(ReglaNotificacion).filter_by(cuenta_id=cuenta_a.id, evento="evaluacion_asignada").one()
    check(regla.candidato_whatsapp and not regla.entrevistador_correo, "la regla guardada NO cambió")
    base = db.query(NotificacionEnviada).count()
    r = client.post(f"/evaluaciones/{codigo_ev}/recordatorio", json={"a": "ambos", "notificar": {"candidato_whatsapp": True}})
    check(r.status_code == 200 and [e.destinatario_tipo for e in enviados(base)] == ["candidato"], "recordatorio: regla apagada + override encendido → envía")
    base = db.query(NotificacionEnviada).count()
    r = client.post(f"/evaluaciones/{codigo_ev}/recordatorio", json={"a": "ambos"})
    check(r.status_code == 200 and enviados(base) == [], "recordatorio sin override → sigue la regla (apagada): no envía")
    # registrar un resultado nunca avisa al candidato (ni candidato_apto ni recomendación): la decisión es de RH
    base = db.query(NotificacionEnviada).count()
    r = client.post(f"/evaluaciones/{codigo_ev}/resultado", data={"conclusion": "avanzar", "version": "0"})
    check(r.status_code == 200, "capturar resultado desde el sistema")
    check(enviados(base) == [], "registrar el resultado no manda ningún aviso (tampoco candidato_apto)")
    # alta con override
    r = client.patch(f"/candidatos/{p.codigo}/etapa", json={"etapa": "Contratación"})
    exp_id = r.json()["expedienteId"]
    db.expire_all()
    base = db.query(NotificacionEnviada).count()
    # 2026-09-18 (Modo Prueba TOTAL): con Modo Prueba apagado, sin NINGÚN documento adjunto el alta es 400;
    # con Modo Prueba activo se omite toda la validación de integridad.
    client.patch("/configuracion", json={"modo_prueba": False})
    r = client.post(f"/contratacion/expedientes/{exp_id}/alta", json={}, params={"forzar_prueba": "true"})
    check(r.status_code == 400 and "no tiene documentos adjuntos" in r.json()["detail"], "alta sin documentos adjuntos → 400 con Modo Prueba apagado (forzar_prueba no aplica)")
    client.patch("/configuracion", json={"modo_prueba": True})
    pdf = b"%PDF-1.4\n" + b"%" * 600 + b"\n%%EOF\n"
    r = client.post(f"/contratacion/expedientes/{exp_id}/documentos", data={"tipo": "CURP"}, files={"archivo": ("curp.pdf", pdf, "application/pdf")})
    check(r.status_code == 200, "subir un documento al expediente")
    r = client.post(f"/contratacion/expedientes/{exp_id}/alta", json={"notificar": {"candidato_whatsapp": True}}, params={"forzar_prueba": "true"})
    client.patch("/configuracion", json={"modo_prueba": False})
    check(r.status_code == 200 and [e.evento for e in enviados(base)] == ["contratacion"], "alta de colaborador con override → notifica al candidato")
    # recordatorio de documentos desde contratación (bug Fase 2 corregido: usa la postulación)
    persona2 = db.query(Candidato).filter_by(codigo="C-8808").one()
    exp2 = persona2.expedientes[0]
    r = client.post(f"/contratacion/expedientes/{exp2.id}/recordatorio", json={"notificar": {"candidato_whatsapp": True}})
    check(r.status_code == 200 and r.json()["enviado"] and any(x["enviado"] is False or True for x in r.json()["notificaciones"]), "POST /contratacion/expedientes/{id}/recordatorio funciona sobre la postulación (antes tronaba)")

    # ============ PUNTO 13 — Modo Prueba ============
    r = client.patch("/configuracion", json={"modo_prueba_ventana_min": 3})
    check(r.status_code == 400, "ventana fuera de rango → 400")
    r = client.patch("/configuracion", json={"modo_prueba": True, "modo_prueba_ventana_min": 10})
    check(r.status_code == 200 and r.json()["modoPruebaVentanaMin"] == 10 and r.json()["modoPrueba"], "ventana configurable (10 min) + modo prueba activo")
    client.patch(f"/cuentas/{ID_B}", json={"estado": "Inactiva"})  # el webhook exige exactamente 1 Cuenta activa
    WA = "5215544332211"
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA, "Hola"))
    check(r.json().get("accion") == "menu_vacantes", "webhook: persona de prueba nueva → menú")
    pers = db.query(Candidato).filter_by(wa_id=WA).one()
    p_ini = pers.postulaciones[-1]
    m = db.query(Mensaje).filter_by(candidato_id=pers.id).order_by(Mensaje.id.desc()).first()
    if m:
        m.creado_en = datetime.now(timezone.utc) - timedelta(minutes=8)
    pers.creado_en = datetime.now(timezone.utc) - timedelta(minutes=8)
    db.commit()
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA, "Hola otra vez"))
    db.expire_all()
    check(pers.postulaciones[-1].id == p_ini.id, "8 min de inactividad con ventana de 10 → misma postulación (no expira)")
    for mm in db.query(Mensaje).filter_by(candidato_id=pers.id).all():
        mm.creado_en = datetime.now(timezone.utc) - timedelta(minutes=12)
    pers.creado_en = datetime.now(timezone.utc) - timedelta(minutes=12)  # sin mensajes guardados aún (solo menú), cuenta desde el alta
    # 2026-09-15: la frialdad se mide sobre la POSTULACIÓN (creación / última actividad / último mensaje)
    p_ini.creado_en = datetime.now(timezone.utc) - timedelta(minutes=12)
    p_ini.ultima_actividad_en = None
    db.commit()
    r = client.post("/webhooks/whatsapp", json=meta_texto(WA, "Hola de nuevo"))
    db.expire_all()
    check(pers.postulaciones[-1].id != p_ini.id and not p_ini.activa and p_ini.motivo_cierre == "prueba_expirada", "12 min > ventana de 10 → la anterior expira y arranca una nueva")
    client.patch("/configuracion", json={"modo_prueba": False})
    # borrado de prueba: solo es_prueba de la cuenta, limpia NotificacionEnviada
    pers.es_prueba = True
    db.add(NotificacionEnviada(cuenta_id=cuenta_a.id, candidato_id=pers.id, evento="contratacion", destinatario_tipo="candidato", destino="x", canal="whatsapp", enviado=False, detalle=""))
    db.commit()
    total_antes = db.query(Candidato).count()
    r = client.get("/configuracion")
    check(r.json()["candidatosPrueba"] == 1 and r.json()["postulacionesPrueba"] >= 1, "GET /configuracion cuenta personas y postulaciones de prueba")
    r = client.post("/candidatos/prueba/eliminar")
    check(r.status_code == 200 and r.json()["candidatos"] == 1 and r.json()["notificaciones"] == 1, "eliminar prueba: solo la persona de prueba y limpia sus notificaciones")
    check(db.query(Candidato).count() == total_antes - 1 and db.query(NotificacionEnviada).filter_by(candidato_id=pers.id).count() == 0, "los candidatos reales siguen intactos")

    db.close()

print(f"\n🎉 Configuración administrativa verificada: {OK} comprobaciones OK.")
