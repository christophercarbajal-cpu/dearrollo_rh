import hashlib
import json
import re
import unicodedata
from datetime import timedelta, date, datetime, timezone
from typing import List, Optional

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .database import Base
from .fechas import FechaUTC


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def slugificar(texto: str) -> str:
    """'Cajero(a) de sucursal' → 'cajero-a-de-sucursal' (para /aplicar/[slug])."""
    plano = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", plano.lower())).strip("-") or "vacante"


# Plataformas de publicación soportadas por el distribuidor (módulo 3.5).
# Canales de publicación que RH elige al crear/publicar una vacante (2026-09-26). OCC y LinkedIn salieron
# del catálogo: nunca tuvieron integración (sus textos generados se siguen copiando a mano desde
# `Vacante.publicaciones`). Google Empleos, Jooble y Talent.com por ahora solo se REGISTRAN; su
# integración (feeds XML / JobPosting) está en docs/arquitectura_bolsas_empleo.md. Valores viejos ya
# guardados en `Vacante.plataformas` se conservan tal cual (ver routers.vacantes._unir_plataformas).
PLATAFORMAS = ["Portal", "WhatsApp", "Google Empleos", "Jooble", "Talent.com"]

# Kanban de Candidato.etapa — flujo confirmado con el cliente (documento + audio, 2026-08-29):
# Prefiltro -> Entrevista IA -> Evaluación -> Entrevista Humana -> Contratación -> Onboarding.
# "Entrevista IA" cubre TANTO la videollamada mock que agenda el agente (Zero-Touch,
# ver candidatos._procesar_turno_agenda) COMO la entrevista con avatar del módulo 3.10
# (ver entrevistas.py): ambas las conduce la IA. "Entrevista Humana" es la única etapa
# nueva que RH mueve a mano sin automatización detrás.
ETAPAS_CANDIDATO = ["Prefiltro", "Entrevista IA", "Evaluación", "Entrevista Humana", "Contratación", "Onboarding"]

# 2026-09-15 (memoria de 5 días): en estas etapas la conversación de WhatsApp conserva su contexto
# hasta CONTEXTO_WHATSAPP_HORAS sin actividad — un "sí quiero reagendar" al día 3 debe caer en la
# postulación en curso, nunca en el menú de vacantes. Solo el Prefiltro sigue con la ventana corta
# del Modo Prueba (ConfiguracionSistema.modo_prueba_ventana_min).
ETAPAS_CONTEXTO_LARGO = ("Entrevista IA", "Evaluación", "Entrevista Humana", "Contratación", "Onboarding")
CONTEXTO_WHATSAPP_HORAS = 120


class Vacante(Base):
    __tablename__ = "vacantes"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(160), default="", index=True)
    titulo: Mapped[str] = mapped_column(String(200))
    # Evaluaciones (2026-09-28): sugeridas para quien se postule [{tipo, prueba_id?, nombre?}] y si RH quiere un
    # aviso al enviar a Onboarding cuando falte alguna o no esté revisada. Solo SUGIERE: nunca bloquea ni asigna sola.
    evaluaciones_sugeridas: Mapped[list] = mapped_column(JSON, default=list)
    avisar_evaluaciones_antes_onboarding: Mapped[bool] = mapped_column(Boolean, default=False)
    area: Mapped[str] = mapped_column(String(100), default="")
    empresa: Mapped[str] = mapped_column(String(150), default="Grupo Carbe")
    ubicacion: Mapped[str] = mapped_column(String(150), default="")
    modalidad: Mapped[str] = mapped_column(String(30), default="Presencial")
    # Parte 3 (2026-09-12): sueldo ESTRUCTURADO capturado por RH. `sueldo` (texto) se conserva como
    # valor DERIVADO para mostrar (WhatsApp, prefiltro, entrevista, portal, publicaciones, agente lo
    # siguen leyendo) — ver texto_sueldo(). Vacantes viejas: estructurado vacío y texto intacto.
    sueldo: Mapped[str] = mapped_column(String(80), default="A convenir")
    sueldo_desde: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sueldo_hasta: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sueldo_moneda: Mapped[str] = mapped_column(String(5), default="MXN")
    sueldo_periodicidad: Mapped[str] = mapped_column(String(15), default="")  # ver PERIODICIDADES_SUELDO
    estado: Mapped[str] = mapped_column(String(30), default="Borrador")  # Publicada | Borrador | En revisión | Cerrada | Eliminada
    # CRUD (2026-09-15): baja LÓGICA — la fila y sus postulaciones/entrevistas/expedientes se conservan;
    # deja de aparecer en listados, portal, menú de WhatsApp y métricas.
    eliminada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    eliminada_por: Mapped[str] = mapped_column(String(150), default="")
    requisitos: Mapped[str] = mapped_column(Text, default="")
    descripcion: Mapped[str] = mapped_column(Text, default="")
    texto_whatsapp: Mapped[str] = mapped_column(Text, default="")
    texto_bolsa: Mapped[str] = mapped_column(Text, default="")
    preguntas_filtro: Mapped[list] = mapped_column(JSON, default=list)  # [str] (legado) o [PreguntaFiltro]
    # Fase 4 (2026-09-15): preguntas del prefiltro por WhatsApp, INDEPENDIENTES de las de la postulación web
    # (`preguntas_filtro`). Vacía = el agente usa las de la web (compatibilidad con vacantes previas).
    preguntas_filtro_whatsapp: Mapped[list] = mapped_column(JSON, default=list)
    # Fase 4: ubicación estructurada (Estado / Municipio-Alcaldía de México); `ubicacion` (texto) se deriva.
    ubicacion_estado: Mapped[str] = mapped_column(String(60), default="")
    ubicacion_municipio: Mapped[str] = mapped_column(String(100), default="")
    # Capacitación universal (2026-09-16): curso que se asigna al candidato como filtro al quedar apto.
    curso_filtro_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cursos.id"), nullable=True)
    curso_filtro: Mapped[Optional["Curso"]] = relationship(foreign_keys=[curso_filtro_id])
    plataformas: Mapped[list] = mapped_column(JSON, default=list)
    # Fase 4 (Punto 6): qué cubre la Entrevista IA — ver ENFOQUES_ENTREVISTA. Solo 2 niveles.
    enfoque_entrevista: Mapped[str] = mapped_column(String(30), default="profesional")

    # --- contenido enriquecido del generador (módulo 3.5) ---
    resumen: Mapped[str] = mapped_column(Text, default="")
    perfil_ideal: Mapped[str] = mapped_column(Text, default="")
    responsabilidades: Mapped[list] = mapped_column(JSON, default=list)
    requisitos_deseables: Mapped[list] = mapped_column(JSON, default=list)
    beneficios: Mapped[list] = mapped_column(JSON, default=list)
    palabras_clave: Mapped[list] = mapped_column(JSON, default=list)
    seniority: Mapped[str] = mapped_column(String(40), default="")
    avisos_cumplimiento: Mapped[list] = mapped_column(JSON, default=list)
    # {"occ": {titulo, copy, page, etiquetas}, "linkedin": {...}, "portal": {...}, "whatsapp": {...}}
    publicaciones: Mapped[dict] = mapped_column(JSON, default=dict)

    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)
    # Fase C: fecha de primera publicación — se estampa automáticamente en publicar(), nunca captura manual.
    # NULL para vacantes que aún no se han publicado o que existían antes del deploy de Fase C.
    publicada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)

    # Origen: si esta vacante nació de una requisición autorizada (módulo 4). Puede ser null
    # para vacantes creadas directamente por RH sin pasar por el flujo de requisición.
    requisicion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("requisiciones.id"), nullable=True)
    requisicion: Mapped[Optional["Requisicion"]] = relationship(back_populates="vacante")

    # --- Cuenta/Cliente (Fase A multi-cuenta) — nullable a nivel de esquema (SQLite sin Alembic
    # no puede agregar NOT NULL retroactivo); la obligatoriedad de cuenta_id se aplica en capa de
    # aplicación. cliente_id es opcional de verdad: una Cuenta sin Clientes recluta directo.
    cuenta_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cuentas.id"), nullable=True, index=True)
    cliente_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clientes.id"), nullable=True, index=True)

    # --- Fase B: creación de vacante (Responsable/Colaboradores/plantilla/visibilidad del Cliente) ---
    responsable_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    # lista de ids de Usuario — sin tabla puente, mismo patrón que Vacante.plataformas/preguntas_filtro
    colaboradores_ids: Mapped[list] = mapped_column(JSON, default=list)
    # solo aplica si cliente_id está definido; default True = comportamiento de hoy (se muestra)
    mostrar_cliente_candidato: Mapped[bool] = mapped_column(Boolean, default=True)
    # de qué Plantilla nació esta vacante, si de alguna — solo trazabilidad, no fuerza nada
    plantilla_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plantillas.id"), nullable=True)

    responsable: Mapped[Optional["Usuario"]] = relationship(foreign_keys=[responsable_id])
    cliente: Mapped[Optional["Cliente"]] = relationship()
    cuenta: Mapped[Optional["Cuenta"]] = relationship()

    # Fase 2: las aplicaciones a esta vacante (una tarjeta del Kanban cada una).
    postulaciones: Mapped[List["Postulacion"]] = relationship(back_populates="vacante", order_by="Postulacion.id")


class Candidato(Base):
    """PERSONA (maestro de identidad) — Fase 2 (Puntos 7/8).

    Un candidato es una persona: nombre, contacto, WhatsApp, CV y archivos. Todo lo que es
    "proceso" (etapa, estado, score, chat, entrevistas, expediente) vive en `Postulacion`:
    una persona puede aplicar a varias vacantes a lo largo del tiempo y cada aplicación es
    una tarjeta distinta en el Kanban.

    Ruteo de WhatsApp (decisión 2026-09-11): `postulacion_conversacion_id` apunta a la
    postulación "en conversación" — lo mueve SOLO el candidato (un mensaje entrante suyo o
    una selección explícita en la lista interactiva), nunca un mensaje saliente de RH o del
    sistema (B1). Si el puntero no sirve y hay más de una postulación esperando respuesta,
    el webhook PREGUNTA con una lista interactiva; nunca adivina (ver webhooks.py).
    """

    __tablename__ = "candidatos"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    correo: Mapped[str] = mapped_column(String(200), default="")
    telefono: Mapped[str] = mapped_column(String(30), default="", index=True)
    ubicacion: Mapped[str] = mapped_column(String(150), default="")
    experiencia: Mapped[str] = mapped_column(String(250), default="")
    fuente: Mapped[str] = mapped_column(String(30), default="Formulario")  # Formulario|WhatsApp|OCC|LinkedIn|Indeed|RH
    cv_datos: Mapped[dict] = mapped_column(JSON, default=dict)
    wa_nombre: Mapped[str] = mapped_column(String(200), default="")  # nombre del perfil de WhatsApp
    wa_id: Mapped[str] = mapped_column(String(30), default="", index=True)  # ID de WhatsApp (tel tal como lo envía Meta)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    # Modo Prueba (solo admin, ver ConfiguracionSistema): nunca aparece en listados/reportes de RH.
    es_prueba: Mapped[bool] = mapped_column(Boolean, default=False)
    # CRUD (2026-09-15): baja LÓGICA de la persona (LFPDPPP: derecho de cancelación con rastro en
    # bitácora). Sus postulaciones se cierran (motivo `eliminado`); nada se borra físicamente.
    eliminado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    eliminado_por: Mapped[str] = mapped_column(String(150), default="")
    # Cuenta (Fase A multi-cuenta).
    cuenta_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cuentas.id"), nullable=True, index=True)
    # Postulación con la que está conversando por WhatsApp ahora mismo (ver docstring).
    postulacion_conversacion_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("postulaciones.id", use_alter=True, name="fk_candidato_postulacion_conversacion"), nullable=True
    )

    # --- LEGADO (pre-Fase 2): estado de proceso que antes vivía en la persona. Solo lo lee
    # scripts/migrar_postulaciones.py para crear la Postulación inicial; NINGÚN endpoint lo
    # escribe ni lo lee ya. Se conservan sin tocar hasta correr la migración de datos.
    vacante_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vacantes.id"), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    etapa: Mapped[str] = mapped_column(String(30), default="Prefiltro")
    score: Mapped[int] = mapped_column(Integer, default=0)
    evidencia: Mapped[str] = mapped_column(Text, default="")
    analisis: Mapped[dict] = mapped_column(JSON, default=dict)
    consentimiento: Mapped[bool] = mapped_column(Boolean, default=False)
    consentimiento_fecha: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    prefiltro_completo: Mapped[bool] = mapped_column(Boolean, default=False)
    videollamada_agendada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    videollamada_liga: Mapped[str] = mapped_column(String(300), default="")
    videollamada_aviso_noshow_enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    ultima_actividad_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    resultado_apto: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # LEGADO más antiguo — puente para scripts/migrar_entrevistas_humanas.py.
    entrevista_humana_entrevistador: Mapped[str] = mapped_column(String(150), default="")
    entrevista_humana_tipo: Mapped[str] = mapped_column(String(20), default="")
    entrevista_humana_usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    entrevista_humana_correo_externo: Mapped[str] = mapped_column(String(200), default="")
    entrevista_humana_fecha: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    entrevista_humana_modalidad: Mapped[str] = mapped_column(String(20), default="")
    entrevista_humana_liga: Mapped[str] = mapped_column(String(300), default="")
    entrevista_humana_ubicacion: Mapped[str] = mapped_column(String(300), default="")
    entrevista_humana_telefono_contacto: Mapped[str] = mapped_column(String(30), default="")
    entrevista_humana_comentario: Mapped[str] = mapped_column(Text, default="")
    entrevista_humana_realizada: Mapped[bool] = mapped_column(Boolean, default=False)
    entrevista_humana_resultado: Mapped[str] = mapped_column(String(20), default="")
    entrevista_humana_recomendacion: Mapped[str] = mapped_column(String(30), default="")

    # --- Relaciones de persona ---
    archivos: Mapped[List["Archivo"]] = relationship(
        back_populates="candidato", order_by="Archivo.id", cascade="all, delete-orphan"
    )
    postulaciones: Mapped[List["Postulacion"]] = relationship(
        back_populates="candidato", order_by="Postulacion.id",
        primaryjoin="Candidato.id == Postulacion.candidato_id", foreign_keys="Postulacion.candidato_id",
        cascade="all, delete-orphan",
    )
    postulacion_conversacion: Mapped[Optional["Postulacion"]] = relationship(
        primaryjoin="Candidato.postulacion_conversacion_id == Postulacion.id",
        foreign_keys=[postulacion_conversacion_id], post_update=True,
    )
    # Vistas de solo lectura sobre TODAS las postulaciones de la persona (historial completo).
    # Cada hijo también cuelga de su Postulación; escribir siempre por la Postulación.
    mensajes: Mapped[List["Mensaje"]] = relationship(
        order_by="Mensaje.id", viewonly=True,
        primaryjoin="Candidato.id == Mensaje.candidato_id", foreign_keys="Mensaje.candidato_id",
    )
    entrevistas: Mapped[List["Entrevista"]] = relationship(
        order_by="Entrevista.id", viewonly=True,
        primaryjoin="Candidato.id == Entrevista.candidato_id", foreign_keys="Entrevista.candidato_id",
    )
    entrevistas_humanas: Mapped[List["EntrevistaHumana"]] = relationship(
        order_by="EntrevistaHumana.id", viewonly=True,
        primaryjoin="Candidato.id == EntrevistaHumana.candidato_id", foreign_keys="EntrevistaHumana.candidato_id",
    )
    expedientes: Mapped[List["Expediente"]] = relationship(
        order_by="Expediente.id", viewonly=True,
        primaryjoin="Candidato.id == Expediente.candidato_id", foreign_keys="Expediente.candidato_id",
    )

    @property
    def postulaciones_activas(self) -> List["Postulacion"]:
        return [p for p in self.postulaciones if p.activa]


# Recordatorios de documentos en 3 niveles (2026-09-17): tono progresivo, nunca agresivo.
NIVELES_RECORDATORIO = {1: "ligero", 2: "intermedio", 3: "definitivo"}

# Cómo nació la postulación — alimenta "por fuente" en /metricas.
ORIGENES_POSTULACION = ["formulario", "whatsapp", "rh_directo", "cv_masivo", "reinicio_prueba", "migracion"]
# Por qué se cerró (activa=False). "" mientras sigue en curso.
MOTIVOS_CIERRE = ["descartado", "contratado", "reinicio_prueba", "prueba_expirada", "sin_interes", "vacante_eliminada", "eliminado"]


class Postulacion(Base):
    """Una aplicación de una persona (`Candidato`) a una `Vacante` — la unidad del Kanban.

    Aquí vive TODO el estado del proceso: etapa, clasificación del prefiltro, score, chat,
    entrevistas (IA y humanas) y el expediente de contratación (decisión P5: la contratación
    es resultado de una aplicación específica). `vacante_id` es nullable solo mientras el
    candidato elige vacante por WhatsApp (menú inicial).
    """

    __tablename__ = "postulaciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # P-####
    candidato_id: Mapped[int] = mapped_column(ForeignKey("candidatos.id"), index=True)
    vacante_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vacantes.id"), nullable=True, index=True)
    cuenta_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cuentas.id"), nullable=True, index=True)

    activa: Mapped[bool] = mapped_column(Boolean, default=True)  # False = cerrada (ver motivo_cierre)
    motivo_cierre: Mapped[str] = mapped_column(String(30), default="")  # ver MOTIVOS_CIERRE
    cerrada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    origen: Mapped[str] = mapped_column(String(30), default="formulario")  # ver ORIGENES_POSTULACION
    # Copia de Candidato.es_prueba al crear (para filtrar métricas sin JOIN).
    es_prueba: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- Estado del proceso ---
    etapa: Mapped[str] = mapped_column(String(30), default="Prefiltro")  # ver ETAPAS_CANDIDATO
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")  # cumple | revision | no_cumple | pendiente
    score: Mapped[int] = mapped_column(Integer, default=0)
    evidencia: Mapped[str] = mapped_column(Text, default="")
    # detalle del match del CV contra la vacante + respuestas_prefiltro + respuestas_web + inconsistencias
    # (Web vs WhatsApp, 2026-09-16) + flags de conversación
    analisis: Mapped[dict] = mapped_column(JSON, default=dict)
    prefiltro_completo: Mapped[bool] = mapped_column(Boolean, default=False)
    # 2026-09-22 («Avanzar a Entrevista Humana»): historial legible del expediente — notas de decisiones
    # humanas que hay que poder leer en la ficha sin abrir la bitácora ([{evento, texto, usuario, fecha, …}]).
    # SOLO se agrega: nunca se borra ni se reescribe lo ya generado (chat, entrevistas, análisis).
    historial: Mapped[list] = mapped_column(JSON, default=list)
    # 2026-09-16 (control manual de RH): actividades que RH saltó al mover de etapa —
    # [{actividad, etapa, usuario, fecha, motivo}] — registro interno, nunca bloquea.
    actividades_omitidas: Mapped[list] = mapped_column(JSON, default=list)
    # Fase C: resultado vigente ("el más reciente gana"), ver candidatos._recalcular_resultado_apto.
    resultado_apto: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    ultima_actividad_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)

    # --- Consentimiento LFPDPPP: por proceso de selección ---
    consentimiento: Mapped[bool] = mapped_column(Boolean, default=False)
    consentimiento_fecha: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)

    # --- Zero-Touch fase 1: videollamada agendada por el agente (herramienta agendar_videollamada) ---
    videollamada_agendada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    videollamada_liga: Mapped[str] = mapped_column(String(300), default="")
    videollamada_aviso_noshow_enviado: Mapped[bool] = mapped_column(Boolean, default=False)

    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    # --- Relaciones ---
    candidato: Mapped["Candidato"] = relationship(
        back_populates="postulaciones",
        primaryjoin="Postulacion.candidato_id == Candidato.id", foreign_keys=[candidato_id],
    )
    vacante: Mapped[Optional["Vacante"]] = relationship(back_populates="postulaciones")
    cuenta: Mapped[Optional["Cuenta"]] = relationship()
    mensajes: Mapped[List["Mensaje"]] = relationship(
        back_populates="postulacion", order_by="Mensaje.id", cascade="all, delete-orphan"
    )
    entrevistas: Mapped[List["Entrevista"]] = relationship(
        back_populates="postulacion", order_by="Entrevista.id", cascade="all, delete-orphan"
    )
    entrevistas_humanas: Mapped[List["EntrevistaHumana"]] = relationship(
        back_populates="postulacion", order_by="EntrevistaHumana.id", cascade="all, delete-orphan"
    )
    expediente: Mapped[Optional["Expediente"]] = relationship(
        back_populates="postulacion", uselist=False, cascade="all, delete-orphan"
    )

    # --- Datos de persona, delegados (solo lectura) — así los serializadores y las plantillas
    # de mensajes pueden leer p.nombre / p.telefono sin conocer la separación. ---
    @property
    def nombre(self) -> str:
        return self.candidato.nombre if self.candidato else ""

    @property
    def correo(self) -> str:
        return self.candidato.correo if self.candidato else ""

    @property
    def telefono(self) -> str:
        return self.candidato.telefono if self.candidato else ""

    @property
    def ubicacion(self) -> str:
        return self.candidato.ubicacion if self.candidato else ""

    @property
    def experiencia(self) -> str:
        return self.candidato.experiencia if self.candidato else ""

    @property
    def fuente(self) -> str:
        return self.candidato.fuente if self.candidato else "Formulario"

    @property
    def wa_id(self) -> str:
        return self.candidato.wa_id if self.candidato else ""

    @property
    def wa_nombre(self) -> str:
        return self.candidato.wa_nombre if self.candidato else ""

    @property
    def archivos(self) -> list:
        return self.candidato.archivos if self.candidato else []

    @property
    def cv_datos(self) -> dict:
        return self.candidato.cv_datos if self.candidato else {}

    @property
    def espera_respuesta(self) -> bool:
        """True si el agente está a media conversación con el candidato por ESTA postulación:
        prefiltro en curso, coordinando videollamada u onboarding. Es lo que el webhook usa para
        saber entre qué postulaciones tendría que elegir un mensaje entrante."""
        if not self.activa:
            return False
        if self.etapa == "Onboarding":
            return True
        if self.etapa == "Prefiltro":
            return not self.prefiltro_completo
        if self.etapa == "Entrevista IA":
            return self.estado == "cumple" and not self.videollamada_agendada_en
        # Evaluación / Entrevista Humana / Contratación: RH ya tomó el control — aunque el
        # prefiltro haya quedado a medias, el agente no tiene nada que preguntar por chat.
        return False

    def cerrar(self, motivo: str) -> None:
        self.activa = False
        self.motivo_cierre = motivo
        self.cerrada_en = ahora()


class EntrevistaHumana(Base):
    """Una ronda de entrevista humana (flujo manual de RH, ver ETAPAS_CANDIDATO). Un candidato
    puede tener varias a lo largo del proceso — cada "Agendar otra Entrevista Humana" crea una
    fila nueva en vez de sobreescribir la anterior, para no perder el resultado de rondas
    previas."""

    __tablename__ = "entrevistas_humanas"

    id: Mapped[int] = mapped_column(primary_key=True)
    # candidato_id (persona) se conserva desnormalizado para consultas de historial; la
    # entrevista pertenece a la Postulación. NULL en postulacion_id = registro previo a la
    # migración de Fase 2 (scripts/migrar_postulaciones.py lo rellena).
    candidato_id: Mapped[int] = mapped_column(ForeignKey("candidatos.id"), index=True)
    postulacion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("postulaciones.id"), nullable=True, index=True)
    entrevistador: Mapped[str] = mapped_column(String(150), default="")  # nombre a mostrar (usuario.nombre si es interno, tecleado si es externo)
    tipo: Mapped[str] = mapped_column(String(20), default="")  # interno | externo
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    correo_externo: Mapped[str] = mapped_column(String(200), default="")
    # WhatsApp del entrevistador externo (Fase D, punto 23) — paralelo a correo_externo, se
    # registra al asignarlo y se reutiliza para invitaciones/recordatorios de esa misma ronda.
    whatsapp_externo: Mapped[str] = mapped_column(String(30), default="")
    # Fase 7A: entrevistador externo elegido de los contactos del Cliente de la vacante (trazabilidad;
    # nombre/correo/WhatsApp se copian arriba con los datos con los que se notificó).
    contacto_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cliente_contactos.id"), nullable=True)
    # Fase 7B: id del evento de calendario (Graph) cuando la videollamada la creó Teams — sirve para
    # modificar/cancelar la reunión; "" = liga manual.
    teams_evento_id: Mapped[str] = mapped_column(String(300), default="")
    fecha: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    modalidad: Mapped[str] = mapped_column(String(20), default="")  # Presencial|Videollamada|Llamada
    liga: Mapped[str] = mapped_column(String(300), default="")  # obligatoria si modalidad=Videollamada
    ubicacion: Mapped[str] = mapped_column(String(300), default="")  # obligatoria si modalidad=Presencial
    telefono_contacto: Mapped[str] = mapped_column(String(30), default="")  # opcional si modalidad=Llamada
    comentario: Mapped[str] = mapped_column(Text, default="")
    realizada: Mapped[bool] = mapped_column(Boolean, default=False)
    # Fase D, evento "Entrevista cancelada" — no mueve la etapa del candidato automáticamente,
    # RH decide el siguiente paso a mano (agendar otra ronda o mover la etapa).
    cancelada: Mapped[bool] = mapped_column(Boolean, default=False)
    resultado: Mapped[str] = mapped_column(String(20), default="")  # aprobado | no_aprobado
    recomendacion: Mapped[str] = mapped_column(String(30), default="")  # avanzar | no_avanzar | segunda_entrevista
    # --- evaluación del entrevistador por liga (Lote 3) ---
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # liga pública para que el entrevistador registre su evaluación
    # "" hasta que alguien capture el resultado; "rh" | "entrevistador" según quién ganó la
    # carrera (ver candidatos.py: RH siempre puede sobreescribir después, para corregir).
    resultado_capturado_por: Mapped[str] = mapped_column(String(20), default="")
    # 2026-09-19: recordatorio automático (job) y cierre del ciclo desde la liga del entrevistador.
    recordatorio_enviado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    evaluada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    candidato: Mapped["Candidato"] = relationship(foreign_keys=[candidato_id])
    postulacion: Mapped[Optional["Postulacion"]] = relationship(back_populates="entrevistas_humanas")


class Archivo(Base):
    """Archivo del prospecto (CV y anexos) — el expediente de contratación usa `Documento`."""

    __tablename__ = "archivos"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidato_id: Mapped[int] = mapped_column(ForeignKey("candidatos.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(40), default="cv")  # cv | carta | certificado | otro
    nombre: Mapped[str] = mapped_column(String(255), default="")  # nombre original del archivo
    ruta: Mapped[str] = mapped_column(String(400), default="")
    mime: Mapped[str] = mapped_column(String(80), default="")
    tamano: Mapped[int] = mapped_column(Integer, default=0)
    estado: Mapped[str] = mapped_column(String(20), default="recibido")  # recibido | revision | rechazado
    notas_ia: Mapped[str] = mapped_column(Text, default="")
    extraccion: Mapped[dict] = mapped_column(JSON, default=dict)
    subido_por: Mapped[str] = mapped_column(String(150), default="RH")
    subido_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    candidato: Mapped[Candidato] = relationship(back_populates="archivos")


# parcial = el candidato contestó poco y la IA determinó que NO hay información suficiente para una
# evaluación integral (2026-09-13). Igual que interrumpida, no genera score ni recomendación.
ESTADOS_ENTREVISTA = ["programada", "en_curso", "completada", "evaluada", "interrumpida", "parcial"]
# Motivos de una entrevista NO evaluada (Entrevista.motivo): sin_respuestas = transcript vacío o
# sin intervenciones reales del candidato · desconexion = se cortó con muy pocas respuestas ·
# parcial = contestó poco y la IA juzgó insuficiente. Acción siguiente: «Reintentar Entrevista Red Human».
MOTIVOS_ENTREVISTA = ["", "sin_respuestas", "desconexion", "parcial"]
# herramienta = tool `terminar_entrevista` del avatar · marcador = despedida detectada en el
# transcript · texto = `terminada` del modo texto · manual = botón del candidato ·
# desconexion = CONNECTION_CLOSED / red · tiempo = tope de sesión.
CIERRES_ENTREVISTA = ["herramienta", "marcador", "texto", "manual", "desconexion", "tiempo"]
# Con estos cierres la entrevista se considera completa y se evalúa; con los demás, si el candidato
# habló poco, queda `interrumpida` (RH puede reabrir).
CIERRES_COMPLETOS = ("herramienta", "marcador", "texto", "manual")


class Entrevista(Base):
    """Entrevista estructurada con agente IA (módulo 3.10) — avatar de video o texto."""

    __tablename__ = "entrevistas"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    candidato_id: Mapped[int] = mapped_column(ForeignKey("candidatos.id"), index=True)  # persona (historial)
    postulacion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("postulaciones.id"), nullable=True, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # liga pública para el candidato
    tipo: Mapped[str] = mapped_column(String(12), default="avatar")  # avatar | texto
    # programada | en_curso | completada | evaluada | interrumpida (ver ESTADOS_ENTREVISTA)
    estado: Mapped[str] = mapped_column(String(20), default="programada")
    guion: Mapped[dict] = mapped_column(JSON, default=dict)  # {enfoque, temas[], preguntas[]} (preguntas = legado)
    transcript: Mapped[list] = mapped_column(JSON, default=list)  # [{rol, texto}]
    evaluacion: Mapped[dict] = mapped_column(JSON, default=dict)  # EvaluacionEntrevista (+ perfil profundo, Fase 4)
    # Fase 4 (Punto 4): cómo terminó — señal que el backend pudo verificar (ver CIERRES_ENTREVISTA).
    # Vacío mientras sigue abierta. La transición a evaluada/interrumpida SOLO ocurre en /finalizar.
    cierre: Mapped[str] = mapped_column(String(20), default="")
    motivo: Mapped[str] = mapped_column(String(30), default="")  # ver MOTIVOS_ENTREVISTA
    iniciada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    finalizada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    # 2026-09-17: última vez que el navegador sincronizó el transcript (modo avatar) o hubo turno de
    # texto. Con ella el job `cerrar_entrevistas_inactivas` cierra y evalúa entrevistas cuya pestaña
    # se cerró sin /finalizar — antes se quedaban `en_curso` para siempre y RH no veía nada.
    ultima_actividad_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    # Reapertura explícita por RH: cada intento anterior se archiva aquí ({transcript, evaluacion,
    # cierre, finalizada_en}) — nunca se pisa ni se borra.
    intentos_previos: Mapped[list] = mapped_column(JSON, default=list)
    consentimiento: Mapped[bool] = mapped_column(Boolean, default=False)
    consentimiento_fecha: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    programada_para: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    # Videollamada de Google Meet generada a mano por RH (botones del panel) — independiente
    # de `token`/avatar Anam de arriba. Usa el mismo generador mock que la herramienta
    # agendar_videollamada del agente (ver services/ia.agendar_videollamada_mock).
    liga_meet: Mapped[str] = mapped_column(String(300), default="")
    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    candidato: Mapped[Candidato] = relationship(foreign_keys=[candidato_id])
    postulacion: Mapped[Optional["Postulacion"]] = relationship(back_populates="entrevistas")


class Mensaje(Base):
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidato_id: Mapped[int] = mapped_column(ForeignKey("candidatos.id"), index=True)  # persona (historial)
    postulacion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("postulaciones.id"), nullable=True, index=True)
    rol: Mapped[str] = mapped_column(String(12))  # user | assistant
    texto: Mapped[str] = mapped_column(Text)
    canal: Mapped[str] = mapped_column(String(20), default="whatsapp")  # whatsapp | web | simulador
    enviado: Mapped[bool] = mapped_column(Boolean, default=True)
    # id del mensaje en WhatsApp (wamid…). Meta reenvía el webhook si no le
    # contestamos rápido; guardarlo evita procesar dos veces el mismo mensaje.
    wa_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    candidato: Mapped[Candidato] = relationship(foreign_keys=[candidato_id])
    postulacion: Mapped[Optional["Postulacion"]] = relationship(back_populates="mensajes")


# ============================================================
# Módulo 4 · Requisiciones inteligentes — Cazatalentos de IA
# ============================================================
#
# Flujo: un gerente levanta una Requisicion (por qué se abre la plaza, skills,
# sueldo) → RH la autoriza → al autorizar se corre el Radar Interno, que compara
# la requisición contra los Empleado activos y guarda cada resultado como
# SugerenciaMovilidad → si no hay match interno suficiente, RH decide publicar
# hacia afuera y la Requisicion se convierte en una Vacante (Vacante.requisicion_id).
# A partir de ahí corre el pipeline que ya existe: generador de contenido,
# prefiltro conversacional y extracción/ranking de CVs externos.


MOTIVOS_REQUISICION = ["Crecimiento", "Reemplazo"]
ESTADOS_REQUISICION = ["borrador", "pendiente_autorizacion", "autorizada", "rechazada", "convertida_vacante"]


class Requisicion(Base):
    """Solicitud de un gerente para abrir una plaza — vive ANTES de que exista la Vacante pública."""

    __tablename__ = "requisiciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)

    solicitante_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    solicitante_nombre: Mapped[str] = mapped_column(String(150), default="")
    area: Mapped[str] = mapped_column(String(100), default="")

    motivo: Mapped[str] = mapped_column(String(20), default="Crecimiento")  # Crecimiento | Reemplazo
    reemplazo_de: Mapped[str] = mapped_column(String(150), default="")  # solo si motivo == "Reemplazo"

    puesto: Mapped[str] = mapped_column(String(200))
    ubicacion: Mapped[str] = mapped_column(String(150), default="")
    modalidad: Mapped[str] = mapped_column(String(30), default="Presencial")
    sueldo_propuesto: Mapped[str] = mapped_column(String(80), default="A convenir")
    habilidades_requeridas: Mapped[list] = mapped_column(JSON, default=list)  # [str] — insumo del Radar Interno
    requisitos: Mapped[str] = mapped_column(Text, default="")
    justificacion: Mapped[str] = mapped_column(Text, default="")

    estado: Mapped[str] = mapped_column(String(30), default="borrador")
    autorizada_por: Mapped[str] = mapped_column(String(150), default="")
    autorizada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    comentario_autorizacion: Mapped[str] = mapped_column(Text, default="")

    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)

    # Cuenta/Cliente (Fase A multi-cuenta) — la Vacante que nace de esta Requisición hereda
    # estos valores automáticamente (regla de Fase E).
    cuenta_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cuentas.id"), nullable=True, index=True)
    cliente_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clientes.id"), nullable=True, index=True)

    solicitante: Mapped[Optional["Usuario"]] = relationship()
    vacante: Mapped[Optional["Vacante"]] = relationship(back_populates="requisicion", uselist=False)
    sugerencias: Mapped[List["SugerenciaMovilidad"]] = relationship(
        back_populates="requisicion", order_by="SugerenciaMovilidad.porcentaje_match.desc()"
    )


class Empleado(Base):
    """Colaborador activo de la empresa — universo del Radar Interno y, a futuro, base del
    ciclo de vida del Colaborador (onboarding, capacitación, desempeño, clima)."""

    __tablename__ = "empleados"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    correo: Mapped[str] = mapped_column(String(200), default="")
    telefono: Mapped[str] = mapped_column(String(30), default="")
    puesto_actual: Mapped[str] = mapped_column(String(200), default="")
    area: Mapped[str] = mapped_column(String(100), default="")
    ubicacion: Mapped[str] = mapped_column(String(150), default="")
    seniority: Mapped[str] = mapped_column(String(40), default="")
    skills: Mapped[list] = mapped_column(JSON, default=list)  # [str] — insumo del match del Radar Interno
    anios_experiencia: Mapped[float] = mapped_column(Float, default=0.0)
    fecha_ingreso: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    jefe_directo_id: Mapped[Optional[int]] = mapped_column(ForeignKey("empleados.id"), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    # si esta persona fue contratada a través de la plataforma, queda la trazabilidad completa
    candidato_origen_id: Mapped[Optional[int]] = mapped_column(ForeignKey("candidatos.id"), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    # Cuenta/Cliente (Fase A multi-cuenta) — el Radar Interno compara Requisición contra
    # Empleados del mismo Cliente.
    cuenta_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cuentas.id"), nullable=True, index=True)
    cliente_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clientes.id"), nullable=True, index=True)

    jefe_directo: Mapped[Optional["Empleado"]] = relationship(remote_side=[id])
    candidato_origen: Mapped[Optional["Candidato"]] = relationship()
    sugerencias: Mapped[List["SugerenciaMovilidad"]] = relationship(back_populates="empleado")


ESTADOS_SUGERENCIA = ["sugerida", "notificada", "interesado", "no_interesado", "avanzo", "descartada"]


class SugerenciaMovilidad(Base):
    """Resultado del Radar Interno: qué tanto empata un empleado actual con una requisición,
    antes de salir a buscar afuera. La decisión final de avanzarlo siempre es de RH (HITL)."""

    __tablename__ = "sugerencias_movilidad"

    id: Mapped[int] = mapped_column(primary_key=True)
    requisicion_id: Mapped[int] = mapped_column(ForeignKey("requisiciones.id"), index=True)
    empleado_id: Mapped[int] = mapped_column(ForeignKey("empleados.id"), index=True)
    porcentaje_match: Mapped[int] = mapped_column(Integer, default=0)
    habilidades_coincidentes: Mapped[list] = mapped_column(JSON, default=list)
    habilidades_faltantes: Mapped[list] = mapped_column(JSON, default=list)
    evidencia: Mapped[str] = mapped_column(Text, default="")
    estado: Mapped[str] = mapped_column(String(20), default="sugerida")
    revisado_por: Mapped[str] = mapped_column(String(150), default="")
    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    requisicion: Mapped["Requisicion"] = relationship(back_populates="sugerencias")
    empleado: Mapped["Empleado"] = relationship(back_populates="sugerencias")


# Checklist exacto pedido por el cliente para el expediente de contratación.
DOCUMENTOS_BASE = [
    "Identificación oficial",
    "CURP",
    "Constancia de Situación Fiscal / RFC",
    "Número de Seguridad Social",
    "Comprobante de domicilio",
    "Cuenta bancaria / CLABE",
]


# Onboarding v2 (2026-09-28): vocabulario visible de los documentos. NO se migran datos: los valores
# guardados (pendiente | revision | recibido | rechazado) se LEEN así. «Aprobado» exige que una persona de
# RH lo haya confirmado (`revisado_por`); lo que la IA validó sola sigue «Por revisar» (HITL).
ESTADOS_DOCUMENTO_ONBOARDING = ("Pendiente", "Por revisar", "Aprobado", "Rechazado", "No aplica")
# Documento interno que cierra la tarea fija «Contrato firmado» (Fase 2).
TIPO_CONTRATO_FIRMADO = "Contrato firmado"
TIPO_CARTA_FIRMADA = "Carta de intención firmada"  # Dropbox Sign (2026-09-29), también documento interno


def estado_documento_onboarding(d: "Documento") -> str:
    if d.estado == "no_aplica":
        return "No aplica"
    if d.estado == "rechazado":
        return "Rechazado"
    if d.estado == "recibido":
        return "Aprobado" if d.revisado_por else "Por revisar"
    if d.estado == "revision" and d.archivo:
        return "Por revisar"
    return "Pendiente"


# 2026-09-20 (B2): tipo de contratación con vigencia y unidades de duración permitidas.
TIPO_CONTRATACION_DETERMINADO = "Tiempo determinado"
UNIDADES_DURACION = ("días", "meses", "años")


def calcular_fecha_termino(fecha_ingreso: Optional[datetime], duracion: Optional[int], unidad: str) -> Optional[datetime]:
    """Fecha de término = fecha de ingreso + duración. Meses/años se suman calendario (28 feb + 1 mes = 28 mar;
    31 ene + 1 mes = 28/29 feb). None si falta cualquier dato."""
    if not fecha_ingreso or not duracion or duracion <= 0 or unidad not in UNIDADES_DURACION:
        return None
    if unidad == "días":
        return fecha_ingreso + timedelta(days=duracion)
    meses = duracion if unidad == "meses" else duracion * 12
    total = fecha_ingreso.month - 1 + meses
    anio, mes = fecha_ingreso.year + total // 12, total % 12 + 1
    import calendar as _cal

    dia = min(fecha_ingreso.day, _cal.monthrange(anio, mes)[1])
    return fecha_ingreso.replace(year=anio, month=mes, day=dia)


class Expediente(Base):
    __tablename__ = "expedientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Fase 2 (decisión P5): el expediente pertenece a la Postulación (uno por postulación).
    # candidato_id (persona) se conserva para contratacion.py / historial; una persona puede
    # tener varios expedientes a lo largo del tiempo (uno por contratación).
    candidato_id: Mapped[Optional[int]] = mapped_column(ForeignKey("candidatos.id"), nullable=True, index=True)
    postulacion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("postulaciones.id"), unique=True, nullable=True)
    puesto: Mapped[str] = mapped_column(String(200), default="")
    # --- condiciones finales de contratación (formulario de la etapa Contratación) ---
    sueldo: Mapped[str] = mapped_column(String(80), default="")
    tipo_contratacion: Mapped[str] = mapped_column(String(60), default="")
    ubicacion: Mapped[str] = mapped_column(String(150), default="")
    jefe_directo: Mapped[str] = mapped_column(String(150), default="")
    fecha_ingreso: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    # 2026-09-19 (Bloque 3): empresa contratante capturada en las condiciones (default: la visible de la vacante).
    # 2026-09-20 (B2): SOLO una razón social configurada en la Cuenta (Cuenta.razon_social o la de un Cliente);
    # nunca texto libre — ver routers.cuentas.razones_sociales / candidatos.guardar_condiciones_contratacion.
    empresa: Mapped[str] = mapped_column(String(200), default="")
    condiciones_guardadas_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    # 2026-09-20 (B2): «Tiempo determinado» = duración (número + unidad) y fecha de término CALCULADA
    # (`calcular_fecha_termino`), nunca capturada a mano. Vacíos en los demás tipos de contratación.
    duracion_contrato: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duracion_unidad: Mapped[str] = mapped_column(String(10), default="")  # UNIDADES_DURACION
    fecha_termino: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    # --- preparación de ingreso (Onboarding, bloque 4) ---
    contrato: Mapped[str] = mapped_column(String(20), default="Pendiente")  # Pendiente | Firmado
    alta_administrativa: Mapped[str] = mapped_column(String(20), default="Pendiente")  # Pendiente | Realizada
    equipo_accesos: Mapped[str] = mapped_column(String(20), default="Pendiente")  # Pendiente | Listo | No aplica
    estado: Mapped[str] = mapped_column(String(20), default="integracion")  # integracion | completo | alta
    alta_autorizada_por: Mapped[str] = mapped_column(String(150), default="")
    alta_fecha: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    seleccionado_por: Mapped[str] = mapped_column(String(150), default="")
    # Liga pública para que el candidato suba sus documentos sin sesión (Lote 4). Nullable:
    # los expedientes creados antes de este lote no tienen uno hasta que se genera perezosamente
    # (ver candidatos._disparar_mensaje_onboarding) — no es de un solo uso como el de
    # EntrevistaHumana, sigue válido hasta que el expediente llega a estado "alta".
    token: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    # Fase 5 (2026-09-15): instrucciones de ingreso que RH captura en Contratación (hora y lugar de
    # llegada, con quién presentarse, qué llevar…). Van en el mensaje automático de bienvenida al alta.
    instrucciones_ingreso: Mapped[str] = mapped_column(Text, default="")
    # Fase 3 (2026-09-15): «recordar hasta» — fecha límite que respeta el cron de recordatorios de
    # documentos. Null = sin recordatorios automáticos para este expediente.
    documentos_hasta: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    ultimo_recordatorio_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    documentos_vencidos_avisado: Mapped[bool] = mapped_column(Boolean, default=False)
    # 2026-09-17: recordatorios en 3 niveles progresivos (ligero → intermedio → definitivo). Cuenta los
    # enviados (automáticos y manuales); el nivel del SIGUIENTE es min(enviados+1, 3). Tras el
    # definitivo no salen más automáticos: RH da seguimiento (bitácora `recordatorios_agotados`).
    recordatorios_enviados: Mapped[int] = mapped_column(Integer, default=0)
    # Onboarding v2 (2026-09-28): plantilla que se aplicó (Configuración → Plantillas de Onboarding).
    # Null = aún no se generó el Onboarding o se usó la configuración predeterminada.
    plantilla_onboarding_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Onboarding v2 (Fase 3): fecha REAL de llegada («Confirmar ingreso»). Con ella el alta se habilita y los
    # plazos pendientes se recalculan contra la fecha real (si no, contra la prevista `fecha_ingreso`).
    fecha_ingreso_real: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    ingreso_confirmado_por: Mapped[str] = mapped_column(String(150), default="")
    ingreso_confirmado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    # «Cerrar Onboarding» (manual, nunca automático) y «No ingresó» (solo antes del alta).
    onboarding_cerrado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    onboarding_cerrado_por: Mapped[str] = mapped_column(String(150), default="")
    no_ingreso_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    no_ingreso_por: Mapped[str] = mapped_column(String(150), default="")
    no_ingreso_motivo: Mapped[str] = mapped_column(Text, default="")

    candidato: Mapped[Optional[Candidato]] = relationship(foreign_keys=[candidato_id])
    postulacion: Mapped[Optional["Postulacion"]] = relationship(back_populates="expediente")
    documentos: Mapped[List["Documento"]] = relationship(
        back_populates="expediente", order_by="Documento.id", cascade="all, delete-orphan"
    )

    @property
    def obligatorios(self) -> List["Documento"]:
        """Obligatorios que SÍ aplican a esta persona (RH puede marcar uno «No aplica» con motivo). Los
        documentos INTERNOS (contrato firmado) nunca cuentan."""
        return [d for d in self.documentos if d.obligatorio and d.estado != "no_aplica" and not d.interno]

    @property
    def progreso(self) -> int:
        """% de documentos OBLIGATORIOS **Aprobados** — es lo que habilita el contrato y el alta.
        Onboarding v2 (2026-09-28, decisión del usuario): solo cuenta lo que una persona de RH confirmó
        (`Documento.aprobado`); lo subido o validado solo por la IA queda «Por revisar» y no suma. En Modo
        Prueba la subida se aprueba sola («Modo Prueba» en `revisado_por`)."""
        docs = self.obligatorios
        if not docs:
            return 0
        aprobados = sum(1 for d in docs if d.aprobado)
        return round(aprobados / len(docs) * 100)

    @property
    def no_aprobados(self) -> List[str]:
        """Obligatorios que todavía no están «Aprobados» (lo que falta para el 100 %)."""
        return [d.tipo for d in self.obligatorios if not d.aprobado]

    @property
    def pendientes(self) -> List[str]:
        return [d.tipo for d in self.obligatorios if d.estado in ("pendiente", "rechazado")]

    @property
    def nivel_recordatorio(self) -> int:
        """Nivel (1-3) del PRÓXIMO recordatorio — ver NIVELES_RECORDATORIO."""
        return min((self.recordatorios_enviados or 0) + 1, 3)

    @property
    def recordatorios_agotados(self) -> bool:
        return (self.recordatorios_enviados or 0) >= 3

    @property
    def por_revisar(self) -> List[str]:
        return [d.tipo for d in self.documentos if d.estado == "revision"]

    @property
    def sin_confirmar(self) -> List[str]:
        """Obligatorios entregados que ninguna persona de RH ha confirmado todavía (HITL del alta)."""
        return [d.tipo for d in self.obligatorios if d.entregado and not d.revisado_por]


class Documento(Base):
    __tablename__ = "documentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    expediente_id: Mapped[int] = mapped_column(ForeignKey("expedientes.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(80))
    # pendiente | revision | recibido | rechazado | no_aplica (Onboarding v2, 2026-09-28: SOLO RH, con motivo).
    # Vocabulario de Onboarding (Pendiente / Por revisar / Aprobado / Rechazado / No aplica) = `estado_documento_onboarding`.
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    obligatorio: Mapped[bool] = mapped_column(Boolean, default=True)
    archivo: Mapped[str] = mapped_column(String(300), default="")  # ruta en disco
    nombre_archivo: Mapped[str] = mapped_column(String(255), default="")  # nombre original
    mime: Mapped[str] = mapped_column(String(80), default="")
    tamano: Mapped[int] = mapped_column(Integer, default=0)
    notas_ia: Mapped[str] = mapped_column(Text, default="")
    validacion: Mapped[dict] = mapped_column(JSON, default=dict)  # salida cruda de ia.validar_documento
    revisado_por: Mapped[str] = mapped_column(String(150), default="")
    subido_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    actualizado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)
    # 2026-09-20 (B3, trazabilidad): cuándo y por qué canal se PIDIÓ el documento (primera solicitud +
    # historial de solicitudes/recordatorios) y cuándo/por dónde se RECIBIÓ. Lo escriben
    # `services.recordatorios.marcar_solicitud_documentos` y `contratacion._registrar_documento` /
    # `marcar_documento`; nunca cambian la etapa de la postulación (B5).
    solicitado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    solicitado_canal: Mapped[str] = mapped_column(String(40), default="")  # whatsapp | correo | whatsapp, correo
    solicitudes: Mapped[list] = mapped_column(JSON, default=list)  # [{en, canal, tipo: solicitud|recordatorio, por}]
    recibido_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    recibido_canal: Mapped[str] = mapped_column(String(40), default="")  # whatsapp | liga | rh | fisico
    # Onboarding v2 (2026-09-28): «No aplica» lo marca SOLO una persona de RH y siempre con motivo.
    motivo_no_aplica: Mapped[str] = mapped_column(Text, default="")
    no_aplica_por: Mapped[str] = mapped_column(String(150), default="")
    no_aplica_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    # Onboarding v2 (Fase 2): documento INTERNO de RH (el contrato firmado). Nunca se le pide al candidato,
    # no entra al porcentaje ni a recordatorios y solo se carga por su acción propia.
    interno: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def aplica(self) -> bool:
        return self.estado != "no_aplica"

    @property
    def aprobado(self) -> bool:
        """«Aprobado» = recibido y confirmado por una persona de RH (o Modo Prueba)."""
        return self.estado == "recibido" and bool(self.revisado_por)

    @property
    def entregado(self) -> bool:
        """Cuenta para el porcentaje: recibido (físico o confirmado) o digital subido pendiente de revisión."""
        return self.estado == "recibido" or (self.estado == "revision" and bool(self.archivo))

    expediente: Mapped[Expediente] = relationship(back_populates="documentos")


class Colaborador(Base):
    """Colaborador activo — se crea al presionar «Dar de alta como colaborador» al cierre del
    Onboarding (ver contratacion.alta). Hereda del candidato y del expediente lo definitivo:
    nombre, puesto, CV, sueldo, ubicación, jefe directo y empresa.
    """

    __tablename__ = "colaboradores"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    correo: Mapped[str] = mapped_column(String(200), default="")
    telefono: Mapped[str] = mapped_column(String(30), default="")
    puesto: Mapped[str] = mapped_column(String(200), default="")
    # 2026-09-22: área/departamento — la usan los permisos de la Base de Conocimiento y los tableros de
    # Desempeño y Clima. Se captura en el perfil del colaborador; nunca en otro módulo.
    area: Mapped[str] = mapped_column(String(120), default="")
    salario: Mapped[str] = mapped_column(String(80), default="")
    empresa: Mapped[str] = mapped_column(String(150), default="")
    # 2026-09-19 (Bloque 3): condiciones FINALES de contratación tal como se guardaron en el expediente y
    # snapshot inmutable de ingreso (nunca se edita después del alta; es el registro histórico).
    tipo_contratacion: Mapped[str] = mapped_column(String(60), default="")
    condiciones_ingreso: Mapped[dict] = mapped_column(JSON, default=dict)
    ubicacion: Mapped[str] = mapped_column(String(150), default="")
    jefe_directo: Mapped[str] = mapped_column(String(150), default="")  # nombre a mostrar
    # 2026-09-27 (Desempeño v2): el jefe como otro colaborador del roster (para proponer evaluador).
    jefe_id: Mapped[Optional[int]] = mapped_column(ForeignKey("colaboradores.id", use_alter=True, name="fk_colaborador_jefe"), nullable=True)
    cv_ruta: Mapped[str] = mapped_column(String(400), default="")
    cv_nombre: Mapped[str] = mapped_column(String(255), default="")
    fecha_ingreso: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    dado_de_alta_por: Mapped[str] = mapped_column(String(150), default="")
    candidato_origen_id: Mapped[Optional[int]] = mapped_column(ForeignKey("candidatos.id"), nullable=True)
    expediente_id: Mapped[Optional[int]] = mapped_column(ForeignKey("expedientes.id"), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    # Cuenta/Cliente (Fase A multi-cuenta) — hereda de la Vacante/Candidato de origen al dar de alta.
    cuenta_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cuentas.id"), nullable=True, index=True)
    cliente_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clientes.id"), nullable=True, index=True)

    # 2026-09-15: baja (activo=False, conserva historial) y eliminación LÓGICA (limpieza de pruebas).
    baja_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    baja_motivo: Mapped[str] = mapped_column(String(300), default="")
    baja_por: Mapped[str] = mapped_column(String(150), default="")
    eliminado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    eliminado_por: Mapped[str] = mapped_column(String(150), default="")

    candidato_origen: Mapped[Optional["Candidato"]] = relationship()
    cliente: Mapped[Optional["Cliente"]] = relationship()  # Fase 5: filtro por Cliente
    expediente: Mapped[Optional["Expediente"]] = relationship()
    # 2026-09-18: sus asignaciones de capacitación se van con él (borrado físico en cascada; en la
    # eliminación LÓGICA el endpoint las borra explícitamente para que el tablero y los contadores se actualicen).
    asignaciones_curso: Mapped[List["AsignacionCurso"]] = relationship(cascade="all, delete-orphan", passive_deletes=False)


# ============================================================
# Cuentas y Clientes (Fase A · reestructuración multi-cuenta)
# ============================================================
#
# Cuenta = empresa reclutadora que opera la plataforma (puede tener cero o varios
# Clientes: empresas para las que recluta). Un Usuario puede tener acceso a varias
# Cuentas (ver UsuarioCuenta) — si solo tiene una, el frontend no muestra ningún selector.


class Cuenta(Base):
    __tablename__ = "cuentas"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Punto 9: nombre interno con el que RH identifica la Cuenta (listados/selector). Vacío en
    # las cuentas previas → en lecturas se resuelve `nombre or nombre_comercial` (ver nombre_visible).
    nombre: Mapped[str] = mapped_column(String(200), default="")
    nombre_comercial: Mapped[str] = mapped_column(String(200))  # lo que ven candidatos/portal
    # 2026-09-17: identificador público de la Cuenta para el portal por Cuenta (/portal?cuenta=<slug>).
    # Se genera del nombre comercial al arrancar (seed.rellenar_slugs_cuentas) y al crear la Cuenta.
    slug: Mapped[str] = mapped_column(String(120), default="", index=True)
    razon_social: Mapped[str] = mapped_column(String(200), default="")
    logo: Mapped[str] = mapped_column(String(400), default="")  # ruta en disco
    contacto_nombre: Mapped[str] = mapped_column(String(150), default="")
    correo_comunicacion: Mapped[str] = mapped_column(String(200), default="")
    whatsapp_comunicacion: Mapped[str] = mapped_column(String(30), default="")
    # 2026-09-17 (WhatsApp multi-tenant): por defecto TODAS las Cuentas activas comparten el número
    # maestro de WhatsApp (el candidato ve las vacantes de todas y su postulación queda amarrada a la
    # Cuenta de la vacante que elija). `whatsapp_exclusivo=True` (Premium) reserva el número capturado
    # en `whatsapp_comunicacion` para ESTA Cuenta: los mensajes que lleguen a ese número solo ven sus
    # vacantes y sus personas (ruteo dedicado, comportamiento anterior).
    whatsapp_exclusivo: Mapped[bool] = mapped_column(Boolean, default=False)
    estado: Mapped[str] = mapped_column(String(20), default="Activa")  # Activa | Inactiva | Eliminada
    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)
    # Fase 2 (2026-09-15): baja lógica. Una Cuenta eliminada conserva TODO (vacantes, postulaciones,
    # bitácora) pero deja de aparecer en listados/selector y el webhook de WhatsApp no la usa.
    eliminada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    eliminada_por: Mapped[str] = mapped_column(String(150), default="")

    clientes: Mapped[List["Cliente"]] = relationship(back_populates="cuenta")
    usuarios: Mapped[List["UsuarioCuenta"]] = relationship(back_populates="cuenta")

    @property
    def nombre_visible(self) -> str:
        return self.nombre or self.nombre_comercial


class Cliente(Base):
    """Empresa para la que recluta una Cuenta."""

    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    # Punto 10: razón social y nombre comercial (el candidato ve nombre_comercial si existe).
    razon_social: Mapped[str] = mapped_column(String(200), default="")
    nombre_comercial: Mapped[str] = mapped_column(String(200), default="")
    estado: Mapped[str] = mapped_column(String(20), default="Activo")  # Activo | Inactivo
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    cuenta: Mapped["Cuenta"] = relationship(back_populates="clientes")

    @property
    def nombre_visible(self) -> str:
        return self.nombre_comercial or self.nombre
    contactos: Mapped[List["ClienteContacto"]] = relationship(
        back_populates="cliente", cascade="all, delete-orphan"
    )


class ClienteContacto(Base):
    """Persona de contacto en el Cliente — NO es un usuario del sistema."""

    __tablename__ = "cliente_contactos"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(150))
    apellidos: Mapped[str] = mapped_column(String(150), default="")  # Punto 10
    puesto: Mapped[str] = mapped_column(String(120), default="")
    correo: Mapped[str] = mapped_column(String(200), default="")
    telefono: Mapped[str] = mapped_column(String(30), default="")

    cliente: Mapped["Cliente"] = relationship(back_populates="contactos")


class Plantilla(Base):
    """Plantilla reutilizable de vacante (Fase B, punto 11) — General de la Cuenta
    (cliente_id=None) o de un Cliente específico. No hay nivel intermedio."""

    __tablename__ = "plantillas"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"), index=True)
    cliente_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clientes.id"), nullable=True, index=True)
    nombre: Mapped[str] = mapped_column(String(150))  # para identificarla en el selector
    # "eliminar" = desactivar, nunca borrado físico — una Vacante ya creada desde ella conserva
    # plantilla_id para trazabilidad aunque la plantilla ya no se ofrezca para nuevas vacantes.
    activa: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- contenido reutilizable: mismos campos/tipos que Vacante ---
    titulo: Mapped[str] = mapped_column(String(200), default="")
    area: Mapped[str] = mapped_column(String(100), default="")
    ubicacion: Mapped[str] = mapped_column(String(150), default="")  # Punto 11 ("condiciones")
    modalidad: Mapped[str] = mapped_column(String(30), default="Presencial")
    sueldo: Mapped[str] = mapped_column(String(80), default="A convenir")
    sueldo_desde: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Parte 3: igual que Vacante
    sueldo_hasta: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sueldo_moneda: Mapped[str] = mapped_column(String(5), default="MXN")
    sueldo_periodicidad: Mapped[str] = mapped_column(String(15), default="")
    requisitos: Mapped[str] = mapped_column(Text, default="")
    descripcion: Mapped[str] = mapped_column(Text, default="")
    resumen: Mapped[str] = mapped_column(Text, default="")
    perfil_ideal: Mapped[str] = mapped_column(Text, default="")
    responsabilidades: Mapped[list] = mapped_column(JSON, default=list)
    requisitos_deseables: Mapped[list] = mapped_column(JSON, default=list)
    beneficios: Mapped[list] = mapped_column(JSON, default=list)
    palabras_clave: Mapped[list] = mapped_column(JSON, default=list)
    seniority: Mapped[str] = mapped_column(String(40), default="")
    avisos_cumplimiento: Mapped[list] = mapped_column(JSON, default=list)
    preguntas_filtro: Mapped[list] = mapped_column(JSON, default=list)  # = "evaluaciones" (ver spec Fase B)
    preguntas_filtro_whatsapp: Mapped[list] = mapped_column(JSON, default=list)  # Fase 4
    ubicacion_estado: Mapped[str] = mapped_column(String(60), default="")  # Fase 4
    ubicacion_municipio: Mapped[str] = mapped_column(String(100), default="")
    texto_whatsapp: Mapped[str] = mapped_column(Text, default="")
    texto_bolsa: Mapped[str] = mapped_column(Text, default="")
    enfoque_entrevista: Mapped[str] = mapped_column(String(30), default="profesional")  # Fase 4

    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora, nullable=True)

    cuenta: Mapped["Cuenta"] = relationship()
    cliente: Mapped[Optional["Cliente"]] = relationship()


# Punto 11: contenido reutilizable que comparten Vacante y Plantilla (mismo nombre y tipo en
# ambos modelos). Es la única lista: crear vacante desde plantilla, guardar vacante como plantilla
# y duplicar plantilla copian exactamente estos campos.
CAMPOS_PLANTILLA = [
    "titulo", "area", "ubicacion", "modalidad", "sueldo", "requisitos", "descripcion", "resumen",
    "perfil_ideal", "responsabilidades", "requisitos_deseables", "beneficios", "palabras_clave",
    "seniority", "avisos_cumplimiento", "preguntas_filtro", "texto_whatsapp", "texto_bolsa",
    "enfoque_entrevista",
    "sueldo_desde", "sueldo_hasta", "sueldo_moneda", "sueldo_periodicidad",  # Parte 3
    "preguntas_filtro_whatsapp", "ubicacion_estado", "ubicacion_municipio",  # Fase 4
]


def texto_ubicacion(estado: str, municipio: str, libre: str = "") -> str:
    """Fase 4: `ubicacion` (texto que leen portal, WhatsApp, IA) se deriva de Estado/Municipio cuando
    se capturaron; si no, se conserva el texto libre (vacantes previas)."""
    estado, municipio = (estado or "").strip(), (municipio or "").strip()
    if municipio and estado:
        return municipio if municipio == estado else f"{municipio}, {estado}"
    if estado:
        return estado
    return (libre or "").strip()

# Parte 3 (2026-09-12): sueldo estructurado. "a_convenir" = sin montos.
PERIODICIDADES_SUELDO = ["semanal", "quincenal", "mensual", "anual", "a_convenir"]
NOMBRE_PERIODICIDAD = {"semanal": "semanales", "quincenal": "quincenales", "mensual": "mensuales", "anual": "anuales"}
MONEDAS_SUELDO = ["MXN", "USD"]


def texto_sueldo(desde: Optional[int], hasta: Optional[int], moneda: str = "MXN", periodicidad: str = "") -> str:
    """Texto DERIVADO del sueldo estructurado, para todo lo que muestra `Vacante.sueldo` (WhatsApp,
    prefiltro, entrevista, portal, publicaciones). Nunca inventa: sin montos → «A convenir»."""
    if periodicidad == "a_convenir" or (not desde and not hasta):
        return "A convenir"
    mon = (moneda or "MXN").upper()
    per = NOMBRE_PERIODICIDAD.get(periodicidad, "")
    cola = f" {mon}" + (f" {per}" if per else "")
    if desde and hasta and hasta != desde:
        return f"${desde:,} – ${hasta:,}{cola}"
    if desde and not hasta:
        return f"Desde ${desde:,}{cola}"
    monto = hasta if not desde else desde
    return f"${monto:,}{cola}"

# Fase 4 (Punto 6): enfoque de la Entrevista IA por vacante. Solo estos 2 niveles — nunca más.
ENFOQUES_ENTREVISTA = ["profesional", "profesional_personal"]


class UsuarioCuenta(Base):
    """Puente muchos-a-muchos: qué Cuenta(s) puede ver cada Usuario."""

    __tablename__ = "usuario_cuentas"
    __table_args__ = (UniqueConstraint("usuario_id", "cuenta_id", name="uq_usuario_cuenta"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"), index=True)

    usuario: Mapped["Usuario"] = relationship(back_populates="cuentas")
    cuenta: Mapped["Cuenta"] = relationship(back_populates="usuarios")


ROLES = ("Administrador", "Usuario")


class Usuario(Base):
    """Persona de RH que opera la plataforma.

    La LFPDPPP exige que detrás de cada decisión haya alguien identificable, así
    que la bitácora firma con el usuario de la sesión, nunca con lo que mande el
    cliente. La contraseña se guarda como scrypt con sal por usuario.
    """

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    correo: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(150))
    puesto: Mapped[str] = mapped_column(String(120), default="")
    # WhatsApp del usuario (Fase D, punto 23) — para notificarlo como Responsable o como
    # entrevistador interno sin volver a capturar el dato en ningún lado.
    telefono: Mapped[str] = mapped_column(String(30), default="")
    rol: Mapped[str] = mapped_column(String(20), default="Usuario")  # Administrador | Usuario
    # Evaluaciones (2026-09-28): ver el informe médico COMPLETO (dato sensible). Sin él, solo estado y dictamen.
    # El Administrador lo tiene siempre (`puede_ver_informe_medico`).
    acceso_informes_medicos: Mapped[bool] = mapped_column(Boolean, default=False)
    hash_pass: Mapped[str] = mapped_column(String(255))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    debe_cambiar_pass: Mapped[bool] = mapped_column(Boolean, default=False)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_hasta: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    ultimo_acceso: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    # Visibilidad automática (Fase A): si puede alternar Mío/Mi equipo, y a quién reporta.
    ve_equipo: Mapped[bool] = mapped_column(Boolean, default=False)
    reporta_a_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    # Fase 2 (2026-09-15): Cuenta con la que arranca la sesión cuando el usuario tiene varias
    # (POST /cuentas/{id}/predeterminada). Null = la primera vinculada, como siempre.
    cuenta_predeterminada_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cuentas.id"), nullable=True)

    sesiones: Mapped[List["Sesion"]] = relationship(back_populates="usuario", cascade="all, delete-orphan")
    cuentas: Mapped[List["UsuarioCuenta"]] = relationship(back_populates="usuario", cascade="all, delete-orphan")
    reporta_a: Mapped[Optional["Usuario"]] = relationship(remote_side=[id])

    @property
    def bloqueado(self) -> bool:
        if not self.bloqueado_hasta:
            return False
        limite = self.bloqueado_hasta
        if limite.tzinfo is None:
            limite = limite.replace(tzinfo=timezone.utc)
        return limite > ahora()

    def puede_ver_informe_medico(self) -> bool:
        return self.rol == "Administrador" or bool(self.acceso_informes_medicos)

    def puede_decidir(self) -> bool:
        """Ya no hay perfil de solo lectura (Fase A): Administrador y Usuario deciden por
        igual, la diferencia entre ellos es de alcance de visibilidad. Se deja el método
        para no tocar los call-sites existentes de `usuario_decisor`."""
        return True


class Sesion(Base):
    """Sesión en servidor: se puede revocar al instante (logout, baja de usuario)."""

    __tablename__ = "sesiones"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256 del token de la cookie
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    expira_en: Mapped[datetime] = mapped_column(FechaUTC(), index=True)
    ip: Mapped[str] = mapped_column(String(60), default="")
    agente: Mapped[str] = mapped_column(String(255), default="")
    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    usuario: Mapped[Usuario] = relationship(back_populates="sesiones")


# ============================================================
# Fase F — Agente global "Pregunta a Red Human" (punto 29)
# ============================================================


class UsoAgente(Base):
    """Contador de mensajes del agente por Usuario/día (límite diario, Fase F punto 29,
    decisión Q7) — NUNCA guarda el texto de la conversación (decisión Q6, es una decisión de
    privacidad aparte): solo cuántos mensajes mandó cada quien cada día."""

    __tablename__ = "uso_agente"
    __table_args__ = (UniqueConstraint("usuario_id", "fecha", name="uq_uso_agente_usuario_fecha"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    mensajes: Mapped[int] = mapped_column(Integer, default=0)


class Bitacora(Base):
    """Bitácora de auditoría append-only con cadena de hashes (LFPDPPP)."""

    __tablename__ = "bitacora"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actor: Mapped[str] = mapped_column(String(150))
    accion: Mapped[str] = mapped_column(String(80))
    entidad: Mapped[str] = mapped_column(String(40))
    entidad_id: Mapped[str] = mapped_column(String(40))
    detalle: Mapped[dict] = mapped_column(JSON, default=dict)
    hash_prev: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    # Cuenta (Fase A multi-cuenta) — informativa, fuera del payload que se hashea en registrar():
    # agregarla no rompe la cadena. Nullable: eventos de sistema (login fallido antes de resolver
    # usuario, semilla) pueden no tener una Cuenta a la que atribuirse.
    cuenta_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cuentas.id"), nullable=True, index=True)


# ------------------------------------------------------------
# Base de conocimiento con RAG (2026-09-18): documentos de la Cuenta (políticas, procesos, manuales) →
# fragmentos con embedding. Ver services/rag.py.
# ------------------------------------------------------------

TIPOS_CONOCIMIENTO = ["politica", "proceso", "manual", "reglamento", "faq", "otro"]

# Tablas del módulo (se crean en un paso aparte y NO fatal del arranque — ver main.lifespan y
# migraciones.crear_tablas_conocimiento). HOTFIX 2026-09-18: el primer despliegue tiró la API en producción
# al crear estas tablas («FOREIGN KEY(cuenta_id) REFERENCES cuentas (id)» → ProgrammingError). `cuenta_id`
# es ahora un entero indexado SIN restricción de llave foránea (el aislamiento por Cuenta lo garantiza el
# router con cuenta_actual, igual que en el resto del sistema), y un fallo al crearlas deja la Base de
# Conocimiento deshabilitada (503) sin afectar al resto de la plataforma.
TABLAS_CONOCIMIENTO = ("documentos_conocimiento", "fragmentos_conocimiento", "consultas_conocimiento")


class DocumentoConocimiento(Base):
    __tablename__ = "documentos_conocimiento"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)  # sin FK (hotfix 2026-09-18)
    titulo: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(20), default="politica")  # ver TIPOS_CONOCIMIENTO
    nombre_archivo: Mapped[str] = mapped_column(String(300), default="")
    ruta: Mapped[str] = mapped_column(String(400), default="")  # archivo original en uploads/ (vacío si fue texto pegado)
    texto: Mapped[str] = mapped_column(Text, default="")  # texto plano completo (fuente de los fragmentos)
    fragmentos_total: Mapped[int] = mapped_column(Integer, default=0)
    con_embeddings: Mapped[bool] = mapped_column(Boolean, default=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    # 2026-09-22 (permisos): la IA responde SOLO con documentos publicados; `areas`/`puestos` limitan
    # quién los ve, usando el área y el puesto de `colaboradores` (ver puede_ver_conocimiento).
    publicado: Mapped[bool] = mapped_column(Boolean, default=True)
    areas: Mapped[list] = mapped_column(JSON, default=list)    # [str] vacío = todas las áreas
    puestos: Mapped[list] = mapped_column(JSON, default=list)  # [str] vacío = todos los puestos
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    fragmentos: Mapped[List["FragmentoConocimiento"]] = relationship(back_populates="documento", cascade="all, delete-orphan")


class FragmentoConocimiento(Base):
    __tablename__ = "fragmentos_conocimiento"

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos_conocimiento.id"), index=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)  # sin FK (hotfix 2026-09-18)
    orden: Mapped[int] = mapped_column(Integer, default=0)
    texto: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # vector (text-embedding-3-small); None = solo léxico

    documento: Mapped["DocumentoConocimiento"] = relationship(back_populates="fragmentos")


class ConsultaConocimiento(Base):
    """Historial de preguntas (auditoría + mejora de la base): qué se preguntó, si hubo evidencia."""
    __tablename__ = "consultas_conocimiento"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)  # sin FK (hotfix 2026-09-18)
    usuario: Mapped[str] = mapped_column(String(150), default="")
    pregunta: Mapped[str] = mapped_column(Text)
    respuesta: Mapped[dict] = mapped_column(JSON, default=dict)
    sin_evidencia: Mapped[bool] = mapped_column(Boolean, default=False)
    modo: Mapped[str] = mapped_column(String(20), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)


def registrar(db: Session, actor: str, accion: str, entidad: str, entidad_id: str, detalle: Optional[dict] = None) -> Bitacora:
    """Escribe un evento en la bitácora encadenando el hash del evento anterior."""
    prev = db.query(Bitacora).order_by(Bitacora.id.desc()).first()
    hash_prev = prev.hash if prev else "GENESIS"
    ts = ahora()
    # 2026-09-22 (hotfix): `default=str` — un detalle con datetime/objeto raro (respuestas de proveedores,
    # excepciones) ya no truena la acción completa con un 500 al escribir la bitácora.
    detalle_seguro = json.loads(json.dumps(detalle or {}, default=str, ensure_ascii=False))
    payload = json.dumps(
        {"ts": ts.isoformat(), "actor": actor, "accion": accion, "entidad": entidad, "entidad_id": entidad_id, "detalle": detalle_seguro},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    h = hashlib.sha256((hash_prev + payload).encode("utf-8")).hexdigest()
    ev = Bitacora(ts=ts, actor=actor, accion=accion, entidad=entidad, entidad_id=entidad_id, detalle=detalle_seguro, hash_prev=hash_prev, hash=h)
    db.add(ev)
    return ev


class ConfiguracionSistema(Base):
    """Configuración global editable solo por admins. Fila única (id=1) — ver services/configuracion.py."""

    __tablename__ = "configuracion_sistema"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Modo Prueba: mientras esté activo, webhooks._buscar_o_crear_candidato deja de deduplicar
    # conversaciones frías (Candidato.es_prueba=True); nunca aparecen en listados/reportes de RH.
    modo_prueba: Mapped[bool] = mapped_column(Boolean, default=False)
    # Punto 13: minutos sin actividad tras los cuales, con Modo Prueba activo, el siguiente
    # mensaje del mismo WhatsApp arranca una postulación de prueba nueva (ver webhooks.py).
    modo_prueba_ventana_min: Mapped[int] = mapped_column(Integer, default=60)
    # Fase 3 (2026-09-15): recordatorios automáticos de documentos pendientes (services/recordatorios.py):
    # cada N días, a partir de esta hora (America/Mexico_City), mientras no pase Expediente.documentos_hasta.
    recordatorio_documentos_dias: Mapped[int] = mapped_column(Integer, default=2)
    # 2026-09-19: horas antes de la Entrevista Humana para el recordatorio automático (0 = apagado).
    recordatorio_entrevista_horas: Mapped[int] = mapped_column(Integer, default=24)
    recordatorio_documentos_hora: Mapped[int] = mapped_column(Integer, default=10)


# ============================================================
# Capacitación (Fase 1) — modelo, generación con IA, asignación.
# El avatar (Fase 2) queda pendiente: AsignacionCurso.token ya se genera con el mismo patrón
# que Entrevista.token, pero todavía no hay ninguna ruta pública que lo sirva.
# ============================================================


MODALIDADES_CURSO = ("instructor_ia", "autoguiado")


class Curso(Base):
    __tablename__ = "cursos"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    titulo: Mapped[str] = mapped_column(String(200))
    categoria: Mapped[str] = mapped_column(String(100), default="")
    duracion_horas: Mapped[float] = mapped_column(Float, default=0)
    # 2026-09-19 (Bloque 4): cómo se imparte y duración libre («5 min», «1 h 30»); duracion_horas se deriva para KPIs.
    modalidad: Mapped[str] = mapped_column(String(20), default="autoguiado")  # instructor_ia | autoguiado
    duracion_texto: Mapped[str] = mapped_column(String(40), default="")
    objetivo: Mapped[str] = mapped_column(Text, default="")  # generado por IA
    estado: Mapped[str] = mapped_column(String(20), default="Borrador")  # Borrador | Publicado | Archivado
    obligatorio: Mapped[bool] = mapped_column(Boolean, default=False)
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    # Cuenta (Fase A multi-cuenta) — a diferencia del resto, Curso no cuelga de ningún
    # Candidato/Vacante, así que necesita su propia columna en vez de resolverse por join.
    cuenta_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cuentas.id"), nullable=True, index=True)
    # Módulo universal (2026-09-16): lo que RH capturó para generar y la evaluación final INTEGRADA
    # (opción múltiple / verdadero-falso, calificada automáticamente). Un solo curso sirve para
    # colaboradores, candidatos y externos.
    contexto: Mapped[str] = mapped_column(Text, default="")
    adjuntos: Mapped[list] = mapped_column(JSON, default=list)  # [{nombre, ruta, caracteres}]
    evaluacion: Mapped[list] = mapped_column(JSON, default=list)  # [{pregunta, tipo, opciones, correcta, explicacion}]
    calificacion_minima: Mapped[int] = mapped_column(Integer, default=70)  # % para «Aprobado»

    modulos: Mapped[List["ModuloCurso"]] = relationship(
        back_populates="curso", order_by="ModuloCurso.orden", cascade="all, delete-orphan"
    )
    asignaciones: Mapped[List["AsignacionCurso"]] = relationship(back_populates="curso", cascade="all, delete-orphan")
    cuenta: Mapped[Optional["Cuenta"]] = relationship()


class ModuloCurso(Base):
    __tablename__ = "modulos_curso"

    id: Mapped[int] = mapped_column(primary_key=True)
    curso_id: Mapped[int] = mapped_column(ForeignKey("cursos.id"), index=True)
    orden: Mapped[int] = mapped_column(Integer)
    titulo: Mapped[str] = mapped_column(String(200))
    contenido: Mapped[str] = mapped_column(Text, default="")  # guion que explicará el avatar (Fase 2)
    # [{"pregunta": str, "criterio_respuesta_correcta": str}] — con qué evaluar la comprensión (Fase 2)
    # 2026-09-19 (Bloque 4): material de apoyo (PDF) — resumen breve y puntos clave; `contenido` es el guion
    # conversacional (Instructor IA) o el contenido modular (Autoguiado).
    resumen: Mapped[str] = mapped_column(Text, default="")
    puntos_clave: Mapped[list] = mapped_column(JSON, default=list)
    preguntas_verificacion: Mapped[list] = mapped_column(JSON, default=list)

    curso: Mapped["Curso"] = relationship(back_populates="modulos")


class AsignacionCurso(Base):
    __tablename__ = "asignaciones_curso"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    curso_id: Mapped[int] = mapped_column(ForeignKey("cursos.id"), index=True)
    # Asignación UNIVERSAL (2026-09-16): colaborador, candidato (postulación) o externo — mismo curso.
    tipo: Mapped[str] = mapped_column(String(20), default="colaborador")  # colaborador | candidato | externo
    colaborador_id: Mapped[Optional[int]] = mapped_column(ForeignKey("colaboradores.id"), nullable=True, index=True)
    postulacion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("postulaciones.id"), nullable=True, index=True)
    externo_nombre: Mapped[str] = mapped_column(String(200), default="")
    externo_correo: Mapped[str] = mapped_column(String(200), default="")
    externo_telefono: Mapped[str] = mapped_column(String(30), default="")
    externo_organizacion: Mapped[str] = mapped_column(String(200), default="")  # proveedor/cliente (opcional)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # liga pública
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")  # pendiente | en_curso | completado
    modulo_actual: Mapped[int] = mapped_column(Integer, default=0)  # módulos completados
    transcript: Mapped[list] = mapped_column(JSON, default=list)  # legado (sala con avatar)
    resultado_evaluacion: Mapped[dict] = mapped_column(JSON, default=dict)  # {respuestas:[...], aciertos, total, calificacion, aprobado}
    calificacion: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # %
    aprobado: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    asignado_por: Mapped[str] = mapped_column(String(150), default="")
    asignado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    iniciado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    completado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)

    curso: Mapped["Curso"] = relationship(back_populates="asignaciones")
    colaborador: Mapped[Optional["Colaborador"]] = relationship(overlaps="asignaciones_curso")
    postulacion: Mapped[Optional["Postulacion"]] = relationship()

    @property
    def viva(self) -> bool:
        """False si su colaborador fue eliminado (lógicamente) — nunca se lista ni se cuenta."""
        return not (self.colaborador is not None and self.colaborador.eliminado_en is not None)

    @property
    def nombre_persona(self) -> str:
        if self.tipo == "colaborador" and self.colaborador:
            return self.colaborador.nombre
        if self.tipo == "candidato" and self.postulacion:
            return self.postulacion.nombre
        return self.externo_nombre or ""

    @property
    def correo_persona(self) -> str:
        if self.tipo == "colaborador" and self.colaborador:
            return self.colaborador.correo or ""
        if self.tipo == "candidato" and self.postulacion:
            return self.postulacion.correo or ""
        return self.externo_correo or ""

    @property
    def telefono_persona(self) -> str:
        if self.tipo == "colaborador" and self.colaborador:
            return self.colaborador.telefono or ""
        if self.tipo == "candidato" and self.postulacion:
            return self.postulacion.telefono or ""
        return self.externo_telefono or ""


# ============================================================
# Fase D — Notificaciones configurables por evento/destinatario/canal (puntos 22-26)
# ============================================================

EVENTOS_NOTIFICACION = [
    "entrevista_agendada",
    "recordatorio_entrevista",
    "entrevista_modificada",
    "entrevista_cancelada",
    "candidato_apto",
    "entrevista_humana_terminada",
    "recomendacion_final",
    "contratacion",
    "solicitud_documentos",
    "recordatorio_documentos",
    # Fase 5 (2026-09-15): bienvenida + instrucciones de ingreso, automático al dar de alta (sin override).
    "instrucciones_ingreso",
    # 2026-09-19: al publicar una vacante, su descripción (HTML) al Cliente y al responsable.
    "vacante_publicada",
    # 2026-09-19: cierre del ciclo — el entrevistador registró su evaluación desde su liga.
    "entrevista_completada",
]

# Fase 7A (2026-09-12): valores con los que NACE la regla de cada evento cuando una Cuenta no la
# tiene todavía (siembra perezosa de GET /notificaciones/reglas y scripts/sembrar_reglas_notificacion.py).
# Decisión del usuario: al programar una Entrevista Humana la confirmación sale por correo Y WhatsApp
# a candidato y entrevistador cuando existan ambos datos; RH puede apagarlo por acción. Las reglas
# ya guardadas de una Cuenta NUNCA se tocan desde aquí.
REGLAS_NOTIFICACION_DEFAULT = {
    "entrevista_agendada": {"candidato_correo": True, "candidato_whatsapp": True, "entrevistador_correo": True, "entrevistador_whatsapp": True},
    "recordatorio_entrevista": {"candidato_whatsapp": True, "candidato_correo": True, "entrevistador_correo": True, "entrevistador_whatsapp": True},
    "entrevista_humana_terminada": {"entrevistador_correo": True, "entrevistador_whatsapp": True},
    "entrevista_completada": {"candidato_correo": True, "candidato_whatsapp": True, "cliente_correo": True},
    "vacante_publicada": {"cliente_correo": True},
    "contratacion": {"candidato_whatsapp": True},
    "solicitud_documentos": {"candidato_whatsapp": True},
    "recordatorio_documentos": {"candidato_whatsapp": True},
    "instrucciones_ingreso": {"candidato_correo": True, "candidato_whatsapp": True},
    # entrevista_modificada, entrevista_cancelada, recomendacion_final, candidato_apto: todo apagado.
}


class ReglaNotificacion(Base):
    """Configuración por Cuenta: para este evento, ¿a quién y por qué canal? Una fila por
    (cuenta_id, evento). El texto del mensaje sigue viviendo en código — esto solo decide
    destinatario × canal (puntos 24-25)."""

    __tablename__ = "reglas_notificacion"
    __table_args__ = (UniqueConstraint("cuenta_id", "evento", name="uq_regla_cuenta_evento"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"), index=True)
    evento: Mapped[str] = mapped_column(String(50), index=True)
    candidato_correo: Mapped[bool] = mapped_column(Boolean, default=False)
    candidato_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False)
    entrevistador_correo: Mapped[bool] = mapped_column(Boolean, default=False)
    entrevistador_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False)
    cliente_correo: Mapped[bool] = mapped_column(Boolean, default=False)
    cliente_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False)
    actualizada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)


class NotificacionEnviada(Base):
    """Bitácora OPERATIVA de envíos (distinta de `Bitacora`, la cadena de auditoría LFPDPPP) —
    para que RH pueda ver qué se mandó, a quién y si falló, sin bucear en logs del servidor."""

    __tablename__ = "notificaciones_enviadas"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"), index=True)
    candidato_id: Mapped[Optional[int]] = mapped_column(ForeignKey("candidatos.id"), nullable=True, index=True)
    evento: Mapped[str] = mapped_column(String(50), index=True)
    destinatario_tipo: Mapped[str] = mapped_column(String(20))  # candidato | entrevistador | cliente
    destino: Mapped[str] = mapped_column(String(200), default="")  # correo o teléfono real usado
    canal: Mapped[str] = mapped_column(String(20))  # correo | whatsapp
    enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    detalle: Mapped[str] = mapped_column(Text, default="")
    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)


class IntegracionTeams(Base):
    """Fase 7B — conexión de Microsoft 365 POR CUENTA (Configuración → Integraciones). Una fila por
    Cuenta. Los tokens se guardan CIFRADOS (services/teams.py: Fernet con clave derivada de
    TEAMS_CLIENT_SECRET); nunca se exponen por la API."""

    __tablename__ = "integraciones_teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"), unique=True, index=True)
    usuario_m365: Mapped[str] = mapped_column(String(200), default="")  # UPN del usuario que conectó
    nombre_m365: Mapped[str] = mapped_column(String(200), default="")
    access_token_cifrado: Mapped[str] = mapped_column(Text, default="")
    refresh_token_cifrado: Mapped[str] = mapped_column(Text, default="")
    expira_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    scopes: Mapped[str] = mapped_column(String(300), default="")
    conectado_por: Mapped[str] = mapped_column(String(150), default="")  # nombre de la persona de RH
    conectado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    ultimo_error: Mapped[str] = mapped_column(Text, default="")


# ============================================================
# Ciclo de vida del Colaborador — Desempeño · Clima · Conocimiento (andamiaje 2026-09-22)
# ============================================================
#
# REGLA DE ORO (inquebrantable): `colaboradores` es la BASE MAESTRA de las personas internas.
# Ningún módulo de aquí en adelante captura personas ni crea otra tabla de usuarios internos:
# todos apuntan a `Colaborador.id`. Si alguien no está en el roster, primero se da de alta
# (contratacion.alta) — nunca se "recaptura" dentro del módulo.
#
# Igual que las tablas de conocimiento (hotfix 2026-09-18), estas llevan `cuenta_id` como entero
# indexado SIN llave foránea a `cuentas` (el aislamiento lo da `cuenta_actual`) y se crean en el paso
# NO fatal del arranque: si el motor de producción las rechaza, el resto de la plataforma arranca
# igual y estos módulos responden 503 con el motivo.

TABLAS_MODULOS_RH = (
    "ciclos_desempeno", "evaluaciones_desempeno", "mediciones_clima", "respuestas_clima",
    "participaciones_clima", "plantillas_clima",  # Clima v2 (2026-09-27)
    "plantillas_desempeno", "acciones_desempeno",  # Desempeño v2 (2026-09-27)
    "plantillas_onboarding", "tareas_onboarding",  # Onboarding v2 (2026-09-28)
    "pruebas_psicometricas", "evaluaciones_candidato",  # Evaluaciones y verificaciones (2026-09-28)
    "firmas_documentos",  # Dropbox Sign (2026-09-29)
    "evaluaciones", "eventos_evaluacion",  # Evaluaciones unificadas — Fase 1 (2026-09-29)
)

# --- Desempeño ---
# Desempeño v2 (2026-09-27). Evaluación general: Borrador → En curso → Cerrada (flujo de ida).
# Por persona: Pendiente → En proceso → Completada. Valores viejos («cerrado», «en_curso» de persona) se
# leen con `normalizar_estado_ciclo` / `normalizar_estado_persona` (sin migrar datos).
ESTADOS_CICLO_DESEMPENO = ("borrador", "en_curso", "cerrada")
TRANSICIONES_CICLO_DESEMPENO = {"borrador": ("en_curso",), "en_curso": ("cerrada",), "cerrada": ()}
ESTADOS_EVALUACION_DESEMPENO = ("pendiente", "en_proceso", "completada")
TIPOS_CRITERIO_DESEMPENO = ("medible", "descriptivo")
SENTIDOS_INDICADOR = ("mayor_es_mejor", "menor_es_mejor")
# Escala por defecto de un criterio descriptivo (1-5 con significado). Cumplimiento = (valor-1)/4 → 1=0 %, 5=100 %.
ESCALA_DESCRIPTIVA_DEFAULT = [
    {"valor": 1, "significado": "No cumple lo esperado"},
    {"valor": 2, "significado": "Cumple parcialmente"},
    {"valor": 3, "significado": "Cumple lo esperado"},
    {"valor": 4, "significado": "Supera lo esperado"},
    {"valor": 5, "significado": "Es referente para el equipo"},
]


def normalizar_estado_ciclo(estado: str) -> str:
    return "cerrada" if estado in ("cerrado", "cerrada") else (estado or "borrador")


def normalizar_estado_persona(estado: str) -> str:
    return "en_proceso" if estado in ("en_curso", "en_proceso") else (estado or "pendiente")


class CicloDesempeno(Base):
    """Evaluación de desempeño de un periodo: los objetivos y KPIs que se van a evaluar (capturados por
    RH o propuestos por la IA y SIEMPRE editables). Cada colaborador evaluado cuelga de aquí."""

    __tablename__ = "ciclos_desempeno"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # DES-####
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)  # sin FK (ver nota arriba)
    nombre: Mapped[str] = mapped_column(String(200))
    periodo: Mapped[str] = mapped_column(String(60), default="")  # «2026-S2», «Q3 2026», «Anual 2026»
    descripcion: Mapped[str] = mapped_column(Text, default="")
    puesto_objetivo: Mapped[str] = mapped_column(String(200), default="")  # contexto para la IA (no filtra)
    objetivos: Mapped[list] = mapped_column(JSON, default=list)  # [{titulo, descripcion, peso}]
    kpis: Mapped[list] = mapped_column(JSON, default=list)       # [{nombre, descripcion, unidad, meta, peso}]
    # Desempeño v2: criterios unificados (reemplazan a objetivos/kpis, que quedan como LEGADO de solo
    # lectura: `services.desempeno_calculo.criterios_de` los convierte al leer). Ver ese módulo.
    criterios: Mapped[list] = mapped_column(JSON, default=list)
    equipo: Mapped[str] = mapped_column(String(200), default="")  # puesto/equipo que se evalúa (contexto de la IA)
    origen_criterios: Mapped[str] = mapped_column(String(20), default="")  # ia | plantilla | manual
    pesos_personalizados: Mapped[bool] = mapped_column(Boolean, default=False)  # False = todos pesan igual
    plantilla_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # trazabilidad, sin FK
    duplicado_de: Mapped[str] = mapped_column(String(20), default="")  # DES-#### de origen al duplicar
    # cambios a criterios/metas DESPUÉS de iniciar: [{fecha, usuario, criterio_id, campo, anterior, nuevo, motivo}]
    historial_cambios: Mapped[list] = mapped_column(JSON, default=list)
    iniciado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    cerrado_por: Mapped[str] = mapped_column(String(150), default="")
    escala_maxima: Mapped[int] = mapped_column(Integer, default=100)  # calificación 0-100 por defecto
    generado_con_ia: Mapped[bool] = mapped_column(Boolean, default=False)
    estado: Mapped[str] = mapped_column(String(20), default="borrador")  # ver ESTADOS_CICLO_DESEMPENO
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    cerrado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)

    evaluaciones: Mapped[List["EvaluacionDesempeno"]] = relationship(back_populates="ciclo", cascade="all, delete-orphan")


class EvaluacionDesempeno(Base):
    """Evaluación de UN colaborador dentro de un ciclo. La persona SIEMPRE es un `Colaborador` que ya
    existe (regla de oro): aquí solo viven sus resultados, su calificación y sus brechas."""

    __tablename__ = "evaluaciones_desempeno"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # EVD-####
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    ciclo_id: Mapped[int] = mapped_column(ForeignKey("ciclos_desempeno.id"), index=True)
    colaborador_id: Mapped[int] = mapped_column(ForeignKey("colaboradores.id"), index=True)
    evaluador: Mapped[str] = mapped_column(String(150), default="")  # nombre a mostrar del evaluador (HITL)
    # Desempeño v2: el evaluador es un Usuario del sistema (puede entrar y guardar borradores). Si el
    # colaborador tiene jefe en el roster y ese jefe tiene usuario (mismo correo), se propone solo.
    evaluador_usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    # Ajustes INDIVIDUALES a criterios/metas de esta persona: {criterio_id: {campo: valor, ..., "motivo": str}}
    ajustes: Mapped[dict] = mapped_column(JSON, default=dict)
    conclusion: Mapped[str] = mapped_column(Text, default="")  # obligatoria para completar (Fase 5)
    resumen: Mapped[str] = mapped_column(Text, default="")
    fortalezas: Mapped[list] = mapped_column(JSON, default=list)  # confirmadas por el evaluador (nunca automáticas)
    propuesta_ia: Mapped[dict] = mapped_column(JSON, default=dict)  # última propuesta de la IA (editable)
    notas: Mapped[list] = mapped_column(JSON, default=list)  # [{id, fecha, texto, criterio_id?, autor}]
    historial_cambios: Mapped[list] = mapped_column(JSON, default=list)
    completada_por: Mapped[str] = mapped_column(String(150), default="")
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")  # ver ESTADOS_EVALUACION_DESEMPENO
    # v2: [{criterio_id, real, valoracion, no_aplica, motivo_no_aplica, comentario}]; LEGADO: [{tipo, nombre, logro, …}]
    resultados: Mapped[list] = mapped_column(JSON, default=list)
    calificacion: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0-escala_maxima
    brechas: Mapped[list] = mapped_column(JSON, default=list)  # [{tema, brecha, accion_sugerida}] → plan de capacitación
    comentarios: Mapped[str] = mapped_column(Text, default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    completada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)

    ciclo: Mapped["CicloDesempeno"] = relationship(back_populates="evaluaciones")
    colaborador: Mapped["Colaborador"] = relationship()

    __table_args__ = (UniqueConstraint("ciclo_id", "colaborador_id", name="uq_evaluacion_ciclo_colaborador"),)


ESTADOS_ACCION_DESEMPENO = ("abierta", "en_proceso", "completada", "cancelada")


class AccionDesempeno(Base):
    """Acción que nace de una BRECHA CONFIRMADA por el evaluador (Desempeño v2, 2026-09-27): responsable,
    fecha compromiso y estado. Si es un curso, se crea la asignación en Capacitación (misma tabla que usa
    ese módulo) y el estado de la acción sigue al de la asignación."""

    __tablename__ = "acciones_desempeno"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    evaluacion_id: Mapped[int] = mapped_column(ForeignKey("evaluaciones_desempeno.id"), index=True)
    colaborador_id: Mapped[int] = mapped_column(ForeignKey("colaboradores.id"), index=True)
    brecha_id: Mapped[str] = mapped_column(String(20), default="")
    brecha: Mapped[str] = mapped_column(String(200), default="")  # tema de la brecha (copia legible)
    tipo: Mapped[str] = mapped_column(String(20), default="accion")  # accion | curso
    descripcion: Mapped[str] = mapped_column(Text, default="")
    responsable: Mapped[str] = mapped_column(String(150), default="")
    fecha_compromiso: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="abierta")  # ver ESTADOS_ACCION_DESEMPENO
    curso_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    asignacion_curso_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)

    evaluacion: Mapped["EvaluacionDesempeno"] = relationship()


class PlantillaDesempeno(Base):
    """Plantilla de Desempeño v2 (2026-09-27): criterios con sus definiciones, forma de evaluar (tipo,
    meta/sentido o escala) y pesos, para reutilizar. Usarla COPIA los criterios a la evaluación: editar la
    plantilla nunca modifica evaluaciones ya creadas, iniciadas o cerradas (cada una conserva su versión)."""

    __tablename__ = "plantillas_desempeno"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[str] = mapped_column(Text, default="")
    equipo: Mapped[str] = mapped_column(String(200), default="")
    criterios: Mapped[list] = mapped_column(JSON, default=list)
    pesos_personalizados: Mapped[bool] = mapped_column(Boolean, default=False)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)  # «eliminar» = desactivar
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)


# --- Clima ---
# Clima v2 (2026-09-27): flujo ESTRICTO de ida borrador → abierta → cerrada (nunca se reabre; mientras
# está abierta solo se puede mover la fecha de cierre). En la interfaz: Borrador / Abierta / Cerrada.
ESTADOS_MEDICION_CLIMA = ("borrador", "abierta", "cerrada")
TRANSICIONES_CLIMA = {"borrador": ("abierta",), "abierta": ("cerrada",), "cerrada": ()}
TIPOS_PREGUNTA_CLIMA = ("escala", "opcion", "abierta")  # escala = 1 a 5 (favorable = 4 o 5)
ESCALA_CLIMA = 5
DIMENSION_CLIMA_DEFAULT = "General"


class MedicionClima(Base):
    """Medición de clima laboral: un cuestionario que se abre a los colaboradores y, si se quiere, a una
    liga pública externa. `anonima=True` (default) significa que NUNCA se guarda quién respondió."""

    __tablename__ = "mediciones_clima"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # CLI-####
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    titulo: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[str] = mapped_column(Text, default="")
    # [{id, texto, tipo, dimension, orden, opciones, escala_max}] ordenadas por `orden` (Clima v2)
    preguntas: Mapped[list] = mapped_column(JSON, default=list)
    dimensiones: Mapped[list] = mapped_column(JSON, default=list)  # nombres en orden de presentación
    anonima: Mapped[bool] = mapped_column(Boolean, default=True)
    estado: Mapped[str] = mapped_column(String(20), default="borrador")  # ver ESTADOS_MEDICION_CLIMA
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # liga pública /clima/{token}
    permite_externos: Mapped[bool] = mapped_column(Boolean, default=False)
    abierta_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    cierra_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    cerrada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    cerrada_por: Mapped[str] = mapped_column(String(150), default="")  # nombre de RH o «sistema» (cron)
    plantilla_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # trazabilidad, sin FK
    # Destinatarios elegidos al abrir: {areas: [...], sedes: [...]} (solo para mostrar qué filtro se usó).
    filtros_envio: Mapped[dict] = mapped_column(JSON, default=dict)
    # Análisis con IA a demanda (botón «Analizar resultados con Red Human»), el más reciente al final.
    analisis: Mapped[list] = mapped_column(JSON, default=list)
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    respuestas: Mapped[List["RespuestaClima"]] = relationship(back_populates="medicion", cascade="all, delete-orphan")
    participaciones: Mapped[List["ParticipacionClima"]] = relationship(back_populates="medicion", cascade="all, delete-orphan")


class RespuestaClima(Base):
    """Una respuesta al cuestionario. Si la medición es ANÓNIMA, `colaborador_id` queda NULL a propósito
    (no se puede reconstruir quién contestó); si es identificada, apunta al Colaborador del roster."""

    __tablename__ = "respuestas_clima"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    medicion_id: Mapped[int] = mapped_column(ForeignKey("mediciones_clima.id"), index=True)
    colaborador_id: Mapped[Optional[int]] = mapped_column(ForeignKey("colaboradores.id"), nullable=True, index=True)
    # participante externo por liga pública: se guarda el dato de contacto, NUNCA se crea una persona
    externo_nombre: Mapped[str] = mapped_column(String(200), default="")
    externo_correo: Mapped[str] = mapped_column(String(200), default="")
    origen: Mapped[str] = mapped_column(String(20), default="colaborador")  # colaborador | externo
    # Clima v2: una respuesta de PRUEBA («Probar encuesta») nunca se mezcla con las reales; una EXTERNA
    # (liga compartida) se reporta aparte y no suma a la participación.
    es_prueba: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    es_externa: Mapped[bool] = mapped_column(Boolean, default=False)
    respuestas: Mapped[dict] = mapped_column(JSON, default=dict)  # {pregunta_id: valor}
    # En mediciones ANÓNIMAS solo se guarda el DÍA (00:00 UTC): con la hora exacta se podría cruzar
    # contra la participación. Identificadas: hora exacta.
    enviado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)

    medicion: Mapped["MedicionClima"] = relationship(back_populates="respuestas")
    colaborador: Mapped[Optional["Colaborador"]] = relationship()


class ParticipacionClima(Base):
    """Control de participación de una medición (Clima v2), SEPARADO de las respuestas: quién fue invitado,
    su liga personal y si ya respondió (para recordatorios y para no aceptar duplicados).

    Anonimato estricto: no hay ninguna llave hacia `respuestas_clima` y NO se guarda cuándo respondió
    (solo `respondio`). La fila nace al invitar, así que su orden de inserción tampoco delata el orden de
    las respuestas. Solo los invitados cuentan para la participación."""

    __tablename__ = "participaciones_clima"
    __table_args__ = (UniqueConstraint("medicion_id", "colaborador_id", name="uq_participacion_clima"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    medicion_id: Mapped[int] = mapped_column(ForeignKey("mediciones_clima.id"), index=True)
    colaborador_id: Mapped[int] = mapped_column(ForeignKey("colaboradores.id"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # liga personal /clima/{token}
    respondio: Mapped[bool] = mapped_column(Boolean, default=False)
    invitado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    invitado_por: Mapped[str] = mapped_column(String(150), default="")
    recordatorios_enviados: Mapped[int] = mapped_column(Integer, default=0)
    ultimo_recordatorio_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)

    medicion: Mapped["MedicionClima"] = relationship(back_populates="participaciones")
    colaborador: Mapped["Colaborador"] = relationship()


class PlantillaClima(Base):
    """Plantilla reutilizable de encuesta de clima (Configuración → Plantillas de clima). Usarla COPIA sus
    dimensiones y preguntas a la medición nueva: editar la medición nunca altera la plantilla."""

    __tablename__ = "plantillas_clima"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[str] = mapped_column(Text, default="")
    dimensiones: Mapped[list] = mapped_column(JSON, default=list)
    preguntas: Mapped[list] = mapped_column(JSON, default=list)  # mismo formato que MedicionClima.preguntas
    activa: Mapped[bool] = mapped_column(Boolean, default=True)  # «eliminar» = desactivar
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)


def puede_ver_conocimiento(doc: "DocumentoConocimiento", colaborador: Optional["Colaborador"]) -> bool:
    """Permisos de la Base de Conocimiento (2026-09-22) resueltos con la BASE MAESTRA: un documento
    PUBLICADO se ve si no restringe áreas/puestos, o si el área (`Colaborador.area`) o el puesto de la
    persona están en la lista. La sesión de RH (sin colaborador) ve todo. Un documento NO publicado solo
    lo ve RH y NUNCA alimenta las respuestas de la IA."""
    if not doc.activo:
        return False
    if colaborador is None:
        return True  # sesión de RH en el dashboard
    if not doc.publicado:
        return False
    areas = [str(a).strip().lower() for a in (doc.areas or []) if str(a).strip()]
    puestos = [str(p).strip().lower() for p in (doc.puestos or []) if str(p).strip()]
    if not areas and not puestos:
        return True
    area_col = (colaborador.area or "").strip().lower()
    puesto_col = (colaborador.puesto or "").strip().lower()
    return bool((area_col and area_col in areas) or (puesto_col and puesto_col in puestos))


# --- Onboarding v2 (2026-09-28) ---
# Plantillas de Onboarding (Configuración): qué documentos se piden, qué recursos internos se preparan
# (correo, equipo, accesos), quién es responsable por defecto, el curso de inducción y los plazos RELATIVOS a
# la fecha de ingreso. Jerarquía: la plantilla de PUESTO prevalece sobre la de EMPRESA; sin ninguna aplica la
# configuración predeterminada (DOCUMENTOS_BASE + las tres tareas fijas). Aplicarla a una persona COPIA la
# configuración: cambiar la selección de un candidato nunca altera la plantilla, ni al revés.
ALCANCES_PLANTILLA_ONBOARDING = ("empresa", "puesto")
TIPOS_RECURSO_ONBOARDING = ("correo", "equipo", "accesos", "otro")
ESTADOS_TAREA_ONBOARDING = ("pendiente", "realizada", "cancelada")
# Tareas FIJAS y obligatorias de todo Onboarding (clave, nombre). No se eliminan ni se cancelan una por una.
TAREAS_FIJAS_ONBOARDING = (
    ("contrato_firmado", "Contrato firmado"),
    ("alta_imss_nomina", "Alta IMSS / nómina"),
    ("confirmar_ingreso", "Confirmar ingreso"),
)
# Plazos predeterminados en días respecto a la fecha de ingreso (negativo = antes del ingreso).
PLAZOS_ONBOARDING_DEFAULT = {"documentos": -3, "contrato_firmado": -1, "alta_imss_nomina": 0, "confirmar_ingreso": 0}


class PlantillaOnboarding(Base):
    __tablename__ = "plantillas_onboarding"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    alcance: Mapped[str] = mapped_column(String(20), default="empresa")  # ALCANCES_PLANTILLA_ONBOARDING
    # Razón social contratante (una de `cuentas.razones_sociales_de`); vacío = cualquiera de la Cuenta.
    empresa: Mapped[str] = mapped_column(String(200), default="")
    puesto: Mapped[str] = mapped_column(String(200), default="")  # solo alcance «puesto»
    documentos: Mapped[list] = mapped_column(JSON, default=list)  # [{tipo, obligatorio}]
    recursos: Mapped[list] = mapped_column(JSON, default=list)  # [{nombre, tipo, responsable, dias}]
    responsables: Mapped[dict] = mapped_column(JSON, default=dict)  # {documentos, contrato_firmado, alta_imss_nomina, confirmar_ingreso}
    plazos: Mapped[dict] = mapped_column(JSON, default=dict)  # días relativos a la fecha de ingreso
    curso_induccion_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)  # «eliminar» = desactivar
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)


class TareaOnboarding(Base):
    """Tarea del Onboarding de UNA persona (expediente). Estados: pendiente → realizada | cancelada (con
    motivo). Las tres fijas (`TAREAS_FIJAS_ONBOARDING`) nacen siempre y no se cancelan una por una."""

    __tablename__ = "tareas_onboarding"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    expediente_id: Mapped[int] = mapped_column(Integer, index=True)
    clave: Mapped[str] = mapped_column(String(40), default="")  # contrato_firmado | alta_imss_nomina | confirmar_ingreso | recurso | otra
    nombre: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(20), default="otro")  # fija | correo | equipo | accesos | otro
    fija: Mapped[bool] = mapped_column(Boolean, default=False)
    obligatoria: Mapped[bool] = mapped_column(Boolean, default=True)
    responsable: Mapped[str] = mapped_column(String(150), default="")
    dias_relativos: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # respecto a la fecha de ingreso
    fecha_limite: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")  # ESTADOS_TAREA_ONBOARDING
    motivo_cancelacion: Mapped[str] = mapped_column(Text, default="")
    notas: Mapped[str] = mapped_column(Text, default="")
    realizada_por: Mapped[str] = mapped_column(String(150), default="")
    realizada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    cancelada_por: Mapped[str] = mapped_column(String(150), default="")
    cancelada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    creada_por: Mapped[str] = mapped_column(String(150), default="")
    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)


# --- Evaluaciones y verificaciones del candidato (2026-09-28) ---
# Psicométrica, técnica/caso práctico, referencias, médico, socioeconómico u otra. Se agregan desde la ficha y
# NUNCA mueven la columna del pipeline. Sin conexiones a proveedores todavía: el modo «Integrada» se simula a mano
# (Asignada → Enviada → Iniciada → Completada → Resultado recibido). HITL: la IA no revisa ni dictamina nada.
TIPOS_EVALUACION = {
    "psicometrica": "Psicométrica",
    "tecnica": "Técnica o caso práctico",
    "referencias": "Referencias",
    "medico": "Médico",
    "socioeconomico": "Socioeconómico",
    "otra": "Otra",
}
MODOS_PRUEBA = {"integrada": "Integrada", "enlace": "Enlace externo", "manual": "Carga manual"}
# Seguimiento (lo que ve RH). «fallida» = Fallida/Cancelada, siempre con motivo.
ESTADOS_EVALUACION = {
    "en_espera_consentimiento": "En espera de consentimiento",
    "pendiente": "Pendiente",
    "en_proceso": "En proceso",
    "resultado_recibido": "Resultado recibido",
    "revisada": "Revisada",
    "fallida": "Fallida/Cancelada",
}
# Modo Integrada (simulado hasta conectar proveedores): cada paso y su estado de seguimiento.
PASOS_INTEGRADA = ("asignada", "enviada", "iniciada", "completada", "resultado_recibido")
ESTADO_POR_PASO = {"asignada": "pendiente", "enviada": "en_proceso", "iniciada": "en_proceso", "completada": "en_proceso", "resultado_recibido": "resultado_recibido"}
DICTAMENES_GENERALES = {"favorable": "Favorable", "con_observaciones": "Con observaciones", "desfavorable": "Desfavorable"}
DICTAMENES_MEDICOS = {"apto": "Apto", "apto_con_restricciones": "Apto con restricciones", "no_apto": "No apto"}
# Texto del consentimiento EXPRESO y POR ESCRITO (medio electrónico) para el estudio médico — LFPDPPP: los datos de
# salud son sensibles. Se guarda la copia EXACTA que la persona aceptó.
TEXTO_CONSENTIMIENTO_MEDICO = (
    "Yo, {nombre}, otorgo mi consentimiento expreso y por escrito, por medio electrónico, para que {empresa} "
    "realice o solicite un estudio médico relacionado con el puesto de {puesto}. Entiendo que mis datos de salud "
    "son datos personales sensibles conforme a la Ley Federal de Protección de Datos Personales en Posesión de los "
    "Particulares; que solo se usarán para evaluar mi aptitud para el puesto; que el informe completo solo lo podrán "
    "consultar las personas autorizadas y que el resto del equipo verá únicamente el dictamen (Apto, Apto con "
    "restricciones o No apto). Sé que puedo revocar este consentimiento y ejercer mis derechos ARCO en cualquier momento."
)


class PruebaPsicometrica(Base):
    """Catálogo de Configuración → Pruebas psicométricas (por Cuenta). «Eliminar» = inactivar."""

    __tablename__ = "pruebas_psicometricas"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    clave: Mapped[str] = mapped_column(String(60))  # identificador interno (único por Cuenta)
    nombre: Mapped[str] = mapped_column(String(200))  # nombre visible
    descripcion: Mapped[str] = mapped_column(Text, default="")
    puestos: Mapped[list] = mapped_column(JSON, default=list)  # puestos sugeridos
    modo: Mapped[str] = mapped_column(String(20), default="manual")  # MODOS_PRUEBA
    proveedor: Mapped[str] = mapped_column(String(150), default="")
    id_proveedor: Mapped[str] = mapped_column(String(150), default="")  # identificador en el proveedor
    url: Mapped[str] = mapped_column(String(500), default="")  # modo «Enlace externo»
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)


class EvaluacionCandidato(Base):
    """Una evaluación o verificación asignada a una POSTULACIÓN. Nunca escribe `Postulacion.etapa`."""

    __tablename__ = "evaluaciones_candidato"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), index=True)  # EVA-####
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    postulacion_id: Mapped[int] = mapped_column(Integer, index=True)
    tipo: Mapped[str] = mapped_column(String(20))  # TIPOS_EVALUACION
    nombre: Mapped[str] = mapped_column(String(200))
    prueba_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # catálogo psicométrico
    modo: Mapped[str] = mapped_column(String(20), default="manual")  # MODOS_PRUEBA
    proveedor: Mapped[str] = mapped_column(String(150), default="")
    id_proveedor: Mapped[str] = mapped_column(String(150), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    # Psicométricas.mx (2026-09-29): «clave» del candidato en el proveedor (la usa su webhook) y el resultado crudo.
    clave_proveedor: Mapped[str] = mapped_column(String(60), default="", index=True)
    resultado_json: Mapped[dict] = mapped_column(JSON, default=dict)
    estado: Mapped[str] = mapped_column(String(30), default="pendiente")  # ESTADOS_EVALUACION
    paso_integrada: Mapped[str] = mapped_column(String(20), default="")  # PASOS_INTEGRADA (solo modo integrada)
    motivo_fallida: Mapped[str] = mapped_column(Text, default="")
    notas: Mapped[str] = mapped_column(Text, default="")
    # --- consentimiento ---
    requiere_consentimiento_expreso: Mapped[bool] = mapped_column(Boolean, default=False)  # estudio médico
    consentimiento_token: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    consentimiento_texto: Mapped[str] = mapped_column(Text, default="")  # copia exacta aceptada
    consentimiento_aceptado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    consentimiento_evidencia: Mapped[dict] = mapped_column(JSON, default=dict)  # nombre tecleado, IP, navegador, huella
    # --- resultado / informe ---
    archivo: Mapped[str] = mapped_column(String(300), default="")
    nombre_archivo: Mapped[str] = mapped_column(String(255), default="")
    mime: Mapped[str] = mapped_column(String(80), default="")
    resultado_resumen: Mapped[str] = mapped_column(Text, default="")
    resultado_cargado_por: Mapped[str] = mapped_column(String(150), default="")
    resultado_cargado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    dictamen: Mapped[str] = mapped_column(String(30), default="")  # DICTAMENES_GENERALES | DICTAMENES_MEDICOS
    comentario_revision: Mapped[str] = mapped_column(Text, default="")
    revisada_por: Mapped[str] = mapped_column(String(150), default="")
    revisada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    historial: Mapped[list] = mapped_column(JSON, default=list)  # [{fecha, usuario, de, a, detalle}]
    asignada_por: Mapped[str] = mapped_column(String(150), default="")
    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)

    @property
    def es_medico(self) -> bool:
        return self.tipo == "medico"


# ============================================================
# Evaluaciones unificadas — Fase 1 (2026-09-29, especificación de Raúl)
# ============================================================
# Toda evaluación del candidato (entrevista humana, médica, psicométrica, …) se crea, se sigue y se cierra con
# el MISMO objeto. Sustituye a `EntrevistaHumana` y `EvaluacionCandidato` (que se conservan intactas como origen
# de `scripts/migrar_evaluaciones_unificadas.py`). La Entrevista Red Human (IA) queda FUERA: sigue en
# `entrevistas` con su sala y su etapa, y solo se muestra como insumo de solo lectura.
# Reglas: una segunda entrevista/estudio = registro NUEVO; nadie sobrescribe a nadie (todo va a
# `eventos_evaluacion`); recibir o guardar un resultado NUNCA escribe `Postulacion.etapa`.
TIPOS_EVALUACION_U = {
    "entrevista_humana": "Entrevista humana",
    "medica": "Médica",
    "psicometrica": "Psicométrica",
    "socioeconomica": "Socioeconómica",
    "tecnica": "Técnica o caso práctico",
    "referencias": "Referencias",
    "otra": "Otra",
}
TIPO_DESDE_LEGADO = {"medico": "medica", "socioeconomico": "socioeconomica"}  # el resto conserva su clave
# «¿Cómo se realizará?» — sustituye al selector «Modo».
FORMAS_EVALUACION = {
    "asignada": "Asignar a una persona",
    "registro_directo": "Registrar resultado ahora",
    "liga_otro_sistema": "Enviar liga de otro sistema",
    "integrada": "Usar proveedor integrado",
}
FORMA_DESDE_MODO = {"manual": "registro_directo", "enlace": "liga_otro_sistema", "integrada": "integrada"}
EVALUADORES_TIPO = ("interno", "externo")
# Solo estos cinco estados. El consentimiento NO es un estado (ver CONSENTIMIENTOS).
ESTADOS_EVALUACION_U = {
    "pendiente": "Pendiente",
    "realizada_sin_resultado": "Realizada · Resultado pendiente",
    "con_resultado": "Con resultado",
    "no_realizada": "No realizada",
    "cancelada": "Cancelada",
}
# Transiciones por acción de seguimiento. Además: registrar un resultado desde cualquier estado distinto de
# Cancelada deja «Con resultado» (`puede_registrar_resultado`); «Con resultado» se complementa/corrige sin
# cambiar de estado.
TRANSICIONES_EVALUACION = {
    "pendiente": ("realizada_sin_resultado", "con_resultado", "no_realizada", "cancelada"),
    "realizada_sin_resultado": ("con_resultado", "cancelada"),
    "con_resultado": (),
    "no_realizada": ("pendiente", "cancelada"),  # pendiente = reprogramar
    "cancelada": (),
}
# Condición aparte del estado. Etiqueta en la tarjeta: solo pendiente y rechazado muestran algo.
CONSENTIMIENTOS = {"no_requerido": "", "pendiente": "En espera de consentimiento", "otorgado": "", "rechazado": "Consentimiento rechazado"}
TIPOS_CON_CONSENTIMIENTO = ("medica",)
MODALIDADES_CITA = ("Presencial", "Videollamada", "Teléfono")
MODALIDAD_DESDE_LEGADO = {"Llamada": "Teléfono"}
# Conclusión: obligatoria SOLO en entrevista humana; en los demás tipos es opcional y basta un comentario o un
# adjunto. La médica conserva el estándar clínico (Apto / Apto con restricciones / No apto).
CONCLUSIONES_ENTREVISTA = {"avanzar": "Avanzar", "no_avanzar": "No avanzar", "requiere_otra_entrevista": "Requiere otra entrevista"}
CONCLUSIONES_MEDICAS = DICTAMENES_MEDICOS
CONCLUSIONES_GENERALES = DICTAMENES_GENERALES
REALIZADA_POR_NO_ESPECIFICADO = "No especificado"
# Por dónde entró cada cosa (eventos y `registrada_via`).
CANALES_EVALUACION = ("sistema", "liga_evaluador", "liga_candidato", "proveedor", "migracion")
ACCIONES_EVENTO_EVALUACION = (
    "creada", "modificada", "reprogramada", "envio", "recordatorio", "cambio_estado", "consentimiento",
    "resultado_registrado", "resultado_corregido", "resultado_complementado", "adjunto_agregado", "migrada",
)
BASE_CODIGO_EVALUACION = 50000  # códigos nuevos EVA-5xxxx; los EVA-7xxx migrados conservan el suyo


def conclusiones_de(tipo: str) -> dict:
    if tipo == "entrevista_humana":
        return CONCLUSIONES_ENTREVISTA
    return CONCLUSIONES_MEDICAS if tipo == "medica" else CONCLUSIONES_GENERALES


def puede_registrar_resultado(estado: str) -> bool:
    return estado != "cancelada"


class Evaluacion(Base):
    """Una evaluación del candidato ligada a una POSTULACIÓN (persona + vacante). Nunca escribe `Postulacion.etapa`.
    `cuenta_id`/`postulacion_id`/… son enteros indexados SIN llave foránea (tabla del paso NO fatal)."""

    __tablename__ = "evaluaciones"
    __table_args__ = (UniqueConstraint("origen_tabla", "origen_id", name="uq_evaluacion_origen"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), index=True)  # EVA-####
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    postulacion_id: Mapped[int] = mapped_column(Integer, index=True)
    candidato_id: Mapped[int] = mapped_column(Integer, index=True)  # persona (desnormalizado para historial)
    vacante_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    tipo: Mapped[str] = mapped_column(String(20))  # TIPOS_EVALUACION_U
    nombre: Mapped[str] = mapped_column(String(200), default="")  # obligatorio solo con tipo «otra»
    forma: Mapped[str] = mapped_column(String(20))  # FORMAS_EVALUACION
    # --- evaluador (solo forma «asignada»): datos copiados con los que se notificó ---
    evaluador_tipo: Mapped[str] = mapped_column(String(10), default="")  # interno | externo
    evaluador_usuario_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    evaluador_contacto_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # ClienteContacto reutilizable
    evaluador_nombre: Mapped[str] = mapped_column(String(150), default="")
    evaluador_correo: Mapped[str] = mapped_column(String(200), default="")
    evaluador_whatsapp: Mapped[str] = mapped_column(String(30), default="")
    token_evaluador: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # liga sin cuenta; lo genera Red Human
    instrucciones: Mapped[str] = mapped_column(Text, default="")
    # --- liga de otro sistema / proveedor integrado (funciones actuales «Enlace externo» e «Integrada») ---
    liga_externa_candidato: Mapped[str] = mapped_column(String(500), default="")
    prueba_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # PruebaPsicometrica
    proveedor: Mapped[str] = mapped_column(String(150), default="")
    id_proveedor: Mapped[str] = mapped_column(String(150), default="")
    clave_proveedor: Mapped[str] = mapped_column(String(60), default="", index=True)  # webhook de Psicométricas.mx
    resultado_json: Mapped[dict] = mapped_column(JSON, default=dict)
    paso_integrada: Mapped[str] = mapped_column(String(20), default="")  # PASOS_INTEGRADA
    # --- cita (opcional): se guarda UTC + la zona con la que se capturó ---
    cita_fecha_hora: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    cita_zona_horaria: Mapped[str] = mapped_column(String(50), default="")
    cita_modalidad: Mapped[str] = mapped_column(String(20), default="")  # MODALIDADES_CITA
    cita_direccion: Mapped[str] = mapped_column(String(300), default="")  # obligatoria si Presencial
    cita_liga_videollamada: Mapped[str] = mapped_column(String(500), default="")  # obligatoria si Videollamada
    cita_telefono: Mapped[str] = mapped_column(String(30), default="")
    teams_evento_id: Mapped[str] = mapped_column(String(300), default="")
    # --- estado + condición de consentimiento ---
    estado: Mapped[str] = mapped_column(String(30), default="pendiente")  # ESTADOS_EVALUACION_U
    motivo_estado: Mapped[str] = mapped_column(Text, default="")  # no realizada / cancelada
    consentimiento: Mapped[str] = mapped_column(String(15), default="no_requerido")  # CONSENTIMIENTOS
    consentimiento_token: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    consentimiento_texto: Mapped[str] = mapped_column(Text, default="")  # copia EXACTA aceptada
    consentimiento_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    consentimiento_evidencia: Mapped[dict] = mapped_column(JSON, default=dict)
    # --- resultado (formulario único) ---
    conclusion: Mapped[str] = mapped_column(String(30), default="")  # conclusiones_de(tipo); "" = sin conclusión
    comentarios: Mapped[str] = mapped_column(Text, default="")
    adjuntos: Mapped[list] = mapped_column(JSON, default=list)  # [{id, archivo, nombre, mime, subido_por, subido_via, subido_en}]
    realizada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    realizada_por: Mapped[str] = mapped_column(String(200), default="")  # AUTOR real; "" = «No especificado»
    realizada_por_usuario_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    registrada_por: Mapped[str] = mapped_column(String(150), default="")  # quién CAPTURÓ (nunca se asume autor)
    registrada_por_usuario_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    registrada_via: Mapped[str] = mapped_column(String(20), default="")  # CANALES_EVALUACION
    registrada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    resultado_version: Mapped[int] = mapped_column(Integer, default=0)  # «Este resultado cambió mientras lo editabas»
    resultado_visto_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)  # etiqueta «Nuevo resultado»
    recordatorio_enviado_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
    # --- trazabilidad ---
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_por_usuario_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)
    # registro del que se migró («entrevistas_humanas» | «evaluaciones_candidato»); vacío = nació aquí
    origen_tabla: Mapped[str] = mapped_column(String(40), default="")
    origen_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    @property
    def nombre_visible(self) -> str:
        return self.nombre or TIPOS_EVALUACION_U.get(self.tipo, self.tipo)

    @property
    def nuevo_resultado(self) -> bool:
        return self.registrada_via == "liga_evaluador" and self.resultado_visto_en is None


class EventoEvaluacion(Base):
    """Historial append-only de una evaluación (creación, modificación, reprogramación, envíos, cambios de estado,
    resultado registrado/corregido/complementado). NUNCA se edita ni se borra."""

    __tablename__ = "eventos_evaluacion"

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluacion_id: Mapped[int] = mapped_column(Integer, index=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    accion: Mapped[str] = mapped_column(String(40))  # ACCIONES_EVENTO_EVALUACION
    actor: Mapped[str] = mapped_column(String(150), default="")  # usuario, evaluador o «sistema»
    actor_usuario_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    canal: Mapped[str] = mapped_column(String(20), default="sistema")  # CANALES_EVALUACION
    fecha: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, index=True)
    estado_anterior: Mapped[str] = mapped_column(String(30), default="")
    estado_nuevo: Mapped[str] = mapped_column(String(30), default="")
    anteriores: Mapped[dict] = mapped_column(JSON, default=dict)  # valores previos de lo que cambió
    detalle: Mapped[dict] = mapped_column(JSON, default=dict)


# --- Firma electrónica incrustada con Dropbox Sign (2026-09-29) ---
DOCUMENTOS_FIRMA = {"carta": "Carta de intención", "contrato": "Contrato individual de trabajo"}
ESTADOS_FIRMA = ("enviada", "firmada", "descargada", "cancelada", "error")


class FirmaDocumento(Base):
    """Una solicitud de firma (carta o contrato) de un expediente. Firmantes: la persona de RH que la crea
    (representante de la empresa) y el candidato. Cada quien firma en NUESTRA interfaz (modal incrustado): RH en el
    tablero, el candidato en su liga de expediente. El PDF final firmado se guarda en el expediente por webhook."""

    __tablename__ = "firmas_documentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    expediente_id: Mapped[int] = mapped_column(Integer, index=True)
    documento: Mapped[str] = mapped_column(String(20))  # DOCUMENTOS_FIRMA
    signature_request_id: Mapped[str] = mapped_column(String(80), index=True)
    firmantes: Mapped[list] = mapped_column(JSON, default=list)  # [{rol: rh|candidato, nombre, correo, signature_id, estado}]
    estado: Mapped[str] = mapped_column(String(20), default="enviada")  # ESTADOS_FIRMA
    test_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    documento_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Documento interno con el PDF firmado
    error: Mapped[str] = mapped_column(Text, default="")
    eventos: Mapped[list] = mapped_column(JSON, default=list)  # [{fecha, tipo}]
    creado_por: Mapped[str] = mapped_column(String(150), default="")
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    firmada_en: Mapped[Optional[datetime]] = mapped_column(FechaUTC(), nullable=True)
