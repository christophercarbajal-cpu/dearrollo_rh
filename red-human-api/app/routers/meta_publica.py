"""Cambio 31 (2026-10-13): metadatos Open Graph de las ligas PÚBLICAS del candidato (entrevista, documentos, oferta).

`GET /publico/meta/{tipo}/{token}` → {titulo: "[Tipo de liga] · [puesto]", descripcion: "[empresa]"}. Lo leen los
`layout.tsx` de `/entrevista/[token]`, `/expediente/[token]` y `/oferta/[token]` (generateMetadata, en el servidor),
así la vista previa de la liga en WhatsApp muestra el puesto y la empresa — nunca textos comerciales de la plataforma.
La empresa sale de la regla de siempre (`serial.nombre_empresa_candidato`: Cliente visible o la Cuenta). Sin sesión;
no expone nada más que puesto y empresa.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Entrevista, Expediente, Postulacion

router = APIRouter(prefix="/publico/meta", tags=["publico"])

TIPOS_LIGA = {"entrevista": "Entrevista", "documentos": "Documentos", "oferta": "Oferta laboral"}


def _postulacion(db: Session, tipo: str, token: str):
    if tipo == "entrevista":
        e = db.query(Entrevista).filter(Entrevista.token == token).first()
        return e.postulacion if e is not None else None
    if tipo == "documentos":
        x = db.query(Expediente).filter(Expediente.token == token).first()
        return db.get(Postulacion, x.postulacion_id) if x is not None and x.postulacion_id else None
    if tipo == "oferta":
        from ..services.oferta import postulacion_por_token

        return postulacion_por_token(db, token)
    return None


def metadatos(db: Session, tipo: str, token: str) -> dict:
    from ..serial import nombre_empresa_candidato

    etiqueta = TIPOS_LIGA.get(tipo, "Tu proceso")
    try:
        p = _postulacion(db, tipo, token) if token else None
    except Exception:  # noqa: BLE001 — una vista previa nunca truena
        p = None
    v = p.vacante if p is not None else None
    puesto = (v.titulo if v else "") or ""
    empresa = (nombre_empresa_candidato(v) if v else "") or ""
    return {"titulo": f"{etiqueta} · {puesto}" if puesto else etiqueta, "descripcion": empresa}


@router.get("/{tipo}/{token}")
def meta(tipo: str, token: str, db: Session = Depends(get_db)):
    return metadatos(db, tipo, token)
