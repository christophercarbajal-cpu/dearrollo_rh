"""Operación de UNA actividad de la ruta desde la ficha (2026-10-08): Iniciar · Reenviar · Registrar resultado.

* Iniciar en UN paso: ejecuta lo que la ruta ya trae configurado (batería, evaluador responsable, tipo de entrevista).
  Solo si falta un dato CRÍTICO (evaluador, correo, pruebas) lo pide (`faltan`) — sin crear nada a medias ni duplicar:
  si la actividad ya tiene su evaluación viva, se regresa esa.
* Reenvíos granulares: «Reenviar al candidato / al entrevistador / al médico / al evaluador». Cada uno manda SU liga y
  deja su trazabilidad por destinatario; ninguno cambia el estado ni reinicia el avance de la actividad.
* Registrar resultado: lo hecho FUERA del sistema (examen físico, llamada, entrevista presencial) se captura en la
  MISMA evaluación de la actividad (o en una nueva de «registro directo» ligada a ella, nunca un duplicado), con el
  dictamen, quién la aplicó y quién la capturó. La captura manual tiene prioridad sobre un webhook tardío.
Nada de esto mueve la etapa por sí mismo: después corre el motor de siempre (`proceso.avanzar_seguro`).
"""

from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import TIPOS_PASO_EVALUACION, Evaluacion, Postulacion, Usuario, registrar
from . import evaluaciones as sev
from . import proceso as sproc

# Tipos en los que quien la aplica es casi siempre EXTERNO (médico, empresa de estudios): sin evaluador explícito
# en la ruta se pide; en los demás, RH (responsable de la vacante o quien la inicia) la conduce.
TIPOS_EVALUADOR_EXTERNO = ("medica", "socioeconomica")


def _paso_vivo(p: Postulacion, paso_id: str) -> dict:
    try:
        sproc._paso(p, paso_id)
    except sproc.ErrorProceso as e:
        raise HTTPException(e.status, e.mensaje)
    x = next((y for y in sproc.estado_pasos(p) if y["id"] == paso_id), None)
    if x is None:
        raise HTTPException(404, "Actividad no encontrada.")
    return x


def evaluacion_de_paso(db: Session, p: Postulacion, paso_id: str) -> Optional[Evaluacion]:
    """La evaluación que hoy cumple la actividad (la misma que lee el seguimiento)."""
    evs = db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id).order_by(Evaluacion.id).all()
    return sproc.asignar_evaluaciones(p.proceso["pasos"], evs).get(paso_id)


def marcar_pendiente_correo(p: Postulacion, paso_id: str) -> None:
    """La psicometría de ese paso quiso salir y no tenía correo: queda pendiente de reanudarse al capturarlo."""
    from sqlalchemy.orm.attributes import flag_modified

    a = dict(p.analisis or {})
    pend = list(a.get("psicometria_pendiente_correo") or [])
    if paso_id not in pend:
        pend.append(paso_id)
    a["psicometria_pendiente_correo"] = pend
    p.analisis = a
    flag_modified(p, "analisis")


def _quitar_pendiente_correo(p: Postulacion, paso_id: str) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    a = dict(p.analisis or {})
    pend = [x for x in (a.get("psicometria_pendiente_correo") or []) if x != paso_id]
    a["psicometria_pendiente_correo"] = pend
    p.analisis = a
    flag_modified(p, "analisis")


async def reanudar_por_correo(db: Session, p: Postulacion, u, cuenta, paso_extra: str = "") -> List[dict]:
    """Al guardar el correo del candidato: retoma AUTOMÁTICAMENTE el envío de las psicometrías que se detuvieron por
    falta de correo (y la que RH pidió al pulsar «Agregar correo»). `iniciar` nunca duplica: si ya hay asignación viva,
    regresa esa. Regresa [{paso, nombre, ok, mensaje}]."""
    if not (p.correo or "").strip() or not p.activa or not sproc.tiene_proceso(p):
        return []
    pasos = list((p.analisis or {}).get("psicometria_pendiente_correo") or [])
    if paso_extra and paso_extra not in pasos:
        pasos.append(paso_extra)
    salida = []
    for paso_id in pasos:
        paso = next((x for x in p.proceso.get("pasos", []) if x["id"] == paso_id and x["tipo"] == "psicometrica"), None)
        if paso is None:
            _quitar_pendiente_correo(p, paso_id)
            continue
        try:
            r = await iniciar(db, p, paso_id, u, cuenta, {})
            ok = bool(r.get("iniciada"))
            salida.append({"paso": paso_id, "nombre": paso["nombre"], "ok": ok, "mensaje": r.get("mensaje") or ""})
        except HTTPException as ex:
            salida.append({"paso": paso_id, "nombre": paso["nombre"], "ok": False, "mensaje": str(ex.detail)})
            ok = False
        db.refresh(p)
        if ok:
            _quitar_pendiente_correo(p, paso_id)
    db.commit()
    return salida


def _admin_de_cuenta(db: Session, cuenta_id: int) -> Optional[int]:
    from ..models import UsuarioCuenta

    fila = (db.query(Usuario).join(UsuarioCuenta, UsuarioCuenta.usuario_id == Usuario.id)
            .filter(UsuarioCuenta.cuenta_id == cuenta_id, Usuario.activo.is_(True)).order_by(Usuario.id).first())
    return fila.id if fila else None


def _evaluador_configurado(db: Session, p: Postulacion, paso: dict, u: Usuario) -> Optional[dict]:
    """Evaluador que ya trae la ruta: usuario responsable del paso; si el responsable es RH, el responsable de la vacante
    (o quien inicia). None = hay que pedirlo (externos: médico, socioeconómico, entrevista sin entrevistador)."""
    r = paso.get("responsable") or {}
    if r.get("tipo") == "usuario" and r.get("usuario_id"):
        return {"tipo": "interno", "usuario_id": r["usuario_id"]}
    if paso["tipo"] in TIPOS_EVALUADOR_EXTERNO or paso["tipo"] == "entrevista_humana" or r.get("tipo") == "externo":
        return None
    v = p.vacante
    # el motor automático no es una persona: responsable de la vacante o, si no hay, un usuario de la Cuenta
    uid = (v.responsable_id if v is not None and v.responsable_id else None) or getattr(u, "id", None) or _admin_de_cuenta(db, p.cuenta_id)
    return {"tipo": "interno", "usuario_id": uid} if uid else None


async def iniciar(db: Session, p: Postulacion, paso_id: str, u: Usuario, cuenta, datos: dict) -> dict:
    """«Iniciar» de la actividad. Regresa {iniciada, evaluacion?, faltan?, mensaje, resultados?}."""
    if not p.activa:
        raise HTTPException(409, "La postulación está cerrada.")
    x = _paso_vivo(p, paso_id)
    paso = sproc._paso(p, paso_id)
    if x["estado"] in ("omitida", "cancelada"):
        raise HTTPException(409, f"«{x['nombre']}» está omitida: reactívala para iniciarla.")
    if x["estado"] == "completada":
        raise HTTPException(409, f"«{x['nombre']}» ya está completada.")
    tipo = paso["tipo"]
    if tipo not in TIPOS_PASO_EVALUACION:
        return await _iniciar_otra(db, p, paso, u, cuenta)
    ev = evaluacion_de_paso(db, p, paso_id)
    if ev is not None and ev.estado != "cancelada":
        # ya iniciada: nunca se crea otra (para avisar de nuevo están los reenvíos del «…»)
        return {"iniciada": True, "yaExistia": True, "evaluacion": ev.codigo,
                "mensaje": f"«{x['nombre']}» ya estaba iniciada ({ev.codigo}). Para volver a avisar usa «Reenviar»."}
    if tipo == "psicometrica":
        return await _iniciar_psicometria(db, p, paso, u, cuenta, datos)
    from ..routers.evaluaciones import CitaIn, CrearEvaluacionIn, EvaluadorIn, crear_evaluacion

    forma = (datos.get("forma") or "").strip() or "asignada"
    evaluador = datos.get("evaluador") or None
    if forma == "asignada" and not evaluador:
        evaluador = _evaluador_configurado(db, p, paso, u)
        if evaluador is None:
            quien = {"medica": "el médico", "entrevista_humana": "el entrevistador"}.get(tipo, "quién la aplicará")
            return {"iniciada": False, "faltan": ["evaluador"],
                    "mensaje": f"Para iniciar «{x['nombre']}» solo falta elegir {quien}."}
    cuerpo = CrearEvaluacionIn(
        tipo=tipo, forma=forma, paso_id=paso_id, nombre=paso["nombre"] if tipo == "otra" else "",
        evaluador=EvaluadorIn(**evaluador) if evaluador else None,
        instrucciones=str(datos.get("instrucciones") or ""),
        liga_externa_candidato=str(datos.get("liga_externa_candidato") or ""),
        cita=CitaIn(**datos["cita"]) if isinstance(datos.get("cita"), dict) else None,
    )
    r = await crear_evaluacion(p.codigo, cuerpo, db=db, u=u, cuenta=cuenta)
    return {"iniciada": True, "evaluacion": r["evaluacion"]["codigo"], "resultados": r.get("resultados") or [],
            "advertencias": r.get("advertencias") or [], "mensaje": f"«{x['nombre']}» iniciada."}


async def _iniciar_psicometria(db: Session, p: Postulacion, paso: dict, u: Usuario, cuenta, datos: dict) -> dict:
    from ..routers.evaluaciones import AsignarPsicometriaIn, asignar_psicometria, bateria_predeterminada
    from . import psicometricas as psi

    ids = [int(i) for i in (datos.get("prueba_ids") or [])] or bateria_predeterminada(db, p, paso)[0]
    faltan = []
    if not ids:
        faltan.append("pruebas")
    correo = (datos.get("correo") or "").strip() or None
    if psi.configurado() and not ((p.correo or "").strip() or correo):
        faltan.append("correo")
    if "correo" in faltan:
        marcar_pendiente_correo(p, paso["id"])  # al guardar el correo se retoma sola (sin pedir nada más)
        db.commit()
    if faltan:
        return {"iniciada": False, "faltan": faltan,
                "mensaje": "Para enviar la prueba solo falta " + " y ".join({"pruebas": "elegir las pruebas", "correo": "el correo del candidato"}[f] for f in faltan) + "."}
    r = await asignar_psicometria(p.codigo, AsignarPsicometriaIn(prueba_ids=ids, paso_id=paso["id"], correo=correo), db=db, u=u, cuenta=cuenta)
    return {"iniciada": True, "evaluacion": r["evaluacion"]["codigo"], "resultados": r.get("resultados") or [],
            "advertencias": r.get("advertencias") or [], "simulado": bool(r.get("simulado")),
            "mensaje": r.get("aviso") or "Prueba generada y enviada al candidato."}


async def _iniciar_otra(db: Session, p: Postulacion, paso: dict, u: Usuario, cuenta) -> dict:
    """Actividades que no son evaluación: la liga de documentos y la Entrevista Red Human se mandan aquí mismo; el resto
    se trabaja en su pestaña (condiciones, expediente, alta), así que «Iniciar» no aplica."""
    if paso["tipo"] == "solicitud_documentos":
        from ..routers.candidatos import solicitar_documentos

        r = await solicitar_documentos(p.codigo, None, db=db, u=u, cuenta=cuenta)
        return {"iniciada": True, "resultados": r.get("resultados") or [], "mensaje": "Liga de documentos enviada."}
    if paso["tipo"] == "entrevista_agente":
        return await reenviar(db, p, paso["id"], "candidato", u, cuenta, crear=True)
    raise HTTPException(409, f"«{paso['nombre']}» se trabaja desde su pestaña; no se inicia desde aquí.")


# ------------------------------------------------------------ reenvíos granulares


async def reenviar(db: Session, p: Postulacion, paso_id: str, a: str, u: Usuario, cuenta, crear: bool = False) -> dict:
    """Manda la liga que le toca a ESE destinatario (candidato / entrevistador / médico / evaluador). No toca el estado."""
    x = _paso_vivo(p, paso_id)
    paso = sproc._paso(p, paso_id)
    opcion = next((r for r in x.get("reenvios") or [] if r["a"] == a), None)
    if opcion is None and not (crear and paso["tipo"] == "entrevista_agente"):
        raise HTTPException(409, f"«{x['nombre']}» no tiene nada que enviar {sproc.TEXTO_DESTINATARIO.get(a, 'a ese destinatario')} en este momento.")
    motivo = opcion["motivo"] if opcion else "entrevista"
    if paso["tipo"] in TIPOS_PASO_EVALUACION:
        from ..routers.evaluaciones import _enviar_liga_candidato

        ev = evaluacion_de_paso(db, p, paso_id)
        if ev is None:
            raise HTTPException(409, "La actividad aún no se inicia.")
        if a == "candidato" and motivo in ("consentimiento", "referencias", "proveedor", "otro_sistema"):
            resultados = await _enviar_liga_candidato(db, ev, p, u, motivo, cuenta)
        elif a == "candidato":  # la cita de una entrevista/evaluación presencial
            resultados = await sev.notificar(db, ev, p, "evaluacion_asignada", u.nombre, audiencias={"candidato"})
        else:
            resultados = await sev.notificar(db, ev, p, "evaluacion_asignada", u.nombre, audiencias={"entrevistador"})
    elif paso["tipo"] == "entrevista_agente":
        resultados = await _reenviar_entrevista(db, p, paso, u, crear)
    elif paso["tipo"] in ("solicitud_documentos", "documentos"):
        from ..routers.candidatos import solicitar_documentos

        r = await solicitar_documentos(p.codigo, None, db=db, u=u, cuenta=cuenta)
        return {"ok": True, "resultados": r.get("resultados") or [], "enviado": any(y.get("enviado") for y in r.get("resultados") or [])}
    else:
        raise HTTPException(409, "Esta actividad no envía ligas.")
    registrar(db, u.nombre, "actividad_reenviada", "postulacion", p.codigo,
              {"paso": paso_id, "a": a, "motivo": motivo, "envios": resultados, "correo_rh": u.correo})
    db.commit()
    return {"ok": True, "resultados": resultados, "enviado": any(y.get("enviado") for y in resultados or [])}


async def _reenviar_entrevista(db: Session, p: Postulacion, paso: dict, u: Usuario, crear: bool) -> List[dict]:
    from ..config import settings

    from . import motor_ruta

    e = next((y for y in reversed(list(p.entrevistas or [])) if y.estado != "evaluada"), None)
    if e is None:
        if not crear:
            raise HTTPException(409, "La Entrevista Red Human aún no tiene sala.")
        from .entrevistas import crear_entrevista_para_candidato

        e, _ = crear_entrevista_para_candidato(db, p, u.nombre)
        db.flush()
    liga = f"{settings.app_url}/entrevista/{e.token}"
    nombre = (p.nombre or "").split(" ")[0] if p.nombre and not p.nombre.startswith("Candidato") else ""
    vacante = p.vacante.titulo if p.vacante else "la vacante"
    texto = (f"¡Hola{(' ' + nombre) if nombre else ''}! Te compartimos de nuevo la liga de tu entrevista con Red Human para "
             f"{vacante}. Entra cuando gustes desde tu celular o computadora: {liga}")
    entregado, detalle = await motor_ruta._avisar(db, p, texto, f"Tu entrevista para {vacante}", liga, motivo="entrevista",
                                                  paso_id=paso["id"], actor=u.nombre)
    return [{"destinatario": "candidato", "canal": detalle if entregado else "", "enviado": entregado, "detalle": "" if entregado else detalle}]


# ------------------------------------------------------------ captura manual (fuera del sistema)


def referencias_manuales(lista, u: Usuario) -> List[dict]:
    """Contactos verificados FUERA del sistema (por teléfono, en papel): mismo formato que los que captura el candidato,
    con su dictamen ya puesto por quien registra."""
    from datetime import datetime, timezone
    import secrets as _s

    from .. import fechas
    from ..models import DICTAMENES_GENERALES
    from .telegram import telefono_10

    salida = []
    for i, r in enumerate(lista or []):
        if not isinstance(r, dict):
            continue
        nombre = " ".join(str(r.get("nombre") or "").split())[:150]
        if not nombre:
            continue
        dictamen = str(r.get("dictamen") or "").strip()
        if dictamen and dictamen not in DICTAMENES_GENERALES:
            raise HTTPException(400, f"Referencia {i + 1}: dictamen inválido.")
        contactado = r.get("contactado")
        salida.append({
            "id": _s.token_hex(4), "nombre": nombre, "empresa": str(r.get("empresa") or "").strip()[:150],
            "puesto": str(r.get("puesto") or "").strip()[:150], "relacion": str(r.get("relacion") or "").strip()[:100],
            "telefono": telefono_10(str(r.get("telefono") or "")) if str(r.get("telefono") or "").strip() else "",
            "correo": str(r.get("correo") or "").strip().lower()[:200],
            "contactado": True if contactado is None else bool(contactado), "dictamen": dictamen,
            "comentario": str(r.get("comentario") or "").strip()[:2000], "dictaminado_por": u.nombre[:150],
            "dictaminado_en": fechas.iso(datetime.now(timezone.utc)), "capturada_por": "rh",
        })
    return salida


async def registrar_resultado(db: Session, p: Postulacion, paso_id: str, u: Usuario, *, conclusion: str, comentarios: str,
                              realizada_por: str, archivos, referencias: Optional[list] = None) -> dict:
    from ..routers.evaluaciones import _adjuntos_subidos, _recalcular_indicador

    x = _paso_vivo(p, paso_id)
    paso = sproc._paso(p, paso_id)
    ev = evaluacion_de_paso(db, p, paso_id)
    if ev is not None and ev.estado == "cancelada":
        ev = None
    if not sproc.puede_registrar_resultado_paso(paso, x["estado"], ev):
        if paso["tipo"] == "medica":
            raise HTTPException(409, "La evaluación médica necesita el consentimiento expreso del candidato antes de registrar su resultado.")
        raise HTTPException(409, f"«{x['nombre']}» no acepta un resultado en este momento.")
    if paso["tipo"] == "medica" and not u.puede_ver_informe_medico():
        raise HTTPException(403, "Registrar el resultado médico requiere el permiso de informes médicos.")
    if paso["tipo"] != "entrevista_humana" and not p.consentimiento:
        raise HTTPException(409, "Falta el consentimiento de privacidad del candidato (LFPDPPP).")
    nueva = ev is None
    if nueva:
        # actividad hecha fuera del sistema sin haberla iniciado: registro directo LIGADO a la actividad (nunca otro)
        ev = sev.nueva(p, p.cuenta_id, u.nombre, u.id, tipo=paso["tipo"], forma="registro_directo", paso_id=paso_id,
                       nombre=paso["nombre"] if paso["nombre"] != sproc.TIPOS_PASO[paso["tipo"]]["nombre"] else "")
        sev.asegurar_ligas(ev)
        db.add(ev)
        db.flush()
        sev.asignar_codigo(ev)
        sev.evento(db, ev, "creada", u.nombre, a=ev.estado, usuario_id=u.id, tipo=ev.tipo, forma=ev.forma, via="registrar_resultado")
    adjuntos = await _adjuntos_subidos(ev, archivos, u.nombre, "sistema")
    verificadas = referencias_manuales(referencias, u) if paso["tipo"] == "referencias" else []
    if verificadas:
        # referencias verificadas fuera del sistema: se SUMAN a las que haya capturado el candidato (nada se pierde)
        from datetime import datetime, timezone

        ev.referencias = list(ev.referencias or []) + verificadas
        ev.referencias_capturadas_en = ev.referencias_capturadas_en or datetime.now(timezone.utc)
        sev.evento(db, ev, "referencias_capturadas", u.nombre, "sistema", total=len(verificadas), via="registro_manual")
        if not (comentarios or "").strip():
            comentarios = f"{len(verificadas)} referencia(s) verificada(s) fuera del sistema."
    try:
        accion = sev.registrar_resultado(db, ev, actor=u.nombre, canal="sistema", conclusion=conclusion, comentarios=comentarios,
                                         realizada_por=realizada_por, adjuntos=adjuntos, version=None, modo="registrar", usuario_id=u.id)
    except sev.ErrorEvaluacion as e:
        db.rollback()
        raise HTTPException(e.status, e.mensaje)
    _recalcular_indicador(p)
    registrar(db, u.nombre, "actividad_resultado_manual", "postulacion", p.codigo,
              {"paso": paso_id, "evaluacion": ev.codigo, "conclusion": ev.conclusion, "realizada_por": ev.realizada_por,
               "capturada_por": u.nombre, "nueva": nueva, "adjuntos": len(adjuntos), "correo_rh": u.correo})
    db.commit()
    await sproc.avanzar_seguro(db, p)
    return {"evaluacion": ev.codigo, "accion": accion}
