"""Verificación de Evaluaciones — ligas externas, captura unificada, seguimiento por tipo y revisión de RH (2026-10-01).

1) Agregar una evaluación NO cambia la etapa, salvo crear una entrevista humana: mueve a «Filtro humano» (2026-10-01).
2) Ligas externas (consentimiento, evaluador/médico, otro sistema): existen aunque el envío automático FALLE, se
   reutilizan, el estado del envío viaja aparte (también el fallido) y «Enviar o reenviar» funciona por liga.
3) Captura manual de RH sobre una evaluación asignada a un externo = MISMO registro que la liga (autor y fecha).
4) Médica: sin consentimiento solo la liga de consentimiento; al otorgarlo aparece la «Liga del médico» (también
   con registro directo); la captura manual sigue exigiendo el consentimiento.
5) Psicométrica: nombre de prueba + proveedor; Pendiente → Enviada → En curso (solo con confirmación) →
   Resultado recibido → Revisada.
6) Socioeconómica: evaluador con contacto, cita opcional; Pendiente → En proceso → Resultado recibido → Revisada.
7) Revisión: exige conclusión de RH, guarda usuario y fecha, no mueve la etapa; un resultado nuevo la reinicia.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_evaluaciones_ligas_revision.py
"""

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_evlr_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "evlr.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY",
          "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-evlr"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Candidato, Cliente, Cuenta, Evaluacion, EventoEvaluacion, Postulacion, Usuario, UsuarioCuenta, Vacante  # noqa: E402
from app.services import notificaciones as sn  # noqa: E402

OK = 0
ENVIOS = []
FALLA_WHATSAPP = {"activa": False}


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _texto(destino, texto, *a, **k):
    if FALLA_WHATSAPP["activa"]:
        return {"enviado": False, "proveedor": "prueba", "detalle": "Meta 131047 fuera de ventana"}
    ENVIOS.append(("whatsapp", destino, texto))
    return {"enviado": True, "proveedor": "prueba", "detalle": "ok"}


async def _correo(destino, asunto, html, *a, **k):
    ENVIOS.append(("correo", destino, asunto + " " + html))
    return {"enviado": True, "proveedor": "prueba", "detalle": "ok"}


sn.enviar_mensaje = _texto
sn.enviar_correo = _correo
import app.services.correo as _scorreo  # noqa: E402
import app.services.whatsapp as _swa  # noqa: E402

_scorreo.enviar_correo = _correo
_swa.enviar_mensaje = _texto  # el envío directo de ligas al candidato importa enviar_mensaje al momento

PDF = b"%PDF-1.4\n" + b"0" * 900


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta EVLR", nombre_comercial="EVLR RH", estado="Activa", correo_comunicacion="rh@evlr.mx")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    cliente = Cliente(cuenta_id=cuenta.id, nombre="Tiendas Luna", nombre_comercial="Luna Retail", estado="Activo")
    db.add(cliente)
    db.flush()
    for v in db.query(Vacante).all():
        v.cuenta_id, v.cliente_id = cuenta.id, cliente.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id, p.consentimiento = cuenta.id, True
    persona = db.query(Candidato).filter_by(codigo="C-8801").one()
    persona.correo = "cand@correo.mx"
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    p = persona.postulaciones_activas[-1]
    P = p.codigo
    p.etapa = "Entrevista IA"  # Filtro Red Human (la columna «Evaluación» se retiró el 2026-10-01)
    db.commit()

    def etapa():
        db.expire_all()
        return db.get(Postulacion, p.id).etapa

    def omitir_previos(codigo):
        """2026-10-06: toda postulación tiene ruta. Esta verificación es de evaluaciones, no de la ruta: RH omite (con
        permiso) los obligatorios de Prefiltro y Filtro Red Human y apaga el avance automático de su copia."""
        # Primero se apaga el avance automático de su copia (desde 2026-10-07 omitir corre el avance de la ruta)
        db.expire_all()
        pp = db.query(Postulacion).filter_by(codigo=codigo).one()
        pp.proceso = {**pp.proceso, "etapas": {e: {"avance_automatico": False} for e in pp.proceso.get("etapas", {})}}
        db.commit()
        seg = client.get(f"/procesos/postulaciones/{codigo}").json()
        for e in seg.get("etapas", []):
            if e["etapa"] in ("Prefiltro", "Entrevista IA"):
                for x in e["pasos"]:
                    if x["obligatorio"] and x["estado"] in ("pendiente", "en_curso"):
                        client.post(f"/procesos/postulaciones/{codigo}/pasos/{x['id']}/omitir",
                                    json={"motivo": "Prueba: fuera del alcance de esta verificación"})
        db.expire_all()

    def tarjeta(codigo):
        return next(x for x in client.get(f"/evaluaciones/postulaciones/{P}").json() if x["codigo"] == codigo)

    def liga(t, clave):
        return next((x for x in t["ligas"] if x["clave"] == clave), None)

    # ---------------- 1. Solo la entrevista humana mueve (a Filtro humano) ----------------
    print("\n--- 1. Agregar evaluación: solo la entrevista humana mueve a Filtro humano ---")
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Entrevista Humana"})
    check(r.status_code == 409 and ("Agregar evaluación" in r.json()["detail"] or "pasos obligatorios" in r.json()["detail"]),
          "sin entrevista humana agregada no se envía a «Filtro humano» (salvo movimiento manual)")
    omitir_previos(P)
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "tecnica", "forma": "registro_directo"})
    check(r.status_code == 201 and etapa() == "Entrevista IA", "agregar otra evaluación (técnica) NO cambia la columna")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "externo", "nombre": "Leo Externo", "correo": "leo@externo.mx"},
        "cita": {"fecha": "2026-10-20", "hora": "10:00", "modalidad": "Presencial", "direccion": "Av. Juárez 10"},
    })
    check(r.status_code == 201 and etapa() == "Entrevista Humana", "agregar una entrevista humana con cita mueve al candidato a Filtro humano")
    EH = r.json()["evaluacion"]["codigo"]

    # ---------------- 2. Ligas externas ----------------
    print("\n--- 2. Ligas externas ---")
    FALLA_WHATSAPP["activa"] = True
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={
        "tipo": "socioeconomica", "forma": "asignada", "evaluador": {"tipo": "externo", "nombre": "Sofía Visitadora", "whatsapp": "3311112222"},
    })
    check(r.status_code == 201, "socioeconómica con evaluador externo (solo WhatsApp) se guarda aunque el WhatsApp falle")
    S = r.json()["evaluacion"]
    lev = liga(S, "evaluador")
    check(lev and lev["url"].endswith(f"/evaluacion/{db.query(Evaluacion).filter_by(codigo=S['codigo']).one().token_evaluador}"),
          "la liga del evaluador existe aunque el envío automático haya fallado")
    check(lev["ultimoEnvio"] and lev["ultimoEnvio"]["enviado"] is False and "131047" in lev["ultimoEnvio"]["envios"][0]["detalle"],
          "el estado del envío fallido se muestra aparte (con el motivo)")
    check(S["seguimientoTexto"] == "Pendiente" and S["enviadaEn"] is None, "socioeconómica: Pendiente; un envío fallido no cuenta como enviado")
    token_antes = lev["url"]
    FALLA_WHATSAPP["activa"] = False
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/{S['codigo']}/ligas/evaluador/enviar")
    S = r.json()["evaluacion"]
    check(r.status_code == 200 and any(d == "3311112222" for c, d, t in ENVIOS) and liga(S, "evaluador")["url"] == token_antes,
          "«Enviar o reenviar»: sale con la MISMA liga (se reutiliza)")
    check(liga(S, "evaluador")["ultimoEnvio"]["enviado"] is True, "el estado del envío se actualiza a enviado")
    r = client.post(f"/evaluaciones/{S['codigo']}/ligas/consentimiento/enviar")
    check(r.status_code == 409, "una liga que no aplica (consentimiento en socioeconómica) → 409")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "tecnica", "forma": "liga_otro_sistema", "liga_externa_candidato": "https://otro.sistema/caso"})
    T = r.json()["evaluacion"]
    check(liga(T, "otro_sistema") and liga(T, "otro_sistema")["url"] == "https://otro.sistema/caso" and liga(T, "otro_sistema")["ultimoEnvio"]["enviado"],
          "liga de otro sistema con su estado de envío al candidato")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/{T['codigo']}/ligas/otro_sistema/enviar")
    check(r.status_code == 200 and any("https://otro.sistema/caso" in t for c, d, t in ENVIOS), "reenviar la liga de otro sistema al candidato")

    # ---------------- 3. Captura manual = mismo registro ----------------
    print("\n--- 3. Captura manual unificada ---")
    s_tarjeta = tarjeta(S["codigo"])
    check(s_tarjeta["acciones"]["principal"] == "registrar_resultado", "asignada a un externo: RH puede «Registrar resultado / Adjuntar reporte»")
    r = client.post(f"/evaluaciones/{S['codigo']}/confirmar-inicio")
    check(r.status_code == 200 and r.json()["evaluacion"]["seguimientoTexto"] == "En proceso", "socioeconómica: confirmación de inicio → En proceso")
    r = client.post(f"/evaluaciones/{S['codigo']}/resultado", data={"comentarios": "Visita domiciliaria sin hallazgos", "realizada_por": "Sofía Visitadora", "version": "0"},
                    files=[("archivos", ("reporte.pdf", PDF, "application/pdf"))])
    check(r.status_code == 200 and r.json()["evaluacion"]["seguimientoTexto"] == "Resultado recibido", "RH carga el reporte → Resultado recibido")
    ev_s = db.query(Evaluacion).filter_by(codigo=S["codigo"]).one()
    db.refresh(ev_s)
    tok_s = ev_s.token_evaluador
    r = client.post(f"/evaluaciones/publica/{tok_s}/resultado", data={"comentarios": "Complemento: referencias vecinales OK", "version": str(ev_s.resultado_version)})
    db.refresh(ev_s)
    check(r.status_code == 200 and ev_s.registrada_por == admin.nombre and ev_s.realizada_por == "Sofía Visitadora" and ev_s.registrada_en
          and "Complemento: referencias vecinales OK" in ev_s.comentarios and len(ev_s.adjuntos) == 1
          and db.query(Evaluacion).filter_by(postulacion_id=p.id, tipo="socioeconomica").count() == 1,
          "la liga del evaluador complementa el MISMO registro (resultado, observaciones, archivo, autor y fecha)")
    aid = ev_s.adjuntos[0]["id"]
    r1 = client.get(f"/evaluaciones/{S['codigo']}/adjuntos/{aid}")
    r2 = client.get(f"/evaluaciones/{S['codigo']}/adjuntos/{aid}?descargar=true")
    check(r1.headers["content-disposition"].startswith("inline") and r2.headers["content-disposition"].startswith("attachment"),
          "el reporte se abre en el navegador o se descarga")

    # ---------------- 7. Revisión de RH (sobre la socioeconómica) ----------------
    print("\n--- 7. Revisión de RH ---")
    t = tarjeta(S["codigo"])
    check(t["acciones"]["principal"] == "marcar_revisada" and t["revision"] is None and t["comentarios"] and t["adjuntos"],
          "con resultado: la tarjeta trae resultado, observaciones y reporte; acción principal «Marcar como revisada»")
    r = client.post(f"/evaluaciones/{S['codigo']}/revisar", json={"conclusion": "", "comentario": "ok"})
    check(r.status_code == 400, "la revisión exige la conclusión de RH")
    etapa_antes = etapa()
    r = client.post(f"/evaluaciones/{S['codigo']}/revisar", json={"conclusion": "favorable", "comentario": "Sin observaciones"})
    rev = r.json()["evaluacion"]["revision"]
    check(r.status_code == 200 and rev["revisadaPor"] == admin.nombre and rev["revisadaEn"] and rev["conclusionTexto"] == "Favorable"
          and r.json()["evaluacion"]["seguimientoTexto"] == "Revisada", "«Marcar como revisada» guarda conclusión, usuario y fecha → Revisada")
    check(etapa() == etapa_antes, "revisar NO mueve la etapa")
    ev_s = db.query(Evaluacion).filter_by(codigo=S["codigo"]).one()
    db.refresh(ev_s)
    r = client.post(f"/evaluaciones/publica/{tok_s}/resultado", data={"comentarios": "Otro dato", "version": str(ev_s.resultado_version)})
    t = tarjeta(S["codigo"])
    check(r.status_code == 200 and t["revision"] is None and t["seguimientoTexto"] == "Resultado recibido"
          and db.query(EventoEvaluacion).filter_by(evaluacion_id=ev_s.id, accion="revision_reiniciada").count() == 1,
          "un resultado nuevo vuelve a requerir revisión (la anterior queda en el historial)")

    # ---------------- 4. Médica ----------------
    print("\n--- 4. Evaluación médica ---")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "medica", "forma": "registro_directo"})
    M = r.json()["evaluacion"]
    check(liga(M, "consentimiento") and not liga(M, "evaluador") and M["ligaEvaluador"] is None,
          "sin consentimiento: solo «Liga de consentimiento del candidato» (sin liga del médico)")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/{M['codigo']}/ligas/consentimiento/enviar")
    check(r.status_code == 200 and liga(r.json()["evaluacion"], "consentimiento")["ultimoEnvio"]["enviado"], "enviar la liga de consentimiento con su estado")
    r = client.post(f"/evaluaciones/{M['codigo']}/resultado", data={"conclusion": "apto", "version": "0"})
    check(r.status_code == 409 and "consentimiento" in r.json()["detail"], "la captura manual sigue exigiendo el consentimiento")
    tok_c = db.query(Evaluacion).filter_by(codigo=M["codigo"]).one().consentimiento_token
    client.post(f"/evaluaciones/publica/consentimiento/{tok_c}/aceptar", json={"nombre": "Carlos Hernández Ruiz", "acepto": True})
    m = tarjeta(M["codigo"])
    lm = liga(m, "evaluador")
    check(not liga(m, "consentimiento") and lm and lm["titulo"] == "Liga del médico" and not lm["puedeEnviar"] and lm["motivoNoEnvio"],
          "con consentimiento: aparece la «Liga del médico» (abrir/copiar; sin contacto, el envío explica por qué)")
    tok_m = db.query(Evaluacion).filter_by(codigo=M["codigo"]).one().token_evaluador
    r = client.post(f"/evaluaciones/publica/{tok_m}/resultado", data={"conclusion": "apto", "comentarios": "Dictamen", "version": "0"},
                    files=[("archivos", ("dictamen.pdf", PDF, "application/pdf"))])
    check(r.status_code == 200 and r.json()["evaluacion"]["conclusionTexto"] == "Apto", "el médico registra resultado y adjunta dictamen por su liga")

    # ---------------- 5. Psicométrica ----------------
    print("\n--- 5. Psicométrica ---")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "psicometrica", "forma": "liga_otro_sistema", "nombre": "Batería Cleaver + Terman",
                                                             "proveedor": "Evaluatest", "liga_externa_candidato": "https://evaluatest.mx/x"})
    PS = r.json()["evaluacion"]
    check(r.status_code == 201 and PS["nombre"] == "Batería Cleaver + Terman" and PS["proveedor"] == "Evaluatest" and not PS["usaPsicometricas"],
          "psicométrica guarda nombre de prueba/batería y proveedor (texto libre, sin API)")
    check(PS["seguimientoTexto"] == "Esperando candidato", "liga enviada al candidato → Esperando candidato (no «En curso» sin confirmación)")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "psicometrica", "forma": "asignada", "nombre": "16PF", "proveedor": "Consultora X",
                                                             "evaluador": {"tipo": "externo", "nombre": "Psic. Ruiz", "correo": "ruiz@consultora.mx"}})
    PA = r.json()["evaluacion"]
    check(liga(PA, "evaluador") and PA["seguimientoTexto"] == "Esperando evaluador", "asignar a una persona: liga para el evaluador y «Esperando evaluador»")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "psicometrica", "forma": "registro_directo", "nombre": "DISC"})
    PR = r.json()["evaluacion"]
    check(PR["seguimientoTexto"] == "Pendiente" and not PR["ligas"], "registrar resultado ahora: sin ligas, Pendiente hasta guardar")
    r = client.post(f"/evaluaciones/{PS['codigo']}/confirmar-inicio")
    check(r.status_code == 200 and r.json()["evaluacion"]["seguimientoTexto"] == "En curso", "con confirmación de inicio → En curso")
    r = client.post(f"/evaluaciones/{PS['codigo']}/confirmar-inicio")
    check(r.status_code == 200, "confirmar dos veces es idempotente")
    r = client.post(f"/evaluaciones/{PS['codigo']}/resultado", data={"comentarios": "Perfil D", "version": "0"}, files=[("archivos", ("informe.pdf", PDF, "application/pdf"))])
    check(r.json()["evaluacion"]["seguimientoTexto"] == "Resultado recibido", "→ Resultado recibido")
    r = client.post(f"/evaluaciones/{PS['codigo']}/revisar", json={"conclusion": "con_observaciones"})
    check(r.json()["evaluacion"]["seguimientoTexto"] == "Revisada", "→ Revisada")
    check(etapa() == "Entrevista Humana", "ninguna otra evaluación, resultado ni revisión movió la etapa")

    # ---------------- 6. Socioeconómica con cita ----------------
    print("\n--- 6. Socioeconómica con cita ---")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "socioeconomica", "forma": "asignada",
                                                             "evaluador": {"tipo": "interno", "usuario_id": admin.id},
                                                             "cita": {"fecha": "2026-10-22", "hora": "09:00", "modalidad": "Presencial", "direccion": "Calle 5 #20"}})
    SC = r.json()["evaluacion"]
    check(r.status_code == 201 and SC["cita"]["direccion"] == "Calle 5 #20" and SC["evaluador"]["tipo"] == "interno" and liga(SC, "evaluador"),
          "socioeconómica con evaluador interno, cita con dirección y liga de captura")
    check(SC["acciones"]["secundaria"] == "confirmar_inicio", "acción para confirmar el inicio (En proceso)")

    # ---------------- 1b. Sin doble movimiento ----------------
    r = client.patch(f"/candidatos/{P}/etapa", json={"etapa": "Entrevista Humana"})
    check(r.status_code == 409 and etapa() == "Entrevista Humana", "ya en Filtro humano: el movimiento no se repite")
    check(tarjeta(EH)["estado"] == "pendiente", "mover la etapa no altera la evaluación")
    db.close()

print(f"\n{OK} verificaciones OK")
