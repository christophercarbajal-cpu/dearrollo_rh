"""Verificación del PROCESO CONFIGURABLE y seguimiento de candidatos (2026-10-06).

Los tres procesos de validación se arman con los MISMOS componentes (plantillas de la Cuenta → vacante → postulación):
1) Operativo mínimo: Prefiltro por WhatsApp → documentos → contratación. Las etapas sin pasos no bloquean (el avance
   automático del Prefiltro salta Filtro Red Human y Filtro humano, vacías, directo a Contratación); «Falta …» exacto.
2) Administrativo completo: prefiltro web → Entrevista Red Human (calificación mínima, enfoque del paso) → psicometría →
   entrevista humana (guion del tipo «con jefe directo») → referencias (validación de RH) → contratación → onboarding.
   Estado ≠ resultado («Completada · No favorable» bloquea sin descartar).
3) Paralelo: médica y socioeconómica disponibles a la vez y obligatorias antes de Contratación; el consentimiento médico
   bloquea la captura sin frenar la socioeconómica; plazo vencido = solo alerta; avance automático de Filtro humano.
Además: versionado (plantilla → vacante → candidato), validación de dependencias, omitir obligatorio con justificación +
autorización (403 sin permiso), «Aplicar versión vigente» sin cancelar lo que está en curso.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_proceso_configurable.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_proc_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "proc.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY",
          "TEAMS_CLIENT_ID", "TEAMS_TENANT_ID", "TEAMS_CLIENT_SECRET"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-proc"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual, usuario_decisor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Bitacora, Candidato, ConfiguracionSistema, Cuenta, Entrevista, Evaluacion, Mensaje, PlantillaProceso, Postulacion, Usuario,
    UsuarioCuenta, Vacante,
)
from app.routers import candidatos as rcand  # noqa: E402
from app.services import notificaciones as sn  # noqa: E402
from app.services import proceso as sproc  # noqa: E402

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


async def _envio(*a, **k):
    return {"enviado": True, "proveedor": "prueba", "detalle": "ok"}

sn.enviar_mensaje = _envio
sn.enviar_correo = _envio
import app.services.correo as _scorreo  # noqa: E402
import app.services.whatsapp as _swa  # noqa: E402

_scorreo.enviar_correo = _envio
_swa.enviar_mensaje = _envio


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta Procesos", nombre_comercial="Procesos RH", estado="Activa", correo_comunicacion="rh@proc.mx")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    operadora = Usuario(correo="operadora@proc.mx", nombre="Olivia Operadora", rol="Usuario", hash_pass="x")
    db.add(operadora)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=operadora.id, cuenta_id=cuenta.id))
    cfg = db.query(ConfiguracionSistema).first()
    if cfg:
        cfg.modo_prueba = False  # las compuertas se prueban con Modo Prueba APAGADO
    db.commit()
    actual = {"u": admin}
    app.dependency_overrides[usuario_actual] = lambda: actual["u"]
    app.dependency_overrides[usuario_decisor] = lambda: actual["u"]
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    tel = iter(range(5541000001, 5541000200))

    def post(codigo):
        db.expire_all()
        return db.query(Postulacion).filter_by(codigo=codigo).one()

    def nueva(nombre, vac):
        r = client.post("/candidatos", json={"nombre": nombre, "telefono": str(next(tel)), "correo": f"{nombre.split()[0].lower()}@proc.mx",
                                             "vacante": vac, "consentimiento": True, "fuente": "RH"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def vacante(titulo, proceso=None):
        cuerpo = {"titulo": titulo, "generar_si_falta": False}
        if proceso is not None:
            cuerpo["proceso"] = proceso
        r = client.post("/vacantes", json=cuerpo)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def seg(codigo):
        r = client.get(f"/procesos/postulaciones/{codigo}")
        assert r.status_code == 200, r.text
        return r.json()

    def paso(s, pid):
        return next(x for e in s["etapas"] for x in e["pasos"] if x["id"] == pid)

    def etapa_cfg(s, etapa):
        return next(e for e in s["etapas"] if e["etapa"] == etapa)

    def evaluar(codigo, tipo, paso_id="", forma="registro_directo"):
        r = client.post(f"/evaluaciones/postulaciones/{codigo}", json={"tipo": tipo, "forma": forma, "paso_id": paso_id})
        assert r.status_code == 201, r.text
        return r.json()

    def resultado(eva, conclusion):
        ver = client.get(f"/evaluaciones/{eva}").json()["evaluacion"]["resultadoVersion"]
        return client.post(f"/evaluaciones/{eva}/resultado", data={"conclusion": conclusion, "comentarios": "ok", "version": str(ver)})

    # ================= 0. Plantillas, vacante y versionado =================
    print("\n--- 0. Plantillas de la Cuenta → vacante → candidato (versionado) ---")
    op = client.get("/procesos/opciones").json()
    check([e["valor"] for e in op["etapas"]] == ["Prefiltro", "Entrevista IA", "Entrevista Humana", "Contratación", "Onboarding"]
          and [e["texto"] for e in op["etapas"]][1:3] == ["Filtro Red Human", "Filtro humano"], "las 5 etapas siguen fijas")
    check({"pendiente", "en_curso", "completada", "omitida", "cancelada"} == {x["valor"] for x in op["estados"]}
          and {"favorable", "con_observaciones", "no_favorable"} == {x["valor"] for x in op["resultados"]},
          "estado (5) y resultado (3) son catálogos independientes")
    plantillas = {}
    for clave in ("operativo_minimo", "administrativo_completo", "paralelo_medica_socioeconomica"):
        r = client.post(f"/procesos/plantillas/ejemplo/{clave}")
        check(r.status_code == 201 and r.json()["version"] == 1, f"plantilla «{r.json().get('nombre')}» creada desde ejemplo (v1)")
        plantillas[clave] = r.json()
    r = client.post("/procesos/plantillas", json={"nombre": "Mal", "pasos": [
        {"id": "a", "tipo": "medica", "etapa": "Entrevista Humana", "depende_de": ["b"]}, {"id": "b", "tipo": "condiciones"}]})
    check(r.status_code == 400 and "etapa posterior" in r.json()["detail"], "dependencia hacia una etapa posterior → 400")
    r = client.post("/procesos/plantillas", json={"nombre": "Ciclo", "pasos": [
        {"id": "a", "tipo": "medica", "depende_de": ["b"]}, {"id": "b", "tipo": "tecnica", "depende_de": ["a"]}]})
    check(r.status_code == 400 and "ciclo" in r.json()["detail"], "dependencias en ciclo → 400")
    r = client.post("/procesos/plantillas", json={"nombre": "Etapa", "pasos": [{"tipo": "alta", "etapa": "Prefiltro"}]})
    check(r.status_code == 400 and "no puede ir" in r.json()["detail"], "un paso en una etapa no permitida → 400")

    V1 = vacante("Operador de almacén", {"plantilla_id": plantillas["operativo_minimo"]["id"]})
    pv = client.get(f"/procesos/vacantes/{V1}").json()["proceso"]
    check(pv["plantilla_id"] == plantillas["operativo_minimo"]["id"] and pv["version"] == 1 and not pv["personalizado"],
          "el formulario de vacante asocia una plantilla (copia v1)")
    r = client.patch(f"/procesos/plantillas/{plantillas['operativo_minimo']['id']}",
                     json={"pasos": plantillas["operativo_minimo"]["pasos"] + [{"id": "tecnica", "tipo": "tecnica", "obligatorio": False}]})
    check(r.status_code == 200 and r.json()["version"] == 2, "editar la plantilla sube su versión")
    pv = client.get(f"/procesos/vacantes/{V1}").json()["proceso"]
    check(len(pv["pasos"]) == 3 and pv["plantilla_version"] == 1, "…y NO toca a la vacante que ya la copió")
    client.patch(f"/procesos/plantillas/{plantillas['operativo_minimo']['id']}", json={"pasos": plantillas["operativo_minimo"]["pasos"]})

    # ================= 1. Operativo mínimo =================
    print("\n--- 1. Operativo mínimo: Prefiltro WhatsApp → documentos → contratación ---")
    P1 = nueva("Pedro Operativo", V1)
    p1 = post(P1)
    check(sproc.tiene_proceso(p1) and p1.proceso["vacante_version"] == 1, "la postulación congela la versión vigente del proceso")
    s = seg(P1)
    check(etapa_cfg(s, "Entrevista IA")["sinPasos"] and etapa_cfg(s, "Entrevista Humana")["sinPasos"] and s["siguienteEtapa"] == "Contratación",
          "Filtro Red Human y Filtro humano sin pasos: la siguiente etapa es Contratación")
    check(paso(s, "prefiltro")["estado"] == "pendiente" and paso(s, "prefiltro")["responsable"] == "Red Human",
          "seguimiento: nombre, responsable (Red Human) y estado Pendiente del prefiltro")
    check(s["siguienteAccion"]["tipo"] in ("esperar", "paso"), "la siguiente acción arriba: esperar al candidato")
    # personalizar la vacante → versión 2; el candidato ya creado conserva la suya
    r = client.patch(f"/vacantes/{V1}", json={"proceso": {"pasos": pv["pasos"] + [{"id": "referencias", "tipo": "referencias", "obligatorio": False,
                                                                                    "etapa": "Entrevista Humana"}]}})
    check(r.status_code == 200 and r.json()["proceso"]["version"] == 2 and r.json()["proceso"]["personalizado"],
          "personalizar el proceso en la vacante sube su versión (personalizado, sin tocar la plantilla)")
    check(len(client.get("/procesos/plantillas").json()[0]["pasos"]) >= 2 and len(post(P1).proceso["pasos"]) == 3,
          "el candidato existente NO cambia (sigue con 3 pasos de la v1)")
    check(seg(P1)["desactualizado"] is True, "la ficha avisa que hay una versión más nueva del proceso")
    client.patch(f"/vacantes/{V1}", json={"proceso": {"pasos": pv["pasos"]}})  # v3 = sin referencias (para los siguientes)

    # compuerta: avanzar a Contratación sin el prefiltro (obligatorio) → 409; omitir requiere justificación + permiso
    P1b = nueva("Paula Pendiente", V1)
    r = client.patch(f"/candidatos/{P1b}/etapa", json={"etapa": "Contratación"})
    check(r.status_code == 409 and "Prefiltro WhatsApp" in r.json()["detail"], "avanzar con un obligatorio sin cumplir → 409 con lo que falta")
    r = client.patch(f"/candidatos/{P1b}/etapa", json={"etapa": "Contratación", "omitir_obligatorios": True, "comentario": "corto"})
    check(r.status_code == 400 and "justificación" in r.json()["detail"], "omitir un obligatorio exige justificación (≥10 caracteres)")
    actual["u"] = operadora
    r = client.patch(f"/candidatos/{P1b}/etapa", json={"etapa": "Contratación", "omitir_obligatorios": True,
                                                       "comentario": "Ya lo validamos por teléfono con la candidata"})
    check(r.status_code == 403 and "autorización" in r.json()["detail"], "sin el permiso «Autorizar omisiones» → 403")
    actual["u"] = admin
    r = client.patch(f"/auth/usuarios/{operadora.id}", json={"autoriza_omisiones": True})
    check(r.status_code == 200, "un Administrador concede el permiso «Autorizar omisiones»")
    actual["u"] = db.query(Usuario).get(operadora.id)
    db.refresh(actual["u"])
    r = client.patch(f"/candidatos/{P1b}/etapa", json={"etapa": "Contratación", "omitir_obligatorios": True,
                                                       "comentario": "Ya lo validamos por teléfono con la candidata"})
    actual["u"] = admin
    s = seg(P1b)
    check(r.status_code == 200 and post(P1b).etapa == "Contratación" and paso(s, "prefiltro")["estado"] == "omitida"
          and paso(s, "prefiltro")["decision"]["autorizado_por"] == "Olivia Operadora",
          "con justificación y autorización avanza; el paso queda «Omitida» con quién lo autorizó")
    check(any(h.get("evento") == "paso_omitida" for h in post(P1b).historial), "la omisión queda en el historial del candidato")

    # Zero-Touch: termina el prefiltro «cumple» → avance automático del Prefiltro, salta las etapas vacías
    p1 = post(P1)
    p1.prefiltro_completo = True
    db.commit()
    asyncio.run(rcand._auto_decision_zero_touch(db, p1, "cumple"))
    db.commit()
    p1 = post(P1)
    check(p1.etapa == "Contratación" and p1.expediente is not None, "prefiltro cumple + avance automático → Contratación (las etapas vacías no bloquean)")
    check(any(h.get("evento") == "avance_automatico" for h in p1.historial), "el avance automático queda en el historial")
    textos = [m.texto for m in db.query(Mensaje).filter(Mensaje.postulacion_id == p1.id).all()]
    check(not any("videollamada" in t for t in textos), "sin Entrevista Red Human en el proceso no se invita a agendar videollamada")
    s = seg(P1)
    docs = paso(s, "documentos")
    check(docs["espera"].startswith("Falta ") and "comprobante de domicilio" in docs["espera"],
          f"documentos en espera dicen exactamente qué falta → «{docs['espera'][:70]}…»")
    cond = paso(s, "condiciones")
    check(cond["disponible"] is False and "Falta completar: Solicitud y revisión de documentos" in cond["espera"], "condiciones depende de documentos (dependencia explícita)")
    r = client.patch(f"/candidatos/{P1}/condiciones-contratacion", json={"puesto": "Operador", "sueldo": "$12,000 mensuales",
                                                                          "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-11-02"})
    check(r.status_code == 200, "una dependencia no bloquea capturar las condiciones (no frena actividades en paralelo)")
    # 2026-10-10 (Cambio 3): «Propuesta y aceptación» se cumple con la aceptación del candidato (aquí la registra RH)
    client.post(f"/candidatos/{P1}/propuesta", json={"puesto": "Operador", "sueldo": "$12,000 mensuales",
                                                     "tipo_contratacion": "Tiempo indeterminado", "fecha_ingreso": "2026-11-02"})
    client.post(f"/candidatos/{P1}/propuesta/respuesta", json={"acepta": True})
    exp_id = post(P1).expediente.id
    r = client.post(f"/onboarding/expedientes/{exp_id}/iniciar", json={"documentos": [{"tipo": "CURP", "obligatorio": True}]})
    check(r.status_code == 409 and "documentos" in r.json()["detail"], "«Iniciar Onboarding» respeta los obligatorios de Contratación")
    for d in post(P1).expediente.documentos:
        rr = client.post(f"/contratacion/expedientes/{exp_id}/documentos/estado", json={"tipo": d.tipo, "estado": "recibido", "recibido_fisico": True})
        assert rr.status_code == 200, rr.text
    s = seg(P1)
    check(paso(s, "documentos")["estado"] == "completada" and paso(s, "documentos")["revisadoPor"].startswith("Revisado por:"),
          "documentos aprobados por RH → Completada · «Revisado por: …»")
    check(paso(s, "condiciones")["estado"] == "completada" and s["listaParaAvanzar"] and s["siguienteAccion"]["texto"] == "Enviar a Onboarding",
          "Contratación lista: la siguiente acción es «Enviar a Onboarding»")

    # ================= 2. Administrativo completo =================
    print("\n--- 2. Administrativo completo ---")
    V2 = vacante("Analista administrativo", {"plantilla_id": plantillas["administrativo_completo"]["id"]})
    pasos2 = client.get(f"/procesos/vacantes/{V2}").json()["proceso"]["pasos"]
    for x in pasos2:
        if x["id"] == "entrevista_agente":
            x["tipo_entrevista"] = "profesional_personal"
    client.put(f"/procesos/vacantes/{V2}", json={"pasos": pasos2})
    P2 = nueva("Ana Administrativa", V2)
    p2 = post(P2)
    check(sproc.enfoque_entrevista_agente(p2, p2.vacante) == "profesional_personal" and p2.vacante.enfoque_entrevista == "profesional",
          "la Entrevista Red Human usa el enfoque configurado en su paso (no el de la vacante)")
    p2.analisis = {**(p2.analisis or {}), "respuestas_web": [{"pregunta": "¿Tienes licenciatura?", "respuesta": "Sí"}]}
    p2.estado, p2.prefiltro_completo = "cumple", True
    db.commit()
    s = seg(P2)
    check(paso(s, "prefiltro_web")["estado"] == "completada" and paso(s, "prefiltro_web")["resultado"] == "favorable"
          and s["siguienteAccion"]["tipo"] == "avanzar" and s["siguienteAccion"]["etapa"] == "Entrevista IA",
          "prefiltro web completo; sin avance automático la acción principal es «Avanzar a Filtro Red Human»")
    r = client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Entrevista IA"})
    check(r.status_code == 200 and post(P2).etapa == "Entrevista IA", "RH avanza a Filtro Red Human")
    check(db.query(Bitacora).filter(Bitacora.accion == "entrevista_ia_forzada", Bitacora.entidad_id == P2).count() == 1,
          "con Entrevista Red Human en el proceso se invita al candidato a agendarla")
    p2 = post(P2)
    db.add(Entrevista(codigo="ENT-P2", candidato_id=p2.candidato_id, postulacion_id=p2.id, token="tok-p2", estado="evaluada",
                      evaluacion={"match_perfil": 82, "score_entrevista": 82, "recomendacion": "Avanzar"}, finalizada_en=datetime.now(timezone.utc)))
    db.commit()
    s = seg(P2)
    check(paso(s, "entrevista_agente")["estado"] == "completada" and paso(s, "entrevista_agente")["cumpleRegla"]
          and "82/100" in paso(s, "entrevista_agente")["detalle"], "Entrevista Red Human 82 ≥ mínimo 70 → cumple su condición")
    r = client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Entrevista Humana"})
    check(r.status_code == 200 and post(P2).etapa == "Entrevista Humana", "con el proceso cumplido avanza a Filtro humano (sin exigir la entrevista agregada)")
    s = seg(P2)
    eh = paso(s, "entrevista_humana")
    check(paso(s, "psicometria")["disponible"] and eh["disponible"] is False and "Falta completar: Psicometría digital" in eh["espera"]
          and eh["accion"] is None, "la entrevista humana espera a la psicometría (dependencia) y no ofrece «Iniciar»")
    check(paso(s, "psicometria")["accion"]["clave"] == "iniciar_evaluacion" and s["siguienteAccion"]["paso"] == "psicometria",
          "la siguiente acción principal es «Iniciar: Psicométrica» (reutiliza «Agregar evaluación»)")
    e_psi = evaluar(P2, "psicometrica", "psicometria")["evaluacion"]
    check(e_psi["pasoId"] == "psicometria", "la evaluación creada desde el paso queda ligada a él")
    s = seg(P2)
    check(paso(s, "psicometria")["estado"] == "en_curso" and paso(s, "psicometria")["espera"].startswith("Falta el resultado"),
          "psicometría En curso: «Falta el resultado»")
    check(resultado(e_psi["codigo"], "favorable").status_code == 200, "resultado favorable de la psicometría")
    r = evaluar(P2, "entrevista_humana")
    e_eh = r["evaluacion"]
    check(e_eh["pasoId"] == "entrevista_humana", "una entrevista humana agregada sin paso se liga sola al paso de ese tipo")
    check(e_eh["guion"].get("tipo") == "jefe_directo" and len(e_eh["guion"].get("preguntas") or []) >= 5,
          "el tipo de entrevista del paso («Con jefe directo») genera su guion específico")
    resultado(e_eh["codigo"], "avanzar")
    e_ref = evaluar(P2, "referencias", "referencias")["evaluacion"]
    resultado(e_ref["codigo"], "favorable")
    s = seg(P2)
    ref = paso(s, "referencias")
    check(ref["estado"] == "completada" and ref["resultado"] == "favorable" and not ref["cumpleRegla"] and ref["espera"] == "Falta la revisión de RH",
          "referencias: Completada · Favorable pero su regla es «Validación de RH» → falta revisión")
    check(not s["listaParaAvanzar"], "Filtro humano aún no está listo (falta validar referencias)")
    r = client.post(f"/evaluaciones/{e_ref['codigo']}/revisar", json={"conclusion": "favorable", "comentario": "Verificadas"})
    s = seg(P2)
    check(r.status_code == 200 and paso(s, "referencias")["cumpleRegla"] and paso(s, "referencias")["revisadoPor"] == f"Revisado por: {admin.nombre}",
          "revisada por RH → cumple; «Revisado por: [nombre]»")
    check(post(P2).etapa == "Contratación", "avance automático en TODAS las Cuentas (2026-10-09): Filtro humano completo → Contratación")
    integral = client.get(f"/candidatos/{P2}").json()["resultadoIntegral"]
    nombres = {v["nombre"]: v for v in integral["validaciones"]}
    check(nombres.get("Psicometría digital", {}).get("obligatoria") and nombres.get("Entrevista Red Human con avatar", {}).get("score") == 82,
          "la evaluación integral toma las validaciones obligatorias del proceso")
    r = client.patch(f"/candidatos/{P2}/etapa", json={"etapa": "Contratación"})
    s = seg(P2)
    check(post(P2).etapa == "Contratación" and paso(s, "onboarding")["espera"] == "Se habilita en Onboarding" and paso(s, "alta")["disponible"] is False,
          "en Contratación: los pasos de Onboarding esperan su etapa")

    # estado ≠ resultado: psicometría desfavorable
    P2b = nueva("Bruno Desfavorable", V2)
    pb = post(P2b)
    pb.analisis = {"respuestas_web": [{"pregunta": "x", "respuesta": "Sí"}]}
    pb.estado, pb.prefiltro_completo = "cumple", True
    db.commit()
    client.patch(f"/candidatos/{P2b}/etapa", json={"etapa": "Entrevista IA"})
    db.add(Entrevista(codigo="ENT-P2B", candidato_id=pb.candidato_id, postulacion_id=pb.id, token="tok-p2b", estado="evaluada",
                      evaluacion={"match_perfil": 75, "score_entrevista": 75}, finalizada_en=datetime.now(timezone.utc)))
    db.commit()
    client.patch(f"/candidatos/{P2b}/etapa", json={"etapa": "Entrevista Humana"})
    e = evaluar(P2b, "psicometrica", "psicometria")["evaluacion"]
    resultado(e["codigo"], "desfavorable")
    s = seg(P2b)
    ps = paso(s, "psicometria")
    check(ps["estado"] == "completada" and ps["resultado"] == "no_favorable" and not ps["cumpleRegla"],
          "estado y resultado desacoplados: «Completada · No favorable»")
    r = client.patch(f"/candidatos/{P2b}/etapa", json={"etapa": "Contratación"})
    check(r.status_code == 409 and "No favorable" in r.json()["detail"] and post(P2b).activa,
          "un obligatorio No favorable bloquea el avance sin descartar (RH decide)")

    # versionado: quitar «referencias» de la vacante y aplicar la versión vigente a P2 (que ya tiene referencias hecha)
    pasos2 = [x for x in client.get(f"/procesos/vacantes/{V2}").json()["proceso"]["pasos"] if x["id"] != "referencias"]
    client.put(f"/procesos/vacantes/{V2}", json={"pasos": pasos2})
    check(any(x["id"] == "referencias" for x in post(P2).proceso["pasos"]), "quitar un paso de la vacante no toca al candidato existente")
    r = client.post(f"/procesos/postulaciones/{P2}/aplicar-vigente")
    ref = paso(r.json()["proceso"], "referencias")
    check(r.status_code == 200 and ref["heredado"] and not ref["obligatorio"] and ref["evaluacion"] == e_ref["codigo"],
          "«Aplicar versión vigente» conserva el paso con actividad como heredado (no se cancela nada en curso)")

    # ================= 3. Actividades en paralelo =================
    print("\n--- 3. Médica y socioeconómica en paralelo ---")
    V3 = vacante("Chofer repartidor", {"plantilla_id": plantillas["paralelo_medica_socioeconomica"]["id"]})
    client.put(f"/procesos/vacantes/{V3}", json={"etapas": {"Entrevista Humana": {"avance_automatico": True}}})
    P3 = nueva("Carla Paralelo", V3)
    r = client.post(f"/evaluaciones/postulaciones/{P3}", json={"tipo": "entrevista_humana", "forma": "registro_directo"})
    check(r.status_code == 201 and r.json()["movidaAFiltroHumano"] is False and post(P3).etapa == "Prefiltro" and r.json()["avisoProceso"],
          "crear la entrevista humana NO salta un prefiltro obligatorio sin cumplir (avisa por qué)")
    p3 = post(P3)
    p3.prefiltro_completo, p3.estado = True, "cumple"
    db.commit()
    r = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Entrevista Humana"})
    check(r.status_code == 200, "con el prefiltro cumplido pasa a Filtro humano (Filtro Red Human vacío no bloquea)")
    s = seg(P3)
    med, soc = paso(s, "medica"), paso(s, "socioeconomica")
    check(med["disponible"] and soc["disponible"] and med["accion"]["clave"] == soc["accion"]["clave"] == "iniciar_evaluacion",
          "médica y socioeconómica disponibles AL MISMO TIEMPO")
    e_med = evaluar(P3, "medica", "medica")["evaluacion"]
    e_soc = evaluar(P3, "socioeconomica", "socioeconomica")["evaluacion"]
    s = seg(P3)
    check(paso(s, "medica")["espera"] == "Falta el consentimiento médico del candidato", "la médica espera el consentimiento (dice qué falta)")
    check(paso(s, "socioeconomica")["estado"] == "en_curso" and "consentimiento" not in paso(s, "socioeconomica")["espera"],
          "…y eso no frena a la socioeconómica en paralelo")
    check(resultado(e_med["codigo"], "apto").status_code == 409, "el consentimiento médico bloquea la captura hasta ser aceptado")
    r = client.patch(f"/candidatos/{P3}/etapa", json={"etapa": "Contratación"})
    check(r.status_code == 409 and "Evaluación médica" in r.json()["detail"] and "Estudio socioeconómico" in r.json()["detail"],
          "ambas obligatorias antes de Contratación (409 las lista)")
    # plazo vencido = solo alerta
    p3 = post(P3)
    p3.etapa_desde = datetime.now(timezone.utc) - timedelta(days=9)
    db.commit()
    s = seg(P3)
    check(paso(s, "medica")["vencido"] and any("Evaluación médica" in a["texto"] for a in s["alertas"]) and post(P3).activa and post(P3).etapa == "Entrevista Humana",
          "plazo vencido: alerta en el seguimiento, el candidato sigue activo y en su etapa")
    tok = db.query(Evaluacion).filter_by(codigo=e_med["codigo"]).one().consentimiento_token
    r = client.post(f"/evaluaciones/publica/consentimiento/{tok}/aceptar", json={"nombre": "Carla Paralelo", "acepto": True})
    check(r.status_code == 200, "el candidato otorga el consentimiento médico")
    check(resultado(e_med["codigo"], "apto").status_code == 200, "con consentimiento ya se captura el resultado médico")
    eh3 = db.query(Evaluacion).filter_by(postulacion_id=post(P3).id, tipo="entrevista_humana").one().codigo
    resultado(eh3, "avanzar")
    check(post(P3).etapa == "Entrevista Humana", "aún falta la socioeconómica: no hay avance automático")
    resultado(e_soc["codigo"], "favorable")
    p3 = post(P3)
    check(p3.etapa == "Contratación" and p3.expediente is not None and any(h.get("evento") == "avance_automatico" for h in p3.historial),
          "último obligatorio cumplido + interruptor encendido → avance automático a Contratación")

    # una etapa sin pasos obligatorios no bloquea
    V4 = vacante("Auxiliar", {"pasos": [{"id": "tec", "tipo": "tecnica", "obligatorio": False, "etapa": "Entrevista Humana"}]})
    P4 = nueva("Diana Opcional", V4)
    r = client.patch(f"/candidatos/{P4}/etapa", json={"etapa": "Contratación"})
    check(r.status_code == 200, "un proceso solo con pasos opcionales nunca bloquea el avance")
    # predeterminada: una vacante nueva sin elegir proceso toma la plantilla predeterminada
    client.patch(f"/procesos/plantillas/{plantillas['paralelo_medica_socioeconomica']['id']}", json={"predeterminada": True})
    V5 = vacante("Vendedor")
    check(client.get(f"/procesos/vacantes/{V5}").json()["proceso"].get("plantilla_id") == plantillas["paralelo_medica_socioeconomica"]["id"],
          "la plantilla predeterminada de la Cuenta se copia a una vacante nueva")
    # sin proceso todo sigue como antes
    vac_sin = db.query(Vacante).filter(Vacante.cuenta_id == cuenta.id, Vacante.proceso == {}).first()
    if vac_sin is None:
        vac_sin = db.query(Vacante).filter(Vacante.codigo == vacante("Sin proceso", {"quitar": True})).one()
    P5 = nueva("Eva Legado", vac_sin.codigo)
    # 2026-10-06: ninguna postulación sin ruta → vacante sin proceso = predeterminado de la Cuenta (cascada nivel 2)
    s5 = seg(P5)
    check(s5["tieneProceso"] is True and s5["origen"] == "cuenta" and s5["plantilla"] == "Médica y socioeconómica en paralelo",
          "una vacante sin proceso: el candidato recibe el proceso predeterminado de la Cuenta")
    r = client.patch(f"/candidatos/{P5}/etapa", json={"etapa": "Contratación", "manual": True})
    check(r.status_code == 409 and "obligatorios" in r.json()["detail"], "…y su compuerta aplica igual (409 sin omisión autorizada)")

    db.close()

print(f"\n{OK} verificaciones OK")
