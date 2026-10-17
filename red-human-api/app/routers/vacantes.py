"""Módulo 1 · Reclutamiento — Vacantes y distribuidor (3.4, 3.5).

El generador produce, en una sola llamada, el contenido base de la vacante y la
publicación adaptada a cada plataforma (WhatsApp, OCC, LinkedIn y portal propio),
cada una con su `copy` (difusión) y su `page` (cuerpo listo para pegar).
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..database import get_db
from ..services import bolsas, conteos, notificaciones
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import (
    ENFOQUES_ENTREVISTA, MONEDAS_SUELDO, PERIODICIDADES_SUELDO, PLATAFORMAS, Cliente, Cuenta, Curso, Plantilla, PlantillaProceso, Postulacion,
    SUELDO_COMPETITIVO, Usuario, UsuarioCuenta, Vacante, normalizar_documentos_vacante, registrar, slugificar, texto_sueldo, texto_ubicacion,
)
from ..services.mensajeria import canales_publicos
from ..serial import nombre_empresa, nombre_empresa_candidato, vacante_dict
from ..services import ia

router = APIRouter(prefix="/vacantes", tags=["vacantes"])

ESTADOS = ["Borrador", "En revisión", "Publicada", "Pausada", "Cerrada"]  # «Eliminada» solo vía DELETE (baja lógica)
# Cambio 36: «El proceso manda» — con candidatos, la ruta de la vacante ya no se cambia
MENSAJE_PROCESO_BLOQUEADO = ("No se puede cambiar: la vacante ya tiene candidatos y cada uno sigue la ruta con la que entró. "
                             "Para usar otra ruta, crea una vacante nueva.")
MENSAJE_PAUSADA = "Esta vacante está en pausa: por ahora no recibe postulaciones."


def con_candidatos(db: Session, v: Vacante) -> int:
    """Postulaciones (activas o cerradas) de la vacante: con alguna, su proceso queda bloqueado."""
    return db.query(Postulacion).filter(Postulacion.vacante_id == v.id).count() if v.id else 0


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


def liga_congelada(v: Vacante) -> bool:
    """La liga de postulación (`slug`) queda CONGELADA desde la primera publicación: ya circula en portal, WhatsApp y las
    plataformas (cada una con su `?canal=`). Editar el título después cambia el título, nunca la liga."""
    return bool(v.slug) and (v.publicada_en is not None or v.estado in ("Publicada", "Pausada", "Cerrada"))


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
    return {"etapas": conteos.por_etapa(db, v.cuenta_id, v.id), "estados": conteos.por_estado(db, v.cuenta_id, v.id),
            # Cambio 37: «[n] descartados · [n] en revisión» (debajo de los contadores, con liga a la lista)
            "descartados": conteos.descartados(db, v.cuenta_id, v.id), "enRevision": conteos.en_revision(db, v.cuenta_id, v.id)}


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


def _salida_rh(db: Session, v: Vacante) -> dict:
    """Detalle para RH: agrega las plantillas de conversación (guiones por actividad de la ruta). NUNCA en los payloads
    públicos (portal, /aplicar): el guion de la entrevista no se le muestra al candidato. Cambio 36: canales conectados
    («Publicar en») y si el proceso quedó bloqueado por tener candidatos."""
    from ..services import guiones as sgui
    from ..services import publicador

    n = con_candidatos(db, v)
    return {**_salida(db, v), "guiones": sgui.vista(v, db=db), "canalesConectados": publicador.conectados(db, v),
            "candidatosTotales": n, "procesoBloqueado": MENSAJE_PROCESO_BLOQUEADO if n else ""}


def _guardar_guiones(v: Vacante, entrada: Optional[dict], por: str, web=None, whatsapp=None) -> None:
    from ..services import guiones as sgui

    try:
        sgui.guardar_desde_formulario(v, entrada, por, preguntas_web=web, preguntas_whatsapp=whatsapp)
    except sgui.ErrorGuion as e:
        raise HTTPException(e.status, e.mensaje)


def _sincronizar_ruta(db: Session, v: Vacante, publicar: bool = False) -> None:
    """Cambio 28: la ruta guardada se ajusta a la vacante (documentos tras el prefiltro, CV con Análisis de CV). Lo que
    falte según la ruta no impide guardar, pero impide PUBLICAR (409 con la lista)."""
    from ..services import sincronizacion_vacante as ssv

    ssv.sincronizar(v)
    if publicar:
        faltan = ssv.faltantes_publicacion(db, v)
        if faltan:
            raise HTTPException(409, "No se puede publicar: " + " ".join(faltan))


def _sincronizar_filtro(v: Vacante, entrada=None, propuestos: Optional[List[str]] = None, webs: Optional[dict] = None,
                        seguimientos: Optional[dict] = None) -> None:
    """Cambio 37: «Preguntas filtro» — la entidad única se recalcula contra los indispensables vigentes (conserva lo que
    RH ajustó) y DERIVA el formulario web; el prefiltro de WhatsApp separado deja de existir. 2026-10-17: `webs` /
    `seguimientos` = las preguntas web (Sí/No) y de WhatsApp (continuación) que acaba de proponer la generación."""
    from ..services import preguntas_filtro as pfu

    pfu.sincronizar(v, entrada=entrada, propuestos=propuestos, webs=webs, seguimientos=seguimientos)


def _validar_minimo_prefiltro(db: Session, v: Vacante, explicito: bool) -> None:
    """2026-10-11 (tarde): con prefiltro web en la ruta se exigen al menos 3 preguntas clave (si no, no se guarda)."""
    from ..services import guiones as sgui

    try:
        sgui.validar_minimo_web(db, v, explicito)
    except sgui.ErrorGuion as e:
        raise HTTPException(e.status, e.mensaje)


# Cambio 58: campos de la vacante que solo ve RH (el nombre del proceso distingue vacantes homónimas en el tablero)
CAMPOS_SOLO_RH = ("proceso", "procesoNombre")


def _con_logo(db: Session, salida: dict, v: Vacante) -> dict:
    """Agrega el logo de la Cuenta al payload candidato-visible (Fase B, punto 12: la vista
    previa/portal público hereda la apariencia mínima de la Cuenta — logo + nombre comercial;
    el nombre ya lo resuelve `nombre_empresa_candidato` dentro de `vacante_dict`).

    Cambio 58 (2026-10-15): el proceso de selección es INTERNO — su nombre y su ruta (`CAMPOS_SOLO_RH`) nunca salen en
    estos payloads (portal, /aplicar, vista previa)."""
    salida = {k: x for k, x in salida.items() if k not in CAMPOS_SOLO_RH}
    if getattr(v, "mostrar_sueldo", True) is False:
        # 2026-10-16 (Bloque 2): «Mostrar sueldo en la publicación» desmarcado → el candidato ve «Sueldo competitivo»
        salida.update({"sueldo": SUELDO_COMPETITIVO, "sueldoPublico": SUELDO_COMPETITIVO, "sueldoDesde": None, "sueldoHasta": None})
    return {
        **salida,
        "logoUrl": v.cuenta.logo if v.cuenta else "",
        "cuentaId": v.cuenta_id,
        "cuentaSlug": (v.cuenta.slug if v.cuenta else "") or "",
        **canales_publicos(v, db),  # 2026-10-06: canal con candidatos de la Cuenta (WhatsApp / Telegram / ambos)
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
    mostrar_sueldo: bool = True  # 2026-10-16 (Bloque 2): «Mostrar sueldo en la publicación»
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
    # Generación según la ruta (2026-10-09): la ruta que eligió RH en el formulario ({plantilla_id} o {pasos}; sin ella,
    # la predeterminada de la Cuenta), el enfoque, lo que ya hay (para conservar ediciones) y qué secciones conservar.
    responsabilidades: List[str] = []
    enfoque_entrevista: str = "profesional"
    proceso: Optional[dict] = None
    guiones_actuales: Optional[dict] = None  # {preguntas_filtro, preguntas_filtro_whatsapp, secciones: {clave: guion}}
    conservar: List[str] = []
    # Cambio 25: «Documentos y fotos que se piden» [{nombre, cantidad, foto}]; vacío al generar → la IA los toma de la
    # descripción si los menciona
    documentos_solicitados: List[dict] = []
    # Cambio 31: pregunta natural por indispensable [{requisito, pregunta}] (la propone /generar; RH puede ajustarla)
    preguntas_requisitos: List[dict] = []
    # Cambio 37: «Preguntas filtro» (entidad única) que RH ya ajustó y los indispensables «Propuesto por Red Human»
    filtro: Optional[List[dict]] = None
    indispensables_propuestos: Optional[List[str]] = None
    perfil_operativo: Optional[dict] = None  # 2026-10-10: oficio de la Biblioteca (ajustado para esta vacante), enfoque operativo

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
        modalidad=datos.modalidad, sueldo_texto=datos.sueldo_texto() if datos.mostrar_sueldo else SUELDO_COMPETITIVO, empresa=empresa,
        descripcion_breve=datos.descripcion, requisitos_indispensables=datos.indispensables(),
        requisitos_deseables=[x for x in datos.requisitos_deseables if x.strip()],
        beneficios=[x for x in datos.beneficios if x.strip()], mostrar_sueldo=datos.mostrar_sueldo,
    )


def _generar(datos: GenerarIn, empresa: str, pide_cv: Optional[bool] = None):
    if not datos.titulo.strip():
        raise HTTPException(400, "El título del puesto es obligatorio para generar la publicación.")
    _validar_sueldo(datos)
    ficha = _ficha(datos, empresa)
    ficha.pide_cv = pide_cv
    return ia.generar_vacante(ficha)


@router.post("/generar")
def generar(
    datos: GenerarIn, db: Session = Depends(get_db), _: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)
):
    """Genera/completa la vacante a partir de la ficha capturada (Parte 3): nunca inventa condiciones y
    respeta literal lo capturado (ia._asegurar_capturado). Todo lo generado nombra a la empresa con la
    regla Cliente-visible / Cuenta (Punto 1)."""
    empresa = _empresa_resuelta(db, cuenta, datos.cliente_id, datos.mostrar_cliente_candidato)
    from ..services import guiones as sgui

    pasos_ruta = _pasos_de_formulario(db, cuenta.id, datos.proceso)
    resultado, con_ia = _generar(datos, empresa, pide_cv=sgui.pide_cv(pasos_ruta) if pasos_ruta else None)
    salida = {"ia": con_ia, "empresa": empresa, "sueldo_texto": datos.sueldo_texto() or "A convenir", **resultado.model_dump(by_alias=True)}
    # Cambio 25: lo capturado se respeta literal; si RH no capturó nada, los documentos que MENCIONA la vacante
    salida["documentos_solicitados"] = normalizar_documentos_vacante(datos.documentos_solicitados) or ia.documentos_de_descripcion(
        datos.descripcion, " ".join(datos.indispensables()), " ".join(datos.requisitos_deseables),
        " ".join(datos.responsabilidades), resultado.descripcion if hasattr(resultado, "descripcion") else "")
    # 2026-10-09: prefiltros y guiones SOLO de las actividades de la ruta elegida, con las reglas de generación por tipo
    actuales = dict(datos.guiones_actuales or {})
    vigentes = {"prefiltro_web": actuales.get("preguntas_filtro") or [], "prefiltro_whatsapp": actuales.get("preguntas_filtro_whatsapp") or [],
                **{k: g for k, g in (actuales.get("secciones") or {}).items() if k in sgui.SECCIONES}}
    datos_g = {"titulo": datos.titulo.strip(), "responsabilidades": [x for x in datos.responsabilidades if x.strip()] or list(resultado.responsabilidades),
               "requisitos": list(resultado.requisitos_indispensables), "requisitos_deseables": list(resultado.requisitos_deseables),
               "ubicacion": datos.ubicacion_texto(), "modalidad": datos.modalidad, "sueldo": datos.sueldo_texto() or "A convenir",
               "enfoque_entrevista": datos.enfoque_entrevista if datos.enfoque_entrevista in ENFOQUES_ENTREVISTA else "profesional", "horario": ""}
    from ..services import proceso as sproc

    # Cambio 27: la plantilla de ruta define el enfoque (Masivos = Operativo), sin importar el seniority
    datos_g["enfoque_entrevista"] = sproc.enfoque_de_ruta(pasos_ruta) or datos_g["enfoque_entrevista"]
    salida["enfoque_entrevista"] = datos_g["enfoque_entrevista"]
    if datos_g["enfoque_entrevista"] == "operativo" and datos.perfil_operativo:
        from ..services import entrevista_operativa as eop

        try:
            datos_g["perfil_operativo"] = eop.normalizar_perfil(datos.perfil_operativo)
        except ValueError as e:
            raise HTTPException(400, str(e))
    # Cambio 37: los prefiltros son UNA sola entidad («Preguntas filtro»): aquí solo se generan los guiones de entrevista
    guion_claves = [c for c in sgui.secciones_aplicables(sgui.tipos_de_ruta(pasos_ruta)) if sgui.SECCIONES[c]["clase"] == "guion"]
    try:
        g = sgui.generar(datos_g, pasos_ruta, vigentes, conservar=datos.conservar, empresa=empresa, solo=guion_claves or ["-"])
    except sgui.ErrorGuion as e:
        raise HTTPException(e.status, e.mensaje)
    from ..services import preguntas_filtro as pfu

    filtro = pfu.armar(list(resultado.requisitos_indispensables), titulo=datos.titulo.strip(),
                       sueldo="" if ((datos.sueldo_texto() or "A convenir") == "A convenir" or not datos.mostrar_sueldo) else datos.sueldo_texto(),
                       ubicacion="" if (datos.modalidad or "").lower().startswith("remot") else datos.ubicacion_texto(),
                       modalidad=datos.modalidad, seniority=datos.seniority or resultado.seniority,
                       naturales={ia._norm_req(x.requisito): x.pregunta for x in resultado.preguntas_requisitos},
                       **_web_y_whatsapp(resultado),
                       previas=datos.filtro or (actuales.get("filtro") or []), propuestos=list(resultado.indispensables_propuestos),
                       enfoque=datos_g["enfoque_entrevista"], periodicidad=datos.sueldo_periodicidad or "", descripcion=datos.descripcion)
    salida["filtro"] = filtro
    salida["preguntas_filtro"] = pfu.web(filtro)
    salida["preguntas_filtro_whatsapp"] = []
    salida["guiones"] = {"secciones": {k: c for k, c in g["secciones"].items() if sgui.SECCIONES[k]["clase"] == "guion"},
                         "meta": g["meta"], "aplican": g["aplican"], "conservadas": g["conservadas"], "generadas": g["generadas"],
                         "huella": g["huella"], "ia": g["ia"]}
    return salida


@router.post("/preguntas-filtro/regenerar")
def regenerar_preguntas_filtro(
    datos: GenerarIn, db: Session = Depends(get_db), _: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)
):
    """2026-10-16 (Bloque 2): «Regenerar» en la vista previa de las preguntas. Conserva lo «Agregado por ti» (requisitos que
    capturó RH y sus preguntas ajustadas) y vuelve a proponer el resto desde la descripción (mínimo 3, máximo 5). No guarda
    nada: el formulario recibe la lista nueva y RH decide."""
    from ..services import guiones as sgui
    from ..services import preguntas_filtro as pfu
    from ..services import proceso as sproc

    empresa = _empresa_resuelta(db, cuenta, datos.cliente_id, datos.mostrar_cliente_candidato)
    propuestos_previos = {ia._norm_req(x) for x in datos.indispensables_propuestos or []}
    rh = [r for r in datos.indispensables() if ia._norm_req(r) not in propuestos_previos][:pfu.MAX_PREGUNTAS]
    entrada = datos.model_copy(update={"requisitos_indispensables": rh, "requisitos": ""})
    pasos_ruta = _pasos_de_formulario(db, cuenta.id, datos.proceso)
    resultado, con_ia = _generar(entrada, empresa, pide_cv=sgui.pide_cv(pasos_ruta) if pasos_ruta else None)
    previas = [q for q in datos.filtro or [] if isinstance(q, dict)
               and (q.get("base") or q.get("propia") or (q.get("indispensable") and ia._norm_req(q.get("requisito") or "") not in propuestos_previos))]
    enfoque = sproc.enfoque_de_ruta(pasos_ruta) or (datos.enfoque_entrevista if datos.enfoque_entrevista in ENFOQUES_ENTREVISTA else "profesional")
    filtro = pfu.armar(list(resultado.requisitos_indispensables), titulo=datos.titulo.strip(),
                       sueldo="" if ((datos.sueldo_texto() or "A convenir") == "A convenir" or not datos.mostrar_sueldo) else datos.sueldo_texto(),
                       ubicacion="" if (datos.modalidad or "").lower().startswith("remot") else datos.ubicacion_texto(),
                       modalidad=datos.modalidad, seniority=datos.seniority or resultado.seniority,
                       naturales={ia._norm_req(x.requisito): x.pregunta for x in resultado.preguntas_requisitos},
                       **_web_y_whatsapp(resultado),
                       previas=previas, propuestos=list(resultado.indispensables_propuestos), enfoque=enfoque,
                       periodicidad=datos.sueldo_periodicidad or "", descripcion=datos.descripcion)
    return {"ia": con_ia, "filtro": filtro, "requisitos": list(resultado.requisitos_indispensables),
            "indispensables_propuestos": list(resultado.indispensables_propuestos), "preguntas_filtro": pfu.web(filtro),
            "preguntas_requisitos": [x.model_dump() for x in resultado.preguntas_requisitos]}


def _web_y_whatsapp(resultado) -> dict:
    """2026-10-17: la pregunta del PREFILTRO WEB (Sí/No) y la del PREFILTRO WHATSAPP (continuación que nunca repite la web)
    que propuso la generación para cada requisito."""
    return {"webs": {ia._norm_req(x.requisito): x.pregunta_web for x in resultado.preguntas_requisitos if x.pregunta_web},
            "seguimientos": {ia._norm_req(x.requisito): x.pregunta_whatsapp for x in resultado.preguntas_requisitos if x.pregunta_whatsapp}}


def _pasos_de_formulario(db: Session, cuenta_id: int, proceso: Optional[dict]) -> list:
    """Ruta que eligió RH en el formulario (aún sin guardar): pasos personalizados, una plantilla o, sin elegir, la que le
    tocaría a la vacante (predeterminada de la Cuenta o la de respaldo)."""
    from ..services import proceso as sproc

    proceso = proceso or {}
    if proceso.get("quitar"):
        return []
    if proceso.get("pasos"):
        return [x for x in proceso["pasos"] if isinstance(x, dict)]
    pid = proceso.get("plantilla_id", proceso.get("plantillaId"))
    if pid:
        pl = db.query(PlantillaProceso).filter(PlantillaProceso.id == int(pid), PlantillaProceso.cuenta_id == cuenta_id).first()
        if pl is not None:
            return list(pl.pasos or [])
    try:
        return list(sproc.ruta_para(db, cuenta_id, None).get("pasos") or [])
    except Exception:  # noqa: BLE001
        return []


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
    from ..services import guiones as _sg

    _sg.guardar_preguntas_requisitos(v, [x.model_dump() for x in g.preguntas_requisitos])  # Cambio 31
    gg = dict(v.guiones or {})
    gg["indispensables_propuestos"] = list(g.indispensables_propuestos or [])  # Cambio 37
    v.guiones = gg
    v.palabras_clave = v.palabras_clave or g.palabras_clave
    v.seniority = v.seniority or g.seniority
    v.avisos_cumplimiento = g.avisos_cumplimiento
    v.texto_whatsapp = v.texto_whatsapp or g.texto_whatsapp
    v.texto_bolsa = v.texto_bolsa or g.occ.page  # compatibilidad con la forma anterior
    # 2026-10-09: los prefiltros ya no salen de aquí — los genera `services/guiones` según la ruta de la vacante
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
    # Plantillas de conversación (2026-10-09): {secciones: {entrevista_whatsapp|entrevista_avatar|llamada: guion},
    # meta: {...de /generar}} — los prefiltros viajan en preguntas_filtro / preguntas_filtro_whatsapp.
    guiones: Optional[dict] = None
    # Cambio 36: canales marcados en «Publicar en» (claves de `publicador.CANALES`); sin ellos, `plataformas` (compatibilidad)
    canales: Optional[List[str]] = None
    # Cambio 47: ubicaciones de la vacante (muchos-a-muchos, ids de `ubicaciones` de la Cuenta) y posiciones a cubrir
    ubicaciones_ids: Optional[List[int]] = None
    posiciones: Optional[int] = None


@router.post("", status_code=201)
async def crear(
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
        mostrar_sueldo=datos.mostrar_sueldo,
        estado="Publicada" if datos.publicar else "Borrador",
        requisitos=SEPARADOR_REQUISITOS.join(datos.indispensables()),
        descripcion=datos.descripcion,
        texto_whatsapp=datos.texto_whatsapp,
        texto_bolsa=datos.texto_bolsa,
        preguntas_filtro=datos.preguntas_filtro,
        preguntas_filtro_whatsapp=datos.preguntas_filtro_whatsapp,
        documentos_solicitados=normalizar_documentos_vacante(datos.documentos_solicitados),  # Cambio 25
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
    v.proceso = _proceso_nuevo(db, cuenta.id, datos.proceso)
    _enfoque_por_ruta(db, v)
    # Cambio 37: «Preguntas filtro» (entidad única) salvo que un cliente anterior mande las listas viejas por separado
    unificado = datos.filtro is not None or not (datos.preguntas_filtro or datos.preguntas_filtro_whatsapp)
    if datos.generar_si_falta and not datos.publicaciones and not datos.responsabilidades:
        from ..services import guiones as sgui

        entrada_generador = GenerarIn(**datos.model_dump(include=set(GenerarIn.model_fields) - {"proceso", "guiones_actuales", "conservar"}))
        generado, con_ia = _generar(entrada_generador, v.empresa, pide_cv=sgui.pide_cv(sgui.pasos_de_vacante(db, v)))
        _aplicar_generado(v, generado)
    if unificado:
        _guardar_guiones(v, datos.guiones, u.nombre)
    else:
        _guardar_guiones(v, datos.guiones, u.nombre, web=v.preguntas_filtro, whatsapp=v.preguntas_filtro_whatsapp)
    if con_ia is not None:
        from ..services import guiones as sgui

        # conserva lo que RH ya escribió; genera lo que falte de la ruta (con la entidad única, solo los guiones)
        guion_claves = [c for c in sgui.secciones_aplicables(sgui.tipos_de_ruta(sgui.pasos_de_vacante(db, v)))
                        if sgui.SECCIONES[c]["clase"] == "guion"]
        sgui.generar_para_vacante(db, v, u.nombre, solo=(guion_claves or ["-"]) if unificado else None)
    from ..services import guiones as _sg

    if unificado:
        _sincronizar_filtro(v, entrada=datos.filtro or [],
                            propuestos=datos.indispensables_propuestos if datos.indispensables_propuestos is not None else None)
    _validar_minimo_prefiltro(db, v, explicito=datos.guiones is not None)
    _sincronizar_ruta(db, v, publicar=datos.publicar)
    if not unificado:
        _sg.guardar_preguntas_requisitos(v, datos.preguntas_requisitos)  # Cambio 31
    db.add(v)
    db.flush()
    v.codigo = f"VAC-{1036 + v.id}"
    v.slug = _slug_unico(db, v.titulo, v.id)
    if datos.ubicaciones_ids:
        _asignar_ubicaciones(db, v, datos.ubicaciones_ids)
    if datos.posiciones is not None:
        if datos.posiciones < 1:
            raise HTTPException(400, "Las posiciones deben ser 1 o más.")
        v.posiciones = datos.posiciones
    # Fase C: estampar fecha de publicación si la vacante se publica directamente al crear.
    if datos.publicar and v.publicada_en is None:
        v.publicada_en = v.creada_en  # misma marca de tiempo que la creación
    registrar(
        db, u.nombre, "vacante_creada", "vacante", v.codigo,
        {"titulo": v.titulo, "estado": v.estado, "ia": con_ia, "plataformas": v.plataformas,
         "proceso": (v.proceso or {}).get("plantilla_nombre") or ("personalizado" if v.proceso else "")},
    )
    db.commit()
    if datos.publicar:
        # Cambio 36: «Publicar» distribuye a TODOS los canales marcados (cada uno queda «Publicada» o «Error: motivo»)
        from ..services import publicador

        await publicador.publicar(db, v, datos.canales if datos.canales is not None else (plataformas or ["Portal", "WhatsApp"]))
        registrar(db, u.nombre, "vacante_distribuida", "vacante", v.codigo, {"distribucion": publicador.vista(v)})
        db.commit()
    return _salida_rh(db, v)


def _asignar_ubicaciones(db: Session, v: Vacante, ids: List[int]) -> None:
    """Cambio 47: ubicaciones de la vacante (muchos-a-muchos). Un id ajeno a la Cuenta → 400."""
    from ..services import ubicaciones as subic

    try:
        subic.asignar(db, v, ids)
    except subic.ErrorUbicacion as e:
        raise HTTPException(e.status, e.mensaje)


def _enfoque_por_ruta(db: Session, v: Vacante) -> None:
    """Cambio 27: la plantilla de ruta define el enfoque de la entrevista — Masivos = Operativo, sin importar el seniority
    ni lo que llegue del formulario. Corporativos conserva el que eligió RH."""
    from ..services import guiones as sgui
    from ..services import proceso as sproc

    enfoque = sproc.enfoque_de_ruta(v.proceso) or sproc.enfoque_de_ruta(sgui.pasos_de_vacante(db, v))
    if enfoque:
        v.enfoque_entrevista = enfoque


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


@router.get("/canales")
def canales_conectados(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Cambio 36 — panel «Publicar en» de una vacante NUEVA: los canales conectados de la Cuenta (marcados por defecto)."""
    from ..services import publicador

    return publicador.conectados(db, cuenta=cuenta)


@router.get("/perfiles-operativos")
def perfiles_operativos(_: Usuario = Depends(usuario_actual)):
    """Biblioteca de Perfiles de la entrevista operativa (2026-10-10): 10 oficios fijos. Solo lectura — se ajustan
    por vacante (`PUT /vacantes/{codigo}/perfil-operativo`), nunca globalmente."""
    from ..services import entrevista_operativa as eop

    return {"oficios": eop.biblioteca_publica(), "secciones": [{"clave": k, "titulo": t, "origen": o} for k, t, o in eop.SECCIONES],
            "maxIndispensables": eop.MAX_INDISPENSABLES}


class PerfilOperativoIn(BaseModel):
    oficio: Optional[str] = None  # None = volver al de la biblioteca detectado por el puesto
    datos: Optional[List[str]] = None
    situacion: Optional[dict] = None
    sinonimos: Optional[List[str]] = None
    excluyentes: Optional[List[str]] = None


@router.put("/{codigo}/perfil-operativo")
def guardar_perfil_operativo(
    codigo: str, datos: PerfilOperativoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Ajusta el perfil del oficio SOLO para esta vacante (copia de la biblioteca). Los guiones ya generados quedan
    «desactualizados» hasta que RH los vuelva a generar; las entrevistas en curso conservan el suyo."""
    from ..services import guiones as sgui

    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    entrada = None if not datos.oficio else {k: x for k, x in datos.model_dump().items() if x is not None}
    try:
        sgui.guardar_perfil_operativo(v, entrada)
    except sgui.ErrorGuion as e:
        raise HTTPException(e.status, e.mensaje)
    registrar(db, u.nombre, "perfil_operativo_actualizado", "vacante", v.codigo,
              {"oficio": datos.oficio or "biblioteca", "editado": bool(((v.guiones or {}).get("perfil_operativo") or {}).get("editado"))})
    db.commit()
    return _salida_rh(db, v)


@router.get("/slug/{slug}")
def por_slug(slug: str, db: Session = Depends(get_db)):
    """Vacante para la página pública de postulación (/aplicar/[slug])."""
    v = db.query(Vacante).filter(Vacante.slug == slug).first()
    if not v:
        raise HTTPException(404, "Vacante no encontrada")
    if v.estado == "Pausada":
        raise HTTPException(410, MENSAJE_PAUSADA)  # Cambio 36: la liga muestra el aviso de pausa
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
def listar_publicas(cuenta: str = "", cuenta_id: str = "", db: Session = Depends(get_db)):
    """Bolsa de trabajo pública (/portal) — solo vacantes Publicadas, sin sesión.

    2026-09-17: `?cuenta=<slug|id>` aísla el portal a UNA Cuenta (bolsa propia de cada cliente de
    la plataforma). Sin parámetro sigue siendo la bolsa global (todas las Cuentas activas) — por eso
    una vacante «eliminada» en el dashboard (que solo ve la Cuenta actual) puede seguir viéndose en
    el portal global: es otra vacante homónima de OTRA Cuenta. Cada fila trae `cuentaId`/`cuentaSlug`.
    Una Cuenta eliminada (baja lógica) nunca publica.

    Debe declararse antes de GET /{codigo} para que 'publicas' no se interprete
    como un código de vacante.

    2026-10-06: `?cuenta_id=` es alias de `?cuenta=`. La Cuenta incluye las vacantes de TODOS sus
    Clientes (cada vacante de Cliente vive en `Vacante.cuenta_id` de la Cuenta que la opera).
    """
    cuenta = cuenta or cuenta_id
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
        # La Cuenta principal + las vacantes de sus Clientes (subcuentas), aunque alguna vacante legado
        # de un Cliente hubiera quedado con otro cuenta_id.
        clientes = select(Cliente.id).where(Cliente.cuenta_id == cu.id)
        q = q.filter(or_(Vacante.cuenta_id == cu.id, Vacante.cliente_id.in_(clientes)))
    return [_con_logo(db, _salida(db, v), v) for v in q.all()]


@router.get("/publicas/cuenta")
def cuenta_publica(cuenta: str = "", cuenta_id: str = "", db: Session = Depends(get_db)):
    """Encabezado del portal por Cuenta (nombre comercial + logo), sin sesión."""
    cu = _cuenta_publica(db, cuenta or cuenta_id) if (cuenta or cuenta_id) else None
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
    return {**_salida_rh(db, v), "homonimasOtrasCuentas": _homonimas_otras_cuentas(db, v)}


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
    mostrar_sueldo: Optional[bool] = None  # 2026-10-16 (Bloque 2)
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
    documentos_solicitados: Optional[List[dict]] = None  # Cambio 25
    preguntas_requisitos: Optional[List[dict]] = None  # Cambio 31
    filtro: Optional[List[dict]] = None  # Cambio 37: «Preguntas filtro» (entidad única)
    indispensables_propuestos: Optional[List[str]] = None  # Cambio 37
    ubicaciones_ids: Optional[List[int]] = None  # Cambio 47 ([] = quitar todas)
    posiciones: Optional[int] = None  # Cambio 47
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
    # «Ajustar ruta de esta vacante»: solo cambia esta copia; la plantilla general de la Cuenta no se toca.
    proceso: Optional[dict] = None
    guiones: Optional[dict] = None  # 2026-10-09: plantillas de conversación (mismo formato que en la creación)


@router.patch("/{codigo}")
async def actualizar(
    codigo: str, datos: ActualizarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Edición manual de RH sobre lo que generó la IA (el agente propone, RH dispone)."""
    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    congelada = liga_congelada(v)  # antes de aplicar cambios: el estado previo decide
    colaboradores_validos = _validar_relaciones(
        db, cuenta, datos.cliente_id, datos.responsable_id, datos.colaboradores_ids, None
    )
    cambios = datos.model_dump(exclude_none=True, exclude={"autor"})
    # 2026-10-09: guiones y prefiltros se guardan por el servicio de guiones (validación estricta + quién editó)
    entrada_guiones = cambios.pop("guiones", None)
    web_nuevo = cambios.pop("preguntas_filtro", None)
    whatsapp_nuevo = cambios.pop("preguntas_filtro_whatsapp", None)
    filtro_nuevo = cambios.pop("filtro", None)  # Cambio 37
    propuestos_nuevos = cambios.pop("indispensables_propuestos", None)
    if filtro_nuevo is not None:
        web_nuevo = whatsapp_nuevo = None  # la entidad única manda; las listas viejas se derivan de ella
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
        elif con_candidatos(db, v):
            raise HTTPException(409, MENSAJE_PROCESO_BLOQUEADO)  # Cambio 36: el proceso manda y no cambia con candidatos
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

    if "documentos_solicitados" in cambios:
        cambios["documentos_solicitados"] = normalizar_documentos_vacante(cambios["documentos_solicitados"])
    preguntas_req = cambios.pop("preguntas_requisitos", None)  # Cambio 31: se guardan en `guiones` (abajo)
    ubicaciones_ids = cambios.pop("ubicaciones_ids", None)  # Cambio 47
    for campo, valor in cambios.items():
        setattr(v, campo, valor)
    if ubicaciones_ids is not None:
        _asignar_ubicaciones(db, v, ubicaciones_ids)
    if "proceso" in cambios or "enfoque_entrevista" in cambios:
        _enfoque_por_ruta(db, v)
    from ..services import preguntas_filtro as _pfu

    if (web_nuevo is not None or whatsapp_nuevo is not None) and _pfu.aplica(v):
        # un cliente anterior edita las listas viejas por separado: la vacante vuelve al prefiltro de siempre
        g_leg = dict(v.guiones or {})
        g_leg.pop(_pfu.CLAVE, None)
        v.guiones = g_leg
    if entrada_guiones is not None or web_nuevo is not None or whatsapp_nuevo is not None:
        _guardar_guiones(v, entrada_guiones, u.nombre, web=web_nuevo, whatsapp=whatsapp_nuevo)
        cambios.update({k: True for k, x in (("guiones", entrada_guiones), ("preguntas_filtro", web_nuevo),
                                             ("preguntas_filtro_whatsapp", whatsapp_nuevo)) if x is not None})
    if filtro_nuevo is not None or (_pfu.aplica(v) and any(k in cambios for k in (
            "requisitos", "titulo", "sueldo", "sueldo_desde", "sueldo_hasta", "sueldo_periodicidad", "mostrar_sueldo", "modalidad", "ubicacion",
            "ubicacion_estado", "ubicacion_municipio", "seniority"))) or (propuestos_nuevos is not None and _pfu.aplica(v)):
        if any(k in cambios for k in ("sueldo_desde", "sueldo_hasta", "sueldo_moneda", "sueldo_periodicidad")):
            v.sueldo = texto_sueldo(v.sueldo_desde, v.sueldo_hasta, v.sueldo_moneda, v.sueldo_periodicidad)
        _sincronizar_filtro(v, entrada=filtro_nuevo, propuestos=propuestos_nuevos)
        cambios["filtro"] = True
    if web_nuevo is not None or "proceso" in cambios:
        _validar_minimo_prefiltro(db, v, explicito=web_nuevo is not None)
    _sincronizar_ruta(db, v, publicar=v.estado == "Publicada" and cambios.get("estado") == "Publicada")
    if (preguntas_req is not None or "requisitos" in cambios) and not _pfu.aplica(v):
        from ..services import guiones as _sg

        _sg.guardar_preguntas_requisitos(v, preguntas_req)  # Cambio 31
    # Fase 4: si tocó Estado/Municipio, el texto de ubicación se deriva
    if any(k in cambios for k in ("ubicacion_estado", "ubicacion_municipio")):
        v.ubicacion = texto_ubicacion(v.ubicacion_estado, v.ubicacion_municipio, v.ubicacion)
    # Parte 3: si tocó el sueldo estructurado, el texto se deriva (nunca se edita por separado)
    if any(k in cambios for k in ("sueldo_desde", "sueldo_hasta", "sueldo_moneda", "sueldo_periodicidad")):
        v.sueldo = texto_sueldo(v.sueldo_desde, v.sueldo_hasta, v.sueldo_moneda, v.sueldo_periodicidad)
    db.flush()
    db.refresh(v)  # recarga cliente/cuenta si cambió cliente_id
    v.empresa = nombre_empresa_candidato(v)
    if datos.titulo and not congelada:
        v.slug = _slug_unico(db, v.titulo, v.id)  # una vacante ya publicada conserva su liga original
    if v.estado == "Publicada" and v.publicada_en is None:
        v.publicada_en = datetime.now(timezone.utc)  # publicada por edición de estado: desde aquí la liga queda fija
    if datos.publicaciones:
        v.texto_bolsa = (datos.publicaciones.get("occ") or {}).get("page", v.texto_bolsa)

    registrar(db, u.nombre, "vacante_editada", "vacante", v.codigo, {"campos": sorted(cambios)})
    db.commit()
    if v.estado == "Publicada" and set(cambios) - {"estado", "responsable_id", "colaboradores_ids"}:
        # Cambio 36: «Editar» de una vacante publicada se SINCRONIZA en las plataformas donde está
        from ..services import publicador

        await publicador.a_todos(db, v, "actualizar")
        db.commit()
    return _salida_rh(db, v)


class RegenerarIn(BaseModel):
    notas: str = ""
    sobrescribir_guiones: bool = False  # 2026-10-09: por defecto los guiones editados a mano se conservan


@router.post("/{codigo}/regenerar")
def regenerar(
    codigo: str, datos: RegenerarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Vuelve a generar todo el contenido de una vacante existente con los datos ya capturados."""
    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    ficha = ia.FichaVacante(
        titulo=v.titulo, area=v.area, seniority=v.seniority or "", ubicacion=v.ubicacion, modalidad=v.modalidad,
        sueldo_texto=(v.sueldo or "") if v.mostrar_sueldo is not False else SUELDO_COMPETITIVO, empresa=v.empresa or nombre_empresa_candidato(v),
        descripcion_breve=(datos.notas or "").strip(), requisitos_indispensables=requisitos_lista(v.requisitos),
        requisitos_deseables=list(v.requisitos_deseables or []), beneficios=list(v.beneficios or []),
        mostrar_sueldo=v.mostrar_sueldo is not False,
    )
    from ..services import guiones as _sgui

    ficha.pide_cv = _sgui.pide_cv(_sgui.pasos_de_vacante(db, v))
    generado, con_ia = ia.generar_vacante(ficha)
    # regenerar = volver a redactar: se limpian los textos generados para que _aplicar_generado los
    # rellene, pero lo capturado (requisitos, beneficios, sueldo, seniority) se respeta igual.
    v.resumen = v.perfil_ideal = v.texto_whatsapp = v.texto_bolsa = ""
    v.responsabilidades, v.palabras_clave = [], []
    _aplicar_generado(v, generado)
    from ..services import guiones as sgui

    from ..services import preguntas_filtro as _pfu

    unificado = _pfu.aplica(v)
    guion_claves = [c for c in sgui.secciones_aplicables(sgui.tipos_de_ruta(sgui.pasos_de_vacante(db, v)))
                    if sgui.SECCIONES[c]["clase"] == "guion"]
    g = sgui.generar_para_vacante(db, v, u.nombre, sobrescribir_editadas=datos.sobrescribir_guiones,
                                  solo=(guion_claves or ["-"]) if unificado else None)
    if unificado:
        _sincronizar_filtro(v, propuestos=list(generado.indispensables_propuestos or []), **_web_y_whatsapp(generado))
    registrar(db, u.nombre, "vacante_regenerada", "vacante", v.codigo,
              {"ia": con_ia, "notas": datos.notas, "guiones": g["generadas"], "guiones_conservados": g["conservadas"]})
    db.commit()
    return {"ia": con_ia, **_salida_rh(db, v)}


class GenerarGuionesIn(BaseModel):
    secciones: List[str] = []  # vacío = todas las de la ruta
    # «Volver a generar»: si alguna sección tiene ediciones de RH, sin confirmación explícita → 409 (nunca se pisan solas)
    sobrescribir_editadas: Optional[bool] = None


@router.post("/{codigo}/guiones/generar")
def generar_guiones_vacante(
    codigo: str, datos: GenerarGuionesIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Genera (o vuelve a generar) las plantillas de conversación de la vacante con sus datos actuales, SOLO para las
    actividades de su ruta. Con ediciones manuales en las secciones pedidas exige `sobrescribir_editadas` (true = se
    reemplazan; false = se conservan y se genera lo demás)."""
    from ..services import guiones as sgui

    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    aplican = sgui.secciones_aplicables(sgui.tipos_de_ruta(sgui.pasos_de_vacante(db, v)))
    pedidas = [c for c in aplican if not datos.secciones or c in datos.secciones]
    if not pedidas:
        raise HTTPException(409, "La ruta de esta vacante no tiene actividades con guion (prefiltros, entrevistas o llamada).")
    editadas = sgui.editadas(v, pedidas)
    if editadas and datos.sobrescribir_editadas is None:
        titulos = ", ".join(f"«{sgui.SECCIONES[c]['titulo']}»" for c in editadas)
        raise HTTPException(409, f"Hay ediciones manuales en {titulos}. Confirma si quieres sobrescribirlas o conservarlas.")
    try:
        g = sgui.generar_para_vacante(db, v, u.nombre, sobrescribir_editadas=bool(datos.sobrescribir_editadas), solo=pedidas)
    except sgui.ErrorGuion as e:
        raise HTTPException(e.status, e.mensaje)
    registrar(db, u.nombre, "guiones_generados", "vacante", v.codigo,
              {"generadas": g["generadas"], "conservadas": g["conservadas"], "ia": g["ia"]})
    db.commit()
    return {**_salida_rh(db, v), "generadas": g["generadas"], "conservadas": g["conservadas"]}


# ------------------------------------------------------------
# Distribución
# ------------------------------------------------------------


class PublicarIn(BaseModel):
    plataformas: List[str] = ["Portal", "WhatsApp"]
    # Cambio 36: canales marcados en «Publicar en» (claves de `publicador.CANALES`); manda sobre `plataformas`
    canales: Optional[List[str]] = None


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
    from ..services import publicador

    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    _sincronizar_ruta(db, v, publicar=True)
    canales = [c for c in (publicador.clave_canal(x) for x in (datos.canales if datos.canales is not None else datos.plataformas)) if c]
    if not canales:
        raise HTTPException(400, f"Elige al menos un canal: {', '.join(c['nombre'] for c in publicador.CANALES.values())}")
    if not (v.publicaciones or v.descripcion):
        raise HTTPException(409, "La vacante no tiene contenido. Genera la publicación antes de distribuirla.")

    v.estado = "Publicada"
    if not v.slug:
        v.slug = _slug_unico(db, v.titulo, v.id)
    # Fase C: estampar fecha de primera publicación (solo la primera vez; no sobreescribir en
    # reaperturas — la fecha que importa es cuándo salió por primera vez al público).
    if v.publicada_en is None:
        v.publicada_en = datetime.now(timezone.utc)
    db.flush()
    # Cambio 36: distribuye a TODOS los canales marcados; cada uno queda «Publicada» (con liga ?canal=) o «Error: motivo»
    await publicador.publicar(db, v, canales)
    registrar(db, u.nombre, "vacante_publicada", "vacante", v.codigo, {"plataformas": v.plataformas, "distribucion": publicador.vista(v)})
    db.commit()
    # 2026-09-19: descripción de la vacante (HTML corporativo) al Cliente y al responsable; nunca bloquea.
    envios = await notificaciones.notificar_vacante_publicada(db, v, u.nombre)
    db.commit()
    return {**_salida(db, v), "notificaciones": envios}


@router.post("/{codigo}/cerrar")
async def cerrar(
    codigo: str, datos: PublicarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Cerrar (definitivo): la vacante se retira de todos los canales donde estaba."""
    from ..services import publicador

    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    await publicador.a_todos(db, v, "cerrar")
    v.estado = "Cerrada"
    v.plataformas = []
    registrar(db, u.nombre, "vacante_cerrada", "vacante", v.codigo, {"distribucion": publicador.vista(v)})
    db.commit()
    return _salida(db, v)


@router.post("/{codigo}/pausar")
async def pausar(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                 cuenta: Cuenta = Depends(cuenta_actual)):
    """Cambio 36 — Pausar: la liga muestra «en pausa» (no recibe postulaciones), sale del portal y de WhatsApp y se retira de
    las plataformas. Los candidatos que ya tiene siguen su proceso. «Reanudar» la vuelve a publicar donde estaba."""
    from ..services import publicador

    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    if v.estado != "Publicada":
        raise HTTPException(409, "Solo se puede pausar una vacante publicada.")
    await publicador.a_todos(db, v, "pausar")
    v.estado = "Pausada"
    registrar(db, u.nombre, "vacante_pausada", "vacante", v.codigo, {"distribucion": publicador.vista(v)})
    db.commit()
    return _salida_rh(db, v)


@router.post("/{codigo}/reanudar")
async def reanudar(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                   cuenta: Cuenta = Depends(cuenta_actual)):
    from ..services import publicador

    v = _no_eliminada(_por_codigo(db, codigo, cuenta.id))
    if v.estado != "Pausada":
        raise HTTPException(409, "Solo se puede reanudar una vacante en pausa.")
    v.estado = "Publicada"
    await publicador.a_todos(db, v, "reanudar")
    registrar(db, u.nombre, "vacante_reanudada", "vacante", v.codigo, {"distribucion": publicador.vista(v)})
    db.commit()
    return _salida_rh(db, v)


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
