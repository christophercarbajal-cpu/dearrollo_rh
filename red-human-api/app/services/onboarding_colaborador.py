"""Cambio 33 (2026-10-13) — de Onboarding a Colaborador y acciones manuales en la ficha.

* Al firmar el contrato el candidato entra a Onboarding (`routers.onboarding.iniciar_onboarding`). Si su ruta tiene
  actividades de Onboarding (sin contar «Confirmación de ingreso», que ES el paso a colaborador) se ejecutan y, al
  completarse TODAS, pasa SOLO a «Colaborador» (`revisar`, desde `proceso.avanzar_si_corresponde`). Si no tiene ninguna,
  queda «Listo para pasar a colaborador» y no se le envía nada.
* «Pasar a colaborador» (`pasar_a_colaborador`): el alta de siempre (`contratacion.ejecutar_alta`) ignorando pendientes;
  lo que falte (documentos, tareas) se QUEDA pendiente en su expediente y queda listado en historial y bitácora.
* Acciones manuales que no alteran la plantilla ni la vacante (actividades ad hoc solo de esta postulación, en
  Onboarding, sin regresar de etapa): «Solicitar documentos pendientes» (actividad «Documentos» que pide SOLO los
  documentos requeridos — Cuenta + vacante — que aún no están Aprobados) y «Asignar tareas» (actividad «Tareas de
  onboarding» con las tareas de la plantilla que aplica).
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Postulacion, clave_documento, registrar

CLAVE = "onboarding_estado"
LISTO = "listo_colaborador"
TEXTO_LISTO = "Listo para pasar a colaborador"
ACTOR = "Red Human (avance automático)"


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def actividades(p: Postulacion, pasos: Optional[List[dict]] = None) -> List[dict]:
    """Actividades de Onboarding de la ruta de esta postulación (sin «Confirmación de ingreso» ni heredadas)."""
    from . import proceso as sproc

    if not sproc.tiene_proceso(p):
        return []
    pasos = pasos if pasos is not None else sproc.estado_pasos(p)
    return [x for x in pasos if x["etapa"] == "Onboarding" and not x.get("heredado") and x["tipo"] != "alta"
            and x.get("obligatorio", True)]


def tiene_actividades(p: Postulacion) -> bool:
    from . import proceso as sproc

    if not sproc.tiene_proceso(p):
        return True  # sin ruta: el Onboarding de siempre (tareas de la plantilla)
    return any(x.get("etapa") == "Onboarding" and not x.get("heredado") and x.get("tipo") != "alta"
               for x in (p.proceso or {}).get("pasos") or [])


def _hecha(x: dict) -> bool:
    return x["estado"] in ("omitida", "cancelada") or (x["estado"] == "completada" and x.get("cumpleRegla", x.get("cumple", True)))


def marcar_listo(p: Postulacion) -> None:
    a = dict(p.analisis or {})
    a[CLAVE] = {"estado": LISTO, "en": _ahora()}
    p.analisis = a
    flag_modified(p, "analisis")


def estado(p: Postulacion) -> Optional[str]:
    """«listo_colaborador» | «actividades» | None (fuera de Onboarding o ya colaborador)."""
    exp = p.expediente
    if p.etapa != "Onboarding" or exp is None or exp.estado == "alta" or not p.activa:
        return None
    return "actividades" if tiene_actividades(p) else LISTO


def documentos_pendientes(db: Session, p: Postulacion) -> List[str]:
    """Documentos requeridos (Cuenta + vacante, catálogo único) que aún NO están Aprobados en el expediente. Los que RH
    marcó «No aplica» no cuentan."""
    from .onboarding import documentos_requeridos

    exp = p.expediente
    if exp is None:
        return []
    por_clave = {clave_documento(d.tipo): d for d in exp.documentos if not d.interno}
    salida = []
    for r in documentos_requeridos(db, p):
        d = por_clave.get(clave_documento(r["tipo"]))
        if d is not None and (d.aprobado or d.estado == "no_aplica"):
            continue
        salida.append(r["tipo"])
    return salida


def _pendientes_alta(db: Session, p: Postulacion) -> dict:
    from . import onboarding as onb

    exp = p.expediente
    tareas = [t.nombre for t in onb.tareas_de(db, exp) if t.estado == "pendiente"] if exp is not None else []
    return {"documentos": documentos_pendientes(db, p), "tareas": tareas}


async def pasar_a_colaborador(db: Session, p: Postulacion, u, cuenta, comentario: str = "", automatico: bool = False) -> dict:
    """«Pasar a colaborador»: alta ignorando pendientes (se quedan «Pendiente» en el expediente)."""
    from ..routers.contratacion import AltaIn, ejecutar_alta

    exp = p.expediente
    pendientes = _pendientes_alta(db, p)
    quien = getattr(u, "nombre", "") or ACTOR
    texto = ("Pasó a colaborador al completar sus actividades de Onboarding" if automatico
             else f"Pasado a colaborador por {quien}") + (
        f" — pendientes: {', '.join(pendientes['documentos'] + pendientes['tareas'])}" if pendientes["documentos"] or pendientes["tareas"] else "")
    p.historial = list(p.historial or []) + [{
        "evento": "pasado_a_colaborador", "usuario": quien, "fecha": _ahora(), "etapa": p.etapa, "automatico": automatico,
        "pendientes": pendientes, "comentario": (comentario or "").strip()[:500], "texto": texto,
    }]
    registrar(db, quien, "pasado_a_colaborador", "expediente", str(exp.id),
              {"postulacion": p.codigo, "automatico": automatico, "pendientes": pendientes, "comentario": (comentario or "")[:500]})
    r = await ejecutar_alta(db, exp, AltaIn(), u, cuenta, ignorar_pendientes=True)
    return {**r, "pendientes": pendientes}


async def revisar(db: Session, p: Postulacion) -> bool:
    """Desde el motor de ruta: en Onboarding, con actividades y TODAS completadas (u omitidas) → Colaborador. Nunca lanza."""
    from fastapi import HTTPException

    from ..models import Cuenta

    exp = p.expediente
    if p.etapa != "Onboarding" or exp is None or exp.estado == "alta" or exp.no_ingreso_en or not p.activa:
        return False
    acts = actividades(p)
    if not acts or not all(_hecha(x) for x in acts):
        return False
    actor = SimpleNamespace(nombre=ACTOR, correo="", id=None, rol="Sistema", puede_autorizar_omisiones=lambda: False)
    try:
        await pasar_a_colaborador(db, p, actor, db.get(Cuenta, p.cuenta_id), automatico=True)
        return True
    except HTTPException as ex:
        registrar(db, ACTOR, "colaborador_automatico_detenido", "postulacion", p.codigo, {"motivo": str(ex.detail)[:300]})
        db.commit()
        return False


def _paso_adhoc(db: Session, p: Postulacion, tipo: str, nombre: str, u, **extra) -> dict:
    """Agrega (o reutiliza, si sigue viva) la actividad ad hoc de Onboarding de ese tipo. Nunca regresa de etapa."""
    from . import proceso as sproc

    vivos = {x["id"]: x for x in sproc.estado_pasos(p)}
    for x in (p.proceso or {}).get("pasos") or []:
        if x.get("adhoc") and x.get("tipo") == tipo and x.get("etapa") == "Onboarding" and vivos.get(x["id"], {}).get("estado") not in ("completada", "omitida", "cancelada"):
            if extra:
                proc = dict(p.proceso)
                proc["pasos"] = [{**y, **extra} if y["id"] == x["id"] else y for y in proc["pasos"]]
                p.proceso = proc
                flag_modified(p, "proceso")
            return x
    return sproc.agregar_paso_adhoc(db, p, {"tipo": tipo, "nombre": nombre, "etapa": "Onboarding", "obligatorio": True, **extra}, u)


async def solicitar_documentos_pendientes(db: Session, p: Postulacion, u) -> dict:
    """Actividad «Documentos» que pide SOLO los requeridos que aún no están Aprobados (lo Aprobado nunca se vuelve a
    pedir) + la solicitud al candidato con la liga de su expediente."""
    from fastapi import HTTPException

    from ..routers import candidatos as rcand
    from . import onboarding as onb

    exp = p.expediente
    if exp is None or p.etapa != "Onboarding":
        raise HTTPException(409, "Esta acción es para candidatos en Onboarding.")
    pendientes = documentos_pendientes(db, p)
    if not pendientes:
        raise HTTPException(409, "No hay documentos pendientes: todos los requeridos están aprobados.")
    onb.aplicar_documentos(db, exp, [{"tipo": t, "obligatorio": True} for t in pendientes])
    for d in exp.documentos:  # un requerido que estaba «No aplica» sin motivo de RH vuelve a pedirse
        if clave_documento(d.tipo) in {clave_documento(t) for t in pendientes} and d.estado == "no_aplica" and not d.motivo_no_aplica:
            d.estado = "pendiente"
    paso = _paso_adhoc(db, p, "documentos", "Documentos pendientes", u, documentos=pendientes)
    db.commit()
    try:
        r = await rcand._disparar_mensaje_onboarding(db, p, "solicitud_documentos", "documentos_solicitados", rcand._liga_documentos(p), u)
        resultados = r.get("resultados") or []
    except Exception as ex:  # noqa: BLE001 — un aviso caído nunca rompe la acción de RH
        resultados = [{"destinatario": "Candidato", "canal": "whatsapp/correo", "destino": "", "enviado": False, "detalle": str(ex)[:200]}]
    db.commit()
    return {"paso": paso["id"], "documentos": pendientes, "solicitud": resultados}


async def asignar_tareas(db: Session, p: Postulacion, u, cuenta) -> dict:
    """Actividad «Tareas de onboarding» para ESTA persona con las tareas de la plantilla que aplica (idempotente)."""
    from fastapi import HTTPException

    from ..routers.onboarding import _avisar_responsables
    from . import onboarding as onb

    exp = p.expediente
    if exp is None or p.etapa != "Onboarding":
        raise HTTPException(409, "Esta acción es para candidatos en Onboarding.")
    paso = _paso_adhoc(db, p, "onboarding", "Tareas de onboarding", u)
    cfg = onb.configuracion_para(db, cuenta.id, exp.puesto, exp.empresa)
    tareas = onb.generar_tareas(db, exp, cuenta.id, cfg, getattr(u, "nombre", "") or "RH")
    onb.sincronizar_legado(db, exp)
    db.commit()
    avisos = await _avisar_responsables(db, exp, cuenta, [t for t in tareas if t.estado == "pendiente"])
    db.commit()
    return {"paso": paso["id"], "tareas": [t.nombre for t in tareas], "avisosResponsables": avisos}


def resumen(db: Session, p: Postulacion) -> Optional[dict]:
    """Lo que la ficha muestra en Onboarding: estado, «Documentos pendientes: N» y si se puede pasar a colaborador."""
    est = estado(p)
    if est is None:
        return None
    try:
        pendientes = documentos_pendientes(db, p)
    except Exception:  # noqa: BLE001 — la ficha nunca truena por esto
        pendientes = []
    return {"estado": est, "texto": TEXTO_LISTO if est == LISTO else "En actividades de Onboarding",
            "documentosPendientes": len(pendientes), "documentosPendientesLista": pendientes, "puedePasarColaborador": True}
