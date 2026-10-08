"""Webhook de WhatsApp (Meta Cloud API) — el agente de prefiltro conversa con el candidato.

Fase 2 (Puntos 7/8): un número de WhatsApp es una PERSONA (`Candidato`), pero la conversación
es siempre sobre UNA `Postulacion`. Cómo se decide cuál (decisión de negocio 2026-09-11,
"contexto de conversación + preguntar"):

1. `Candidato.postulacion_conversacion_id` apunta a la postulación en conversación. Lo mueve
   SOLO el candidato: un mensaje entrante suyo que se enruta a una postulación, o una
   selección explícita en la lista interactiva. Un mensaje saliente/proactivo (plantilla de
   inicio tras /aplicar, aviso de apto, recordatorio, notificación de RH) NUNCA lo mueve
   (decisión B1, 2026-09-11) — ver candidatos.fijar_conversacion.
2. Si el puntero apunta a una postulación que sigue esperando respuesta, el mensaje va ahí.
3. Si no, y exactamente UNA postulación activa espera respuesta, se cambia el puntero a esa.
4. Si VARIAS esperan respuesta, el agente manda una lista interactiva solo con SUS vacantes
   en curso y no procesa nada hasta que elija. Nunca adivina.
5. Si ninguna espera respuesta: se sigue en la del puntero (respuesta fija de "ya te
   contactamos"), o —si el candidato eligió explícitamente una vacante del menú— se abre una
   postulación nueva para esa vacante. Sin nada activo, nace una postulación sin vacante y se
   le manda el menú de vacantes publicadas.

WhatsApp multi-tenant (2026-09-17) — UN número maestro para varias Cuentas:
- `_alcance_whatsapp` decide el ALCANCE (lista de Cuentas activas) del mensaje entrante: si el número
  que lo recibió está reservado por una Cuenta (`Cuenta.whatsapp_exclusivo` + `whatsapp_comunicacion`,
  opción Premium) el alcance es solo esa Cuenta (ruteo dedicado, comportamiento anterior); si no, el
  alcance son TODAS las Cuentas activas (número compartido).
- Persona y postulaciones se resuelven dentro del alcance (una persona puede tener filas `Candidato`
  en varias Cuentas: cada Cuenta solo ve la suya). El menú lista las vacantes publicadas de todo el
  alcance agrupadas por empresa; con más de 10 primero se pregunta la empresa (ids `CTA-<id>`).
- Al elegir vacante, `_amarrar_a_cuenta` deja la postulación, sus mensajes y (después) su expediente en
  la `cuenta_id` de ESA vacante: si la persona nació en el número compartido y no tiene otro proceso,
  la persona misma se mueve a esa Cuenta; si ya tiene procesos en otra Cuenta, se crea/reutiliza su
  fila en la Cuenta destino. El candidato nunca nota el número compartido: toda identidad de empresa
  sale de la vacante (`serial.nombre_empresa_candidato`).

Telegram (2026-10-06): `procesar_entrante(db, msg)` es el procesamiento COMÚN. El webhook de WhatsApp le pasa lo que
normaliza `parsear_webhook`; el de Telegram (`routers/webhooks_telegram.py`) lo que normaliza `telegram.parsear_update`
con `msg["canal"]="telegram"`, dentro de `mensajeria.en_conversacion` (las respuestas a esa persona salen por Telegram).
El alcance de cada canal respeta `Cuenta.canal_mensajeria` (`mensajeria.alcance_canal`). Con proceso configurable, lo
que se le contesta al candidato lo decide el proceso de su postulación (`services/canal_proceso.py`).
"""

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import cuenta_actual, usuario_actual
from ..models import CONTEXTO_WHATSAPP_HORAS, ETAPAS_CONTEXTO_LARGO, Bitacora, Candidato, Cuenta, Postulacion, Usuario, Vacante, registrar
from ..serial import nombre_empresa_candidato
from ..services.configuracion import modo_prueba_activo, ventana_modo_prueba_min
from ..services.mensajeria import alcance_canal, canal_conversacion, en_conversacion
from ..services.whatsapp import descargar_media, enviar_mensaje, enviar_lista_interactiva, parsear_webhook
from .candidatos import (
    _actualizar_ultima_actividad,
    _crear_candidato,
    crear_postulacion,
    fijar_conversacion,
    guardar_mensaje,
    postulacion_para_vacante,
    procesar_prefiltro,
)
from .contratacion import adjuntar_documento_bytes, documento_para_adjunto

# Modo Prueba: una conversación con actividad más vieja que la ventana configurada
# (ConfiguracionSistema.modo_prueba_ventana_min, Punto 13; 60 min por defecto) ya no se
# reutiliza — se cierra la postulación y se trata como una nueva e independiente
# (ver _resolver_postulacion). Con Modo Prueba apagado no aplica.
VENTANA_MODO_PRUEBA_DEFAULT = timedelta(minutes=60)

router = APIRouter(tags=["webhooks"])

# Regex para detectar códigos de vacante en el texto del candidato
_RE_VAC = re.compile(r"VAC[-_]?([A-Za-z0-9]+)", re.IGNORECASE)

# Palabras (completas, ya sin acentos) que interpretamos como consentimiento LFPDPPP. Se compara
# por palabra, no por subcadena: "va" no debe dispararse con "vacante" ni "si" con "sin".
_ACEPTA = {"si", "acepto", "aceptar", "autorizo", "ok", "va", "vale", "dale", "claro", "supuesto", "acuerdo", "adelante"}


# ============================================================
# Helpers internos
# ============================================================

def _normalizar_str(s: str) -> str:
    """Quita acentos y pasa a minúsculas para comparaciones flexibles."""
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def _es_aceptacion(texto: str) -> bool:
    """True si el mensaje contiene una palabra de aceptación ("Sí", "Acepto", "De acuerdo"…)."""
    return bool(set(re.findall(r"[a-z]+", _normalizar_str(texto))) & _ACEPTA)


def _normalizar_telefono(wa_id: str) -> str:
    """521XXXXXXXXXX → 10 dígitos mexicanos para dedup con Candidato.telefono."""
    digitos = re.sub(r"\D", "", wa_id)
    if digitos.startswith("521") and len(digitos) == 13:
        return digitos[3:]  # quitar 521
    if digitos.startswith("52") and len(digitos) == 12:
        return digitos[2:]  # quitar 52
    return digitos[-10:] if len(digitos) > 10 else digitos


def _origen() -> str:
    """Origen de la postulación / fuente de la persona según el canal del mensaje entrante."""
    return "telegram" if canal_conversacion() == "telegram" else "whatsapp"


def _ids(alcance) -> List[int]:
    """Acepta un id de Cuenta o una lista de ids (alcance del número compartido)."""
    if isinstance(alcance, int):
        return [alcance]
    return [int(x) for x in alcance]


def _vacantes_publicadas(db: Session, alcance, limite: int = 10) -> list:
    """Vacantes que el agente ofrece por WhatsApp: SOLO las «Publicada» de las Cuentas del alcance,
    leídas de la base en cada turno (nunca se cachean: una vacante recién creada o recién eliminada se
    refleja en el siguiente mensaje). Borrador / Cerrada / Eliminada nunca salen."""
    q = (
        db.query(Vacante)
        .filter(Vacante.estado == "Publicada", Vacante.cuenta_id.in_(_ids(alcance)))
        .order_by(Vacante.id.desc())
    )
    return q.limit(limite).all() if limite else q.all()


def _vacante_por_codigo(db: Session, alcance, codigo: str) -> Optional[Vacante]:
    """Selección por código (lista interactiva o «VAC-####» escrito) restringida a vacantes
    PUBLICADAS del alcance: el candidato puede tocar una lista vieja cuya vacante ya se eliminó o cerró
    y eso no debe abrirle una postulación en una vacante que ya no existe para RH."""
    return (
        db.query(Vacante)
        .filter(func.lower(Vacante.codigo) == codigo.lower(), Vacante.cuenta_id.in_(_ids(alcance)), Vacante.estado == "Publicada")
        .first()
    )


def _detectar_vacante(texto: str, db: Session, cuenta_id, id_seleccionado: Optional[str] = None) -> Optional[Vacante]:
    """Busca la vacante por ID interactivo, código VAC-XXXX, número de lista, título o slug."""
    candidatos_cod = [s for s in [id_seleccionado, texto] if s]

    # 1. Búsqueda por código exacto o regex VAC-XXXX
    for s in candidatos_cod:
        s_clean = s.strip()
        # Coincidencia directa por código (solo publicadas)
        v = _vacante_por_codigo(db, cuenta_id, s_clean)
        if v:
            return v
        # Regex VAC-####
        m = _RE_VAC.search(s_clean)
        if m:
            v = _vacante_por_codigo(db, cuenta_id, f"VAC-{m.group(1)}")
            if v:
                return v

    vacantes_activas = _vacantes_publicadas(db, cuenta_id)

    # 2. Búsqueda por número si el usuario respondió "1", "2", etc.
    t_clean = (texto or "").strip()
    if t_clean.isdigit():
        num = int(t_clean)
        if 1 <= num <= len(vacantes_activas):
            return vacantes_activas[num - 1]

    # 3. Búsqueda por título o slug (flexible / sin acentos)
    t_norm = _normalizar_str(texto)
    id_norm = _normalizar_str(id_seleccionado or "")
    if t_norm or id_norm:
        for v in vacantes_activas:
            v_tit = _normalizar_str(v.titulo)
            v_slug = _normalizar_str(v.slug)
            if t_norm and (v_tit in t_norm or t_norm in v_tit or v_slug == t_norm):
                return v
            if id_norm and (v_tit in id_norm or id_norm in v_tit or v_slug == id_norm):
                return v

    return None


def _vacante_explicita(db: Session, cuenta_id, texto: str, id_seleccionado: str) -> Optional[Vacante]:
    """Solo selecciones INEQUÍVOCAS (respuesta a la lista interactiva o código VAC-#### escrito):
    sirve para que una persona con procesos ya cerrados pueda abrir otra postulación desde
    WhatsApp. Un "3" suelto o un título aproximado NO cuentan aquí — podrían ser respuestas
    de prefiltro."""
    for s in [id_seleccionado, texto]:
        s = (s or "").strip()
        if not s:
            continue
        v = _vacante_por_codigo(db, cuenta_id, s)
        if v:
            return v
        m = _RE_VAC.search(s)
        if m:
            v = _vacante_por_codigo(db, cuenta_id, f"VAC-{m.group(1)}")
            if v:
                return v
    return None


def _personas_en_alcance(db: Session, wa_id: str, alcance) -> List[Candidato]:
    """Filas `Candidato` de este número dentro del alcance: UNA por Cuenta (la más reciente — con Modo
    Prueba puede haber varias personas con el mismo número en la misma Cuenta y la nueva es la que
    manda, ver `_buscar_o_crear_candidato`), más reciente primero."""
    tel = _normalizar_telefono(wa_id)
    condiciones = [Candidato.wa_id == wa_id]
    if tel:
        condiciones.append(Candidato.telefono == tel)
    filas = (
        db.query(Candidato)
        .filter(or_(*condiciones), Candidato.cuenta_id.in_(_ids(alcance)), Candidato.eliminado_en.is_(None))
        .order_by(Candidato.id.desc())
        .all()
    )
    por_cuenta: dict = {}
    for c in filas:
        por_cuenta.setdefault(c.cuenta_id, c)
    return list(por_cuenta.values())


def _buscar_o_crear_candidato(db: Session, wa_id: str, nombre: str, cuenta_id: int, prueba: bool) -> Candidato:
    """Resuelve a la PERSONA por wa_id (exacto) O por teléfono normalizado, en UNA sola consulta,
    y se queda con la más reciente; la crea si no existe. Con Modo Prueba puede haber más de una
    persona con el mismo número (cada postulación web de prueba crea una persona nueva): la nueva
    todavía no tiene wa_id (solo teléfono) y la vieja sí — si se buscara primero por wa_id, la
    respuesta al botón de la plantilla caería en la persona/postulación VIEJA y el proceso nuevo
    nunca arrancaría."""
    tel = _normalizar_telefono(wa_id)
    condiciones = [Candidato.wa_id == wa_id]
    if tel:
        condiciones.append(Candidato.telefono == tel)
    existente = (
        db.query(Candidato)
        .filter(or_(*condiciones), Candidato.cuenta_id == cuenta_id, Candidato.eliminado_en.is_(None))  # CRUD: eliminados no se reutilizan
        .order_by(Candidato.id.desc())
        .first()
    )
    if existente:
        if not existente.wa_id:
            existente.wa_id = wa_id
        if nombre and not existente.wa_nombre:
            existente.wa_nombre = nombre
        return existente

    fuente = "Telegram" if _origen() == "telegram" else "WhatsApp"
    c = _crear_candidato(
        db, cuenta_id, nombre or f"Candidato {fuente}", fuente, prueba,
        telefono=tel, wa_id=wa_id, wa_nombre=nombre,
    )
    registrar(db, "sistema", "candidato_ingresado", "candidato", c.codigo, {"fuente": fuente, "wa_id": wa_id, "es_prueba": prueba})
    return c


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    # SQLite descarta el offset de un DateTime(timezone=True) y regresa un datetime naive con
    # los mismos números de reloj UTC — hay que reponerle el tzinfo antes de restar.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _ultima_actividad(p: Postulacion) -> datetime:
    """Último rastro de vida de ESTA postulación: mensaje del chat, actividad registrada por RH/IA
    (entrevista, documento, etapa) o su creación. Antes se miraba solo el último mensaje de la
    persona, así que una entrevista agendada hace 2 h por RH no contaba como actividad."""
    marcas = [p.creado_en, p.ultima_actividad_en, p.mensajes[-1].creado_en if p.mensajes else None]
    return max(_utc(m) for m in marcas if m is not None)


def _ventana_contexto(db: Session, p: Postulacion) -> Tuple[timedelta, str]:
    """(ventana, motivo_cierre) que aplica a esta postulación en Modo Prueba (2026-09-15):
    en Prefiltro la ventana corta configurada (`prueba_expirada`); en etapas avanzadas la memoria
    de CONTEXTO_WHATSAPP_HORAS (5 días) y, pasada, se cierra por `sin_interes`."""
    if p.etapa in ETAPAS_CONTEXTO_LARGO:
        return timedelta(hours=CONTEXTO_WHATSAPP_HORAS), "sin_interes"
    return (timedelta(minutes=ventana_modo_prueba_min(db)) or VENTANA_MODO_PRUEBA_DEFAULT), "prueba_expirada"


def _conversacion_fria(db: Session, p: Postulacion) -> Tuple[bool, str]:
    ventana, motivo = _ventana_contexto(db, p)
    return datetime.now(timezone.utc) - _ultima_actividad(p) >= ventana, motivo


async def _resolver_postulacion(
    db: Session, c: Candidato, texto: str, id_seleccionado: str, cuenta_id, prueba: bool, telefono: str,
    personas: Optional[List[Candidato]] = None,
) -> Tuple[Optional[Postulacion], str]:
    """Decide sobre QUÉ postulación es el mensaje entrante (ver docstring del módulo).
    Regresa (postulacion, accion). postulacion=None significa que se le pidió al candidato
    elegir y no hay nada más que procesar en este turno.

    `personas` (número compartido, 2026-09-17): TODAS las filas de esta persona en el alcance; sus
    postulaciones activas cuentan igual aunque vivan en otra Cuenta. `c` es la fila ancla (donde
    nacería una postulación nueva sin vacante)."""
    personas = personas or [c]
    conv = None
    for per in personas:  # la conversación en curso más reciente entre todas sus filas
        cand = per.postulacion_conversacion
        if cand and cand.activa and (conv is None or cand.id > conv.id):
            conv = cand

    # Modo Prueba: la conversación en curso ya está fría → se cierra y se empieza de cero,
    # sin tocar teléfono ni wa_id de la persona. 2026-09-15: la ventana depende de la etapa —
    # en Prefiltro la corta configurada; con entrevista/evaluación/contratación/onboarding en
    # curso el contexto vive CONTEXTO_WHATSAPP_HORAS (5 días) y solo entonces se cierra por
    # `sin_interes`. Antes, 60 min de silencio mandaban al candidato al menú de vacantes.
    if prueba and conv:
        fria, motivo = _conversacion_fria(db, conv)
        if fria:
            conv.cerrar(motivo)
            registrar(
                db, "sistema", "postulacion_" + motivo, "postulacion", conv.codigo,
                {"candidato": c.codigo, "etapa": conv.etapa, "ultima_actividad": _ultima_actividad(conv).isoformat()},
            )
            if conv.candidato:
                conv.candidato.postulacion_conversacion_id = None
            conv = None
            db.flush()

    activas = [p for per in personas for p in per.postulaciones_activas]
    activas.sort(key=lambda p: p.id)
    esperando = [p for p in activas if p.espera_respuesta]

    # 0. Respuesta a la lista "¿sobre cuál vacante me escribes?" (ids = P-####)
    if id_seleccionado and id_seleccionado.startswith("P-"):
        elegida = next((p for p in activas if p.codigo == id_seleccionado), None)
        if elegida:
            fijar_conversacion(elegida)
            return elegida, "postulacion_elegida"

    # 1. La conversación en curso sigue esperando respuesta → seguir ahí.
    if conv and conv.espera_respuesta:
        return conv, "conversacion"

    # 2. Sin puntero útil: ¿cuántas esperan respuesta?
    if len(esperando) == 1:
        fijar_conversacion(esperando[0])
        return esperando[0], "conversacion_unica"
    if len(esperando) > 1:
        await enviar_lista_interactiva(
            telefono,
            "📋 Tus postulaciones",
            "Tienes más de un proceso en curso. ¿Sobre cuál vacante me escribes?",
            "Elegir vacante",
            [
                {"id": p.codigo, "titulo": (p.vacante.titulo if p.vacante else "Sin vacante")[:24], "descripcion": f"Etapa: {p.etapa}"[:72]}
                for p in esperando[:10]
            ],
        )
        registrar(db, "agente-ia", "postulacion_ambigua_preguntada", "candidato", c.codigo, {"opciones": [p.codigo for p in esperando]})
        return None, "elegir_postulacion"

    # 3. Nada espera respuesta. ¿Eligió explícitamente una vacante (lista/código)? → nueva
    # postulación (o la activa que ya tenga para esa vacante).
    vac = _vacante_explicita(db, cuenta_id, texto, id_seleccionado)
    if vac and not any(p.vacante_id == vac.id for p in activas):
        # la postulación nace en la Cuenta de la vacante, con la fila de la persona de ESA Cuenta
        persona = _persona_para_cuenta(db, c, personas, vac.cuenta_id, prueba)
        p, _nueva = postulacion_para_vacante(db, persona, vac, vac.cuenta_id, _origen(), es_prueba=prueba)
        fijar_conversacion(p)
        return p, "postulacion_nueva_por_seleccion"
    if conv:
        return conv, "conversacion_cerrada"  # respuesta fija de post-completo
    if activas:
        fijar_conversacion(activas[-1])
        return activas[-1], "activa_mas_reciente"

    # 4. Sin nada activo → postulación nueva sin vacante (en la Cuenta ancla); el flujo de abajo manda
    # el menú y, al elegir, `_amarrar_a_cuenta` la deja en la Cuenta de la vacante.
    p = crear_postulacion(db, c, None, c.cuenta_id, _origen(), es_prueba=prueba)
    fijar_conversacion(p)
    return p, "postulacion_nueva"


def _persona_para_cuenta(db: Session, c: Candidato, personas: List[Candidato], cuenta_id: int, prueba: bool) -> Candidato:
    """Fila `Candidato` de esta persona en la Cuenta destino (2026-09-17, número compartido):
    - ya existe una → esa;
    - la fila ancla `c` no tiene ningún otro proceso (nació en el número compartido solo para el menú)
      → se MUEVE de Cuenta (no quedan personas huérfanas en la Cuenta maestra);
    - si no, se crea una copia de identidad en la Cuenta destino (cada Cuenta ve solo la suya)."""
    if c.cuenta_id == cuenta_id:
        return c
    for per in personas:
        if per.cuenta_id == cuenta_id:
            return per
    if not [p for p in c.postulaciones if p.vacante_id is not None]:
        c.cuenta_id = cuenta_id
        for p in c.postulaciones:
            p.cuenta_id = cuenta_id
        db.flush()
        return c
    nueva = _crear_candidato(
        db, cuenta_id, c.nombre, "WhatsApp", prueba,
        telefono=c.telefono, wa_id=c.wa_id, wa_nombre=c.wa_nombre, correo=c.correo,
    )
    registrar(db, "sistema", "candidato_ingresado", "candidato", nueva.codigo, {"fuente": "WhatsApp", "wa_id": c.wa_id, "es_prueba": prueba, "desde": c.codigo, "numero_compartido": True})
    db.flush()
    return nueva


def _amarrar_a_cuenta(db: Session, p: Postulacion, vacante: Vacante, personas: List[Candidato], prueba: bool) -> Postulacion:
    """Al elegir vacante desde el número compartido, la postulación (y sus mensajes) quedan en la
    `cuenta_id` de la vacante; el expediente nacerá después ya con esa persona/Cuenta."""
    if p.cuenta_id == vacante.cuenta_id and p.candidato and p.candidato.cuenta_id == vacante.cuenta_id:
        return p
    persona = _persona_para_cuenta(db, p.candidato, personas, vacante.cuenta_id, prueba)
    if p.candidato_id != persona.id:
        anterior = p.candidato
        if anterior and anterior.postulacion_conversacion_id == p.id:
            anterior.postulacion_conversacion_id = None
        p.candidato_id = persona.id
        p.candidato = persona
        for m in p.mensajes:
            m.candidato_id = persona.id
    p.cuenta_id = vacante.cuenta_id
    fijar_conversacion(p)
    db.flush()
    registrar(db, "sistema", "postulacion_amarrada_cuenta", "postulacion", p.codigo, {"cuenta_id": vacante.cuenta_id, "vacante": vacante.codigo, "candidato": persona.codigo})
    return p


# Etapas en las que un adjunto de WhatsApp es un documento del expediente de contratación.
ETAPAS_DOCUMENTOS = ("Contratación", "Onboarding")


async def _recibir_documento_whatsapp(db: Session, p: Postulacion, msg: dict, telefono: str) -> dict:
    """Descarga el medio de Meta (Graph: /{media_id} → url → binario) y lo adjunta al Expediente de la
    postulación como Documento, con la MISMA validación (firma binaria + IA) que la liga pública y
    la subida de RH. El tipo se resuelve por el pie de foto / nombre del archivo; si no coincide con
    nada, se usa el primer obligatorio pendiente; si no falta ninguno, se agrega como documento
    adicional. Nunca truena: cualquier fallo se le explica al candidato por WhatsApp."""
    media = msg.get("media") or {}
    e = p.expediente
    etiqueta = media.get("filename") or f"{msg.get('tipo')} de WhatsApp"
    guardar_mensaje(db, p, "user", f"[📎 {etiqueta}]" + (f" {msg.get('texto')}" if msg.get("texto") else ""), "whatsapp", wa_id=msg.get("wa_id", ""))
    db.flush()

    descarga = await descargar_media(media.get("id", ""))  # Telegram deduce el formato de la ruta de getFile
    if not descarga.get("ok"):
        registrar(db, "sistema", "documento_whatsapp_error", "postulacion", p.codigo, {"media": media, "error": descarga.get("detalle", "")})
        aviso = "Recibí tu archivo pero no pude descargarlo 😕 ¿Me lo puedes reenviar? Si sigue fallando, súbelo desde la liga que te compartimos."
        envio = await enviar_mensaje(telefono, aviso)
        guardar_mensaje(db, p, "assistant", aviso, "whatsapp", envio)
        db.commit()
        return {"documento": None, "error": descarga.get("detalle", "")}

    doc = documento_para_adjunto(db, e, msg.get("texto", ""), descarga.get("filename", ""))
    try:
        res = await adjuntar_documento_bytes(
            db, e, doc, descarga["contenido"], descarga.get("filename") or etiqueta, descarga.get("mime", ""),
            subido_por=f"whatsapp:{p.candidato.codigo if p.candidato else ''}",
        )
    except HTTPException as ex:
        registrar(db, "sistema", "documento_whatsapp_rechazado", "documento", f"{e.id}:{doc.tipo}", {"detalle": str(ex.detail)})
        aviso = f"No pude registrar tu archivo como «{doc.tipo}»: {ex.detail} Envíalo en PDF o foto (JPG/PNG) por favor. 🙏"
        envio = await enviar_mensaje(telefono, aviso)
        guardar_mensaje(db, p, "assistant", aviso, "whatsapp", envio)
        db.commit()
        return {"documento": doc.tipo, "error": str(ex.detail)}

    pendientes = e.pendientes
    estado = res["documento"]["estado"]
    if estado == "rechazado":
        respuesta = f"Recibí tu {doc.tipo}, pero no pasó la validación: {res['documento']['notas']} ¿Me lo mandas de nuevo? 🙏"
    elif pendientes:
        respuesta = f"¡Listo! Recibí tu {doc.tipo} ✅ Me falta: {', '.join(pendientes)}. Mándamelos por aquí cuando puedas."
    else:
        respuesta = f"¡Listo! Recibí tu {doc.tipo} ✅ Con esto tu expediente ya está completo; RH lo revisa y te confirma. 🎉"
    envio = await enviar_mensaje(telefono, respuesta)
    guardar_mensaje(db, p, "assistant", respuesta, "whatsapp", envio)
    registrar(
        db, "agente-ia", "documento_whatsapp_recibido", "documento", f"{e.id}:{doc.tipo}",
        {"postulacion": p.codigo, "estado": estado, "archivo": descarga.get("filename", ""), "mime": descarga.get("mime", "")},
    )
    _actualizar_ultima_actividad(p)
    db.commit()
    return {"documento": doc.tipo, "estado": estado, "pendientes": pendientes, "whatsapp": envio}


def _alcance_whatsapp(db: Session, numero_receptor: str, canal: str = "whatsapp") -> Tuple[List[Cuenta], str]:
    """(Cuentas activas que atiende este mensaje, modo) — ver docstring del módulo (multi-tenant).

    - `dedicado`: el número receptor está reservado por UNA Cuenta (`whatsapp_exclusivo` + número
      igual a `whatsapp_comunicacion`) → solo esa Cuenta. Opción Premium / comportamiento anterior.
    - `compartido`: cualquier otro caso → todas las Cuentas activas (número maestro).
    Canal (2026-10-06): solo las Cuentas cuyo `canal_mensajeria` acepta ese canal (lista vacía = nadie lo atiende)."""
    activas = db.query(Cuenta).filter(Cuenta.estado == "Activa").order_by(Cuenta.id).all()
    if not activas:
        raise HTTPException(500, "El webhook de WhatsApp no tiene ninguna Cuenta activa a la que asignar el mensaje.")
    activas = alcance_canal(activas, canal)
    if not activas:
        return [], "sin_canal"
    receptor = _normalizar_telefono(numero_receptor) if numero_receptor else ""
    if receptor:
        dedicadas = [
            c for c in activas
            if c.whatsapp_exclusivo and _normalizar_telefono(c.whatsapp_comunicacion or "") == receptor
        ]
        if len(dedicadas) == 1:
            return dedicadas, "dedicado"
    return activas, "compartido"


def _cuenta_ancla(db: Session, alcance: List[Cuenta], personas: List[Candidato]) -> Cuenta:
    """Cuenta donde nace una persona/postulación NUEVA del número compartido antes de elegir vacante:
    la de su fila más reciente si ya existe; si no, la primera que tenga vacantes publicadas; si
    ninguna, la más antigua. Al elegir vacante se re-amarra a la Cuenta correcta."""
    if len(alcance) == 1:
        return alcance[0]
    if personas:
        return next((c for c in alcance if c.id == personas[0].cuenta_id), alcance[0])
    con_vacantes = {
        cid for (cid,) in db.query(Vacante.cuenta_id)
        .filter(Vacante.estado == "Publicada", Vacante.cuenta_id.in_([c.id for c in alcance]))
        .distinct()
        .all()
    }
    return next((c for c in alcance if c.id in con_vacantes), alcance[0])


def _cuentas_con_vacantes(db: Session, alcance: List[Cuenta]) -> List[Tuple[Cuenta, int]]:
    filas = (
        db.query(Vacante.cuenta_id, func.count(Vacante.id))
        .filter(Vacante.estado == "Publicada", Vacante.cuenta_id.in_([c.id for c in alcance]))
        .group_by(Vacante.cuenta_id)
        .all()
    )
    conteo = {cid: n for cid, n in filas}
    return [(c, conteo[c.id]) for c in alcance if c.id in conteo]


def _descripcion_vacante(v: Vacante, con_empresa: bool) -> str:
    partes = [x for x in [nombre_empresa_candidato(v) if con_empresa else "", v.ubicacion, v.sueldo] if x]
    return " · ".join(partes)[:72]


async def _enviar_menu_vacantes(db: Session, telefono: str, alcance: List[Cuenta], p: Postulacion, cuenta_filtro: Optional[Cuenta] = None) -> dict:
    """Menú de vacantes publicadas del alcance (número compartido: todas las empresas). Con más de 10
    vacantes en total —límite de Meta por lista— primero se pregunta la empresa (ids `CTA-<id>`) y se
    guarda en `analisis.cuenta_elegida`; `cuenta_filtro` lista solo esa Cuenta."""
    if cuenta_filtro:
        vacantes = _vacantes_publicadas(db, cuenta_filtro.id)
        return await enviar_lista_interactiva(
            telefono, f"📋 Vacantes · {(cuenta_filtro.nombre_comercial or cuenta_filtro.nombre)[:40]}",
            "Selecciona la vacante que te interesa:", "Ver vacantes",
            [{"id": v.codigo, "titulo": v.titulo, "descripcion": _descripcion_vacante(v, False)} for v in vacantes],
        )
    todas = _vacantes_publicadas(db, [c.id for c in alcance], limite=0)
    if not todas:
        return {"enviado": False, "vacantes": 0}
    varias_cuentas = len({v.cuenta_id for v in todas}) > 1
    if len(todas) <= 10:
        if not varias_cuentas:
            return await enviar_lista_interactiva(
                telefono, "📋 Vacantes disponibles", "Selecciona la vacante que te interesa:", "Ver vacantes",
                [{"id": v.codigo, "titulo": v.titulo, "descripcion": _descripcion_vacante(v, False)} for v in todas],
            )
        # ≤10 en total y varias empresas → una lista con una sección por empresa
        por_cuenta: dict = {}
        for v in todas:
            por_cuenta.setdefault(v.cuenta_id, []).append(v)
        secciones = []
        for c in alcance:
            if c.id in por_cuenta:
                secciones.append({
                    "titulo": c.nombre_comercial or c.nombre,
                    "opciones": [{"id": v.codigo, "titulo": v.titulo, "descripcion": _descripcion_vacante(v, False)} for v in por_cuenta[c.id]],
                })
        return await enviar_lista_interactiva(
            telefono, "📋 Vacantes disponibles", "Selecciona la vacante que te interesa:", "Ver vacantes", [], secciones=secciones,
        )
    # >10 → primero la empresa
    empresas = _cuentas_con_vacantes(db, alcance)
    if len(empresas) == 1:
        return await _enviar_menu_vacantes(db, telefono, alcance, p, cuenta_filtro=empresas[0][0])
    analisis = dict(p.analisis or {})
    analisis["eligiendo_empresa"] = True
    p.analisis = analisis
    return await enviar_lista_interactiva(
        telefono, "🏢 ¿Para qué empresa?", "Tenemos vacantes en varias empresas. Elige una para ver sus puestos:", "Ver empresas",
        [{"id": f"CTA-{c.id}", "titulo": (c.nombre_comercial or c.nombre), "descripcion": f"{n} vacante{'s' if n != 1 else ''} disponible{'s' if n != 1 else ''}"} for c, n in empresas[:10]],
    )


def _texto_aviso_privacidad(nombre: str, vacante: Optional[Vacante]) -> str:
    """Mensaje de bienvenida + aviso de privacidad LFPDPPP."""
    saludo = f"¡Hola{' ' + nombre if nombre else ''}! 👋"
    puesto = f" para *{vacante.titulo}*" if vacante else ""
    return (
        f"{saludo} Gracias por tu interés{puesto}. Soy Red Human.\n\n"
        "Antes de comenzar, necesito tu autorización: tus datos personales serán tratados conforme "
        "a nuestro Aviso de Privacidad, exclusivamente para este proceso de selección. "
        "Puedes consultar el aviso completo en redhuman.mx/privacidad.\n\n"
        "¿Autorizas el uso de tus datos para continuar? (Responde *Sí* o *Acepto*)"
    )


# ============================================================
# Webhook GET — Handshake de verificación de Meta
# ============================================================

@router.get("/webhooks/whatsapp")
def verificar_webhook(
    mode: Optional[str] = Query(None, alias="hub.mode"),
    verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Handshake de verificación requerido por Meta al registrar el Webhook."""
    print(f"\n[webhook-get] Verificación recibida: mode={mode}, token={verify_token}, challenge={challenge}")
    if mode == "subscribe" and verify_token == settings.meta_verify_token:
        print(f"[webhook-get] ✅ Handshake de Meta exitoso. Challenge: {challenge}")
        return PlainTextResponse(content=challenge or "", status_code=200)

    print(f"[webhook-get] ❌ Fallo de verificación: token esperado={settings.meta_verify_token}, recibido={verify_token}")
    raise HTTPException(status_code=403, detail="Token de verificación inválido o modo incorrecto.")


# ============================================================
# Webhook POST — Agente de IA para pre-filtro de candidatos
# ============================================================

def verificar_firma_whatsapp(cuerpo: bytes, cabecera: str) -> bool:
    """2026-10-08 (seguridad): el webhook es público y los acuses de entrega ya dictan el estado de las actividades.
    * Con `META_APP_SECRET` → la firma `X-Hub-Signature-256` (HMAC-SHA256 del cuerpo CRUDO) es OBLIGATORIA siempre.
    * Sin secreto y `WHATSAPP_PROVIDER=meta` → se rechaza todo (nunca se procesa un webhook de Meta sin verificar).
    * Sin secreto y otro proveedor (WAHA/Evolution, desarrollo): se aceptan mensajes, pero los acuses NO se procesan
      (ver `whatsapp_entrante`): sin firma nadie puede marcar un envío como entregado o fallido."""
    from ..services.whatsapp import firma_valida

    if settings.meta_app_secret:
        return firma_valida(cuerpo, cabecera)
    return (settings.whatsapp_provider or "").lower() != "meta"


@router.post("/webhooks/whatsapp")
async def whatsapp_entrante(request: Request, db: Session = Depends(get_db)):
    """Agente de reclutamiento IA — recibe webhook de Meta / WAHA / Evolution. La firma se valida ANTES de leer nada."""
    cuerpo = await request.body()
    if not verificar_firma_whatsapp(cuerpo, request.headers.get("X-Hub-Signature-256", "")):
        print("[webhook-post] ❌ Firma X-Hub-Signature-256 ausente o inválida: webhook rechazado.")
        if not settings.meta_app_secret:
            raise HTTPException(status_code=503, detail="Webhook de Meta sin META_APP_SECRET configurado: no se puede validar la firma.")
        raise HTTPException(status_code=403, detail="Firma inválida.")
    try:
        payload = json.loads(cuerpo or b"{}")
    except Exception as e:
        print(f"[webhook-post-error] No se pudo parsear JSON: {e}")
        return {"ok": False, "error": "JSON no válido"}

    print("\n" + "=" * 60)
    print(f"[webhook-post] Recibido POST en /webhooks/whatsapp")
    print(f"[webhook-post] Payload: {json.dumps(payload, ensure_ascii=False)}")
    print("=" * 60)

    if settings.meta_app_secret:  # solo acuses con firma VERIFICADA alteran la trazabilidad de envíos
        _registrar_acuses(db, payload)
    msg = parsear_webhook(payload)
    if not msg:
        print("[webhook-post] Webhook procesado sin mensaje de candidato (estado de entrega o evento ignorado).")
        return {"ok": True, "ignorado": True}
    with en_conversacion("whatsapp", msg["telefono"]):  # la respuesta a esta persona sale por WhatsApp
        return await procesar_entrante(db, msg)


def _registrar_acuses(db: Session, payload: dict) -> None:
    """Acuses de entrega/lectura/falla de Meta → trazabilidad de envíos (entregado / fallido). Nunca rompe el webhook."""
    from ..services import envios
    from ..services.whatsapp import acuses_de_entrega

    try:
        tocadas = sum(envios.actualizar_por_mensaje(db, a["mensaje_id"], a["estado"], a["detalle"]) for a in acuses_de_entrega(payload))
        if tocadas:
            db.commit()
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        print(f"[webhook-post] no se pudo registrar el acuse de entrega: {ex}")


async def procesar_entrante(db: Session, msg: dict) -> dict:
    """Procesamiento COMÚN de un mensaje entrante ya normalizado (WhatsApp o Telegram, ver docstring del módulo)."""
    telefono = msg["telefono"]
    texto = msg["texto"].strip()
    nombre_wa = msg.get("nombre", "")
    id_seleccionado = msg.get("id_seleccionado", "")

    print(f"[agente] Procesando mensaje de {nombre_wa} ({telefono}): '{texto}' (id_sel='{id_seleccionado}')")

    alcance, modo_numero = _alcance_whatsapp(db, msg.get("numero_receptor", ""), msg.get("canal") or "whatsapp")
    if not alcance:
        print(f"[agente] Ninguna Cuenta activa atiende el canal {msg.get('canal') or 'whatsapp'}; mensaje ignorado.")
        return {"ok": True, "ignorado": True, "motivo": "canal_sin_cuentas"}
    alcance_ids = [x.id for x in alcance]
    prueba = modo_prueba_activo(db)

    # ── 1. Persona y postulación en conversación (dentro del alcance del número) ──
    personas = _personas_en_alcance(db, telefono, alcance_ids)
    cuenta = _cuenta_ancla(db, alcance, personas)
    c = _buscar_o_crear_candidato(db, telefono, nombre_wa, cuenta.id, prueba) if not personas else personas[0]
    if not personas:
        personas = [c]
    else:
        if not c.wa_id:
            c.wa_id = telefono
        if nombre_wa and not c.wa_nombre:
            c.wa_nombre = nombre_wa
    print(f"[agente] Alcance del número: {modo_numero} ({len(alcance)} Cuenta(s)); persona(s): {[x.codigo for x in personas]}")
    p, ruteo = await _resolver_postulacion(db, c, texto, id_seleccionado, alcance_ids, prueba, telefono, personas=personas)
    if p is None:
        db.commit()
        return {"ok": True, "accion": ruteo, "candidato": c.codigo}
    c = p.candidato or c  # la fila de la persona en la Cuenta de la postulación elegida
    cuenta = next((x for x in alcance if x.id == p.cuenta_id), cuenta)
    print(f"[agente] Postulación {p.codigo} ({ruteo}) — candidato={c.codigo} {c.nombre}, consentimiento={p.consentimiento}, vacante_id={p.vacante_id}, cuenta={p.cuenta_id}")

    # ── 1.1 Documento o imagen adjunta (2026-09-15): si la postulación ya está en Contratación u
    # Onboarding, el archivo ES el documento del expediente — se descarga de Meta y se adjunta.
    # En cualquier otra etapa se sigue tratando el pie de foto como texto (comportamiento previo).
    if msg.get("tipo") in ("image", "document") and msg.get("media") and p.etapa in ETAPAS_DOCUMENTOS and p.expediente:
        resultado = await _recibir_documento_whatsapp(db, p, msg, telefono)
        return {"ok": True, "accion": "documento_whatsapp", "candidato": c.codigo, "postulacion": p.codigo, **resultado}
    if msg.get("tipo") in ("image", "document") and not texto:
        # adjunto sin pie de foto fuera de Contratación/Onboarding: no hay nada que procesar como turno
        aviso_adj = "Recibí tu archivo, pero por ahora solo puedo leer mensajes de texto en esta etapa. ¿Me lo cuentas por escrito? 🙂"
        envio_adj = await enviar_mensaje(telefono, aviso_adj)
        guardar_mensaje(db, p, "user", f"[📎 {msg.get('tipo')}: {(msg.get('media') or {}).get('filename') or 'archivo'}]", "whatsapp", wa_id=msg.get("wa_id", ""))
        guardar_mensaje(db, p, "assistant", aviso_adj, "whatsapp", envio_adj)
        db.commit()
        return {"ok": True, "accion": "adjunto_ignorado", "candidato": c.codigo, "postulacion": p.codigo}

    # ── 2. Detectar vacante SOLO si la postulación sigue en selección (sin vacante, o con
    # vacante pero sin consentimiento — sigue respondiendo el menú inicial). Con vacante +
    # consentimiento, ninguna respuesta del prefiltro (p.ej. "3" años de experiencia) puede
    # reasignar la vacante — antes un número de un dígito se interpretaba como "selección #N".
    en_seleccion_vacante = not p.vacante_id or not p.consentimiento
    analisis_p = dict(p.analisis or {})
    # ── 1.9 Número compartido con >10 vacantes: primero eligió empresa (CTA-<id>) → lista de esa empresa ──
    if en_seleccion_vacante and not p.vacante_id and id_seleccionado.startswith("CTA-"):
        elegida = next((x for x in alcance if f"CTA-{x.id}" == id_seleccionado), None)
        if elegida:
            analisis_p["cuenta_elegida"] = elegida.id
            analisis_p.pop("eligiendo_empresa", None)
            p.analisis = analisis_p
            await _enviar_menu_vacantes(db, telefono, alcance, p, cuenta_filtro=elegida)
            db.commit()
            return {"ok": True, "accion": "menu_vacantes_empresa", "candidato": c.codigo, "postulacion": p.codigo, "cuenta": elegida.id}
    # un número («2») cuando se estaba eligiendo empresa se interpreta contra la lista de empresas
    if en_seleccion_vacante and not p.vacante_id and analisis_p.get("eligiendo_empresa") and texto.strip().isdigit():
        empresas = _cuentas_con_vacantes(db, alcance)
        n = int(texto.strip())
        if 1 <= n <= len(empresas):
            elegida = empresas[n - 1][0]
            analisis_p["cuenta_elegida"] = elegida.id
            analisis_p.pop("eligiendo_empresa", None)
            p.analisis = analisis_p
            await _enviar_menu_vacantes(db, telefono, alcance, p, cuenta_filtro=elegida)
            db.commit()
            return {"ok": True, "accion": "menu_vacantes_empresa", "candidato": c.codigo, "postulacion": p.codigo, "cuenta": elegida.id}
    alcance_deteccion = [analisis_p["cuenta_elegida"]] if analisis_p.get("cuenta_elegida") in alcance_ids else alcance_ids
    vacante_detectada = _detectar_vacante(texto, db, alcance_deteccion, id_seleccionado) if en_seleccion_vacante else None
    if vacante_detectada and not p.vacante_id:
        # multi-tenant: la postulación (y sus mensajes) quedan en la Cuenta de la vacante elegida
        p = _amarrar_a_cuenta(db, p, vacante_detectada, personas, prueba)
        c = p.candidato or c
        cuenta = next((x for x in alcance if x.id == p.cuenta_id), cuenta)
    if vacante_detectada:
        print(f"[agente] Vacante detectada: {vacante_detectada.codigo} - {vacante_detectada.titulo}")

    analisis_p = dict(p.analisis or {})
    analisis_p.pop("eligiendo_empresa", None)
    analisis_p.pop("cuenta_elegida", None) if vacante_detectada else None

    # ── 2.1 Captura interactiva de nombre si Meta no lo proporcionó ──
    if analisis_p.get("esperando_nombre"):
        nombre_ingresado = texto.strip()
        c.nombre = nombre_ingresado
        c.wa_nombre = nombre_ingresado
        analisis_p.pop("esperando_nombre", None)
        p.analisis = analisis_p
        registrar(db, c.codigo, "nombre_actualizado", "candidato", c.codigo, {"nombre": nombre_ingresado, "fuente": "whatsapp_inbound"})
        db.flush()

        vac = p.vacante
        puesto = f" de *{vac.titulo}*" if vac else ""
        primer_nombre = nombre_ingresado.split()[0]
        saludo = f"¡Mucho gusto, {primer_nombre}! 👋 Vamos a iniciar con unas breves preguntas para tu postulación{puesto}."
        envio_saludo = await enviar_mensaje(telefono, saludo)
        guardar_mensaje(db, p, "assistant", saludo, "whatsapp", envio_saludo)
        db.flush()

        mensaje_inicio = f"Mi nombre es {c.nombre} y me postulo a la vacante {vac.titulo if vac else ''}."
        resultado = await procesar_prefiltro(db, p, mensaje_inicio, "whatsapp")
        return {"ok": True, "accion": "nombre_capturado_y_prefiltro_iniciado", "candidato": c.codigo, "postulacion": p.codigo, **resultado}

    # Si se detectó una vacante, asignarla a la postulación. Si la persona ya tiene otra
    # postulación ACTIVA para esa misma vacante, se sigue en aquella (no se duplica la tarjeta)
    # y esta postulación vacía se descarta.
    seleccion_nueva_vacante = False
    if vacante_detectada and p.vacante_id != vacante_detectada.id:
        otra = next((x for x in c.postulaciones_activas if x.vacante_id == vacante_detectada.id and x.id != p.id), None)
        if otra:
            if not p.vacante_id and not p.mensajes:
                db.delete(p)
            p = otra
            fijar_conversacion(p)
            db.flush()
        else:
            p.vacante_id = vacante_detectada.id
            from ..services.proceso import congelar

            congelar(p, vacante_detectada)  # proceso configurable: congela la versión vigente de la vacante elegida
            seleccion_nueva_vacante = True
            db.flush()
    elif not vacante_detectada and p.vacante:
        vacante_detectada = p.vacante

    vacante = vacante_detectada

    # ── 3. Si aún no hay vacante asignada → enviar menú de vacantes activas ──
    if not p.vacante_id:
        print(f"[agente] Postulación {p.codigo} no tiene vacante asignada. Buscando vacantes publicadas en {len(alcance)} Cuenta(s)...")
        vacantes = _vacantes_publicadas(db, alcance_ids, limite=0)
        if vacantes:
            print(f"[agente] Enviando menú con {len(vacantes)} vacantes a {telefono}")
            res_envio = await _enviar_menu_vacantes(db, telefono, alcance, p)
            print(f"[agente] Resultado envío lista: {res_envio}")
        else:
            print(f"[agente] Sin vacantes publicadas. Enviando mensaje estándar a {telefono}")
            await enviar_mensaje(telefono, "Por el momento no tenemos vacantes abiertas, pero guardo tu contacto. ¡Te avisamos cuando haya una oportunidad! 😊")
        db.commit()
        return {"ok": True, "accion": "menu_vacantes", "candidato": c.codigo, "postulacion": p.codigo}

    # ── 4. Si acaba de seleccionar vacante o falta consentimiento → aviso, consentimiento, prefiltro ──
    if seleccion_nueva_vacante or not p.consentimiento:
        if not p.consentimiento:
            # Decisión 2026-09-11 (LFPDPPP): elegir la vacante del menú NUNCA cuenta como
            # consentimiento. Siempre se manda el aviso de privacidad (con la vacante elegida) y
            # el prefiltro no arranca hasta un "Sí"/"Acepto" explícito en un turno posterior.
            if seleccion_nueva_vacante or not _es_aceptacion(texto):
                aviso = _texto_aviso_privacidad(c.nombre if not c.nombre.startswith("Candidato") else "", vacante)
                envio_aviso = await enviar_mensaje(telefono, aviso)
                guardar_mensaje(db, p, "assistant", aviso, "whatsapp", envio_aviso)
                db.commit()
                return {"ok": True, "accion": "aviso_privacidad_enviado", "candidato": c.codigo, "postulacion": p.codigo}

            p.consentimiento = True
            p.consentimiento_fecha = datetime.now(timezone.utc)
            registrar(
                db, c.codigo, "consentimiento_otorgado", "postulacion", p.codigo,
                {"candidato": c.codigo, "medio": "whatsapp", "vacante": vacante.codigo if vacante else "",
                 "accion": "acepto_aviso", "texto": texto[:200]},
            )

        # Si no tenemos el nombre real del candidato, solicitárselo antes de las preguntas
        nombre_desconocido = not c.nombre or c.nombre.startswith("Candidato") or c.nombre == "TMP"
        if nombre_desconocido and not nombre_wa:
            analisis_p["esperando_nombre"] = True
            p.analisis = analisis_p
            db.commit()

            pregunta_nombre = f"¡Excelente elección! Te postularás para *{vacante.titulo}*.\n\nAntes de comenzar, ¿cuál es tu *nombre completo*?"
            envio_pregunta = await enviar_mensaje(telefono, pregunta_nombre)
            guardar_mensaje(db, p, "assistant", pregunta_nombre, "whatsapp", envio_pregunta)
            db.commit()
            return {"ok": True, "accion": "solicitando_nombre", "candidato": c.codigo, "postulacion": p.codigo}

        db.flush()
        print(f"[agente] ✅ Vacante {vacante.titulo if vacante else ''} confirmada para postulación {p.codigo} ({c.nombre})")

        # Iniciar inmediatamente el prefiltro con la primera pregunta de la vacante
        mensaje_inicio = f"Me interesa postularme para la vacante de {vacante.titulo if vacante else 'la posición'}."
        resultado = await procesar_prefiltro(db, p, mensaje_inicio, "whatsapp")
        print(f"[agente] Prefiltro iniciado para postulación {p.codigo}: {resultado.get('respuesta')}")
        return {"ok": True, "accion": "prefiltro_iniciado", "candidato": c.codigo, "postulacion": p.codigo, **resultado}

    # ── 5. Turno conversacional — una sola fuente de verdad ──
    # procesar_prefiltro ya resuelve internamente si la postulación está en Onboarding,
    # coordinando videollamada, con el prefiltro completo (cita agendada o no) o si le toca
    # una pregunta más. No dupliques esa decisión aquí.
    print(f"[agente] Procesando turno con IA para {p.codigo} / {c.codigo} (etapa={p.etapa}, estado={p.estado})...")
    resultado = await procesar_prefiltro(db, p, texto, "whatsapp", reconexion=bool(msg.get("reconexion")))
    print(f"[agente] Turno completado para {p.codigo}: ia={resultado.get('ia')}, clasificacion={resultado.get('clasificacion')}")
    return {"ok": True, "accion": "turno_prefiltro", "candidato": c.codigo, "postulacion": p.codigo, **resultado}


# ============================================================
# Bitácora
# ============================================================

@router.get("/bitacora")
def bitacora(
    limite: int = 50, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Últimos eventos de auditoría con su cadena de hashes, de la Cuenta actual."""
    filas = (
        db.query(Bitacora)
        .filter(Bitacora.cuenta_id == cuenta.id)
        .order_by(Bitacora.id.desc())
        .limit(limite)
        .all()
    )
    return [
        {
            "id": b.id,
            "ts": b.ts.isoformat(),
            "actor": b.actor,
            "accion": b.accion,
            "entidad": b.entidad,
            "entidad_id": b.entidad_id,
            "detalle": b.detalle,
            "hash": b.hash,
            "hash_prev": b.hash_prev,
        }
        for b in filas
    ]
