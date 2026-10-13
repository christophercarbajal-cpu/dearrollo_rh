"""Cambio 30 (2026-10-13) — «Oferta laboral». Ver services/oferta.py.

RH (sesión): `GET /candidatos/{codigo}/oferta` (carta precargada con los datos de la vacante), `POST …/oferta` («Enviar
oferta» o «Generar carta oferta»; WhatsApp «[nombre], tenemos una oferta para ti. Revísala aquí: [liga]») y
`POST …/oferta/respuesta` (RH registra la respuesta obtenida por otro medio).
Candidato (público, por token): `GET /ofertas/publica/{token}` y `POST /ofertas/publica/{token}/responder {acepta}`.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import Cuenta, Usuario
from ..services import oferta as soferta
from ..services import proceso as sproc

router = APIRouter(tags=["ofertas"])


class OfertaIn(BaseModel):
    datos: Optional[dict] = None


class RespuestaOfertaIn(BaseModel):
    acepta: bool


def _postulacion(db: Session, codigo: str, cuenta_id: int):
    from .candidatos import _por_codigo

    return _por_codigo(db, codigo, cuenta_id)


def _en_ruta(p) -> bool:
    return sproc.paso_de_tipo(p, "oferta") is not None


@router.get("/candidatos/{codigo}/oferta")
def ver_oferta(codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    p = _postulacion(db, codigo, cuenta.id)
    return {"datos": soferta.precarga(p), "oferta": soferta.publica(p), "enRuta": _en_ruta(p),
            "campos": list(soferta.CAMPOS)}


@router.post("/candidatos/{codigo}/oferta")
async def enviar_oferta(codigo: str, datos: OfertaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                        cuenta: Cuenta = Depends(cuenta_actual)):
    """«Enviar oferta» (actividad de la ruta) o «Generar carta oferta» (sin la actividad: solo registra la respuesta)."""
    p = _postulacion(db, codigo, cuenta.id)
    if not p.activa:
        raise HTTPException(409, "La postulación está cerrada.")
    if p.etapa not in ("Contratación", "Onboarding"):
        raise HTTPException(409, "La oferta laboral se envía en Contratación.")
    carta = soferta.limpiar(datos.datos, soferta.precarga(p))
    if not carta["puesto"] or not carta["sueldo"]:
        raise HTTPException(400, "La carta oferta necesita al menos el puesto y el sueldo.")
    r = await soferta.enviar(db, p, u.nombre, carta, en_ruta=_en_ruta(p))
    db.refresh(p)
    return {**r, "oferta": soferta.publica(p)}


@router.post("/candidatos/{codigo}/oferta/respuesta")
async def registrar_respuesta(codigo: str, datos: RespuestaOfertaIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
                              cuenta: Cuenta = Depends(cuenta_actual)):
    p = _postulacion(db, codigo, cuenta.id)
    if not soferta.estado(p).get("enviada_en"):
        raise HTTPException(409, "Primero envía la oferta laboral.")
    r = await soferta.responder(db, p, datos.acepta, canal="rh", por=u.nombre)
    db.refresh(p)
    return {**r, "oferta": soferta.publica(p)}


@router.get("/ofertas/publica/{token}")
def oferta_publica(token: str, db: Session = Depends(get_db)):
    p = soferta.postulacion_por_token(db, token)
    if p is None:
        raise HTTPException(404, "Esta liga de oferta no existe o ya no está disponible.")
    return soferta.vista_candidato(p)


@router.post("/ofertas/publica/{token}/responder")
async def responder_publica(token: str, datos: RespuestaOfertaIn, db: Session = Depends(get_db)):
    p = soferta.postulacion_por_token(db, token)
    if p is None:
        raise HTTPException(404, "Esta liga de oferta no existe o ya no está disponible.")
    if not p.activa:
        raise HTTPException(409, "Este proceso ya no está activo.")
    if soferta.estado(p).get("respuesta"):
        raise HTTPException(409, "Ya registramos tu respuesta a esta oferta.")
    await soferta.responder(db, p, datos.acepta, canal="liga")
    db.refresh(p)
    return soferta.vista_candidato(p)
