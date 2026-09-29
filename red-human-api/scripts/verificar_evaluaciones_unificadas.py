"""Verificación de Evaluaciones unificadas — Fase 1 (2026-09-29, especificación de Raúl).

Recorre la API nueva de punta a punta:
1) Pantalla única: entrevista humana asignada (evaluador externo nuevo → contacto del Cliente; sin correo ni
   WhatsApp → 400), con cita en zona de la organización, avisos a candidato + evaluador; la tarjeta pasa a
   «Entrevista Humana». Otra (nombre obligatorio), liga de otro sistema, registro directo sin evaluador.
2) Formulario único: el MISMO endpoint lógico desde la liga y desde el sistema; conclusión por tipo (entrevista
   obligatoria; médica Apto…; resto Favorable… opcional); basta un adjunto; autor ≠ captura; «Nuevo resultado»
   hasta que RH abre; RH ya registró → el evaluador solo complementa; versión vieja → 409 «Este resultado cambió…».
3) Estados: solo 5 y sus transiciones (realizada, no realizada, reprogramar, cancelar; la liga de una cancelada no
   acepta resultados). Consentimiento médico = condición: sin él no sale la liga al evaluador ni se guarda
   resultado (tampoco desde el sistema); al aceptarlo sale el aviso; rechazado → solo Cancelar.
4) NINGÚN resultado mueve la etapa ni avisa al candidato que avanza. Historial append-only.
5) Psicométricas.mx: el webhook encuentra la evaluación por `clave_proveedor` y guarda el resultado vía proveedor.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_evaluaciones_unificadas.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_evu_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "evu.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY",
          "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-evu"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Candidato, Cliente, ClienteContacto, Cuenta, Evaluacion, EventoEvaluacion, NotificacionEnviada, Postulacion, Usuario, UsuarioCuenta, Vacante,
)
from app.services import notificaciones as sn  # noqa: E402

OK = 0
ENVIOS = []  # (canal, destino, contenido)


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _texto(destino, texto, *a, **k):
    ENVIOS.append(("whatsapp", destino, texto))
    return {"enviado": True, "proveedor": "prueba", "detalle": "ok"}


async def _correo(destino, asunto, html, *a, **k):
    ENVIOS.append(("correo", destino, asunto + " " + html))
    return {"enviado": True, "proveedor": "prueba", "detalle": "ok"}


sn.enviar_mensaje = _texto
sn.enviar_correo = _correo
import app.services.correo as _scorreo  # noqa: E402

_scorreo.enviar_correo = _correo  # el aviso a RH importa enviar_correo al momento

PDF = b"%PDF-1.4\n" + b"0" * 900
DOCX = bytes.fromhex("504b0304") + b"0" * 900


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta EVU", nombre_comercial="EVU RH", estado="Activa", correo_comunicacion="rh@evu.mx")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    cliente = Cliente(cuenta_id=cuenta.id, nombre="Tiendas Sol", nombre_comercial="Sol Retail", estado="Activo")
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
    rh = Usuario(correo="rh.sinmedico@evu.mx", nombre="RH Sin Permiso", rol="Usuario", activo=True, hash_pass="x")
    db.add(rh)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=rh.id, cuenta_id=cuenta.id))
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    p = persona.postulaciones_activas[-1]
    P = p.codigo
    p.etapa = "Entrevista IA"
    db.commit()

    def etapa():
        db.expire_all()
        return db.get(Postulacion, p.id).etapa

    # ---------------- 1. Pantalla única ----------------
    print("\n--- 1. Agregar evaluación ---")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "externo", "nombre": "Ana Externa"},
    })
    check(r.status_code == 400 and "correo o WhatsApp" in r.json()["detail"], "evaluador externo sin correo ni WhatsApp → «Captura correo o WhatsApp para enviarle la solicitud»")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "entrevista_humana", "forma": "asignada"})
    check(r.status_code == 400, "«Asignar a una persona» exige evaluador")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={
        "tipo": "entrevista_humana", "forma": "asignada", "evaluador": {"tipo": "externo", "nombre": "Ana Externa", "whatsapp": "3399990000", "correo": "ana@externa.mx"},
        "instrucciones": "Trae tu portafolio",
        "cita": {"fecha": "2026-10-05", "hora": "07:29", "modalidad": "Presencial", "direccion": "Av. Reforma 1"},
    })
    check(r.status_code == 201, f"entrevista humana asignada con cita → 201 ({r.status_code} {r.text[:200]})")
    E1 = r.json()["evaluacion"]
    check(E1["codigo"].startswith("EVA-5") and E1["estado"] == "pendiente" and E1["cita"]["fechaHora"] == "2026-10-05T13:29:00Z",
          "código EVA-5xxxx, Pendiente, cita 07:29 de CDMX guardada como 13:29Z")
    check(E1["cita"]["direccion"] == "Av. Reforma 1" and E1["instrucciones"] == "Trae tu portafolio", "dirección e instrucciones en campos separados")
    check(etapa() == "Entrevista Humana", "asignar una entrevista humana lleva la tarjeta a «Entrevista Humana» (comportamiento actual)")
    check(db.query(ClienteContacto).filter_by(cliente_id=cliente.id, correo="ana@externa.mx").count() == 1, "«+ Nuevo evaluador» quedó como contacto reutilizable del Cliente")
    destinos = {(c, d) for c, d, _ in ENVIOS}
    check({("whatsapp", persona.telefono), ("correo", "cand@correo.mx"), ("whatsapp", "3399990000"), ("correo", "ana@externa.mx")} <= destinos,
          "con cita: aviso a candidato y evaluador por correo y WhatsApp")
    aviso_eval = next(t for c, d, t in ENVIOS if d == "3399990000")
    check("/evaluacion/" in aviso_eval and "(hora de Ciudad de México)" in aviso_eval and "Av. Reforma 1" in aviso_eval,
          "el mensaje al evaluador trae su liga de Red Human, hora con zona y la dirección")
    token1 = db.query(Evaluacion).filter_by(codigo=E1["codigo"]).one().token_evaluador

    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "otra", "forma": "registro_directo"})
    check(r.status_code == 400 and "nombre" in r.json()["detail"], "con «Otra» el nombre es obligatorio")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "socioeconomica", "forma": "registro_directo"})
    check(r.status_code == 201 and r.json()["evaluacion"]["evaluador"] is None and not ENVIOS, "registro directo: sin evaluador y sin avisos")
    E2 = r.json()["evaluacion"]["codigo"]
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "referencias", "forma": "liga_otro_sistema", "liga_externa_candidato": "otra.cosa"})
    check(r.status_code == 400, "liga de otro sistema: exige https://")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "referencias", "forma": "liga_otro_sistema", "liga_externa_candidato": "https://otro.sistema/ref"})
    check(r.status_code == 201 and any("https://otro.sistema/ref" in t for c, d, t in ENVIOS if d == persona.telefono),
          "liga de otro sistema sin cita: el candidato recibe la liga")
    E3 = r.json()["evaluacion"]["codigo"]

    # ---------------- 2. Formulario único ----------------
    print("\n--- 2. Formulario de resultado ---")
    etapa_antes = etapa()
    r = client.post(f"/evaluaciones/publica/{token1}/resultado", data={"comentarios": "Muy bien"})
    check(r.status_code == 400 and "conclusión" in r.json()["detail"], "entrevista humana: la conclusión es obligatoria")
    r = client.post(f"/evaluaciones/publica/{token1}/resultado", data={"conclusion": "favorable"})
    check(r.status_code == 400, "entrevista humana: «Favorable» no es una conclusión válida")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/publica/{token1}/resultado", data={"conclusion": "avanzar", "comentarios": "Buen perfil", "version": "0"},
                    files=[("archivos", ("notas.pdf", PDF, "application/pdf")), ("archivos", ("guia.docx", DOCX, "application/octet-stream"))])
    check(r.status_code == 200 and r.json()["evaluacion"]["estado"] == "con_resultado", f"el evaluador registra por su liga → Con resultado ({r.status_code} {r.text[:200]})")
    check(etapa() == etapa_antes, "registrar el resultado NO movió la etapa")
    check(db.query(NotificacionEnviada).filter_by(evento="evaluacion_resultado", destinatario_tipo="rh", enviado=True).count() == 1 and not any(d in (persona.telefono, "cand@correo.mx") for c, d, t in ENVIOS),
          "aviso a RH; el candidato NO recibe «avanzas» ni nada por un resultado")
    ev1 = db.query(Evaluacion).filter_by(codigo=E1["codigo"]).one()
    db.refresh(ev1)
    check(ev1.realizada_por == "Ana Externa" and ev1.registrada_via == "liga_evaluador" and len(ev1.adjuntos) == 2, "autor precargado con el evaluador; captura vía liga; PDF + Word adjuntos")
    lista = client.get(f"/evaluaciones/postulaciones/{P}").json()
    tarjeta = next(x for x in lista if x["codigo"] == E1["codigo"])
    check(tarjeta["nuevoResultado"] and tarjeta["conclusionTexto"] == "Avanzar" and tarjeta["acciones"]["principal"] == "ver_resultado",
          "tarjeta: «Nuevo resultado», una sola etiqueta de conclusión, botón «Ver resultado»")
    det = client.get(f"/evaluaciones/{E1['codigo']}").json()
    check(not det["evaluacion"]["nuevoResultado"] and [e["accion"] for e in det["eventos"]][:1] == ["creada"], "abrir el detalle quita «Nuevo resultado»; historial con la creación")
    r = client.get(f"/evaluaciones/{E1['codigo']}/adjuntos/{det['evaluacion']['adjuntos'][0]['id']}")
    check(r.status_code == 200 and r.content.startswith(b"%PDF"), "RH descarga el adjunto")

    v = det["evaluacion"]["resultadoVersion"]
    r = client.post(f"/evaluaciones/{E1['codigo']}/resultado", data={"conclusion": "no_avanzar", "comentarios": "Corrección", "version": str(v - 1), "modo": "corregir"})
    check(r.status_code == 409 and "cambió mientras lo editabas" in r.json()["detail"], "guardado con versión vieja → «Este resultado cambió mientras lo editabas»")
    r = client.post(f"/evaluaciones/{E1['codigo']}/resultado", data={"conclusion": "requiere_otra_entrevista", "comentarios": "Falta validar inglés", "version": str(v), "modo": "corregir"})
    check(r.status_code == 200 and r.json()["evaluacion"]["conclusion"] == "requiere_otra_entrevista", "RH corrige el resultado")
    ev_corr = db.query(EventoEvaluacion).filter_by(evaluacion_id=ev1.id, accion="resultado_corregido").one()
    check(ev_corr.anteriores.get("conclusion") == "avanzar", "la versión anterior queda en el historial")
    check("programar_otra" in r.json()["evaluacion"]["acciones"]["menu"], "«Requiere otra entrevista» ofrece «Programar otra entrevista» (no la crea sola)")
    check(db.query(Evaluacion).filter_by(postulacion_id=p.id, tipo="entrevista_humana").count() == 1, "no se creó otra entrevista automáticamente")
    pub = client.get(f"/evaluaciones/publica/{token1}").json()
    check(pub["yaTieneResultado"] and pub["avisoResultadoRh"].startswith("RH ya registró un resultado el"), "liga: el evaluador ve «RH ya registró un resultado el [fecha]»")
    v = client.get(f"/evaluaciones/{E1['codigo']}").json()["evaluacion"]["resultadoVersion"]
    r = client.post(f"/evaluaciones/publica/{token1}/resultado", data={"conclusion": "avanzar", "comentarios": "Complemento del evaluador", "version": str(v)})
    ev1 = db.query(Evaluacion).filter_by(codigo=E1["codigo"]).one()
    db.refresh(ev1)
    check(r.status_code == 200 and r.json()["accion"] == "resultado_complementado" and ev1.conclusion == "requiere_otra_entrevista"
          and "Complemento del evaluador" in ev1.comentarios, "liga con resultado previo: solo complementa (la conclusión de RH no se sobrescribe)")

    r = client.post(f"/evaluaciones/{E2}/resultado", files=[("archivos", ("estudio.pdf", PDF, "application/pdf"))], data={"version": "0"})
    ev2 = db.query(Evaluacion).filter_by(codigo=E2).one()
    db.refresh(ev2)
    check(r.status_code == 200 and ev2.estado == "con_resultado" and ev2.realizada_por == "" and ev2.registrada_por == admin.nombre and not ev2.conclusion,
          "socioeconómico cerrado solo con el PDF; «Realizada por» vacío (No especificado); captura = RH")
    check(r.json()["evaluacion"]["sinConclusion"], "tarjeta: «Resultado recibido · Sin conclusión»")
    r = client.post(f"/evaluaciones/{E3}/resultado", data={"conclusion": "apto", "version": "0"})
    check(r.status_code == 400, "referencias: «Apto» no es válida (solo Favorable / Con observaciones / Desfavorable)")
    r = client.post(f"/evaluaciones/{E3}/resultado", data={"conclusion": "con_observaciones", "version": "0"})
    check(r.status_code == 200 and r.json()["evaluacion"]["conclusionTexto"] == "Con observaciones", "referencias: Con observaciones")

    # ---------------- 3. Estados y consentimiento ----------------
    print("\n--- 3. Estados y consentimiento médico ---")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "medica", "forma": "asignada",
                                                             "evaluador": {"tipo": "externo", "nombre": "Dra. Pérez", "correo": "dra@clinica.mx"}})
    M = r.json()["evaluacion"]
    check(r.status_code == 201 and M["estado"] == "pendiente" and M["consentimiento"] == "pendiente" and M["consentimientoTexto"] == "En espera de consentimiento",
          "médica: Pendiente + condición «En espera de consentimiento» (no es un estado)")
    check(not any(d == "dra@clinica.mx" for c, d, t in ENVIOS), "sin consentimiento NO sale la liga al evaluador")
    tok_m = db.query(Evaluacion).filter_by(codigo=M["codigo"]).one().token_evaluador
    r = client.post(f"/evaluaciones/{M['codigo']}/resultado", data={"conclusion": "apto", "version": "0"})
    check(r.status_code == 409 and "consentimiento" in r.json()["detail"], "sin consentimiento no se guarda resultado médico, tampoco desde el sistema")
    check(client.get(f"/evaluaciones/publica/{tok_m}").json()["enEsperaConsentimiento"], "liga del evaluador: en espera de consentimiento")
    tok_c = db.query(Evaluacion).filter_by(codigo=M["codigo"]).one().consentimiento_token
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/publica/consentimiento/{tok_c}/aceptar", json={"nombre": "Carlos Hernández Ruiz", "acepto": True})
    check(r.status_code == 200 and any(d == "dra@clinica.mx" for c, d, t in ENVIOS), "al otorgar el consentimiento sale la liga al evaluador")
    app.dependency_overrides[usuario_actual] = lambda: rh
    app.dependency_overrides[usuario_decisor] = lambda: rh
    r = client.post(f"/evaluaciones/{M['codigo']}/resultado", data={"conclusion": "apto", "version": "0"})
    check(r.status_code == 403, "sin permiso de informes médicos RH no captura el resultado médico")
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[usuario_decisor] = lambda: admin
    r = client.post(f"/evaluaciones/publica/{tok_m}/resultado", data={"conclusion": "favorable", "version": "0"})
    check(r.status_code == 400, "médica: «Favorable» no es válida")
    r = client.post(f"/evaluaciones/publica/{tok_m}/resultado", data={"conclusion": "apto_con_restricciones", "comentarios": "Sin cargas > 10 kg", "version": "0"})
    check(r.status_code == 200 and r.json()["evaluacion"]["conclusionTexto"] == "Apto con restricciones", "médica: Apto con restricciones desde la liga")
    app.dependency_overrides[usuario_actual] = lambda: rh
    lista_rh = client.get(f"/evaluaciones/postulaciones/{P}").json()
    med_rh = next(x for x in lista_rh if x["codigo"] == M["codigo"])
    check(med_rh["restringido"] and med_rh["comentarios"] == "" and med_rh["conclusionTexto"] == "Apto con restricciones",
          "sin permiso: ve estado y conclusión, no el detalle médico")
    app.dependency_overrides[usuario_actual] = lambda: admin

    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "medica", "forma": "registro_directo"})
    M2 = r.json()["evaluacion"]["codigo"]
    tok_c2 = db.query(Evaluacion).filter_by(codigo=M2).one().consentimiento_token
    client.post(f"/evaluaciones/publica/consentimiento/{tok_c2}/rechazar")
    r = client.get(f"/evaluaciones/postulaciones/{P}").json()
    m2 = next(x for x in r if x["codigo"] == M2)
    check(m2["consentimiento"] == "rechazado" and m2["acciones"]["menu"] == ["cancelar"] and m2["acciones"]["principal"] is None,
          "consentimiento rechazado: la única acción es Cancelar")

    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "tecnica", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
                                                             "cita": {"fecha": "2026-10-07", "hora": "10:00", "modalidad": "Teléfono", "telefono": "5512345678"}})
    T = r.json()["evaluacion"]["codigo"]
    r = client.post(f"/evaluaciones/{T}/realizada")
    check(r.status_code == 200 and r.json()["evaluacion"]["estado"] == "realizada_sin_resultado", "«Marcar como realizada» → Realizada · Resultado pendiente")
    r = client.post(f"/evaluaciones/{T}/no-realizada", json={"motivo": "x"})
    check(r.status_code == 409, "Realizada · Resultado pendiente no pasa a No realizada")
    r = client.post(f"/evaluaciones/postulaciones/{P}", json={"tipo": "tecnica", "forma": "asignada", "evaluador": {"tipo": "interno", "usuario_id": admin.id},
                                                             "cita": {"fecha": "2026-10-08", "hora": "10:00", "modalidad": "Videollamada", "liga_videollamada": "https://meet.example/x"}})
    T2 = r.json()["evaluacion"]["codigo"]
    r = client.post(f"/evaluaciones/{T2}/no-realizada", json={"motivo": "El candidato no se presentó"})
    check(r.status_code == 200 and r.json()["evaluacion"]["acciones"]["principal"] == "reprogramar", "No realizada → botón «Reprogramar»")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/{T2}/reprogramar", json={"cita": {"fecha": "2026-10-09", "hora": "12:00", "modalidad": "Videollamada", "liga_videollamada": "https://meet.example/y"}})
    check(r.status_code == 200 and r.json()["evaluacion"]["estado"] == "pendiente" and r.json()["evaluacion"]["cita"]["fechaHora"] == "2026-10-09T18:00:00Z",
          "Reprogramar → Pendiente con la cita nueva")
    check(any(d == persona.telefono for c, d, t in ENVIOS), "reprogramar avisa al candidato")
    tok_t2 = db.query(Evaluacion).filter_by(codigo=T2).one().token_evaluador
    r = client.post(f"/evaluaciones/{T2}/cancelar", json={"motivo": "Ya no aplica"})
    check(r.status_code == 200 and r.json()["evaluacion"]["estado"] == "cancelada", "Cancelar → Cancelada (estado final)")
    pub = client.get(f"/evaluaciones/publica/{tok_t2}").json()
    r = client.post(f"/evaluaciones/publica/{tok_t2}/resultado", data={"comentarios": "tarde", "version": "0"})
    check(pub["cancelada"] and r.status_code == 409 and "cancelada" in r.json()["detail"], "la liga de una evaluación cancelada ya no acepta resultados")
    r = client.post(f"/evaluaciones/{T2}/reprogramar", json={"cita": {"fecha": "2026-10-10", "hora": "12:00", "modalidad": "Teléfono"}})
    check(r.status_code == 409, "una Cancelada no se reabre")
    ENVIOS.clear()
    r = client.post(f"/evaluaciones/{T}/recordatorio", json={"a": "evaluador"})
    check(r.status_code == 200 and all(d != persona.telefono for c, d, t in ENVIOS), "recordatorio solo al evaluador (según elección)")

    # ---------------- 4. Etapa y bitácora ----------------
    print("\n--- 4. Reglas de proceso ---")
    check(etapa() == "Entrevista Humana", "después de todos los resultados la etapa sigue igual (nada envía a Contratación)")
    evs_hist = db.query(EventoEvaluacion).count()
    check(evs_hist >= 20, f"historial append-only con {evs_hist} eventos")
    check(db.query(NotificacionEnviada).filter_by(evento="candidato_apto").count() == 0, "ningún aviso «candidato_apto» por resultados de evaluación")

    # ---------------- 5. Psicométricas.mx por webhook ----------------
    print("\n--- 5. Proveedor integrado ---")
    from app.routers import webhooks_proveedores as wp
    from app.services import psicometricas as psi

    ev_psi = db.query(Evaluacion).filter_by(codigo=E3).one()
    nueva = Evaluacion(codigo="EVA-PSI", cuenta_id=cuenta.id, postulacion_id=p.id, candidato_id=p.candidato_id, tipo="psicometrica",
                       forma="integrada", proveedor="Psicométricas.mx", clave_proveedor="CLV-9", paso_integrada="enviada",
                       token_evaluador="tok-psi-9", estado="pendiente")
    db.add(nueva)
    db.commit()
    orig = (psi.consultar_candidato, psi.terminado, psi.resultado_json, psi.resultado_pdf)
    psi.consultar_candidato = lambda clave: [{"fecha_fin": "2026-09-29"}]
    psi.terminado = lambda filas: True
    psi.resultado_json = lambda clave: {"perfil": "D"}
    psi.resultado_pdf = lambda clave: PDF
    wp._procesar_psicometricas("CLV-9", "termina_prueba", {})
    psi.consultar_candidato, psi.terminado, psi.resultado_json, psi.resultado_pdf = orig
    db.expire_all()
    nueva = db.query(Evaluacion).filter_by(codigo="EVA-PSI").one()
    check(nueva.estado == "con_resultado" and nueva.registrada_via == "proveedor" and nueva.realizada_por == "Psicométricas.mx"
          and nueva.resultado_json == {"perfil": "D"} and len(nueva.adjuntos) == 1, "webhook de Psicométricas.mx → Con resultado (JSON + PDF, autor = proveedor)")
    check(etapa() == "Entrevista Humana", "el resultado del proveedor tampoco mueve la etapa")
    db.close()

print(f"\n{OK} verificaciones OK")
