"""Módulo 2 · Contratación e integración (3.11) — expedientes, documentos, recordatorios y alta HITL.

El expediente nace desde el Módulo 1 (`/candidatos/{codigo}/seleccionar`). Aquí el
agente recibe los documentos, los valida con IA y da seguimiento; el alta la
autoriza siempre una persona de RH.
"""

import re
import unicodedata
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import NIVELES_RECORDATORIO, Candidato, Colaborador, Cuenta, Documento, Expediente, Postulacion, Usuario, registrar
from ..services.recordatorios import registrar_recordatorio_enviado
from ..serial import colaborador_dict, expediente_dict, nombre_empresa_candidato
from ..services import archivos as fs
from ..services import ia
from ..services import notificaciones
from ..services.pdf import pdf_carta_intencion, pdf_contrato
from ..services import plantillas_correo
from ..services.correo import enviar_correo
from ..services.mensajeria import de_cuenta
from ..services.whatsapp import enviar_mensaje
from ..services.notificaciones import TZ_MEXICO, NotificarIn, override_de
from ..services.configuracion import modo_prueba_activo, puede_forzar_prueba

router = APIRouter(prefix="/contratacion", tags=["contratacion"])

ESTADOS_DOC = ("recibido", "rechazado", "revision", "pendiente")


def _expediente(db: Session, exp_id: int, cuenta_id: int) -> Expediente:
    """El Expediente no tiene columna cuenta_id propia — se resuelve por join contra el
    Candidato dueño, igual que el resto de las entidades que cuelgan de él."""
    e = (
        db.query(Expediente)
        .join(Candidato, Expediente.candidato_id == Candidato.id)
        .filter(Expediente.id == exp_id, Candidato.cuenta_id == cuenta_id)
        .first()
    )
    if not e:
        raise HTTPException(404, "Expediente no encontrado")
    return e


def _documento(e: Expediente, tipo: str) -> Documento:
    doc = next((d for d in e.documentos if d.tipo.lower() == tipo.strip().lower()), None)
    if not doc:
        raise HTTPException(404, f"El expediente no requiere el documento '{tipo}'")
    return doc


def _sincronizar_estado(e: Expediente) -> None:
    """El expediente pasa a 'completo' solo cuando no falta ningún obligatorio."""
    if e.estado != "alta":
        e.estado = "completo" if e.progreso == 100 else "integracion"


@router.get("/expedientes")
def listar(
    estado: Optional[str] = None,
    cerrados: bool = False,
    db: Session = Depends(get_db),
    _: Usuario = Depends(usuario_actual),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    # 2026-09-15: el tablero de Onboarding solo muestra expedientes VIVOS — nunca de personas eliminadas
    # (baja lógica) ni de postulaciones cerradas (descartado, vacante eliminada, etc.); los ya dados de
    # alta (postulación cerrada como `contratado`) sí se conservan para ver «Alta completada».
    q = (
        db.query(Expediente)
        .join(Candidato, Expediente.candidato_id == Candidato.id)
        .outerjoin(Postulacion, Expediente.postulacion_id == Postulacion.id)
        .filter(
            Candidato.cuenta_id == cuenta.id,
            Candidato.eliminado_en.is_(None),
            or_(Postulacion.id.is_(None), Postulacion.activa.is_(True), Postulacion.motivo_cierre == "contratado"),
            # 2026-10-06: un expediente abierto ANTES de Contratación (documentos de la ruta Masivos) no es Onboarding
            or_(Postulacion.id.is_(None), Postulacion.etapa.in_(("Contratación", "Onboarding")), Postulacion.motivo_cierre == "contratado"),
        )
        .order_by(Expediente.id)
    )
    if estado:
        q = q.filter(Expediente.estado == estado)
    # Onboarding v2 (Fase 3): los Onboardings CERRADOS salen del tablero (salvo `cerrados=true`)
    if not cerrados:
        q = q.filter(Expediente.onboarding_cerrado_en.is_(None))
    expedientes = q.all()
    return [{**expediente_dict(e), "onboarding": r} for e, r in zip(expedientes, _resumenes_onboarding(db, expedientes))]


def _resumenes_onboarding(db: Session, expedientes: List[Expediente]) -> List[Optional[dict]]:
    """Resumen de tareas/documentos por expediente con UNA consulta de tareas (sin N+1). Si las tablas de los
    módulos no están disponibles, el tablero sigue funcionando sin él."""
    from ..models import TareaOnboarding
    from ..services import onboarding as onb

    try:
        ids = [e.id for e in expedientes]
        por_exp: dict = {}
        if ids:
            for t in db.query(TareaOnboarding).filter(TareaOnboarding.expediente_id.in_(ids)).all():
                por_exp.setdefault(t.expediente_id, []).append(t)
        return [onb.resumen_tablero(e, sorted(por_exp.get(e.id, []), key=lambda t: t.id)) for e in expedientes]
    except Exception:  # noqa: BLE001
        db.rollback()
        return [None for _ in expedientes]


@router.get("/metricas")
def metricas(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Resumen del módulo 2 para el tablero — cierra el ciclo con el módulo 1."""
    todos = (
        db.query(Expediente)
        .join(Candidato, Expediente.candidato_id == Candidato.id)
        .filter(Candidato.cuenta_id == cuenta.id)
        .all()
    )
    docs_pendientes = sum(len(e.pendientes) for e in todos)
    por_revisar = sum(len(e.por_revisar) for e in todos)
    completos = [e for e in todos if e.progreso == 100]
    return {
        "expedientes": len(todos),
        "en_integracion": sum(1 for e in todos if e.estado == "integracion"),
        "completos": len(completos),
        "altas": sum(1 for e in todos if e.estado == "alta"),
        "documentos_pendientes": docs_pendientes,
        "documentos_por_revisar": por_revisar,
        "progreso_promedio": round(sum(e.progreso for e in todos) / len(todos)) if todos else 0,
        "listos_para_alta": [
            {"expedienteId": e.id, "nombre": e.candidato.nombre if e.candidato else "", "puesto": e.puesto}
            for e in completos
            if e.estado != "alta"
        ],
    }


@router.get("/expedientes/{exp_id}")
def detalle(
    exp_id: int, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)
):
    return expediente_dict(_expediente(db, exp_id, cuenta.id))


# ------------------------------------------------------------
# Preparación de ingreso (Onboarding, bloque 4) — contrato, alta administrativa, equipo/accesos
# ------------------------------------------------------------

ESTADOS_CONTRATO = ("Pendiente", "Firmado")
ESTADOS_ALTA_ADMIN = ("Pendiente", "Realizada")
ESTADOS_EQUIPO_ACCESOS = ("Pendiente", "Listo", "No aplica")


class PreparacionIn(BaseModel):
    contrato: Optional[str] = None
    alta_administrativa: Optional[str] = None
    equipo_accesos: Optional[str] = None
    # Fase 3: «recordar hasta» (fecha ISO). "" = quitar los recordatorios automáticos.
    documentos_hasta: Optional[str] = None


@router.patch("/expedientes/{exp_id}/preparacion")
def actualizar_preparacion(
    exp_id: int, datos: PreparacionIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Bloque 4 de Onboarding — checklist de preparación de ingreso, independiente del
    expediente documental (bloque 3)."""
    e = _expediente(db, exp_id, cuenta.id)
    if e.estado == "alta":
        raise HTTPException(409, "El expediente ya fue dado de alta; no admite cambios.")
    from ..services import onboarding as onb

    tareas = onb.tareas_de(db, e) if (datos.contrato or datos.alta_administrativa or datos.equipo_accesos) else []
    if tareas:
        # Onboarding v2 (2026-09-28): las TAREAS son la fuente de verdad; estos campos se derivan de ellas.
        _preparacion_por_tareas(db, e, tareas, datos, u)

    cambios = []
    if datos.contrato is not None and not tareas:
        if datos.contrato not in ESTADOS_CONTRATO:
            raise HTTPException(400, f"contrato inválido. Usa uno de: {', '.join(ESTADOS_CONTRATO)}")
        e.contrato = datos.contrato
        cambios.append("contrato")
    if datos.alta_administrativa is not None and not tareas:
        if datos.alta_administrativa not in ESTADOS_ALTA_ADMIN:
            raise HTTPException(400, f"alta_administrativa inválida. Usa una de: {', '.join(ESTADOS_ALTA_ADMIN)}")
        e.alta_administrativa = datos.alta_administrativa
        cambios.append("alta_administrativa")
    if datos.equipo_accesos is not None and not tareas:
        if datos.equipo_accesos not in ESTADOS_EQUIPO_ACCESOS:
            raise HTTPException(400, f"equipo_accesos inválido. Usa uno de: {', '.join(ESTADOS_EQUIPO_ACCESOS)}")
        e.equipo_accesos = datos.equipo_accesos
        cambios.append("equipo_accesos")
    if datos.documentos_hasta is not None:
        if not datos.documentos_hasta.strip():
            e.documentos_hasta = None
        else:
            try:
                # medianoche de ese día en México → UTC (el cron respeta el final del día en México)
                e.documentos_hasta = datetime.fromisoformat(datos.documentos_hasta[:10]).replace(tzinfo=TZ_MEXICO).astimezone(timezone.utc)
            except ValueError:
                raise HTTPException(400, "documentos_hasta inválida (usa ISO: 2026-10-15)")
            if e.documentos_hasta.astimezone(TZ_MEXICO).date() < datetime.now(TZ_MEXICO).date():
                raise HTTPException(400, "La fecha límite de documentos ya pasó.")
        e.documentos_vencidos_avisado = False
        cambios.append("documentos_hasta")

    if cambios:
        registrar(
            db, u.nombre, "preparacion_ingreso_actualizada", "expediente", str(e.id),
            {"campos": cambios, "correo_rh": u.correo},
        )
    db.commit()
    return expediente_dict(e)


def _preparacion_por_tareas(db: Session, e: Expediente, tareas: list, datos: "PreparacionIn", u: Usuario) -> None:
    """Traduce el checklist viejo a las tareas (fuente de verdad) y deriva de vuelta los campos."""
    from ..services import onboarding as onb

    por_clave = {t.clave: t for t in tareas if t.fija}
    if datos.contrato is not None:
        if datos.contrato not in ESTADOS_CONTRATO:
            raise HTTPException(400, f"contrato inválido. Usa uno de: {', '.join(ESTADOS_CONTRATO)}")
        t = por_clave.get("contrato_firmado")
        if t and datos.contrato == "Firmado" and t.estado != "realizada":
            raise HTTPException(409, "El contrato se marca firmado al cargar el PDF firmado («Cargar contrato firmado»).")
        if t and datos.contrato == "Pendiente" and t.estado == "realizada":
            raise HTTPException(409, "El contrato firmado ya está cargado; reemplázalo desde las tareas del Onboarding.")
    if datos.alta_administrativa is not None:
        if datos.alta_administrativa not in ESTADOS_ALTA_ADMIN:
            raise HTTPException(400, f"alta_administrativa inválida. Usa una de: {', '.join(ESTADOS_ALTA_ADMIN)}")
        t = por_clave.get("alta_imss_nomina")
        if t:
            onb.cambiar_estado_tarea(t, "realizada" if datos.alta_administrativa == "Realizada" else "pendiente", "", u.nombre)
    if datos.equipo_accesos is not None:
        if datos.equipo_accesos not in ESTADOS_EQUIPO_ACCESOS:
            raise HTTPException(400, f"equipo_accesos inválido. Usa uno de: {', '.join(ESTADOS_EQUIPO_ACCESOS)}")
        destino = {"Listo": "realizada", "Pendiente": "pendiente", "No aplica": "cancelada"}[datos.equipo_accesos]
        for t in tareas:
            if not t.fija and t.tipo in ("correo", "equipo", "accesos") and t.estado != destino:
                onb.cambiar_estado_tarea(t, destino, "Marcado «No aplica» en la preparación de ingreso.", u.nombre)
    onb.sincronizar_legado(db, e)
    registrar(db, u.nombre, "preparacion_ingreso_actualizada", "expediente", str(e.id),
              {"por_tareas": True, "contrato": datos.contrato, "alta_administrativa": datos.alta_administrativa,
               "equipo_accesos": datos.equipo_accesos, "correo_rh": u.correo})


# ------------------------------------------------------------
# Documentos: subir → validar con IA → estado (revisión humana si hay duda)
# ------------------------------------------------------------


def _resolver_estado(v: ia.DocumentoValidado, con_ia: bool) -> tuple[str, str]:
    """Traduce la validación de la IA a un estado y un motivo legible para RH."""
    if not con_ia:
        return "revision", "Modo demo: se requiere revisión humana."
    # 2026-09-18 (validación estricta): un archivo que no es claramente el documento oficial solicitado
    # (tarea, foto casual, captura, otro trámite) se RECHAZA automáticamente — nunca cuenta como válido.
    if not v.coincide_tipo or not getattr(v, "es_documento_oficial", True) or (v.tipo_detectado or "").strip().lower() in ("otro", "desconocido", "ninguno", ""):
        detectado = (v.tipo_detectado or "").strip()
        que = f"El archivo parece ser {detectado}" if detectado and detectado.lower() not in ("otro", "desconocido", "ninguno") else "El archivo no es el documento solicitado"
        return "rechazado", v.motivo_rechazo or f"{que}; sube el documento oficial correcto (foto clara o PDF)."
    if not v.legible:
        return "rechazado", v.motivo_rechazo or "El documento no se lee con claridad."
    if not v.completo:
        return "rechazado", v.motivo_rechazo or "Falta parte del documento (por ejemplo, el reverso)."
    if v.vigente is False:
        return "rechazado", v.motivo_rechazo or "El documento está vencido."
    if v.coincide_titular is False:
        return "revision", "El nombre del documento no coincide con el del candidato: verifícalo."
    return "recibido", v.observaciones


async def subir_documento_interno(db: Session, e: Expediente, tipo: str, archivo: UploadFile, subido_por: str) -> dict:
    """Lógica compartida entre el endpoint autenticado de RH (subir_documento) y la liga
    pública del candidato (routers/expediente_publico.py) — el "ya fue dado de alta" se queda
    duro para los dos, sin forzar_prueba: no es fricción de secuencia, es una guarda de
    integridad (ver Lote 4)."""
    if e.estado == "alta":
        raise HTTPException(409, "El expediente ya fue dado de alta; no admite cambios.")
    doc = _documento(e, tipo)
    if doc.interno:
        raise HTTPException(409, f"«{doc.tipo}» es un documento interno de RH: cárgalo desde las tareas del Onboarding.")
    if doc.estado == "no_aplica" and subido_por == "candidato":
        raise HTTPException(409, f"«{doc.tipo}» no se requiere en tu expediente.")
    validado = await fs.validar(archivo, f"documento «{doc.tipo}»")
    return _registrar_documento(db, e, doc, validado, subido_por)


async def adjuntar_documento_bytes(
    db: Session, e: Expediente, doc: Documento, contenido: bytes, nombre: str, mime: str, subido_por: str
) -> dict:
    """Tercera puerta de entrada (2026-09-15): documentos que llegan por WhatsApp ya descargados de
    Meta. Misma validación de archivo e IA que la liga pública y la subida de RH."""
    if e.estado == "alta":
        raise HTTPException(409, "El expediente ya fue dado de alta; no admite cambios.")
    validado = fs.validar_bytes(contenido, nombre, f"documento «{doc.tipo}»")
    return _registrar_documento(db, e, doc, validado, subido_por)


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


# Sinónimos con los que un candidato nombra sus papeles por WhatsApp → palabra clave del tipo del
# expediente (DOCUMENTOS_BASE). Se compara sin acentos y por subcadena.
_SINONIMOS_DOC = [
    (("ine", "ife", "pasaporte", "identificacion", "credencial"), "identificacion"),
    (("curp",), "curp"),
    (("rfc", "fiscal", "sat", "constancia"), "fiscal"),
    (("nss", "seguro social", "imss"), "seguridad social"),
    (("domicilio", "luz", "cfe", "agua", "telmex", "predial", "recibo"), "domicilio"),
    (("clabe", "banco", "bancaria", "estado de cuenta", "cuenta"), "bancaria"),
]


def documento_para_adjunto(db: Session, e: Expediente, pie: str, nombre_archivo: str) -> Documento:
    """A qué Documento del expediente corresponde un adjunto de WhatsApp: (1) el pie de foto o el
    nombre del archivo mencionan un tipo ("mi INE", "comprobante de luz"); (2) si no, el primer
    obligatorio pendiente/rechazado; (3) si no falta ninguno, el primer opcional pendiente; (4) si
    tampoco, se agrega como documento adicional (RH lo reclasifica desde el expediente)."""
    pista = _norm(f"{pie} {nombre_archivo}")
    docs = sorted((d for d in e.documentos if d.estado != "no_aplica" and not d.interno), key=lambda d: d.id)
    if pista.strip():
        for d in docs:
            if _norm(d.tipo) in pista:
                return d
        for palabras, clave in _SINONIMOS_DOC:
            if any(re.search(rf"\b{re.escape(w)}\b", pista) for w in palabras):
                d = next((x for x in docs if clave in _norm(x.tipo)), None)
                if d:
                    return d
    for d in docs:
        if d.obligatorio and d.estado in ("pendiente", "rechazado"):
            return d
    for d in docs:
        if d.estado in ("pendiente", "rechazado"):
            return d
    n = sum(1 for d in docs if d.tipo.startswith("Documento WhatsApp")) + 1
    nuevo = Documento(expediente_id=e.id, tipo=f"Documento WhatsApp {n}", obligatorio=False)
    db.add(nuevo)
    db.flush()
    return nuevo


def _canal_recepcion(subido_por: str) -> str:
    """B3: canal por el que llegó el archivo — whatsapp (webhook), liga (expediente público) o rh (tablero)."""
    s = (subido_por or "").lower()
    if s.startswith("whatsapp"):
        return "whatsapp"
    if s == "candidato":
        return "liga"
    return "rh"


def _registrar_documento(db: Session, e: Expediente, doc: Documento, validado, subido_por: str) -> dict:
    """Registra el archivo recibido (validación IA/Modo Prueba) y su trazabilidad (B3: `recibido_en`, `recibido_canal`).
    B5: NUNCA toca `postulacion.etapa` — el candidato sigue en Contratación/Onboarding hasta que RH lo mueva."""
    titular = e.candidato.nombre if e.candidato else ""
    if modo_prueba_activo(db):
        # 2026-09-18 (Modo Prueba TOTAL): se salta el OCR/IA y cualquier PDF o imagen queda válido de inmediato.
        v = ia.DocumentoValidado(
            tipo_detectado=doc.tipo, es_documento_oficial=True, coincide_tipo=True, legible=True, completo=True, vigente=None,
            nombre_detectado=None, coincide_titular=None, motivo_rechazo=None,
            observaciones="Modo Prueba: validación por IA omitida; documento aceptado automáticamente.",
        )
        con_ia = True
    else:
        v, con_ia = ia.validar_documento(validado.b64, validado.extension, doc.tipo, titular)

    doc.archivo = fs.guardar(validado, f"expedientes/{e.id}", doc.tipo.replace(" ", "_"))
    doc.nombre_archivo = validado.nombre
    doc.mime = validado.mime
    doc.tamano = validado.tamano
    doc.subido_en = datetime.now(timezone.utc)
    doc.validacion = v.model_dump()
    doc.estado, doc.notas_ia = _resolver_estado(v, con_ia)
    # vuelve a quedar pendiente de revisión humana («Por revisar»); en Modo Prueba queda «Aprobado» (Onboarding v2)
    doc.revisado_por = "Modo Prueba" if (modo_prueba_activo(db) and doc.estado == "recibido") else ""
    if doc.entregado:  # B3: recibido (o digital en revisión) → fecha/hora y canal de recepción
        doc.recibido_en = doc.subido_en
        doc.recibido_canal = _canal_recepcion(subido_por)
    else:
        doc.recibido_en = None
        doc.recibido_canal = ""

    _sincronizar_estado(e)
    registrar(
        db, "agente-ia", "documento_validado", "documento", f"{e.id}:{doc.tipo}",
        {"ia": con_ia, "estado": doc.estado, "tipo_detectado": v.tipo_detectado, "subido_por": subido_por},
    )
    db.commit()
    return {
        "ia": con_ia,
        "documento": {"tipo": doc.tipo, "estado": doc.estado, "notas": doc.notas_ia},
        "expediente": expediente_dict(e),
    }


@router.post("/expedientes/{exp_id}/documentos")
async def subir_documento(
    exp_id: int,
    tipo: str = Form(...),
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    e = _expediente(db, exp_id, cuenta.id)
    return await subir_documento_interno(db, e, tipo, archivo, subido_por=u.nombre)


@router.get("/expedientes/{exp_id}/documentos/{tipo}/archivo")
def descargar_documento(
    exp_id: int, tipo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    e = _expediente(db, exp_id, cuenta.id)
    doc = _documento(e, tipo)
    if not fs.existe(doc.archivo):
        raise HTTPException(404, "Todavía no se ha subido este documento.")
    return FileResponse(doc.archivo, media_type=doc.mime or "application/octet-stream", filename=doc.nombre_archivo or doc.tipo)


# Onboarding v2 (2026-09-28): también se acepta el vocabulario nuevo; se guarda con los valores de siempre.
ALIAS_ESTADO_DOC = {"aprobado": "recibido", "por_revisar": "revision", "por revisar": "revision", "no aplica": "no_aplica"}


class EstadoDocIn(BaseModel):
    tipo: str
    estado: str  # recibido (Aprobado) | rechazado | revision (Por revisar) | pendiente | no_aplica (solo RH, con motivo)
    notas: str = ""
    motivo: str = ""  # obligatorio para «No aplica»
    # el documento se entregó en físico o fuera del sistema: RH lo da por recibido bajo su responsabilidad
    recibido_fisico: bool = False


@router.post("/expedientes/{exp_id}/documentos/estado")
def marcar_documento(
    exp_id: int, datos: EstadoDocIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Revisión humana manual de un documento (el agente propone, RH dispone)."""
    e = _expediente(db, exp_id, cuenta.id)
    doc = _documento(e, datos.tipo)
    if doc.interno:
        raise HTTPException(409, f"«{doc.tipo}» es un documento interno de RH: se gestiona desde las tareas del Onboarding.")
    datos.estado = ALIAS_ESTADO_DOC.get((datos.estado or "").strip().lower(), (datos.estado or "").strip().lower())
    if datos.estado not in ESTADOS_DOC + ("no_aplica",):
        raise HTTPException(400, f"Estado inválido. Usa uno de: {', '.join(ESTADOS_DOC + ('no_aplica',))}")
    if datos.estado == "no_aplica":
        # «No aplica» lo decide SOLO una persona de RH (esta ruta exige sesión con permiso de decisión) y con motivo
        motivo = (datos.motivo or datos.notas or "").strip()
        if not motivo:
            raise HTTPException(400, "Indica el motivo por el que este documento no aplica.")
        anterior = doc.estado
        doc.estado, doc.motivo_no_aplica, doc.no_aplica_por = "no_aplica", motivo[:1000], u.nombre
        doc.no_aplica_en = datetime.now(timezone.utc)
        doc.revisado_por = u.nombre
        _sincronizar_estado(e)
        registrar(db, u.nombre, "documento_no_aplica", "documento", f"{e.id}:{doc.tipo}", {"de": anterior, "motivo": motivo[:300], "correo_rh": u.correo})
        db.commit()
        return expediente_dict(e)
    if doc.estado == "no_aplica":
        # vuelve a aplicar: se limpia el motivo (queda en bitácora)
        doc.motivo_no_aplica, doc.no_aplica_por, doc.no_aplica_en = "", "", None
    # confirmar uno ya recibido es válido; darlo por recibido de la nada exige archivo o entrega física declarada
    if datos.estado == "recibido" and not doc.archivo and doc.estado != "recibido" and not datos.recibido_fisico:
        raise HTTPException(
            409,
            "Ese documento no tiene archivo cargado. Súbelo, o marca «recibido en físico» para dejar "
            "constancia de que lo recibiste fuera del sistema.",
        )

    anterior = doc.estado
    doc.estado = datos.estado
    doc.revisado_por = u.nombre
    if datos.notas:
        doc.notas_ia = datos.notas
    # B3: trazabilidad de recepción también en la revisión manual (físico → «fisico»; con archivo conserva su canal)
    if doc.entregado and not doc.recibido_en:
        doc.recibido_en = datetime.now(timezone.utc)
        doc.recibido_canal = doc.recibido_canal or ("fisico" if not doc.archivo else "rh")
    elif not doc.entregado:
        doc.recibido_en = None
        doc.recibido_canal = ""

    _sincronizar_estado(e)
    registrar(
        db, u.nombre, "documento_revisado", "documento", f"{e.id}:{doc.tipo}",
        {"de": anterior, "a": datos.estado, "notas": datos.notas[:300], "sin_archivo": not doc.archivo},
    )
    db.commit()
    return expediente_dict(e)


class AgregarDocIn(BaseModel):
    tipo: str
    obligatorio: bool = True


@router.post("/expedientes/{exp_id}/documentos/agregar", status_code=201)
def agregar_documento(
    exp_id: int, datos: AgregarDocIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Suma un documento al checklist (título profesional, licencia, carta de no antecedentes…)."""
    e = _expediente(db, exp_id, cuenta.id)
    tipo = datos.tipo.strip()
    if not tipo:
        raise HTTPException(400, "Indica el nombre del documento.")
    if any(d.tipo.lower() == tipo.lower() for d in e.documentos):
        raise HTTPException(409, f"El expediente ya incluye '{tipo}'.")

    db.add(Documento(expediente_id=e.id, tipo=tipo, obligatorio=datos.obligatorio))
    _sincronizar_estado(e)
    registrar(db, u.nombre, "documento_agregado", "expediente", str(e.id), {"tipo": tipo, "obligatorio": datos.obligatorio})
    db.commit()
    db.refresh(e)
    return expediente_dict(e)


@router.delete("/expedientes/{exp_id}/documentos/{tipo}")
def quitar_documento(
    exp_id: int, tipo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    e = _expediente(db, exp_id, cuenta.id)
    doc = _documento(e, tipo)
    if doc.estado == "recibido":
        raise HTTPException(409, "No se puede quitar un documento ya recibido; márcalo como no obligatorio.")
    e.documentos.remove(doc)
    _sincronizar_estado(e)
    registrar(db, u.nombre, "documento_quitado", "expediente", str(e.id), {"tipo": doc.tipo})
    db.commit()
    return expediente_dict(e)


# ------------------------------------------------------------
# Recordatorios (el agente da seguimiento a pendientes)
# ------------------------------------------------------------


@router.post("/expedientes/{exp_id}/recordatorio")
async def recordatorio(
    exp_id: int, notificar: Optional[NotificarIn] = Body(default=None, embed=True),
    db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual),
):
    e = _expediente(db, exp_id, cuenta.id)
    if e.no_ingreso_en:
        raise HTTPException(409, "Esta persona quedó como «No ingresó»: ya no se le envían recordatorios.")
    pendientes = e.pendientes
    if not pendientes:
        return {"enviado": False, "detalle": "Sin documentos pendientes 🎉", "expediente": expediente_dict(e)}

    # Fase 2: las notificaciones van por la POSTULACION del expediente (no por la persona).
    p = e.postulacion
    rechazados = [d for d in e.documentos if d.estado == "rechazado"]
    detalle_rechazos = "".join(f"\n• {d.tipo}: {d.notas_ia}" for d in rechazados if d.notas_ia)
    resultados: List[dict] = []
    nivel = e.nivel_recordatorio  # 2026-09-17: 1 ligero → 2 intermedio → 3 definitivo (RH puede seguir mandando el definitivo)
    if p:
        resultados = await notificaciones.disparar(
            db, "recordatorio_documentos", p, "agente-ia",
            extra={"pendientes": pendientes, "detalle_rechazos": detalle_rechazos, "puesto": e.puesto or "tu nuevo puesto",
                   "fecha_limite": e.documentos_hasta, "nivel": nivel},
            override=override_de(notificar),
        )
    registrar_recordatorio_enviado(db, e, nivel, u.nombre, resultados)  # B3: también marca la solicitud por documento
    e.ultimo_recordatorio_en = datetime.now(timezone.utc)
    db.commit()
    return {"enviado": True, "pendientes": pendientes, "nivel": nivel, "tono": NIVELES_RECORDATORIO[nivel], "notificaciones": resultados, "expediente": expediente_dict(e)}


# ------------------------------------------------------------
# Alta del colaborador (HITL obligatorio — RH autoriza) — «Dar de alta como colaborador»
# ------------------------------------------------------------


def _crear_colaborador(db: Session, e: Expediente, u: Usuario) -> Optional[Colaborador]:
    """Cierra el ciclo del candidato: crea el registro en `colaboradores` con los datos
    definitivos capturados en el formulario de la etapa Contratación (sueldo, ubicación,
    jefe directo, tipo de contratación) más nombre, puesto y CV heredados del candidato."""
    c = e.candidato
    if not c:
        return None
    # Fase 2: la vacante es la de la POSTULACIÓN de este expediente (decisión P5).
    vac = e.postulacion.vacante if e.postulacion else None

    cv = next((a for a in reversed(c.archivos) if a.tipo == "cv"), None)
    # 2026-09-19 (Bloque 3, «alta perfecta»): las condiciones FINALES guardadas en Contratación mandan;
    # la vacante solo completa lo que RH dejó vacío (antes: empresa interna de la vacante y campos vacíos).
    empresa = e.empresa or (nombre_empresa_candidato(vac) if vac else "") or ""
    col = Colaborador(
        codigo="TMP",
        cuenta_id=c.cuenta_id,
        cliente_id=vac.cliente_id if vac else None,
        nombre=c.nombre,
        correo=c.correo,
        telefono=c.telefono,
        puesto=e.puesto or (vac.titulo if vac else ""),
        salario=e.sueldo or "",
        empresa=empresa,
        ubicacion=e.ubicacion or (vac.ubicacion if vac else ""),
        jefe_directo=e.jefe_directo or "",
        tipo_contratacion=e.tipo_contratacion or "",
        cv_ruta=cv.ruta if cv else "",
        cv_nombre=cv.nombre if cv else "",
        fecha_ingreso=e.fecha_ingreso_real or e.fecha_ingreso,  # Onboarding v2: la fecha REAL confirmada manda
        dado_de_alta_por=u.nombre,
        candidato_origen_id=c.id,
        expediente_id=e.id,
    )
    # Registro histórico INMUTABLE de ingreso: lo que se firmó/acordó al momento del alta.
    col.condiciones_ingreso = {
        "puesto": col.puesto, "sueldo": col.salario, "tipo_contratacion": col.tipo_contratacion,
        "fecha_ingreso": e.fecha_ingreso.isoformat() if e.fecha_ingreso else None, "ubicacion": col.ubicacion,
        "jefe_directo": col.jefe_directo, "empresa": col.empresa, "cliente_id": col.cliente_id,
        "duracion_contrato": e.duracion_contrato, "duracion_unidad": e.duracion_unidad or "",
        "fecha_termino": e.fecha_termino.isoformat() if e.fecha_termino else None,
        "vacante": vac.codigo if vac else None, "expediente": e.id, "postulacion": e.postulacion.codigo if e.postulacion else None,
        "condiciones_guardadas_en": e.condiciones_guardadas_en.isoformat() if e.condiciones_guardadas_en else None,
        "alta_por": u.nombre, "alta_en": datetime.now(timezone.utc).isoformat(),
    }
    db.add(col)
    db.flush()
    col.codigo = f"COL-{100 + col.id}"
    registrar(
        db, u.nombre, "colaborador_alta", "colaborador", col.codigo,
        {"candidato_origen": c.codigo, "puesto": col.puesto, "expediente": e.id, "condiciones_ingreso": col.condiciones_ingreso},
    )
    return col


class AltaIn(BaseModel):
    fecha_ingreso: Optional[str] = None
    notificar: Optional[NotificarIn] = None  # Punto 12


@router.post("/expedientes/{exp_id}/alta")
async def alta(
    exp_id: int, datos: AltaIn, forzar_prueba: bool = False,
    db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """`forzar_prueba` (Lote 4) deja saltar los bloqueos de completitud de abajo, pero NUNCA el
    de "ya fue dado de alta" — _crear_colaborador() no es idempotente, forzar ese en particular
    crearía un Colaborador duplicado, así que se queda tan duro como el gate de consentimiento."""
    e = _expediente(db, exp_id, cuenta.id)
    if e.estado == "alta":
        raise HTTPException(409, f"El expediente ya fue dado de alta por {e.alta_autorizada_por}.")
    # 2026-09-18 (Modo Prueba TOTAL, pedido del cliente): con modo_prueba activo se omite POR COMPLETO la
    # validación de integridad del expediente (documentos adjuntos, 100 %, confirmación de RH) — el flag
    # forzar_prueba ya no es necesario. Con Modo Prueba apagado todo sigue exigiéndose.
    prueba = modo_prueba_activo(db)
    if e.no_ingreso_en:
        raise HTTPException(409, "Esta persona quedó como «No ingresó»; no se puede dar de alta.")
    if not prueba and not any(d.archivo for d in e.documentos):
        raise HTTPException(400, "No se puede dar de alta al colaborador: El expediente no tiene documentos adjuntos.")
    # 2026-09-19 (Bloque 3, alta perfecta): el alta toma ESTRICTAMENTE las condiciones finales guardadas —
    # sin ellas no hay alta (salvo Modo Prueba).
    faltan = [n for n, v in (("puesto", e.puesto), ("sueldo", e.sueldo), ("tipo de contratación", e.tipo_contratacion), ("fecha de ingreso", e.fecha_ingreso)) if not v]
    if faltan and not prueba:
        raise HTTPException(409, f"Captura y guarda las condiciones de contratación antes del alta. Faltan: {', '.join(faltan)}.")
    if e.progreso < 100 and not prueba and not puede_forzar_prueba(db, forzar_prueba):
        raise HTTPException(409, f"El expediente está al {e.progreso}% (solo cuentan documentos Aprobados). Faltan: {', '.join(e.no_aprobados)}.")
    # HITL: lo que cuenta para el % (recibido o digital en revisión) lo confirma una persona de RH
    # antes del alta — el porcentaje ya no espera esa confirmación, el alta sí.
    sin_revisar = e.sin_confirmar
    if sin_revisar and not prueba and not puede_forzar_prueba(db, forzar_prueba):
        raise HTTPException(
            409,
            "Antes del alta, una persona de RH debe confirmar los documentos subidos (validados por la IA o en revisión): "
            + ", ".join(sin_revisar),
        )
    # Onboarding v2 (Fase 3), después de las validaciones de integridad: «Dar de alta» se habilita SOLO
    # después de «Confirmar ingreso» (fecha real), sin importar la fecha prevista. Modo Prueba se la salta.
    if not e.ingreso_confirmado_en and not prueba:
        raise HTTPException(409, "Primero confirma el ingreso (fecha real de llegada) con «Confirmar ingreso».")

    if datos.fecha_ingreso:
        try:
            e.fecha_ingreso = datetime.fromisoformat(datos.fecha_ingreso).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(400, "fecha_ingreso inválida (usa ISO: 2026-08-15)")

    e.estado = "alta"
    e.alta_autorizada_por = u.nombre
    e.alta_fecha = datetime.now(timezone.utc)
    registrar(
        db, u.nombre, "alta_autorizada", "expediente", str(e.id),
        {"candidato": e.candidato.codigo if e.candidato else "", "puesto": e.puesto, "correo_rh": u.correo},
    )

    colaborador = _crear_colaborador(db, e, u)

    p = e.postulacion
    resultados: List[dict] = []
    if p:
        # La postulación cierra su ciclo: queda como historial "contratado" de la persona.
        p.cerrar("contratado")
        # Fase 5 (2026-09-15): bienvenida + instrucciones de ingreso AUTOMÁTICAS al candidato (evento
        # `instrucciones_ingreso`, regla de la Cuenta, sin override — como candidato_apto). Para no
        # mandarle dos mensajes seguidos, el evento `contratacion` deja de escribirle al candidato
        # cuando la bienvenida sí sale; sigue avisando al Cliente según la regla/override de RH.
        vac = p.vacante
        extra_ingreso = {
            "fecha_ingreso": e.fecha_ingreso,
            "puesto": e.puesto or (vac.titulo if vac else ""),
            "empresa": nombre_empresa_candidato(vac) if vac else "",
            "contacto_rh": " · ".join(x for x in [cuenta.correo_comunicacion, cuenta.whatsapp_comunicacion] if x),
        }
        bienvenida = await notificaciones.disparar(db, "instrucciones_ingreso", p, "sistema", extra=extra_ingreso)
        override_contratacion = override_de(datos.notificar) or {}
        if any(r.get("enviado") for r in bienvenida):
            override_contratacion = {**override_contratacion, "candidato_correo": False, "candidato_whatsapp": False}
        resultados = await notificaciones.disparar(
            db, "contratacion", p, u.nombre, extra={"fecha_ingreso": e.fecha_ingreso}, override=override_contratacion or None
        )
        resultados = bienvenida + resultados
        registrar(
            db, "sistema", "instrucciones_ingreso_enviadas", "expediente", str(e.id),
            {"postulacion": p.codigo, "envios": [{k: r.get(k) for k in ("canal", "destino", "enviado", "detalle")} for r in bienvenida]},
        )

    db.commit()
    return {
        "ok": True,
        "notificaciones": resultados,
        "expediente": expediente_dict(e),
        "colaborador": colaborador_dict(colaborador) if colaborador else None,
    }


_MESES_LARGO = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _fecha_larga(dt: Optional[datetime]) -> str:
    if not dt:
        return "por definir"
    return f"{dt.day} de {_MESES_LARGO[dt.month - 1]} de {dt.year}"


def _datos_carta_intencion(e: Expediente) -> dict:
    """Datos reales + fallback «por definir» que comparten el PDF y la vista HTML."""
    c = e.candidato
    vac = e.postulacion.vacante if e.postulacion else None
    return {
        "nombre": c.nombre if c else "[Nombre del colaborador]",
        # B2: la empresa contratante es la razón social elegida en las condiciones; fallback a la visible de la vacante
        "empresa": e.empresa or (nombre_empresa_candidato(vac) if vac else "") or "la empresa",
        "puesto": e.puesto or (vac.titulo if vac else "") or "el puesto",
        "sueldo": e.sueldo or "por definir",
        "tipo_contratacion": e.tipo_contratacion or "por definir",
        "ubicacion": e.ubicacion or (c.ubicacion if c else "") or "por definir",
        "jefe": e.jefe_directo or "por definir",
        "fecha_ingreso": _fecha_larga(e.fecha_ingreso),
        # B2: vigencia de «Tiempo determinado» (duración capturada + fecha de término calculada)
        "duracion": f"{e.duracion_contrato} {e.duracion_unidad}" if e.duracion_contrato and e.duracion_unidad else "",
        "fecha_termino": _fecha_larga(e.fecha_termino) if e.fecha_termino else "",
        "hoy": _fecha_larga(datetime.now(timezone.utc)),
    }


def _html_carta_intencion(e: Expediente) -> str:
    """HTML de la carta de intención — mismo estilo de f-strings con datos reales y fallback
    ('por definir') que ya usa candidatos._html_correo_candidato, pero con estructura de
    documento formal (encabezado, cuerpo, firma) porque esto se imprime/firma, no se lee en un
    correo."""
    c = e.candidato
    vac = e.postulacion.vacante if e.postulacion else None  # Fase 2: la vacante es de la postulación
    nombre = c.nombre if c else "[Nombre del colaborador]"
    empresa = (nombre_empresa_candidato(vac) if vac else "") or "la empresa"
    puesto = e.puesto or (vac.titulo if vac else "") or "el puesto"
    sueldo = e.sueldo or "por definir"
    tipo_contratacion = e.tipo_contratacion or "por definir"
    ubicacion = e.ubicacion or (c.ubicacion if c else "") or "por definir"
    jefe = e.jefe_directo or "por definir"
    fecha_ingreso = _fecha_larga(e.fecha_ingreso)
    hoy = _fecha_larga(datetime.now(timezone.utc))

    return f"""
    <html>
    <head>
      <meta charset="utf-8" />
      <style>
        body {{ font-family: 'Helvetica', 'Arial', sans-serif; font-size: 12pt; color: #1a1a1a; line-height: 1.6; }}
        h1 {{ font-size: 16pt; margin-bottom: 0; }}
        .subtitulo {{ color: #555; margin-top: 4px; }}
        .condiciones {{ margin: 24px 0; border-collapse: collapse; width: 100%; }}
        .condiciones td {{ padding: 6px 0; border-bottom: 1px solid #ddd; }}
        .condiciones td:first-child {{ font-weight: bold; width: 40%; }}
        .firma {{ margin-top: 64px; }}
        .linea-firma {{ margin-top: 48px; border-top: 1px solid #1a1a1a; width: 280px; padding-top: 4px; }}
      </style>
    </head>
    <body>
      <h1>Carta de Intención de Contratación</h1>
      <p class="subtitulo">{empresa} · {hoy}</p>

      <p>Estimado(a) <strong>{nombre}</strong>,</p>
      <p>
        Nos da mucho gusto confirmarte que, tras concluir el proceso de selección, {empresa} te
        extiende esta carta de intención para incorporarte a nuestro equipo bajo las siguientes
        condiciones:
      </p>

      <table class="condiciones">
        <tr><td>Puesto</td><td>{puesto}</td></tr>
        <tr><td>Sueldo</td><td>{sueldo}</td></tr>
        <tr><td>Tipo de contratación</td><td>{tipo_contratacion}</td></tr>
        <tr><td>Ubicación de trabajo</td><td>{ubicacion}</td></tr>
        <tr><td>Jefe directo</td><td>{jefe}</td></tr>
        <tr><td>Fecha de ingreso</td><td>{fecha_ingreso}</td></tr>
      </table>

      <p>
        Esta carta es una manifestación de intención y no constituye por sí misma un contrato
        laboral; las condiciones definitivas quedarán formalizadas en el contrato individual de
        trabajo correspondiente.
      </p>

      <div class="firma">
        <p>Saludos cordiales,</p>
        <div class="linea-firma">{empresa} · Recursos Humanos</div>
      </div>
    </body>
    </html>
    """


@router.get("/expedientes/{exp_id}/carta-intencion")
def carta_intencion(
    exp_id: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)
):
    """Genera la carta de intención en PDF a partir de las condiciones ya capturadas en el
    expediente. GET (no POST) a propósito: mismo patrón que urlArchivoCandidato/urlDocumento —
    un <a href> autenticado por cookie de sesión, sin manejo de blobs en el frontend.

    Import perezoso de weasyprint a propósito: necesita librerías nativas de sistema (Pango,
    Cairo, GDK-PixBuf) que Linux (producción) resuelve con apt-get, pero que no vienen en
    Windows — si `import weasyprint` estuviera a nivel de módulo, tumbaría el arranque de TODA
    la API en una máquina sin esas librerías, no solo este endpoint."""
    e = _expediente(db, exp_id, cuenta.id)
    # 2026-09-15 (Fase 1): el PDF se genera con fpdf2 (Python puro, sin Pango/Cairo): WeasyPrint
    # fallaba en el servidor por librerías nativas y la carta no se generaba. Mismo contenido.
    try:
        pdf = pdf_carta_intencion(_datos_carta_intencion(e))
    except Exception as ex:  # noqa: BLE001 — se reporta a RH, nunca 500 mudo
        raise HTTPException(503, f"No se pudo generar el PDF de la carta de intención: {ex}")
    registrar(
        db, u.nombre, "carta_intencion_generada", "expediente", str(e.id),
        {"candidato": e.candidato.codigo if e.candidato else "", "correo_rh": u.correo},
    )
    db.commit()
    nombre_archivo = f"carta-intencion-{e.candidato.codigo if e.candidato else exp_id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        # 2026-09-18: inline — la vista /carta/[id] del frontend la embebe con título y favicon de Red Human
        headers={"Content-Disposition": f'inline; filename="{nombre_archivo}"'},
    )


def _documentos_listos(e: Expediente) -> bool:
    return e.progreso == 100


@router.get("/expedientes/{exp_id}/contrato")
def contrato(
    exp_id: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)
):
    """Contrato individual de trabajo (PDF) con las condiciones FINALES guardadas (2026-09-19). Solo cuando
    los documentos requeridos ya están (expediente al 100 %), salvo Modo Prueba."""
    e = _expediente(db, exp_id, cuenta.id)
    if not _documentos_listos(e) and not modo_prueba_activo(db):
        raise HTTPException(409, f"El contrato se genera cuando el expediente está al 100 % de documentos Aprobados (hoy {e.progreso} %). Faltan: {', '.join(e.no_aprobados)}.")
    d = _datos_carta_intencion(e)
    if not (e.puesto and e.sueldo and e.tipo_contratacion and e.fecha_ingreso) and not modo_prueba_activo(db):
        raise HTTPException(409, "Captura y guarda las condiciones de contratación (puesto, sueldo, tipo y fecha de ingreso) antes de generar el contrato.")
    d["borrador"] = True  # Onboarding v2: lo generado es BORRADOR; el firmado se carga en la tarea «Contrato firmado»
    try:
        pdf = pdf_contrato(d)
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(503, f"No se pudo generar el contrato: {ex}")
    registrar(db, u.nombre, "contrato_generado", "expediente", str(e.id), {"candidato": e.candidato.codigo if e.candidato else "", "correo_rh": u.correo})
    db.commit()
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="contrato-{e.candidato.codigo if e.candidato else exp_id}.pdf"'})


class EnviarCartaIn(BaseModel):
    canal: str  # whatsapp | correo


@router.post("/expedientes/{exp_id}/carta-intencion/enviar")
async def enviar_carta_intencion(
    exp_id: int, datos: EnviarCartaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)
):
    """Manda la carta al candidato (2026-09-19): por WhatsApp la liga pública de su expediente con la carta; por
    correo el PDF adjunto con el layout corporativo. Trazabilidad en bitácora, sin ruido en pantalla."""
    e = _expediente(db, exp_id, cuenta.id)
    p = e.postulacion
    c = e.candidato
    if not c:
        raise HTTPException(404, "Expediente sin candidato.")
    if not e.token:
        e.token = secrets.token_urlsafe(24)
    liga = f"{settings.app_url}/expediente/{e.token}"
    d = _datos_carta_intencion(e)
    if datos.canal == "whatsapp":
        if not c.telefono:
            raise HTTPException(400, "El candidato no tiene WhatsApp registrado.")
        texto = (
            f"Hola {c.nombre.split(' ')[0]}, {d['empresa']} te comparte tu carta de intención para el puesto de {d['puesto']} "
            f"({d['sueldo']}, ingreso el {d['fecha_ingreso']}). La puedes descargar desde tu expediente: {liga}"
        )
        with de_cuenta(p.cuenta_id if p else None):
            envio = await enviar_mensaje(c.telefono, texto)
        if p:
            from .candidatos import guardar_mensaje  # import local: candidatos ↔ contratacion

            guardar_mensaje(db, p, "assistant", texto, "whatsapp", envio)
    elif datos.canal == "correo":
        if not c.correo:
            raise HTTPException(400, "El candidato no tiene correo registrado.")
        try:
            pdf = pdf_carta_intencion(d)
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(503, f"No se pudo generar el PDF: {ex}")
        asunto, html = plantillas_correo.html_aviso(
            f"Tu carta de intención · {d['puesto']}",
            f"{d['empresa']} te extiende esta carta de intención con las condiciones de tu incorporación. La encuentras adjunta en PDF y también en tu expediente.",
            d["empresa"],
            [("Puesto", d["puesto"]), ("Sueldo", d["sueldo"]), ("Tipo de contratación", d["tipo_contratacion"]), ("Fecha de ingreso", d["fecha_ingreso"]), ("Ubicación", d["ubicacion"])],
            ("Ver mi expediente", liga),
        )
        envio = await enviar_correo(c.correo, asunto, html, adjuntos=[{"filename": "carta-intencion.pdf", "content": pdf}])
    else:
        raise HTTPException(400, "canal debe ser whatsapp o correo")
    registrar(db, u.nombre, "carta_intencion_enviada", "expediente", str(e.id), {"canal": datos.canal, "enviado": bool(envio.get("enviado")), "detalle": str(envio.get("detalle", ""))[:200], "correo_rh": u.correo})
    db.commit()
    return {"canal": datos.canal, **envio, "detalle": str(envio.get("detalle", ""))}


class CancelarIn(BaseModel):
    motivo: str


@router.post("/expedientes/{exp_id}/cancelar")
def cancelar(
    exp_id: int, datos: CancelarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Botón «Cancelar contratación» — cierra un expediente que no llegó a alta y regresa al
    candidato a Entrevista Humana (la etapa manual inmediata anterior a Contratación)."""
    e = _expediente(db, exp_id, cuenta.id)
    if not datos.motivo.strip():
        raise HTTPException(400, "La cancelación requiere un motivo.")
    if e.estado == "alta":
        raise HTTPException(409, "No se puede cancelar un expediente ya dado de alta.")

    p = e.postulacion
    codigo = p.codigo if p else (e.candidato.codigo if e.candidato else "")
    if p:
        p.etapa = "Entrevista Humana"
    registrar(db, u.nombre, "expediente_cancelado", "expediente", str(e.id), {"motivo": datos.motivo, "postulacion": codigo})
    db.delete(e)
    db.commit()
    return {"ok": True, "candidato": codigo}
