"""Evaluaciones unificadas — Fase 1 (2026-09-29) + catálogo de Pruebas psicométricas (2026-09-28).

* Catálogo (Configuración → Pruebas psicométricas): identificador interno, nombre visible, descripción, puestos
  sugeridos, modo (Integrada / Enlace externo / Carga manual), proveedor, identificador en el proveedor y estado.
* Evaluaciones: UN objeto (`Evaluacion`) para entrevista humana, médica, psicométrica, socioeconómica, técnica,
  referencias u otra. Pantalla única «Agregar evaluación» (POST /postulaciones/{codigo}), formulario único de
  resultado (POST /{codigo}/resultado desde el sistema y POST /publica/{token}/resultado desde la liga del
  evaluador), cinco estados, consentimiento médico como condición, historial en `eventos_evaluacion`.
* Recibir o guardar un resultado NUNCA mueve la etapa del candidato ni lo envía a Contratación.
* Consentimiento médico EXPRESO y POR ESCRITO: liga pública `/consentimiento/{token}` (texto exacto + evidencia).
* Informe médico COMPLETO (comentarios y adjuntos) solo para quien tiene permiso (`Usuario.puede_ver_informe_medico`).
"""

import hashlib
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import fechas
from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import (
    ESTADOS_EVALUACION_U,
    FORMAS_EVALUACION,
    MODOS_PRUEBA,
    TEXTO_CONSENTIMIENTO_MEDICO,
    Archivo,
    Cuenta,
    Evaluacion,
    Postulacion,
    PruebaPsicometrica,
    Usuario,
    registrar,
)
from ..serial import evaluacion_dict, evento_evaluacion_dict, nombre_empresa_candidato, prueba_psicometrica_dict
from ..services import archivos as fs
from ..services import evaluaciones as sev
from ..services import notificaciones
from ..services.modulos_rh import requiere_modulos_rh
from ..services.notificaciones import NotificarIn, override_de

router = APIRouter(prefix="/evaluaciones", tags=["evaluaciones"], dependencies=[Depends(requiere_modulos_rh)])


def _norm(s: str) -> str:
    from ..services.onboarding import norm

    return norm(s)


# ---------------- Catálogo: Pruebas psicométricas ----------------

def _prueba(db: Session, pid: int, cuenta_id: int) -> PruebaPsicometrica:
    pr = db.query(PruebaPsicometrica).filter(PruebaPsicometrica.id == pid, PruebaPsicometrica.cuenta_id == cuenta_id).first()
    if not pr:
        raise HTTPException(404, "Prueba psicométrica no encontrada.")
    return pr


class PruebaIn(BaseModel):
    clave: str
    nombre: str
    descripcion: str = ""
    puestos: List[str] = []
    modo: str = "manual"
    proveedor: str = ""
    id_proveedor: str = ""
    url: str = ""
    activa: bool = True


class EditarPruebaIn(BaseModel):
    clave: Optional[str] = None
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    puestos: Optional[List[str]] = None
    modo: Optional[str] = None
    proveedor: Optional[str] = None
    id_proveedor: Optional[str] = None
    url: Optional[str] = None
    activa: Optional[bool] = None


def _validar_prueba(db: Session, cuenta_id: int, pr: PruebaPsicometrica) -> None:
    pr.clave = (pr.clave or "").strip()[:60]
    pr.nombre = (pr.nombre or "").strip()[:200]
    if not pr.clave:
        raise HTTPException(400, "Captura el identificador interno de la prueba.")
    if not pr.nombre:
        raise HTTPException(400, "Captura el nombre visible de la prueba.")
    if pr.modo not in MODOS_PRUEBA:
        raise HTTPException(400, "Modo inválido: usa integrada, enlace o manual.")
    if pr.modo == "enlace" and not (pr.url or "").strip().lower().startswith(("http://", "https://")):
        raise HTTPException(400, "El modo «Enlace externo» necesita la liga de la prueba (https://…).")
    if pr.modo == "integrada" and not (pr.proveedor or "").strip():
        raise HTTPException(400, "El modo «Integrada» necesita el proveedor.")
    pr.puestos = [p.strip()[:200] for p in (pr.puestos or []) if p and p.strip()]
    otra = (
        db.query(PruebaPsicometrica)
        .filter(PruebaPsicometrica.cuenta_id == cuenta_id, PruebaPsicometrica.id != (pr.id or 0))
        .all()
    )
    if any(_norm(o.clave) == _norm(pr.clave) for o in otra):
        raise HTTPException(409, f"Ya existe una prueba con el identificador «{pr.clave}».")


@router.get("/pruebas")
def listar_pruebas(incluir_inactivas: bool = False, puesto: str = "", db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Con `puesto` las sugeridas para ese puesto van primero (`sugerida=true`)."""
    q = db.query(PruebaPsicometrica).filter(PruebaPsicometrica.cuenta_id == cuenta.id)
    if not incluir_inactivas:
        q = q.filter(PruebaPsicometrica.activa.is_(True))
    np_ = _norm(puesto)
    salida = []
    for pr in q.all():
        d = prueba_psicometrica_dict(pr)
        d["sugerida"] = bool(np_) and any(_norm(x) == np_ for x in pr.puestos or [])
        salida.append(d)
    return sorted(salida, key=lambda d: (not d["sugerida"], _norm(d["nombre"])))


@router.post("/pruebas", status_code=201)
def crear_prueba(datos: PruebaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    pr = PruebaPsicometrica(cuenta_id=cuenta.id, creado_por=u.nombre, **datos.model_dump())
    _validar_prueba(db, cuenta.id, pr)
    db.add(pr)
    db.flush()
    registrar(db, u.nombre, "prueba_psicometrica_creada", "evaluaciones", str(pr.id), {"clave": pr.clave, "modo": pr.modo, "correo_rh": u.correo})
    db.commit()
    return prueba_psicometrica_dict(pr)


@router.patch("/pruebas/{pid}")
def editar_prueba(pid: int, datos: EditarPruebaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    pr = _prueba(db, pid, cuenta.id)
    for campo, valor in datos.model_dump(exclude_none=True).items():
        setattr(pr, campo, valor)
    try:
        _validar_prueba(db, cuenta.id, pr)
    except HTTPException:
        db.rollback()
        raise
    registrar(db, u.nombre, "prueba_psicometrica_editada", "evaluaciones", str(pr.id), {"clave": pr.clave, "correo_rh": u.correo})
    db.commit()
    return prueba_psicometrica_dict(pr)


@router.delete("/pruebas/{pid}")
def inactivar_prueba(pid: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Baja lógica: queda Inactiva; las evaluaciones ya asignadas con ella no cambian."""
    pr = _prueba(db, pid, cuenta.id)
    pr.activa = False
    registrar(db, u.nombre, "prueba_psicometrica_inactivada", "evaluaciones", str(pr.id), {"clave": pr.clave, "correo_rh": u.correo})
    db.commit()
    return prueba_psicometrica_dict(pr)



# ======================================================================================================
# Evaluaciones unificadas — Fase 1 (2026-09-29). Toda evaluación (entrevista humana, médica, psicométrica…) se
# crea, se sigue y se cierra con el MISMO objeto y el MISMO formulario de resultado (RH y liga del evaluador).
# Ningún resultado mueve la etapa; solo ASIGNAR una entrevista humana lleva la tarjeta a «Entrevista Humana».
# ======================================================================================================

def _postulacion(db: Session, codigo: str, cuenta_id: int) -> Postulacion:
    from .candidatos import _por_codigo

    return _por_codigo(db, codigo, cuenta_id)


def _ev(db: Session, codigo: str, cuenta_id: int) -> Evaluacion:
    try:
        return sev.por_codigo(db, codigo, cuenta_id)
    except sev.ErrorEvaluacion as e:
        raise HTTPException(e.status, e.mensaje)


@contextmanager
def _negocio():
    """Traduce los errores de negocio del servicio a HTTP."""
    try:
        yield
    except sev.ErrorEvaluacion as e:
        raise HTTPException(e.status, e.mensaje)


def _respuesta(db: Session, ev: Evaluacion, u: Optional[Usuario], p: Optional[Postulacion] = None, resultados: Optional[list] = None, **extra) -> dict:
    from .candidatos import postulacion_dict

    salida = {"evaluacion": evaluacion_dict(ev, u), **extra}
    if resultados is not None:
        salida["resultados"] = resultados
        salida["advertencias"] = notificaciones.advertencias_de(resultados)
    if p is not None:
        salida["candidato"] = postulacion_dict(p, detalle=True)
    return salida


def _tocar(p: Postulacion) -> None:
    from .candidatos import _actualizar_ultima_actividad

    _actualizar_ultima_actividad(p)


@router.get("/postulaciones/{codigo}")
def listar_evaluaciones(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Tarjetas de «Resumen» (la más reciente arriba)."""
    p = _postulacion(db, codigo, cuenta.id)
    return [evaluacion_dict(ev, u) for ev in sev.de_postulacion(db, p)]


class EvaluadorIn(BaseModel):
    tipo: str = ""  # interno | externo
    usuario_id: Optional[int] = None
    contacto_id: Optional[int] = None  # contacto reutilizable del Cliente
    nombre: str = ""  # «+ Nuevo evaluador»
    correo: str = ""
    whatsapp: str = ""


class CitaIn(BaseModel):
    fecha: str  # 2026-09-30 (zona de la organización)
    hora: str  # 07:29
    modalidad: str  # Presencial | Videollamada | Teléfono
    direccion: str = ""
    liga_videollamada: str = ""  # obligatoria en Videollamada salvo que la cree Teams
    telefono: str = ""
    usar_teams: bool = True  # Fase 7B: con Teams conectado la videollamada se crea sola


class CrearEvaluacionIn(BaseModel):
    tipo: str
    nombre: str = ""  # obligatorio con «Otra»
    forma: str = "asignada"  # asignada | registro_directo | liga_otro_sistema | integrada
    evaluador: Optional[EvaluadorIn] = None
    instrucciones: str = ""
    liga_externa_candidato: str = ""
    prueba_id: Optional[int] = None  # proveedor integrado (catálogo)
    cita: Optional[CitaIn] = None
    notificar: Optional[NotificarIn] = None


async def _cita_con_teams(db: Session, p: Postulacion, cita: CitaIn, evaluador: dict) -> dict:
    """Arma la cita; en Videollamada sin liga y con Teams conectado crea la reunión ANTES de guardar (Fase 7B)."""
    from .candidatos import _reunion_teams_o_error

    with _negocio():
        datos = sev.armar_cita(cita.model_dump())
    if datos["cita_modalidad"] == "Videollamada" and not datos["cita_liga_videollamada"]:
        reunion = None
        if cita.usar_teams:
            reunion = await _reunion_teams_o_error(db, p, datos["cita_fecha_hora"],
                                                   {"correo": evaluador.get("evaluador_correo", ""), "nombre": evaluador.get("evaluador_nombre", "")})
        if not reunion:
            raise HTTPException(400, "Falta la liga de la videollamada.")
        datos["cita_liga_videollamada"], datos["teams_evento_id"] = reunion["liga"], reunion["evento_id"]
    return datos


@router.post("/postulaciones/{codigo}", status_code=201)
async def crear_evaluacion(codigo: str, datos: CrearEvaluacionIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Pantalla única «Agregar evaluación». Solo se piden dos decisiones: qué (tipo) y quién (forma/evaluador).
    - asignada: exige evaluador (interno o externo con correo o WhatsApp); cita opcional.
    - registro_directo: sin evaluador; el frontend manda enseguida el resultado a POST /{codigo}/resultado.
    - liga_otro_sistema: exige la liga que recibe el candidato. integrada: prueba del catálogo con proveedor.
    Asignar una entrevista humana lleva la tarjeta a «Entrevista Humana» (comportamiento actual) salvo que ya esté
    más adelante; nada más mueve la etapa."""
    p = _postulacion(db, codigo, cuenta.id)
    if not p.activa:
        raise HTTPException(409, "La postulación está cerrada.")
    with _negocio():
        tipo = sev.normalizar_tipo(datos.tipo)
    if datos.forma not in FORMAS_EVALUACION:
        raise HTTPException(400, f"Forma inválida. Usa una de: {', '.join(FORMAS_EVALUACION)}.")
    nombre = datos.nombre.strip()[:200]
    if tipo == "otra" and not nombre:
        raise HTTPException(400, "Con «Otra» captura el nombre de la evaluación.")
    if tipo != "entrevista_humana" and not p.consentimiento:
        raise HTTPException(409, "Falta el consentimiento de privacidad del candidato (LFPDPPP).")

    campos: dict = {"tipo": tipo, "nombre": nombre, "forma": datos.forma, "instrucciones": datos.instrucciones.strip()[:4000]}
    evaluador: dict = {}
    if datos.forma == "asignada":
        if not datos.evaluador:
            raise HTTPException(400, "Elige al evaluador.")
        with _negocio():
            evaluador = sev.resolver_evaluador(db, p, datos.evaluador.model_dump(), u.nombre)
        campos.update(evaluador)
    elif datos.forma == "liga_otro_sistema":
        liga = datos.liga_externa_candidato.strip()
        if not liga.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "Captura la liga del otro sistema que recibirá el candidato (https://…).")
        campos["liga_externa_candidato"] = liga[:500]
    elif datos.forma == "integrada":
        prueba = db.query(PruebaPsicometrica).filter(PruebaPsicometrica.id == datos.prueba_id, PruebaPsicometrica.cuenta_id == cuenta.id).first() if datos.prueba_id else None
        if not prueba or not prueba.activa or prueba.modo != "integrada":
            raise HTTPException(400, "Elige una prueba del catálogo conectada a un proveedor integrado.")
        campos.update({"prueba_id": prueba.id, "proveedor": prueba.proveedor or "", "id_proveedor": prueba.id_proveedor or "",
                       "nombre": nombre or prueba.nombre, "paso_integrada": "asignada"})
    if datos.cita and datos.forma != "registro_directo":
        campos.update(await _cita_con_teams(db, p, datos.cita, evaluador))

    ev = sev.nueva(p, cuenta.id, u.nombre, u.id, **campos)
    db.add(ev)
    db.flush()
    sev.asignar_codigo(ev)
    sev.evento(db, ev, "creada", u.nombre, a=ev.estado, usuario_id=u.id, tipo=ev.tipo, forma=ev.forma,
               evaluador=ev.evaluador_nombre, cita=fechas.iso(ev.cita_fecha_hora), consentimiento=ev.consentimiento)

    anterior = p.etapa
    if tipo == "entrevista_humana" and datos.forma == "asignada" and p.etapa in ("Prefiltro", "Entrevista IA", "Evaluación"):
        p.etapa = "Entrevista Humana"
    resultados: list = []
    if datos.forma != "registro_directo":
        resultados = await sev.notificar(db, ev, p, "evaluacion_asignada", u.nombre, override=override_de(datos.notificar))
    registrar(db, u.nombre, "evaluacion_creada", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "tipo": ev.tipo, "forma": ev.forma, "evaluador": ev.evaluador_nombre,
               "cita": fechas.iso(ev.cita_fecha_hora), "de": anterior, "a": p.etapa, "correo_rh": u.correo,
               "notificaciones": resultados})
    _tocar(p)
    db.commit()
    return _respuesta(db, ev, u, p, resultados)


@router.get("/{codigo}")
def detalle(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Ver resultado»: detalle + historial. Abrirla quita la etiqueta «Nuevo resultado». Consultar un resultado
    médico completo queda en bitácora."""
    ev = _ev(db, codigo, cuenta.id)
    if ev.estado == "con_resultado" and ev.resultado_visto_en is None:
        ev.resultado_visto_en = datetime.now(timezone.utc)
    if ev.tipo == "medica" and ev.estado == "con_resultado" and u.puede_ver_informe_medico():
        registrar(db, u.nombre, "informe_medico_consultado", "evaluaciones", ev.codigo, {"correo_rh": u.correo})
    db.commit()
    return {"evaluacion": evaluacion_dict(ev, u), "eventos": [evento_evaluacion_dict(e) for e in sev.eventos_de(db, ev)]}


class ModificarIn(BaseModel):
    nombre: Optional[str] = None
    instrucciones: Optional[str] = None
    liga_externa_candidato: Optional[str] = None
    evaluador: Optional[EvaluadorIn] = None
    cita: Optional[CitaIn] = None
    quitar_cita: bool = False
    notificar: Optional[NotificarIn] = None


@router.patch("/{codigo}")
async def modificar(codigo: str, datos: ModificarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Modificar datos e instrucciones». Si cambia la fecha/hora de la cita es una reprogramación (avisa a ambos)."""
    from .candidatos import _teams_best_effort

    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    if ev.estado in ("con_resultado", "cancelada"):
        raise HTTPException(409, f"Una evaluación «{ESTADOS_EVALUACION_U[ev.estado]}» ya no se modifica.")
    anteriores: dict = {}
    if datos.nombre is not None and datos.nombre.strip() != (ev.nombre or ""):
        if ev.tipo == "otra" and not datos.nombre.strip():
            raise HTTPException(400, "Con «Otra» el nombre es obligatorio.")
        anteriores["nombre"], ev.nombre = ev.nombre, datos.nombre.strip()[:200]
    if datos.instrucciones is not None and datos.instrucciones.strip() != (ev.instrucciones or ""):
        anteriores["instrucciones"], ev.instrucciones = ev.instrucciones, datos.instrucciones.strip()[:4000]
    if datos.liga_externa_candidato is not None and ev.forma == "liga_otro_sistema":
        liga = datos.liga_externa_candidato.strip()
        if not liga.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "La liga del otro sistema debe empezar con https://.")
        if liga != ev.liga_externa_candidato:
            anteriores["liga_externa_candidato"], ev.liga_externa_candidato = ev.liga_externa_candidato, liga[:500]
    if datos.evaluador is not None and ev.forma == "asignada":
        with _negocio():
            nuevo = sev.resolver_evaluador(db, p, datos.evaluador.model_dump(), u.nombre)
        if nuevo["evaluador_nombre"] != ev.evaluador_nombre or nuevo["evaluador_correo"] != ev.evaluador_correo:
            anteriores["evaluador"] = ev.evaluador_nombre
            for k, v in nuevo.items():
                setattr(ev, k, v)
    reprogramada = False
    aviso_teams = None
    if datos.quitar_cita and ev.cita_fecha_hora:
        anteriores["cita_fecha_hora"] = fechas.iso(ev.cita_fecha_hora)
        if ev.teams_evento_id:
            aviso_teams = await _teams_best_effort(db, p, ev, "cancelar")
        for k in ("cita_fecha_hora",):
            setattr(ev, k, None)
        ev.cita_zona_horaria = ev.cita_modalidad = ev.cita_direccion = ev.cita_liga_videollamada = ev.cita_telefono = ev.teams_evento_id = ""
    elif datos.cita is not None:
        cita = await _cita_con_teams(db, p, datos.cita, {"evaluador_correo": ev.evaluador_correo, "evaluador_nombre": ev.evaluador_nombre}) \
            if not (datos.cita.modalidad == "Videollamada" and ev.teams_evento_id and not datos.cita.liga_videollamada) else None
        if cita is None:  # videollamada de Teams existente: se conserva la liga y se mueve la reunión
            with _negocio():
                cita = sev.armar_cita({**datos.cita.model_dump(), "liga_videollamada": ev.cita_liga_videollamada})
            aviso_teams = await _teams_best_effort(db, p, ev, "actualizar", inicio=cita["cita_fecha_hora"])
            cita["teams_evento_id"] = ev.teams_evento_id
        if cita["cita_fecha_hora"] != ev.cita_fecha_hora:
            reprogramada = True
            anteriores["cita_fecha_hora"] = fechas.iso(ev.cita_fecha_hora)
        for k, v in cita.items():
            setattr(ev, k, v)
    if not anteriores and not reprogramada and datos.cita is None:
        return _respuesta(db, ev, u)
    sev.evento(db, ev, "reprogramada" if reprogramada else "modificada", u.nombre, anteriores=anteriores, usuario_id=u.id,
               cita=fechas.iso(ev.cita_fecha_hora))
    resultados = await sev.notificar(db, ev, p, "evaluacion_reprogramada" if reprogramada else "evaluacion_asignada", u.nombre,
                                     override=override_de(datos.notificar)) if (reprogramada or "evaluador" in anteriores) else []
    registrar(db, u.nombre, "evaluacion_modificada", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "cambios": list(anteriores), "reprogramada": reprogramada, "correo_rh": u.correo, "notificaciones": resultados})
    _tocar(p)
    db.commit()
    return _respuesta(db, ev, u, p, resultados, avisoTeams=aviso_teams)


class ReprogramarIn(BaseModel):
    cita: CitaIn
    notificar: Optional[NotificarIn] = None


@router.post("/{codigo}/reprogramar")
async def reprogramar(codigo: str, datos: ReprogramarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """No realizada → Pendiente con cita nueva (o una Pendiente con cita a otra fecha). Avisa a ambos."""
    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    if ev.estado not in ("no_realizada", "pendiente"):
        raise HTTPException(409, "Solo se reprograma una evaluación pendiente o no realizada.")
    anterior_cita = fechas.iso(ev.cita_fecha_hora)
    cita = await _cita_con_teams(db, p, datos.cita, {"evaluador_correo": ev.evaluador_correo, "evaluador_nombre": ev.evaluador_nombre})
    for k, v in cita.items():
        setattr(ev, k, v)
    with _negocio():
        if ev.estado == "no_realizada":
            sev.cambiar_estado(db, ev, "pendiente", u.nombre, usuario_id=u.id, motivo="")
            ev.motivo_estado = ""
    sev.evento(db, ev, "reprogramada", u.nombre, anteriores={"cita_fecha_hora": anterior_cita}, usuario_id=u.id, cita=fechas.iso(ev.cita_fecha_hora))
    resultados = await sev.notificar(db, ev, p, "evaluacion_reprogramada", u.nombre, override=override_de(datos.notificar))
    registrar(db, u.nombre, "evaluacion_reprogramada", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "de": anterior_cita, "a": fechas.iso(ev.cita_fecha_hora), "correo_rh": u.correo, "notificaciones": resultados})
    _tocar(p)
    db.commit()
    return _respuesta(db, ev, u, p, resultados)


@router.post("/{codigo}/realizada")
def marcar_realizada(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Marcar como realizada» → Realizada · Resultado pendiente."""
    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
        bloqueo = sev.bloqueo_consentimiento(ev)
        if bloqueo:
            raise sev.ErrorEvaluacion(409, bloqueo)
        sev.cambiar_estado(db, ev, "realizada_sin_resultado", u.nombre, usuario_id=u.id)
    ev.realizada_en = ev.realizada_en or datetime.now(timezone.utc)
    registrar(db, u.nombre, "evaluacion_marcada_realizada", "postulacion", p.codigo, {"evaluacion": ev.codigo, "correo_rh": u.correo})
    _tocar(p)
    db.commit()
    return _respuesta(db, ev, u, p)


class MotivoIn(BaseModel):
    motivo: str = ""
    notificar: Optional[NotificarIn] = None


@router.post("/{codigo}/no-realizada")
def marcar_no_realizada(codigo: str, datos: MotivoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Marcar como no realizada»: el candidato o el evaluador no se presentó. Después: Reprogramar o Cancelar."""
    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
        sev.cambiar_estado(db, ev, "no_realizada", u.nombre, motivo=datos.motivo.strip() or "No se presentó", usuario_id=u.id)
    registrar(db, u.nombre, "evaluacion_no_realizada", "postulacion", p.codigo, {"evaluacion": ev.codigo, "motivo": ev.motivo_estado, "correo_rh": u.correo})
    _tocar(p)
    db.commit()
    return _respuesta(db, ev, u, p)


@router.post("/{codigo}/cancelar")
async def cancelar(codigo: str, datos: MotivoIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Cancelar (estado final, pide confirmación en la interfaz). Avisa al evaluador y al candidato si había cita.
    No mueve la etapa: RH decide el siguiente paso."""
    from .candidatos import _teams_best_effort

    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
        sev.cambiar_estado(db, ev, "cancelada", u.nombre, motivo=datos.motivo.strip() or "Cancelada por RH", usuario_id=u.id)
    aviso_teams = await _teams_best_effort(db, p, ev, "cancelar") if ev.teams_evento_id else None
    resultados = await sev.notificar(db, ev, p, "evaluacion_cancelada", u.nombre, override=override_de(datos.notificar))
    registrar(db, u.nombre, "evaluacion_cancelada", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "motivo": ev.motivo_estado, "correo_rh": u.correo, "notificaciones": resultados})
    _tocar(p)
    db.commit()
    return _respuesta(db, ev, u, p, resultados, avisoTeams=aviso_teams)


async def _adjuntos_subidos(ev: Evaluacion, archivos: Optional[List[UploadFile]], actor: str, canal: str) -> list:
    salida = []
    for a in archivos or []:
        if not a or not a.filename:
            continue
        validado = fs.validar_bytes(await a.read(), a.filename, f"adjunto «{a.filename}»", fs.FORMATOS_ADJUNTO_EVALUACION)
        ruta = fs.guardar(validado, f"evaluaciones/{ev.id}", f"{ev.codigo}_{secrets.token_hex(4)}")
        salida.append(sev.adjunto_de(validado, ruta, actor, canal))
    return salida


@router.post("/{codigo}/resultado")
async def registrar_resultado(
    codigo: str,
    conclusion: str = Form(""), comentarios: str = Form(""), realizada_por: str = Form(""), version: Optional[int] = Form(None),
    modo: str = Form("registrar"), archivos: Optional[List[UploadFile]] = File(None),
    db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual),
):
    """Formulario único de resultado desde el sistema («Registrar resultado» / «Complementar o corregir»). El mismo
    formulario usa la liga del evaluador (POST /publica/{token}/resultado). NUNCA mueve la etapa del candidato."""
    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    if ev.tipo == "medica" and not u.puede_ver_informe_medico():
        raise HTTPException(403, "Registrar el resultado médico requiere el permiso de informes médicos.")
    adjuntos = await _adjuntos_subidos(ev, archivos, u.nombre, "sistema")
    with _negocio():
        accion = sev.registrar_resultado(db, ev, actor=u.nombre, canal="sistema", conclusion=conclusion, comentarios=comentarios,
                                         realizada_por=realizada_por, adjuntos=adjuntos, version=version, modo=modo, usuario_id=u.id)
    _recalcular_indicador(p)
    registrar(db, u.nombre, f"evaluacion_{accion}", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "conclusion": ev.conclusion, "adjuntos": len(adjuntos), "realizada_por": ev.realizada_por, "correo_rh": u.correo})
    _tocar(p)
    db.commit()
    return _respuesta(db, ev, u, p)


def _recalcular_indicador(p: Postulacion) -> None:
    """Solo el indicador «Apto/No apto» del tablero (sin avisos ni movimiento de etapa)."""
    from .candidatos import _recalcular_resultado_apto

    _recalcular_resultado_apto(p)


class RecordatorioIn(BaseModel):
    a: str = "ambos"  # candidato | evaluador | ambos
    notificar: Optional[NotificarIn] = None


@router.post("/{codigo}/recordatorio")
async def recordatorio(codigo: str, datos: RecordatorioIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    if ev.estado not in ("pendiente", "realizada_sin_resultado"):
        raise HTTPException(409, "Solo se manda recordatorio de una evaluación pendiente.")
    audiencias = {"candidato": {"candidato"}, "evaluador": {"entrevistador"}, "ambos": {"candidato", "entrevistador"}}.get(datos.a)
    if audiencias is None:
        raise HTTPException(400, "Elige a quién: candidato, evaluador o ambos.")
    resultados = await sev.notificar(db, ev, p, "recordatorio_evaluacion", u.nombre, override=override_de(datos.notificar), audiencias=audiencias)
    sev.evento(db, ev, "recordatorio", u.nombre, usuario_id=u.id, a_quien=datos.a)
    registrar(db, u.nombre, "evaluacion_recordatorio", "postulacion", p.codigo, {"evaluacion": ev.codigo, "a": datos.a, "correo_rh": u.correo, "notificaciones": resultados})
    db.commit()
    return _respuesta(db, ev, u, None, resultados)


@router.post("/{codigo}/reenviar-liga")
async def reenviar_liga(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Reenvía la liga al evaluador (y al candidato si hay liga de otro sistema)."""
    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
        bloqueo = sev.bloqueo_consentimiento(ev)
        if bloqueo:
            raise sev.ErrorEvaluacion(409, bloqueo)
    if ev.estado in ("con_resultado", "cancelada"):
        raise HTTPException(409, "Esta evaluación ya no espera respuesta.")
    audiencias = {"entrevistador"} | ({"candidato"} if ev.forma == "liga_otro_sistema" else set())
    resultados = await sev.notificar(db, ev, p, "evaluacion_asignada", u.nombre, audiencias=audiencias)
    registrar(db, u.nombre, "evaluacion_liga_reenviada", "postulacion", p.codigo, {"evaluacion": ev.codigo, "correo_rh": u.correo, "notificaciones": resultados})
    db.commit()
    return _respuesta(db, ev, u, None, resultados)


@router.post("/{codigo}/enviar")
async def enviar_proveedor(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Proveedor integrado: manda la evaluación. Psicométricas.mx real si está configurado (agregaCandidato); si no,
    modo integrado simulado (Asignada → Enviada)."""
    from ..services import psicometricas as psi

    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
        bloqueo = sev.bloqueo_consentimiento(ev)
        if bloqueo:
            raise sev.ErrorEvaluacion(409, bloqueo)
    if ev.forma != "integrada" or ev.estado != "pendiente" or (ev.paso_integrada or "asignada") != "asignada":
        raise HTTPException(409, "Solo se envía al proveedor una evaluación integrada pendiente.")
    if sev.usa_psicometricas(ev) and psi.configurado():
        if not p.correo:
            raise HTTPException(409, "Psicométricas.mx necesita el correo del candidato para mandarle su liga.")
        try:
            clave = psi.agregar_candidato(p.nombre, p.correo, p.vacante.titulo if p.vacante else ev.nombre_visible, psi.tests_de(ev.id_proveedor))
        except psi.PsicometricasError as ex:
            raise HTTPException(400 if ex.status == 400 else 502, str(ex))
        ev.clave_proveedor = clave
        sev.aplicar_paso(db, ev, "enviada", u.nombre, "Psicométricas.mx")
    else:
        sev.aplicar_paso(db, ev, "enviada", u.nombre)
    registrar(db, u.nombre, "evaluacion_enviada", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "proveedor": ev.proveedor, "clave_proveedor": ev.clave_proveedor, "correo_rh": u.correo})
    db.commit()
    return _respuesta(db, ev, u)


@router.post("/{codigo}/integracion/avanzar")
def avanzar_integrada(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Modo integrado SIMULADO (proveedores sin conexión): Enviada → Iniciada → Completada. El resultado se registra con
    el formulario único."""
    ev = _ev(db, codigo, cuenta.id)
    if ev.forma != "integrada" or ev.clave_proveedor:
        raise HTTPException(409, "Solo las evaluaciones integradas simuladas avanzan por pasos.")
    paso = sev.siguiente_paso(ev)
    if not paso or paso == "resultado_recibido":
        raise HTTPException(409, "Registra el resultado con «Registrar resultado».")
    with _negocio():
        sev.aplicar_paso(db, ev, paso, u.nombre)
    db.commit()
    return _respuesta(db, ev, u)


@router.post("/{codigo}/sincronizar")
def sincronizar(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Consultar resultado» en Psicométricas.mx (por si el webhook no llegó). Solo guarda si su API confirma que terminó."""
    from ..services import psicometricas as psi

    ev = _ev(db, codigo, cuenta.id)
    if not ev.clave_proveedor:
        raise HTTPException(409, "Esta evaluación no está conectada a Psicométricas.mx.")
    try:
        with _negocio():
            r = sev.sincronizar_psicometricas(db, ev)
    except psi.PsicometricasError as ex:
        raise HTTPException(502, str(ex))
    registrar(db, u.nombre, "evaluacion_sincronizada", "evaluaciones", ev.codigo, {"resultado": r, "correo_rh": u.correo})
    db.commit()
    return _respuesta(db, ev, u, sincronizacion=r)


def _adjunto(ev: Evaluacion, aid: str) -> dict:
    a = next((x for x in (ev.adjuntos or []) if x.get("id") == aid), None)
    if not a or not fs.existe(a.get("archivo")):
        raise HTTPException(404, "Adjunto no disponible.")
    return a


@router.get("/{codigo}/adjuntos/{aid}")
def descargar_adjunto(codigo: str, aid: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Los adjuntos heredan los permisos de la evaluación (médica: solo con permiso de informes médicos)."""
    ev = _ev(db, codigo, cuenta.id)
    if ev.tipo == "medica" and not u.puede_ver_informe_medico():
        registrar(db, u.nombre, "informe_medico_acceso_denegado", "evaluaciones", ev.codigo, {"correo_rh": u.correo})
        db.commit()
        raise HTTPException(403, "El informe médico completo solo lo ven usuarios con permiso; tú ves el estado y la conclusión.")
    a = _adjunto(ev, aid)
    if ev.tipo == "medica":
        registrar(db, u.nombre, "informe_medico_consultado", "evaluaciones", ev.codigo, {"adjunto": a.get("nombre"), "correo_rh": u.correo})
        db.commit()
    return FileResponse(a["archivo"], media_type=a.get("mime") or "application/octet-stream", filename=a.get("nombre") or "adjunto")


@router.post("/{codigo}/consentimiento/enviar")
async def enviar_liga_consentimiento(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Manda al candidato la liga del consentimiento expreso de la evaluación médica (WhatsApp y correo con lo que
    tenga). Un canal caído nunca rompe la acción: el resultado de cada envío regresa a RH."""
    from ..services import plantillas_correo
    from ..services.correo import enviar_correo
    from ..services.whatsapp import enviar_mensaje

    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    if ev.consentimiento != "pendiente" or not ev.consentimiento_token:
        raise HTTPException(409, "Esta evaluación no está en espera de consentimiento.")
    liga = sev.liga_consentimiento(ev)
    empresa = nombre_empresa_candidato(p.vacante) if p.vacante else cuenta.nombre_visible
    texto = (f"Hola {p.nombre}. Para continuar con tu proceso en {empresa} necesitamos tu consentimiento por escrito para la evaluación "
             f"médica. Léelo y, si estás de acuerdo, acéptalo aquí: {liga}")
    resultados = []
    if p.telefono:
        try:
            r = await enviar_mensaje(p.telefono, texto)
        except Exception as ex:  # noqa: BLE001
            r = {"enviado": False, "detalle": str(ex)[:200]}
        resultados.append({"destinatario": "candidato", "canal": "whatsapp", "destino": p.telefono, "enviado": bool(r.get("enviado")), "detalle": str(r.get("detalle") or "")})
    if p.correo:
        try:
            asunto, html = plantillas_correo.html_aviso("Consentimiento para tu evaluación médica", texto.replace(liga, "").strip(), empresa, [], ("Leer y responder", liga))
            r = await enviar_correo(p.correo, asunto, html)
        except Exception as ex:  # noqa: BLE001
            r = {"enviado": False, "detalle": str(ex)[:200]}
        resultados.append({"destinatario": "candidato", "canal": "correo", "destino": p.correo, "enviado": bool(r.get("enviado")), "detalle": str(r.get("detalle") or "")})
    sev.evento(db, ev, "envio", u.nombre, usuario_id=u.id, que="liga_consentimiento", envios=resultados)
    registrar(db, u.nombre, "consentimiento_medico_solicitado", "postulacion", p.codigo, {"evaluacion": ev.codigo, "envios": resultados, "correo_rh": u.correo})
    db.commit()
    return {"liga": liga, "resultados": resultados, "advertencias": notificaciones.advertencias_de(resultados)}


# ---------------- Pública: liga del evaluador (sin cuenta; el token es la credencial) ----------------

def _por_token(db: Session, token: str) -> Evaluacion:
    try:
        return sev.por_token(db, token)
    except sev.ErrorEvaluacion as e:
        raise HTTPException(e.status, e.mensaje)


def _expediente_para_evaluador(db: Session, ev: Evaluacion, p: Postulacion) -> dict:
    """Solo lo necesario para realizar ESTA evaluación: candidato, vacante y CV; en la entrevista humana además el
    análisis de CV, la Entrevista Red Human (solo lectura), capacitación y documentos (como hasta hoy)."""
    c = p.candidato
    v = p.vacante
    cv = dict((c.cv_datos or {}) if c else {})
    base = {
        "candidato": {"nombre": p.nombre or "", "telefono": (c.telefono if c else "") or "", "correo": (c.correo if c else "") or ""},
        "vacante": {"titulo": v.titulo if v else "", "requisitos": (v.requisitos if v else "") or "", "perfilIdeal": (v.perfil_ideal if v else "") or "",
                    "empresa": nombre_empresa_candidato(v) if v else ""},
        "cv": {"resumen": cv.get("resumen_profesional") or "", "habilidades": cv.get("habilidades") or [], "estudios": cv.get("estudios") or [],
               "idiomas": cv.get("idiomas") or [], "experiencia": cv.get("experiencia") or cv.get("experiencia_laboral") or [],
               "anosExperiencia": cv.get("anos_experiencia")},
        "archivos": [{"id": x.id, "tipo": x.tipo, "nombre": x.nombre, "mime": x.mime} for x in (c.archivos if c else [])],
        "etapa": p.etapa or "", "score": None,
        "analisis": None, "entrevistaIA": None, "capacitacion": [], "documentos": [],
    }
    if ev.tipo != "entrevista_humana":
        return base
    a = dict(p.analisis or {})
    ultima_ia = next((e.evaluacion for e in reversed(p.entrevistas or []) if e.estado == "evaluada" and e.evaluacion), None)
    exp = p.expediente
    base.update({
        "score": p.score,  # afinidad del Análisis de CV (como en la liga anterior del entrevistador)
        "analisis": {"requisitosCumplidos": a.get("requisitos_cumplidos") or [], "brechas": a.get("brechas") or [],
                     "fortalezas": a.get("fortalezas_cv") or [], "alertas": a.get("alertas") or [], "resumen": a.get("resumen") or ""},
        "entrevistaIA": {"matchPerfil": ultima_ia.get("match_perfil"), "recomendacion": ultima_ia.get("recomendacion") or "",
                         "resumen": ultima_ia.get("resumen") or "", "fortalezas": ultima_ia.get("fortalezas") or [],
                         "riesgos": ultima_ia.get("riesgos") or [], "faltante": ultima_ia.get("faltante") or []} if ultima_ia else None,
        "capacitacion": a.get("capacitacion") or [],
        "documentos": [{"tipo": d.tipo, "estado": d.estado, "obligatorio": d.obligatorio} for d in (exp.documentos if exp else []) if not getattr(d, "interno", False)],
    })
    return base


@router.get("/publica/{token}")
def publica(token: str, db: Session = Depends(get_db)):
    """Liga del evaluador: su evaluación + lo necesario para realizarla. Si RH ya registró un resultado lo ve en solo
    lectura con «Agregar complemento». Cancelada → «Esta evaluación fue cancelada»."""
    ev = _por_token(db, token)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    registrada_por_rh = ev.estado == "con_resultado" and ev.registrada_via == "sistema"
    return {
        "evaluacion": evaluacion_dict(ev, publico=True),
        "cancelada": ev.estado == "cancelada",
        "enEsperaConsentimiento": bool(sev.bloqueo_consentimiento(ev)),
        "yaTieneResultado": ev.estado == "con_resultado",
        "avisoResultadoRh": (f"RH ya registró un resultado el {fechas.local(ev.registrada_en).strftime('%d/%m/%Y')}" if registrada_por_rh and ev.registrada_en else ""),
        "expediente": _expediente_para_evaluador(db, ev, p),
        "empresa": nombre_empresa_candidato(p.vacante) if p.vacante else "",
    }


@router.post("/publica/{token}/resultado")
async def resultado_publico(
    token: str, request: Request,
    conclusion: str = Form(""), comentarios: str = Form(""), realizada_por: str = Form(""), version: Optional[int] = Form(None),
    modo: str = Form("registrar"), archivos: Optional[List[UploadFile]] = File(None),
    db: Session = Depends(get_db),
):
    """El MISMO formulario de resultado desde la liga del evaluador. Si ya hay resultado solo agrega complemento (nunca
    sobrescribe en silencio). Avisa a RH. NUNCA mueve la etapa del candidato ni lo aprueba."""
    ev = _por_token(db, token)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    actor = ev.evaluador_nombre or "Evaluador vía liga"
    adjuntos = await _adjuntos_subidos(ev, archivos, actor, "liga_evaluador")
    with _negocio():
        accion = sev.registrar_resultado(db, ev, actor=actor, canal="liga_evaluador", conclusion=conclusion, comentarios=comentarios,
                                         realizada_por=realizada_por or ev.evaluador_nombre, adjuntos=adjuntos, version=version, modo=modo)
    _recalcular_indicador(p)
    aviso_rh = await sev.notificar_rh_resultado(db, ev, p)
    registrar(db, actor, f"evaluacion_{accion}_por_liga", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "conclusion": ev.conclusion, "adjuntos": len(adjuntos), "aviso_rh": aviso_rh,
               "ip": (request.client.host if request.client else "")[:64]})
    _tocar(p)
    db.commit()
    return {"ok": True, "accion": accion, "evaluacion": evaluacion_dict(ev, publico=True)}


@router.get("/publica/{token}/adjuntos/{aid}")
def adjunto_publico(token: str, aid: str, db: Session = Depends(get_db)):
    ev = _por_token(db, token)
    a = _adjunto(ev, aid)
    return FileResponse(a["archivo"], media_type=a.get("mime") or "application/octet-stream", filename=a.get("nombre") or "adjunto")


@router.get("/publica/{token}/archivo/{archivo_id}")
def archivo_publico(token: str, archivo_id: int, db: Session = Depends(get_db)):
    """CV u otro archivo del candidato para el evaluador (la liga es la credencial)."""
    ev = _por_token(db, token)
    arch = db.query(Archivo).filter(Archivo.id == archivo_id, Archivo.candidato_id == ev.candidato_id).first()
    if not arch or not arch.ruta or not fs.existe(arch.ruta):
        raise HTTPException(404, "Archivo no disponible.")
    return FileResponse(arch.ruta, media_type=arch.mime or "application/octet-stream", filename=arch.nombre or "archivo")


# ---------------- Pública: consentimiento expreso de la evaluación médica ----------------

def _por_token_consentimiento(db: Session, token: str) -> Evaluacion:
    ev = db.query(Evaluacion).filter(Evaluacion.consentimiento_token == token).first() if token else None
    if not ev:
        raise HTTPException(404, "Esta liga no es válida.")
    return ev


def _texto_consentimiento(p: Postulacion) -> str:
    empresa = nombre_empresa_candidato(p.vacante) if p.vacante else "la empresa"
    return TEXTO_CONSENTIMIENTO_MEDICO.format(nombre=(p.nombre or "la persona candidata"), empresa=empresa,
                                              puesto=(p.vacante.titulo if p.vacante else "el puesto"))


@router.get("/publica/consentimiento/{token}")
def ver_consentimiento(token: str, db: Session = Depends(get_db)):
    ev = _por_token_consentimiento(db, token)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    return {
        "candidato": p.nombre or "", "empresa": nombre_empresa_candidato(p.vacante) if p.vacante else "",
        "puesto": p.vacante.titulo if p.vacante else "", "evaluacion": ev.nombre_visible,
        "texto": ev.consentimiento_texto or _texto_consentimiento(p),
        "aceptado": ev.consentimiento == "otorgado", "rechazado": ev.consentimiento == "rechazado",
        "aceptadoEn": fechas.iso(ev.consentimiento_en), "cancelada": ev.estado == "cancelada",
    }


class AceptarConsentimientoIn(BaseModel):
    nombre: str
    acepto: bool = False


@router.post("/publica/consentimiento/{token}/aceptar")
async def aceptar_consentimiento(token: str, datos: AceptarConsentimientoIn, request: Request, db: Session = Depends(get_db)):
    """Consentimiento EXPRESO y POR ESCRITO por medio electrónico: la persona escribe su nombre completo como firma y
    marca «Acepto». Se guarda el texto exacto, la aceptación y la evidencia (nombre, IP, navegador, huella SHA-256) y
    queda en la bitácora. Al otorgarse, la liga sale al evaluador (antes no se le manda nada)."""
    ev = _por_token_consentimiento(db, token)
    if ev.estado == "cancelada":
        raise HTTPException(409, "Esta evaluación fue cancelada.")
    if ev.consentimiento == "otorgado":
        raise HTTPException(409, "Ya habías otorgado tu consentimiento. Gracias.")
    firma = " ".join((datos.nombre or "").split())
    if not datos.acepto:
        raise HTTPException(400, "Para otorgar tu consentimiento marca «Acepto».")
    if len(firma) < 5:
        raise HTTPException(400, "Escribe tu nombre completo como firma.")
    with _negocio():
        p = sev.postulacion_de(db, ev)
    ahora = datetime.now(timezone.utc)
    texto = _texto_consentimiento(p)
    huella = hashlib.sha256(f"{texto}|{firma}|{ahora.isoformat()}|{ev.codigo}".encode("utf-8")).hexdigest()
    anterior = ev.consentimiento
    ev.consentimiento, ev.consentimiento_texto, ev.consentimiento_en = "otorgado", texto, ahora
    ev.consentimiento_evidencia = {
        "nombre_escrito": firma[:200], "ip": (request.client.host if request.client else "")[:64],
        "navegador": (request.headers.get("user-agent") or "")[:300], "medio": "electronico", "huella_sha256": huella,
    }
    sev.evento(db, ev, "consentimiento", firma, "liga_candidato", anteriores={"consentimiento": anterior}, valor="otorgado", huella_sha256=huella)
    resultados = []
    if ev.forma == "asignada" and ev.estado == "pendiente":
        resultados = await sev.notificar(db, ev, p, "evaluacion_asignada", "sistema", audiencias={"entrevistador"})
    registrar(db, "candidato", "consentimiento_medico_otorgado", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "huella_sha256": huella, "nombre_escrito": firma[:200], "aviso_evaluador": resultados})
    db.commit()
    return {"ok": True, "aceptadoEn": fechas.iso(ahora), "estado": ev.estado}


@router.post("/publica/consentimiento/{token}/rechazar")
def rechazar_consentimiento(token: str, request: Request, db: Session = Depends(get_db)):
    """El candidato no otorga su consentimiento. La evaluación conserva su estado; la única acción de RH es Cancelar."""
    ev = _por_token_consentimiento(db, token)
    if ev.estado == "cancelada":
        raise HTTPException(409, "Esta evaluación fue cancelada.")
    if ev.consentimiento == "otorgado":
        raise HTTPException(409, "Ya habías otorgado tu consentimiento.")
    with _negocio():
        p = sev.postulacion_de(db, ev)
    anterior = ev.consentimiento
    ev.consentimiento = "rechazado"
    sev.evento(db, ev, "consentimiento", p.nombre or "Candidato", "liga_candidato", anteriores={"consentimiento": anterior}, valor="rechazado",
               ip=(request.client.host if request.client else "")[:64])
    registrar(db, "candidato", "consentimiento_medico_rechazado", "postulacion", p.codigo, {"evaluacion": ev.codigo})
    db.commit()
    return {"ok": True, "rechazado": True}
