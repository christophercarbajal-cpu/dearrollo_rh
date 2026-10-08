"""Evaluaciones unificadas — Fase 1 (2026-09-29) + catálogo de Pruebas psicométricas (2026-09-28).

* Catálogo (Configuración → Pruebas psicométricas): identificador interno, nombre visible, descripción, puestos
  sugeridos, modo (Integrada / Enlace externo / Carga manual), proveedor, identificador en el proveedor y estado.
* Evaluaciones: UN objeto (`Evaluacion`) para entrevista humana, médica, psicométrica, socioeconómica, técnica,
  referencias u otra. Pantalla única «Agregar evaluación» (POST /postulaciones/{codigo}), formulario único de
  resultado (POST /{codigo}/resultado desde el sistema y POST /publica/{token}/resultado desde la liga del
  evaluador), cinco estados, consentimiento médico como condición, historial en `eventos_evaluacion`.
* Recibir, guardar o REVISAR un resultado NUNCA mueve la etapa del candidato ni lo envía a Contratación; agregar una
  evaluación tampoco, SALVO crear una entrevista humana: mueve a «Entrevista Humana» (Filtro humano) desde
  Prefiltro / Filtro Red Human (2026-10-01, pipeline de 5 columnas; `candidatos.mover_por_entrevista_humana`).
* Ligas externas (consentimiento, evaluador/médico, otro sistema, proveedor): existen aunque el envío falle; Abrir /
  Copiar / Enviar o reenviar (POST /{codigo}/ligas/{clave}/enviar) y el estado de cada envío viaja aparte.
* Consentimiento médico EXPRESO y POR ESCRITO: liga pública `/consentimiento/{token}` (texto exacto + evidencia).
* Informe médico COMPLETO (comentarios y adjuntos) solo para quien tiene permiso (`Usuario.puede_ver_informe_medico`).
"""

import asyncio
import hashlib
import re
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
    TIPOS_EVALUACION_U,
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
from ..services import proceso as sproc
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
    # 2026-10-07: el formulario solo pide Nombre, Tipo, Identificador en el proveedor y Activa; el identificador
    # interno se genera solo si no llega (las capturas previas se siguen aceptando).
    clave: str = ""
    nombre: str
    tipo: str = "prueba"
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
    tipo: Optional[str] = None
    descripcion: Optional[str] = None
    puestos: Optional[List[str]] = None
    modo: Optional[str] = None
    proveedor: Optional[str] = None
    id_proveedor: Optional[str] = None
    url: Optional[str] = None
    activa: Optional[bool] = None


def _clave_automatica(db: Session, cuenta_id: int, nombre: str, pid: int) -> str:
    """Identificador interno a partir del nombre («Cleaver + Terman» → PSI-CLEAVER-TERMAN), único en la Cuenta."""
    base = "PSI-" + (re.sub(r"[^A-Z0-9]+", "-", _norm(nombre).upper()).strip("-")[:48] or "PRUEBA")
    usadas = {_norm(o.clave) for o in db.query(PruebaPsicometrica).filter(PruebaPsicometrica.cuenta_id == cuenta_id, PruebaPsicometrica.id != (pid or 0)).all()}
    clave, i = base, 1
    while _norm(clave) in usadas:
        i += 1
        clave = f"{base}-{i}"
    return clave


def _validar_prueba(db: Session, cuenta_id: int, pr: PruebaPsicometrica) -> None:
    from ..models import TIPOS_PRUEBA

    pr.nombre = (pr.nombre or "").strip()[:200]
    if not pr.nombre:
        raise HTTPException(400, "Captura el nombre visible de la prueba.")
    pr.clave = (pr.clave or "").strip()[:60] or _clave_automatica(db, cuenta_id, pr.nombre, pr.id)
    if (pr.tipo or "prueba") not in TIPOS_PRUEBA:
        raise HTTPException(400, "Tipo inválido: usa prueba o batería.")
    pr.tipo = pr.tipo or "prueba"
    pr.id_proveedor = (pr.id_proveedor or "").strip()[:150]
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
# Ningún resultado mueve la etapa. Agregar solo mueve con la entrevista humana (→ Filtro humano, 2026-10-01).
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
    lista = sev.de_postulacion(db, p)
    if any([sev.asegurar_ligas(ev) for ev in lista]):  # registros viejos sin token: la liga se genera una vez y se reutiliza
        db.commit()
    return [evaluacion_dict(ev, u) for ev in lista]


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
    proveedor: str = ""  # psicométrica fuera del catálogo integrado (texto libre; nunca dispara una API)
    cita: Optional[CitaIn] = None
    notificar: Optional[NotificarIn] = None
    # Proceso configurable (2026-10-06): paso del proceso que cumple esta evaluación («Iniciar» en el seguimiento). Vacío
    # = se liga sola al primer paso de ese tipo que aún no tenga evaluación; sin paso, queda fuera del proceso.
    paso_id: str = ""


def _guion_entrevista_humana(p: Postulacion, paso: Optional[dict]) -> dict:
    """Cada tipo de entrevista humana genera su guion (general, técnica, con jefe directo, valores). Nunca bloquea."""
    from ..models import TIPOS_ENTREVISTA_HUMANA
    from ..services import ia

    tipo = (paso or {}).get("tipo_entrevista") or "general"
    v = p.vacante
    try:
        g, con_ia = ia.guion_entrevista_humana(tipo, v.titulo if v else "la vacante", (v.requisitos if v else "") or "", p.experiencia or "")
    except Exception:  # noqa: BLE001
        return {}
    return {**g.model_dump(), "tipo": tipo, "tipoTexto": TIPOS_ENTREVISTA_HUMANA.get(tipo, tipo), "ia": con_ia}


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
    2026-10-01 (pipeline de 5 columnas): crear una ENTREVISTA HUMANA mueve al candidato a Filtro humano si está en
    Prefiltro o Filtro Red Human; cualquier otro tipo NO mueve la etapa. Las ligas externas se generan aunque el envío
    automático falle y el estado de cada envío regresa aparte."""
    p = _postulacion(db, codigo, cuenta.id)
    if not p.activa:
        raise HTTPException(409, "La postulación está cerrada.")
    with _negocio():
        tipo = sev.normalizar_tipo(datos.tipo)
    if datos.forma not in FORMAS_EVALUACION:
        raise HTTPException(400, f"Forma inválida. Usa una de: {', '.join(FORMAS_EVALUACION)}.")
    try:
        paso = sproc.paso_para_evaluacion(p, tipo, datos.paso_id.strip())
    except sproc.ErrorProceso as e:
        raise HTTPException(e.status, e.mensaje)
    nombre = datos.nombre.strip()[:200]
    if not nombre and paso and paso["nombre"] != TIPOS_EVALUACION_U.get(tipo):
        nombre = paso["nombre"][:200]  # el nombre que le dio el proceso («Entrevista con jefe directo», …)
    if tipo == "otra" and not nombre:
        raise HTTPException(400, "Con «Otra» captura el nombre de la evaluación.")
    if tipo != "entrevista_humana" and not p.consentimiento:
        raise HTTPException(409, "Falta el consentimiento de privacidad del candidato (LFPDPPP).")

    campos: dict = {"tipo": tipo, "nombre": nombre, "forma": datos.forma, "instrucciones": datos.instrucciones.strip()[:4000],
                    "paso_id": paso["id"] if paso else ""}
    if tipo == "entrevista_humana":
        campos["guion"] = _guion_entrevista_humana(p, paso)
    if tipo == "psicometrica" and datos.forma != "integrada":
        campos["proveedor"] = datos.proveedor.strip()[:150]
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
    sev.asegurar_ligas(ev)
    db.add(ev)
    db.flush()
    sev.asignar_codigo(ev)
    if paso is None:
        # 2026-10-06: fuera de la ruta = actividad AD HOC solo de esta postulación (la plantilla y la vacante no cambian);
        # así aparece en el seguimiento con su estado y resultado. Nunca bloquea la creación.
        try:
            sproc.paso_adhoc_para_evaluacion(db, p, ev, u)
        except Exception as ex:  # noqa: BLE001
            print(f"[proceso] no se pudo registrar la evaluación {ev.codigo} como actividad ad hoc: {ex}")
    sev.evento(db, ev, "creada", u.nombre, a=ev.estado, usuario_id=u.id, tipo=ev.tipo, forma=ev.forma,
               evaluador=ev.evaluador_nombre, cita=fechas.iso(ev.cita_fecha_hora), consentimiento=ev.consentimiento,
               proveedor=ev.proveedor)
    movida = False
    if ev.tipo == "entrevista_humana":
        from .candidatos import mover_por_entrevista_humana

        movida = mover_por_entrevista_humana(db, p, u, ev.codigo)
    resultados: list = []
    if datos.forma != "registro_directo":
        resultados = await sev.notificar(db, ev, p, "evaluacion_asignada", u.nombre, override=override_de(datos.notificar))
    registrar(db, u.nombre, "evaluacion_creada", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "tipo": ev.tipo, "forma": ev.forma, "evaluador": ev.evaluador_nombre,
               "cita": fechas.iso(ev.cita_fecha_hora), "etapa": p.etapa, "movida_a_filtro_humano": movida,
               "paso_proceso": ev.paso_id, "correo_rh": u.correo, "notificaciones": resultados})
    _tocar(p)
    db.commit()
    aviso_proceso = ""
    if ev.tipo == "entrevista_humana" and not movida and p.activa and p.etapa in ("Prefiltro", "Entrevista IA") and sproc.tiene_proceso(p):
        aviso_proceso = ("El candidato sigue en su columna: el proceso tiene pasos obligatorios sin cumplir antes de Filtro humano "
                         "(complétalos u omítelos con autorización desde «Seguimiento»).")
    return _respuesta(db, ev, u, p, resultados, movidaAFiltroHumano=movida, avisoProceso=aviso_proceso)


@router.get("/{codigo}")
def detalle(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Ver resultado»: detalle + historial. Abrirla quita la etiqueta «Nuevo resultado». Consultar un resultado
    médico completo queda en bitácora."""
    ev = _ev(db, codigo, cuenta.id)
    sev.asegurar_ligas(ev)
    if ev.estado == "con_resultado" and ev.resultado_visto_en is None:
        ev.resultado_visto_en = datetime.now(timezone.utc)
    if ev.tipo == "medica" and ev.estado == "con_resultado" and u.puede_ver_informe_medico():
        registrar(db, u.nombre, "informe_medico_consultado", "evaluaciones", ev.codigo, {"correo_rh": u.correo})
    db.commit()
    return {"evaluacion": evaluacion_dict(ev, u), "eventos": [evento_evaluacion_dict(e) for e in sev.eventos_de(db, ev)]}


class ModificarIn(BaseModel):
    nombre: Optional[str] = None
    proveedor: Optional[str] = None  # solo psicométrica no integrada
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
    if datos.proveedor is not None and ev.tipo == "psicometrica" and ev.forma != "integrada" and datos.proveedor.strip() != (ev.proveedor or ""):
        anteriores["proveedor"], ev.proveedor = ev.proveedor, datos.proveedor.strip()[:150]
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
    await sproc.avanzar_seguro(db, p)  # interruptor de avance automático de la etapa (solo si está encendido)
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
    if sev.usa_psicometricas(ev) and ev.clave_proveedor and "candidato" in audiencias:
        # 2026-10-07: psicometría del proveedor → «Recordatorio de psicometría pendiente» (portal + clave, regla propia)
        if (sev.estado_proveedor(ev) or ("",))[0] == "completada":
            raise HTTPException(409, "El candidato ya terminó su psicometría.")
        resultados = await sev.notificar_psicometria(db, ev, p, u.nombre, usuario_id=u.id, evento_notificacion="recordatorio_psicometria")
        ev.recordatorios_psicometria = (ev.recordatorios_psicometria or 0) + 1
        ev.recordatorio_psicometria_en = datetime.now(timezone.utc)
        sev.evento(db, ev, "recordatorio", u.nombre, usuario_id=u.id, a_quien="candidato")
        registrar(db, u.nombre, "psicometria_recordatorio", "postulacion", p.codigo, {"evaluacion": ev.codigo, "correo_rh": u.correo, "notificaciones": resultados})
        db.commit()
        return _respuesta(db, ev, u, None, resultados)
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
    if ev.estado == "cancelada":  # con resultado sigue: el evaluador puede complementar
        raise HTTPException(409, "Esta evaluación fue cancelada.")
    audiencias = {"entrevistador"} | ({"candidato"} if ev.forma == "liga_otro_sistema" else set())
    resultados = await sev.notificar(db, ev, p, "evaluacion_asignada", u.nombre, audiencias=audiencias)
    registrar(db, u.nombre, "evaluacion_liga_reenviada", "postulacion", p.codigo, {"evaluacion": ev.codigo, "correo_rh": u.correo, "notificaciones": resultados})
    db.commit()
    return _respuesta(db, ev, u, None, resultados)


def _activar_psicometria(db: Session, ev: Evaluacion, p) -> str:
    """Da de alta al candidato en Psicométricas.mx (agregaCandidato) y deja su `clave_proveedor` en la evaluación.

    Cuida el saldo de la API (lo compartimos con producción): exige correo ANTES de llamar y llama SOLO si el candidato
    no tiene ya una clave EN CURSO para esas pruebas — la de esta evaluación o la de otra evaluación pendiente de la
    misma postulación con el mismo proveedor y las mismas pruebas (`id_proveedor`), que se reutiliza. Una aplicación ya
    con resultado no se reutiliza: pedir otra vez la misma prueba es un re-test y sí da de alta una clave nueva."""
    from ..services import psicometricas as psi

    if ev.clave_proveedor:
        return ev.clave_proveedor
    if not (p.correo or "").strip():
        raise HTTPException(409, "Psicométricas.mx necesita el correo del candidato para mandarle su liga.")
    previa = (
        db.query(Evaluacion)
        .filter(Evaluacion.postulacion_id == ev.postulacion_id, Evaluacion.id != ev.id, Evaluacion.tipo == "psicometrica",
                Evaluacion.clave_proveedor != "", Evaluacion.estado == "pendiente")
        .all()
    )
    tests = psi.tests_de(ev.id_proveedor)
    previa = next((x for x in previa if psi.es_psicometricas(x.proveedor) and psi.tests_de(x.id_proveedor) == tests), None)
    if previa:
        ev.clave_proveedor = previa.clave_proveedor
        registrar(db, "sistema", "psicometria_clave_reutilizada", "evaluaciones", ev.codigo,
                  {"clave_proveedor": previa.clave_proveedor, "de": previa.codigo})
        return ev.clave_proveedor
    try:
        alta = psi.asignar_candidato(p.nombre, p.correo, p.vacante.titulo if p.vacante else ev.nombre_visible, tests)
    except psi.PsicometricasError as ex:
        raise HTTPException(400 if ex.status == 400 else 502, str(ex))
    ev.clave_proveedor = alta["clave"]
    if alta["liga"]:
        ev.liga_externa_candidato = alta["liga"][:500]
    registrar(db, "sistema", "psicometria_alta_proveedor", "evaluaciones", ev.codigo,
              {"clave_proveedor": alta["clave"], "liga": alta["liga"], "respuesta": alta["respuesta"]})
    return ev.clave_proveedor


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
    real = sev.usa_psicometricas(ev) and psi.configurado()
    if real:
        _activar_psicometria(db, ev, p)
        sev.aplicar_paso(db, ev, "enviada", u.nombre, "Psicométricas.mx")
    else:
        sev.aplicar_paso(db, ev, "enviada", u.nombre)
    registrar(db, u.nombre, "evaluacion_enviada", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "proveedor": ev.proveedor, "clave_proveedor": ev.clave_proveedor, "correo_rh": u.correo})
    db.commit()
    if not real:
        return _respuesta(db, ev, u)
    resultados = await sev.notificar_psicometria(db, ev, p, u.nombre, usuario_id=u.id)
    registrar(db, "sistema", "psicometria_notificada", "evaluaciones", ev.codigo, {"clave_proveedor": ev.clave_proveedor, "envios": resultados})
    db.commit()
    envio = {"enviado": any(r["enviado"] for r in resultados), "conLiga": True,
             "canales": [r["canal"] for r in resultados if r["enviado"]],
             "detalle": "; ".join(f"{r['canal'] or 'sin canal'}: {r['detalle']}" for r in resultados if not r["enviado"])}
    return _respuesta(db, ev, u, resultados=resultados, envioCandidato=envio)


# ---------------- Psicometría: «Asignar y enviar» (2026-10-07, asignación simplificada) ----------------
# Vista limpia: la batería sale de la actividad de la ruta (o de la vacante/ruta vigente si la copia del candidato no
# la trae); RH solo confirma o cambia la selección. Una sola acción da de alta en el proveedor, guarda la clave y avisa
# al candidato por Notificaciones. Si el proveedor falla NO se guarda nada (ni la evaluación ni «Enviada»). Doble clic /
# dos pestañas: candado por postulación + verificación en base de una asignación viva con las mismas pruebas.

_candados_psicometria: dict = {}


def _tests(id_proveedor: str) -> str:
    """Identificadores del proveedor normalizados («1, 7» → «1,7») sin lanzar con capturas no numéricas (legado)."""
    from ..services import psicometricas as psi

    try:
        return psi.tests_de(id_proveedor)
    except psi.PsicometricasError:
        return ",".join(x.strip() for x in (id_proveedor or "").split(",") if x.strip())


def _candado_psicometria(postulacion_id: int) -> asyncio.Lock:
    return _candados_psicometria.setdefault(postulacion_id, asyncio.Lock())


def _catalogo_integrado(db: Session, cuenta_id: int) -> List[PruebaPsicometrica]:
    return (db.query(PruebaPsicometrica)
            .filter(PruebaPsicometrica.cuenta_id == cuenta_id, PruebaPsicometrica.activa.is_(True), PruebaPsicometrica.modo == "integrada")
            .order_by(PruebaPsicometrica.nombre).all())


def _pruebas_de_ruta(proc: dict, paso_id: str = "") -> List[int]:
    pasos = [x for x in (proc or {}).get("pasos", []) if x.get("tipo") == "psicometrica" and x.get("pruebas")]
    propio = next((x for x in pasos if x.get("id") == paso_id), None) if paso_id else None
    elegido = propio or (pasos[0] if pasos else None)
    return [int(i) for i in (elegido or {}).get("pruebas", [])]


def bateria_predeterminada(db: Session, p: Postulacion, paso: Optional[dict]) -> tuple:
    """(ids, origen): la batería de la actividad en la ruta del candidato; si su copia no la trae (entró antes de
    configurarla), la de la vacante o la ruta VIGENTE — solo como sugerencia, la copia del candidato no cambia."""
    origen_copia = "vacante" if (p.proceso or {}).get("origen") == "vacante" else "ruta"
    ids = _pruebas_de_ruta(p.proceso or {}, (paso or {}).get("id", ""))
    if ids:
        return ids, origen_copia
    try:
        vigente = sproc.ruta_para(db, p.cuenta_id, p.vacante)
    except Exception:  # noqa: BLE001
        vigente = {}
    ids = _pruebas_de_ruta(vigente, (paso or {}).get("id", ""))
    return ids, ("vacante" if vigente.get("origen") == "vacante" else "ruta") if ids else ""


def _psicometrias_vivas(db: Session, p: Postulacion) -> List[Evaluacion]:
    return (db.query(Evaluacion)
            .filter(Evaluacion.postulacion_id == p.id, Evaluacion.tipo == "psicometrica", Evaluacion.forma == "integrada",
                    Evaluacion.estado.in_(("pendiente", "realizada_sin_resultado")))
            .order_by(Evaluacion.id).all())


def _ya_asignada(vivas: List[Evaluacion], tests: str) -> Optional[Evaluacion]:
    """Asignación viva de las MISMAS pruebas que ya salió (clave del proveedor o envío simulado)."""
    from ..services import psicometricas as psi

    return next((e for e in vivas if _tests(e.id_proveedor) == tests
                 and (e.clave_proveedor or (e.paso_integrada or "asignada") != "asignada")), None)


@router.get("/postulaciones/{codigo}/psicometria")
def vista_psicometria(codigo: str, paso_id: str = "", db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Vista limpia de la actividad de psicometría: batería configurada, catálogo para «Cambiar selección» y, si ya
    hay una asignación viva con esas pruebas, cuál es (para no duplicarla)."""
    from ..services import psicometricas as psi

    p = _postulacion(db, codigo, cuenta.id)
    try:
        paso = sproc.paso_para_evaluacion(p, "psicometrica", paso_id.strip())
    except sproc.ErrorProceso as e:
        raise HTTPException(e.status, e.mensaje)
    ids, origen = bateria_predeterminada(db, p, paso)
    catalogo = _catalogo_integrado(db, cuenta.id)
    por_id = {x.id: x for x in catalogo}
    seleccion = [por_id[i] for i in ids if i in por_id]
    tests = _tests(",".join(x.id_proveedor or "" for x in seleccion)) if seleccion else ""
    vigente = _ya_asignada(_psicometrias_vivas(db, p), tests) if tests else None
    return {
        "paso": {"id": paso["id"], "nombre": paso["nombre"]} if paso else None,
        "seleccion": [x.id for x in seleccion],
        "origen": origen,
        "noDisponibles": len([i for i in ids if i not in por_id]),
        "catalogo": [prueba_psicometrica_dict(x) for x in catalogo],
        "conectado": psi.configurado(),
        "faltaCorreo": not (p.correo or "").strip(),
        "consentimiento": bool(p.consentimiento),
        "asignada": evaluacion_dict(vigente, u) if vigente else None,
    }


class AsignarPsicometriaIn(BaseModel):
    prueba_ids: List[int] = []  # vacía = la batería configurada en la ruta/vacante
    paso_id: str = ""
    # 2026-10-07 (modal de psicometría): correo capturado/corregido en el MISMO modal. Se guarda en la persona ANTES de
    # llamar al proveedor (y se conserva aunque el envío falle). None = no tocar el correo de la ficha.
    correo: Optional[str] = None


@router.post("/postulaciones/{codigo}/psicometria", status_code=201)
async def asignar_psicometria(codigo: str, datos: AsignarPsicometriaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Asignar y enviar»: valida → (candado) → crea la asignación en el proveedor (agregaCandidato) y guarda la clave →
    marca Enviada → avisa al candidato por el evento «Psicometría enviada». La evaluación queda ligada a la actividad de
    la ruta (`paso_id`), así «Ruta» y «Evaluaciones» leen el MISMO registro. Una repetición de las mismas pruebas se
    liga a la misma actividad (la anterior conserva su historial); otras pruebas fuera de la ruta = actividad ad hoc."""
    from ..services import psicometricas as psi

    p = _postulacion(db, codigo, cuenta.id)
    if not p.activa:
        raise HTTPException(409, "La postulación está cerrada.")
    if not p.consentimiento:
        raise HTTPException(409, "Falta el consentimiento de privacidad del candidato (LFPDPPP).")
    if datos.correo is not None:
        from .candidatos import actualizar_contacto  # import tardío (evita el ciclo entre routers)

        if not datos.correo.strip():
            raise HTTPException(400, "Escribe el correo del candidato: Psicométricas.mx le manda ahí su liga.")
        if actualizar_contacto(db, p, u.nombre, correo=datos.correo, correo_rh=u.correo, origen="psicometria"):
            db.commit()  # el correo queda guardado aunque después falle el proveedor (su rollback no lo deshace)
    async with _candado_psicometria(p.id):
        try:
            paso = sproc.paso_para_evaluacion(p, "psicometrica", datos.paso_id.strip())
        except sproc.ErrorProceso as e:
            raise HTTPException(e.status, e.mensaje)
        ids = list(dict.fromkeys(int(i) for i in datos.prueba_ids)) or bateria_predeterminada(db, p, paso)[0]
        if not ids:
            raise HTTPException(400, "Elige la batería o las pruebas del catálogo («Cambiar selección»).")
        por_id = {x.id: x for x in _catalogo_integrado(db, cuenta.id)}
        if any(i not in por_id for i in ids):
            raise HTTPException(400, "Alguna prueba elegida ya no está activa en el catálogo; vuelve a elegir con «Cambiar selección».")
        pruebas = [por_id[i] for i in ids]
        sin_id = [x.nombre for x in pruebas if not (x.id_proveedor or "").strip()]
        if sin_id:
            raise HTTPException(400, f"Falta el identificador en el proveedor de: {', '.join(sin_id)} (Configuración → Pruebas psicométricas).")
        proveedores = {(x.proveedor or "").strip() or "Psicométricas.mx" for x in pruebas}
        if len(proveedores) > 1:
            raise HTTPException(400, "Las pruebas elegidas son de proveedores distintos: asígnalas por separado.")
        proveedor = proveedores.pop()
        tests = _tests(",".join(x.id_proveedor for x in pruebas))
        vivas = _psicometrias_vivas(db, p)
        ya = _ya_asignada(vivas, tests)
        if ya:
            raise HTTPException(409, f"Estas pruebas ya se asignaron y enviaron ({ya.codigo}). Para volver a avisar al candidato usa «Reenviar» o «Recordatorio».")
        if paso is None and not datos.paso_id.strip() and sproc.tiene_proceso(p):
            # repetición de las mismas pruebas: se liga a la actividad de la ruta que ya las tuvo
            previa = (db.query(Evaluacion)
                      .filter(Evaluacion.postulacion_id == p.id, Evaluacion.tipo == "psicometrica", Evaluacion.paso_id != "")
                      .order_by(Evaluacion.id.desc()).all())
            previa = next((e for e in previa if _tests(e.id_proveedor) == tests), None)
            if previa and any(x["id"] == previa.paso_id and not x.get("heredado") for x in p.proceso.get("pasos", [])):
                paso = sproc._paso(p, previa.paso_id)
        paso_id = paso["id"] if paso else ""
        campos = {"tipo": "psicometrica", "nombre": " + ".join(x.nombre for x in pruebas)[:200], "forma": "integrada",
                  "prueba_id": pruebas[0].id, "pruebas": ids, "proveedor": proveedor, "id_proveedor": tests,
                  "paso_integrada": "asignada", "paso_id": paso_id}
        # una asignación previa que nunca salió (intento fallido del flujo anterior) se reutiliza: no se duplica
        ev = next((e for e in vivas if not e.clave_proveedor and (e.paso_integrada or "asignada") == "asignada" and e.paso_id == paso_id), None)
        nueva = ev is None
        if nueva:
            ev = sev.nueva(p, cuenta.id, u.nombre, u.id, **campos)
            sev.asegurar_ligas(ev)
            db.add(ev)
            db.flush()
            sev.asignar_codigo(ev)
            sev.evento(db, ev, "creada", u.nombre, a=ev.estado, usuario_id=u.id, tipo=ev.tipo, forma=ev.forma, proveedor=ev.proveedor, pruebas=ids)
        else:
            anteriores = {k: getattr(ev, k) for k in ("nombre", "id_proveedor")}
            for k, v in campos.items():
                setattr(ev, k, v)
            sev.evento(db, ev, "modificada", u.nombre, usuario_id=u.id, anteriores=anteriores, pruebas=ids)
        real = psi.es_psicometricas(proveedor) and psi.configurado()
        if real:
            try:
                psi.tests_de(tests)
            except psi.PsicometricasError as ex:
                db.rollback()
                raise HTTPException(400, str(ex))
            try:
                _activar_psicometria(db, ev, p)
            except HTTPException:
                db.rollback()  # sin clave del proveedor no queda NADA guardado: ni la evaluación ni «Enviada»
                raise
            sev.aplicar_paso(db, ev, "enviada", u.nombre, "Psicométricas.mx")
        else:
            sev.aplicar_paso(db, ev, "enviada", u.nombre)  # proveedor sin llaves: modo integrado simulado
        if nueva and not paso_id:
            try:
                sproc.paso_adhoc_para_evaluacion(db, p, ev, u)
            except Exception as ex:  # noqa: BLE001
                print(f"[proceso] no se pudo registrar la psicometría {ev.codigo} como actividad ad hoc: {ex}")
        registrar(db, u.nombre, "psicometria_asignada", "postulacion", p.codigo,
                  {"evaluacion": ev.codigo, "pruebas": ids, "proveedor": proveedor, "tests": tests, "clave_proveedor": ev.clave_proveedor,
                   "simulado": not real, "paso_proceso": paso_id, "correo_rh": u.correo})
        _tocar(p)
        db.commit()
    if not real:
        return _respuesta(db, ev, u, p, simulado=True,
                          aviso="Psicométricas.mx sin llaves en este servidor: asignación simulada (no se llamó a su API ni se avisó al candidato).")
    resultados = await sev.notificar_psicometria(db, ev, p, u.nombre, usuario_id=u.id)
    registrar(db, "sistema", "psicometria_notificada", "evaluaciones", ev.codigo, {"clave_proveedor": ev.clave_proveedor, "envios": resultados})
    db.commit()
    envio = {"enviado": any(r["enviado"] for r in resultados), "conLiga": True,
             "canales": [r["canal"] for r in resultados if r["enviado"]],
             "detalle": "; ".join(f"{r['canal'] or 'sin canal'}: {r['detalle']}" for r in resultados if not r["enviado"])}
    return _respuesta(db, ev, u, p, resultados, envioCandidato=envio, simulado=False)


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
async def sincronizar(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Sincronizar resultado» en Psicométricas.mx (por si el webhook no llegó: en desarrollo apunta a la Demo).
    consultaCandidato → si terminó, descarga JSON + PDF → «Con resultado» (el paso de la ruta queda Completado) y corre
    el avance automático de la etapa. Solo guarda si su API confirma que terminó."""
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
    if r == "resultado_recibido":
        try:
            await sproc.avanzar_seguro(db, sev.postulacion_de(db, ev))
        except sev.ErrorEvaluacion:
            pass  # evaluación sin postulación viva: el resultado ya quedó guardado
    return _respuesta(db, ev, u, sincronizacion=r)


def _adjunto(ev: Evaluacion, aid: str) -> dict:
    a = next((x for x in (ev.adjuntos or []) if x.get("id") == aid), None)
    if not a or not fs.existe(a.get("archivo")):
        raise HTTPException(404, "Adjunto no disponible.")
    return a


@router.get("/{codigo}/adjuntos/{aid}")
def descargar_adjunto(codigo: str, aid: str, descargar: bool = False, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
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
    # «Abrir» = inline en el navegador; «Descargar» = ?descargar=true
    return FileResponse(a["archivo"], media_type=a.get("mime") or "application/octet-stream", filename=a.get("nombre") or "adjunto",
                        content_disposition_type="attachment" if descargar else "inline")


@router.post("/{codigo}/consentimiento/enviar")
async def enviar_liga_consentimiento(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Manda al candidato la liga del consentimiento expreso de la evaluación médica (WhatsApp y correo con lo que
    tenga). Un canal caído nunca rompe la acción: el resultado de cada envío regresa a RH."""
    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    if ev.consentimiento != "pendiente":
        raise HTTPException(409, "Esta evaluación no está en espera de consentimiento.")
    resultados = await _enviar_liga_candidato(db, ev, p, u, "consentimiento", cuenta)
    db.commit()
    return {"liga": sev.liga_consentimiento(ev), "resultados": resultados, "advertencias": notificaciones.advertencias_de(resultados),
            "evaluacion": evaluacion_dict(ev, u)}


async def _enviar_liga_candidato(db: Session, ev: Evaluacion, p: Postulacion, u: Usuario, clave: str, cuenta: Cuenta) -> list:
    """Consentimiento, liga de otro sistema o liga del proveedor → candidato (WhatsApp y correo con lo que tenga)."""
    empresa = nombre_empresa_candidato(p.vacante) if p.vacante else cuenta.nombre_visible
    if clave == "consentimiento":
        sev.asegurar_ligas(ev)
        liga = sev.liga_consentimiento(ev)
        asunto = "Consentimiento para tu evaluación médica"
        texto = (f"Hola {p.nombre}. Para continuar con tu proceso en {empresa} necesitamos tu consentimiento por escrito para la "
                 "evaluación médica. Léelo y, si estás de acuerdo, acéptalo aquí:")
        accion_bitacora = "consentimiento_medico_solicitado"
    elif clave == "proveedor" and sev.usa_psicometricas(ev) and ev.clave_proveedor:
        # Psicométricas.mx: mismo aviso que al enviar (portal + clave + pasos, por canal activo y correo).
        resultados = await sev.notificar_psicometria(db, ev, p, u.nombre, usuario_id=u.id)
        registrar(db, u.nombre, "evaluacion_liga_candidato_enviada", "postulacion", p.codigo, {"evaluacion": ev.codigo, "liga": clave, "envios": resultados, "correo_rh": u.correo})
        return resultados
    else:
        liga = ev.liga_externa_candidato if clave == "otro_sistema" else sev._url_proveedor(ev)
        if not liga:
            raise HTTPException(409, "Esta evaluación no tiene liga para el candidato.")
        asunto = f"Tu evaluación: {ev.nombre_visible}"
        texto = f"Hola {p.nombre}. Como parte de tu proceso en {empresa}, realiza tu evaluación «{ev.nombre_visible}» aquí:"
        accion_bitacora = "evaluacion_liga_candidato_enviada"
    resultados = await sev.enviar_liga_a_candidato(db, ev, p, u.nombre, clave, asunto, texto, liga, usuario_id=u.id)
    registrar(db, u.nombre, accion_bitacora, "postulacion", p.codigo, {"evaluacion": ev.codigo, "liga": clave, "envios": resultados, "correo_rh": u.correo})
    return resultados


@router.post("/{codigo}/ligas/{clave}/enviar")
async def enviar_liga(codigo: str, clave: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Enviar o reenviar» de CUALQUIER liga externa (consentimiento | evaluador | otro_sistema | proveedor). La liga ya
    existe (se genera al crear y se reutiliza); un envío fallido no la invalida ni bloquea Abrir/Copiar: el resultado de
    cada canal regresa a RH y queda en el historial de la evaluación."""
    if clave not in sev.CLAVES_LIGA:
        raise HTTPException(400, f"Liga inválida. Usa una de: {', '.join(sev.CLAVES_LIGA)}.")
    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    if ev.estado == "cancelada":
        raise HTTPException(409, "Esta evaluación fue cancelada.")
    vigentes = {x["clave"]: x for x in sev.ligas_de(db, ev)}
    if clave not in vigentes:
        raise HTTPException(409, "Esa liga no aplica a esta evaluación en este momento.")
    if not vigentes[clave]["puedeEnviar"]:
        raise HTTPException(409, vigentes[clave]["motivoNoEnvio"] or "No hay a quién enviarla.")
    if clave == "evaluador":
        resultados = await sev.notificar(db, ev, p, "evaluacion_asignada", u.nombre, audiencias={"entrevistador"})
        registrar(db, u.nombre, "evaluacion_liga_reenviada", "postulacion", p.codigo, {"evaluacion": ev.codigo, "correo_rh": u.correo, "notificaciones": resultados})
    else:
        resultados = await _enviar_liga_candidato(db, ev, p, u, clave, cuenta)
    db.commit()
    return _respuesta(db, ev, u, None, resultados)


@router.post("/{codigo}/confirmar-inicio")
def confirmar_inicio(codigo: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """Psicométrica → «En curso» / socioeconómica → «En proceso». Solo con confirmación (esta acción de RH o el
    proveedor al iniciar). No cambia la etapa."""
    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
        sev.confirmar_inicio(db, ev, u.nombre, usuario_id=u.id)
    registrar(db, u.nombre, "evaluacion_inicio_confirmado", "postulacion", p.codigo, {"evaluacion": ev.codigo, "correo_rh": u.correo})
    db.commit()
    return _respuesta(db, ev, u)


class RevisarIn(BaseModel):
    conclusion: str
    comentario: str = ""


@router.post("/{codigo}/revisar")
async def revisar(codigo: str, datos: RevisarIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    """«Marcar como revisada»: conclusión de RH + comentario, con usuario y fecha. Recibir un resultado nunca la marca
    revisada; revisarla NUNCA mueve la etapa ni avisa al candidato (HITL)."""
    ev = _ev(db, codigo, cuenta.id)
    with _negocio():
        p = sev.postulacion_de(db, ev)
    if ev.tipo == "medica" and not u.puede_ver_informe_medico():
        raise HTTPException(403, "Revisar el resultado médico requiere el permiso de informes médicos.")
    with _negocio():
        sev.revisar(db, ev, actor=u.nombre, usuario_id=u.id, conclusion=datos.conclusion, comentario=datos.comentario)
    _recalcular_indicador(p)
    registrar(db, u.nombre, "evaluacion_revisada", "postulacion", p.codigo,
              {"evaluacion": ev.codigo, "conclusion_rh": ev.conclusion_rh, "etapa": p.etapa, "correo_rh": u.correo})
    _tocar(p)
    db.commit()
    # Revisar nunca mueve por sí mismo; solo el interruptor de avance automático del proceso (si RH lo encendió).
    await sproc.avanzar_seguro(db, p)
    return _respuesta(db, ev, u, p)


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
    actor = ev.evaluador_nombre or ("Médico vía liga" if ev.tipo == "medica" else "Evaluador vía liga")
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
    await sproc.avanzar_seguro(db, p)
    return {"ok": True, "accion": accion, "evaluacion": evaluacion_dict(ev, publico=True)}


@router.get("/publica/{token}/adjuntos/{aid}")
def adjunto_publico(token: str, aid: str, descargar: bool = False, db: Session = Depends(get_db)):
    ev = _por_token(db, token)
    a = _adjunto(ev, aid)
    return FileResponse(a["archivo"], media_type=a.get("mime") or "application/octet-stream", filename=a.get("nombre") or "adjunto",
                        content_disposition_type="attachment" if descargar else "inline")


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
