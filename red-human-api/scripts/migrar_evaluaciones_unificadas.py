"""Migración a Evaluaciones unificadas — Fase 1 (2026-09-29).

Copia `entrevistas_humanas` y `evaluaciones_candidato` a `evaluaciones` (+ historial en `eventos_evaluacion`).
La Entrevista Red Human (`entrevistas`) NO se toca: queda fuera de la unificación.

GARANTÍAS
- Por defecto es SIMULACIÓN: no escribe nada (ni crea tablas); imprime el plan, el mapeo y las advertencias.
- Solo copia: las tablas de origen nunca se modifican ni se borran (se comprueba con una huella SHA-256 de cada
  tabla de origen antes y después; si cambió, se deshace todo).
- Idempotente: cada fila migrada guarda (origen_tabla, origen_id) con restricción única; volver a correrla solo
  agrega lo que falte (p. ej. registros creados después de la primera corrida). NO sincroniza cambios hechos
  después en filas ya migradas → córrela con la API DETENIDA justo antes de desplegar las pantallas nuevas.
- Ligas: `EntrevistaHumana.token` → `token_evaluador` (la liga del evaluador ya enviada sigue sirviendo) y
  `consentimiento_token` se conserva (liga de consentimiento médico). `clave_proveedor` y `resultado_json` se
  copian tal cual (webhook de Psicométricas.mx).
- Citas: la fecha se copia byte a byte, sin recalcular (el diagnóstico de zona horaria dejó claro que lo guardado
  es UTC correcto; para citas corridas por el modal viejo: scripts/diagnostico_citas_desfasadas.py).
- Adjuntos: se copia la RUTA del archivo (no se mueve ni se duplica nada en disco); se avisa si falta en disco.
- Autor ≠ captura: `realizada_por` solo se llena si el origen identifica al evaluador (entrevistador asignado o
  proveedor integrado); en cargas manuales queda vacío = «No especificado». Quien capturó va en `registrada_por`.
- Historial: se reconstruye desde la bitácora (entrevistas) y desde `historial` (evaluaciones), con la acción
  original y el id de bitácora en el detalle. Nada de la bitácora se modifica (solo se AGREGA un registro final).
- Todo en UNA transacción; antes del commit se verifica fila por fila (conteos, ligas, fechas, adjuntos, eventos).

MAPEO (especificación, sección 11)
- Entrevista humana: cancelada → Cancelada · con resultado → Con resultado · realizada sin resultado →
  Realizada · Resultado pendiente · resto → Pendiente (no se infiere «No realizada»: hoy no se registra).
  Conclusión: Aprobado + Avanzar → Avanzar · Rechazado o No avanzar → No avanzar · Segunda entrevista → Requiere
  otra entrevista. Conflicto (p. ej. Aprobado + No avanzar): gana la recomendación y queda nota en el historial.
  `comentario`: antes del resultado era la instrucción de la cita; el resultado la SOBRESCRIBÍA → con resultado va
  a «comentarios» (la instrucción original no es recuperable y se anota), sin resultado va a «instrucciones».
- Evaluación: Carga manual → registro_directo · Enlace externo → liga_otro_sistema · Integrada → integrada.
  En espera de consentimiento → Pendiente + consentimiento pendiente · Pendiente/En proceso → Pendiente (paso
  integrado «completada» → Realizada · Resultado pendiente) · Resultado recibido / Revisada → Con resultado ·
  Fallida/Cancelada → Cancelada (con su motivo). Dictamen → conclusión (médica: Apto…; resto: Favorable…).

USO (desde red-human-api/, con el .env del entorno):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/migrar_evaluaciones_unificadas.py            # simulación
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/migrar_evaluaciones_unificadas.py --forzar   # migra
    Con un motor que no sea SQLite, --forzar exige además --respaldo-hecho (haz tu pg_dump antes).
"""

import hashlib
import secrets
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app import fechas  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    FORMA_DESDE_MODO,
    MODALIDAD_DESDE_LEGADO,
    TIPO_DESDE_LEGADO,
    Bitacora,
    EntrevistaHumana,
    Evaluacion,
    EvaluacionCandidato,
    EventoEvaluacion,
    Postulacion,
    Usuario,
    conclusiones_de,
    registrar,
)
from app.services import archivos as fs  # noqa: E402

ORIGEN_EH = "entrevistas_humanas"
ORIGEN_EV = "evaluaciones_candidato"
TABLAS_ORIGEN = (ORIGEN_EH, ORIGEN_EV)
ACTOR = "migración evaluaciones unificadas"
CONCLUSION_DESDE_RECOMENDACION = {"avanzar": "avanzar", "no_avanzar": "no_avanzar", "segunda_entrevista": "requiere_otra_entrevista"}
CONCLUSION_DESDE_RESULTADO = {"aprobado": "avanzar", "no_aprobado": "no_avanzar"}
ACCIONES_BITACORA_EH = {
    "entrevista_humana_programada": "creada",
    "entrevista_humana_modificada": "modificada",
    "entrevista_humana_cancelada": "cambio_estado",
    "entrevista_humana_marcada_realizada": "cambio_estado",
    "entrevista_humana_resultado_capturado_rh": "resultado_registrado",
    "entrevista_humana_evaluada_por_liga": "resultado_registrado",
    "recordatorio_entrevista_humana_enviado": "recordatorio",
    "recordatorio_entrevista_automatico": "recordatorio",
}


# ------------------------------------------------------------ utilidades


def huella_tablas(conn) -> dict:
    """SHA-256 del contenido completo de cada tabla de origen (orden por id)."""
    salida = {}
    for t in TABLAS_ORIGEN:
        h = hashlib.sha256()
        for fila in conn.execute(text(f"SELECT * FROM {t} ORDER BY id")):
            h.update(repr(tuple(fila)).encode("utf-8"))
        salida[t] = h.hexdigest()
    return salida


def _dt(valor):
    if not valor:
        return None
    if isinstance(valor, datetime):
        return fechas.a_utc(valor)
    try:
        return fechas.a_utc(datetime.fromisoformat(str(valor).replace("Z", "+00:00")))
    except ValueError:
        return None


def _evento(accion, fecha, actor="", canal="sistema", de="", a="", anteriores=None, **detalle) -> dict:
    return {"accion": accion, "fecha": _dt(fecha) or datetime.now(timezone.utc), "actor": actor or "", "canal": canal,
            "estado_anterior": de, "estado_nuevo": a, "anteriores": anteriores or {}, "detalle": detalle}


def _adjunto(ruta, nombre, mime, por, via, en, avisos, ref) -> dict:
    if ruta and not fs.existe(ruta):
        avisos.append(f"{ref}: el archivo «{nombre or ruta}» no está en disco (se conserva la referencia).")
    return {"id": secrets.token_hex(6), "archivo": ruta, "nombre": nombre or Path(ruta).name, "mime": mime or "",
            "subido_por": por or "", "subido_via": via, "subido_en": fechas.iso(en)}


# ------------------------------------------------------------ Entrevista humana


def conclusion_entrevista(resultado: str, recomendacion: str):
    """(conclusión, nota de conflicto). Gana la recomendación; el resultado solo decide si no hay recomendación."""
    por_rec = CONCLUSION_DESDE_RECOMENDACION.get(recomendacion or "")
    por_res = CONCLUSION_DESDE_RESULTADO.get(resultado or "")
    if por_rec:
        conflicto = (resultado == "aprobado" and recomendacion == "no_avanzar") or (
            resultado == "no_aprobado" and recomendacion in ("avanzar", "segunda_entrevista"))
        nota = (f"Conflicto en el registro original: resultado «{resultado}» + recomendación «{recomendacion}». "
                f"Se conservó la recomendación ({por_rec}).") if conflicto else ""
        return por_rec, nota
    if por_res:
        return por_res, f"El registro original solo tenía resultado «{resultado}» (sin recomendación)."
    return "", ""


def estado_entrevista(eh: EntrevistaHumana) -> str:
    if eh.cancelada:
        return "cancelada"
    if eh.resultado or eh.recomendacion:
        return "con_resultado"
    if eh.realizada:
        return "realizada_sin_resultado"
    return "pendiente"


def eventos_bitacora_por_entrevista(db, entrevistas_por_p: dict) -> dict:
    """{eh.id: [Bitacora]}: cada evento de la postulación va a la entrevista vigente en ese momento (la más
    reciente creada antes del evento; los que traen `entrevista_humana` en el detalle se asignan directo)."""
    codigos = {p.codigo: p for p in entrevistas_por_p}
    salida = defaultdict(list)
    if not codigos:
        return salida
    filas = (db.query(Bitacora).filter(Bitacora.entidad == "postulacion", Bitacora.entidad_id.in_(list(codigos)),
                                       Bitacora.accion.in_(list(ACCIONES_BITACORA_EH))).order_by(Bitacora.id).all())
    for b in filas:
        ehs = entrevistas_por_p[codigos[b.entidad_id]]
        directo = (b.detalle or {}).get("entrevista_humana")
        destino = next((e for e in ehs if e.id == directo), None) if directo else None
        if destino is None:
            limite = fechas.a_utc(b.ts) + timedelta(seconds=5)
            previas = [e for e in ehs if fechas.a_utc(e.creado_en) <= limite]
            destino = previas[-1] if previas else ehs[0]
        salida[destino.id].append(b)
    return salida


def plan_entrevista(db, eh: EntrevistaHumana, p: Postulacion, bitacora: list, avisos: list) -> tuple:
    ref = f"entrevista humana #{eh.id} ({p.codigo})"
    estado = estado_entrevista(eh)
    conclusion, nota_conflicto = conclusion_entrevista(eh.resultado, eh.recomendacion)
    usuario = db.get(Usuario, eh.usuario_id) if eh.usuario_id else None
    correo = (usuario.correo if usuario else "") if eh.tipo == "interno" else eh.correo_externo
    whatsapp = (getattr(usuario, "telefono", "") or "") if eh.tipo == "interno" else eh.whatsapp_externo
    con_resultado = estado == "con_resultado"
    notas = [n for n in (nota_conflicto,) if n]
    if con_resultado and eh.comentario:
        notas.append("El comentario del registro original se tomó como comentario del resultado (la instrucción "
                     "original de la cita, si existía, fue sobrescrita por el sistema anterior).")

    # quién capturó el resultado
    registrada_por, registrada_via, reg_uid = "", "", None
    if con_resultado:
        if eh.resultado_capturado_por == "entrevistador":
            registrada_por, registrada_via = (eh.entrevistador or "Evaluador vía liga"), "liga_evaluador"
        else:
            ultimo_rh = next((b for b in reversed(bitacora) if b.accion == "entrevista_humana_resultado_capturado_rh"), None)
            registrada_por, registrada_via = (ultimo_rh.actor if ultimo_rh else "RH (no registrado)"), "sistema"
            u = db.query(Usuario).filter(Usuario.nombre == registrada_por).first() if ultimo_rh else None
            reg_uid = u.id if u else None
    creacion = next((b for b in bitacora if b.accion == "entrevista_humana_programada"), None)
    autor_ok = estado in ("realizada_sin_resultado", "con_resultado") and bool(eh.entrevistador)

    fila = {
        "codigo": "",  # se asigna al insertar (EVA-5xxxx)
        "cuenta_id": p.cuenta_id or (p.candidato.cuenta_id if p.candidato else None),
        "postulacion_id": p.id, "candidato_id": eh.candidato_id or p.candidato_id, "vacante_id": p.vacante_id,
        "tipo": "entrevista_humana", "nombre": "", "forma": "asignada",
        "evaluador_tipo": eh.tipo if eh.tipo in ("interno", "externo") else ("interno" if eh.usuario_id else "externo"),
        "evaluador_usuario_id": eh.usuario_id, "evaluador_contacto_id": eh.contacto_id,
        "evaluador_nombre": eh.entrevistador or "", "evaluador_correo": correo or "", "evaluador_whatsapp": whatsapp or "",
        "token_evaluador": eh.token,
        "instrucciones": "" if con_resultado else (eh.comentario or ""),
        "cita_fecha_hora": eh.fecha, "cita_zona_horaria": fechas.TZ_ORG.key if eh.fecha else "",
        "cita_modalidad": MODALIDAD_DESDE_LEGADO.get(eh.modalidad, eh.modalidad or ""),
        "cita_direccion": eh.ubicacion or "", "cita_liga_videollamada": eh.liga or "", "cita_telefono": eh.telefono_contacto or "",
        "teams_evento_id": eh.teams_evento_id or "",
        "estado": estado, "motivo_estado": "",
        "consentimiento": "no_requerido",
        "conclusion": conclusion, "comentarios": (eh.comentario or "") if con_resultado else "", "adjuntos": [],
        "realizada_en": (eh.evaluada_en or eh.fecha) if estado in ("realizada_sin_resultado", "con_resultado") else None,
        "realizada_por": eh.entrevistador if autor_ok else "",
        "realizada_por_usuario_id": eh.usuario_id if autor_ok and eh.tipo == "interno" else None,
        "registrada_por": registrada_por, "registrada_por_usuario_id": reg_uid, "registrada_via": registrada_via,
        "registrada_en": eh.evaluada_en if con_resultado else None,
        "resultado_visto_en": (eh.evaluada_en or eh.creado_en) if con_resultado else None,  # lo viejo no se marca «Nuevo»
        "recordatorio_enviado_en": eh.recordatorio_enviado_en,
        "creado_por": creacion.actor if creacion else "", "creado_en": eh.creado_en,
        "origen_tabla": ORIGEN_EH, "origen_id": eh.id,
    }
    if not eh.token:
        fila["token_evaluador"] = secrets.token_urlsafe(24)
        avisos.append(f"{ref}: no tenía liga del evaluador; se generó una nueva.")

    eventos, estado_prev, fecha_prev = [], "", None
    for b in bitacora:
        d = b.detalle or {}
        accion = ACCIONES_BITACORA_EH[b.accion]
        base = {"accion_original": b.accion, "bitacora_id": b.id}
        canal = "liga_evaluador" if b.accion == "entrevista_humana_evaluada_por_liga" else "sistema"
        if b.accion == "entrevista_humana_programada":
            fecha_prev = _dt(d.get("fecha"))
            eventos.append(_evento("creada", b.ts, b.actor, a="pendiente", **base, fecha_cita=d.get("fecha"), modalidad=d.get("modalidad"),
                                   entrevistador=d.get("entrevistador")))
            estado_prev = "pendiente"
        elif b.accion == "entrevista_humana_modificada":
            nueva = _dt(d.get("fecha"))
            reprogramada = bool(nueva and fecha_prev and nueva != fecha_prev)
            eventos.append(_evento("reprogramada" if reprogramada else "modificada", b.ts, b.actor,
                                   anteriores={"cita_fecha_hora": fechas.iso(fecha_prev)} if reprogramada else {},
                                   **base, fecha_cita=d.get("fecha"), modalidad=d.get("modalidad")))
            fecha_prev = nueva or fecha_prev
        elif b.accion == "entrevista_humana_cancelada":
            eventos.append(_evento("cambio_estado", b.ts, b.actor, de=estado_prev, a="cancelada", **base))
            estado_prev = "cancelada"
        elif b.accion == "entrevista_humana_marcada_realizada":
            eventos.append(_evento("cambio_estado", b.ts, b.actor, de=estado_prev, a="realizada_sin_resultado", **base))
            estado_prev = "realizada_sin_resultado"
        elif accion == "resultado_registrado":
            conc, _ = conclusion_entrevista(d.get("resultado", ""), d.get("recomendacion", ""))
            nombre = "resultado_corregido" if (d.get("corrigio_captura_previa") or estado_prev == "con_resultado") else "resultado_registrado"
            eventos.append(_evento(nombre, b.ts, eh.entrevistador if canal == "liga_evaluador" else b.actor, canal, de=estado_prev,
                                   a="con_resultado", **base, resultado_original=d.get("resultado"),
                                   recomendacion_original=d.get("recomendacion"), conclusion=conc, comentario=d.get("comentario", "")))
            estado_prev = "con_resultado"
        else:  # recordatorios
            eventos.append(_evento("recordatorio", b.ts, b.actor, **base))
    if not any(e["accion"] == "creada" for e in eventos):
        eventos.insert(0, _evento("creada", eh.creado_en, "", a="pendiente", nota="Creación sin registro en bitácora (dato anterior)."))
    eventos.append(_evento("migrada", datetime.now(timezone.utc), ACTOR, "migracion", a=estado, origen=ORIGEN_EH, origen_id=eh.id,
                           resultado_original=eh.resultado, recomendacion_original=eh.recomendacion,
                           resultado_capturado_por=eh.resultado_capturado_por, notas=notas))
    return fila, eventos, notas


# ------------------------------------------------------------ Evaluaciones y verificaciones


def estado_evaluacion(ev: EvaluacionCandidato) -> str:
    return {
        "fallida": "cancelada", "revisada": "con_resultado", "resultado_recibido": "con_resultado",
    }.get(ev.estado) or ("realizada_sin_resultado" if ev.estado == "en_proceso" and ev.paso_integrada == "completada" else "pendiente")


def consentimiento_evaluacion(ev: EvaluacionCandidato, tipo: str, avisos: list, ref: str) -> str:
    if ev.requiere_consentimiento_expreso or tipo == "medica":
        if ev.consentimiento_aceptado_en:
            return "otorgado"
        if tipo == "medica" and not ev.requiere_consentimiento_expreso:
            avisos.append(f"{ref}: evaluación médica sin bandera de consentimiento expreso en el origen; queda «pendiente».")
        return "pendiente"
    return "no_requerido"


def plan_evaluacion(ev: EvaluacionCandidato, p: Postulacion, avisos: list) -> tuple:
    ref = f"evaluación {ev.codigo} ({p.codigo})"
    tipo = TIPO_DESDE_LEGADO.get(ev.tipo, ev.tipo)
    forma = FORMA_DESDE_MODO.get(ev.modo, "registro_directo")
    estado = estado_evaluacion(ev)
    con_resultado = estado == "con_resultado"
    notas = []
    conclusion = ev.dictamen if ev.dictamen in conclusiones_de(tipo) else ""
    if ev.dictamen and not conclusion:
        notas.append(f"Dictamen original «{ev.dictamen}» no válido para el tipo; se conserva solo en el historial.")
    comentarios = ev.resultado_resumen or ""
    if ev.comentario_revision:
        comentarios = (comentarios + "\n\n" if comentarios else "") + f"Revisión de RH ({ev.revisada_por or 'RH'}): {ev.comentario_revision}"
    automatico = "automático" in (ev.resultado_cargado_por or "").lower()
    registrada_por = ev.resultado_cargado_por or ev.revisada_por or ""
    if con_resultado and not (ev.archivo or comentarios or ev.resultado_json or conclusion):
        avisos.append(f"{ref}: estaba en «{ev.estado}» sin informe, resumen ni dictamen; queda «Con resultado» sin contenido.")
    adjuntos = []
    if ev.archivo:
        adjuntos.append(_adjunto(ev.archivo, ev.nombre_archivo, ev.mime, ev.resultado_cargado_por,
                                 "proveedor" if automatico else "sistema", ev.resultado_cargado_en, avisos, ref))
    fila = {
        "codigo": ev.codigo, "cuenta_id": ev.cuenta_id, "postulacion_id": ev.postulacion_id,
        "candidato_id": p.candidato_id, "vacante_id": p.vacante_id,
        "tipo": tipo, "nombre": ev.nombre if (tipo == "otra" or ev.prueba_id) else ("" if ev.nombre in ("", tipo) else ev.nombre),
        "forma": forma, "evaluador_tipo": "", "token_evaluador": secrets.token_urlsafe(24),
        "instrucciones": ev.notas or "",
        "liga_externa_candidato": ev.url or "", "prueba_id": ev.prueba_id, "proveedor": ev.proveedor or "",
        "id_proveedor": ev.id_proveedor or "", "clave_proveedor": ev.clave_proveedor or "",
        "resultado_json": ev.resultado_json or {}, "paso_integrada": ev.paso_integrada or "",
        "estado": estado, "motivo_estado": ev.motivo_fallida if estado == "cancelada" else "",
        "consentimiento": consentimiento_evaluacion(ev, tipo, avisos, ref),
        "consentimiento_token": ev.consentimiento_token, "consentimiento_texto": ev.consentimiento_texto or "",
        "consentimiento_en": ev.consentimiento_aceptado_en, "consentimiento_evidencia": ev.consentimiento_evidencia or {},
        "conclusion": conclusion, "comentarios": comentarios, "adjuntos": adjuntos,
        "realizada_en": (ev.resultado_cargado_en or ev.revisada_en) if estado in ("con_resultado", "realizada_sin_resultado") else None,
        # autor: solo si el origen lo identifica (proveedor integrado); cargas manuales → «No especificado»
        "realizada_por": ev.proveedor if (forma == "integrada" and ev.proveedor and estado == "con_resultado") else "",
        "registrada_por": registrada_por if con_resultado else "",
        "registrada_via": ("proveedor" if automatico else "sistema") if con_resultado else "",
        "registrada_en": (ev.resultado_cargado_en or ev.revisada_en) if con_resultado else None,
        "resultado_visto_en": (ev.revisada_en or ev.resultado_cargado_en) if con_resultado else None,
        "creado_por": ev.asignada_por or "", "creado_en": ev.creada_en,
        "origen_tabla": ORIGEN_EV, "origen_id": ev.id,
    }
    eventos = [_evento("creada", ev.creada_en, ev.asignada_por, a="pendiente", tipo_original=ev.tipo, modo_original=ev.modo)]
    for h in ev.historial or []:
        de, a = h.get("de", ""), h.get("a", "")
        eventos.append(_evento("cambio_estado", h.get("fecha") or ev.creada_en, h.get("usuario", ""),
                               "proveedor" if "automático" in (h.get("usuario") or "").lower() else "sistema",
                               de=_estado_legado(de, ev), a=_estado_legado(a, ev), estado_original_de=de, estado_original_a=a,
                               nota=h.get("detalle", "")))
    if ev.consentimiento_aceptado_en:
        eventos.append(_evento("consentimiento", ev.consentimiento_aceptado_en, (ev.consentimiento_evidencia or {}).get("nombre", "Candidato"),
                               "liga_candidato", valor="otorgado"))
    if ev.resultado_cargado_en:
        eventos.append(_evento("resultado_registrado", ev.resultado_cargado_en, ev.resultado_cargado_por,
                               "proveedor" if automatico else "sistema", a="con_resultado", con_archivo=bool(ev.archivo)))
    if ev.revisada_en:
        eventos.append(_evento("resultado_complementado", ev.revisada_en, ev.revisada_por, a="con_resultado",
                               dictamen=ev.dictamen, comentario=ev.comentario_revision))
    eventos.sort(key=lambda e: e["fecha"])
    eventos.append(_evento("migrada", datetime.now(timezone.utc), ACTOR, "migracion", a=estado, origen=ORIGEN_EV, origen_id=ev.id,
                           estado_original=ev.estado, modo_original=ev.modo, dictamen_original=ev.dictamen, notas=notas))
    return fila, eventos, notas


def _estado_legado(valor: str, ev: EvaluacionCandidato) -> str:
    if not valor:
        return ""
    return {"fallida": "cancelada", "revisada": "con_resultado", "resultado_recibido": "con_resultado"}.get(valor, "pendiente")


# ------------------------------------------------------------ ejecución


def construir_plan(db) -> tuple:
    avisos, planes, omitidas = [], [], []
    existentes = set()
    if "evaluaciones" in inspect(engine).get_table_names():
        existentes = {(o, i) for o, i in db.execute(text("SELECT origen_tabla, origen_id FROM evaluaciones WHERE origen_id IS NOT NULL"))}

    ehs = db.query(EntrevistaHumana).order_by(EntrevistaHumana.id).all()
    por_p = defaultdict(list)
    for eh in ehs:
        if eh.postulacion is None:
            omitidas.append(f"entrevista humana #{eh.id}: sin postulación (corre scripts/migrar_postulaciones.py); NO se migra.")
            continue
        por_p[eh.postulacion].append(eh)
    bit = eventos_bitacora_por_entrevista(db, por_p)
    for p, lista in por_p.items():
        for eh in lista:
            if (ORIGEN_EH, eh.id) in existentes:
                continue
            fila, evs, notas = plan_entrevista(db, eh, p, bit.get(eh.id, []), avisos)
            planes.append((fila, evs, notas))

    for ev in db.query(EvaluacionCandidato).order_by(EvaluacionCandidato.id).all():
        if (ORIGEN_EV, ev.id) in existentes:
            continue
        p = db.get(Postulacion, ev.postulacion_id)
        if p is None:
            omitidas.append(f"evaluación {ev.codigo}: su postulación #{ev.postulacion_id} no existe; NO se migra.")
            continue
        planes.append(plan_evaluacion(ev, p, avisos))

    for fila, _, _ in planes:
        if not fila["cuenta_id"]:
            omitidas.append(f"{fila['origen_tabla']} #{fila['origen_id']}: sin Cuenta resoluble; NO se migra.")
    planes = [x for x in planes if x[0]["cuenta_id"]]
    return planes, avisos, omitidas, len(existentes)


def imprimir_resumen(planes, avisos, omitidas, ya_migradas):
    por_origen = Counter(f["origen_tabla"] for f, _, _ in planes)
    print(f"Motor: {engine.dialect.name} · zona de la organización: {fechas.TZ_ORG.key}")
    print(f"Ya migradas antes: {ya_migradas} · por migrar: {len(planes)} "
          f"({por_origen.get(ORIGEN_EH, 0)} entrevistas humanas, {por_origen.get(ORIGEN_EV, 0)} evaluaciones)")
    for origen in TABLAS_ORIGEN:
        filas = [f for f, _, _ in planes if f["origen_tabla"] == origen]
        if not filas:
            continue
        print(f"\n== {origen}")
        print("   estado:      ", dict(Counter(f["estado"] for f in filas)))
        print("   conclusión:  ", dict(Counter(f["conclusion"] or "(sin conclusión)" for f in filas)))
        print("   forma:       ", dict(Counter(f["forma"] for f in filas)))
        print("   consent.:    ", dict(Counter(f["consentimiento"] for f in filas)))
        print("   con cita:    ", sum(1 for f in filas if f.get("cita_fecha_hora")), "· adjuntos:", sum(len(f["adjuntos"]) for f in filas),
              "· autor identificado:", sum(1 for f in filas if f["realizada_por"]))
    notas = [(f, n) for f, _, ns in planes for n in ns]
    if notas:
        print(f"\n== Notas que quedan en el historial ({len(notas)})")
        for f, n in notas:
            print(f"   · {f['origen_tabla']} #{f['origen_id']}: {n}")
    if avisos:
        print(f"\n== Advertencias ({len(avisos)})")
        for a in avisos:
            print("   ⚠", a)
    if omitidas:
        print(f"\n== NO se migran ({len(omitidas)}) — revísalas a mano")
        for o in omitidas:
            print("   ✖", o)


def respaldar_sqlite() -> Path:
    ruta = Path(engine.url.database).resolve()
    destino = ruta.with_name(f"{ruta.stem}.antes_evaluaciones_{datetime.now():%Y%m%d_%H%M%S}{ruta.suffix}")
    origen = sqlite3.connect(str(ruta))
    copia = sqlite3.connect(str(destino))
    with copia:
        origen.backup(copia)  # copia consistente aunque haya WAL
    copia.close()
    origen.close()
    return destino


def verificar(db, planes) -> list:
    """Compara cada fila insertada contra su origen. Regresa la lista de errores (vacía = todo bien)."""
    errores = []
    for fila, eventos, _ in planes:
        ev = db.query(Evaluacion).filter_by(origen_tabla=fila["origen_tabla"], origen_id=fila["origen_id"]).one_or_none()
        ref = f"{fila['origen_tabla']} #{fila['origen_id']}"
        if ev is None:
            errores.append(f"{ref}: no quedó en evaluaciones")
            continue
        if ev.token_evaluador != fila["token_evaluador"]:
            errores.append(f"{ref}: la liga del evaluador cambió")
        if fila["origen_tabla"] == ORIGEN_EH:
            crudo_o = db.execute(text("SELECT fecha FROM entrevistas_humanas WHERE id = :i"), {"i": fila["origen_id"]}).scalar()
            crudo_d = db.execute(text("SELECT cita_fecha_hora FROM evaluaciones WHERE id = :i"), {"i": ev.id}).scalar()
            if crudo_o != crudo_d and _dt(crudo_o) != _dt(crudo_d):
                errores.append(f"{ref}: la cita no se copió idéntica ({crudo_o!r} → {crudo_d!r})")
        else:
            o = db.get(EvaluacionCandidato, fila["origen_id"])
            if (o.clave_proveedor or "") != ev.clave_proveedor or (o.consentimiento_token or None) != (ev.consentimiento_token or None):
                errores.append(f"{ref}: clave del proveedor o liga de consentimiento no coinciden")
            if bool(o.archivo) != bool(ev.adjuntos) or (o.archivo and ev.adjuntos[0]["archivo"] != o.archivo):
                errores.append(f"{ref}: el adjunto no se conservó")
        n = db.query(EventoEvaluacion).filter_by(evaluacion_id=ev.id).count()
        if n != len(eventos):
            errores.append(f"{ref}: historial incompleto ({n} de {len(eventos)} eventos)")
    return errores


def migrar(db, planes) -> None:
    for fila, eventos, _ in planes:
        ev = Evaluacion(**fila)
        db.add(ev)
        db.flush()
        if not ev.codigo:
            ev.codigo = f"EVA-{50000 + ev.id}"
        for e in eventos:
            db.add(EventoEvaluacion(evaluacion_id=ev.id, cuenta_id=ev.cuenta_id, **e))
    db.flush()


def main() -> int:
    forzar = "--forzar" in sys.argv
    tablas = set(inspect(engine).get_table_names())
    faltan = [t for t in TABLAS_ORIGEN if t not in tablas]
    if faltan:
        print(f"No existen las tablas de origen {faltan}; nada que migrar.")
        return 1
    db = SessionLocal()
    try:
        with engine.connect() as conn:
            huella_antes = huella_tablas(conn)
        planes, avisos, omitidas, ya = construir_plan(db)
        imprimir_resumen(planes, avisos, omitidas, ya)
        if not forzar:
            print("\nSIMULACIÓN: no se escribió nada. Revisa el resumen y corre con --forzar para migrar.")
            return 0
        if not planes:
            print("\nNada por migrar.")
            return 0
        if engine.dialect.name == "sqlite":
            print(f"\nRespaldo: {respaldar_sqlite()}")
        elif "--respaldo-hecho" not in sys.argv:
            print("\nMotor distinto de SQLite: haz tu respaldo (pg_dump) y vuelve a correr con --forzar --respaldo-hecho.")
            return 1
        db.rollback()  # la sesión no guarda nada del plan: todo lo escribe `migrar` en UNA transacción
        Base.metadata.create_all(bind=engine, tables=[Base.metadata.tables["evaluaciones"], Base.metadata.tables["eventos_evaluacion"]])
        migrar(db, planes)
        errores = verificar(db, planes)
        with engine.connect() as conn:
            huella_despues = huella_tablas(conn)
        if huella_despues != huella_antes:
            errores.append("las tablas de origen cambiaron durante la migración")
        if errores:
            db.rollback()
            print(f"\n❌ Verificación fallida ({len(errores)}); se deshizo TODO:")
            for e in errores:
                print("   ·", e)
            return 1
        registrar(db, ACTOR, "evaluaciones_unificadas_migradas", "sistema", "evaluaciones",
                  {"migradas": len(planes), "por_origen": dict(Counter(f["origen_tabla"] for f, _, _ in planes)),
                   "advertencias": avisos, "omitidas": omitidas, "huella_origen": huella_antes})
        db.commit()
        print(f"\n✅ Migradas {len(planes)} evaluaciones; verificación fila por fila OK; tablas de origen intactas.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
