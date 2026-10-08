"""Motor de ruta AUTOMATIZADO (2026-10-08) — SOLO las Cuentas de `models.CUENTAS_RUTA_AUTOMATICA` (hoy «demo-grupak»).

Las demás Cuentas conservan su avance manual: nada de este módulo corre para ellas (`ruta_automatica(p.cuenta)`).

Un ciclo (`procesar`) por postulación:
  1. Resuelve el PREFILTRO WEB contra la vacante (`resolver_prefiltro_web`): todas las respuestas esperadas → Cumple;
     un criterio EXCLUYENTE (`descarta`) contestado al revés → No cumple (con motivo); «Parcial» o sin respuesta en un
     excluyente → «Revisar prefiltro» (decide RH con `aprobar_prefiltro` o descartando).
  2. Avanza la etapa si los obligatorios de la actual cumplen (`proceso.avanzar_si_corresponde`, forzado en
     Prefiltro / Filtro Red Human / Filtro humano; Contratación y Onboarding nunca avanzan solos).
  3. DISPARA las actividades que se habilitaron y se pueden enviar sin una persona (`TIPOS_DISPARABLES`:
     psicometría del catálogo, Entrevista Red Human, liga de documentos) por el canal del candidato. Cada disparo se
     RECLAMA antes de enviar (`analisis.motor_ruta.envios[paso]`) y se intenta UNA sola vez: un fallo queda como
     «Error» en la actividad y RH reintenta con su acción. Lo que pide agendar, aplicar en persona, revisar o
     aprobar sigue manual.
  4. Repite 2-3 mientras algo cambie.

HUMAN-IN-THE-LOOP (LFPDPPP 2025, decisión del usuario 2026-10-08): el motor NUNCA descarta. Un resultado excluyente
(prefiltro No cumple, calificación bajo el mínimo, dictamen desfavorable) deja «Descarte sugerido»
(`proceso.descarte_sugerido`): el funnel se detiene en la compuerta y RH confirma el descarte u omite la actividad.
Sin consentimiento del candidato no se dispara nada.

Pausa por postulación: `analisis.motor_ruta.pausado` (los candidatos SEMBRADOS de la demo la traen: datos de
demostración que nunca reciben envíos). Lo llaman: `proceso.avanzar_seguro` (tras cualquier evento: prefiltro, resultado, revisión, omitir…), `/postular` y el
job `motor_ruta` (cada 5 min, `barrido`), que también recoge lo que haya quedado pendiente.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import CUENTAS_RUTA_AUTOMATICA, Cuenta, Postulacion, registrar, ruta_automatica
from . import proceso as sproc

ACTOR = "Red Human (automático)"
_ESPERADA_DEFAULT = "Sí"


def _actor() -> SimpleNamespace:
    return SimpleNamespace(nombre=ACTOR, correo="", id=None, rol="Sistema", puede_autorizar_omisiones=lambda: False,
                           puede_ver_informe_medico=lambda: False)


def _norm(texto: str) -> str:
    from ..routers.candidatos import _norm_resp

    return _norm_resp(texto)


# ============================================================ 1. prefiltro web

def evaluar_prefiltro_web(respuestas: list, preguntas: list) -> Tuple[str, str, List[dict]]:
    """(resultado, motivo, criterios) — resultado ∈ cumple | no_cumple | revision. Solo los criterios EXCLUYENTES
    (`descarta`) deciden; los demás quedan como observación."""
    por_pregunta = {_norm(r.get("pregunta", "")): str(r.get("respuesta", "")).strip() for r in respuestas or [] if isinstance(r, dict)}
    criterios: List[dict] = []
    incumple: List[str] = []
    dudosos: List[str] = []
    for pv in preguntas or []:
        if not isinstance(pv, dict) or not str(pv.get("pregunta", "")).strip():
            continue
        nombre = (pv.get("valida") or pv["pregunta"]).strip()
        excluyente = bool(pv.get("descarta"))
        esperada = _norm(pv.get("respuesta_esperada") or _ESPERADA_DEFAULT)
        respuesta = por_pregunta.get(_norm(pv["pregunta"]))
        r = _norm(respuesta or "")
        if not respuesta:
            veredicto = "sin_respuesta"
        elif r == esperada or (esperada in ("si", "sí") and r.startswith("si")):
            veredicto = "cumple"
        elif r.startswith("parcial"):
            veredicto = "parcial"
        else:
            veredicto = "no_cumple"
        criterios.append({"criterio": nombre, "respuesta": respuesta or "", "excluyente": excluyente, "veredicto": veredicto})
        if excluyente and veredicto == "no_cumple":
            incumple.append(nombre)
        elif excluyente and veredicto in ("parcial", "sin_respuesta"):
            dudosos.append(f"{nombre} ({'Parcial' if veredicto == 'parcial' else 'sin respuesta'})")
    if incumple:
        return "no_cumple", "No cumple el requisito excluyente: " + ", ".join(incumple), criterios
    if dudosos:
        return "revision", "Respuesta no concluyente en: " + ", ".join(dudosos), criterios
    return "cumple", "Cumple los requisitos excluyentes de la vacante", criterios


def resolver_prefiltro_web(db: Session, p: Postulacion) -> Optional[str]:
    """Resuelve UNA vez el prefiltro web de la postulación (si su ruta lo tiene, hay respuestas y nadie lo decidió).
    Escribe `analisis.prefiltro_web` y sincroniza `Postulacion.estado` (tablero). No hace commit."""
    if not sproc.tiene_proceso(p) or not any(x["tipo"] == "prefiltro_web" for x in p.proceso.get("pasos", [])):
        return None
    a = dict(p.analisis or {})
    if (a.get("prefiltro_web") or {}).get("resultado") or not a.get("respuestas_web"):
        return None
    preguntas = list((p.vacante.preguntas_filtro if p.vacante else None) or [])
    resultado, motivo, criterios = evaluar_prefiltro_web(a["respuestas_web"], preguntas)
    a["prefiltro_web"] = {"resultado": resultado, "motivo": motivo, "criterios": criterios,
                          "en": datetime.now(timezone.utc).isoformat(), "decidido_por": "Red Human"}
    p.analisis = a
    flag_modified(p, "analisis")
    p.estado = resultado  # cumple | no_cumple | revision: la RECOMENDACIÓN visible en el tablero (nunca cierra)
    registrar(db, ACTOR, "prefiltro_web_resuelto", "postulacion", p.codigo, {"resultado": resultado, "motivo": motivo})
    return resultado


def aprobar_prefiltro(db: Session, p: Postulacion, u, comentario: str = "") -> None:
    """RH resuelve un «Revisar prefiltro» a favor (el descarte va por «Descartar»). No hace commit."""
    a = dict(p.analisis or {})
    pw = dict(a.get("prefiltro_web") or {})
    if pw.get("resultado") != "revision":
        raise sproc.ErrorProceso(409, "El prefiltro no está en «Revisar prefiltro».")
    pw.update({"resultado": "cumple", "motivo": (comentario or "").strip()[:300] or "Aprobado por RH tras revisión",
               "decidido_por": u.nombre, "en": datetime.now(timezone.utc).isoformat(), "revision_previa": pw.get("motivo", "")})
    a["prefiltro_web"] = pw
    p.analisis = a
    flag_modified(p, "analisis")
    p.estado = "cumple"
    registrar(db, u.nombre, "prefiltro_aprobado_por_rh", "postulacion", p.codigo,
              {"comentario": comentario[:300], "correo_rh": getattr(u, "correo", "")})


# ============================================================ 3. disparo de actividades

def _envios(p: Postulacion) -> dict:
    return dict(((p.analisis or {}).get("motor_ruta") or {}).get("envios") or {})


def _marcar(p: Postulacion, paso_id: str, datos: dict) -> None:
    a = dict(p.analisis or {})
    motor = dict(a.get("motor_ruta") or {})
    envios = dict(motor.get("envios") or {})
    envios[paso_id] = {**(envios.get(paso_id) or {}), **datos}
    motor["envios"] = envios
    a["motor_ruta"] = motor
    p.analisis = a
    flag_modified(p, "analisis")


async def _avisar(db: Session, p: Postulacion, texto: str, asunto: str, liga: str, *, motivo: str = "aviso", paso_id: str = "",
                  actor: str = ACTOR) -> Tuple[bool, str]:
    """Canal del candidato en cascada: mensajería activa (WhatsApp o Telegram, la fachada decide) → correo. Cada canal
    intentado queda trazado por destinatario (`envios_actividad`, 2026-10-08)."""
    from . import envios
    from .correo import enviar_correo
    from .mensajeria import de_cuenta
    from .whatsapp import enviar_mensaje

    detalles = []
    resultados: List[dict] = []
    salida: Tuple[bool, str] = (False, "")
    if p.telefono:
        try:
            with de_cuenta(p.cuenta_id):
                r = await enviar_mensaje(p.telefono, texto)
            resultados.append({"destinatario": "candidato", "canal": "whatsapp", "destino": p.telefono, **r})
            if r.get("enviado"):
                from ..routers.candidatos import guardar_mensaje

                guardar_mensaje(db, p, "assistant", texto, "whatsapp", r)
                salida = (True, r.get("proveedor") or "whatsapp")
            else:
                detalles.append(f"mensaje: {r.get('detalle') or 'no salió'}")
        except Exception as ex:  # noqa: BLE001
            resultados.append({"destinatario": "candidato", "canal": "whatsapp", "destino": p.telefono, "enviado": False, "detalle": str(ex)[:200]})
            detalles.append(f"mensaje: {str(ex)[:120]}")
    if p.correo and not salida[0]:
        try:
            from .notificaciones import _html

            asunto_html, html = _html(asunto, texto)
            r = await enviar_correo(p.correo, asunto_html or asunto, html)
            resultados.append({"destinatario": "candidato", "canal": "correo", "destino": p.correo, **r})
            if r.get("enviado"):
                salida = (True, "correo")
            else:
                detalles.append(f"correo: {r.get('detalle') or 'no salió'}")
        except Exception as ex:  # noqa: BLE001
            resultados.append({"destinatario": "candidato", "canal": "correo", "destino": p.correo, "enviado": False, "detalle": str(ex)[:200]})
            detalles.append(f"correo: {str(ex)[:120]}")
    envios.registrar(db, p, resultados, motivo=motivo, paso_id=paso_id, actor=actor, destinatario="candidato")
    if salida[0]:
        return salida
    return False, "; ".join(detalles) or "El candidato no tiene teléfono ni correo registrados."


async def _disparar_psicometria(db: Session, p: Postulacion, paso: dict) -> Tuple[bool, str, Optional[bool]]:
    from fastapi import HTTPException

    from ..routers.evaluaciones import AsignarPsicometriaIn, asignar_psicometria

    try:
        r = await asignar_psicometria(p.codigo, AsignarPsicometriaIn(paso_id=paso["id"]), db=db, u=_actor(), cuenta=p.cuenta)
    except HTTPException as ex:
        return False, str(ex.detail), None
    if r.get("simulado"):
        return True, "Asignada en modo simulado (plataforma de evaluación sin conectar)", None
    env = r.get("envioCandidato") or {}
    # el estado de entrega lo deriva la propia psicometría («Error de envío»): aquí solo se informa
    return True, ("Enviada por " + " y ".join(env.get("canales") or [])) if env.get("enviado") else "Generada; el aviso no salió", None


async def _disparar_entrevista(db: Session, p: Postulacion, paso: dict) -> Tuple[bool, str, Optional[bool]]:
    from ..config import settings
    from .entrevistas import crear_entrevista_para_candidato

    e, _ = crear_entrevista_para_candidato(db, p, ACTOR)
    liga = f"{settings.app_url}/entrevista/{e.token}"
    nombre = (p.nombre or "").split(" ")[0] if p.nombre and not p.nombre.startswith("Candidato") else ""
    vacante = p.vacante.titulo if p.vacante else "la vacante"
    texto = (f"¡Hola{(' ' + nombre) if nombre else ''}! 👋 Avanzaste en tu proceso para {vacante}. El siguiente paso es tu "
             f"entrevista con Red Human: entra cuando gustes desde tu celular o computadora (dura unos 10 minutos): {liga}")
    db.flush()
    entregado, detalle = await _avisar(db, p, texto, f"Tu entrevista para {vacante}", liga, motivo="entrevista", paso_id=paso["id"])
    return True, (f"Liga enviada por {detalle}" if entregado else f"Liga creada; el aviso no salió ({detalle})"), entregado


async def _disparar_documentos(db: Session, p: Postulacion, paso: dict) -> Tuple[bool, str, Optional[bool]]:
    from fastapi import HTTPException

    from ..routers.candidatos import solicitar_documentos

    try:
        r = await solicitar_documentos(p.codigo, None, db=db, u=_actor(), cuenta=p.cuenta)
    except HTTPException as ex:
        return False, str(ex.detail), None
    enviados = [x for x in (r.get("resultados") or []) if x.get("enviado")] if isinstance(r, dict) else []
    return True, "Liga de documentos enviada" if enviados else "Liga creada; el aviso no salió", bool(enviados)


DISPARADORES = {"psicometrica": _disparar_psicometria, "entrevista_agente": _disparar_entrevista,
                "solicitud_documentos": _disparar_documentos}


async def disparar(db: Session, p: Postulacion, pasos: List[dict]) -> List[str]:
    """Envía las actividades habilitadas que no necesitan a una persona. Reclama cada una ANTES de enviar (commit) para
    que dos corridas simultáneas (job + petición) nunca la dupliquen; regresa los ids disparados."""
    if not p.activa or not p.consentimiento or sproc.descarte_sugerido(p, pasos):
        return []
    hechos: List[str] = []
    envios = _envios(p)
    for x in pasos:
        if (x["tipo"] not in DISPARADORES or x["heredado"] or not x["disponible"] or x["estado"] != "pendiente"
                or x["id"] in envios or x.get("evaluacion")):
            continue
        _marcar(p, x["id"], {"en": datetime.now(timezone.utc).isoformat(), "ok": None})
        db.commit()
        codigo = p.codigo
        try:
            ok, detalle, entregado = await DISPARADORES[x["tipo"]](db, p, x)
        except Exception as ex:  # noqa: BLE001 — un disparo fallido nunca rompe el ciclo ni la acción que lo llamó
            db.rollback()
            ok, detalle, entregado = False, f"Error inesperado: {str(ex)[:200]}", None
        p = db.query(Postulacion).filter(Postulacion.codigo == codigo).first() or p
        _marcar(p, x["id"], {"ok": ok, "detalle": detalle[:300], **({"entregado": entregado} if entregado is not None else {})})
        registrar(db, ACTOR, "actividad_disparada" if ok else "actividad_disparo_fallido", "postulacion", p.codigo,
                  {"paso": x["id"], "actividad": x["nombre"], "detalle": detalle[:300]})
        db.commit()
        hechos.append(x["id"])
    return hechos


# ============================================================ ciclo

async def procesar(db: Session, p: Postulacion) -> List[str]:
    """Un ciclo completo del motor para la postulación. Regresa las etapas a las que avanzó (como
    `avanzar_si_corresponde`). No hace nada fuera de `CUENTAS_RUTA_AUTOMATICA`."""
    if p is None or not ruta_automatica(p.cuenta) or not p.activa or not sproc.tiene_proceso(p):
        return []
    if ((p.analisis or {}).get("motor_ruta") or {}).get("pausado"):
        # p. ej. candidatos sembrados de la demo: se ven sus estados, pero el motor no dispara ni mueve nada
        return []
    if resolver_prefiltro_web(db, p):
        db.commit()
    movidas: List[str] = list(await sproc.avanzar_si_corresponde(db, p))
    for _ in range(4):
        disparadas = await disparar(db, p, sproc.estado_pasos(p))
        mas = await sproc.avanzar_si_corresponde(db, p)
        movidas += mas
        if not disparadas and not mas:
            break
    return movidas


async def barrido() -> dict:
    """Job (cada 5 min): un ciclo para cada postulación ACTIVA de las Cuentas con ruta automática. Cada postulación en
    su propia sesión: una que truene no frena a las demás."""
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        ids = [x for (x,) in db.query(Postulacion.id).join(Cuenta, Postulacion.cuenta_id == Cuenta.id)
               .filter(Cuenta.slug.in_(CUENTAS_RUTA_AUTOMATICA), Postulacion.activa.is_(True)).all()]
    finally:
        db.close()
    resumen = {"revisadas": 0, "errores": 0}

    for pid in ids:
        s = SessionLocal()
        try:
            p = s.get(Postulacion, pid)
            if p is not None:
                await procesar(s, p)
        except Exception as ex:  # noqa: BLE001
            s.rollback()
            resumen["errores"] += 1
            print(f"[motor_ruta] postulación {pid}: {ex}", flush=True)
        finally:
            s.close()
        resumen["revisadas"] += 1
    return resumen
