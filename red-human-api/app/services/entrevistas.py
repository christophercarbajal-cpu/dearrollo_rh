"""
Creación del registro `Entrevista` (guion vía IA + liga pública con token) — lógica
compartida por dos caminos que la disparan por razones distintas:

  · routers/entrevistas.py `agendar()` — RH la agenda a mano desde el panel.
  · services/ia.agendar_videollamada_mock — el agente Zero-Touch, cuando el candidato
    confirma su disponibilidad por WhatsApp y hay avatar de Anam activo (ver ese archivo).

No manda WhatsApp ni hace `db.commit()`: cada camino avisa al candidato con un mensaje
distinto (uno fijo, el otro redactado por el agente), así que eso se queda con quien llama.

2026-10-09 (guiones por vacante): el guion SALE DE LA VACANTE (su plantilla de conversación de esa actividad, con las
ediciones de RH); si la vacante no lo tiene se genera Just-In-Time antes del primer mensaje (`guiones.asegurar`) — nunca
se arranca una conversación sin guion. Tres actividades de IA independientes: con avatar (sala), por WhatsApp (texto en
el chat, `turno_whatsapp`) y Llamada Red Human (voz, sala en modo llamada).
"""

import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from ..models import ETAPAS_LEGADO, Entrevista, Postulacion, registrar
from .proceso import enfoque_entrevista_agente
from . import ia
from .avatar import avatar_activo


def reabrir_entrevista(db: Session, e: Entrevista, actor: str, motivo: str = "", extra: Optional[dict] = None) -> dict:
    """Vuelve a aceptar respuestas tras un cierre. El intento anterior (transcript, evaluación,
    cierre) se archiva en `intentos_previos`, nunca se pisa; la liga (token) es la misma; la
    postulación vuelve a Entrevista IA si estaba en Evaluación. Fase 3 (2026-09-15): compartido
    entre RH (POST /entrevistas/{codigo}/reabrir) y el candidato por WhatsApp (solo interrumpida/
    parcial — ver candidatos._reanudar_entrevista_ia). No hace commit."""
    p = e.postulacion
    intento = {
        "estado": e.estado, "cierre": e.cierre, "iniciada_en": e.iniciada_en.isoformat() if e.iniciada_en else None,
        "finalizada_en": e.finalizada_en.isoformat() if e.finalizada_en else None,
        "transcript": list(e.transcript or []), "evaluacion": dict(e.evaluacion or {}),
    }
    e.intentos_previos = list(e.intentos_previos or []) + [intento]
    e.transcript = []
    e.evaluacion = {}
    e.cierre = ""
    e.motivo = ""
    e.iniciada_en = None
    e.finalizada_en = None
    e.estado = "programada"
    if p and p.etapa in ETAPAS_LEGADO:  # 2026-10-01: la columna «Evaluación» ya no existe
        p.etapa = ETAPAS_LEGADO[p.etapa]
    registrar(
        db, actor, "entrevista_reabierta", "entrevista", e.codigo,
        {"postulacion": p.codigo if p else None, "motivo": (motivo or "").strip()[:300], "intento_archivado": intento["estado"], **(extra or {})},
    )
    return intento


TIPO_DE_PASO = {"entrevista_whatsapp": "whatsapp", "llamada_agente": "llamada"}


def guion_de_vacante(db: Session, p: Postulacion, paso_tipo: str = "entrevista_agente") -> Tuple[dict, bool]:
    """Guion de la actividad tomado de la VACANTE (con las ediciones de RH). Si no existe se genera JIT y se guarda en la
    vacante. Sin vacante (postulación sin vacante todavía) se genera uno genérico, sin guardarlo."""
    from . import guiones as sgui

    v = p.vacante
    clave = sgui.SECCION_DE_PASO.get(paso_tipo, "entrevista_avatar")
    if v is not None:
        guion = sgui.asegurar(db, v, clave, motivo=f"primer mensaje de {p.codigo}")
        if not sgui.vacio(guion):
            meta = ((v.guiones or {}).get("meta") or {}).get(clave) or {}
            return {**guion, "seccion": clave}, bool(meta.get("generado_ia"))
    g, con_ia = ia.guion_entrevista(
        v.titulo if v else "vacante general", v.requisitos if v else "", p.experiencia or "",
        enfoque_entrevista=enfoque_entrevista_agente(p, v, paso_tipo), perfil_ideal=(v.perfil_ideal if v else "") or "",
        responsabilidades=list(v.responsabilidades or []) if v else [],
    )
    return g.model_dump(), con_ia


def refrescar_guion(e: Entrevista) -> None:
    """Antes del PRIMER turno se toma la versión vigente del guion de la vacante: lo que RH editó después de mandar la
    liga también aplica. Ya iniciada, la entrevista conserva el guion con el que empezó (auditoría)."""
    from . import guiones as sgui

    seccion = (e.guion or {}).get("seccion")
    v = e.postulacion.vacante if e.postulacion is not None else None
    if not seccion or v is None or e.transcript or e.estado not in ("programada",):
        return
    actual = sgui.contenido_de(v, seccion)
    if not sgui.vacio(actual) and {k: actual.get(k) for k in ("enfoque", "temas", "preguntas")} != {
            k: (e.guion or {}).get(k) for k in ("enfoque", "temas", "preguntas")}:
        e.guion = {**actual, "seccion": seccion}


def crear_entrevista_para_candidato(
    db: Session,
    p: Postulacion,
    actor: str,
    programada_para: Optional[datetime] = None,
    paso_tipo: str = "entrevista_agente",
) -> Tuple[Entrevista, bool]:
    """Crea la `Entrevista` (con su token) de la actividad `paso_tipo` para la postulación `p`, con el guion de su
    vacante (JIT si falta). `actor` firma la bitácora — el nombre de RH en el camino manual, "agente-ia" en Zero-Touch,
    igual que el resto de acciones automáticas del agente."""
    guion, con_ia = guion_de_vacante(db, p, paso_tipo)
    e = Entrevista(
        codigo="TMP",
        candidato_id=p.candidato_id,
        token=secrets.token_urlsafe(24),
        tipo=TIPO_DE_PASO.get(paso_tipo) or ("avatar" if avatar_activo() else "texto"),
        guion=guion,
        programada_para=programada_para,
    )
    p.entrevistas.append(e)
    db.add(e)
    db.flush()
    e.codigo = f"ENT-{300 + e.id}"
    if p.etapa == "Prefiltro":
        p.etapa = "Entrevista IA"  # ver ETAPAS_CANDIDATO — la entrevista con avatar también es "IA"
    registrar(db, actor, "entrevista_agendada", "entrevista", e.codigo, {"candidato": p.candidato.codigo, "postulacion": p.codigo, "ia": con_ia,
                                                                        "tipo": e.tipo, "guion": (e.guion or {}).get("seccion") or "generico"})
    return e, con_ia


# ============================================================ Entrevista Red Human por WhatsApp (2026-10-09)

_AFIRMA = ("si", "sí", "listo", "lista", "adelante", "vamos", "claro", "ok", "va", "dale", "comencemos", "empecemos", "comenzar",
           "empezar", "acepto", "de acuerdo")
_NIEGA = ("no", "luego", "despues", "después", "mas tarde", "más tarde", "ahorita no", "ahora no", "mañana", "manana")


def entrevista_whatsapp_activa(p: Postulacion) -> Optional[Entrevista]:
    """La entrevista por WhatsApp que conduce el chat: solo mientras la postulación sigue en Filtro Red Human (si RH
    la movió u omitió la actividad, el chat ya no le pertenece)."""
    if not p.activa or p.etapa != "Entrevista IA":
        return None
    return next((e for e in reversed(list(p.entrevistas or [])) if e.tipo == "whatsapp" and e.estado in ("programada", "en_curso")), None)


# 2026-10-12: la transición a la Entrevista Red Human por WhatsApp lleva SIEMPRE esta primera pregunta, inyectada en código
# (nunca la redacta el LLM ni sale del guion guardado, que pudo quedar con «Cuéntame…»). El LLM toma el control desde la
# respuesta del candidato a esta pregunta, con la pregunta 2 del guion.
PRIMERA_PREGUNTA_FIJA = "¿Cuál fue tu último trabajo?"


def guion_conversacion(e: Entrevista) -> dict:
    """El guion que se conversa. Por WhatsApp, la primera pregunta es SIEMPRE `PRIMERA_PREGUNTA_FIJA`: sustituye a la
    primera de Trayectoria del guion operativo (en los demás enfoques, a la primera del guion) — así la lista que ven el LLM y el
    motor sin IA coincide con lo que ya se preguntó en la transición."""
    import copy

    from . import entrevista_operativa as eop

    guion = copy.deepcopy(e.guion or {})
    if e.tipo != "whatsapp":
        return guion
    preguntas = list(guion.get("preguntas") or [])
    op = guion.get("operativo") or {}
    tray = next((s for s in op.get("secciones") or [] if s.get("clave") == "trayectoria"), None)
    if tray and tray.get("preguntas"):
        vieja = tray["preguntas"][0]
        tray["preguntas"] = [PRIMERA_PREGUNTA_FIJA] + [q for q in tray["preguntas"][1:] if eop._n(eop.texto_pregunta(q)) != eop._n(PRIMERA_PREGUNTA_FIJA)]
        preguntas = [q for q in preguntas if q != vieja]
    else:
        preguntas = preguntas[1:]  # la primera del guion es la de apertura: la sustituye la fija (el LLM sigue con la 2)
    preguntas = [PRIMERA_PREGUNTA_FIJA] + [q for q in preguntas if eop._n(eop.texto_pregunta(q)) != eop._n(PRIMERA_PREGUNTA_FIJA)]
    guion["preguntas"] = preguntas
    return guion


def system_prompt(e: Entrevista, historial: Optional[list] = None) -> str:
    """El MISMO prompt del entrevistador para las tres actividades, con el guion de la vacante y su canal."""
    from ..serial import nombre_empresa_candidato
    from ..routers.entrevistas import _nombre_entrevistado as nombre_para_entrevista

    p = e.postulacion
    v = p.vacante if p else None
    guion = guion_conversacion(e)
    bloque = ""
    from .evaluacion_integral import respuestas_previas

    previas = respuestas_previas(p) if p else []
    if guion.get("operativo"):
        from . import entrevista_operativa as eop

        bloque = eop.bloque_prompt(guion, previas, historial if historial is not None else e.transcript)
    return ia.prompt_entrevistador(
        v.titulo if v else "vacante general", v.requisitos if v else "", nombre_para_entrevista(e),
        list(guion.get("preguntas") or []), empresa=nombre_empresa_candidato(v) if v else "la empresa",
        temas=ia.temas_de_guion(guion), enfoque=guion.get("enfoque", ""),
        enfoque_entrevista=enfoque_entrevista_agente(p, v, _paso_de(e)), ubicacion=(v.ubicacion if v else "") or "",
        modalidad=(v.modalidad if v else "") or "", sueldo=(v.sueldo if v else "") or "",
        beneficios=list(v.beneficios or []) if v else [], area=(v.area if v else "") or "",
        canal=e.tipo, guion_de_vacante=bool(guion.get("seccion")), bloque_operativo=bloque,
        ya_respondido=[] if bloque else previas,  # Cambio 52 (el bloque operativo ya trae lo respondido)
    ) + (f"\nLA PRIMERA PREGUNTA («{PRIMERA_PREGUNTA_FIJA}») YA SE HIZO en el mensaje de transición: no la repitas ni "
         "vuelvas a presentarte; toma su respuesta y continúa con la SIGUIENTE pregunta del guion.\n" if e.tipo == "whatsapp" else "")


def preguntas_de(e: Entrevista, historial: Optional[list] = None) -> list:
    """Preguntas que se hacen en esta entrevista. Operativa: sin las de inicio/condiciones que ya cubrió el prefiltro ni
    la evidencia de un indispensable que el candidato ya demostró (Cambio 19)."""
    guion = guion_conversacion(e)
    if guion.get("operativo") and e.postulacion is not None:
        from . import entrevista_operativa as eop
        from .evaluacion_integral import respuestas_previas

        return eop.preguntas_para_conversacion(guion, respuestas_previas(e.postulacion), historial if historial is not None else e.transcript)
    return list(guion.get("preguntas") or [])


def mensaje_inicial(p: Postulacion, primera: str = "", pedir_permiso: bool = False) -> str:
    """«Listo, [nombre]. Ahora te haré unas preguntas sobre tu experiencia. ¿Cuál fue tu último trabajo?» (Cambio 19): un solo
    mensaje, sin presentarse de nuevo, sin reglas internas ni «¿Comenzamos?»."""
    from ..routers.candidatos import nombre_ficha

    nombre = nombre_ficha(p).split(" ")[0] if nombre_ficha(p) and not nombre_ficha(p).startswith("Candidato") else ""
    return ia.mensaje_inicial_entrevista_whatsapp(p.vacante.titulo if p.vacante else "", nombre, primera, pedir_permiso)


def primera_pregunta(e: Entrevista) -> str:
    """Primera pregunta — va en el mismo mensaje de la transición. Por WhatsApp es SIEMPRE la fija (inyectada en código,
    nunca generada); en otros canales, la primera del guion."""
    from . import entrevista_operativa as eop

    if e.tipo == "whatsapp":
        return PRIMERA_PREGUNTA_FIJA
    qs = [q for q in preguntas_de(e) if str(q or "").strip()]
    return eop.texto_pregunta(qs[0]) if qs else PRIMERA_PREGUNTA_FIJA


def _paso_de(e: Entrevista) -> str:
    from ..models import paso_de_entrevista

    return paso_de_entrevista(e.tipo)


def _norm(t: str) -> str:
    import unicodedata

    return " ".join(unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower().split())


def directo_de_solicitud(p: Postulacion) -> bool:
    """2026-10-15 (Grupak «Operativo básico»): la Entrevista Red Human por WhatsApp sigue DIRECTO a la solicitud web (sin
    prefiltro por WhatsApp): el bot todavía no ha hablado con el candidato."""
    pasos = [x for x in (p.proceso or {}).get("pasos") or [] if not x.get("heredado")]
    i = next((n for n, x in enumerate(pasos) if x.get("tipo") == "entrevista_whatsapp"), None)
    if i is None:
        return False
    antes = {x.get("tipo") for x in pasos[:i]}
    return "solicitud_web" in antes and not antes & {"prefiltro_whatsapp", "prefiltro_web", "entrevista_agente", "llamada_agente"}


async def _iniciar_directo(db: Session, p: Postulacion, e: Entrevista, actor: str, paso_id: str,
                           ahora: datetime) -> Tuple[bool, str, Optional[bool], Entrevista]:
    """Primer mensaje cuando la entrevista sigue directo a la solicitud: «Hola, [nombre]. Soy Red Human, el agente
    virtual de [empresa]. Recibimos tu postulación para [puesto]. Ahora te haré unas preguntas sobre tu experiencia.
    [primera pregunta]». Sale por la cascada: si el candidato no ha escrito (sin ventana de 24 h) va la plantilla
    `inicio_entrevista_rh` y la entrevista espera su «Sí» para empezar (Cambio 51: transición, nunca «RH te escribirá»)."""
    from ..config import settings
    from . import entrevista_whatsapp_fija as ewf
    from . import plantillas_meta
    from .ia import presentacion_bot
    from .mensajeria import de_cuenta
    from .prefiltro_conversacional import empresa_de, marcar_presentado, ya_presentado
    from .reintento_plantillas import registrar_envio
    from .whatsapp import enviar_en_cascada

    refrescar_guion(e)
    primera = ewf.primera(e) if ewf.aplica(e) else PRIMERA_PREGUNTA_FIJA
    v = plantillas_meta.valores_inicio(p)
    nombre = v.get("nombre") or ""
    presentacion = "" if ya_presentado(p) else f" {presentacion_bot(empresa_de(p))}"
    nombre = "" if nombre in ("-", "candidato(a)") else nombre
    texto = (f"Hola{', ' + nombre if nombre else ''}.{presentacion} Recibimos tu postulación para {v.get('puesto') or 'la vacante'}. "
             f"Ahora te haré unas preguntas sobre tu experiencia. {primera}")
    marcar_presentado(p)
    try:
        with de_cuenta(p.cuenta_id):
            envio = await enviar_en_cascada(p.telefono, texto, plantillas_meta.nombre_plantilla_inicio(), plantillas_meta.parametros_inicio(p),
                                            settings.meta_plantilla_idioma)
    except Exception as ex:  # noqa: BLE001
        envio = {"enviado": False, "detalle": f"{type(ex).__name__}: {str(ex)[:200]}"}
    from ..routers.candidatos import guardar_mensaje

    g = dict(e.guion or {})
    if envio.get("enviado") and envio.get("formato") != "plantilla":
        # texto libre entregado: la entrevista arranca en este mensaje (consentimiento = aviso de privacidad aceptado)
        e.consentimiento, e.consentimiento_fecha = True, ahora
        e.estado, e.iniciada_en = "en_curso", ahora
        e.transcript = [{"rol": "assistant", "texto": texto}]
        guardar_mensaje(db, p, "assistant", texto, "whatsapp", envio)
    else:
        # plantilla de inicio (o pendiente de plantilla): la entrevista espera el «Sí» del candidato
        g["via_plantilla"] = True
        plantillas_meta._guardar(p, {"enviada_en": ahora.isoformat(), "plantilla": plantillas_meta.nombre_plantilla_inicio(),
                                     "entregada": bool(envio.get("enviado")), "entrevista": e.codigo})
        if envio.get("enviado") or envio.get("estado") == "pendiente":
            guardar_mensaje(db, p, "assistant", plantillas_meta.texto_inicio(p) if envio.get("enviado") else f"[Pendiente de plantilla] {texto}",
                            "whatsapp", envio)
    e.guion = g
    try:
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(e, "guion")
    except Exception:  # noqa: BLE001
        pass
    e.ultima_actividad_en = ahora
    try:
        registrar_envio(db, p, "inicio_entrevista_whatsapp", envio, "inicio_prefiltro")
    except Exception as ex:  # noqa: BLE001
        print(f"[entrevistas] {p.codigo}: no se registró el inicio — {ex}", flush=True)
    from . import envios

    envios.registrar(db, p, [{"destinatario": "candidato", "canal": "whatsapp", "destino": p.telefono, **envio}],
                     motivo="entrevista", paso_id=paso_id, actor=actor, destinatario="candidato")
    entregado = bool(envio.get("enviado"))
    ok = entregado or envio.get("estado") == "pendiente"
    return ok, ("Entrevista iniciada por WhatsApp" if entregado and envio.get("formato") != "plantilla" else
                "Plantilla de inicio enviada" if entregado else "Pendiente de plantilla" if ok else
                f"El mensaje no salió ({envio.get('error') or envio.get('detalle') or ''})"), entregado, e


async def iniciar_whatsapp(db: Session, p: Postulacion, actor: str, paso_id: str = "") -> Tuple[bool, str, Optional[bool], Entrevista]:
    """Crea (o reutiliza) la entrevista por WhatsApp y manda la transición. 2026-10-11 (tarde): sin pedir permiso — el
    candidato ya aceptó el aviso de privacidad (que dice que el proceso lo conduce una IA), así que la entrevista arranca
    en ese mismo mensaje con la primera pregunta del guion. Solo una postulación sin consentimiento registrado conserva el
    «¿Comenzamos?» de antes (su «Sí» es el consentimiento)."""
    from .motor_ruta import _avisar

    e = entrevista_whatsapp_activa(p)
    if e is None:
        e, _ = crear_entrevista_para_candidato(db, p, actor, paso_tipo="entrevista_whatsapp")
        db.flush()
    if not p.telefono:
        return False, "El candidato no tiene teléfono: la entrevista por WhatsApp no se puede iniciar.", None, e
    ahora = datetime.now(timezone.utc)
    if p.consentimiento and e.estado == "programada" and directo_de_solicitud(p) and not any(m.rol == "user" for m in p.mensajes or []):
        return await _iniciar_directo(db, p, e, actor, paso_id, ahora)
    if p.consentimiento and e.estado == "programada":
        refrescar_guion(e)
        from . import entrevista_whatsapp_fija as ewf

        # Cambio 29: rutas Masivos — entrevista FIJA (situación actual → historial → preguntas de RH → confirmación)
        texto = ewf.transicion(p, ewf.primera(e)) if ewf.aplica(e) else mensaje_inicial(p, primera_pregunta(e))
        e.consentimiento = True
        e.consentimiento_fecha = ahora
        e.estado = "en_curso"
        e.iniciada_en = ahora
        e.transcript = [{"rol": "assistant", "texto": texto}]
        registrar(db, p.candidato.codigo if p.candidato else "candidato", "consentimiento_entrevista", "entrevista", e.codigo,
                  {"canal": "whatsapp", "medio": "aviso_privacidad", "postulacion": p.codigo})
    else:
        texto = mensaje_inicial(p, pedir_permiso=e.estado == "programada")
    e.ultima_actividad_en = ahora
    entregado, detalle = await _avisar(db, p, texto, "Tu entrevista con Red Human", "", motivo="entrevista", paso_id=paso_id, actor=actor)
    return True, (f"Entrevista iniciada por {detalle}" if entregado else f"Entrevista creada; el mensaje no salió ({detalle})"), entregado, e


async def turno_whatsapp(db: Session, p: Postulacion, e: Entrevista, texto: str, canal: str) -> dict:
    """Un turno de la Entrevista Red Human por WhatsApp (el mensaje del candidato ya está guardado en el chat). Mismo
    prompt, mismo cierre verificado y misma evaluación que la sala (`finalizar_entrevista`)."""
    from ..routers.candidatos import _actualizar_ultima_actividad, _enviar_whatsapp, guardar_mensaje
    from ..routers.entrevistas import finalizar_entrevista

    ahora = datetime.now(timezone.utc)

    async def responder(mensaje: str, **extra) -> dict:
        envio = await _enviar_whatsapp(p, mensaje, canal)
        guardar_mensaje(db, p, "assistant", mensaje, canal, envio)
        _actualizar_ultima_actividad(p)
        e.ultima_actividad_en = ahora
        db.commit()
        return {"respuesta": mensaje, "clasificacion": None, "ia": False, "whatsapp": envio, "entrevista": e.codigo, **extra}

    t = _norm(texto)
    if e.estado == "programada":
        palabras = set(t.split())
        afirma = bool(palabras & {_norm(x) for x in _AFIRMA if " " not in x}) or any(x in t for x in ("de acuerdo", "estoy listo", "estoy lista"))
        niega = t in {_norm(x) for x in _NIEGA} or t.startswith(("no ", "ahorita no", "ahora no", "mas tarde", "luego"))
        if niega or not afirma:
            if niega:
                return await responder("Sin problema. Cuando quieras comenzar tu entrevista, escríbeme «Comenzar». 🙂", esperando=True)
            return await responder("Para comenzar tu entrevista con Red Human respóndeme «Sí» cuando estés listo(a). 🙂", esperando=True)
        refrescar_guion(e)
        e.consentimiento = True
        e.consentimiento_fecha = ahora
        e.estado = "en_curso"
        e.iniciada_en = ahora
        via_plantilla = bool((e.guion or {}).get("via_plantilla"))
        if via_plantilla:
            from . import plantillas_meta

            plantillas_meta.marcar_respuesta(db, p, texto)
        intro = mensaje_inicial(p, pedir_permiso=True)
        registrar(db, p.candidato.codigo if p.candidato else "candidato", "consentimiento_entrevista", "entrevista", e.codigo,
                  {"canal": canal, "respuesta": texto[:120]})
        # la primera pregunta se inyecta fija (el LLM empieza con la respuesta del candidato a ella)
        from . import entrevista_whatsapp_fija as ewf

        primera = ewf.primera(e) if ewf.aplica(e) else PRIMERA_PREGUNTA_FIJA
        if via_plantilla:
            # 2026-10-15: contestó la plantilla de inicio («Sí, empezar ahora») → transición + primera pregunta (Cambio 51)
            primera = ewf.transicion(p, primera) if ewf.aplica(e) else mensaje_inicial(p, primera)
        e.transcript = [{"rol": "assistant", "texto": intro}, {"rol": "user", "texto": texto[:2000]},
                        {"rol": "assistant", "texto": primera}]
        return await responder(primera, ia_entrevista=False)
    historial = list(e.transcript or []) + [{"rol": "user", "texto": texto[:2000]}]
    from . import entrevista_whatsapp_fija as ewf

    if ewf.aplica(e):
        # Cambio 29: rutas Masivos — entrevista FIJA, sin IA generativa
        mensaje, terminada = ewf.turno(e, texto)
        e.transcript = historial + [{"rol": "assistant", "texto": mensaje}]
        if terminada:
            from . import respuestas_vacante as rva

            rva.marcar_cierre(p, "entrevista_whatsapp", mensaje)  # Cambio 59: después del cierre el chat queda pasivo
        salida = await responder(mensaje, ia_entrevista=False)
        if terminada:
            db.refresh(e)
            await finalizar_entrevista(db, e, None, "texto")
            salida["terminada"] = True
        return salida
    turno, con_ia = ia.entrevista_turno(system_prompt(e, historial), historial, preguntas=preguntas_de(e, historial),
                                        operativo=bool((e.guion or {}).get("operativo")))
    e.transcript = historial + [{"rol": "assistant", "texto": turno.respuesta}]
    terminada = turno.terminada or ia.DESPEDIDA_ENTREVISTA.lower() in (turno.respuesta or "").lower()
    if terminada:
        from . import respuestas_vacante as rva

        rva.marcar_cierre(p, "entrevista_whatsapp", turno.respuesta)  # Cambio 59
    salida = await responder(turno.respuesta, ia_entrevista=con_ia)
    if terminada:
        db.refresh(e)
        await finalizar_entrevista(db, e, None, "texto")
        salida["terminada"] = True
    return salida
