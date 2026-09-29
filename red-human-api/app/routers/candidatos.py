"""Módulo 1 · Reclutamiento — Candidatos y Postulaciones: ingesta, CV, prefiltro y decisión HITL (3.6–3.9).

Fase 2 (Puntos 7/8): `Candidato` es la PERSONA (identidad: teléfono, correo, WhatsApp, CV) y
`Postulacion` es cada aplicación a una vacante — la unidad del Kanban (decisión P4) y la dueña
del chat, las entrevistas y el expediente (decisión P5). Todos los endpoints `/{codigo}/...`
reciben el código de la Postulación (P-####); por compatibilidad también aceptan el de la
persona (C-####), resuelto a su postulación en conversación / más reciente activa.

La salida del pipeline es el enlace con el Módulo 2: al entrar a Contratación se abre el
expediente de ESA postulación y arranca la solicitud de documentos.
"""

import base64
import json
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..config import settings
from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_admin, usuario_decisor
from ..models import (
    TIPO_CONTRATACION_DETERMINADO, UNIDADES_DURACION, calcular_fecha_termino,
    DOCUMENTOS_BASE,
    ETAPAS_CANDIDATO,
    Archivo,
    Candidato,
    Colaborador,
    Cuenta,
    Documento,
    Entrevista,
    EntrevistaHumana,
    Evaluacion,
    EventoEvaluacion,
    Expediente,
    Mensaje,
    NotificacionEnviada,
    Postulacion,
    Usuario,
    Vacante,
    registrar,
)
from ..serial import archivo_dict, expediente_dict, nombre_empresa_candidato, postulacion_dict
from ..services import archivos as fs
from ..services import ia
from ..services import notificaciones
from ..services.configuracion import modo_prueba_activo, permite_duplicados, puede_forzar_prueba
from ..services.notificaciones import TZ_MEXICO, NotificarIn, override_de
from ..services.whatsapp import enviar_mensaje, enviar_plantilla
from ..services import teams as teams_srv

router = APIRouter(prefix="/candidatos", tags=["candidatos"])

# Plantilla aprobada en Meta para romper el hielo tras una postulación web (fuera de la ventana
# de 24h: el candidato no nos ha escrito todavía, así que solo una plantilla aprobada pasa).
PLANTILLA_INICIO_ENTREVISTA = "inicio_entrevista_rh"

TIPOS_ARCHIVO = ["cv", "carta", "certificado", "identificacion", "otro"]


# ============================================================
# Resolución de códigos y creación de postulaciones
# ============================================================


def _por_codigo(db: Session, codigo: str, cuenta_id: int) -> Postulacion:
    """P-#### → esa postulación. C-#### (ligas viejas: bitácora, colaboradores, agente) → la
    postulación con la que la persona está conversando; si no, su última activa; si no, la
    última que tuvo. Una persona sin postulaciones no es una tarjeta: 404."""
    if codigo.startswith("P-"):
        p = db.query(Postulacion).filter(Postulacion.codigo == codigo, Postulacion.cuenta_id == cuenta_id).first()
        if not p or (p.candidato and p.candidato.eliminado_en):
            raise HTTPException(404, "Postulación no encontrada")
        return p
    c = db.query(Candidato).filter(Candidato.codigo == codigo, Candidato.cuenta_id == cuenta_id, Candidato.eliminado_en.is_(None)).first()
    if not c:
        raise HTTPException(404, "Candidato no encontrado")
    p = _postulacion_principal(c)
    if not p:
        raise HTTPException(404, "El candidato no tiene ninguna postulación")
    return p


def _postulacion_principal(c: Candidato) -> Optional[Postulacion]:
    conv = c.postulacion_conversacion
    if conv and conv.activa:
        return conv
    activas = c.postulaciones_activas
    if activas:
        return activas[-1]
    return c.postulaciones[-1] if c.postulaciones else None


def crear_postulacion(
    db: Session,
    c: Candidato,
    vac: Optional[Vacante],
    cuenta_id: int,
    origen: str,
    *,
    consentimiento: bool = False,
    etapa: str = "Prefiltro",
    es_prueba: bool = False,
) -> Postulacion:
    """ÚNICO lugar donde nace una Postulación (webhooks, entrevistas y scripts la reutilizan):
    el código P-#### siempre sale del id de la postulación, nunca del de la persona."""
    p = Postulacion(
        codigo="TMP",
        candidato_id=c.id,
        vacante_id=vac.id if vac else None,
        cuenta_id=cuenta_id,
        origen=origen,
        etapa=etapa,
        es_prueba=es_prueba or c.es_prueba,
        consentimiento=consentimiento,
        consentimiento_fecha=datetime.now(timezone.utc) if consentimiento else None,
    )
    db.add(p)
    db.flush()
    p.codigo = f"P-{8800 + p.id}"
    c.postulaciones.append(p)
    return p


def postulacion_para_vacante(
    db: Session, c: Candidato, vac: Optional[Vacante], cuenta_id: int, origen: str, **kw
) -> Tuple[Postulacion, bool]:
    """Decisión 2026-09-11 (reaplicar): si la persona ya tiene una postulación ACTIVA para esa
    misma vacante se reutiliza (no se duplica la tarjeta); si la anterior está cerrada
    (descartado / contratado / reinicio) se crea una nueva y la vieja queda como historial.
    Regresa (postulacion, es_nueva)."""
    vac_id = vac.id if vac else None
    for p in reversed(c.postulaciones_activas):
        if p.vacante_id == vac_id:
            if kw.get("consentimiento") and not p.consentimiento:
                p.consentimiento = True
                p.consentimiento_fecha = datetime.now(timezone.utc)
            return p, False
    return crear_postulacion(db, c, vac, cuenta_id, origen, **kw), True


def fijar_conversacion(p: Postulacion) -> None:
    """Marca a `p` como la postulación con la que la persona está conversando por WhatsApp
    (ver Candidato.postulacion_conversacion_id).

    Decisión 2026-09-11 (B1): el puntero se mueve SOLO por acciones del candidato — un mensaje
    entrante suyo o una selección explícita en la lista interactiva (ver webhooks.py). Nunca
    por un mensaje saliente/proactivo de RH o del sistema (plantilla de inicio, aviso de apto,
    recordatorio, notificación): escribirle sobre la postulación B no debe secuestrar en
    silencio una conversación en curso sobre A."""
    if p.candidato and p.candidato.postulacion_conversacion_id != p.id:
        p.candidato.postulacion_conversacion_id = p.id


def guardar_mensaje(
    db: Session, p: Postulacion, rol: str, texto: str, canal: str, envio: Optional[dict] = None, wa_id: str = ""
) -> Mensaje:
    """Todo mensaje cuelga de la Postulación (y de la persona, para el historial completo).
    Solo un mensaje ENTRANTE del candidato (rol "user") fija la conversación en esa
    postulación; los salientes (rol "assistant") nunca la mueven — ver fijar_conversacion."""
    envio = envio or {}
    m = Mensaje(
        candidato_id=p.candidato_id, postulacion_id=p.id, rol=rol, texto=texto, canal=canal,
        enviado=envio.get("enviado", False), wa_id=envio.get("wa_id", "") or wa_id,
    )
    db.add(m)
    if rol == "user":
        fijar_conversacion(p)
    return m


async def _enviar_whatsapp(p: Postulacion, texto: str, canal: str = "whatsapp") -> dict:
    """Envía por WhatsApp si el canal es whatsapp y hay teléfono; que Meta falle nunca tumba el flujo."""
    if canal != "whatsapp" or not p.telefono:
        return {"enviado": False, "proveedor": "demo"}
    try:
        return await enviar_mensaje(p.telefono, texto)
    except Exception as e:
        print(f"[whatsapp-send-error] {p.codigo}: {e}")
        return {"enviado": False, "proveedor": "error", "detalle": str(e)}


def nombre_ficha(p: Postulacion) -> str:
    """Fase 4 (Punto 2): el agente se dirige SIEMPRE por el nombre de la ficha de la persona de
    esta postulación (primer nombre). El nombre del perfil de WhatsApp (`wa_nombre`) solo se usa
    cuando la ficha todavía trae un placeholder ("Candidato WhatsApp", "TMP"). Nunca un nombre de
    otra sesión, entrevista o candidato."""
    c = p.candidato
    nombre = (c.nombre if c else "") or ""
    placeholder = not nombre or nombre.startswith("Candidato") or nombre == "TMP"
    if placeholder and c and c.wa_nombre:
        nombre = c.wa_nombre
    return (nombre.split(" ")[0] if nombre else "") or "candidato(a)"


def _vacante(db: Session, codigo: Optional[str], cuenta_id: int) -> Optional[Vacante]:
    if not codigo:
        return None
    v = db.query(Vacante).filter(Vacante.codigo == codigo, Vacante.cuenta_id == cuenta_id).first()
    if not v:
        raise HTTPException(404, f"Vacante '{codigo}' no encontrada")
    return v


def _telefono(valor: Optional[str]) -> str:
    """Normaliza a dígitos para que el dedup por teléfono sea confiable."""
    digitos = re.sub(r"\D", "", valor or "")
    return digitos[-10:] if len(digitos) > 10 else digitos


def _distinto(a: str, b: str) -> bool:
    """Compara nombres ignorando acentos, orden y palabras sueltas; True si no comparten ningún apellido."""
    def tokens(s: str) -> set:
        plano = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
        return {t for t in re.split(r"\W+", plano) if len(t) > 2}

    ta, tb = tokens(a), tokens(b)
    return bool(ta and tb and not (ta & tb))


def _duplicado(db: Session, telefono: str, correo: str, cuenta_id: int, excluir: Optional[int] = None) -> Optional[Candidato]:
    """Busca a la PERSONA por teléfono o correo (identidad, no proceso). Nunca empareja contra
    un candidato de Modo Prueba (`es_prueba=True`); tampoco cruza Cuentas — dos empresas
    reclutadoras distintas en la plataforma no deben verse como 'el mismo candidato'."""
    q = db.query(Candidato).filter(Candidato.es_prueba.is_(False), Candidato.cuenta_id == cuenta_id, Candidato.eliminado_en.is_(None))
    if excluir:
        q = q.filter(Candidato.id != excluir)
    if telefono:
        existente = q.filter(Candidato.telefono == telefono).first()
        if existente:
            return existente
    if correo:
        return q.filter(func.lower(Candidato.correo) == correo.strip().lower()).first()
    return None


def _crear_candidato(db: Session, cuenta_id: int, nombre: str, fuente: str, es_prueba: bool, **campos) -> Candidato:
    c = Candidato(codigo="TMP", cuenta_id=cuenta_id, nombre=nombre, fuente=fuente, es_prueba=es_prueba, **campos)
    db.add(c)
    db.flush()
    c.codigo = f"C-{8800 + c.id}"
    return c


# ============================================================
# Fase C — Helpers de actividad y resultado "Apto"
# ============================================================


def _actualizar_ultima_actividad(p: Postulacion) -> None:
    """Registra que hubo actividad relevante en esta postulación ahora mismo. Debe llamarse
    justo antes de db.commit() en cualquier endpoint que modifique su estado (etapa,
    evaluación, documento, mensaje, consentimiento)."""
    from ..models import ahora as _ahora
    p.ultima_actividad_en = _ahora()


def _recalcular_resultado_apto(p: Postulacion) -> str:
    """Actualiza Postulacion.resultado_apto aplicando la regla 'el más reciente gana':

    1. Contratación / Onboarding → True siempre (llegaron al final del pipeline).
    3. Entrevista humana más reciente con conclusión (Evaluaciones unificadas) → Avanzar=True | No avanzar=False.
    4. Entrevista IA más reciente evaluada → avanzar=True | no_avanzar=False.
    5. Prefiltro (p.estado) → cumple=True | no_cumple=False | otro=None.

    Regresa qué regla decidió ("contratacion"|"entrevista_humana"|"entrevista_ia"|"prefiltro")
    — lo usa `_recalcular_resultado_apto_y_notificar` (Fase D) para saber si el cambio vino
    del prefiltro Zero-Touch (excluido de notificaciones) o de una etapa posterior real.
    """
    if p.etapa in ("Contratación", "Onboarding"):
        p.resultado_apto = True
        return "contratacion"
    from ..serial import _ultima_entrevista_humana_con_resultado

    eh = _ultima_entrevista_humana_con_resultado(p)
    if eh is not None and eh.conclusion in ("avanzar", "no_avanzar"):
        p.resultado_apto = eh.conclusion == "avanzar"
        return "entrevista_humana"
    for e in reversed(p.entrevistas):
        rec = (e.evaluacion or {}).get("recomendacion", "")
        if rec:
            p.resultado_apto = (rec == "avanzar")
            return "entrevista_ia"
    if p.estado == "cumple":
        p.resultado_apto = True
    elif p.estado == "no_cumple":
        p.resultado_apto = False
    else:
        p.resultado_apto = None
    return "prefiltro"


async def _recalcular_resultado_apto_y_notificar(db: Session, p: Postulacion, actor: str) -> None:
    """Fase D, evento 'candidato_apto': dispara la notificación solo cuando resultado_apto pasa
    a True por una etapa POSTERIOR al prefiltro (Entrevista IA, Entrevista Humana,
    Contratación) — el apto/no-apto de prefiltro (Zero-Touch) sigue 100% excluido."""
    anterior = p.resultado_apto
    origen = _recalcular_resultado_apto(p)
    # Evaluaciones unificadas (2026-09-29): un resultado de evaluación NUNCA avisa al candidato que avanza (solo lo
    # decide RH); el aviso automático queda para la Entrevista Red Human.
    if p.resultado_apto is True and anterior is not True and origen == "entrevista_ia":
        await notificaciones.disparar(db, "candidato_apto", p, actor)


# ============================================================
# Listado (Kanban: una tarjeta por Postulación) y detalle
# ============================================================


@router.get("")
def listar(
    vacante: Optional[str] = None,
    etapa: Optional[str] = None,
    estado: Optional[str] = None,
    fuente: Optional[str] = None,
    nombre: Optional[str] = None,
    consentimiento: Optional[bool] = None,
    duplicados: bool = False,
    apto: Optional[bool] = None,           # Fase C: True=Aptos, False=No aptos, None=todos
    cliente_id: Optional[int] = None,      # Fase C: filtrar por Cliente de la vacante
    responsable_id: Optional[int] = None,  # Fase C: filtrar por responsable de la vacante
    score_min: Optional[int] = None,       # Score CV mínimo (0-100)
    score_max: Optional[int] = None,       # Score CV máximo (0-100)
    candidato: Optional[str] = None,       # Todas las postulaciones de una persona (C-####)
    activa: Optional[bool] = None,         # filtro explícito: True = en curso, False = cerradas
    mostrar_cerradas: bool = False,        # B4: por defecto el Kanban solo muestra activas
    db: Session = Depends(get_db),
    _: Usuario = Depends(usuario_actual),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    # A diferencia de /metricas y los conteos por vacante, este listado (el Kanban de RH) SÍ
    # incluye a las postulaciones de Modo Prueba (es_prueba=True) — el frontend las distingue con
    # un badge "Prueba" para que un admin pueda seguir su propio flujo de pruebas visualmente.
    q = (
        db.query(Postulacion)
        .join(Candidato, Postulacion.candidato_id == Candidato.id)
        .filter(Postulacion.cuenta_id == cuenta.id, Candidato.eliminado_en.is_(None))  # CRUD: personas eliminadas nunca salen
        .order_by(Postulacion.id.desc())
    )
    # B4 (decisión 2026-09-11): las cerradas (descartado/contratado/reinicio) se ocultan salvo
    # que RH active "Mostrar cerradas" o pida un filtro `activa` explícito.
    # 2026-09-20 (B4): este filtro por defecto ES la base de `services.conteos.postulaciones_visibles` —
    # si se cambia aquí hay que cambiarlo allá (los contadores de vacante/pipeline deben coincidir con esta lista).
    if activa is not None:
        q = q.filter(Postulacion.activa.is_(activa))
    elif not mostrar_cerradas:
        q = q.filter(Postulacion.activa.is_(True))
    if vacante:
        v = _vacante(db, vacante, cuenta.id)
        q = q.filter(Postulacion.vacante_id == v.id)
    if etapa:
        q = q.filter(Postulacion.etapa == etapa)
    if estado:
        q = q.filter(Postulacion.estado == estado)
    if fuente:
        q = q.filter(Candidato.fuente == fuente)
    if nombre:
        q = q.filter(Candidato.nombre.ilike(f"%{nombre.strip()}%"))
    if candidato:
        q = q.filter(Candidato.codigo == candidato)
    if consentimiento is not None:
        q = q.filter(Postulacion.consentimiento.is_(consentimiento))
    if apto is not None:
        q = q.filter(Postulacion.resultado_apto.is_(apto))
    if score_min is not None:
        q = q.filter(Postulacion.score >= score_min)
    if score_max is not None:
        q = q.filter(Postulacion.score <= score_max)
    if cliente_id is not None:
        q = q.join(Vacante, Postulacion.vacante_id == Vacante.id).filter(Vacante.cliente_id == cliente_id)
    if responsable_id is not None:
        q = q.join(Vacante, Postulacion.vacante_id == Vacante.id, isouter=True).filter(Vacante.responsable_id == responsable_id)
    if duplicados:
        # PERSONAS cuyo teléfono normalizado o correo en minúsculas aparece más de una vez en la
        # misma Cuenta — misma lógica que _duplicado(), pero para MOSTRAR, no para bloquear.
        # (Una persona con varias postulaciones NO es duplicado: es la misma fila en `candidatos`.)
        tel_dup = (
            select(Candidato.telefono)
            .where(Candidato.cuenta_id == cuenta.id, Candidato.telefono != "")
            .group_by(Candidato.telefono)
            .having(func.count(Candidato.id) > 1)
        ).scalar_subquery()
        correo_dup = (
            select(func.lower(Candidato.correo))
            .where(Candidato.cuenta_id == cuenta.id, Candidato.correo != "")
            .group_by(func.lower(Candidato.correo))
            .having(func.count(Candidato.id) > 1)
        ).scalar_subquery()
        q = q.filter(or_(Candidato.telefono.in_(tel_dup), func.lower(Candidato.correo).in_(correo_dup)))
    # Hotfix concurrencia 2026-09-24 (N+1): todo lo que lee `postulacion_dict` se trae en un puñado
    # de consultas por listado, no ~10 por tarjeta. Los mensajes solo se CUENTAN (una consulta agrupada).
    postulaciones = q.options(
        joinedload(Postulacion.candidato).selectinload(Candidato.postulaciones),
        joinedload(Postulacion.candidato).selectinload(Candidato.archivos),
        joinedload(Postulacion.vacante).joinedload(Vacante.cliente),
        selectinload(Postulacion.expediente).selectinload(Expediente.documentos),
        selectinload(Postulacion.entrevistas),
    ).all()
    n_mensajes = dict(
        db.query(Mensaje.postulacion_id, func.count(Mensaje.id))
        .filter(Mensaje.postulacion_id.in_([p.id for p in postulaciones]))
        .group_by(Mensaje.postulacion_id)
        .all()
    ) if postulaciones else {}
    return [postulacion_dict(p, n_mensajes=n_mensajes.get(p.id, 0)) for p in postulaciones]


@router.post("/prueba/eliminar")
def eliminar_candidatos_prueba(
    db: Session = Depends(get_db), u: Usuario = Depends(usuario_admin), cuenta: Cuenta = Depends(cuenta_actual)
):
    """Botón «Eliminar postulaciones de prueba» (solo admin) — borra TODAS las personas con
    `es_prueba=True` de la Cuenta activa y lo que cuelga de ellas (postulaciones, mensajes,
    entrevistas, expedientes y documentos). Mismo patrón de cascada que
    scripts/borrar_demo_candidatos.py (que borra por prefijo de código en vez de por flag)."""
    candidatos = db.query(Candidato).filter(Candidato.es_prueba.is_(True), Candidato.cuenta_id == cuenta.id).all()
    if not candidatos:
        return {"candidatos": 0, "postulaciones": 0, "mensajes": 0, "entrevistas": 0, "expedientes": 0, "documentos": 0, "notificaciones": 0, "colaboradoresConservados": 0}

    ids = [c.id for c in candidatos]
    # Expedientes uno por uno (no bulk delete) para que la cascada del ORM se lleve también
    # sus Documento — Expediente.documentos tiene cascade="all, delete-orphan".
    expedientes = db.query(Expediente).filter(Expediente.candidato_id.in_(ids)).all()
    n_doc = sum(len(e.documentos) for e in expedientes)
    for e in expedientes:
        db.delete(e)
    db.flush()
    # Hijos por persona (cubre también los registros previos a la migración, con postulacion_id NULL).
    n_msj = db.query(Mensaje).filter(Mensaje.candidato_id.in_(ids)).delete(synchronize_session=False)
    n_ent = db.query(Entrevista).filter(Entrevista.candidato_id.in_(ids)).delete(synchronize_session=False)
    db.query(EntrevistaHumana).filter(EntrevistaHumana.candidato_id.in_(ids)).delete(synchronize_session=False)
    ids_ev = [i for (i,) in db.query(Evaluacion.id).filter(Evaluacion.candidato_id.in_(ids)).all()]
    if ids_ev:
        db.query(EventoEvaluacion).filter(EventoEvaluacion.evaluacion_id.in_(ids_ev)).delete(synchronize_session=False)
        db.query(Evaluacion).filter(Evaluacion.id.in_(ids_ev)).delete(synchronize_session=False)
    # Punto 13: el historial de envíos de esas personas también es de prueba (antes quedaba huérfano).
    n_notif = db.query(NotificacionEnviada).filter(NotificacionEnviada.candidato_id.in_(ids)).delete(synchronize_session=False)
    # Un Colaborador dado de alta desde una prueba NO se borra en cascada (es un registro de
    # nómina/plantilla): se reporta para que RH decida a mano desde Colaboradores.
    from ..models import Colaborador
    n_col = db.query(Colaborador).filter(Colaborador.candidato_origen_id.in_(ids)).count()
    for c in candidatos:
        c.postulacion_conversacion_id = None
    db.flush()
    n_post = db.query(Postulacion).filter(Postulacion.candidato_id.in_(ids)).delete(synchronize_session=False)
    db.expire_all()

    codigos = [c.codigo for c in candidatos]
    for c in candidatos:
        db.delete(c)  # Candidato.archivos tiene cascade="all, delete-orphan"

    registrar(
        db, u.nombre, "candidatos_prueba_borrados", "sistema", "modo_prueba",
        {"candidatos": codigos, "correo_rh": u.correo},
    )
    db.commit()
    return {
        "candidatos": len(candidatos), "postulaciones": n_post, "mensajes": n_msj,
        "entrevistas": n_ent, "expedientes": len(expedientes), "documentos": n_doc,
        "notificaciones": n_notif, "colaboradoresConservados": n_col,
    }


@router.get("/{codigo}")
def detalle(codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    return postulacion_dict(_por_codigo(db, codigo, cuenta.id), detalle=True)


# ------------------------------------------------------------
# Ingesta (rutas 1, 3 y 4: formulario / integración / carga RH)
# ------------------------------------------------------------


class IngresarIn(BaseModel):
    nombre: str
    correo: str = ""
    telefono: str = ""
    ubicacion: str = ""
    experiencia: str = ""
    fuente: str = "Formulario"  # Formulario | WhatsApp | OCC | LinkedIn | Indeed | RH
    vacante: Optional[str] = None  # código VAC-####
    consentimiento: bool = False


@router.post("", status_code=201)
def ingresar(
    datos: IngresarIn,
    db: Session = Depends(get_db),
    _: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    if not datos.nombre.strip():
        raise HTTPException(400, "El nombre es obligatorio.")
    vac = _vacante(db, datos.vacante, cuenta.id)
    telefono = _telefono(datos.telefono)
    prueba = modo_prueba_activo(db)

    # dedup por teléfono o correo (módulo 3.6): la PERSONA se reutiliza; lo que se crea es una
    # postulación nueva. Con Modo Prueba activo (permite_duplicados) se salta: cada alta es independiente.
    c = None if permite_duplicados(db) else _duplicado(db, telefono, datos.correo, cuenta.id)
    nuevo_candidato = c is None
    if c is None:
        c = _crear_candidato(
            db, cuenta.id, datos.nombre.strip(), datos.fuente, prueba,
            correo=datos.correo.strip(), telefono=telefono, ubicacion=datos.ubicacion, experiencia=datos.experiencia,
        )
    p, nueva = postulacion_para_vacante(db, c, vac, cuenta.id, "rh_directo", consentimiento=datos.consentimiento)
    registrar(
        db, "sistema", "candidato_ingresado", "postulacion", p.codigo,
        {"candidato": c.codigo, "fuente": datos.fuente, "vacante": datos.vacante, "persona_nueva": nuevo_candidato, "postulacion_nueva": nueva},
    )
    db.commit()
    # `duplicado` = la persona ya existía (el frontend avisa "ya estaba registrado").
    return {"duplicado": not nuevo_candidato, "postulacionNueva": nueva, **postulacion_dict(p, detalle=True)}


# ------------------------------------------------------------
# CV (ruta 2: carga por RH) — extracción con IA + match contra la vacante
# ------------------------------------------------------------


def _aplicar_cv(c: Candidato, p: Postulacion, datos: ia.CVExtraido, vac: Optional[Vacante], con_ia: bool) -> None:
    """Vuelca la extracción: datos de contacto sobre la PERSONA, match contra la vacante sobre
    la POSTULACIÓN."""
    if datos.nombre and (not c.nombre or c.nombre.startswith("Candidato")):
        c.nombre = datos.nombre
    c.correo = c.correo or (datos.correo or "")
    c.telefono = c.telefono or _telefono(datos.telefono)
    c.ubicacion = c.ubicacion or (datos.ubicacion or "")
    c.experiencia = (datos.experiencia_resumen or c.experiencia)[:240]
    c.cv_datos = datos.model_dump()

    # Merge, NUNCA reemplazo: p.analisis también guarda respuestas_prefiltro (prefiltro por
    # WhatsApp) — reprocesar un CV después no debe borrar esas respuestas.
    analisis_actual = dict(p.analisis or {})

    ajuste = datos.ajuste
    if ajuste and vac:
        p.score = ajuste.score
        p.evidencia = ajuste.evidencia
        # un CV subido después no reclasifica a quien RH ya avanzó: solo actualiza score y evidencia
        if p.etapa == "Prefiltro":
            p.estado = ajuste.estado
        analisis_actual.update({
            "origen": "cv",
            "ia": con_ia,
            "requisitos_cumplidos": ajuste.requisitos_cumplidos,
            "brechas": ajuste.brechas,
            # 2026-09-13: bloque «Análisis de CV» (experiencia relevante, fortalezas, brechas, compatibilidad)
            "fortalezas_cv": list(ajuste.fortalezas or []),
            "compatibilidad_cv": ajuste.compatibilidad or "",
            "experiencia_relevante_cv": datos.experiencia_relevante or "",
            "alertas": datos.alertas,
            "datos_faltantes": datos.datos_faltantes,
        })
    else:
        p.estado = "revision" if datos.datos_faltantes else p.estado
        p.evidencia = (
            "Datos faltantes en el CV: " + ", ".join(datos.datos_faltantes)
            if datos.datos_faltantes
            else p.evidencia
        )
        analisis_actual.update({"origen": "cv", "ia": con_ia, "alertas": datos.alertas, "datos_faltantes": datos.datos_faltantes})

    p.analisis = analisis_actual


def _cv_mas_reciente(c: Candidato) -> Optional[Archivo]:
    return next((a for a in reversed(c.archivos) if a.tipo == "cv" and fs.existe(a.ruta)), None)


async def _reanalizar_cv(p: Postulacion, vac: Optional[Vacante], cv: Archivo) -> ia.CVExtraido:
    """Relee un CV ya guardado en disco y vuelve a correr la extracción — el botón "Reintentar
    análisis" y la reevaluación al reasignar de vacante comparten esta lógica."""
    with open(cv.ruta, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()
    extension = cv.ruta.rsplit(".", 1)[-1]
    datos, con_ia = ia.extraer_cv(b64, extension, vac.titulo if vac else "", vac.requisitos if vac else "")
    _aplicar_cv(p.candidato, p, datos, vac, con_ia)
    cv.extraccion = datos.model_dump()
    cv.notas_ia = "; ".join(datos.alertas) if datos.alertas else ""
    return datos


async def _procesar_cv(
    db: Session,
    subida: UploadFile,
    vac: Optional[Vacante],
    fuente: str,
    subido_por: str,
    cuenta_id: int,
    postulacion: Optional[Postulacion] = None,
    origen: str = "cv_masivo",
) -> dict:
    """Valida el archivo, lo guarda, lo extrae con IA y crea o actualiza a la persona y su
    postulación para `vac`.

    Si la extracción con IA truena (red, proveedor caído), el archivo SIGUE guardándose — nunca
    se pierde un CV que sí llegó a subirse. Sin `datos` no se puede identificar/deduplicar por
    teléfono/correo extraído, así que ese archivo queda adjunto a la postulación ya conocida
    (`postulacion`, si se pasó) o a una nueva mínima con el nombre del archivo; el botón
    "Reintentar análisis" (`_reanalizar_cv`) completa el resto después."""
    archivo = await fs.validar(subida, "CV")
    error_extraccion: Optional[str] = None
    try:
        datos, con_ia = ia.extraer_cv(
            archivo.b64,
            archivo.extension,
            vac.titulo if vac else "",
            vac.requisitos if vac else "",
        )
    except Exception as ex:
        datos, con_ia = None, False
        error_extraccion = str(ex)

    p = postulacion
    c = p.candidato if p else None
    duplicado = False
    avisos: List[str] = []
    prueba = modo_prueba_activo(db)
    if datos is not None:
        if c is None and not permite_duplicados(db):
            telefono = _telefono(datos.telefono)
            c = _duplicado(db, telefono, datos.correo or "", cuenta_id)
            duplicado = c is not None
        if duplicado and c is not None and datos.nombre and _distinto(datos.nombre, c.nombre):
            # mismo teléfono/correo pero otro nombre: puede ser un contacto compartido o un dato mal capturado
            avisos.append(
                f"El CV está a nombre de «{datos.nombre}» pero el contacto ya existía como «{c.nombre}». "
                "Verifica que sea la misma persona antes de avanzarlo."
            )

    if c is None:
        c = _crear_candidato(
            db, cuenta_id, (datos.nombre if datos else None) or archivo.nombre.rsplit(".", 1)[0], fuente, prueba,
        )
    if p is None:
        p, _nueva = postulacion_para_vacante(db, c, vac, cuenta_id, origen)

    if datos is not None:
        _aplicar_cv(c, p, datos, vac, con_ia)

    notas = avisos + (list(datos.alertas) if datos else [])
    if error_extraccion:
        notas.insert(0, f"No fue posible analizar el currículum automáticamente: {error_extraccion}")
    elif not datos.es_cv:
        notas.insert(0, "El archivo no parece un currículum: revísalo manualmente.")
    # duda de identidad, error de análisis o archivo equivocado → nunca se queda en "cumple" automático
    if (avisos or error_extraccion or (datos and not datos.es_cv)) and p.estado == "cumple":
        p.estado = "revision"
        p.evidencia = f"{notas[0]} · {p.evidencia}"

    # el consecutivo evita que un segundo CV con el mismo nombre pise al anterior en disco
    consecutivo = len(c.archivos) + 1
    ruta = fs.guardar(archivo, "cv", f"{c.codigo}_{consecutivo}_{archivo.nombre.rsplit('.', 1)[0]}")
    reg = Archivo(
        tipo="cv",
        nombre=archivo.nombre,
        ruta=ruta,
        mime=archivo.mime,
        tamano=archivo.tamano,
        estado="revision" if (error_extraccion or (datos and not datos.es_cv)) else "recibido",
        notas_ia="; ".join(notas),
        extraccion=datos.model_dump() if datos else {},
        subido_por=subido_por,
    )
    c.archivos.append(reg)  # por la relación, para que la respuesta ya incluya el archivo nuevo
    db.flush()

    registrar(
        db, "agente-ia", "cv_extraido", "postulacion", p.codigo,
        {
            "candidato": c.codigo,
            "ia": con_ia,
            "archivo": archivo.nombre,
            "es_cv": datos.es_cv if datos else None,
            "faltantes": datos.datos_faltantes if datos else [],
            "error": error_extraccion,
            "score": p.score,
            "vacante": vac.codigo if vac else None,
        },
    )
    return {
        "ok": True,
        "archivo": archivo.nombre,
        "ia": con_ia,
        "duplicado": duplicado,
        "esCv": datos.es_cv if datos else None,
        "avisos": notas,
        "extraccion": datos.model_dump() if datos else {},
        "candidato": postulacion_dict(p, detalle=True),
    }


@router.post("/cv", status_code=201)
async def subir_cv(
    archivos: List[UploadFile] = File(..., description="Uno o varios CVs en PDF o imagen"),
    vacante: Optional[str] = Form(default=None),
    fuente: str = Form(default="RH"),
    db: Session = Depends(get_db),
    u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Carga masiva de CVs: valida, extrae con IA y califica contra la vacante."""
    if not archivos:
        raise HTTPException(400, "No se recibió ningún archivo.")
    if len(archivos) > 20:
        raise HTTPException(400, "Máximo 20 CVs por carga.")
    vac = _vacante(db, vacante, cuenta.id)

    resultados = []
    for subida in archivos:
        try:
            resultados.append(await _procesar_cv(db, subida, vac, fuente, u.nombre, cuenta.id))
            db.commit()
        except HTTPException as e:
            db.rollback()
            resultados.append({"ok": False, "archivo": subida.filename or "archivo", "error": e.detail})

    exitosos = [r for r in resultados if r["ok"]]
    return {
        "procesados": len(exitosos),
        "fallidos": len(resultados) - len(exitosos),
        "resultados": resultados,
    }


async def _disparar_plantilla_inicio(db: Session, p: Postulacion) -> dict:
    """Rompe el hielo por WhatsApp justo después de guardar la postulación, usando la plantilla
    aprobada de Meta (nunca texto libre: la web no cuenta como 'el candidato escribió primero').
    Es un mensaje saliente: NO mueve la conversación — si el candidato ya estaba a media
    conversación sobre otra postulación, su respuesta sigue yendo a aquella; si esta es la
    única que espera respuesta, el webhook la elige; si hay varias, le pregunta (B1)."""
    if not p.telefono:
        return {"enviado": False, "detalle": "El candidato no dejó WhatsApp."}
    primer_nombre = (p.nombre or "").split(" ")[0] or "candidato(a)"
    envio = await enviar_plantilla(p.telefono, PLANTILLA_INICIO_ENTREVISTA, [primer_nombre])
    texto_mensaje = (
        f"[Plantilla de WhatsApp «{PLANTILLA_INICIO_ENTREVISTA}»] Hola {primer_nombre}, ¡gracias por tu interés! Empecemos con tu proceso."
        if envio.get("enviado")
        else f"[Fallo de envío Meta] La plantilla «{PLANTILLA_INICIO_ENTREVISTA}» no pudo entregarse a {p.telefono}: {envio.get('detalle', 'sin detalle')}."
    )
    guardar_mensaje(db, p, "assistant", texto_mensaje, "whatsapp", envio)
    registrar(
        db, "sistema", "plantilla_inicio_enviada", "postulacion", p.codigo,
        {"candidato": p.candidato.codigo, "plantilla": PLANTILLA_INICIO_ENTREVISTA, "whatsapp": envio},
    )
    return envio


@router.post("/postular", status_code=201)
async def postular(
    vacante: str = Form(..., description="slug o código de la vacante publicada"),
    nombre: str = Form(...),
    telefono: str = Form(default=""),
    correo: str = Form(default=""),
    consentimiento: bool = Form(default=False),
    respuestas: str = Form(default="", description="JSON: [{pregunta, respuesta}]"),
    cv: Optional[UploadFile] = File(default=None, description="CV en PDF o imagen"),
    db: Session = Depends(get_db),
):
    """Postulación desde la página pública `/aplicar/[slug]` — un solo paso para el candidato.
    Una misma persona puede postularse a varias vacantes: cada una es su propia Postulación."""
    if not nombre.strip():
        raise HTTPException(400, "Necesitamos tu nombre completo.")
    if not consentimiento:
        raise HTTPException(400, "Necesitamos tu autorización para tratar tus datos (Aviso de Privacidad).")
    if not telefono.strip() and not correo.strip():
        raise HTTPException(400, "Déjanos un WhatsApp o un correo para poder contactarte.")

    vac = db.query(Vacante).filter(Vacante.slug == vacante).first()
    if not vac:
        vac = db.query(Vacante).filter(func.lower(Vacante.slug) == vacante.lower()).first()
    if not vac:
        vac = db.query(Vacante).filter(Vacante.codigo == vacante).first()
    if not vac:
        # Si aún no coincide, buscar por título aproximado
        vac = db.query(Vacante).filter(func.lower(Vacante.titulo) == vacante.replace("-", " ").lower()).first()
    if not vac:
        raise HTTPException(404, f"Vacante '{vacante}' no encontrada")
    if vac.estado != "Publicada":  # 2026-09-16: una liga vieja a una vacante eliminada/cerrada no abre postulaciones
        raise HTTPException(410, "Esta vacante ya no está disponible.")

    tel = _telefono(telefono)
    prueba = modo_prueba_activo(db)

    # Con Modo Prueba activo (permite_duplicados), `c` siempre queda en None aquí: cada llamada es una
    # persona nueva e independiente, sin importar cuánto pasó desde la anterior.
    c = None if permite_duplicados(db) else _duplicado(db, tel, correo, vac.cuenta_id)
    nuevo_candidato = c is None
    if c is None:
        c = _crear_candidato(db, vac.cuenta_id, nombre.strip(), "Formulario", prueba, telefono=tel, correo=correo.strip())
    else:
        if nombre.strip() and (not c.nombre or c.nombre.startswith("Candidato")):
            c.nombre = nombre.strip()
        if tel and not c.telefono:
            c.telefono = tel
        if correo.strip() and not c.correo:
            c.correo = correo.strip()

    # Reaplicar (decisión 2026-09-11): activa para esta vacante → se reutiliza; cerrada → nueva.
    p, nueva_postulacion = postulacion_para_vacante(db, c, vac, vac.cuenta_id, "formulario", consentimiento=True)
    # 2026-09-16 (prefiltro dual): las respuestas del formulario web se guardan para compararlas después
    # con lo que la persona diga por WhatsApp (antes se recibían y se tiraban).
    respuestas_web = _parsear_respuestas_web(respuestas)
    if respuestas_web:
        analisis_p = dict(p.analisis or {})
        analisis_p["respuestas_web"] = respuestas_web
        p.analisis = analisis_p
    registrar(
        db, c.codigo, "consentimiento_otorgado", "postulacion", p.codigo,
        {"candidato": c.codigo, "medio": "portal", "vacante": vac.codigo, "aviso_privacidad": "aceptado en /aplicar"},
    )

    resultado_cv = {"ok": False, "avisos": []}
    if cv and cv.filename:
        try:
            resultado_cv = await _procesar_cv(db, cv, vac, "Formulario", c.codigo, vac.cuenta_id, postulacion=p, origen="formulario")
        except Exception as e:
            print(f"[postular-cv-error] Error procesando CV: {e}")
            resultado_cv = {"ok": False, "avisos": [f"No se pudo extraer el CV: {e}"]}

    registrar(
        db, "sistema", "postulacion_recibida", "postulacion", p.codigo,
        {"candidato": c.codigo, "vacante": vac.codigo, "persona_nueva": nuevo_candidato, "postulacion_nueva": nueva_postulacion},
    )
    db.commit()

    # Zero-Touch: dispara la plantilla de Meta ("recibimos tu postulación") ya con la postulación
    # comprometida a disco — vacante y consentimiento incluidos — solo para postulaciones nuevas,
    # para no volver a "romper el hielo" en una que ya está en curso. Se manda DESPUÉS del commit:
    # si el candidato responde muy rápido, el webhook corre en otra transacción que ya ve este
    # registro y la conversación queda fijada en esta postulación.
    if nueva_postulacion:
        await _disparar_plantilla_inicio(db, p)
        db.commit()

    return {
        "ok": True,
        "candidato": c.codigo,
        "postulacion": p.codigo,
        "nombre": c.nombre,
        "nuevo": nuevo_candidato,
        "postulacionNueva": nueva_postulacion,
        "cv": {"procesado": resultado_cv.get("ok", False), "avisos": resultado_cv.get("avisos", [])},
    }


@router.post("/{codigo}/archivos", status_code=201)
async def subir_archivo(
    codigo: str,
    archivo: UploadFile = File(...),
    tipo: str = Form(default="cv"),
    db: Session = Depends(get_db),
    u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Adjunta un archivo a la persona (los archivos son de la persona: un CV sirve para todas
    sus postulaciones). Si es CV, vuelve a extraer y recalifica ESTA postulación."""
    p = _por_codigo(db, codigo, cuenta.id)
    c = p.candidato
    if tipo not in TIPOS_ARCHIVO:
        raise HTTPException(400, f"Tipo inválido. Usa uno de: {', '.join(TIPOS_ARCHIVO)}")

    if tipo == "cv":
        resultado = await _procesar_cv(db, archivo, p.vacante, c.fuente, u.nombre, cuenta.id, postulacion=p)
        _actualizar_ultima_actividad(p)
        db.commit()
        return resultado

    validado = await fs.validar(archivo, tipo)
    consecutivo = len(c.archivos) + 1
    ruta = fs.guardar(validado, "anexos", f"{c.codigo}_{consecutivo}_{tipo}_{validado.nombre.rsplit('.', 1)[0]}")
    reg = Archivo(
        tipo=tipo,
        nombre=validado.nombre,
        ruta=ruta,
        mime=validado.mime,
        tamano=validado.tamano,
        estado="recibido",
        subido_por=u.nombre,
    )
    c.archivos.append(reg)
    db.flush()
    registrar(db, u.nombre, "archivo_adjuntado", "postulacion", p.codigo, {"candidato": c.codigo, "tipo": tipo, "archivo": validado.nombre})
    _actualizar_ultima_actividad(p)
    db.commit()
    return {"ok": True, "archivo": archivo_dict(reg), "candidato": postulacion_dict(p, detalle=True)}


@router.get("/{codigo}/archivos/{archivo_id}")
def descargar_archivo(
    codigo: str,
    archivo_id: int,
    db: Session = Depends(get_db),
    _: Usuario = Depends(usuario_actual),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    c = _por_codigo(db, codigo, cuenta.id).candidato
    a = next((x for x in c.archivos if x.id == archivo_id), None)
    if not a:
        raise HTTPException(404, "Archivo no encontrado")
    if not fs.existe(a.ruta):
        raise HTTPException(410, "El archivo ya no está disponible en el servidor.")
    return FileResponse(a.ruta, media_type=a.mime or "application/octet-stream", filename=a.nombre)


@router.post("/{codigo}/archivos/{archivo_id}/reanalizar")
async def reanalizar_cv(
    codigo: str,
    archivo_id: int,
    db: Session = Depends(get_db),
    u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Botón «Reintentar análisis» — relee un CV ya guardado y reintenta la extracción con IA,
    sin pedirle al usuario que vuelva a subir el archivo."""
    p = _por_codigo(db, codigo, cuenta.id)
    a = next((x for x in p.candidato.archivos if x.id == archivo_id and x.tipo == "cv"), None)
    if not a:
        raise HTTPException(404, "CV no encontrado")
    if not fs.existe(a.ruta):
        raise HTTPException(410, "El archivo ya no está disponible en el servidor; pide que lo vuelvan a subir.")
    try:
        await _reanalizar_cv(p, p.vacante, a)
    except Exception as ex:
        raise HTTPException(502, f"No fue posible analizar el currículum: {ex}")
    registrar(db, u.nombre, "cv_reanalizado", "postulacion", p.codigo, {"archivo": a.nombre, "score": p.score})
    _actualizar_ultima_actividad(p)
    db.commit()
    return postulacion_dict(p, detalle=True)


# ------------------------------------------------------------
# Reasignar de vacante / reiniciar (Modo Prueba)
# ------------------------------------------------------------


class AsignarIn(BaseModel):
    vacante: str  # código VAC-####
    reevaluar: bool = True


@router.post("/{codigo}/asignar")
async def asignar(
    codigo: str,
    datos: AsignarIn,
    db: Session = Depends(get_db),
    u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Mueve ESTA postulación a otra vacante (corrección manual de RH) y, si hay CV, recalcula
    el match. Si la persona ya tiene una postulación activa para la vacante destino no se
    duplica: RH debe trabajar sobre esa."""
    p = _por_codigo(db, codigo, cuenta.id)
    vac = _vacante(db, datos.vacante, cuenta.id)
    otra = next((x for x in p.candidato.postulaciones_activas if x.vacante_id == vac.id and x.id != p.id), None)
    if otra:
        raise HTTPException(409, f"Esta persona ya tiene la postulación {otra.codigo} activa para «{vac.titulo}».")
    anterior = p.vacante.codigo if p.vacante else None
    p.vacante_id = vac.id

    if datos.reevaluar:
        cv = _cv_mas_reciente(p.candidato)
        if cv:
            await _reanalizar_cv(p, vac, cv)

    await _recalcular_resultado_apto_y_notificar(db, p, u.nombre)  # la nueva vacante puede cambiar el contexto de evaluación
    _actualizar_ultima_actividad(p)
    registrar(db, u.nombre, "candidato_reasignado", "postulacion", p.codigo, {"de": anterior, "a": vac.codigo})
    db.commit()
    return postulacion_dict(p, detalle=True)


@router.post("/{codigo}/reiniciar")
def reiniciar_postulacion(
    codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)
):
    """SOLO PRUEBAS (Punto 8) — «Reiniciar prueba»: cierra esta postulación
    (motivo_cierre='reinicio_prueba') y crea una nueva limpia para la misma persona y la misma
    vacante, dejándola como la conversación activa. El teléfono y wa_id de la persona no se
    tocan: así el mismo número de WhatsApp vuelve a empezar el flujo desde cero sin que el
    webhook lo asocie a la postulación anterior. Reemplaza al viejo «Liberar número»."""
    p = _por_codigo(db, codigo, cuenta.id)
    c = p.candidato
    if not (modo_prueba_activo(db) or c.es_prueba or p.es_prueba):
        raise HTTPException(409, "Reiniciar una postulación es una acción de Modo Prueba: actívalo en Configuración.")
    p.cerrar("reinicio_prueba")
    nueva = crear_postulacion(db, c, p.vacante, cuenta.id, "reinicio_prueba", es_prueba=True)
    # No se fija la conversación aquí (B1: solo la mueve el candidato): al cerrarse la anterior,
    # el webhook enruta el siguiente mensaje a la nueva por ser la única que espera respuesta.
    registrar(
        db, u.nombre, "postulacion_reiniciada", "postulacion", nueva.codigo,
        {"candidato": c.codigo, "anterior": p.codigo, "correo_rh": u.correo},
    )
    db.commit()
    return {"ok": True, "candidato": c.codigo, "anterior": p.codigo, "nueva": nueva.codigo, **postulacion_dict(nueva, detalle=True)}


# ------------------------------------------------------------
# Prefiltro con agente (WhatsApp / simulador) — Zero-Touch
# ------------------------------------------------------------


@router.get("/{codigo}/mensajes")
def mensajes(
    codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)
):
    p = _por_codigo(db, codigo, cuenta.id)
    return [
        {"rol": m.rol, "texto": m.texto, "canal": m.canal, "enviado": m.enviado, "ts": m.creado_en.isoformat()}
        for m in p.mensajes
    ]


class MensajeIn(BaseModel):
    texto: str
    canal: str = "simulador"  # simulador | whatsapp | web


# Score mínimo (0-100) para considerarse "apto" en el flujo automático (Zero-Touch).
UMBRAL_ZERO_TOUCH = 50


def _texto_apto(p: Postulacion) -> str:
    return (
        f"¡Buenas noticias, {p.nombre.split(' ')[0]}! 🎉 Tu perfil es compatible con lo que buscamos "
        "para esta vacante. Cuéntame, ¿qué disponibilidad tienes para una breve videollamada?"
    )


async def _avisar_apto_e_iniciar_agenda(db: Session, p: Postulacion) -> dict:
    """Mensaje que invita al candidato a compartir su disponibilidad — en cuanto responda,
    procesar_prefiltro lo enruta a _procesar_turno_agenda (herramienta agendar_videollamada).
    La usan tanto la clasificación automática de Zero-Touch como el botón manual
    'Enviar a Entrevista IA' (mover_etapa), para que ambos caminos se comporten igual."""
    texto = _texto_apto(p)
    envio = await _enviar_whatsapp(p, texto)
    guardar_mensaje(db, p, "assistant", texto, "whatsapp", envio)
    return envio


async def _asignar_curso_filtro(db: Session, p: Postulacion) -> None:
    """Capacitación universal (2026-09-16): si la vacante tiene curso de filtro, se asigna al candidato en
    cuanto queda apto y se le manda la liga; su resultado aparece en la evaluación de la postulación."""
    v = p.vacante
    if not v or not v.curso_filtro or v.curso_filtro.estado != "Publicado":
        return
    from .capacitacion import asignar_a_postulacion  # import local: capacitacion importa modelos, no a candidatos

    try:
        await asignar_a_postulacion(db, p, v.curso_filtro, actor="agente-ia")
    except Exception as ex:  # noqa: BLE001 — nunca bloquea el flujo
        registrar(db, "sistema", "curso_filtro_no_asignado", "postulacion", p.codigo, {"error": str(ex)[:200]})


async def _auto_decision_zero_touch(db: Session, p: Postulacion, resultado_prefiltro: str = "") -> dict:
    """Flujo Zero-Touch: al terminar el prefiltro, clasifica la postulación contra
    UMBRAL_ZERO_TOUCH y le avisa el resultado por WhatsApp sin intervención de RH.

    Reemplaza el estado intermedio 'revision' del agente por una decisión binaria
    (cumple/no_cumple). La postulación NO se cierra: la IA solo recomienda (LFPDPPP) — RH
    conserva la capacidad de reabrir el caso desde el panel o de descartarlo de verdad; la
    bitácora deja constancia de que la acción la tomó el agente ("agente-ia").

    Regresa {"respuesta": str, "whatsapp": dict} — el ÚNICO mensaje que debe ver el candidato
    en el turno de cierre del prefiltro (ver procesar_prefiltro).
    """
    # 2026-09-13: la decisión binaria sale del resultado del prefiltro (cumple / no_cumple), ya no
    # de un score — el prefiltro es solo un filtro de entrada. El score de CV no participa aquí.
    no_cumple = (resultado_prefiltro == "no_cumple") if resultado_prefiltro else (p.score < UMBRAL_ZERO_TOUCH)
    if no_cumple:
        p.estado = "no_cumple"
        texto = (
            f"Gracias por tu tiempo, {p.nombre.split(' ')[0]}. Después de revisar tus respuestas, "
            "por ahora tu perfil no se alinea con lo que busca esta vacante. Guardamos tu información "
            "por si surge una oportunidad más adelante. ¡Mucho éxito en tu búsqueda! 🙌"
        )
        registrar(db, "agente-ia", "auto_descartado_zero_touch", "postulacion", p.codigo, {"prefiltro": resultado_prefiltro or "score", "score_cv": p.score})
        envio = await _enviar_whatsapp(p, texto)
        # Se guarda igual sin teléfono (p.ej. pruebas por simulador): así el veredicto real
        # siempre queda en el historial, aunque no haya salido por WhatsApp.
        guardar_mensaje(db, p, "assistant", texto, "whatsapp", envio)
        return {"respuesta": texto, "whatsapp": envio}

    p.estado = "cumple"
    await _asignar_curso_filtro(db, p)
    # antes la tarjeta solo se movía al agendar la cita, así que una postulación ya clasificada
    # como apta seguía viéndose "atorada" en Prefiltro mientras coordinaba fecha/hora.
    p.etapa = "Entrevista IA"
    registrar(db, "agente-ia", "auto_apto_zero_touch", "postulacion", p.codigo, {"prefiltro": resultado_prefiltro or "score", "score_cv": p.score})
    envio = await _avisar_apto_e_iniciar_agenda(db, p)
    return {"respuesta": _texto_apto(p), "whatsapp": envio}


def _parsear_fecha_cita(valor: str) -> Optional[datetime]:
    """agendar_videollamada debe regresar ISO 8601; si el modelo se equivocó de formato, se
    ignora la fecha — mejor no programar el aviso de no-show que programarlo mal.

    Se normaliza a UTC antes de regresar: SQLite no preserva el offset de un
    DateTime(timezone=True) — si se guardara "09:55:00-06:00" quedaría "09:55:00" a secas y
    services/agenda.py lo compararía contra un "ahora" en UTC como si esas 9:55 ya fueran UTC."""
    try:
        dt = datetime.fromisoformat(valor)
    except (TypeError, ValueError):
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_RE_URL = re.compile(r"https?://\S+")


def _sin_ligas(texto: str) -> str:
    """Quita cualquier URL que el modelo haya escrito en su respuesta (2026-09-17): aunque el prompt
    le pide no transcribir la liga, la copia del resultado de la herramienta y el candidato recibía
    DOS ligas (la del modelo + la real que anexa el sistema). La única liga que sale es la real."""
    limpio = _RE_URL.sub("", texto or "")
    limpio = re.sub(r"[ \t]+\n", "\n", limpio)
    limpio = re.sub(r"\n{3,}", "\n\n", limpio)
    return limpio.strip()


async def _procesar_turno_agenda(db: Session, p: Postulacion, historial: List[dict], canal: str, nota: str = "") -> dict:
    """Turno posterior a la clasificación: coordina la videollamada con la herramienta
    agendar_videollamada (function calling) — ver ia.agenda_turno. `nota` (2026-09-15): contexto
    extra para el modelo cuando se está reagendando."""
    v = p.vacante
    turno, con_ia = ia.agenda_turno(
        nombre_ficha(p), v.titulo if v else "", historial, db=db, candidato=p, nota=nota
    )

    respuesta_final = turno.respuesta
    if turno.cita_fecha_hora and turno.cita_liga:
        fecha = _parsear_fecha_cita(turno.cita_fecha_hora)
        p.videollamada_agendada_en = fecha or datetime.now(timezone.utc)
        p.videollamada_liga = turno.cita_liga
        p.etapa = "Entrevista IA"  # ver ETAPAS_CANDIDATO
        registrar(
            db, "agente-ia", "videollamada_agendada", "postulacion", p.codigo,
            {"fecha_hora": turno.cita_fecha_hora, "liga": turno.cita_liga, "fecha_parseada": bool(fecha)},
        )
        # La liga real se agrega aquí, textual — nunca se manda la que el modelo haya escrito
        # dentro de turno.respuesta: un token de 32+ caracteres es fácil de transcribir mal.
        respuesta_final = f"{_sin_ligas(turno.respuesta)}\n\n{turno.cita_liga}"  # una sola liga (2026-09-17)

    envio = await _enviar_whatsapp(p, respuesta_final, canal)
    guardar_mensaje(db, p, "assistant", respuesta_final, canal, envio)
    _actualizar_ultima_actividad(p)
    db.commit()
    return {
        "respuesta": respuesta_final,
        "clasificacion": None,
        "ia": con_ia,
        "whatsapp": envio,
        "cita": {"fechaHora": turno.cita_fecha_hora, "liga": turno.cita_liga} if turno.cita_liga else None,
    }


async def _procesar_turno_onboarding(db: Session, p: Postulacion, historial: List[dict], canal: str) -> dict:
    """Zero-Touch fase 2: la postulación ya está en Onboarding — el agente ya no evalúa ni
    agenda, solo acompaña la recolección de documentos (ver ia.onboarding_turno)."""
    v = p.vacante
    turno, con_ia = ia.onboarding_turno(nombre_ficha(p), v.titulo if v else "", historial)
    envio = await _enviar_whatsapp(p, turno.respuesta, canal)
    guardar_mensaje(db, p, "assistant", turno.respuesta, canal, envio)
    _actualizar_ultima_actividad(p)
    db.commit()
    return {"respuesta": turno.respuesta, "clasificacion": None, "ia": con_ia, "whatsapp": envio}


# 2026-09-15 — intención de reagendar la videollamada por WhatsApp. Palabras completas, sin acentos.
_RE_REAGENDAR = re.compile(
    r"\b(re-?agendar|re-?agendo|re-?agendamos|reprogramar|reprogramo|cambiar|cambio|mover|otra fecha|otro dia|otro horario|"
    r"nueva fecha|no pude|no puedo|no podre|no alcance|no alcanzo|no llegue|se me paso|se me olvido|me perdi|otra cita|"
    r"nueva cita|agendar de nuevo)\b"
)
_ACEPTA_REAGENDAR = {"si", "claro", "ok", "va", "vale", "dale", "acepto", "adelante", "porfavor", "por favor", "sale", "simon"}


def _norm_txt(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _quiere_reagendar(p: Postulacion, texto: str) -> bool:
    """True si el candidato, con cita ya agendada, pide moverla — o si contesta que sí al aviso de
    no-show («¿Te gustaría reagendar?»). Antes cualquier mensaje caía en la respuesta fija «Ya tienes
    tu videollamada agendada» y la cita nunca se movía."""
    if not p.videollamada_agendada_en:
        return False
    t = _norm_txt(texto)
    if _RE_REAGENDAR.search(t):
        return True
    if p.videollamada_aviso_noshow_enviado:
        palabras = set(re.findall(r"[a-z]+", t))
        return bool(palabras & _ACEPTA_REAGENDAR)
    return False


async def _reabrir_agenda(db: Session, p: Postulacion, historial: List[dict], canal: str) -> dict:
    """Suelta la cita anterior (queda en `analisis.videollamadas_anteriores`) y vuelve a la
    coordinación con la herramienta de agendamiento, en la MISMA postulación."""
    analisis = dict(p.analisis or {})
    anteriores = list(analisis.get("videollamadas_anteriores") or [])
    anteriores.append({
        "fecha": p.videollamada_agendada_en.isoformat() if p.videollamada_agendada_en else None,
        "liga": p.videollamada_liga or "",
        "noshow": bool(p.videollamada_aviso_noshow_enviado),
        "reagendada_en": datetime.now(timezone.utc).isoformat(),
    })
    analisis["videollamadas_anteriores"] = anteriores
    p.analisis = analisis
    registrar(
        db, "agente-ia", "videollamada_reagendar_solicitada", "postulacion", p.codigo,
        {"cita_anterior": anteriores[-1]["fecha"], "noshow": anteriores[-1]["noshow"], "canal": canal},
    )
    p.videollamada_agendada_en = None
    p.videollamada_liga = ""
    p.videollamada_aviso_noshow_enviado = False
    db.flush()
    return await _procesar_turno_agenda(db, p, historial, canal, nota=(
        "El candidato ya tenía una videollamada agendada y pidió REAGENDARLA (o no asistió). Reconoce el "
        "cambio con naturalidad, sin reprochar, y pregunta su nueva disponibilidad."
    ))


# Fase 3 (2026-09-15) — Entrevista IA interrumpida/parcial: el candidato la reanuda desde WhatsApp.
ESTADOS_ENTREVISTA_FALLIDA = ("interrumpida", "parcial")


def _entrevista_ia_fallida(p: Postulacion):
    """Última Entrevista IA de la postulación si quedó interrumpida o parcial (falló / se cortó)."""
    if not p.entrevistas:
        return None
    e = max(p.entrevistas, key=lambda x: x.id)
    return e if e.estado in ESTADOS_ENTREVISTA_FALLIDA else None


async def _reanudar_entrevista_ia(db: Session, p: Postulacion, e, texto: str, historial: List[dict], canal: str) -> dict:
    """El candidato escribe con su Entrevista IA interrumpida/parcial:
    - si pide otra fecha («reagendar», «no pude», «otro día»…) → se suelta la cita y el agente de
      agenda coordina una nueva (genera liga nueva; la fallida queda como historial);
    - cualquier otro mensaje → se REABRE la misma entrevista (mismo token, intento archivado) y se
      le manda la liga de nuevo. Antes solo RH podía reabrirla y el candidato recibía «ya tienes tu
      videollamada agendada» o nada."""
    t = _norm_txt(texto)
    if _RE_REAGENDAR.search(t):
        return await _reabrir_agenda(db, p, historial, canal)
    from ..services.entrevistas import reabrir_entrevista  # import local: entrevistas ↔ candidatos

    reabrir_entrevista(db, e, "candidato-whatsapp", "el candidato pidió reanudar por WhatsApp", {"texto": texto[:200], "canal": canal})
    liga = f"{settings.app_url}/entrevista/{e.token}"
    nombre = nombre_ficha(p).split(" ")[0] if nombre_ficha(p) else ""
    respuesta = (
        f"{('Hola ' + nombre + '. ') if nombre else ''}Vi que tu entrevista con Red Human quedó "
        f"{'incompleta' if e.estado == 'parcial' else 'interrumpida'}. Ya la reabrí: entra cuando estés listo(a) y la "
        f"retomamos desde el inicio 🙂\n\n{liga}\n\nSi prefieres otra fecha u horario, dime cuándo y lo reagendamos."
    )
    envio = await _enviar_whatsapp(p, respuesta, canal)
    guardar_mensaje(db, p, "assistant", respuesta, canal, envio)
    _actualizar_ultima_actividad(p)
    db.commit()
    return {"respuesta": respuesta, "clasificacion": None, "ia": False, "whatsapp": envio, "entrevista_reabierta": e.codigo}


async def _procesar_turno_post_completo(db: Session, p: Postulacion, texto: str, canal: str) -> dict:
    """No queda nada pendiente que la IA deba coordinar (no_cumple ya avisado, o cumple con
    videollamada ya agendada) — se responde con un mensaje fijo, sin volver a llamar al modelo."""
    if p.videollamada_agendada_en:
        respuesta = "¡Ya tienes tu videollamada agendada! Si necesitas reagendar, avísame y lo vemos. 🙌"
    elif p.estado == "no_cumple":
        # Ya se le avisó el rechazo — este mensaje NO debe sonar a que su proceso sigue activo.
        respuesta = (
            "Gracias por escribirnos de nuevo. Ya revisamos tu perfil para esta vacante y por ahora "
            "no avanza en el proceso, pero tus datos quedan en nuestra base para futuras oportunidades."
        )
    else:
        respuesta = "¡Gracias! Ya tengo tu información. Estoy procesando tu perfil y en breve te contactamos con los siguientes pasos. 😊"
    envio = await _enviar_whatsapp(p, respuesta, canal)
    guardar_mensaje(db, p, "assistant", respuesta, canal, envio)
    db.commit()
    return {"respuesta": respuesta, "clasificacion": None, "ia": False, "whatsapp": envio}


def _parsear_respuestas_web(crudo: str) -> List[dict]:
    try:
        datos = json.loads(crudo) if crudo else []
    except (ValueError, TypeError):
        return []
    salida = []
    for x in datos if isinstance(datos, list) else []:
        if isinstance(x, dict) and str(x.get("pregunta", "")).strip():
            salida.append({"pregunta": str(x.get("pregunta", "")).strip()[:300], "respuesta": str(x.get("respuesta", "")).strip()[:300]})
    return salida[:30]


def _norm_resp(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def _si_no(s: str) -> Optional[bool]:
    t = _norm_resp(s)
    if t in ("si", "sí", "yes", "claro", "por supuesto") or t.startswith("si,") or t.startswith("si "):
        return True
    if t in ("no", "nel", "negativo") or t.startswith("no,") or t.startswith("no "):
        return False
    return None


def _mismo_criterio(web_pregunta: str, wa: dict, preguntas_vac: list) -> bool:
    """La pregunta web y el criterio de WhatsApp hablan del mismo requisito: mismo texto, o la
    pregunta web pertenece a un criterio cuyo `valida` coincide con el de WhatsApp."""
    w = _norm_resp(web_pregunta)
    c = _norm_resp(wa.get("criterio", ""))
    q = _norm_resp(wa.get("pregunta", ""))
    if w and (w == c or w == q or (len(w) > 20 and (w in q or q in w))):
        return True
    for pv in preguntas_vac:
        if isinstance(pv, dict) and _norm_resp(pv.get("pregunta", "")) == w and pv.get("valida"):
            if _norm_resp(pv["valida"]) == c or _norm_resp(pv["valida"]) in q:
                return True
    return False


def comparar_web_vs_whatsapp(analisis: dict, preguntas_vac: list) -> List[dict]:
    """Prefiltro dual (2026-09-16): compara lo que la persona contestó en el formulario web con lo que
    dijo por WhatsApp sobre el MISMO criterio. Regresa las contradicciones nuevas (sí/no opuestos, o
    respuesta de opción distinta). Nunca descarta: solo registra para que la IA pregunte y RH decida."""
    web = analisis.get("respuestas_web") or []
    wa = analisis.get("respuestas_prefiltro") or []
    previas = {(i.get("criterio"), i.get("web"), i.get("whatsapp")) for i in (analisis.get("inconsistencias") or [])}
    nuevas: List[dict] = []
    for rw in web:
        for ra in wa:
            if not _mismo_criterio(rw.get("pregunta", ""), ra, preguntas_vac):
                continue
            web_bool = _si_no(rw.get("respuesta", ""))
            wa_bool = ra.get("cumple")
            wa_txt_bool = _si_no(ra.get("respuesta", ""))
            contradice = False
            if web_bool is not None and wa_txt_bool is not None and web_bool != wa_txt_bool:
                contradice = True
            elif web_bool is not None and wa_bool is not None and web_bool != wa_bool and wa_txt_bool is None:
                contradice = True
            elif web_bool is None and wa_txt_bool is None and rw.get("respuesta") and ra.get("respuesta") \
                    and _norm_resp(rw["respuesta"]) != _norm_resp(ra["respuesta"]) and len(_norm_resp(rw["respuesta"])) < 40 \
                    and _norm_resp(rw["respuesta"]) not in _norm_resp(ra["respuesta"]):
                contradice = True
            if contradice:
                clave = (ra.get("criterio") or rw.get("pregunta"), rw.get("respuesta"), ra.get("respuesta"))
                if clave in previas:
                    continue
                nuevas.append({
                    "criterio": ra.get("criterio") or rw.get("pregunta"), "pregunta": rw.get("pregunta"),
                    "web": rw.get("respuesta"), "whatsapp": ra.get("respuesta"),
                    "detectada_en": datetime.now(timezone.utc).isoformat(), "aclarada": False, "aclaracion": "",
                })
    return nuevas


def _texto_aclaracion(nombre: str, inc: dict) -> str:
    n = (nombre or "").split(" ")[0]
    return (
        f"{('Gracias, ' + n + '. ') if n else ''}Una duda para no equivocarme: en el formulario indicaste «{inc.get('web')}» "
        f"sobre {inc.get('criterio') or 'este punto'}, y por aquí me comentas «{inc.get('whatsapp')}». ¿Cuál es la correcta? 🙂"
    )


async def procesar_prefiltro(db: Session, p: Postulacion, texto: str, canal: str, wa_id: str = "") -> dict:
    """Registra el mensaje del candidato en ESTA postulación, corre un turno del agente y
    responde. El historial que ve el modelo es solo el de esta postulación: las preguntas de
    otra vacante no se mezclan."""
    guardar_mensaje(db, p, "user", texto, canal, wa_id=wa_id)
    db.flush()

    v = p.vacante
    mensajes_db = [{"rol": m.rol, "texto": m.texto} for m in p.mensajes]
    if mensajes_db and mensajes_db[-1]["texto"] == texto and mensajes_db[-1]["rol"] == "user":
        historial = mensajes_db
    else:
        historial = mensajes_db + [{"rol": "user", "texto": texto}]

    # Zero-Touch fase 2: ya en Onboarding -> el agente solo acompaña documentos. Va ANTES que las
    # ramas de fase 1 a propósito: sin este check, una postulación en Onboarding (que ya trae
    # prefiltro_completo=True y estado="cumple") caería en "ya tienes tu videollamada agendada".
    if p.etapa == "Onboarding":
        return await _procesar_turno_onboarding(db, p, historial, canal)

    # Zero-Touch fase 1: apto y sin videollamada agendada -> herramienta de agendamiento.
    if p.prefiltro_completo and p.estado == "cumple" and not p.videollamada_agendada_en:
        return await _procesar_turno_agenda(db, p, historial, canal)

    # Fase 3 (2026-09-15): Entrevista IA interrumpida/parcial → el candidato la reanuda (misma liga)
    # o la reagenda escribiendo por WhatsApp. Va ANTES de reagendar/post-completo a propósito.
    if p.etapa == "Entrevista IA" and p.prefiltro_completo and p.estado == "cumple":
        fallida = _entrevista_ia_fallida(p)
        if fallida is not None:
            return await _reanudar_entrevista_ia(db, p, fallida, texto, historial, canal)

    # 2026-09-15: con cita agendada, "quiero reagendar" / "sí" tras el aviso de no-show → se reabre
    # la coordinación en esta misma postulación (nunca al menú de vacantes ni respuesta fija).
    if p.prefiltro_completo and p.estado == "cumple" and _quiere_reagendar(p, texto):
        return await _reabrir_agenda(db, p, historial, canal)

    # Ya no hay nada más que resolver (no_cumple avisado, o cita ya agendada): respuesta fija.
    if p.prefiltro_completo:
        return await _procesar_turno_post_completo(db, p, texto, canal)

    # Fase 4 (2026-09-15): las preguntas del prefiltro por WhatsApp son independientes de las de la
    # postulación web; si RH no capturó ninguna, se usan las de la web (vacantes previas).
    preguntas_wa = ((v.preguntas_filtro_whatsapp or None) or (v.preguntas_filtro or [])) if v else []
    # 2026-09-16 (prefiltro dual): si el turno anterior pidió aclarar una contradicción Web vs WhatsApp,
    # el modelo registra la aclaración y sigue; la contradicción queda documentada para RH.
    analisis_previo = dict(p.analisis or {})
    pendiente = next((i for i in (analisis_previo.get("inconsistencias") or []) if not i.get("aclarada")), None)
    nota_aclaracion = ""
    if pendiente and analisis_previo.get("aclaracion_pendiente"):
        nota_aclaracion = (
            f"El candidato contestó distinto en el formulario web («{pendiente.get('web')}») y por WhatsApp "
            f"(«{pendiente.get('whatsapp')}») sobre {pendiente.get('criterio')}. Su último mensaje aclara ese punto: "
            "toma la aclaración como válida en respuestas_extraidas, agradece y continúa. NO lo descartes por la contradicción."
        )
    turno, con_ia = ia.prefiltro_turno(
        v.titulo if v else "vacante general",
        v.requisitos if v else "",
        preguntas_wa,
        historial,
        empresa=nombre_empresa_candidato(v) if v else "",
        ubicacion=v.ubicacion if v else "",
        sueldo=v.sueldo if v else "",
        modalidad=v.modalidad if v else "",
        beneficios=(v.beneficios or []) if v else [],
        perfil_ideal=v.perfil_ideal if v else "",
        nombre_candidato=nombre_ficha(p),
        nota=nota_aclaracion,
    )

    analisis_actual = dict(p.analisis or {})
    if turno.respuestas_extraidas:
        analisis_actual["respuestas_prefiltro"] = [r.model_dump() for r in turno.respuestas_extraidas]
    if pendiente and analisis_actual.get("aclaracion_pendiente"):
        # el candidato ya respondió a la pregunta de aclaración: se cierra esa contradicción con su texto
        for inc in analisis_actual.get("inconsistencias") or []:
            if not inc.get("aclarada"):
                inc["aclarada"] = True
                inc["aclaracion"] = texto[:300]
        analisis_actual["aclaracion_pendiente"] = False
        registrar(db, "agente-ia", "prefiltro_inconsistencia_aclarada", "postulacion", p.codigo, {"criterio": pendiente.get("criterio"), "aclaracion": texto[:200]})

    # Comparación automática Web vs WhatsApp: si hay contradicción nueva, la IA pregunta para aclarar
    # (un solo mensaje) en vez de clasificar; nunca se descarta por esto.
    nuevas = comparar_web_vs_whatsapp(analisis_actual, list(preguntas_wa) + list((v.preguntas_filtro or []) if v else []))
    if nuevas:
        analisis_actual["inconsistencias"] = list(analisis_actual.get("inconsistencias") or []) + nuevas
        analisis_actual["aclaracion_pendiente"] = True
        registrar(db, "agente-ia", "prefiltro_inconsistencia_detectada", "postulacion", p.codigo, {"inconsistencias": nuevas})
        p.analisis = analisis_actual
        aclaracion = _texto_aclaracion(nombre_ficha(p), nuevas[0])
        envio = await _enviar_whatsapp(p, aclaracion, canal)
        guardar_mensaje(db, p, "assistant", aclaracion, canal, envio)
        _actualizar_ultima_actividad(p)
        db.commit()
        return {"respuesta": aclaracion, "clasificacion": None, "ia": con_ia, "whatsapp": envio, "inconsistencias": nuevas}

    cierra_prefiltro = turno.clasificacion_lista and turno.estado and not p.prefiltro_completo
    # Con contradicciones registradas (aclaradas o no) la IA NUNCA descarta sola: el caso queda en
    # revisión para RH, con la evidencia y las contradicciones a la vista.
    if cierra_prefiltro and turno.estado == "no_cumple" and (analisis_actual.get("inconsistencias") or []):
        turno.estado = "revision"  # type: ignore[assignment]

    if cierra_prefiltro:
        # Turno de cierre: turno.respuesta es el mensaje genérico ("gracias, RH revisará") — nunca
        # debe llegar por WhatsApp ni quedar en el historial: _auto_decision_zero_touch manda el
        # veredicto real y mandar los dos seguidos confundía al candidato.
        # 2026-09-13: el prefiltro NO genera score ni pisa la evidencia del CV — su único resultado
        # es cumple/no_cumple (filtro básico de entrada); p.score sigue siendo SOLO el del CV.
        p.prefiltro_completo = True
        analisis_actual.update({
            "origen": analisis_actual.get("origen") or "prefiltro", "ia": con_ia,
            "prefiltro_resultado": turno.estado, "prefiltro_evidencia": turno.evidencia or "",
        })
        if not p.evidencia:
            p.evidencia = turno.evidencia or ""
        registrar(
            db, "agente-ia", "prefiltro_clasificado", "postulacion", p.codigo,
            {"ia": con_ia, "estado_ia": turno.estado, "evidencia": turno.evidencia or ""},
        )
        if turno.estado == "revision":
            p.estado = "revision"
            respuesta_rev = (
                f"¡Gracias por tus respuestas, {nombre_ficha(p).split(' ')[0]}! Tu información quedó registrada y una persona "
                "del equipo de RH la revisará con calma. Te contactamos por este medio. 😊"
            )
            envio_rev = await _enviar_whatsapp(p, respuesta_rev, canal)
            guardar_mensaje(db, p, "assistant", respuesta_rev, canal, envio_rev)
            registrar(db, "agente-ia", "prefiltro_en_revision_por_inconsistencia", "postulacion", p.codigo, {"evidencia": turno.evidencia or ""})
            resultado_cierre = {"respuesta": respuesta_rev, "whatsapp": envio_rev}
        else:
            resultado_cierre = await _auto_decision_zero_touch(db, p, turno.estado)
        clasificacion = {"estado": p.estado, "evidencia": turno.evidencia or ""}
        respuesta_final = resultado_cierre["respuesta"]
        envio = resultado_cierre["whatsapp"]
    else:
        envio = await _enviar_whatsapp(p, turno.respuesta, canal)
        guardar_mensaje(db, p, "assistant", turno.respuesta, canal, envio)
        clasificacion = None
        respuesta_final = turno.respuesta

    p.analisis = analisis_actual
    # Fase C: cada turno es actividad; si hubo clasificación, recalcular resultado_apto. (El
    # wrapper de Fase D nunca dispara "candidato_apto" aquí: la regla de prefiltro está excluida.)
    _actualizar_ultima_actividad(p)
    if cierra_prefiltro:
        await _recalcular_resultado_apto_y_notificar(db, p, "agente-ia")
    db.commit()
    return {"respuesta": respuesta_final, "clasificacion": clasificacion, "ia": con_ia, "whatsapp": envio}


@router.post("/{codigo}/prefiltro")
async def prefiltro(
    codigo: str,
    datos: MensajeIn,
    db: Session = Depends(get_db),
    _: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    p = _por_codigo(db, codigo, cuenta.id)
    if not datos.texto.strip():
        raise HTTPException(400, "El mensaje va vacío.")
    return await procesar_prefiltro(db, p, datos.texto, datos.canal)


# ------------------------------------------------------------
# Consentimiento (LFPDPPP) — requisito para tratar datos del candidato
# ------------------------------------------------------------


class ConsentimientoIn(BaseModel):
    acepta: bool = True
    medio: str = "verbal"  # portal | whatsapp | verbal | escrito
    evidencia: str = ""


@router.post("/{codigo}/consentimiento")
def consentimiento(
    codigo: str,
    datos: ConsentimientoIn,
    db: Session = Depends(get_db),
    u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Deja constancia del consentimiento (por proceso de selección) en la bitácora hash-encadenada."""
    p = _por_codigo(db, codigo, cuenta.id)
    if not datos.acepta:
        p.consentimiento = False
        p.consentimiento_fecha = None
        registrar(db, u.nombre, "consentimiento_revocado", "postulacion", p.codigo, {"candidato": p.candidato.codigo, "medio": datos.medio})
        db.commit()
        return postulacion_dict(p, detalle=True)

    p.consentimiento = True
    p.consentimiento_fecha = datetime.now(timezone.utc)
    _actualizar_ultima_actividad(p)
    registrar(
        db, u.nombre, "consentimiento_otorgado", "postulacion", p.codigo,
        {"candidato": p.candidato.codigo, "medio": datos.medio, "evidencia": datos.evidencia[:500], "correo_rh": u.correo},
    )
    db.commit()
    return postulacion_dict(p, detalle=True)


# ------------------------------------------------------------
# Decisión humana (HITL — LFPDPPP: RH decide, la IA recomienda)
# ------------------------------------------------------------
#
# El botón genérico "Avanzar etapa" se reemplazó por botones explícitos por destino
# (ver PATCH /{codigo}/etapa más abajo). "Descartar" sigue aquí porque no es un movimiento
# de tarjeta: es cerrar la postulación (activa=False, motivo 'descartado').


class DecisionIn(BaseModel):
    accion: str  # descartar (el avance genérico se reemplazó por PATCH /candidatos/{codigo}/etapa)
    comentario: str = ""


@router.post("/{codigo}/decision")
async def decision(
    codigo: str,
    datos: DecisionIn,
    db: Session = Depends(get_db),
    u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    p = _por_codigo(db, codigo, cuenta.id)
    if datos.accion != "descartar":
        raise HTTPException(400, "Acción inválida. Para mover de etapa usa PATCH /candidatos/{codigo}/etapa.")
    # 2026-09-17: descartar SÍ se permite con expediente abierto (Contratación / Onboarding) — RH lo
    # pedía desde el menú «…» y antes recibía 409. El expediente se cancela en la misma transacción
    # (mismo efecto que «Cancelar contratación»); solo un expediente ya dado de alta lo impide.
    expediente_cancelado = None
    if p.expediente:
        if p.expediente.estado == "alta":
            raise HTTPException(409, "Esta persona ya fue dada de alta como colaborador(a); no se puede descartar la postulación.")
        if not datos.comentario.strip():
            raise HTTPException(400, "Para descartar a un candidato con expediente abierto indica el motivo.")
        exp = p.expediente
        expediente_cancelado = exp.id
        registrar(
            db, u.nombre, "expediente_cancelado", "expediente", str(exp.id),
            {"motivo": f"Candidato descartado: {datos.comentario.strip()}", "postulacion": p.codigo, "etapa": p.etapa},
        )
        db.delete(exp)
        p.expediente = None
        db.flush()

    recomendacion_ia = {"estado": p.estado, "score": p.score, "etapa": p.etapa, "expediente_cancelado": expediente_cancelado}
    p.etapa = "Prefiltro"
    p.estado = "no_cumple"
    p.cerrar("descartado")  # queda como historial; si la persona reaplica se abre una nueva (decisión 2026-09-11)
    _actualizar_ultima_actividad(p)
    await _recalcular_resultado_apto_y_notificar(db, p, u.nombre)
    registrar(
        db, u.nombre, "decision_descartar", "postulacion", p.codigo,
        {"candidato": p.candidato.codigo, "recomendacion_ia": recomendacion_ia, "comentario": datos.comentario, "correo_rh": u.correo},
    )
    db.commit()
    return postulacion_dict(p, detalle=True)


@router.get("/agente/actividad")
def actividad_agente(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Contador REAL del sidebar («Prefiltrando N candidatos por WhatsApp»): postulaciones activas en
    Prefiltro sin terminar, de personas con WhatsApp (wa_id) y con mensaje en las últimas 24 h (ventana
    de sesión de Meta). Antes era un número quemado (3)."""
    corte = datetime.now(timezone.utc) - timedelta(hours=24)
    filas = (
        db.query(Postulacion)
        .join(Candidato, Postulacion.candidato_id == Candidato.id)
        .filter(
            Postulacion.cuenta_id == cuenta.id, Postulacion.activa.is_(True), Postulacion.etapa == "Prefiltro",
            Postulacion.prefiltro_completo.is_(False), Candidato.eliminado_en.is_(None), Candidato.wa_id != "",
        )
        .all()
    )
    prefiltrando = 0
    for p in filas:
        ultimo = max((m.creado_en for m in p.mensajes if m.canal == "whatsapp"), default=None)
        if ultimo is not None:
            if ultimo.tzinfo is None:
                ultimo = ultimo.replace(tzinfo=timezone.utc)
            if ultimo >= corte:
                prefiltrando += 1
    return {"prefiltrando": prefiltrando, "enPrefiltro": len(filas)}


def _fecha_hora_mx(dt: datetime) -> str:
    """«22/09/2026 14:35 h» en hora de México — para las leyendas del historial que lee RH."""
    return dt.astimezone(TZ_MEXICO).strftime("%d/%m/%Y %H:%M") + " h"


class EtapaIn(BaseModel):
    etapa: str
    comentario: str = ""
    # 2026-09-16 (control manual de RH): «Mover a otra etapa» — sin bloqueos de secuencia; lo que se salta
    # queda como «Omitida manualmente» (usuario, fecha, motivo = comentario).
    manual: bool = False
    # 2026-09-22 («Avanzar a Entrevista Humana»): RH decide saltarse la Entrevista Red Human desde Prefiltro
    # o desde la propia Entrevista IA. Implica `manual`, no bloquea por evaluaciones pendientes y deja la
    # leyenda «Entrevista Red Human omitida manualmente por [usuario] — [fecha y hora]» en el historial.
    # NADA de lo generado antes (chat, entrevista parcial, análisis de CV, score) se borra.
    omitir_entrevista_ia: bool = False


def _entrevista_humana_realizada(p: Postulacion) -> bool:
    from sqlalchemy.orm import object_session

    db = object_session(p)
    return bool(db and db.query(Evaluacion.id).filter(
        Evaluacion.postulacion_id == p.id, Evaluacion.tipo == "entrevista_humana",
        Evaluacion.estado.in_(["realizada_sin_resultado", "con_resultado"])).first())


# Actividad esperada en cada etapa y cómo saber si YA se hizo (para marcar «Omitida manualmente»).
def _actividades_pendientes(p: Postulacion, desde: str, hasta: str) -> List[str]:
    orden = ETAPAS_CANDIDATO
    if desde not in orden or hasta not in orden or orden.index(hasta) <= orden.index(desde):
        return []
    hechas = {
        "Prefiltro": bool(p.prefiltro_completo),
        "Entrevista IA": any(e.estado == "evaluada" for e in p.entrevistas),
        "Evaluación": bool(p.resultado_apto is not None or any(e.estado == "evaluada" for e in p.entrevistas)),
        "Entrevista Humana": _entrevista_humana_realizada(p),
        "Contratación": bool(p.expediente and p.expediente.progreso == 100),
    }
    saltadas = orden[orden.index(desde): orden.index(hasta)]
    return [e for e in saltadas if e in hechas and not hechas[e]]


@router.delete("/{codigo}")
def eliminar_candidato(
    codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """CRUD (2026-09-15): baja LÓGICA de la PERSONA (se acepta P-#### o C-####: siempre se elimina el
    candidato dueño). Todas sus postulaciones activas se cierran con motivo `eliminado`, sus
    entrevistas/expedientes/mensajes se conservan como historial y la persona desaparece del Kanban,
    de las búsquedas y de la deduplicación (si vuelve a escribir por WhatsApp nace una persona nueva).
    Nada se borra físicamente; queda en bitácora (LFPDPPP)."""
    p = _por_codigo(db, codigo, cuenta.id)
    c = p.candidato
    if c is None:
        raise HTTPException(404, "Candidato no encontrado")
    if any(col.activo and not col.eliminado_en for col in db.query(Colaborador).filter(Colaborador.candidato_origen_id == c.id).all()):
        raise HTTPException(409, "Esta persona ya es colaborador(a) activo(a); da de baja al colaborador antes de eliminar al candidato.")
    cerradas = []
    for post in c.postulaciones:
        if post.activa:
            post.cerrar("eliminado")
            cerradas.append(post.codigo)
    c.eliminado_en = datetime.now(timezone.utc)
    c.eliminado_por = u.nombre
    c.postulacion_conversacion_id = None
    registrar(
        db, u.nombre, "candidato_eliminado", "candidato", c.codigo,
        {"nombre": c.nombre, "postulaciones_cerradas": cerradas, "desde": codigo, "correo_rh": u.correo},
    )
    db.commit()
    return {"ok": True, "candidato": c.codigo, "postulacionesCerradas": cerradas}


def _abrir_expediente(db: Session, p: Postulacion, u: Usuario) -> Expediente:
    """Crea el expediente con el checklist de 6 documentos al entrar a Contratación. Pertenece a
    ESTA postulación (decisión P5): otra contratación de la misma persona tendrá el suyo."""
    exp = Expediente(
        candidato_id=p.candidato_id,
        puesto=p.vacante.titulo if p.vacante else "",
        seleccionado_por=u.nombre,
        token=secrets.token_urlsafe(24),
    )
    # se asigna por la relación (no solo postulacion_id=p.id): así p.expediente queda
    # sincronizado en memoria de inmediato para postulacion_dict.
    p.expediente = exp
    db.add(exp)
    db.flush()
    for tipo in DOCUMENTOS_BASE:
        db.add(Documento(expediente_id=exp.id, tipo=tipo, obligatorio=True))
    registrar(db, u.nombre, "expediente_abierto", "postulacion", p.codigo, {"candidato": p.candidato.codigo, "expediente": exp.id, "puesto": exp.puesto})
    return exp


@router.post("/{codigo}/expediente")
def asegurar_expediente(
    codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual),
):
    """2026-09-29: red de seguridad para postulaciones que llegaron a Contratación/Onboarding SIN expediente
    (cargas masivas o scripts que no pasaron por `_abrir_expediente`). Idempotente: si ya existe lo regresa tal
    cual; nunca crea uno para otra postulación ni en otra Cuenta. Exige el consentimiento (LFPDPPP)."""
    p = _por_codigo(db, codigo, cuenta.id)
    if p.expediente:
        return postulacion_dict(p, detalle=True)
    if p.etapa not in ("Contratación", "Onboarding"):
        raise HTTPException(409, "El expediente se abre al llegar a Contratación.")
    if not p.consentimiento:
        raise HTTPException(409, "El candidato no tiene consentimiento registrado para el tratamiento de sus datos (LFPDPPP). Regístralo antes de abrir su expediente.")
    exp = _abrir_expediente(db, p, u)
    registrar(db, u.nombre, "expediente_inicializado", "postulacion", p.codigo, {"expediente": exp.id, "motivo": "faltaba al generar documentos", "correo_rh": u.correo})
    db.commit()
    return postulacion_dict(p, detalle=True)


@router.patch("/{codigo}/etapa")
async def mover_etapa(
    codigo: str, datos: EtapaIn, forzar_prueba: bool = False,
    db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Avance manual explícito del Kanban — cada botón del panel manda su etapa destino exacta
    (ver ETAPAS_CANDIDATO). No reemplaza el flujo dedicado de Entrevista Humana
    (POST /{codigo}/entrevista-humana) — aquí se rechaza a propósito.

    "Entrevista IA" desde Prefiltro es la única "fricción manual" a propósito: fuerza la
    clasificación como apto y dispara el mismo mensaje que usa Zero-Touch para invitar al
    candidato a compartir disponibilidad.

    Mover una postulación cerrada (descartada) la reabre: es una decisión humana explícita.

    `forzar_prueba`: inerte salvo que Modo Prueba esté activo (ver
    services.configuracion.puede_forzar_prueba) — deja saltar los bloqueos de secuencia.

    Onboarding v2 (2026-09-28): a Onboarding SOLO se entra con «Iniciar Onboarding»
    (`POST /onboarding/expedientes/{id}/iniciar`, que llama a `aplicar_movimiento`); aquí se rechaza salvo
    con Modo Prueba activo."""
    p = _por_codigo(db, codigo, cuenta.id)
    await aplicar_movimiento(db, p, datos, u, forzar_prueba)
    return postulacion_dict(p, detalle=True)


async def aplicar_movimiento(
    db: Session, p: Postulacion, datos: EtapaIn, u: Usuario, forzar_prueba: bool = False, desde_iniciar: bool = False,
) -> Postulacion:
    """Núcleo del movimiento de etapa (hace commit). `desde_iniciar` = lo llama «Iniciar Onboarding»."""
    if datos.etapa not in ETAPAS_CANDIDATO:
        raise HTTPException(400, f"Etapa inválida. Usa una de: {', '.join(ETAPAS_CANDIDATO)}")
    prueba_total = modo_prueba_activo(db)
    if datos.etapa == "Onboarding" and not desde_iniciar and not prueba_total and datos.etapa != p.etapa:
        raise HTTPException(
            409,
            "Para pasar a Onboarding usa «Enviar a Onboarding» e «Iniciar Onboarding» desde el expediente "
            "(revisa condiciones, consentimiento, documentos y tareas).",
        )
    # «Avanzar a Entrevista Humana» (2026-09-22) es una decisión humana explícita: se comporta como manual.
    omitiendo_ia = bool(datos.omitir_entrevista_ia) and datos.etapa == "Entrevista Humana"
    manual = datos.manual or omitiendo_ia
    libre = manual or puede_forzar_prueba(db, forzar_prueba)
    if datos.etapa == p.etapa:
        raise HTTPException(409, f"La postulación ya está en {datos.etapa}.")
    if datos.etapa == "Entrevista Humana" and not manual:
        raise HTTPException(409, "Para programar la Entrevista Humana usa POST /candidatos/{codigo}/entrevista-humana.")
    if datos.etapa == "Onboarding":
        if p.etapa != "Contratación" and not libre:
            raise HTTPException(409, "Solo se puede enviar a Onboarding desde la etapa de Contratación.")
    elif p.etapa == "Onboarding" and not libre:
        raise HTTPException(409, "El candidato ya está en Onboarding; gestiona su expediente desde ese módulo.")

    # 2026-09-16 (control manual): lo que se salta queda como «Omitida manualmente» — registro interno.
    omitidas = _actividades_pendientes(p, p.etapa, datos.etapa) if manual else []
    if omitidas:
        ahora_iso = datetime.now(timezone.utc).isoformat()
        p.actividades_omitidas = list(p.actividades_omitidas or []) + [
            {"actividad": e, "etapa": e, "usuario": u.nombre, "fecha": ahora_iso, "motivo": datos.comentario.strip()[:300], "hacia": datos.etapa}
            for e in omitidas
        ]
        registrar(db, u.nombre, "actividades_omitidas_manualmente", "postulacion", p.codigo, {"actividades": omitidas, "hacia": datos.etapa, "motivo": datos.comentario[:300], "correo_rh": u.correo})

    if datos.etapa == "Entrevista IA":
        if p.etapa != "Prefiltro" and not libre:
            raise HTTPException(409, "Solo se puede forzar Entrevista IA desde la etapa de Prefiltro.")
        if not p.telefono and not libre:
            raise HTTPException(409, "El candidato no tiene WhatsApp registrado; no se puede iniciar el agendamiento.")
        p.estado = "cumple"
        p.prefiltro_completo = True
        await _asignar_curso_filtro(db, p)
        # Una falla de WhatsApp/Meta NUNCA bloquea el movimiento: se registra y RH sigue.
        try:
            envio = await _avisar_apto_e_iniciar_agenda(db, p)
        except Exception as ex:  # noqa: BLE001
            envio = {"enviado": False, "proveedor": "error", "detalle": str(ex)[:200]}
        registrar(db, u.nombre, "entrevista_ia_forzada", "postulacion", p.codigo, {"whatsapp": envio, "correo_rh": u.correo})
    if datos.etapa == "Onboarding" and not p.expediente and p.consentimiento:
        _abrir_expediente(db, p, u)  # movimiento manual directo a Onboarding: el expediente nace aquí

    if datos.etapa == "Contratación" and not p.expediente:
        if not p.consentimiento:
            raise HTTPException(
                409,
                "El candidato no tiene consentimiento registrado para el tratamiento de sus datos (LFPDPPP). "
                "Regístralo antes de continuar.",
            )
        _abrir_expediente(db, p, u)

    # 2026-09-22: leyenda explícita en el historial del expediente/postulación. Solo se AGREGA: la
    # entrevista IA parcial, el chat, el análisis de CV y el score se conservan tal cual.
    if omitiendo_ia:
        sello = datetime.now(timezone.utc)
        nota = f"Entrevista Red Human omitida manualmente por {u.nombre} — {_fecha_hora_mx(sello)}"
        p.historial = list(p.historial or []) + [
            {"evento": "entrevista_ia_omitida", "texto": nota, "usuario": u.nombre, "fecha": sello.isoformat(),
             "desde": p.etapa, "hacia": datos.etapa, "motivo": datos.comentario.strip()[:300]}
        ]
        registrar(
            db, u.nombre, "entrevista_ia_omitida", "postulacion", p.codigo,
            {"texto": nota, "desde": p.etapa, "motivo": datos.comentario.strip()[:300], "correo_rh": u.correo},
        )

    anterior = p.etapa
    reabierta = not p.activa
    if reabierta:
        p.activa = True
        p.motivo_cierre = ""
        p.cerrada_en = None
    p.etapa = datos.etapa
    # La relación Candidato-Vacante nunca se pierde en WhatsApp: si la persona no tiene conversación
    # fijada, esta postulación pasa a serlo (no se pisa una conversación en curso sobre otra vacante).
    if p.candidato and p.candidato.postulacion_conversacion_id is None:
        fijar_conversacion(p)
    _actualizar_ultima_actividad(p)
    try:
        await _recalcular_resultado_apto_y_notificar(db, p, u.nombre)
    except Exception as ex:  # noqa: BLE001 — correo/WhatsApp caídos no bloquean a RH
        registrar(db, "sistema", "notificacion_fallida_al_mover", "postulacion", p.codigo, {"error": str(ex)[:200]})
    registrar(
        db, u.nombre, "etapa_movida", "postulacion", p.codigo,
        {"candidato": p.candidato.codigo, "de": anterior, "a": datos.etapa, "comentario": datos.comentario, "reabierta": reabierta,
         "manual": manual, "omitidas": omitidas, "omitio_entrevista_ia": omitiendo_ia, "correo_rh": u.correo,
         "iniciar_onboarding": desde_iniciar},
    )
    if datos.etapa == "Onboarding" and not desde_iniciar and p.expediente:
        # Modo Prueba (única vía sin «Iniciar Onboarding»): las tareas nacen igual desde la plantilla, sin avisos.
        try:
            from ..services import onboarding as onb

            e = p.expediente
            with db.begin_nested():  # savepoint: si falla, el movimiento no se pierde
                cfg = onb.configuracion_para(db, p.cuenta_id, e.puesto, e.empresa)
                onb.aplicar_documentos(db, e, cfg["documentos"])
                onb.generar_tareas(db, e, p.cuenta_id, cfg, u.nombre)
                onb.sincronizar_legado(db, e)
        except Exception as ex:  # noqa: BLE001 — tablas de módulos no disponibles: el movimiento sigue
            registrar(db, "sistema", "onboarding_tareas_no_generadas", "postulacion", p.codigo, {"error": str(ex)[:200]})
    db.commit()
    return p


# ------------------------------------------------------------
# Entrevista Humana → Evaluaciones unificadas (2026-09-29). Programar / modificar / cancelar / marcar realizada /
# resultado / recordatorio viven en routers/evaluaciones.py (un solo objeto para todas las evaluaciones). Aquí
# quedan solo los helpers de Teams (Fase 7B) que usa la pantalla única «Agregar evaluación».
# ------------------------------------------------------------


def _asunto_teams(p: Postulacion) -> str:
    v = p.vacante
    return f"Entrevista — {v.titulo if v else 'Red Human'} — {p.nombre}"


async def _reunion_teams_o_error(db: Session, p: Postulacion, inicio: datetime, entrevistador: dict) -> Optional[dict]:
    """Fase 7B: si la Cuenta tiene Teams conectado, crea la reunión + invitaciones ANTES de guardar
    nada. Si Graph falla → 502 con el motivo y no se guarda la entrevista (decisión del usuario: nunca
    confirmar sin liga ni dejar una entrevista a medias). None = Teams no disponible en la Cuenta."""
    integ = teams_srv.integracion_de(db, p.cuenta_id)
    if not integ:
        return None
    asistentes = [{"correo": p.correo, "nombre": p.nombre}, entrevistador]
    try:
        return await teams_srv.crear_reunion(
            db, integ, asunto=_asunto_teams(p), inicio=inicio, asistentes=asistentes,
            cuerpo=f"Entrevista de {p.nombre} para {p.vacante.titulo if p.vacante else 'la vacante'}. Programada desde Red Human.",
        )
    except teams_srv.TeamsError as e:
        integ.ultimo_error = str(e)[:300]
        db.commit()
        raise HTTPException(502, f"No se pudo crear la reunión de Teams: {e} Reintenta o usa «Usar otra liga».")


async def _teams_best_effort(db: Session, p: Postulacion, eh, accion: str, **kw) -> Optional[str]:
    """Modificar/cancelar la reunión de Teams sin bloquear la acción de RH (supuesto Fase 7B)."""
    if not eh.teams_evento_id:
        return None
    integ = teams_srv.integracion_de(db, p.cuenta_id)
    if not integ:
        return "Teams ya no está conectado en la Cuenta; la reunión no se actualizó."
    try:
        if accion == "actualizar":
            await teams_srv.actualizar_reunion(db, integ, eh.teams_evento_id, inicio=kw["inicio"])
        else:
            await teams_srv.cancelar_reunion(db, integ, eh.teams_evento_id)
        return None
    except teams_srv.TeamsError as e:
        integ.ultimo_error = str(e)[:300]
        return f"La reunión de Teams no se pudo {accion}: {e}"


@router.get("/{codigo}/expediente")
def expediente(
    codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)
):
    p = _por_codigo(db, codigo, cuenta.id)
    if not p.expediente:
        raise HTTPException(404, "La postulación aún no tiene expediente de contratación.")
    return expediente_dict(p.expediente)


class CondicionesContratacionIn(BaseModel):
    puesto: str = ""
    sueldo: str = ""
    tipo_contratacion: str = ""
    fecha_ingreso: Optional[str] = None  # ISO: 2026-09-15
    ubicacion: str = ""
    jefe_directo: str = ""
    instrucciones_ingreso: str = ""  # Fase 5: van en la bienvenida automática al dar de alta
    empresa: str = ""  # 2026-09-20 (B2): razón social de la Cuenta o de un Cliente (vacío = la de la Cuenta); nunca libre
    duracion_contrato: Optional[int] = None  # 2026-09-20 (B2): solo «Tiempo determinado»
    duracion_unidad: str = ""  # días | meses | años


@router.patch("/{codigo}/condiciones-contratacion")
def guardar_condiciones_contratacion(
    codigo: str, datos: CondicionesContratacionIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Formulario de la etapa Contratación (puesto precargado pero editable, sueldo, tipo de
    contratación, fecha de ingreso, ubicación y jefe directo). Requiere que el expediente ya
    exista — se abre solo al entrar a Contratación, ver mover_etapa/_abrir_expediente.

    2026-09-20 (B2): es una ACTUALIZACIÓN PURA del expediente — no manda mensajes, no crea colaboradores
    y no mueve la etapa. «Tiempo determinado» exige duración + unidad y CALCULA la fecha de término;
    «Empresa contratante» solo acepta una razón social configurada en la Cuenta (Cuenta o Clientes)."""
    from .cuentas import razones_sociales_de

    p = _por_codigo(db, codigo, cuenta.id)
    if not p.expediente:
        raise HTTPException(404, "La postulación todavía no tiene expediente de contratación.")

    exp = p.expediente
    if datos.puesto.strip():
        exp.puesto = datos.puesto.strip()
    exp.sueldo = datos.sueldo.strip()
    exp.tipo_contratacion = datos.tipo_contratacion.strip()
    exp.ubicacion = datos.ubicacion.strip()
    exp.jefe_directo = datos.jefe_directo.strip()
    exp.instrucciones_ingreso = datos.instrucciones_ingreso.strip()
    razones = razones_sociales_de(db, cuenta)
    empresa = datos.empresa.strip()
    if empresa:
        valida = next((r["razonSocial"] for r in razones if r["razonSocial"].lower() == empresa.lower()), None)
        if not valida:
            raise HTTPException(400, "La empresa contratante debe ser una razón social configurada en la Cuenta (Configuración → Cuenta o Clientes).")
        exp.empresa = valida
    else:
        exp.empresa = razones[0]["razonSocial"]
    if datos.fecha_ingreso:
        try:
            exp.fecha_ingreso = datetime.fromisoformat(datos.fecha_ingreso).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(400, "fecha_ingreso inválida (usa ISO: 2026-09-15)")
    if exp.tipo_contratacion == TIPO_CONTRATACION_DETERMINADO:
        if not datos.duracion_contrato or datos.duracion_contrato <= 0:
            raise HTTPException(400, "Tiempo determinado: indica la duración del contrato (número mayor a cero).")
        if datos.duracion_unidad not in UNIDADES_DURACION:
            raise HTTPException(400, "Tiempo determinado: la unidad debe ser días, meses o años.")
        exp.duracion_contrato = int(datos.duracion_contrato)
        exp.duracion_unidad = datos.duracion_unidad
        exp.fecha_termino = calcular_fecha_termino(exp.fecha_ingreso, exp.duracion_contrato, exp.duracion_unidad)
    else:
        exp.duracion_contrato = None
        exp.duracion_unidad = ""
        exp.fecha_termino = None
    exp.condiciones_guardadas_en = datetime.now(timezone.utc)
    # Onboarding v2 (Fase 3): si cambió la fecha prevista, los plazos PENDIENTES se recalculan solos
    # (mientras no haya fecha real confirmada, que es la que manda después).
    try:
        from ..services import onboarding as onb

        with db.begin_nested():
            onb.recalcular_fechas(db, exp)
    except Exception:  # noqa: BLE001 — tablas de módulos no disponibles: las condiciones se guardan igual
        pass

    registrar(
        db, u.nombre, "condiciones_contratacion_guardadas", "postulacion", p.codigo,
        {"expediente": exp.id, "sueldo": exp.sueldo, "tipo_contratacion": exp.tipo_contratacion, "empresa": exp.empresa,
         "duracion": f"{exp.duracion_contrato} {exp.duracion_unidad}" if exp.duracion_contrato else "", "correo_rh": u.correo},
    )
    _actualizar_ultima_actividad(p)
    db.commit()  # B2: nada más — sin mensajes, sin colaborador, sin cambio de etapa
    return postulacion_dict(p, detalle=True)


# ------------------------------------------------------------
# Zero-Touch fase 2 — botones de Onboarding (RH detona, la IA da seguimiento)
# ------------------------------------------------------------
#
# El checklist real de documentos (qué falta, validación con IA, alta) sigue viviendo en
# contratacion.py sobre el Expediente. Estos dos endpoints son el "romper el hielo" y el
# "recordatorio" que pide RH desde la tarjeta — un mensaje simple que deja al agente
# (ia.onboarding_turno, ver procesar_prefiltro) listo para dar seguimiento a lo que el
# candidato conteste después.

async def _disparar_mensaje_onboarding(
    db: Session, p: Postulacion, evento: str, accion: str, liga: str, u: Usuario, notificar: Optional[NotificarIn] = None
) -> dict:
    # 2026-09-15: también en Contratación — el expediente nace en esa etapa y el cliente pide los
    # papeles (INE, comprobante) desde ahí por WhatsApp.
    if p.etapa not in ("Contratación", "Onboarding"):
        raise HTTPException(409, "Esta acción es solo para postulaciones en Contratación u Onboarding.")
    if p.expediente and p.expediente.no_ingreso_en:
        raise HTTPException(409, "Esta persona quedó como «No ingresó»: ya no se le piden documentos.")
    override = override_de(notificar)
    extra: dict = {}
    e = p.expediente
    if evento == "recordatorio_documentos" and e:
        # 2026-09-17: mismo contador de niveles que el recordatorio del expediente y el job automático.
        extra = {"nivel": e.nivel_recordatorio, "pendientes": e.pendientes or None, "puesto": e.puesto or "tu nuevo puesto", "fecha_limite": e.documentos_hasta}
    resultados = await notificaciones.disparar(db, evento, p, u.nombre, liga=liga, override=override, extra=extra)
    if evento == "recordatorio_documentos" and e:
        from ..services.recordatorios import registrar_recordatorio_enviado  # import local: recordatorios ↔ candidatos

        registrar_recordatorio_enviado(db, e, extra["nivel"], u.nombre, resultados)
        e.ultimo_recordatorio_en = datetime.now(timezone.utc)
    else:
        if e:
            from ..services.recordatorios import marcar_solicitud_documentos  # B3: trazabilidad por documento

            marcar_solicitud_documentos(e, resultados, u.nombre, "solicitud")
        registrar(db, u.nombre, accion, "postulacion", p.codigo, {"notificaciones": resultados, "correo_rh": u.correo, "notificar_override": override})
    _actualizar_ultima_actividad(p)
    db.commit()  # B5: solicitar/recordar documentos NUNCA cambia la etapa
    return {"resultados": resultados, "nivel": extra.get("nivel"), "candidato": postulacion_dict(p, detalle=True)}


def _liga_documentos(p: Postulacion) -> str:
    """Liga pública para que el candidato suba sus documentos (ver routers/expediente_publico.py)
    — genera el token del expediente perezosamente si es uno anterior (Expediente.token es nullable)."""
    if not p.expediente:
        raise HTTPException(409, "La postulación no tiene expediente de contratación; no se puede generar la liga de documentos.")
    if not p.expediente.token:
        p.expediente.token = secrets.token_urlsafe(24)
    return f"{settings.app_url}/expediente/{p.expediente.token}"


@router.post("/{codigo}/solicitar-documentos")
async def solicitar_documentos(
    codigo: str, notificar: Optional[NotificarIn] = Body(default=None, embed=True),
    db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual),
):
    """Botón 'Solicitar documentos' — rompe el hielo por WhatsApp al entrar a Onboarding, con
    la liga pública para que el candidato suba sus documentos él mismo."""
    p = _por_codigo(db, codigo, cuenta.id)
    liga = _liga_documentos(p)
    return await _disparar_mensaje_onboarding(db, p, "solicitud_documentos", "documentos_solicitados", liga, u, notificar)


@router.post("/{codigo}/recordatorio-documentos")
async def recordatorio_documentos(
    codigo: str, notificar: Optional[NotificarIn] = Body(default=None, embed=True),
    db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual),
):
    """Botón 'Enviar recordatorio' — seguimiento manual si el candidato no ha respondido."""
    p = _por_codigo(db, codigo, cuenta.id)
    liga = _liga_documentos(p)
    return await _disparar_mensaje_onboarding(db, p, "recordatorio_documentos", "recordatorio_documentos_enviado", liga, u, notificar)
