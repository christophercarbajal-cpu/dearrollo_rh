"""Plantillas de vacante — reutilizables, Generales de la Cuenta o de un Cliente específico, sin
nivel intermedio (Fase B, reestructuración multi-cuenta, punto 11)."""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import masivo
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import (
    CAMPOS_PLANTILLA, ENFOQUES_ENTREVISTA, normalizar_documentos_vacante, PERIODICIDADES_SUELDO, Cliente, Cuenta, Plantilla, Usuario, Vacante, registrar,
    texto_sueldo, texto_ubicacion,
)

router = APIRouter(prefix="/plantillas", tags=["plantillas"])


def _plantilla_dict(p: Plantilla) -> dict:
    return {
        "id": p.id,
        "nombre": p.nombre,
        "clienteId": p.cliente_id,
        "clienteNombre": p.cliente.nombre if p.cliente else None,
        "activa": p.activa,
        "titulo": p.titulo,
        "area": p.area,
        "ubicacion": p.ubicacion,
        "modalidad": p.modalidad,
        "sueldo": p.sueldo,
        "sueldoDesde": p.sueldo_desde,  # Parte 3
        "sueldoHasta": p.sueldo_hasta,
        "sueldoMoneda": p.sueldo_moneda or "MXN",
        "sueldoPeriodicidad": p.sueldo_periodicidad or "",
        "requisitos": p.requisitos,
        "descripcion": p.descripcion,
        "resumen": p.resumen,
        "perfilIdeal": p.perfil_ideal,
        "responsabilidades": p.responsabilidades or [],
        "requisitosDeseables": p.requisitos_deseables or [],
        "beneficios": p.beneficios or [],
        "palabrasClave": p.palabras_clave or [],
        "seniority": p.seniority,
        "avisosCumplimiento": p.avisos_cumplimiento or [],
        "preguntasFiltro": p.preguntas_filtro or [],  # = "evaluaciones" (ver spec Fase B)
        "preguntasFiltroWhatsapp": p.preguntas_filtro_whatsapp or [],  # Fase 4
        "documentosSolicitados": normalizar_documentos_vacante(p.documentos_solicitados or []),  # Cambio 25
        "ubicacionEstado": p.ubicacion_estado or "",
        "ubicacionMunicipio": p.ubicacion_municipio or "",
        "textoWhatsapp": p.texto_whatsapp,
        "textoBolsa": p.texto_bolsa,
        "enfoqueEntrevista": p.enfoque_entrevista or "profesional",
        "creadoPor": p.creado_por,
        "creada": p.creada_en.isoformat(),
        "actualizada": (p.actualizada_en or p.creada_en).isoformat(),
    }


def _por_id(db: Session, plantilla_id: int, cuenta_id: int) -> Plantilla:
    p = db.query(Plantilla).filter(Plantilla.id == plantilla_id, Plantilla.cuenta_id == cuenta_id).first()
    if not p:
        raise HTTPException(404, "Plantilla no encontrada")
    return p


def _validar_cliente(db: Session, cuenta_id: int, cliente_id: Optional[int]) -> None:
    if cliente_id is None:
        return
    existe = db.query(Cliente).filter(Cliente.id == cliente_id, Cliente.cuenta_id == cuenta_id).first()
    if not existe:
        raise HTTPException(400, "El Cliente indicado no existe en esta Cuenta.")


@router.get("")
def listar(
    cliente_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: Usuario = Depends(usuario_actual),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Sin `cliente_id`: todas las plantillas activas de la Cuenta (pantalla de gestión). Con
    `cliente_id`: las de ese Cliente primero, luego las generales — el orden de sugerencia exacto
    que pide el punto 11 al crear una vacante con Cliente ya elegido."""
    q = db.query(Plantilla).filter(Plantilla.cuenta_id == cuenta.id, Plantilla.activa.is_(True))
    if cliente_id is None:
        return [_plantilla_dict(p) for p in q.order_by(Plantilla.nombre).all()]
    filas = q.filter((Plantilla.cliente_id == cliente_id) | (Plantilla.cliente_id.is_(None))).all()
    filas.sort(key=lambda p: (p.cliente_id != cliente_id, p.nombre))
    return [_plantilla_dict(p) for p in filas]


@router.get("/{plantilla_id}")
def detalle(
    plantilla_id: int, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)
):
    return _plantilla_dict(_por_id(db, plantilla_id, cuenta.id))


class PlantillaIn(BaseModel):
    nombre: str
    cliente_id: Optional[int] = None
    titulo: str = ""
    area: str = ""
    ubicacion: str = ""
    modalidad: str = "Presencial"
    sueldo: str = "A convenir"
    sueldo_desde: Optional[int] = None  # Parte 3
    sueldo_hasta: Optional[int] = None
    sueldo_moneda: str = "MXN"
    sueldo_periodicidad: str = ""
    requisitos: str = ""
    descripcion: str = ""
    resumen: str = ""
    perfil_ideal: str = ""
    responsabilidades: List[str] = []
    requisitos_deseables: List[str] = []
    beneficios: List[str] = []
    palabras_clave: List[str] = []
    seniority: str = ""
    avisos_cumplimiento: List[str] = []
    preguntas_filtro: List[dict] = []
    preguntas_filtro_whatsapp: List[dict] = []  # Fase 4
    documentos_solicitados: List[dict] = []  # Cambio 25
    ubicacion_estado: str = ""  # Fase 4
    ubicacion_municipio: str = ""
    texto_whatsapp: str = ""
    texto_bolsa: str = ""
    enfoque_entrevista: str = "profesional"


def _crear_plantilla(db: Session, cuenta: Cuenta, u: Usuario, datos: PlantillaIn) -> Plantilla:
    """Validaciones de POST /plantillas — compartidas con la carga masiva (Fase 2). No hace commit."""
    if not datos.nombre.strip():
        raise HTTPException(400, "El nombre de la plantilla es obligatorio.")
    _validar_cliente(db, cuenta.id, datos.cliente_id)
    if datos.enfoque_entrevista not in ENFOQUES_ENTREVISTA:
        raise HTTPException(400, f"Enfoque de entrevista inválido. Usa uno de: {', '.join(ENFOQUES_ENTREVISTA)}")

    if datos.sueldo_periodicidad and datos.sueldo_periodicidad not in PERIODICIDADES_SUELDO:
        raise HTTPException(400, f"Periodicidad de sueldo inválida. Usa una de: {', '.join(PERIODICIDADES_SUELDO)}")
    campos = datos.model_dump()
    campos["nombre"] = campos["nombre"].strip()
    if datos.sueldo_periodicidad or datos.sueldo_desde or datos.sueldo_hasta:
        campos["sueldo"] = texto_sueldo(datos.sueldo_desde, datos.sueldo_hasta, datos.sueldo_moneda, datos.sueldo_periodicidad)
    campos["ubicacion"] = texto_ubicacion(datos.ubicacion_estado, datos.ubicacion_municipio, datos.ubicacion)
    campos["documentos_solicitados"] = normalizar_documentos_vacante(campos.get("documentos_solicitados"))
    p = Plantilla(cuenta_id=cuenta.id, creado_por=u.nombre, **campos)
    db.add(p)
    db.flush()
    registrar(
        db, u.nombre, "plantilla_creada", "plantilla", str(p.id),
        {"nombre": p.nombre, "cliente_id": p.cliente_id},
    )
    return p


@router.post("", status_code=201)
def crear(
    datos: PlantillaIn,
    db: Session = Depends(get_db),
    u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    p = _crear_plantilla(db, cuenta, u, datos)
    db.commit()
    return _plantilla_dict(p)


# Columnas de lista en la carga masiva: valores separados por « | » o «;».
_COLUMNAS_LISTA = ("responsabilidades", "requisitos_deseables", "beneficios", "palabras_clave", "avisos_cumplimiento")


@router.post("/masivo", status_code=201)
async def crear_masivo(
    archivo: UploadFile = File(...), db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Fase 2 (2026-09-15) — alta masiva de Plantillas desde CSV/Excel. Columnas = CAMPOS_PLANTILLA en
    snake_case + `nombre` + `cliente` (nombre del Cliente de la Cuenta; vacío = General). Listas
    separadas por « | »; sueldo estructurado (sueldo_desde/hasta/moneda/periodicidad) → el texto se
    deriva con texto_sueldo, nunca se captura aparte. Mismas reglas que POST /plantillas."""
    filas = await masivo.leer_tabla(archivo)
    resultado = masivo.Resultado()
    clientes = {c.nombre.strip().lower(): c.id for c in db.query(Cliente).filter(Cliente.cuenta_id == cuenta.id).all()}
    for numero, fila in filas:
        if not fila.get("nombre"):
            continue
        sp = db.begin_nested()
        try:
            cliente_id = None
            nombre_cliente = fila.get("cliente", "").strip()
            if nombre_cliente:
                cliente_id = clientes.get(nombre_cliente.lower())
                if cliente_id is None:
                    raise HTTPException(400, f"El Cliente «{nombre_cliente}» no existe en esta Cuenta.")
            campos = {k: fila.get(k, "") for k in ("titulo", "area", "ubicacion", "requisitos", "descripcion", "resumen", "perfil_ideal", "seniority", "texto_whatsapp", "texto_bolsa")}
            campos = {k: v for k, v in campos.items() if v}
            for k in _COLUMNAS_LISTA:
                if fila.get(k):
                    campos[k] = masivo.lista(fila[k])
            for k in ("sueldo_desde", "sueldo_hasta"):
                if fila.get(k):
                    campos[k] = masivo.entero(fila[k], k)
            if fila.get("modalidad"):
                campos["modalidad"] = fila["modalidad"]
            if fila.get("sueldo_moneda"):
                campos["sueldo_moneda"] = fila["sueldo_moneda"].upper()
            if fila.get("sueldo_periodicidad"):
                campos["sueldo_periodicidad"] = fila["sueldo_periodicidad"].lower()
            if fila.get("enfoque_entrevista"):
                campos["enfoque_entrevista"] = fila["enfoque_entrevista"].lower()
            p = _crear_plantilla(db, cuenta, u, PlantillaIn(nombre=fila["nombre"], cliente_id=cliente_id, **campos))
            sp.commit()
            resultado.ok(numero, {"id": p.id, "nombre": p.nombre, "cliente_id": p.cliente_id})
        except HTTPException as ex:
            sp.rollback()
            resultado.error(numero, str(ex.detail), fila.get("nombre", ""))
        except (ValueError, TypeError) as ex:  # pydantic: tipo inválido en alguna columna
            sp.rollback()
            resultado.error(numero, f"dato inválido: {str(ex)[:160]}", fila.get("nombre", ""))
    registrar(db, u.nombre, "plantillas_carga_masiva", "cuenta", str(cuenta.id), {"archivo": archivo.filename, **resultado.resumen()})
    db.commit()
    return resultado.dict()


@router.get("/masivo/plantilla")
def plantilla_masivo_plantillas(_: Usuario = Depends(usuario_actual)):
    return masivo.csv_plantilla(
        "plantillas",
        ["nombre", "cliente", "titulo", "area", "ubicacion", "modalidad", "sueldo_desde", "sueldo_hasta", "sueldo_moneda", "sueldo_periodicidad",
         "seniority", "requisitos", "requisitos_deseables", "responsabilidades", "beneficios", "descripcion", "enfoque_entrevista"],
        [["Cajero base", "", "Cajero(a) de sucursal", "Operaciones", "Ciudad de México", "Presencial", "9000", "11000", "MXN", "mensual",
          "Junior", "Secundaria terminada · Manejo de efectivo", "Experiencia en retail | Inglés básico", "Cobro en caja | Arqueo diario", "Vales de despensa | Seguro de vida", "Atención en caja de sucursal.", "profesional"]],
    )


class ActualizarIn(BaseModel):
    nombre: Optional[str] = None
    cliente_id: Optional[int] = None
    activa: Optional[bool] = None
    titulo: Optional[str] = None
    area: Optional[str] = None
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
    avisos_cumplimiento: Optional[List[str]] = None
    preguntas_filtro: Optional[List[dict]] = None
    preguntas_filtro_whatsapp: Optional[List[dict]] = None  # Fase 4
    documentos_solicitados: Optional[List[dict]] = None  # Cambio 25
    ubicacion_estado: Optional[str] = None  # Fase 4
    ubicacion_municipio: Optional[str] = None
    texto_whatsapp: Optional[str] = None
    texto_bolsa: Optional[str] = None
    enfoque_entrevista: Optional[str] = None


@router.patch("/{plantilla_id}")
def actualizar(
    plantilla_id: int,
    datos: ActualizarIn,
    db: Session = Depends(get_db),
    u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    p = _por_id(db, plantilla_id, cuenta.id)
    if datos.cliente_id is not None:
        _validar_cliente(db, cuenta.id, datos.cliente_id)
    if datos.enfoque_entrevista is not None and datos.enfoque_entrevista not in ENFOQUES_ENTREVISTA:
        raise HTTPException(400, f"Enfoque de entrevista inválido. Usa uno de: {', '.join(ENFOQUES_ENTREVISTA)}")

    if datos.sueldo_periodicidad is not None and datos.sueldo_periodicidad not in PERIODICIDADES_SUELDO:
        raise HTTPException(400, f"Periodicidad de sueldo inválida. Usa una de: {', '.join(PERIODICIDADES_SUELDO)}")
    cambios = datos.model_dump(exclude_none=True)
    if "documentos_solicitados" in cambios:
        cambios["documentos_solicitados"] = normalizar_documentos_vacante(cambios["documentos_solicitados"])
    for campo, valor in cambios.items():
        setattr(p, campo, valor.strip() if campo == "nombre" and isinstance(valor, str) else valor)
    if any(k in cambios for k in ("sueldo_desde", "sueldo_hasta", "sueldo_moneda", "sueldo_periodicidad")):
        p.sueldo = texto_sueldo(p.sueldo_desde, p.sueldo_hasta, p.sueldo_moneda, p.sueldo_periodicidad)
    if any(k in cambios for k in ("ubicacion_estado", "ubicacion_municipio")):
        p.ubicacion = texto_ubicacion(p.ubicacion_estado, p.ubicacion_municipio, p.ubicacion)

    if cambios:
        registrar(db, u.nombre, "plantilla_editada", "plantilla", str(p.id), {"campos": sorted(cambios)})
        db.commit()
    return _plantilla_dict(p)


@router.delete("/{plantilla_id}")
def eliminar(
    plantilla_id: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)
):
    """No borra — desactiva (`activa=False`). Una Vacante ya creada desde esta plantilla conserva
    su `plantilla_id` para trazabilidad aunque ya no se sugiera para vacantes nuevas."""
    p = _por_id(db, plantilla_id, cuenta.id)
    p.activa = False
    registrar(db, u.nombre, "plantilla_desactivada", "plantilla", str(p.id), {})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------
# Punto 11 — Duplicar y "Guardar como plantilla" desde una vacante
# ------------------------------------------------------------


def _copiar_contenido(origen, destino) -> None:
    """Copia los campos compartidos Vacante/Plantilla (CAMPOS_PLANTILLA) — única lista, para que
    duplicar, guardar-desde-vacante y (en el frontend) usar-plantilla nunca diverjan."""
    for campo in CAMPOS_PLANTILLA:
        valor = getattr(origen, campo)
        setattr(destino, campo, list(valor) if isinstance(valor, list) else valor)


@router.post("/{plantilla_id}/duplicar", status_code=201)
def duplicar(
    plantilla_id: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)
):
    """Acción «Duplicar» del listado: copia activa con el mismo alcance (General/Cliente)."""
    origen = _por_id(db, plantilla_id, cuenta.id)
    copia = Plantilla(cuenta_id=cuenta.id, cliente_id=origen.cliente_id, nombre=f"Copia de {origen.nombre}"[:150], creado_por=u.nombre)
    _copiar_contenido(origen, copia)
    db.add(copia)
    db.flush()
    registrar(db, u.nombre, "plantilla_duplicada", "plantilla", str(copia.id), {"origen": origen.id, "nombre": copia.nombre})
    db.commit()
    return _plantilla_dict(copia)


class DesdeVacanteIn(BaseModel):
    nombre: str
    cliente_id: Optional[int] = None  # None = General de la Cuenta


@router.post("/desde-vacante/{codigo}", status_code=201)
def desde_vacante(
    codigo: str, datos: DesdeVacanteIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Acción «Guardar como plantilla» desde una vacante existente: el servidor copia los campos
    compartidos (el frontend ya no arma la copia a mano)."""
    if not datos.nombre.strip():
        raise HTTPException(400, "El nombre de la plantilla es obligatorio.")
    v = db.query(Vacante).filter(Vacante.codigo == codigo, Vacante.cuenta_id == cuenta.id).first()
    if not v:
        raise HTTPException(404, "Vacante no encontrada")
    _validar_cliente(db, cuenta.id, datos.cliente_id)
    p = Plantilla(cuenta_id=cuenta.id, cliente_id=datos.cliente_id, nombre=datos.nombre.strip(), creado_por=u.nombre)
    _copiar_contenido(v, p)
    db.add(p)
    db.flush()
    registrar(db, u.nombre, "plantilla_creada", "plantilla", str(p.id), {"nombre": p.nombre, "cliente_id": p.cliente_id, "vacante": v.codigo})
    db.commit()
    return _plantilla_dict(p)
