"""Ubicaciones de la Cuenta (Cambio 47, 2026-10-14): sedes con dirección, mapa, indicaciones y horarios de citas con cupo.

    GET    /ubicaciones                       lista (activas; ?todas=true incluye desactivadas)
    POST   /ubicaciones                       alta
    PATCH  /ubicaciones/{id}                  edición
    DELETE /ubicaciones/{id}                  desactivar (baja lógica: las vacantes y citas existentes no cambian)
    GET    /ubicaciones/{id}/disponibilidad   próximos días hábiles con cupo y sus horarios libres
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import cuenta_actual, usuario_actual, usuario_decisor
from ..models import Cuenta, Ubicacion, Usuario, registrar
from ..services import ubicaciones as subic
from ..services.modulos_rh import requiere_modulos_rh

router = APIRouter(prefix="/ubicaciones", tags=["ubicaciones"], dependencies=[Depends(requiere_modulos_rh)])


class UbicacionIn(BaseModel):
    nombre: Optional[str] = None
    direccion: Optional[str] = None
    mapa: Optional[str] = None
    indicaciones: Optional[str] = None
    estado: Optional[str] = None
    municipio: Optional[str] = None
    horarios: Optional[List[dict]] = None
    activa: Optional[bool] = None


def _de_cuenta(db: Session, uid: int, cuenta_id: int) -> Ubicacion:
    u = db.get(Ubicacion, uid)
    if u is None or u.cuenta_id != cuenta_id:
        raise HTTPException(404, "Ubicación no encontrada")
    return u


def _aplicar(u: Ubicacion, datos: UbicacionIn) -> None:
    if datos.nombre is not None:
        if not datos.nombre.strip():
            raise HTTPException(400, "El nombre de la ubicación es obligatorio.")
        u.nombre = datos.nombre.strip()[:160]
    for campo, attr, n in (("direccion", "direccion", 1000), ("mapa", "mapa", 600), ("indicaciones", "indicaciones", 2000),
                           ("estado", "ubicacion_estado", 80), ("municipio", "ubicacion_municipio", 120)):
        valor = getattr(datos, campo)
        if valor is not None:
            setattr(u, attr, valor.strip()[:n])
    if u.mapa and not u.mapa.startswith(("https://", "http://")):
        raise HTTPException(400, "La liga de Google Maps debe empezar con https://")
    if datos.horarios is not None:
        try:
            u.horarios_citas = subic.normalizar_horarios(datos.horarios)
        except subic.ErrorUbicacion as e:
            raise HTTPException(e.status, e.mensaje)
    if datos.activa is not None:
        u.activa = datos.activa


@router.get("")
def listar(todas: bool = False, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    q = db.query(Ubicacion).filter(Ubicacion.cuenta_id == cuenta.id)
    if not todas:
        q = q.filter(Ubicacion.activa.is_(True))
    return [subic.ubicacion_dict(x) for x in q.order_by(Ubicacion.nombre).all()]


@router.post("", status_code=201)
def crear(datos: UbicacionIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    if not (datos.nombre or "").strip():
        raise HTTPException(400, "El nombre de la ubicación es obligatorio.")
    if db.query(Ubicacion).filter(Ubicacion.cuenta_id == cuenta.id, Ubicacion.nombre == datos.nombre.strip(), Ubicacion.activa.is_(True)).first():
        raise HTTPException(409, f"Ya existe la ubicación «{datos.nombre.strip()}».")
    x = Ubicacion(cuenta_id=cuenta.id, nombre="", activa=True, horarios_citas=[])
    _aplicar(x, datos)
    db.add(x)
    db.flush()
    registrar(db, u.nombre, "ubicacion_creada", "ubicacion", str(x.id), {"nombre": x.nombre, "horarios": x.horarios_citas})
    db.commit()
    return subic.ubicacion_dict(x)


@router.patch("/{uid}")
def editar(uid: int, datos: UbicacionIn, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor),
           cuenta: Cuenta = Depends(cuenta_actual)):
    x = _de_cuenta(db, uid, cuenta.id)
    _aplicar(x, datos)
    registrar(db, u.nombre, "ubicacion_editada", "ubicacion", str(x.id), {"nombre": x.nombre, "horarios": x.horarios_citas})
    db.commit()
    return subic.ubicacion_dict(x)


@router.delete("/{uid}")
def desactivar(uid: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_decisor), cuenta: Cuenta = Depends(cuenta_actual)):
    x = _de_cuenta(db, uid, cuenta.id)
    x.activa = False
    registrar(db, u.nombre, "ubicacion_desactivada", "ubicacion", str(x.id), {"nombre": x.nombre})
    db.commit()
    return {"ok": True}


@router.get("/{uid}/disponibilidad")
def disponibilidad(uid: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual), cuenta: Cuenta = Depends(cuenta_actual)):
    x = _de_cuenta(db, uid, cuenta.id)
    dias = subic.dias_disponibles(db, x)
    return {"ubicacion": subic.ubicacion_dict(x),
            "dias": [{"fecha": d.isoformat(), "texto": subic.texto_dia(d),
                      "horarios": [{"hora": h["hora"], "libres": h["libres"]} for h in subic.horarios_libres(db, x, d)]} for d in dias]}
