"""Módulo 1 · Reclutamiento — Entrevistas con agente IA (módulo 3.10).

Flujo: RH agenda → se genera guion con IA y una liga pública → el candidato
abre la liga, otorga consentimiento y conversa con el avatar (Anam) o por
texto (modo demo) → al terminar, la IA evalúa y deja una RECOMENDACIÓN;
la decisión de avanzar/descartar sigue siendo humana (LFPDPPP).
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import CIERRES_COMPLETOS, CIERRES_ENTREVISTA, Candidato, Cuenta, Entrevista, Usuario, Vacante, registrar
from ..serial import entrevista_dict, nombre_empresa_candidato
from ..services.proceso import avanzar_seguro, enfoque_entrevista_agente
from ..services import ia
from ..services.avatar import AvatarError, avatar_activo, crear_sesion_avatar, probar_avatar
from ..services.configuracion import modo_prueba_activo
from ..services.entrevistas import crear_entrevista_para_candidato, reabrir_entrevista
from ..services.whatsapp import enviar_mensaje
from .candidatos import _crear_candidato, guardar_mensaje, nombre_ficha, postulacion_para_vacante
from .candidatos import _por_codigo as _postulacion_por_codigo

router = APIRouter(prefix="/entrevistas", tags=["entrevistas"])


def _por_token(db: Session, token: str) -> Entrevista:
    e = db.query(Entrevista).filter(Entrevista.token == token).first()
    if not e:
        raise HTTPException(404, "Entrevista no encontrada")
    return e


def _por_codigo(db: Session, codigo: str) -> Entrevista:
    e = db.query(Entrevista).filter(Entrevista.codigo == codigo).first()
    if not e:
        raise HTTPException(404, "Entrevista no encontrada")
    return e


@router.get("")
def listar(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    q = (
        db.query(Entrevista)
        .join(Candidato, Entrevista.candidato_id == Candidato.id)
        .filter(Candidato.cuenta_id == cuenta.id)
        .order_by(Entrevista.id.desc())
    )
    return [entrevista_dict(e) for e in q.all()]


@router.get("/metricas")
def metricas(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Métricas del motor de entrevistas para el dashboard."""
    todas = (
        db.query(Entrevista)
        .join(Candidato, Entrevista.candidato_id == Candidato.id)
        .filter(Candidato.cuenta_id == cuenta.id)
        .all()
    )
    evaluadas = [e for e in todas if e.estado == "evaluada" and e.evaluacion]
    matches = [e.evaluacion.get("match_perfil", 0) for e in evaluadas]
    recomendaciones = {"avanzar": 0, "revision": 0, "no_avanzar": 0}
    for e in evaluadas:
        rec = e.evaluacion.get("recomendacion")
        if rec in recomendaciones:
            recomendaciones[rec] += 1
    return {
        "total": len(todas),
        "evaluadas": len(evaluadas),
        "pendientes": sum(1 for e in todas if e.estado in ("programada", "en_curso")),
        "match_promedio": round(sum(matches) / len(matches)) if matches else 0,
        "recomendaciones": recomendaciones,
        "avatar_activo": avatar_activo(),
    }


class DiagnosticoNavegadorIn(BaseModel):
    """Lo que el navegador vio (2026-09-23, incidente Expo). Solo texto de diagnóstico, sin datos del
    candidato: en un tótem no se puede abrir la consola, así que el reporte llega aquí y queda en el log
    del servidor y en la bitácora."""

    donde: str = "entrevista"   # entrevista | capacitacion
    modo: str = ""
    motivo: str = ""
    error: str = ""
    ice: dict = {}              # {host, srflx, relay, ultimoIce, ultimoConn}
    eventos: List[str] = []
    navegador: str = ""


@router.post("/publica/{token}/diagnostico")
def diagnostico_navegador(token: str, datos: DiagnosticoNavegadorIn, db: Session = Depends(get_db)):
    """Recibe el diagnóstico del avatar desde la sala pública (sin sesión). No cambia nada de la
    entrevista: solo deja la evidencia donde soporte pueda leerla (`journalctl -u redhuman-api`)."""
    e = db.query(Entrevista).filter(Entrevista.token == token).first()
    codigo = e.codigo if e else "(liga desconocida)"
    ice = datos.ice or {}
    sin_srflx = str(ice.get("srflx", "")) in ("0", "False", "false", "")
    veredicto = (
        "RED: el dispositivo no obtuvo candidatos públicos (srflx) — el Wi-Fi bloquea UDP/STUN y WebRTC no puede conectar"
        if sin_srflx and datos.modo == "avatar"
        else "revisar eventos"
    )
    print(
        f"[AVATAR][DIAGNOSTICO] {codigo} donde={datos.donde} modo={datos.modo} motivo={datos.motivo[:160]!r} "
        f"error={datos.error[:200]!r} ice={ice} veredicto={veredicto} navegador={datos.navegador[:120]!r}",
        flush=True,
    )
    for linea in (datos.eventos or [])[-40:]:
        print(f"[AVATAR][DIAGNOSTICO] {codigo}   {linea}", flush=True)
    registrar(db, "sistema", "avatar_diagnostico", "entrevista", codigo,
              {"donde": datos.donde, "modo": datos.modo, "motivo": datos.motivo[:300], "error": datos.error[:300],
               "ice": ice, "veredicto": veredicto, "eventos": (datos.eventos or [])[-20:]})
    db.commit()
    return {"recibido": True, "veredicto": veredicto}


@router.get("/avatar/diagnostico")
async def diagnostico_avatar(_: Usuario = Depends(usuario_actual)):
    """Prueba real de Anam desde el servidor (2026-09-14): qué variables ve el proceso y si Anam emite
    un session token. Sin claves en la respuesta. Para soporte cuando "la sala cae a texto"."""
    return await probar_avatar()


# ------------------------------------------------------------
# Agendar (RH) — genera guion IA + liga pública para el candidato
# ------------------------------------------------------------


class AgendarIn(BaseModel):
    candidato: str  # código de la Postulación (P-####); se acepta C-#### por compatibilidad
    programada_para: Optional[str] = None  # ISO
    avisar_whatsapp: bool = True


@router.post("", status_code=201)
async def agendar(
    datos: AgendarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    p = _postulacion_por_codigo(db, datos.candidato, cuenta.id)

    fecha = None
    if datos.programada_para:
        try:
            fecha = datetime.fromisoformat(datos.programada_para).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(400, "programada_para inválida (usa ISO: 2026-07-28T15:00)")

    e, con_ia = crear_entrevista_para_candidato(db, p, u.nombre, programada_para=fecha)

    v = p.vacante
    liga = f"{settings.app_url}/entrevista/{e.token}"
    envio = {"enviado": False, "proveedor": "demo"}
    if datos.avisar_whatsapp and p.telefono:
        texto = (
            f"¡Hola {p.nombre.split(' ')[0]}! 👋 Tu entrevista para {v.titulo if v else 'la vacante'} está lista. "
            f"Entra cuando gustes desde tu celular o computadora: {liga} — dura unos 10 minutos."
        )
        envio = await enviar_mensaje(p.telefono, texto)
        guardar_mensaje(db, p, "assistant", texto, "whatsapp", envio)
    db.commit()
    return {"liga": liga, "ia": con_ia, "whatsapp": envio, **entrevista_dict(e)}


# ------------------------------------------------------------
# Entrevista inmediata — prospecto nuevo con liga al instante
# ------------------------------------------------------------


class InmediataIn(BaseModel):
    nombre: str
    telefono: str = ""
    correo: str = ""
    vacante: Optional[str] = None  # código VAC-####
    avisar_whatsapp: bool = False


@router.post("/inmediata", status_code=201)
async def inmediata(
    datos: InmediataIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Crea (o reutiliza) al prospecto y genera su liga de entrevista en un paso."""
    if not datos.nombre.strip():
        raise HTTPException(400, "El nombre del prospecto es obligatorio.")

    prueba = modo_prueba_activo(db)

    c = None
    if not prueba:
        if datos.telefono:
            c = db.query(Candidato).filter(
                Candidato.telefono == datos.telefono, Candidato.es_prueba.is_(False), Candidato.cuenta_id == cuenta.id
            ).first()
        if not c and datos.correo:
            c = db.query(Candidato).filter(
                Candidato.correo == datos.correo, Candidato.es_prueba.is_(False), Candidato.cuenta_id == cuenta.id
            ).first()

    vac = (
        db.query(Vacante).filter(Vacante.codigo == datos.vacante, Vacante.cuenta_id == cuenta.id).first()
        if datos.vacante
        else None
    )

    nuevo = c is None
    if c is None:
        c = _crear_candidato(db, cuenta.id, datos.nombre.strip(), "RH", prueba, telefono=datos.telefono, correo=datos.correo)
    # La persona se reutiliza; la postulación es por vacante (activa para esa vacante → la misma).
    p, nueva_p = postulacion_para_vacante(db, c, vac, cuenta.id, "rh_directo", es_prueba=prueba)
    registrar(
        db, "sistema", "candidato_ingresado", "postulacion", p.codigo,
        {"candidato": c.codigo, "fuente": "RH", "via": "entrevista_inmediata", "persona_nueva": nuevo, "postulacion_nueva": nueva_p},
    )
    db.commit()

    return await agendar(AgendarIn(candidato=p.codigo, avisar_whatsapp=datos.avisar_whatsapp), db, u, cuenta)


# ------------------------------------------------------------
# Sala pública (candidato) — por token
# ------------------------------------------------------------


def _contexto(e: Entrevista):
    """Fase 2/4: TODO sale de la Postulación de la entrevista (vacante, empresa visible) y de la
    persona solo el nombre. Nunca de `Candidato.vacante` (ya no existe: bug corregido en Fase 4)."""
    p = e.postulacion
    v = p.vacante if p else None
    empresa = nombre_empresa_candidato(v) if v else "la empresa"
    return p, v, empresa


def _nombre_entrevistado(e: Entrevista) -> str:
    """Nombre de la FICHA de la persona de esta entrevista (Punto 2): nunca de otra sesión."""
    c = e.candidato
    return nombre_ficha(e.postulacion) if e.postulacion else ((c.nombre.split(" ")[0] if c and c.nombre else "candidato"))


@router.get("/publica/{token}")
def publica(token: str, db: Session = Depends(get_db)):
    e = _por_token(db, token)
    p, v, empresa = _contexto(e)
    return {
        "candidato": _nombre_entrevistado(e),
        "puesto": v.titulo if v else "",
        "empresa": empresa,
        "tipo": e.tipo,
        "estado": e.estado,
        "cierre": e.cierre or "",
        "motivo": e.motivo or "",
        "consentimiento": e.consentimiento,
        "avatar_disponible": avatar_activo(),
        "duracion_max_seg": settings.anam_max_sesion_seg,
    }


class ConsentirIn(BaseModel):
    acepta: bool


@router.post("/publica/{token}/consentimiento")
def consentir(token: str, datos: ConsentirIn, db: Session = Depends(get_db)):
    e = _por_token(db, token)
    if not datos.acepta:
        raise HTTPException(400, "La entrevista requiere consentimiento explícito del candidato.")
    if e.estado in ("completada", "evaluada", "interrumpida"):
        raise HTTPException(409, "Esta entrevista ya fue cerrada.")
    e.consentimiento = True
    e.consentimiento_fecha = datetime.now(timezone.utc)
    registrar(db, e.candidato.codigo if e.candidato else "candidato", "consentimiento_entrevista", "entrevista", e.codigo, {})
    db.commit()
    return {"ok": True}


def _system_prompt(e: Entrevista) -> str:
    p, v, empresa = _contexto(e)
    guion = e.guion or {}
    return ia.prompt_entrevistador(
        v.titulo if v else "vacante general",
        v.requisitos if v else "",
        _nombre_entrevistado(e),
        list(guion.get("preguntas") or []),
        empresa=empresa,
        temas=ia.temas_de_guion(guion),
        enfoque=guion.get("enfoque", ""),
        enfoque_entrevista=enfoque_entrevista_agente(p, v),
        ubicacion=(v.ubicacion if v else "") or "",
        modalidad=(v.modalidad if v else "") or "",
        sueldo=(v.sueldo if v else "") or "",
        beneficios=list(v.beneficios or []) if v else [],
        area=(v.area if v else "") or "",
    )


ESTADOS_CERRADOS = ("completada", "evaluada", "interrumpida", "parcial")
MENSAJE_CERRADA = "Esta entrevista ya fue completada o interrumpida. Solicita a RH reabrirla."


class SesionIn(BaseModel):
    # 2026-09-13: el navegador puede FORZAR texto (p. ej. el avatar no pudo transmitir video) sin
    # depender de la configuración del servidor; así una falla de Anam nunca deja la sala en negro.
    modo: Optional[str] = None  # "texto" | None (auto)


@router.post("/publica/{token}/sesion")
async def sesion(token: str, datos: Optional[SesionIn] = None, db: Session = Depends(get_db)):
    """Inicia la sesión: token de avatar (Anam) o modo texto si no hay clave (o si el navegador
    pide texto). 403 sin consentimiento, 409 si la entrevista ya está cerrada."""
    e = _por_token(db, token)
    if not e.consentimiento:
        raise HTTPException(403, "Primero se requiere el consentimiento del candidato.")
    if e.estado in ESTADOS_CERRADOS:
        raise HTTPException(409, MENSAJE_CERRADA)
    forzar_texto = bool(datos and datos.modo == "texto")

    p, v, empresa = _contexto(e)
    saludo = ia.mensaje_inicial_entrevista(v.titulo if v else "")
    if e.estado != "en_curso":
        e.estado = "en_curso"
        e.iniciada_en = datetime.now(timezone.utc)
        e.ultima_actividad_en = e.iniciada_en

    ses = None
    # `motivo` viaja al navegador (solo texto descriptivo, sin claves) para que en consola se vea POR QUÉ
    # se cayó a texto: configuración del servidor, rechazo de Anam o petición explícita del navegador.
    motivo = ""
    if forzar_texto:
        motivo = "el navegador pidió modo texto"
    elif not avatar_activo():
        motivo = "avatar no configurado en el servidor (ANAM_API_KEY / ANAM_AVATAR_ID / ANAM_LLM_ID)"
        print(f"[AVISO] entrevista {e.codigo} en texto: {motivo}", flush=True)
    else:
        try:
            ses = await crear_sesion_avatar("Red Human", _system_prompt(e), saludo)
        except AvatarError as ex:  # el avatar nunca debe tumbar la entrevista: cae a texto
            # 2026-09-23: el motivo viaja con el STATUS para distinguir credencial/plan de todo lo demás.
            motivo = f"Anam {ex.status or '?'} rechazó la sesión ({'credencial o plan' if ex.es_de_plan else 'payload o red del servidor'}): {str(ex)[:300]}"
            print(f"[ERROR][AVATAR] crear_sesion_avatar falló ({e.codigo}) status={ex.status} request-id={ex.request_id}: {str(ex)}", flush=True)
            registrar(db, "sistema", "avatar_error", "entrevista", e.codigo,
                      {"error": str(ex)[:300], "status": ex.status, "request_id": ex.request_id, "causa": "autenticacion_o_plan" if ex.es_de_plan else "payload_o_red"})
        except Exception as ex:  # noqa: BLE001
            motivo = f"Anam rechazó la sesión: {str(ex)[:300]}"
            print(f"[ERROR][AVATAR] crear_sesion_avatar falló ({e.codigo}): {str(ex)}", flush=True)
            registrar(db, "sistema", "avatar_error", "entrevista", e.codigo, {"error": str(ex)[:300]})

    if ses is None:
        e.tipo = "texto"
        if not e.transcript:
            e.transcript = [{"rol": "assistant", "texto": saludo}]
        db.commit()
        return {"modo": "texto", "mensajes": e.transcript, "nombre": _nombre_entrevistado(e), "motivo": motivo}

    e.tipo = "avatar"
    db.commit()
    return {"modo": "avatar", "nombre": _nombre_entrevistado(e), **ses}


class TurnoIn(BaseModel):
    texto: str


@router.post("/publica/{token}/turno")
def turno(token: str, datos: TurnoIn, db: Session = Depends(get_db)):
    """Un turno de la entrevista en modo texto (demo o fallback)."""
    e = _por_token(db, token)
    if not e.consentimiento or e.estado != "en_curso":
        raise HTTPException(403, "La entrevista no está en curso.")

    historial = list(e.transcript or []) + [{"rol": "user", "texto": datos.texto}]
    t, con_ia = ia.entrevista_turno(_system_prompt(e), historial)
    e.transcript = historial + [{"rol": "assistant", "texto": t.respuesta}]
    e.ultima_actividad_en = datetime.now(timezone.utc)
    db.commit()
    return {"respuesta": t.respuesta, "terminada": t.terminada, "ia": con_ia}


class FinalizarIn(BaseModel):
    transcript: Optional[List[dict]] = None  # modo avatar: lo manda el navegador; modo texto: ya está guardado
    # Fase 4 (Punto 4): cómo cerró, ver CIERRES_ENTREVISTA. El servidor lo VERIFICA, no lo confía.
    cierre: str = "manual"


# Mínimo de intervenciones del candidato para considerar que hubo entrevista que evaluar.
MIN_TURNOS_CANDIDATO = 2
# 2026-09-13: con menos turnos ÚTILES que esto, la IA decide primero si la información alcanza
# (suficiencia) antes de generar cualquier score o recomendación.
TURNOS_ENTREVISTA_COMPLETA = 5


def _cerrar_sin_evaluar(db: Session, e: Entrevista, p, motivo: str, turnos: int, extra: Optional[dict] = None) -> dict:
    """Entrevista sin evaluación (2026-09-13): sin score, sin recomendación, la postulación NO se
    mueve. Acción siguiente para RH: «Reintentar Entrevista Red Human» (POST /entrevistas/{codigo}/reabrir)."""
    e.estado = "parcial" if motivo == "parcial" else "interrumpida"
    e.motivo = motivo
    e.evaluacion = {"parcial": True, **(extra or {})} if motivo == "parcial" else None
    registrar(
        db, "sistema", "entrevista_" + ("parcial" if motivo == "parcial" else "interrumpida"), "entrevista", e.codigo,
        {"cierre": e.cierre, "motivo": motivo, "turnos_candidato": turnos, "postulacion": p.codigo if p else None, **(extra or {})},
    )
    db.commit()
    return entrevista_dict(e)


def _cierre_verificado(e: Entrevista, cierre_declarado: str) -> str:
    """El backend decide el cierre real con lo que puede comprobar (Punto 4):
    - `texto`: solo si el último turno de la entrevistadora en el transcript guardado trae la despedida.
    - `herramienta`/`marcador`: solo si la despedida fija (ia.DESPEDIDA_ENTREVISTA) aparece en el
      último turno de la entrevistadora; si no, se degrada a `manual`.
    - `manual`/`desconexion`/`tiempo`: se aceptan tal cual (no cambian nada que haya que verificar).
    """
    if cierre_declarado not in CIERRES_ENTREVISTA:
        cierre_declarado = "manual"
    ultimo_asistente = next((m.get("texto", "") for m in reversed(e.transcript or []) if m.get("rol") == "assistant"), "")
    hay_despedida = ia.DESPEDIDA_ENTREVISTA.lower() in (ultimo_asistente or "").lower()
    if cierre_declarado in ("texto", "herramienta", "marcador"):
        return cierre_declarado if hay_despedida else "manual"
    return cierre_declarado


class TranscriptIn(BaseModel):
    transcript: List[dict] = []


def _normalizar_transcript(transcript: List[dict]) -> List[dict]:
    return [
        {"rol": ("assistant" if m.get("rol") == "assistant" else "user"), "texto": str(m.get("texto", ""))[:2000]}
        for m in (transcript or [])[:400]
    ]


@router.post("/publica/{token}/transcript")
def sincronizar_transcript(token: str, datos: TranscriptIn, db: Session = Depends(get_db)):
    """2026-09-17 — persistencia incremental del transcript en modo avatar. El navegador lo manda en
    cada `MESSAGE_HISTORY_UPDATED` (con debounce); así el servidor SIEMPRE tiene lo dicho aunque la
    pestaña se cierre, falle la red o el SDK vacíe el historial al parar el stream. Nunca acorta lo
    ya guardado (un historial vacío tardío no borra nada). Solo mientras está en curso."""
    e = _por_token(db, token)
    if e.estado != "en_curso":
        return {"ok": False, "estado": e.estado, "turnos": len(e.transcript or [])}
    nuevo = _normalizar_transcript(datos.transcript)
    actual = e.transcript or []
    if len(nuevo) >= len(actual):
        e.transcript = nuevo
    e.ultima_actividad_en = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "estado": e.estado, "turnos": len(e.transcript or [])}


def _transcript_mas_completo(e: Entrevista, recibido: Optional[List[dict]]) -> Optional[List[dict]]:
    """El transcript que se evalúa es el MÁS LARGO entre lo que manda el navegador y lo que ya
    sincronizó (2026-09-17): si el cliente llega vacío o recortado (stopStreaming vació el historial),
    se usa el del servidor. Regresa None si no hay nada nuevo que escribir."""
    guardado = e.transcript or []
    nuevo = _normalizar_transcript(recibido) if recibido else []
    if len(nuevo) >= len(guardado):
        return nuevo or None
    return None


@router.post("/publica/{token}/finalizar")
async def finalizar(token: str, datos: FinalizarIn, db: Session = Depends(get_db)):
    """Cierra la entrevista DE VERDAD (Punto 4): registra cómo cerró (verificado), guarda el
    transcript, corre la evaluación IA (recomendación + perfil profundo) y mueve la postulación a
    Evaluación — sin que el candidato presione nada. Una entrevista con cierre por desconexión o
    tiempo y casi sin turnos del candidato queda `interrumpida` (sin evaluar) para que RH la reabra."""
    e = _por_token(db, token)
    if e.estado in ("evaluada", "interrumpida", "parcial"):
        return entrevista_dict(e)
    if e.estado != "en_curso":
        raise HTTPException(409, "La entrevista no está en curso; no hay nada que cerrar.")
    if not e.consentimiento:
        raise HTTPException(403, "La entrevista no tiene consentimiento del candidato.")
    return await finalizar_entrevista(db, e, _transcript_mas_completo(e, datos.transcript), datos.cierre)


async def finalizar_entrevista(db: Session, e: Entrevista, transcript: Optional[List[dict]], cierre: str) -> dict:
    """Cierre + evaluación (compartido por /finalizar y por el job de inactividad, 2026-09-17).
    `transcript`: None = conservar el ya guardado en `e.transcript`."""
    if transcript:
        e.transcript = [
            {"rol": ("assistant" if m.get("rol") == "assistant" else "user"), "texto": str(m.get("texto", ""))[:2000]}
            for m in transcript[:400]
        ]
    e.cierre = _cierre_verificado(e, cierre)
    e.finalizada_en = datetime.now(timezone.utc)
    turnos_candidato = sum(1 for m in (e.transcript or []) if m.get("rol") == "user")
    turnos_utiles, _chars = ia.texto_util_candidato(e.transcript or [])

    p, v, empresa = _contexto(e)
    guion = e.guion or {}
    temas = ia.temas_de_guion(guion)

    # 2026-09-13 — validación del transcript ANTES de cualquier evaluación:
    # (a) sin respuestas reales del candidato → interrumpida «Sin respuestas», nada de score.
    if turnos_utiles == 0:
        return _cerrar_sin_evaluar(db, e, p, "sin_respuestas", turnos_candidato)
    # (b) se cortó (desconexión/tiempo) con muy pocas respuestas → interrumpida.
    if e.cierre not in CIERRES_COMPLETOS and turnos_candidato < MIN_TURNOS_CANDIDATO:
        return _cerrar_sin_evaluar(db, e, p, "desconexion", turnos_candidato)
    # (c) contestó poco → la IA decide si alcanza; si no, «Entrevista parcial» sin score integral.
    faltante: List[str] = []
    if turnos_utiles < TURNOS_ENTREVISTA_COMPLETA:
        suf, _ = ia.suficiencia_entrevista(v.titulo if v else "vacante general", temas, e.transcript or [])
        if not suf.suficiente:
            return _cerrar_sin_evaluar(
                db, e, p, "parcial", turnos_candidato,
                {"faltante": suf.temas_faltantes, "cubierto": suf.temas_cubiertos, "motivo_ia": suf.motivo},
            )
        faltante = suf.temas_faltantes

    return await _evaluar_y_cerrar(db, e, p, v, empresa, temas, faltante, turnos_candidato)


async def _evaluar_y_cerrar(db: Session, e: Entrevista, p, v, empresa: str, temas: List[str], faltante: List[str], turnos_candidato: int, forzada_por: str = "") -> dict:
    """Evaluación IA + cierre `evaluada` + Kanban a Evaluación + aviso al candidato. Compartida por
    el cierre normal y por «Evaluar con lo que hay» (RH, 2026-09-17)."""
    e.estado = "completada"
    e.motivo = ""
    ev, con_ia = ia.evaluar_entrevista(
        v.titulo if v else "vacante general",
        v.requisitos if v else "",
        e.transcript or [],
        perfil_ideal=(v.perfil_ideal if v else "") or "",
        temas=temas,
        enfoque_entrevista=enfoque_entrevista_agente(p, v),
        faltante=faltante,
        analisis_cv=(p.analisis or {}) if p else {},
        cv_datos=(p.candidato.cv_datos or {}) if p and p.candidato else {},
    )
    if faltante and not ev.faltante:
        ev.faltante = faltante
    e.evaluacion = ev.model_dump()
    e.estado = "evaluada"
    # p.score / p.evidencia son el resultado de Luna sobre el CV (ver ia.AjustePerfil,
    # candidatos._aplicar_cv) — NUNCA se tocan aquí. El resultado del avatar vive completo y
    # aparte en Entrevista.evaluacion (match_perfil, evidencia, recomendación, perfil profundo).
    registrar(
        db, forzada_por or "agente-ia", "entrevista_evaluada", "entrevista", e.codigo,
        {"ia": con_ia, "recomendacion": ev.recomendacion, "match": ev.match_perfil, "cierre": e.cierre, "turnos_candidato": turnos_candidato,
         **({"forzada_por_rh": True} if forzada_por else {})},
    )

    # 2026-10-01 (pipeline de 5 columnas): la postulación se QUEDA en Filtro Red Human («Entrevista IA») con su
    # Evaluación integral calculada; ya no existe la columna «Evaluación». NO toca p.estado: la recomendación de la
    # IA queda solo como dato y RH decide el avance (HITL, ver _auto_decision_zero_touch en candidatos.py).
    if p and p.etapa == "Entrevista IA":
        registrar(
            db, "agente-ia", "auto_evaluacion_zero_touch", "postulacion", p.codigo,
            {"candidato": p.candidato.codigo, "entrevista": e.codigo, "recomendacion": ev.recomendacion, "match": ev.match_perfil},
        )

        primer_nombre = nombre_ficha(p)
        texto = (
            f"¡Gracias, {primer_nombre}! 🙌 Terminamos tu entrevista para {v.titulo if v else 'la vacante'} en {empresa}. "
            "El equipo de RH va a revisar tus resultados y te contactará pronto."
        )
        if p.telefono:
            try:
                envio = await enviar_mensaje(p.telefono, texto)
            except Exception as ex:  # que WhatsApp falle no debe tumbar el cierre de la entrevista
                print(f"[whatsapp-send-error] finalizar -> {e.codigo}: {ex}")
                envio = {"enviado": False, "proveedor": "error", "detalle": str(ex)}
            guardar_mensaje(db, p, "assistant", texto, "whatsapp", envio)

    db.commit()
    # Proceso configurable (2026-10-06): con el avance automático de Filtro Red Human encendido y sus obligatorios
    # cumplidos (p. ej. calificación mínima), pasa a la siguiente etapa con pasos. Apagado: RH decide.
    await avanzar_seguro(db, p)
    return entrevista_dict(e)




@router.post("/{codigo}/evaluar")
async def evaluar_con_lo_que_hay(
    codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """2026-09-17 — «Evaluar con lo que hay»: una entrevista `interrumpida`/`parcial` que SÍ tiene
    respuestas del candidato se evalúa por decisión explícita de RH (queda en bitácora con su nombre),
    sin esperar a que el candidato la repita. Sin ninguna respuesta útil no hay nada que evaluar (409)."""
    e = _por_codigo(db, codigo)
    p = e.postulacion
    if not p or p.cuenta_id != cuenta.id:
        raise HTTPException(404, "Entrevista no encontrada")
    if e.estado not in ("interrumpida", "parcial"):
        raise HTTPException(409, "Solo se puede forzar la evaluación de una entrevista interrumpida o parcial.")
    turnos_utiles, _ = ia.texto_util_candidato(e.transcript or [])
    if turnos_utiles == 0:
        raise HTTPException(409, "La entrevista no tiene respuestas del candidato; hay que reintentarla.")
    _p, v, empresa = _contexto(e)
    temas = ia.temas_de_guion(e.guion or {})
    faltante = list(((e.evaluacion or {}).get("faltante")) or [])
    turnos_candidato = sum(1 for m in (e.transcript or []) if m.get("rol") == "user")
    registrar(db, u.nombre, "entrevista_evaluacion_forzada", "entrevista", e.codigo, {"estado_previo": e.estado, "turnos_utiles": turnos_utiles, "correo_rh": u.correo})
    return await _evaluar_y_cerrar(db, e, p, v, empresa, temas, faltante, turnos_candidato, forzada_por=u.nombre)


# ------------------------------------------------------------
# Reapertura explícita (RH) — Fase 4, Punto 4
# ------------------------------------------------------------


class ReabrirIn(BaseModel):
    motivo: str = ""


@router.post("/{codigo}/reabrir")
def reabrir(
    codigo: str, datos: ReabrirIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Única forma de volver a aceptar respuestas tras un cierre: una persona de RH la reabre. El
    intento anterior (transcript, evaluación, cierre) se archiva en `intentos_previos`, nunca se
    pisa. La liga (token) es la misma; la postulación vuelve a Entrevista IA si estaba en Evaluación."""
    e = _por_codigo(db, codigo)
    p = e.postulacion
    if not p or p.cuenta_id != cuenta.id:
        raise HTTPException(404, "Entrevista no encontrada")
    if e.estado not in ("evaluada", "interrumpida", "parcial", "completada", "en_curso"):
        raise HTTPException(409, "La entrevista no está cerrada ni en curso.")
    reabrir_entrevista(db, e, u.nombre, datos.motivo, {"correo_rh": u.correo})
    db.commit()
    return entrevista_dict(e)


# ------------------------------------------------------------
# Job (2026-09-17): entrevistas en curso abandonadas → se cierran y evalúan con lo que hay
# ------------------------------------------------------------

# Minutos sin sincronización del transcript (ni turno de texto) para dar por abandonada la sesión.
INACTIVIDAD_ENTREVISTA_MIN = 15


async def cerrar_entrevistas_inactivas() -> int:
    """Una entrevista `en_curso` cuya pestaña se cerró (o cayó la red) sin `/finalizar` se quedaba
    así para siempre: la postulación en «Entrevista IA», sin evaluación y sin aviso. Ahora, pasados
    INACTIVIDAD_ENTREVISTA_MIN sin actividad, se cierra con cierre `desconexion` usando el transcript
    sincronizado: con respuestas suficientes se EVALÚA (misma lógica que /finalizar); con pocas queda
    `interrumpida`/`parcial` para que RH la reabra. Regresa cuántas cerró."""
    from ..database import SessionLocal

    corte = datetime.now(timezone.utc) - timedelta(minutes=INACTIVIDAD_ENTREVISTA_MIN)
    cerradas = 0
    with SessionLocal() as db:
        abiertas = db.query(Entrevista).filter(Entrevista.estado == "en_curso").all()
        for e in abiertas:
            ultima = e.ultima_actividad_en or e.iniciada_en or e.creada_en
            if ultima is not None and ultima.tzinfo is None:
                ultima = ultima.replace(tzinfo=timezone.utc)
            if ultima is None or ultima > corte:
                continue
            if not e.consentimiento:
                continue
            try:
                await finalizar_entrevista(db, e, None, "desconexion")
                registrar(
                    db, "sistema", "entrevista_cerrada_por_inactividad", "entrevista", e.codigo,
                    {"estado": e.estado, "ultima_actividad": ultima.isoformat(), "turnos": len(e.transcript or [])},
                )
                db.commit()
                cerradas += 1
            except Exception as ex:  # una entrevista rota no debe frenar a las demás
                db.rollback()
                print(f"[entrevistas] ⚠️ no se pudo cerrar {e.codigo} por inactividad: {ex}", flush=True)
    if cerradas:
        print(f"[entrevistas] {cerradas} entrevista(s) cerradas por inactividad.", flush=True)
    return cerradas
