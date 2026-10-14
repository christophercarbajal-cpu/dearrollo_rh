"""Fase D — configuración de notificaciones por evento/destinatario/canal (puntos 22-26).

Editar es solo admin (son reglas de la Cuenta completa, no de un candidato); LEER las reglas lo
puede hacer cualquier usuario de la Cuenta — Punto 12: la línea "Notificar: … · Editar" de cada
acción se precarga con la configuración predeterminada. El envío en sí vive en
`services/notificaciones.py::disparar()`, llamado desde cada endpoint donde ocurre el evento.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_admin
from ..models import REGLAS_NOTIFICACION_DEFAULT, Cuenta, EVENTOS_NOTIFICACION, NotificacionEnviada, ReglaNotificacion, Usuario, registrar
from ..serial import iso

router = APIRouter(prefix="/notificaciones", tags=["notificaciones"])


def _regla_dict(r: ReglaNotificacion) -> dict:
    return {
        "evento": r.evento,
        "candidatoCorreo": r.candidato_correo,
        "candidatoWhatsapp": r.candidato_whatsapp,
        "entrevistadorCorreo": r.entrevistador_correo,
        "entrevistadorWhatsapp": r.entrevistador_whatsapp,
        "clienteCorreo": r.cliente_correo,
        "clienteWhatsapp": r.cliente_whatsapp,
    }


def _reglas_cuenta(db: Session, cuenta_id: int) -> list:
    """Siembra perezosa: si a la Cuenta le faltan filas (Cuenta nueva creada después de este
    lote), crea las que falten apagadas. El reemplazo de comportamiento histórico (Cuentas que
    ya existían antes de Fase D) es responsabilidad exclusiva de
    scripts/sembrar_reglas_notificacion.py — aquí nunca se inventa un "encendido"."""
    existentes = {r.evento: r for r in db.query(ReglaNotificacion).filter(ReglaNotificacion.cuenta_id == cuenta_id).all()}
    faltantes = [e for e in EVENTOS_NOTIFICACION if e not in existentes]
    for evento in faltantes:
        # Fase 7A: nace con los defaults del proyecto (antes todo apagado → el correo de la entrevista
        # agendada nunca salía en Cuentas donde no corrió el script de siembra).
        r = ReglaNotificacion(cuenta_id=cuenta_id, evento=evento, **REGLAS_NOTIFICACION_DEFAULT.get(evento, {}))
        db.add(r)
        existentes[evento] = r
    if faltantes:
        db.commit()
    return [existentes[e] for e in EVENTOS_NOTIFICACION]


@router.get("/reglas")
def listar_reglas(db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Configuración predeterminada de la Cuenta — lectura para cualquier usuario (Punto 12)."""
    return [_regla_dict(r) for r in _reglas_cuenta(db, cuenta.id)]


class ReglaNotificacionIn(BaseModel):
    candidato_correo: bool = False
    candidato_whatsapp: bool = False
    entrevistador_correo: bool = False
    entrevistador_whatsapp: bool = False
    cliente_correo: bool = False
    cliente_whatsapp: bool = False


@router.patch("/reglas/{evento}")
def actualizar_regla(
    evento: str, datos: ReglaNotificacionIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    if evento not in EVENTOS_NOTIFICACION:
        raise HTTPException(404, f"Evento desconocido. Usa uno de: {', '.join(EVENTOS_NOTIFICACION)}")
    r = db.query(ReglaNotificacion).filter(
        ReglaNotificacion.cuenta_id == cuenta.id, ReglaNotificacion.evento == evento
    ).first()
    if not r:
        r = ReglaNotificacion(cuenta_id=cuenta.id, evento=evento)
        db.add(r)

    r.candidato_correo = datos.candidato_correo
    r.candidato_whatsapp = datos.candidato_whatsapp
    r.entrevistador_correo = datos.entrevistador_correo
    r.entrevistador_whatsapp = datos.entrevistador_whatsapp
    r.cliente_correo = datos.cliente_correo
    r.cliente_whatsapp = datos.cliente_whatsapp

    registrar(
        db, u.nombre, "regla_notificacion_actualizada", "cuenta", str(cuenta.id),
        {"evento": evento, **datos.model_dump(), "correo_rh": u.correo},
    )
    db.commit()
    return _regla_dict(r)


class ReglaConEventoIn(ReglaNotificacionIn):
    evento: str


@router.put("/reglas")
def guardar_reglas(
    reglas: List[ReglaConEventoIn], db: Session = Depends(get_db), u: Usuario = Depends(usuario_admin),
    cuenta: Cuenta = Depends(cuenta_actual),
):
    """Botón «Guardar configuración de notificaciones» (Punto 12): recibe la matriz completa y
    la aplica en una sola transacción; solo se registran en bitácora los eventos que cambiaron."""
    desconocidos = [r.evento for r in reglas if r.evento not in EVENTOS_NOTIFICACION]
    if desconocidos:
        raise HTTPException(400, f"Eventos desconocidos: {', '.join(desconocidos)}")
    actuales = {r.evento: r for r in _reglas_cuenta(db, cuenta.id)}
    cambiados = []
    for entrada in reglas:
        r = actuales[entrada.evento]
        nuevos = entrada.model_dump(exclude={"evento"})
        if any(getattr(r, k) != v for k, v in nuevos.items()):
            for k, v in nuevos.items():
                setattr(r, k, v)
            cambiados.append(entrada.evento)
    if cambiados:
        registrar(
            db, u.nombre, "reglas_notificacion_guardadas", "cuenta", str(cuenta.id),
            {"eventos": cambiados, "correo_rh": u.correo},
        )
        db.commit()
    return [_regla_dict(r) for r in _reglas_cuenta(db, cuenta.id)]


@router.get("/historial")
def historial(
    limite: int = 50, db: Session = Depends(get_db), _: Usuario = Depends(usuario_admin), cuenta: Cuenta = Depends(cuenta_actual)
):
    """Transparencia para RH — '¿esto ya se le avisó a alguien?' — no es la bitácora LFPDPPP
    (esa es `Bitacora`, hash-encadenada e inmutable); esta es solo la lista operativa de envíos."""
    filas = (
        db.query(NotificacionEnviada)
        .filter(NotificacionEnviada.cuenta_id == cuenta.id)
        .order_by(NotificacionEnviada.creada_en.desc())
        .limit(min(limite, 200))
        .all()
    )
    return [
        {
            "id": f.id,
            "candidatoId": f.candidato_id,
            "evento": f.evento,
            "destinatarioTipo": f.destinatario_tipo,
            "destino": f.destino,
            "canal": f.canal,
            "enviado": f.enviado,
            "detalle": f.detalle,
            "creadaEn": iso(f.creada_en),
        }
        for f in filas
    ]


@router.get("/rh")
def campana_rh(db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    """Cambio 42 — campana: candidatos detenidos por una acción humana en las vacantes de las que eres responsable
    (el Administrador también ve las vacantes sin responsable). 1 candidato → abre su ficha en la actividad; varios de la
    misma vacante y acción → abre el tablero de la vacante filtrado por esa acción."""
    from ..services import campana_rh

    return campana_rh.pendientes(db, cuenta.id, u)
