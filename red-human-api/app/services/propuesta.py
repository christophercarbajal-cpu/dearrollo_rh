"""Propuesta de trabajo por WhatsApp (2026-10-10, Cambio 3) — la actividad «Propuesta y aceptación» (`condiciones`).

* Al terminar la selección la actividad queda «Pendiente: capturar condiciones».
* RH captura (precargado: puesto, sueldo y ubicación de la vacante; jefe de la entrevista humana) y pulsa «Enviar
  propuesta»: se guardan las condiciones en el expediente (misma validación que siempre) y sale por el canal del
  candidato con «¿Aceptas? Responde sí o no».
* «sí» explícito → aceptada: la actividad se completa y se piden SOLO los documentos de ingreso que falten.
  «no» → rechazada: se avisa a RH (nunca se descarta solo; RH decide). Cualquier otra cosa → se repite la pregunta.
* RH también puede registrar la respuesta que obtuvo por otro medio (`responder(..., canal="rh")`).

Estado en `Postulacion.analisis["propuesta"]` = {estado: enviada|aceptada|rechazada, datos, enviada_en, enviada_por,
respondida_en, respuesta, canal, legado?}.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import fechas
from ..models import Evaluacion, Postulacion, registrar

CLAVE = "propuesta"
ACTOR = "Red Human (propuesta)"
PREGUNTA = "¿Aceptas? Responde sí o no."


def estado(p: Postulacion) -> dict:
    v = (p.analisis or {}).get(CLAVE)
    return dict(v) if isinstance(v, dict) else {}


def _guardar(p: Postulacion, datos: dict) -> None:
    a = dict(p.analisis or {})
    a[CLAVE] = datos
    p.analisis = a
    flag_modified(p, "analisis")


def esperando_respuesta(p: Postulacion) -> bool:
    return p.activa and estado(p).get("estado") == "enviada"


def _jefe_de_entrevista(db: Session, p: Postulacion) -> str:
    """Jefe directo = quien hizo la entrevista humana (la última con resultado; si no, la última no cancelada)."""
    evs = (db.query(Evaluacion).filter(Evaluacion.postulacion_id == p.id, Evaluacion.tipo == "entrevista_humana",
                                       Evaluacion.estado != "cancelada").order_by(Evaluacion.id.desc()).all())
    ev = next((e for e in evs if e.estado == "con_resultado"), None) or (evs[0] if evs else None)
    return (ev.evaluador_nombre or ev.realizada_por or "") if ev else ""


def precarga(db: Session, p: Postulacion) -> dict:
    """Lo que ya se sabe: lo capturado en el expediente manda; si falta, puesto/sueldo/ubicación de la vacante y el jefe
    de la entrevista humana. `origen` dice de dónde salió cada dato."""
    e = p.expediente
    v = p.vacante
    datos, origen = {}, {}

    def poner(campo, del_exp, respaldo, fuente):
        if (del_exp or "").strip():
            datos[campo], origen[campo] = del_exp.strip(), "expediente"
        elif (respaldo or "").strip():
            datos[campo], origen[campo] = respaldo.strip(), fuente
        else:
            datos[campo] = ""

    sueldo_v = (v.sueldo if v and v.sueldo and v.sueldo != "A convenir" else "") if v else ""
    poner("puesto", e.puesto if e else "", v.titulo if v else "", "vacante")
    poner("sueldo", e.sueldo if e else "", sueldo_v, "vacante")
    poner("ubicacion", e.ubicacion if e else "", (v.ubicacion if v else "") or "", "vacante")
    poner("jefe_directo", e.jefe_directo if e else "", _jefe_de_entrevista(db, p), "entrevista_humana")
    datos["tipo_contratacion"] = (e.tipo_contratacion if e else "") or ""
    datos["fecha_ingreso"] = fechas.dia(e.fecha_ingreso).isoformat() if e and e.fecha_ingreso else ""
    datos["empresa"] = (e.empresa if e else "") or ""
    datos["instrucciones_ingreso"] = (e.instrucciones_ingreso if e else "") or ""
    return {"datos": datos, "origen": origen, "propuesta": publica(p)}


def publica(p: Postulacion) -> Optional[dict]:
    s = estado(p)
    if not s:
        return None
    return {"estado": s.get("estado"), "enviadaEn": s.get("enviada_en"), "enviadaPor": s.get("enviada_por"),
            "respondidaEn": s.get("respondida_en"), "respuesta": s.get("respuesta") or "", "canal": s.get("canal") or "",
            "entregada": s.get("entregada"), "legado": bool(s.get("legado")), "datos": s.get("datos") or {}}


def texto(p: Postulacion) -> str:
    from ..serial import nombre_empresa_candidato

    e = p.expediente
    v = p.vacante
    nombre = (p.nombre or "").split(" ")[0] if p.nombre and not p.nombre.startswith("Candidato") else ""
    empresa = (e.empresa if e and e.empresa else "") or (nombre_empresa_candidato(v) if v else "")
    fecha = ""
    if e and e.fecha_ingreso:
        d = fechas.dia(e.fecha_ingreso)
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        fecha = f"{d.day} de {meses[d.month - 1]} de {d.year}"
    filas = [("Puesto", e.puesto if e else ""), ("Sueldo", e.sueldo if e else ""), ("Ubicación", e.ubicacion if e else ""),
             ("Jefe directo", e.jefe_directo if e else ""), ("Tipo de contratación", e.tipo_contratacion if e else ""),
             ("Fecha de ingreso", fecha)]
    lineas = "\n".join(f"• {k}: {val}" for k, val in filas if (val or "").strip())
    return (f"Hola{(' ' + nombre) if nombre else ''}, esta es la propuesta de trabajo{f' de {empresa}' if empresa else ''}:\n"
            f"{lineas}\n\n{PREGUNTA}")


async def enviar(db: Session, p: Postulacion, actor: str) -> dict:
    """Manda la propuesta (las condiciones ya están guardadas en el expediente) y deja la actividad esperando la
    respuesta del candidato. Reenviar = mismo flujo (la respuesta anterior, si la hubo, se archiva)."""
    from .motor_ruta import _avisar

    e = p.expediente
    previo = estado(p)
    historial = list(previo.get("anteriores") or [])
    if previo.get("estado"):
        historial.append({k: previo.get(k) for k in ("estado", "enviada_en", "respondida_en", "respuesta", "canal")})
    msg = texto(p)
    entregado, detalle = await _avisar(db, p, msg, f"Propuesta de trabajo — {p.vacante.titulo if p.vacante else 'vacante'}", "",
                                       motivo="propuesta", actor=actor)
    ahora = fechas.iso(datetime.now(timezone.utc))
    _guardar(p, {"estado": "enviada", "enviada_en": ahora, "enviada_por": actor, "entregada": entregado, "detalle_envio": detalle[:200],
                 "datos": {"puesto": e.puesto, "sueldo": e.sueldo, "ubicacion": e.ubicacion, "jefe_directo": e.jefe_directo,
                           "tipo_contratacion": e.tipo_contratacion, "fecha_ingreso": fechas.iso(e.fecha_ingreso)} if e else {},
                 "anteriores": historial[-5:]})
    p.historial = list(p.historial or []) + [{"evento": "propuesta_enviada", "usuario": actor, "fecha": ahora,
                                              "texto": f"Propuesta enviada por {actor}" + ("" if entregado else f" (no se entregó: {detalle[:120]})")}]
    registrar(db, actor, "propuesta_enviada", "postulacion", p.codigo, {"entregada": entregado, "detalle": detalle[:200]})
    return {"enviada": entregado, "detalle": detalle, "texto": msg}


async def responder(db: Session, p: Postulacion, acepta: bool, por: str, canal: str, respuesta: str = "") -> dict:
    """Registra la respuesta. Aceptada → avisa el siguiente paso y pide SOLO los documentos que falten. Rechazada → avisa
    a RH (nunca descarta). Después corre la ruta de siempre."""
    from . import proceso as sproc
    from .motor_ruta import _avisar, _disparar_documentos

    s = estado(p)
    ahora = fechas.iso(datetime.now(timezone.utc))
    s.update({"estado": "aceptada" if acepta else "rechazada", "respondida_en": ahora, "respuesta": (respuesta or "")[:300], "canal": canal,
              "registrada_por": por})
    _guardar(p, s)
    p.historial = list(p.historial or []) + [{
        "evento": "propuesta_aceptada" if acepta else "propuesta_rechazada", "usuario": por, "fecha": ahora,
        "texto": ("El candidato aceptó la propuesta" if acepta else "El candidato NO aceptó la propuesta") + (f" ({canal})" if canal else "")}]
    registrar(db, por, "propuesta_aceptada" if acepta else "propuesta_rechazada", "postulacion", p.codigo, {"canal": canal, "respuesta": respuesta[:200]})
    salida = {"estado": s["estado"], "documentos": None, "avisoRh": None}
    if acepta:
        faltan = list(p.expediente.pendientes) if p.expediente else []
        if canal != "rh":
            await _avisar(db, p, "Gracias, registramos tu respuesta. Siguiente paso: " +
                          ("tus documentos de ingreso; en seguida te mando la liga para subir solo los que faltan." if faltan
                           else "la firma de tu contrato; el equipo de RH te escribirá por aquí."),
                          "Tu propuesta de trabajo", "", motivo="propuesta", actor=ACTOR)
        if faltan:
            paso = next((x for x in (p.proceso or {}).get("pasos", []) if x.get("tipo") == "documentos"), {"id": "documentos"})
            ok, detalle, entregado = await _disparar_documentos(db, p, paso)
            salida["documentos"] = {"ok": ok, "detalle": detalle, "enviado": entregado, "pendientes": faltan}
    else:
        if canal != "rh":
            await _avisar(db, p, "Gracias por avisarnos. Lo comparto con el equipo de RH, que se pondrá en contacto contigo.",
                          "Tu propuesta de trabajo", "", motivo="propuesta", actor=ACTOR)
        salida["avisoRh"] = await avisar_rh_rechazo(db, p, respuesta)
    db.commit()
    await sproc.avanzar_seguro(db, p)
    return salida


async def avisar_rh_rechazo(db: Session, p: Postulacion, respuesta: str) -> dict:
    """Correo al responsable de la vacante (o al correo de comunicación de la Cuenta). Nunca truena."""
    from .correo import enviar_correo
    from .notificaciones import _html

    v = p.vacante
    destino = (v.responsable.correo if v is not None and v.responsable is not None and v.responsable.correo else "") or \
              ((p.cuenta.correo_comunicacion or "") if p.cuenta is not None else "")
    texto = (f"{p.nombre} ({p.codigo}) no aceptó la propuesta de trabajo para {v.titulo if v else 'la vacante'}"
             + (f". Su respuesta: «{respuesta[:200]}»" if respuesta else "") + ". Revisa la ficha para decidir el siguiente paso.")
    if not destino:
        registrar(db, ACTOR, "propuesta_rechazada_sin_aviso_rh", "postulacion", p.codigo, {"motivo": "sin correo de RH"})
        return {"enviado": False, "detalle": "Sin correo del responsable ni de la Cuenta"}
    try:
        asunto, html = _html(f"Propuesta no aceptada — {p.nombre}", texto)
        r = await enviar_correo(destino, asunto, html)
    except Exception as ex:  # noqa: BLE001
        r = {"enviado": False, "detalle": str(ex)[:200]}
    registrar(db, ACTOR, "propuesta_rechazada_aviso_rh", "postulacion", p.codigo, {"destino": destino, "enviado": bool(r.get("enviado"))})
    return {"enviado": bool(r.get("enviado")), "destino": destino, "detalle": r.get("detalle", "")}


async def turno(db: Session, p: Postulacion, texto_candidato: str, canal: str) -> dict:
    """Mensaje del candidato mientras la propuesta espera respuesta: «sí» explícito / «no» / se repite la pregunta."""
    from . import agenda_chat
    from ..routers.candidatos import _enviar_whatsapp, guardar_mensaje

    if agenda_chat.es_no(texto_candidato):
        r = await responder(db, p, False, p.nombre or "candidato", canal, texto_candidato)
        return {"respuesta": "", "propuesta": r["estado"], "clasificacion": None, "ia": False}
    if agenda_chat.es_si_explicito(texto_candidato):
        r = await responder(db, p, True, p.nombre or "candidato", canal, texto_candidato)
        return {"respuesta": "", "propuesta": r["estado"], "clasificacion": None, "ia": False}
    msg = f"Para continuar necesito tu respuesta a la propuesta de trabajo. {PREGUNTA}"
    envio = await _enviar_whatsapp(p, msg, canal)
    guardar_mensaje(db, p, "assistant", msg, canal, envio)
    db.commit()
    return {"respuesta": msg, "propuesta": "enviada", "clasificacion": None, "ia": False, "whatsapp": envio}


def marcar_legado(db: Session) -> int:
    """Una sola vez al arrancar: las postulaciones que YA tenían condiciones guardadas antes de este flujo conservan
    «Propuesta y aceptación» cumplida (se marca la propuesta como aceptada «legado»; nada más cambia)."""
    from ..models import Bitacora, Expediente

    marca = "propuesta_legado_2026_10_10"
    if db.query(Bitacora).filter(Bitacora.accion == marca).first():
        return 0
    n = 0
    filas = (db.query(Postulacion).join(Expediente, Expediente.postulacion_id == Postulacion.id)
             .filter(Expediente.condiciones_guardadas_en.isnot(None)).all())
    for p in filas:
        if estado(p):
            continue
        _guardar(p, {"estado": "aceptada", "legado": True, "respondida_en": fechas.iso(p.expediente.condiciones_guardadas_en),
                     "canal": "legado"})
        n += 1
    registrar(db, "sistema", marca, "sistema", "propuesta", {"postulaciones": n})
    return n
