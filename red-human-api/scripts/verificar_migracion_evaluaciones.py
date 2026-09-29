"""Verificación de `scripts/migrar_evaluaciones_unificadas.py` (Evaluaciones unificadas — Fase 1, 2026-09-29).

Arma datos de ORIGEN reales (entrevistas humanas creadas por la API → bitácora real; evaluaciones de cada tipo,
modo y estado) y comprueba:
1) Simulación: no escribe nada.
2) --forzar: respaldo, mapeo de estados (5) y conclusiones (incluido el conflicto Aprobado + No avanzar), modos →
   formas, consentimiento como condición, autor ≠ captura, liga del evaluador y de consentimiento conservadas,
   clave de Psicométricas.mx, adjuntos, cita byte a byte, historial reconstruido.
3) Tablas de origen intactas, ninguna etapa cambió, la Entrevista Red Human no se tocó.
4) Idempotente: la segunda corrida no agrega nada.
Modo demo, base desechable.

Uso (desde red-human-api/):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verificar_migracion_evaluaciones.py
"""

import contextlib
import io
import os
import runpy
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_dir = tempfile.mkdtemp(prefix="rh_migeval_")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_dir) / "mig.db").replace("\\", "/")
for k in ("OPENAI_API_KEY", "WHATSAPP_PROVIDER", "META_WHATSAPP_TOKEN", "META_PHONE_NUMBER_ID", "ANAM_API_KEY", "ANAM_LLM_ID", "RESEND_API_KEY"):
    os.environ[k] = ""
os.environ["ADMIN_PASSWORD"] = "prueba-mig"
os.environ["SEMBRAR_DEMO"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.deps import cuenta_actual, usuario_actual  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Bitacora, Candidato, Cuenta, Entrevista, EntrevistaHumana, Evaluacion, EvaluacionCandidato, EventoEvaluacion,
    Postulacion, Usuario, UsuarioCuenta, Vacante,
)

OK = 0


def check(cond, msg):
    global OK
    if not cond:
        print(f"❌ FALLO: {msg}")
        sys.exit(1)
    OK += 1
    print(f"✅ {msg}")


def correr(*args) -> str:
    salida = io.StringIO()
    argv = sys.argv
    sys.argv = ["migrar_evaluaciones_unificadas.py", *args]
    codigo = 0
    with contextlib.redirect_stdout(salida):
        try:
            runpy.run_path(str(RAIZ / "scripts" / "migrar_evaluaciones_unificadas.py"), run_name="__main__")
        except SystemExit as e:
            codigo = e.code or 0
    sys.argv = argv
    texto = salida.getvalue()
    if codigo:
        print(texto)
    check(codigo == 0, f"migración {' '.join(args) or '(simulación)'} termina sin error")
    return texto


with TestClient(app) as client:
    db = SessionLocal()
    admin = db.query(Usuario).filter(Usuario.rol == "Administrador").first()
    cuenta = Cuenta(nombre="Cuenta MIG", nombre_comercial="MIG RH", estado="Activa")
    db.add(cuenta)
    db.flush()
    db.add(UsuarioCuenta(usuario_id=admin.id, cuenta_id=cuenta.id))
    for v in db.query(Vacante).all():
        v.cuenta_id = cuenta.id
    for c in db.query(Candidato).all():
        c.cuenta_id = cuenta.id
        for p in c.postulaciones:
            p.cuenta_id = cuenta.id
            p.consentimiento = True
    db.commit()
    app.dependency_overrides[usuario_actual] = lambda: admin
    app.dependency_overrides[cuenta_actual] = lambda: cuenta
    posts = [p for p in db.query(Postulacion).filter(Postulacion.activa.is_(True)).order_by(Postulacion.id).all()][:4]
    check(len(posts) >= 4, "hay 4 postulaciones de ejemplo")
    P1, P2, P3, P4 = (p.codigo for p in posts)

    def programar(codigo, hora, modalidad="Videollamada", **extra):
        r = client.post(f"/candidatos/{codigo}/entrevista-humana", json={
            "tipo_entrevistador": "externo", "entrevistador_nombre": "Ana Externa", "entrevistador_correo": "ana@externa.mx",
            "fecha": "2026-10-05", "hora": hora, "modalidad": modalidad, "liga": "https://meet.example/abc" if modalidad == "Videollamada" else "",
            "comentario": "Trae tu portafolio", **extra,
        })
        check(r.status_code == 201, f"programar entrevista en {codigo} ({r.status_code})")

    # --- ORIGEN: entrevistas humanas por la API (bitácora real) ---
    programar(P1, "07:29")  # queda pendiente, con instrucción
    r = client.patch(f"/candidatos/{P1}/entrevista-humana", json={"fecha": "2026-10-06", "hora": "09:00", "modalidad": "Videollamada", "liga": "https://meet.example/abc", "comentario": "Trae tu portafolio"})
    check(r.status_code == 200, "reprogramar P1")
    programar(P2, "10:00")  # resultado por liga con conflicto Aprobado + No avanzar
    db.expire_all()
    eh2 = db.query(EntrevistaHumana).join(Postulacion).filter(Postulacion.codigo == P2).one()
    r = client.post(f"/entrevista-humana/publica/{eh2.token}", json={"resultado": "aprobado", "recomendacion": "no_avanzar", "comentario": "Buen perfil, sin experiencia en SAP"})
    check(r.status_code == 200, "el evaluador registra por su liga (Aprobado + No avanzar)")
    programar(P3, "12:00", modalidad="Llamada")  # cancelada
    check(client.post(f"/candidatos/{P3}/entrevista-humana/cancelar").status_code == 200, "cancelar P3")
    programar(P4, "16:30", modalidad="Presencial", ubicacion="Av. Reforma 1")  # realizada, y luego RH captura segunda entrevista
    check(client.post(f"/candidatos/{P4}/entrevista-humana/realizada", params={"forzar_prueba": True}).status_code == 200, "marcar realizada P4")
    db.expire_all()
    eh4 = db.query(EntrevistaHumana).join(Postulacion).filter(Postulacion.codigo == P4).one()
    eh4_realizada_sin_resultado = not eh4.resultado
    check(eh4_realizada_sin_resultado, "P4 queda realizada sin resultado")

    # --- ORIGEN: evaluaciones y verificaciones (una por caso) ---
    informe = Path(_dir) / "informe.pdf"
    informe.write_bytes(b"%PDF-1.4 prueba")
    p1 = posts[0]
    ahora = datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc)
    evs = [
        EvaluacionCandidato(codigo="EVA-7001", cuenta_id=cuenta.id, postulacion_id=p1.id, tipo="medico", nombre="Médico", modo="manual",
                            estado="en_espera_consentimiento", requiere_consentimiento_expreso=True, consentimiento_token="tok-consent-1",
                            asignada_por="RH Uno", creada_en=ahora),
        EvaluacionCandidato(codigo="EVA-7002", cuenta_id=cuenta.id, postulacion_id=p1.id, tipo="psicometrica", nombre="Cleaver", modo="integrada",
                            proveedor="Psicométricas.mx", id_proveedor="1,7", clave_proveedor="CLV-123", resultado_json={"fecha_fin": "2026-09-28"},
                            estado="resultado_recibido", paso_integrada="resultado_recibido", archivo=str(informe), nombre_archivo="informe.pdf",
                            mime="application/pdf", resultado_cargado_por="Psicométricas.mx (automático)", resultado_cargado_en=ahora,
                            historial=[{"fecha": ahora.isoformat(), "usuario": "Psicométricas.mx (automático)", "de": "en_proceso", "a": "resultado_recibido", "detalle": "webhook"}],
                            asignada_por="RH Uno", creada_en=ahora),
        EvaluacionCandidato(codigo="EVA-7003", cuenta_id=cuenta.id, postulacion_id=p1.id, tipo="socioeconomico", nombre="Socioeconómico", modo="manual",
                            estado="revisada", resultado_resumen="Visita realizada", resultado_cargado_por="RH Dos", resultado_cargado_en=ahora,
                            dictamen="favorable", comentario_revision="Todo en orden", revisada_por="RH Tres", revisada_en=ahora,
                            asignada_por="RH Dos", creada_en=ahora),
        EvaluacionCandidato(codigo="EVA-7004", cuenta_id=cuenta.id, postulacion_id=p1.id, tipo="referencias", nombre="Referencias", modo="enlace",
                            url="https://otro.sistema/ref/1", estado="fallida", motivo_fallida="El candidato no respondió", asignada_por="RH Uno", creada_en=ahora),
        EvaluacionCandidato(codigo="EVA-7005", cuenta_id=cuenta.id, postulacion_id=p1.id, tipo="medico", nombre="Médico", modo="manual", estado="revisada",
                            requiere_consentimiento_expreso=True, consentimiento_token="tok-consent-2", consentimiento_texto="Yo, Ana…",
                            consentimiento_aceptado_en=ahora, consentimiento_evidencia={"nombre": "Ana", "ip": "1.2.3.4"},
                            dictamen="apto_con_restricciones", resultado_cargado_por="Dra. Pérez (capturó RH Uno)", resultado_cargado_en=ahora,
                            revisada_por="RH Uno", revisada_en=ahora, asignada_por="RH Uno", creada_en=ahora),
        EvaluacionCandidato(codigo="EVA-7006", cuenta_id=cuenta.id, postulacion_id=p1.id, tipo="tecnica", nombre="Caso práctico", modo="integrada",
                            estado="en_proceso", paso_integrada="completada", asignada_por="RH Uno", creada_en=ahora),
    ]
    db.add_all(evs)
    db.add(Entrevista(codigo="ENT-9", candidato_id=p1.candidato_id, postulacion_id=p1.id, token="tok-ia", estado="evaluada"))
    db.commit()
    etapas_antes = {p.codigo: p.etapa for p in db.query(Postulacion).all()}
    crudo_eh = {i: f for i, f in db.execute(text("SELECT id, fecha FROM entrevistas_humanas"))}
    ia_antes = db.execute(text("SELECT * FROM entrevistas ORDER BY id")).fetchall()
    n_ehs = db.query(EntrevistaHumana).count()
    db.close()

    # ---------- 1. simulación ----------
    db = SessionLocal()
    bit_antes = db.query(Bitacora).count()
    out = correr()
    check("SIMULACIÓN" in out and db.query(Evaluacion).count() == 0 and db.query(Bitacora).count() == bit_antes,
          "simulación: imprime el plan y no escribe nada")
    check(f"por migrar: {n_ehs + len(evs)}" in out, f"simulación: planea {n_ehs} entrevistas + {len(evs)} evaluaciones")
    check("Conflicto en el registro original" in out, "simulación: reporta el conflicto Aprobado + No avanzar")

    # ---------- 2. migración ----------
    out = correr("--forzar")
    check("Respaldo:" in out and list(Path(_dir).glob("mig.antes_evaluaciones_*.db")), "se hizo respaldo de la base antes de migrar")
    db.expire_all()
    check(db.query(Evaluacion).count() == n_ehs + len(evs), "cada registro de origen tiene exactamente una evaluación")

    def de_eh(codigo):
        eh = db.query(EntrevistaHumana).join(Postulacion).filter(Postulacion.codigo == codigo).one()
        return eh, db.query(Evaluacion).filter_by(origen_tabla="entrevistas_humanas", origen_id=eh.id).one()

    eh, e = de_eh(P1)
    check(e.estado == "pendiente" and e.forma == "asignada" and e.evaluador_tipo == "externo" and e.evaluador_nombre == "Ana Externa",
          "entrevista pendiente → Pendiente, asignada a evaluador externo")
    check(e.token_evaluador == eh.token, "la liga del evaluador se conserva (mismo token)")
    crudo = db.execute(text("SELECT cita_fecha_hora FROM evaluaciones WHERE id = :i"), {"i": e.id}).scalar()
    check(crudo == crudo_eh[eh.id] and e.cita_zona_horaria == "America/Mexico_City", f"la cita se copió byte a byte, sin recalcular ({crudo})")
    check(e.instrucciones == "Trae tu portafolio" and e.comentarios == "" and e.cita_liga_videollamada, "sin resultado: el comentario es instrucción de la cita")
    acc = [x.accion for x in db.query(EventoEvaluacion).filter_by(evaluacion_id=e.id).order_by(EventoEvaluacion.id)]
    check(acc[0] == "creada" and "reprogramada" in acc and acc[-1] == "migrada", f"historial desde la bitácora: {acc}")
    rep = db.query(EventoEvaluacion).filter_by(evaluacion_id=e.id, accion="reprogramada").one()
    check(rep.anteriores.get("cita_fecha_hora", "").startswith("2026-10-05T13:29"), "la reprogramación guarda la fecha anterior")

    eh, e = de_eh(P2)
    check(e.estado == "con_resultado" and e.conclusion == "no_avanzar", "Aprobado + No avanzar → Con resultado · No avanzar (gana la recomendación)")
    mig = db.query(EventoEvaluacion).filter_by(evaluacion_id=e.id, accion="migrada").one()
    check(any("Conflicto" in n for n in mig.detalle.get("notas", [])), "el conflicto queda anotado en el historial")
    check(e.realizada_por == "Ana Externa" and e.registrada_por == "Ana Externa" and e.registrada_via == "liga_evaluador",
          "autor = evaluador asignado; captura = evaluador vía liga")
    check(e.comentarios == "Buen perfil, sin experiencia en SAP" and e.instrucciones == "", "con resultado: el comentario va a comentarios")
    check(db.query(EventoEvaluacion).filter_by(evaluacion_id=e.id, accion="resultado_registrado", canal="liga_evaluador").count() == 1,
          "el resultado por liga queda en el historial con su canal")

    _, e = de_eh(P3)
    check(e.estado == "cancelada" and e.cita_modalidad == "Teléfono", "cancelada → Cancelada; «Llamada» → «Teléfono»")
    _, e = de_eh(P4)
    check(e.estado == "realizada_sin_resultado" and e.cita_direccion == "Av. Reforma 1" and e.conclusion == "",
          "realizada sin resultado → Realizada · Resultado pendiente; ubicación → dirección")

    def de_ev(codigo):
        o = db.query(EvaluacionCandidato).filter_by(codigo=codigo).one()
        return o, db.query(Evaluacion).filter_by(origen_tabla="evaluaciones_candidato", origen_id=o.id).one()

    o, e = de_ev("EVA-7001")
    check(e.codigo == "EVA-7001" and e.tipo == "medica" and e.estado == "pendiente" and e.consentimiento == "pendiente"
          and e.consentimiento_token == "tok-consent-1", "médica en espera de consentimiento → Pendiente + condición pendiente (liga conservada)")
    o, e = de_ev("EVA-7002")
    check(e.forma == "integrada" and e.clave_proveedor == "CLV-123" and e.resultado_json == {"fecha_fin": "2026-09-28"},
          "psicométrica integrada: clave y resultado de Psicométricas.mx intactos")
    check(e.estado == "con_resultado" and e.conclusion == "" and e.adjuntos and e.adjuntos[0]["archivo"] == str(informe),
          "resultado recibido sin dictamen → Con resultado · Sin conclusión, con su adjunto")
    check(e.realizada_por == "Psicométricas.mx" and e.registrada_via == "proveedor", "integrada: autor = proveedor; captura vía proveedor")
    o, e = de_ev("EVA-7003")
    check(e.tipo == "socioeconomica" and e.forma == "registro_directo" and e.conclusion == "favorable", "Carga manual → registro_directo; dictamen favorable")
    check(e.realizada_por == "" and e.registrada_por == "RH Dos", "carga manual: autor «No especificado»; quien cargó va en registrada_por")
    check("Todo en orden" in e.comentarios and "Visita realizada" in e.comentarios, "resumen y comentario de revisión se conservan")
    check(db.query(EventoEvaluacion).filter_by(evaluacion_id=e.id, accion="resultado_complementado").count() == 1, "la revisión de RH queda como complemento en el historial")
    o, e = de_ev("EVA-7004")
    check(e.forma == "liga_otro_sistema" and e.liga_externa_candidato == "https://otro.sistema/ref/1" and e.estado == "cancelada"
          and e.motivo_estado == "El candidato no respondió", "Enlace externo → liga_otro_sistema; Fallida → Cancelada con motivo")
    o, e = de_ev("EVA-7005")
    check(e.consentimiento == "otorgado" and e.consentimiento_evidencia.get("ip") == "1.2.3.4" and e.conclusion == "apto_con_restricciones",
          "médica: consentimiento otorgado con su evidencia; conserva Apto con restricciones")
    o, e = de_ev("EVA-7006")
    check(e.estado == "realizada_sin_resultado", "integrada en paso «completada» → Realizada · Resultado pendiente")

    # ---------- 3. nada más cambió ----------
    check({p.codigo: p.etapa for p in db.query(Postulacion).all()} == etapas_antes, "ninguna postulación cambió de etapa")
    check(db.execute(text("SELECT * FROM entrevistas ORDER BY id")).fetchall() == ia_antes
          and not db.query(Evaluacion).filter(Evaluacion.tipo.notin_(["entrevista_humana", "medica", "psicometrica", "socioeconomica", "tecnica", "referencias", "otra"])).count(),
          "la Entrevista Red Human quedó fuera y sin cambios")
    check(db.query(EntrevistaHumana).count() == n_ehs and db.query(EvaluacionCandidato).count() == len(evs), "las tablas de origen se conservan")
    check(db.query(Bitacora).filter_by(accion="evaluaciones_unificadas_migradas").count() == 1, "la migración queda en bitácora")

    # ---------- 4. idempotente ----------
    total = db.query(Evaluacion).count()
    eventos = db.query(EventoEvaluacion).count()
    db.close()
    out = correr("--forzar")
    db = SessionLocal()
    check("Nada por migrar" in out and db.query(Evaluacion).count() == total and db.query(EventoEvaluacion).count() == eventos,
          "segunda corrida: no agrega ni duplica nada")
    db.close()

print(f"\n{OK} verificaciones OK")
