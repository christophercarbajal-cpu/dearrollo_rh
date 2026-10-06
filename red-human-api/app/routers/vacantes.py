"""Módulo 1 · Reclutamiento — Vacantes y distribuidor (3.4, 3.5).

El generador produce, en una sola llamada, el contenido base de la vacante y la
publicación adaptada a cada plataforma (WhatsApp, OCC, LinkedIn y portal propio),
cada una con su `copy` (difusión) y su `page` (cuerpo listo para pegar).
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..database import get_db
from ..services import bolsas, conteos, notificaciones
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import (
    ENFOQUES_ENTREVISTA, MONEDAS_SUELDO, PERIODICIDADES_SUELDO, PLATAFORMAS, Cliente, Cuenta, Curso, Plantilla, Postulacion,
    Usuario, UsuarioCuenta, Vacante, registrar, slugificar, texto_sueldo, texto_ubicacion,
)
from ..serial import nombre_empresa, nombre_empresa_candidato, vacante_dict
from ..services import ia

router = APIRouter(prefix="/vacantes", tags=["vacantes"])

ESTADOS = ["Borrador", "En revisión", "Publicada", "Cerrada"]  # «Eliminada» solo vía DELETE (baja lógica)


def _por_codigo(db: Session, codigo: str, cuenta_id: int) -> Vacante:
    v = db.query(Vacante).filter(Vacante.codigo == codigo, Vacante.cuenta_id == cuenta_id).first()
    if not v:
        raise HTTPException(404, "Vacante no encontrada")
    return v


def _no_eliminada(v: Vacante) -> Vacante:
    """Las acciones de escritura (editar, publicar, cerrar, regenerar) no aplican a una vacante con
    baja lógica; leerla (GET /{codigo}) sí, para que la ficha explique que fue eliminada."""
    if v.estado == "Eliminada":
        raise HTTPException(409, "Esta vacante fue eliminada; restáurala antes de modificarla.")
    return v


def _slug_unico(db: Session, titulo: str, vacante_id: int) -> str:
    base = slugificar(titulo)
    tomado = (
        db.query(Vacante.id)
        .filter(Vacante.slug == base, Vacante.id != vacante_id)
        .first()
    )
    return f"{base}-{vacante_id}" if tomado else base


def _embudo(db: Session, v: Vacante) -> dict:
    """Conteo por etapa y por clasificación del agente — alimenta la tarjeta de la vacante.
    2026-09-20 (B4): sale de `services.conteos` (postulaciones activas, personas no eliminadas, incluye
    Modo Prueba) — la MISMA base que el Kanban y el pipeline global, así los contadores coinciden."""
    return {"etapas": conteos.por_etapa(db, v.cuenta_id, v.id), "estados": conteos.por_estado(db, v.cuenta_id, v.id)}


def _conteos(db: Session, v: Vacante):
    """B4: `candidatos` = activos de la vacante (== suma del embudo == Kanban filtrado); `nuevos` = de esos,
    los creados en las últimas 24 h."""
    base = conteos.postulaciones_visibles(db, v.cuenta_id, v.id)
    total = base.count()
    hace_24h = datetime.now(timezone.utc) - timedelta(days=1)
    nuevos = base.filter(Postulacion.creado_en >= hace_24h).count()
    return total, nuevos


def _salida(db: Session, v: Vacante) -> dict:
    total, nuevos = _conteos(db, v)
    colaboradores = []
    if v.colaboradores_ids:
        colaboradores = [n for (n,) in db.query(Usuario.nombre).filter(Usuario.id.in_(v.colaboradores_ids)).all()]
    return vacante_dict(v, total, nuevos, _embudo(db, v), colaboradores)


def _con_logo(db: Session, salida: dict, v: Vacante) -> dict:
    """Agrega el logo de la Cuenta al payload candidato-visible (Fase B, punto 12: la vista
    previa/portal público hereda la apariencia mínima de la Cuenta — logo + nombre comercial;
    el nombre ya lo resuelve `nombre_empresa_candidato` dentro de `vacante_dict`)."""
    return {
        **salida,
        "logoUrl": v.cuenta.logo if v.cuenta else "",
        "cuentaId": v.cuenta_id,
        "cuentaSlug": (v.cuenta.slug if v.cuenta else "") or "",
    }


def _validar_relaciones(
    db: Session,
    cuenta: Cuenta,
    cliente_id: Optional[int],
    responsable_id: Optional[int],
    colaboradores_ids: Optional[List[int]],
    plantilla_id: Optional[int],
) -> Optional[List[int]]:
    """Valida que Cliente/Responsable/Colaboradores/Plantilla (cuando vienen) pertenezcan a la
    Cuenta actual — nunca a otra. Regresa la lista de colaboradores_ids ya filtrada a los que sí
    pertenecen (los que no, se ignoran en silencio en vez de tronar: un id viejo/de otra Cuenta no
    debe bloquear guardar el resto del formulario)."""
    if cliente_id is not None:
        existe = db.query(Cliente).filter(Cliente.id == cliente_id, Cliente.cuenta_id == cuenta.id).first()
        if not existe:
            raise HTTPException(400, "El Cliente indicado no existe en esta Cuenta.")
    if responsable_id is not None:
        pertenece = (
            db.query(UsuarioCuenta)
            .filter(UsuarioCuenta.usuario_id == responsable_id, UsuarioCuenta.cuenta_id == cuenta.id)
            .first()
        )
        if not pertenece:
            raise HTTPException(400, "El responsable indicado no tiene acceso a esta Cuenta.")
    if plantilla_id is not None:
        existe = db.query(Plantilla).filter(Plantilla.id == plantilla_id, Plantilla.cuenta_id == cuenta.id).first()
        if not existe:
            raise HTTPException(400, "La plantilla indicada no existe en esta Cuenta.")
    if colaboradores_ids is None:
        return None
    return [
        uid
        for (uid,) in db.query(UsuarioCuenta.usuario_id)
        .filter(UsuarioCuenta.usuario_id.in_(colaboradores_ids), UsuarioCuenta.cuenta_id == cuenta.id)
        .all()
    ]


@router.get("")
def listar(
    estado: Optional[str] = None,
    incluir_eliminadas: bool = False,  # CRUD: baja lógica oculta salvo petición explícita (o estado=Eliminada)
    # --- Fase C: filtros adicionales ---
    busqueda: Optional[str] = None,          # LIKE sobre titulo (case-insensitive)
    cliente_id: Optional[int] = None,
    responsable_id: Optional[int] = None,
    area: Optional[str] = None,              # LIKE sobre area
    ubicacion: Optional[str] = None,         # LIKE sobre ubicacion
    db: Session = Depends(get_db),
    _: Usuario = Depends(usuario_actual),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    q = db.query(Vacante).filter(Vacante.cuenta_id == cuenta.id).order_by(Vacante.id.desc())
    if estado:
        q = q.filter(Vacante.estado == estado)
    elif not incluir_eliminadas:
        q = q.filter(Vacante.estado != "Eliminada")  # CRUD: las eliminadas no salen en tableros activos
    if busqueda:
        q = q.filter(Vacante.titulo.ilike(f"%{busqueda.strip()}%"))
    if cliente_id is not None:
        q = q.filter(Vacante.cliente_id == cliente_id)
    if responsable_id is not None:
        q = q.filter(Vacante.responsable_id == responsable_id)
    if area:
        q = q.filter(Vacante.area.ilike(f"%{area.strip()}%"))
    if ubicacion:
        q = q.filter(Vacante.ubicacion.ilike(f"%{ubicacion.strip()}%"))
    # Hotfix concurrencia 2026-09-24 (N+1): relaciones precargadas, contadores de todas las vacantes en
    # tres consultas agrupadas y nombres de colaboradores en una sola — antes ~7 consultas por vacante.
    vacantes = q.options(
        selectinload(Vacante.cliente), selectinload(Vacante.responsable),
        selectinload(Vacante.cuenta), selectinload(Vacante.curso_filtro),
    ).all()
    resumen = conteos.resumen_por_vacante(db, cuenta.id, datetime.now(timezone.utc) - timedelta(days=1))
    ids_colab = {uid for v in vacantes for uid in (v.colaboradores_ids or [])}
    nombres = dict(db.query(Usuario.id, Usuario.nombre).filter(Usuario.id.in_(ids_colab)).all()) if ids_colab else {}
    salida = []
    for v in vacantes:
        r = resumen.get(v.id) or {"etapas": {}, "estados": {}, "total": 0, "nuevos": 0}
        colaboradores = [nombres[uid] for uid in (v.colaboradores_ids or []) if uid in nombres]
        salida.append(vacante_dict(v, r["total"], r["nuevos"], {"etapas": r["etapas"], "estados": r["estados"]}, colaboradores))
    return salida


# ------------------------------------------------------------
# Generador con IA (no persiste — RH revisa antes de guardar)
# ------------------------------------------------------------


SEPARADOR_REQUISITOS = " · "


def requisitos_lista(texto: str) -> List[str]:
    """`Vacante.requisitos` es texto (legado); la Parte 3 lo trata como lista de indispensables.
    Acepta « · », saltos de línea y punto y coma como separadores."""
    partes: List[str] = []
    for linea in (texto or "").replace(";", "\n").replace(SEPARADOR_REQUISITOS, "\n").splitlines():
        if linea.strip(" .-•"):
            partes.append(linea.strip(" .-•"))
    return partes


class GenerarIn(BaseModel):
    """Ficha capturada por RH ANTES de generar (Parte 3, 2026-09-12). Las condiciones reales
    (sueldo, ubicación, modalidad, prestaciones) SOLO salen de aquí: la IA nunca las inventa."""
    titulo: str
    area: str = ""
    seniority: str = ""
    ubicacion: str = ""
    # Fase 4: ubicación estructurada (Estado / Municipio); si vienen, `ubicacion` se deriva de ellas
    ubicacion_estado: str = ""
    ubicacion_municipio: str = ""
    modalidad: str = "Presencial"
    # sueldo estructurado; `sueldo` (texto) se acepta por compatibilidad (agente, vacantes viejas)
    sueldo: str = ""
    sueldo_desde: Optional[int] = None
    sueldo_hasta: Optional[int] = None
    sueldo_moneda: str = "MXN"
    sueldo_periodicidad: str = ""
    # guía opcional para Red Human (reemplaza a las "notas para la IA")
    descripcion: str = ""  # descripción breve → la IA la expande
    requisitos: str = ""  # legado: indispensables en texto separados por « · »
    requisitos_indispensables: List[str] = []
    requisitos_deseables: List[str] = []
    beneficios: List[str] = []
    # Fase 4 (Punto 1): el nombre de empresa que ve el candidato NUNCA es texto libre — lo resuelve
    # el servidor con la misma regla que nombre_empresa_candidato: Cliente (si está marcado para
    # mostrarse) o nombre comercial de la Cuenta. `empresa` se acepta por compatibilidad y se ignora.
    empresa: str = ""
    cliente_id: Optional[int] = None
    mostrar_cliente_candidato: bool = True

    def ubicacion_texto(self) -> str:
        return texto_ubicacion(self.ubicacion_estado, self.ubicacion_municipio, self.ubicacion)

    def indispensables(self) -> List[str]:
        vistos = set()
        salida = []
        for r in [*self.requisitos_indispensables, *requisitos_lista(self.requisitos)]:
            k = r.strip().lower()
            if k and k not in vistos:
                vistos.add(k)
                salida.append(r.strip())
        return salida

    def sueldo_estructurado(self) -> bool:
        return bool(self.sueldo_periodicidad or self.sueldo_desde or self.sueldo_hasta)

    def sueldo_texto(self) -> str:
        """Derivado del estructurado; si no llegó estructurado, el texto legado tal cual."""
        if self.sueldo_estructurado():
            return texto_sueldo(self.sueldo_desde, self.sueldo_hasta, self.sueldo_moneda, self.sueldo_periodicidad)
        return (self.sueldo or "").strip()


def _validar_sueldo(datos: GenerarIn) -> None:
    if datos.sueldo_periodicidad and datos.sueldo_periodicidad not in PERIODICIDADES_SUELDO:
        raise HTTPException(400, f"Periodicidad de sueldo inválida. Usa una de: {', '.join(PERIODICIDADES_SUELDO)}")
    if datos.sueldo_moneda and datos.sueldo_moneda.upper() not in MONEDAS_SUELDO:
        raise HTTPException(400, f"Moneda inválida. Usa una de: {', '.join(MONEDAS_SUELDO)}")
    if (datos.sueldo_desde or 0) < 0 or (datos.sueldo_hasta or 0) < 0:
        raise HTTPException(400, "El sueldo no puede ser negativo.")
    if datos.sueldo_desde and datos.sueldo_hasta and datos.sueldo_hasta < datos.sueldo_desde:
        raise HTTPException(400, "El sueldo «hasta» no puede ser menor que el «desde».")
    if datos.seniority and datos.seniority not in ia.SENIORITY.__args__:
        raise HTTPException(400, f"Seniority inválido. Usa uno de: {', '.join(ia.SENIORITY.__args__)}")


def _empresa_resuelta(db: Session, cuenta: Cuenta, cliente_id: Optional[int], mostrar_cliente: bool) -> str:
    cliente = None
    if cliente_id is not None:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id, Cliente.cuenta_id == cuenta.id).first()
        if not cliente:
            raise HTTPException(400, "El Cliente indicado no existe en esta Cuenta.")
    return nombre_empresa(cuenta, cliente, mostrar_cliente)


def _ficha(datos: GenerarIn, empresa: str) -> ia.FichaVacante:
    return ia.FichaVacante(
        titulo=datos.titulo.strip(), area=datos.area, seniority=datos.seniority, ubicacion=datos.ubicacion_texto(),
        modalidad=datos.modalidad, sueldo_texto=datos.sueldo_texto(), empresa=empresa,
        descripcion_breve=datos.descripcion, requisitos_indispensables=datos.indispensables(),
        requisitos_deseables=[x for x in datos.requisitos_deseables if x.strip()],
        beneficios=[x for x in datos.beneficios if x.strip()],
    )


def _generar(datos: GenerarIn, empresa: str):
    if not datos.titulo.strip():
        raise HTTPException(400, "El título del puesto es obligatorio para generar la publicación.")
    _validar_sueldo(datos)
    return ia.generar_vacante(_ficha(datos, empresa))


@router.post("/generar")
def generar(
    datos: GenerarIn, db: Session = Depends(get_db), _: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)
):
    """Genera/completa la vacante a partir de la ficha capturada (Parte 3): nunca inventa condiciones y
    respeta literal lo capturado (ia._asegurar_capturado). Todo lo generado nombra a la empresa con la
    regla Cliente-visible / Cuenta (Punto 1)."""
    empresa = _empresa_resuelta(db, cuenta, datos.cliente_id, datos.mostrar_cliente_candidato)
    resultado, con_ia = _generar(datos, empresa)
    return {"ia": con_ia, "empresa": empresa, "sueldo_texto": datos.sueldo_texto() or "A convenir", **resultado.model_dump(by_alias=True)}


def _aplicar_generado(v: Vacante, g: ia.VacanteGenerada) -> None:
    """Vuelca la salida del generador sobre la vacante RESPETANDO lo capturado (Parte 3): solo rellena
    lo vacío y une listas con lo capturado primero; nunca toca sueldo ni beneficios (ya vienen
    garantizados por ia._asegurar_capturado) ni el seniority elegido."""
    v.resumen = v.resumen or g.resumen
    v.descripcion = g.descripcion  # la breve capturada fue la guía; la completa la sustituye (decisión 3)
    v.perfil_ideal = v.perfil_ideal or g.perfil_ideal
    v.responsabilidades = v.responsabilidades or g.responsabilidades
    v.requisitos = SEPARADOR_REQUISITOS.join(g.requisitos_indispensables) or v.requisitos
    v.requisitos_deseables = g.requisitos_deseables
    v.beneficios = g.beneficios
    v.palabras_clave = v.palabras_clave or g.palabras_clave
    v.seniority = v.seniority or g.seniority
    v.avisos_cumplimiento = g.avisos_cumplimiento
    v.texto_whatsapp = v.texto_whatsapp or g.texto_whatsapp
    v.texto_bolsa = v.texto_bolsa or g.occ.page  # compatibilidad con la forma anterior
    v.preguntas_filtro = v.preguntas_filtro or [p.model_dump() for p in g.preguntas_filtro]
    v.preguntas_filtro_whatsapp = v.preguntas_filtro_whatsapp or [p.model_dump() for p in g.preguntas_filtro_whatsapp]
    v.publicaciones = {
        "whatsapp": {"titulo": v.titulo, "copy": v.texto_whatsapp, "page": v.texto_whatsapp, "etiquetas": []},
        "occ": g.occ.bloque(),
        "linkedin": g.linkedin.bloque(),
        "portal": g.portal.bloque(),
    }


# ------------------------------------------------------------
# Alta y edición
# ------------------------------------------------------------


class CrearIn(GenerarIn):
    descripcion: str = ""
    texto_whatsapp: str = ""
    texto_bolsa: str = ""
    preguntas_filtro: List[dict] = []
    preguntas_filtro_whatsapp: List[dict] = []  # Fase 4: independientes de las de la web
    publicaciones: Dict[str, dict] = {}
    resumen: str = ""
    perfil_ideal: str = ""
    responsabilidades: List[str] = []
    requisitos_deseables: List[str] = []
    beneficios: List[str] = []
    palabras_clave: List[str] = []
    seniority: str = ""
    avisos_cumplimiento: List[str] = []
    publicar: bool = False
    plataformas: List[str] = []
    generar_si_falta: bool = True  # si no llega contenido, lo genera antes de guardar
    # --- Fase B: creación de vacante (punto 8) ---
    cliente_id: Optional[int] = None
    responsable_id: Optional[int] = None  # si no viene, default a quien crea la vacante
    colaboradores_ids: List[int] = []
    mostrar_cliente_candidato: bool = True
    plantilla_id: Optional[int] = None  # solo trazabilidad de qué plantilla se usó, si alguna
    enfoque_entrevista: str = "profesional"  # Fase 4 (Punto 6): profesional | profesional_personal
    # Proceso configurable (2026-10-06): {plantilla_id} copia una plantilla de la Cuenta; {pasos, etapas} la personaliza
    # para ESTA vacante sin tocar la plantilla; {quitar: true} = sin proceso. Omitido = la plantilla predeterminada.
    proceso: Optional[dict] = None


@router.post("", status_code=201)
def crear(
    datos: CrearIn,
    db: Session = Depends(get_db),
    u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    if not datos.titulo.strip():
        raise HTTPException(400, "El título del puesto es obligatorio.")

    colaboradores_validos = _validar_relaciones(
        db, cuenta, datos.cliente_id, datos.responsable_id, datos.colaboradores_ids, datos.plantilla_id
    )

    # 2026-09-26: la selección se guarda SIEMPRE (también en Borrador); el estado decide si se publica.
    plataformas = _unir_plataformas([], datos.plataformas)
    if datos.publicar and not plataformas:
        plataformas = ["Portal", "WhatsApp"]
    if datos.enfoque_entrevista not in ENFOQUES_ENTREVISTA:
        raise HTTPException(400, f"Enfoque de entrevista inválido. Usa uno de: {', '.join(ENFOQUES_ENTREVISTA)}")
    _validar_sueldo(datos)

    v = Vacante(
        codigo="TMP",
        cuenta_id=cuenta.id,
        cliente_id=datos.cliente_id,
        responsable_id=datos.responsable_id or u.id,
        colaboradores_ids=colaboradores_validos or [],
        mostrar_cliente_candidato=datos.mostrar_cliente_candidato,
        plantilla_id=datos.plantilla_id,
        titulo=datos.titulo.strip(),
        area=datos.area,
        empresa="",  # se fija abajo con la regla (Punto 1): nunca texto libre
        enfoque_entrevista=datos.enfoque_entrevista,
        ubicacion=datos.ubicacion_texto(),
        ubicacion_estado=datos.ubicacion_estado.strip(),
        ubicacion_municipio=datos.ubicacion_municipio.strip(),
        modalidad=datos.modalidad,
        # Parte 3: el texto del sueldo es DERIVADO del estructurado (o el legado; nunca inventado)
        sueldo=datos.sueldo_texto() or "A convenir",
        sueldo_desde=datos.sueldo_desde,
        sueldo_hasta=datos.sueldo_hasta,
        sueldo_moneda=(datos.sueldo_moneda or "MXN").upper(),
        sueldo_periodicidad=datos.sueldo_periodicidad,
        estado="Publicada" if datos.publicar else "Borrador",
        requisitos=SEPARADOR_REQUISITOS.join(datos.indispensables()),
        descripcion=datos.descripcion,
        texto_whatsapp=datos.texto_whatsapp,
        texto_bolsa=datos.texto_bolsa,
        preguntas_filtro=datos.preguntas_filtro,
        preguntas_filtro_whatsapp=datos.preguntas_filtro_whatsapp,
        plataformas=plataformas,
        resumen=datos.resumen,
        perfil_ideal=datos.perfil_ideal,
        responsabilidades=datos.responsabilidades,
        requisitos_deseables=datos.requisitos_deseables,
        beneficios=datos.beneficios,
        palabras_clave=datos.palabras_clave,
        seniority=datos.seniority,
        avisos_cumplimiento=datos.avisos_cumplimiento,
        publicaciones=datos.publicaciones,
    )

    # Punto 1: `Vacante.empresa` guarda el nombre RESUELTO (Cliente visible o Cuenta), nunca texto
    # libre. Se resuelve con `nombre_empresa` (cuenta + cliente ya validados) porque las relaciones
    # de `v` todavía no están cargadas antes del flush.
    cliente_obj = db.query(Cliente).filter(Cliente.id == datos.cliente_id).first() if datos.cliente_id else None
    v.empresa = nombre_empresa(cuenta, cliente_obj, datos.mostrar_cliente_candidato)

    con_ia = None
    if datos.generar_si_falta and not datos.publicaciones and not datos.responsabilidades:
        entrada_generador = GenerarIn(**datos.model_dump(include=set(GenerarIn.model_fields)))
        generado, con_ia = _generar(entrada_generador, v.empresa)
        _aplicar_generado(v, generado)

    v.proceso = _proceso_nuevo(db, cuenta.id, datos.proceso)
    db.add(v)
    db.flush()
    v.codigo = f"VAC-{1036 + v.id}"
    v.slug = _slug_unico(db, v.titulo, v.id)
    # Fase C: estampar fecha de publicación si la vacante se publica directamente al crear.
    if datos.publicar and v.publicada_en is None:
        v.publicada_en = v.creada_en  # misma marca de tiempo que la creación
    registrar(
        db, u.nombre, "vacante_creada", "vacante", v.codigo,
        {"titulo": v.titulo, "estado": v.estado, "ia": con_ia, "plataformas": v.plataformas,
         "proceso": (v.proceso or {}).get("plantilla_nombre") or ("personalizado" if v.proceso else "")},
    )
    db.commit()
    return _salida(db, v)


def _proceso_nuevo(db: Session, cuenta_id: int, datos: Optional[dict]) -> dict:
    """Proceso de una vacante nueva: el que manda el formulario o, si no manda nada, la plantilla predeterminada."""
    from ..services import proceso as sproc

    try:
        if datos is None:
            pl = sproc.predeterminada(db, cuenta_id)
            return sproc.proceso_desde_plantilla(pl) if pl else {}
        return sproc.proceso_para_vacante(db, cuenta_id, {}, datos)
    except sproc.ErrorProceso as e:
        raise HTTPException(e.status, e.mensaje)


@router.get("/slug/{slug}")
def por_slug(slug: str, db: Session = Depends(get_db)):
    """Vacante para la página pública de postulación (/aplicar/[slug])."""
    v = db.query(Vacante).filter(Vacante.slug == slug).first()
    if not v:
        raise HTTPException(404, "Vacante no encontrada")
    if v.estado != "Publicada":
        raise HTTPException(410, "Esta vacante ya no está recibiendo postulaciones.")
    return _con_logo(db, _salida(db, v), v)


@router.get("/slug/{slug}/jobposting")
def jobposting_por_slug(slug: str, db: Session = Depends(get_db)):
    """JSON-LD `JobPosting` (Schema.org) para Google Empleos — público, sin sesión (solo datos de la
    vacante). 404 si la vacante no existe, no está Publicada, su Cuenta no está activa o RH no marcó
    «Google Empleos»: así, al apagarla, Google deja de ver el marcado y la desindexa."""
    v = db.query(Vacante).filter(Vacante.slug == slug).first()
    if not v or not bolsas.publicable_en(v, bolsas.GOOGLE_EMPLEOS):
        raise HTTPException(404, "Vacante no disponible en Google Empleos")
    return bolsas.jobposting(v)


def _cuenta_publica(db: Session, cuenta: str) -> Optional[Cuenta]:
    """`?cuenta=` del portal: slug o id numérico. Solo Cuentas activas."""
    q = db.query(Cuenta).filter(Cuenta.estado == "Activa")
    if cuenta.isdigit():
        return q.filter(Cuenta.id == int(cuenta)).first()
    return q.filter(Cuenta.slug == cuenta.strip().lower()).first()


@router.get("/publicas")
def listar_publicas(cuenta: str = "", db: Session = Depends(get_db)):
    """Bolsa de trabajo pública (/portal) — solo vacantes Publicadas, sin sesión.

    2026-09-17: `?cuenta=<slug|id>` aísla el portal a UNA Cuenta (bolsa propia de cada cliente de
    la plataforma). Sin parámetro sigue siendo la bolsa global (todas las Cuentas activas) — por eso
    una vacante «eliminada» en el dashboard (que solo ve la Cuenta actual) puede seguir viéndose en
    el portal global: es otra vacante homónima de OTRA Cuenta. Cada fila trae `cuentaId`/`cuentaSlug`.
    Una Cuenta eliminada (baja lógica) nunca publica.

    Debe declararse antes de GET /{codigo} para que 'publicas' no se interprete
    como un código de vacante.
    """
    q = (
        db.query(Vacante)
        .join(Cuenta, Cuenta.id == Vacante.cuenta_id)
        .filter(Vacante.estado == "Publicada", Cuenta.estado == "Activa")
        .order_by(Vacante.id.desc())
    )
    if cuenta:
        cu = _cuenta_publica(db, cuenta)
        if not cu:
            raise HTTPException(404, "Portal no encontrado.")
        q = q.filter(Vacante.cuenta_id == cu.id)
    return [_con_logo(db, _salida(db, v), v) for v in q.all()]


@router.get("/publicas/cuenta")
def cuenta_publica(cuenta: str, db: Session = Depends(get_db)):
    """Encabezado del portal por Cuenta (nombre comercial + logo), sin sesión."""
    cu = _cuenta_publica(db, cuenta)
    if not cu:
        raise HTTPException(404, "Portal no encontrado.")
    return {"id": cu.id, "slug": cu.slug, "nombre": cu.nombre_comercial or cu.nombre, "logoUrl": cu.logo or ""}


def _homonimas_otras_cuentas(db: Session, v: Vacante) -> List[dict]:
    """Vacantes PUBLICADAS con el mismo título en OTRAS Cuentas activas (2026-09-17): explica por qué
    «sigue en el portal» una vacante que esta Cuenta ya eliminó."""
    if not v.titulo:
        return []
    filas = (
        db.query(Vacante, Cuenta)
        .join(Cuenta, Cuenta.id == Vacante.cuenta_id)
        .filter(
            func.lower(Vacante.titulo) == v.titulo.strip().lower(), Vacante.estado == "Publicada",
            Vacante.cuenta_id != v.cuenta_id, Cuenta.estado == "Activa",
        )
        .all()
    )
    return [{"codigo": x.codigo, "cuenta": cu.nombre_visible, "cuentaId": cu.id} for x, cu in filas]


@router.get("/{codigo}")
def detalle(
    codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)
):
    v = _por_codigo(db, codigo, cuenta.id)
    return {**_salida(db, v), "homonimasOtrasCuentas": _homonimas_otras_cuentas(db, v)}


@router.get("/{codigo}/vista-previa")
def vista_previa(
    codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)
):
    """Cómo verá el candidato esta vacante — funciona aunque siga en Borrador (usa `codigo`, no
    `slug`: una vacante sin publicar no tiene slug público), para que "Vista previa" nunca sea
    obligatoria para publicar (punto 12). Mismo payload que `/vacantes/slug/{slug}`, para que la
    vista previa sea fiel a lo que el candidato verá de verdad."""
    v = _por_codigo(db, codigo, cuenta.id)
    return _con_logo(db, _salida(db, v), v)


class ActualizarIn(BaseModel):
    titulo: Optional[str] = None
    area: Optional[str] = None
    empresa: Optional[str] = None
    ubicacion: Optional[str] = None
    modalidad: Optional[str] = None
    sueldo: Optional[str] = None
    sueldo_desde: Optional[int] = None  # Parte 3
    sueldo_hasta: Optional[int] = None
    sueldo_moneda: Optional[str] = None
    sueldo_periodicidad: Optional[str] = None
    requisitos: Optional[str] = None
    descripcion: Optional[str] = None
    resumen: Optional[str] = None
    perfil_ideal: Optional[str] = None
    responsabilidades: Optional[List[str]] = None
    requisitos_deseables: Optional[List[str]] = None
    beneficios: Optional[List[str]] = None
    palabras_clave: Optional[List[str]] = None
    seniority: Optional[str] = None
    texto_whatsapp: Optional[str] = None
    texto_bolsa: Optional[str] = None  # CRUD: el formulario de edición manda el contenido completo
    avisos_cumplimiento: Optional[List[str]] = None
    preguntas_filtro: Optional[List[dict]] = None
    preguntas_filtro_whatsapp: Optional[List[dict]] = None  # Fase 4
    ubicacion_estado: Optional[str] = None  # Fase 4
    ubicacion_municipio: Optional[str] = None
    curso_filtro: Optional[str] = None  # Capacitación universal: código CUR-#### ("" = quitar)
    publicaciones: Optional[Dict[str, dict]] = None
    estado: Optional[str] = None
    # --- Fase B: Cliente/Responsable/Colaboradores/visibilidad (punto 8/10) ---
    cliente_id: Optional[int] = None
    responsable_id: Optional[int] = None
    colaboradores_ids: Optional[List[int]] = None
    mostrar_cliente_candidato: Optional[bool] = None
    enfoque_entrevista: Optional[str] = None  # Fase 4 (Punto 6)
    # Evaluaciones (2026-09-28): sugerencias [{tipo, prueba_id?, nombre?}] + aviso antes de Onboarding
    evaluaciones_sugeridas: Optional[List[dict]] = None
    avisar_evaluaciones_antes_onboarding: Optional[bool] = None
    # Proceso configurable (2026-10-06): mismo formato que en la creación. Cambiarlo sube la versión del proceso de la
    # vacante y NUNCA toca a los candidatos que ya tiene (cada uno conserva la versión con la que entró).
    proceso: Optional[dict] = None


@router.patch("/{codigo}")
def actualizar(
    codigo: str, datos: ActualizarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Edición manual de RH sobre lo que generó la IA (el agente propone, RH dispone)."""
    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    colaboradores_validos = _validar_relaciones(
        db, cuenta, datos.cliente_id, datos.responsable_id, datos.colaboradores_ids, None
    )
    cambios = datos.model_dump(exclude_none=True, exclude={"autor"})
    if colaboradores_validos is not None:
        cambios["colaboradores_ids"] = colaboradores_validos
    if datos.estado is not None and datos.estado not in ESTADOS:
        raise HTTPException(400, f"Estado inválido. Usa uno de: {', '.join(ESTADOS)}")
    if datos.enfoque_entrevista is not None and datos.enfoque_entrevista not in ENFOQUES_ENTREVISTA:
        raise HTTPException(400, f"Enfoque de entrevista inválido. Usa uno de: {', '.join(ENFOQUES_ENTREVISTA)}")
    cambios.pop("empresa", None)  # Punto 1: nunca texto libre; se recalcula abajo
    if "proceso" in cambios:
        from ..services import proceso as sproc

        try:
            cambios["proceso"] = sproc.proceso_para_vacante(db, cuenta.id, v.proceso or {}, cambios["proceso"])
        except sproc.ErrorProceso as e:
            raise HTTPException(e.status, e.mensaje)
        if cambios["proceso"] == (v.proceso or {}):
            cambios.pop("proceso")
    if "curso_filtro" in cambios:
        codigo_curso = (cambios.pop("curso_filtro") or "").strip()
        if not codigo_curso:
            v.curso_filtro_id = None
        else:
            curso = db.query(Curso).filter(Curso.codigo == codigo_curso, Curso.cuenta_id == cuenta.id, Curso.estado == "Publicado").first()
            if not curso:
                raise HTTPException(400, "El curso de filtro no existe o no está publicado.")
            v.curso_filtro_id = curso.id
    if "evaluaciones_sugeridas" in cambios:
        from ..models import PruebaPsicometrica
        from ..services import evaluaciones as sev

        try:
            pruebas = {pr.id: pr.nombre for pr in db.query(PruebaPsicometrica).filter(PruebaPsicometrica.cuenta_id == cuenta.id).all()}
        except Exception:  # noqa: BLE001 — tablas de módulos no disponibles
            db.rollback()
            pruebas = {}
        cambios["evaluaciones_sugeridas"] = sev.normalizar_sugeridas(cambios["evaluaciones_sugeridas"], pruebas)
    if datos.sueldo_periodicidad is not None and datos.sueldo_periodicidad not in PERIODICIDADES_SUELDO:
        raise HTTPException(400, f"Periodicidad de sueldo inválida. Usa una de: {', '.join(PERIODICIDADES_SUELDO)}")
    if datos.seniority is not None and datos.seniority and datos.seniority not in ia.SENIORITY.__args__:
        raise HTTPException(400, f"Seniority inválido. Usa uno de: {', '.join(ia.SENIORITY.__args__)}")

    for campo, valor in cambios.items():
        setattr(v, campo, valor)
    # Fase 4: si tocó Estado/Municipio, el texto de ubicación se deriva
    if any(k in cambios for k in ("ubicacion_estado", "ubicacion_municipio")):
        v.ubicacion = texto_ubicacion(v.ubicacion_estado, v.ubicacion_municipio, v.ubicacion)
    # Parte 3: si tocó el sueldo estructurado, el texto se deriva (nunca se edita por separado)
    if any(k in cambios for k in ("sueldo_desde", "sueldo_hasta", "sueldo_moneda", "sueldo_periodicidad")):
        v.sueldo = texto_sueldo(v.sueldo_desde, v.sueldo_hasta, v.sueldo_moneda, v.sueldo_periodicidad)
    db.flush()
    db.refresh(v)  # recarga cliente/cuenta si cambió cliente_id
    v.empresa = nombre_empresa_candidato(v)
    if datos.titulo:
        v.slug = _slug_unico(db, v.titulo, v.id)
    if datos.publicaciones:
        v.texto_bolsa = (datos.publicaciones.get("occ") or {}).get("page", v.texto_bolsa)

    registrar(db, u.nombre, "vacante_editada", "vacante", v.codigo, {"campos": sorted(cambios)})
    db.commit()
    return _salida(db, v)


class RegenerarIn(BaseModel):
    notas: str = ""


@router.post("/{codigo}/regenerar")
def regenerar(
    codigo: str, datos: RegenerarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Vuelve a generar todo el contenido de una vacante existente con los datos ya capturados."""
    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    ficha = ia.FichaVacante(
        titulo=v.titulo, area=v.area, seniority=v.seniority or "", ubicacion=v.ubicacion, modalidad=v.modalidad,
        sueldo_texto=v.sueldo or "", empresa=v.empresa or nombre_empresa_candidato(v),
        descripcion_breve=(datos.notas or "").strip(), requisitos_indispensables=requisitos_lista(v.requisitos),
        requisitos_deseables=list(v.requisitos_deseables or []), beneficios=list(v.beneficios or []),
    )
    generado, con_ia = ia.generar_vacante(ficha)
    # regenerar = volver a redactar: se limpian los textos generados para que _aplicar_generado los
    # rellene, pero lo capturado (requisitos, beneficios, sueldo, seniority) se respeta igual.
    v.resumen = v.perfil_ideal = v.texto_whatsapp = v.texto_bolsa = ""
    v.responsabilidades, v.palabras_clave, v.preguntas_filtro = [], [], []
    _aplicar_generado(v, generado)
    registrar(db, u.nombre, "vacante_regenerada", "vacante", v.codigo, {"ia": con_ia, "notas": datos.notas})
    db.commit()
    return {"ia": con_ia, **_salida(db, v)}


# ------------------------------------------------------------
# Distribución
# ------------------------------------------------------------


class PublicarIn(BaseModel):
    plataformas: List[str] = ["Portal", "WhatsApp"]


def _unir_plataformas(actuales: List[str], nuevas: List[str]) -> List[str]:
    """Une la selección nueva (solo valores del catálogo `PLATAFORMAS`) con la ya guardada, sin
    duplicados y en el orden del catálogo. Valores viejos que ya no están en el catálogo (p. ej. «OCC»
    de vacantes previas) se conservan al final: nunca se borran en silencio ni rompen el orden."""
    validas = {p for p in nuevas if p in PLATAFORMAS}
    previas = list(dict.fromkeys(actuales or []))
    union = set(previas) | validas
    del_catalogo = [p for p in PLATAFORMAS if p in union]
    return del_catalogo + [p for p in previas if p not in PLATAFORMAS]


@router.post("/{codigo}/publicar")
async def publicar(
    codigo: str, datos: PublicarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    if not any(p in PLATAFORMAS for p in datos.plataformas):
        raise HTTPException(400, f"Elige al menos una plataforma válida: {', '.join(PLATAFORMAS)}")
    if not (v.publicaciones or v.descripcion):
        raise HTTPException(409, "La vacante no tiene contenido. Genera la publicación antes de distribuirla.")

    v.estado = "Publicada"
    v.plataformas = _unir_plataformas(v.plataformas, datos.plataformas)
    if not v.slug:
        v.slug = _slug_unico(db, v.titulo, v.id)
    # Fase C: estampar fecha de primera publicación (solo la primera vez; no sobreescribir en
    # reaperturas — la fecha que importa es cuándo salió por primera vez al público).
    if v.publicada_en is None:
        v.publicada_en = datetime.now(timezone.utc)
    registrar(db, u.nombre, "vacante_publicada", "vacante", v.codigo, {"plataformas": v.plataformas})
    db.commit()
    # 2026-09-19: descripción de la vacante (HTML corporativo) al Cliente y al responsable; nunca bloquea.
    envios = await notificaciones.notificar_vacante_publicada(db, v, u.nombre)
    db.commit()
    return {**_salida(db, v), "notificaciones": envios}


@router.post("/{codigo}/cerrar")
def cerrar(
    codigo: str, datos: PublicarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    v.estado = "Cerrada"
    v.plataformas = []
    registrar(db, u.nombre, "vacante_cerrada", "vacante", v.codigo, {})
    db.commit()
    return _salida(db, v)


@router.delete("/{codigo}")
def eliminar(
    codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """CRUD (2026-09-15): baja LÓGICA. La vacante pasa a «Eliminada» (fecha y quién), se retira de
    portal/WhatsApp/plataformas y deja de aparecer en tableros y métricas. Nada se borra: sus
    postulaciones, entrevistas y expedientes siguen ligados (historial); las postulaciones que
    seguían ACTIVAS se cierran con motivo `vacante_eliminada` para que no queden huérfanas en el
    Kanban. Reversible con POST /{codigo}/restaurar (queda en Borrador)."""
    v = _por_codigo(db, codigo, cuenta.id)
    if v.estado == "Eliminada":
        raise HTTPException(409, "Esta vacante ya está eliminada.")
    activas = [p for p in v.postulaciones if p.activa]
    for p in activas:
        p.cerrar("vacante_eliminada")
    v.estado = "Eliminada"
    v.plataformas = []
    v.eliminada_en = datetime.now(timezone.utc)
    v.eliminada_por = u.nombre
    registrar(
        db, u.nombre, "vacante_eliminada", "vacante", v.codigo,
        {"titulo": v.titulo, "postulaciones_cerradas": [p.codigo for p in activas], "correo_rh": u.correo},
    )
    db.commit()
    return {"ok": True, "vacante": _salida(db, v), "postulacionesCerradas": len(activas)}


@router.post("/{codigo}/restaurar")
def restaurar(
    codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Deshace la baja lógica: vuelve como Borrador (RH decide si la republica). Las postulaciones
    cerradas por la eliminación NO se reabren solas — RH las mueve de etapa si hace falta."""
    v = _por_codigo(db, codigo, cuenta.id)
    if v.estado != "Eliminada":
        raise HTTPException(409, "Esta vacante no está eliminada.")
    v.estado = "Borrador"
    v.eliminada_en = None
    v.eliminada_por = ""
    registrar(db, u.nombre, "vacante_restaurada", "vacante", v.codigo, {"correo_rh": u.correo})
    db.commit()
    return _salida(db, v)


@router.get("/{codigo}/publicacion/{plataforma}")
def publicacion(
    codigo: str, plataforma: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Texto listo para copiar y pegar en la plataforma indicada, con la liga de postulación."""
    v = _por_codigo(db, codigo, cuenta.id)
    clave = plataforma.lower()
    bloque = (v.publicaciones or {}).get(clave)
    if not bloque:
        raise HTTPException(404, f"La vacante no tiene publicación generada para '{plataforma}'. Usa /regenerar.")
    liga = f"{settings.app_url}/aplicar/{v.slug or slugificar(v.titulo)}"
    return {
        "plataforma": clave,
        "vacante": v.codigo,
        "liga": liga,
        "titulo": bloque.get("titulo", v.titulo),
        "copy": bloque.get("copy", ""),
        "page": bloque.get("page", ""),
        "etiquetas": bloque.get("etiquetas", []),
        # el copy con la liga ya incrustada, que es lo que RH pega en la plataforma
        "copyConLiga": f"{bloque.get('copy', '')}\n\n👉 Postúlate aquí: {liga}".strip(),
    }
