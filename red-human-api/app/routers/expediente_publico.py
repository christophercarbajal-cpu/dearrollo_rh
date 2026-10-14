"""Módulo 2 · Contratación e integración — liga pública para que el candidato suba sus
documentos (Lote 4).

Contraparte liviana de `entrevistas.py`/`capacitacion.py`, mismo espíritu que
`entrevista_humana.py`: sin sesión, el token es la credencial. Diferencia importante frente al
de `EntrevistaHumana` (un solo uso): este token NO se invalida tras la primera subida — el
candidato puede volver varias veces hasta completar todos los documentos obligatorios. Solo se
cierra cuando el expediente llega a `estado == "alta"` (ver `subir_documento_interno` en
`contratacion.py`, que ya rechaza cambios en ese estado — mismo guard para RH y para el
candidato, sin excepción de Modo Prueba).
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Expediente
from ..services.pdf import pdf_carta_intencion
from .contratacion import _datos_carta_intencion, subir_documento_interno

router = APIRouter(prefix="/expedientes", tags=["expedientes-publico"])


def _por_token(db: Session, token: str) -> Expediente:
    e = db.query(Expediente).filter(Expediente.token == token).first()
    if not e:
        raise HTTPException(404, "Esta liga no es válida.")
    return e


@router.get("/publica/{token}")
def publica(token: str, db: Session = Depends(get_db)):
    from ..services import documentos_vacante as sdv

    e = _por_token(db, token)
    # Cambio 25: un renglón por documento PEDIDO, con cuántos archivos exige (fotos del auto = 4) y, si RH pidió corrección,
    # el motivo. Cambio 42: SOLO los faltantes, la empresa de la vacante en la cabecera, casillas Frente/Reverso para los
    # documentos de dos caras y, al completar los obligatorios, el mensaje final (la actividad queda «Por revisar»).
    vista = sdv.vista_publica(db, e)
    for d, doc in zip(vista["documentos"], sdv.faltantes(e)):
        d["estadoRevision"] = _estado_publico(doc)
    # 2026-09-19: la carta de intención se descarga desde la misma liga (la comparte RH por WhatsApp)
    return {**vista, "cartaDisponible": bool(e.puesto and e.sueldo)}


def _estado_publico(d) -> str:
    from ..serial import estado_revision_documento

    return estado_revision_documento(d)


@router.get("/publica/{token}/carta-intencion")
def carta_publica(token: str, db: Session = Depends(get_db)):
    """PDF de la carta de intención para el candidato (la liga es la credencial)."""
    e = _por_token(db, token)
    if not (e.puesto and e.sueldo):
        raise HTTPException(404, "Tu carta todavía no está lista.")
    try:
        pdf = pdf_carta_intencion(_datos_carta_intencion(e))
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(503, f"No se pudo generar la carta: {ex}")
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="carta-intencion.pdf"'})


@router.post("/publica/{token}/documentos")
async def subir(
    token: str, tipo: str = Form(...), archivo: UploadFile = File(...), cara: str = Form(default=""), db: Session = Depends(get_db)
):
    """Archivo por archivo (se guarda al subirlo). Cambio 42: `cara` = «Frente» | «Reverso» en documentos de dos caras.
    Al completar los obligatorios, la ruta sigue sola (la actividad queda «Por revisar» para RH)."""
    from ..services import documentos_vacante as sdv
    from ..services import proceso as sproc

    e = _por_token(db, token)
    r = await subir_documento_interno(db, e, tipo, archivo, subido_por="candidato", cara=cara)
    if e.postulacion is not None:
        await sproc.avanzar_seguro(db, e.postulacion)
        db.commit()
    return {**r, **sdv.vista_publica(db, e)}
