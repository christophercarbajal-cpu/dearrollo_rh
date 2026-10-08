"""Trazabilidad de envíos POR DESTINATARIO (2026-10-08).

«Enviada» dejó de ser un estado genérico de la actividad. Cada aviso (liga de la prueba, consentimiento, liga del
evaluador/médico, captura de referencias, sala de la entrevista, documentos) deja en `envios_actividad` una fila por
canal con su estado real:

* intento   — se intentó (la fila nace ANTES de llamar al canal; si el proceso muere a medias, así se queda).
* enviado   — el canal lo ACEPTÓ (Meta/Telegram/Resend respondieron OK).
* entregado — el canal CONFIRMÓ la entrega (acuse de WhatsApp «delivered»/«read»).
* fallido   — el canal lo rechazó (o el acuse llegó como «failed»).

Una llamada de envío = un `lote`; lo que se muestra por destinatario es su ÚLTIMO lote. Nada de esto mueve etapas ni
cambia el estado de la actividad: un reenvío solo agrega un lote nuevo. Registros previos a esta tabla se leen de los
eventos «envio» de la evaluación (compatibilidad, sin migrar).
"""

import secrets
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from .. import fechas
from ..models import DESTINATARIOS_ENVIO, ESTADOS_ENVIO, EnvioActividad, Evaluacion, EventoEvaluacion, Postulacion

PRIORIDAD = ("entregado", "enviado", "intento", "fallido")  # estado agregado de un lote: el «mejor» de sus canales


def destinatario_evaluador(ev: Optional[Evaluacion]) -> str:
    """Cómo se llama a quien realiza la evaluación: médico, entrevistador o evaluador."""
    if ev is None:
        return "evaluador"
    if ev.tipo == "medica":
        return "medico"
    return "entrevistador" if ev.tipo == "entrevista_humana" else "evaluador"


def _normalizar_destinatario(valor: str, ev: Optional[Evaluacion]) -> Optional[str]:
    """None = no es un destinatario trazable (p. ej. «sistema» cuando todo el aviso falló antes de salir)."""
    valor = (valor or "").strip()
    if valor in ("entrevistador", "evaluador", "medico"):
        return destinatario_evaluador(ev) if ev is not None else valor
    return valor if valor in DESTINATARIOS_ENVIO else None


def _tablas_listas(db: Session) -> bool:
    from ..services.modulos_rh import disponible

    try:
        return bool(disponible())
    except Exception:  # noqa: BLE001
        return True


def intento(db: Session, p: Postulacion, *, destinatario: str, motivo: str, ev: Optional[Evaluacion] = None, paso_id: str = "",
            actor: str = "", nombre: str = "") -> Optional[EnvioActividad]:
    """Fila «intento» ANTES de llamar al canal (la cierra `cerrar`). None si la tabla no está disponible."""
    if not _tablas_listas(db):
        return None
    fila = EnvioActividad(
        cuenta_id=p.cuenta_id, postulacion_id=p.id, evaluacion_id=ev.id if ev is not None else None,
        paso_id=paso_id or (ev.paso_id if ev is not None else "") or "", lote=secrets.token_hex(8),
        destinatario=_normalizar_destinatario(destinatario, ev) or "candidato", destinatario_nombre=(nombre or "")[:150],
        motivo=motivo or "aviso", estado="intento", actor=(actor or "")[:150],
    )
    db.add(fila)
    db.flush()
    return fila


def cerrar(db: Session, fila: Optional[EnvioActividad], resultados: Iterable[dict]) -> None:
    """Convierte el intento en una fila por canal con su estado (enviado | fallido). Sin resultados = fallido."""
    if fila is None:
        return
    filas = [r for r in (resultados or []) if isinstance(r, dict)]
    if not filas:
        filas = [{"canal": "", "destino": "", "enviado": False, "detalle": "No hay canal para avisarle."}]
    for i, r in enumerate(filas):
        destino = fila if i == 0 else EnvioActividad(
            cuenta_id=fila.cuenta_id, postulacion_id=fila.postulacion_id, evaluacion_id=fila.evaluacion_id, paso_id=fila.paso_id,
            lote=fila.lote, destinatario=fila.destinatario, destinatario_nombre=fila.destinatario_nombre, motivo=fila.motivo,
            actor=fila.actor,
        )
        destino.canal = str(r.get("proveedor") if r.get("proveedor") == "telegram" else (r.get("canal") or ""))[:20]
        destino.destino = str(r.get("destino") or "")[:200]
        destino.estado = "enviado" if r.get("enviado") else "fallido"
        destino.detalle = str(r.get("detalle") or "")[:1000]
        destino.mensaje_id = str(r.get("wa_id") or r.get("mensaje_id") or r.get("id") or "")[:120]
        if i:
            db.add(destino)


def registrar(db: Session, p: Postulacion, resultados: Iterable[dict], *, motivo: str, ev: Optional[Evaluacion] = None,
              paso_id: str = "", actor: str = "", destinatario: Optional[str] = None) -> None:
    """Registra los resultados de UNA llamada de envío (ya hecha). Agrupa por destinatario (un lote por cada uno): así
    un aviso que salió al candidato y al entrevistador queda trazado por separado. Nunca lanza."""
    try:
        grupos: Dict[str, List[dict]] = {}
        for r in resultados or []:
            if not isinstance(r, dict):
                continue
            d = _normalizar_destinatario(destinatario or r.get("destinatario") or "candidato", ev)
            if d is None:
                continue
            grupos.setdefault(d, []).append(r)
        if not grupos and destinatario and _normalizar_destinatario(destinatario, ev):
            grupos[_normalizar_destinatario(destinatario, ev)] = []
        for d, filas in grupos.items():
            nombre = (ev.evaluador_nombre if ev is not None and d in ("evaluador", "entrevistador", "medico") else
                      (p.nombre or "") if d == "candidato" else "")
            mot = motivo
            if d in ("evaluador", "entrevistador", "medico") and motivo in ("cita", "aviso"):
                mot = "evaluador"
            cerrar(db, intento(db, p, destinatario=d, motivo=mot, ev=ev, paso_id=paso_id, actor=actor, nombre=nombre), filas)
    except Exception as ex:  # noqa: BLE001 — la trazabilidad nunca rompe el envío que la origina
        print(f"[envios] no se pudo registrar el envío ({motivo}): {ex}")


def actualizar_por_mensaje(db: Session, mensaje_id: str, estado_canal: str, detalle: str = "") -> int:
    """Acuse del canal (WhatsApp statuses): delivered/read → entregado; failed → fallido. Regresa filas tocadas."""
    if not mensaje_id or not _tablas_listas(db):
        return 0
    nuevo = {"delivered": "entregado", "read": "entregado", "failed": "fallido"}.get((estado_canal or "").lower())
    if not nuevo:
        return 0
    filas = db.query(EnvioActividad).filter(EnvioActividad.mensaje_id == mensaje_id).all()
    for f in filas:
        if f.estado == "entregado" and nuevo == "fallido":
            continue  # un acuse tardío de error no deshace una entrega confirmada
        f.estado = nuevo
        if nuevo == "entregado" and not f.entregado_en:
            f.entregado_en = datetime.now(timezone.utc)
        if detalle:
            f.detalle = detalle[:1000]
    return len(filas)


# ------------------------------------------------------------ lectura


def filas_de(db: Session, p: Postulacion) -> List[EnvioActividad]:
    if db is None or not _tablas_listas(db):
        return []
    try:
        return db.query(EnvioActividad).filter(EnvioActividad.postulacion_id == p.id).order_by(EnvioActividad.id).all()
    except Exception:  # noqa: BLE001 — tabla sin crear en una base vieja: se lee el legado
        return []


def _estado_lote(filas: List[EnvioActividad]) -> str:
    estados = {f.estado for f in filas}
    return next((e for e in PRIORIDAD if e in estados), "fallido")


def _resumen_lote(filas: List[EnvioActividad], lotes: int) -> dict:
    estado = _estado_lote(filas)
    f0 = filas[0]
    return {
        "estado": estado, "estadoTexto": ESTADOS_ENVIO[estado], "fecha": fechas.iso(f0.creado_en), "motivo": f0.motivo,
        "nombre": f0.destinatario_nombre or "", "intentos": lotes, "por": f0.actor or "",
        "canales": [{"canal": f.canal, "destino": f.destino, "estado": f.estado, "detalle": (f.detalle or "")[:200]} for f in filas],
    }


def resumen(filas: List[EnvioActividad], *, ev: Optional[Evaluacion] = None, motivos: Optional[tuple] = None) -> Dict[str, dict]:
    """{destinatario: último lote} de una evaluación (`ev`) o de los avisos de la postulación con esos `motivos`."""
    if ev is not None:
        propias = [f for f in filas if f.evaluacion_id == ev.id]
    else:
        propias = [f for f in filas if f.evaluacion_id is None and (motivos is None or f.motivo in motivos)]
    por_dest: Dict[str, Dict[str, List[EnvioActividad]]] = {}
    for f in propias:
        por_dest.setdefault(f.destinatario, {}).setdefault(f.lote, []).append(f)
    salida = {}
    for d, lotes in por_dest.items():
        ordenados = sorted(lotes.values(), key=lambda fs: max(x.id for x in fs))
        salida[d] = _resumen_lote(sorted(ordenados[-1], key=lambda x: x.id), len(lotes))
        # último lote de CADA motivo (consentimiento, liga de la prueba, referencias…): el cuello de botella de la
        # actividad se mide contra lo que le toca a ese destinatario, no contra cualquier aviso
        salida[d]["porMotivo"] = {fs[0].motivo: _resumen_lote(sorted(fs, key=lambda x: x.id), 0) for fs in ordenados}
    return salida


def estado_de(resumen_: Dict[str, dict], destinatario: str, motivos: Optional[tuple] = None) -> Optional[str]:
    """Estado del último envío a ese destinatario (de esos motivos): entregado | enviado | intento | fallido | None."""
    d = (resumen_ or {}).get(destinatario)
    if not d:
        return None
    if not motivos:
        return d["estado"]
    pm = d.get("porMotivo") or {}
    lotes = [pm[m] for m in motivos if m in pm]
    if not lotes:
        return None
    return max(lotes, key=lambda x: x.get("fecha") or "")["estado"]


def resumen_legado(db: Session, ev: Evaluacion, eventos: Optional[List[EventoEvaluacion]] = None) -> Dict[str, dict]:
    """Evaluaciones anteriores a la tabla: el último evento «envio» por destinatario (solo enviado / fallido).
    `eventos`: los «envio» de esta evaluación ya precargados en bloque (tablero); sin ellos se consultan."""
    salida: Dict[str, dict] = {}
    if eventos is None:
        try:
            eventos = (db.query(EventoEvaluacion).filter(EventoEvaluacion.evaluacion_id == ev.id, EventoEvaluacion.accion == "envio")
                       .order_by(EventoEvaluacion.id).all())
        except Exception:  # noqa: BLE001
            return salida
    for e in eventos:
        d = e.detalle or {}
        por: Dict[str, List[dict]] = {}
        for r in d.get("envios") or []:
            if isinstance(r, dict):
                dest = _normalizar_destinatario(r.get("destinatario") or "candidato", ev)
                if dest:
                    por.setdefault(dest, []).append(r)
        for dest, rs in por.items():
            ok = any(r.get("enviado") for r in rs)
            previo = salida.get(dest)
            motivo = d.get("liga") or ("evaluador" if dest != "candidato" else "aviso")
            lote = {
                "estado": "enviado" if ok else "fallido", "estadoTexto": "Enviado" if ok else "Fallido", "fecha": fechas.iso(e.fecha),
                "motivo": d.get("liga") or "aviso", "nombre": "", "intentos": (previo or {}).get("intentos", 0) + 1, "por": e.actor or "",
                "canales": [{"canal": r.get("canal") or "", "destino": r.get("destino") or "", "estado": "enviado" if r.get("enviado") else "fallido",
                             "detalle": str(r.get("detalle") or "")[:200]} for r in rs],
            }
            lote["motivo"] = motivo
            lote["porMotivo"] = {**((previo or {}).get("porMotivo") or {}), motivo: dict(lote)}
            salida[dest] = lote
    return salida


def de_evaluacion(db: Session, ev: Evaluacion, filas: Optional[List[EnvioActividad]] = None,
                  eventos_legado: Optional[Dict[int, List[EventoEvaluacion]]] = None) -> Dict[str, dict]:
    filas = filas if filas is not None else ([] if db is None else
                                              db.query(EnvioActividad).filter(EnvioActividad.evaluacion_id == ev.id).all()
                                              if _tablas_listas(db) else [])
    r = resumen(filas, ev=ev)
    if r or db is None:
        return r
    if eventos_legado is not None:  # precarga en bloque: nunca una consulta por evaluación
        return resumen_legado(db, ev, eventos_legado.get(ev.id, []))
    return resumen_legado(db, ev)


def precarga_tablero(db: Session, postulaciones: list, evaluaciones_por_p: Dict[int, list]) -> Dict[int, dict]:
    """Tablero (2026-10-08, sin N+1): TODO lo que `proceso.estado_pasos` consultaría por tarjeta, en 4 consultas para
    el listado completo — envíos por destinatario, eventos «envio» de evaluaciones previas a la tabla, tareas de
    Onboarding y nombres de usuarios responsables. Regresa {postulacion_id: precarga}."""
    from ..models import TareaOnboarding, Usuario

    ids = [p.id for p in postulaciones]
    salida: Dict[int, dict] = {pid: {"envios": [], "eventos_envio": {}, "tareas": [], "usuarios": {}} for pid in ids}
    if not ids:
        return salida
    con_filas: set = set()
    if _tablas_listas(db):
        try:
            for f in db.query(EnvioActividad).filter(EnvioActividad.postulacion_id.in_(ids)).order_by(EnvioActividad.id):
                salida[f.postulacion_id]["envios"].append(f)
                if f.evaluacion_id:
                    con_filas.add(f.evaluacion_id)
        except Exception:  # noqa: BLE001 — base sin la tabla: cada tarjeta lee el legado
            db.rollback()
    ev_a_p = {ev.id: pid for pid, evs in evaluaciones_por_p.items() for ev in evs if ev.id not in con_filas}
    if ev_a_p:
        for e in (db.query(EventoEvaluacion)
                  .filter(EventoEvaluacion.evaluacion_id.in_(list(ev_a_p)), EventoEvaluacion.accion == "envio")
                  .order_by(EventoEvaluacion.id)):
            salida[ev_a_p[e.evaluacion_id]]["eventos_envio"].setdefault(e.evaluacion_id, []).append(e)
    exp_a_p = {p.expediente.id: p.id for p in postulaciones if p.expediente is not None}
    if exp_a_p:
        try:
            for t in db.query(TareaOnboarding).filter(TareaOnboarding.expediente_id.in_(list(exp_a_p))):
                salida[exp_a_p[t.expediente_id]]["tareas"].append(t)
        except Exception:  # noqa: BLE001
            db.rollback()
    ids_u = {((x.get("responsable") or {}).get("usuario_id")) for p in postulaciones for x in ((p.proceso or {}).get("pasos") or [])} - {None}
    if ids_u:
        nombres = {u.id: u.nombre for u in db.query(Usuario).filter(Usuario.id.in_(ids_u)).all()}
        for d in salida.values():
            d["usuarios"] = nombres
    return salida
