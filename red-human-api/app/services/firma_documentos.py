"""«Firmar documentos» (2026-10-09): carta de intención + contrato en UN solo acto de firma, con tres modos.

- `electronica`: Dropbox Sign (una sola solicitud incrustada con ambos documentos unidos en un PDF; RH firma en el
  tablero y el candidato en su liga, como antes).
- `papel`: RH descarga el PDF unido, se firma en físico y RH sube el PDF firmado («Adjuntar documentos firmados»).
- `demo`: cada firmante dibuja o escribe su firma EN la plataforma; al firmar ambos se genera el PDF final con las
  firmas estampadas en sus zonas y una marca de agua «DEMOSTRACIÓN · SIN VALIDEZ LEGAL». Nunca llama a Dropbox Sign.

El modo sale de la Cuenta (`Cuenta.modo_firma`); vacío = Demo en Cuentas demo, Electrónica si Dropbox Sign está
configurado, Papel si no. En cualquier modo, el PDF final se guarda como el documento interno «Contrato firmado»
(el MISMO que lee Onboarding) y la ruta sale sola a Onboarding (`proceso.avanzar_si_corresponde`)."""

import base64
import io
import secrets
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import MODOS_FIRMA, Expediente, FirmaDocumento, es_cuenta_demo, registrar

MARCA_AGUA_DEMO = "DEMOSTRACIÓN · SIN VALIDEZ LEGAL"
ROL_ZONA = {"rh": "empresa", "candidato": "candidato"}
MAX_FIRMA_BYTES = 400_000


def modo_de(cuenta) -> str:
    from . import dropbox_sign as dsign

    elegido = (getattr(cuenta, "modo_firma", "") or "").strip()
    if elegido in MODOS_FIRMA:
        if elegido == "electronica" and not dsign.configurado():
            return "papel"  # sin llaves de Dropbox Sign la electrónica no existe en este servidor
        return elegido
    if es_cuenta_demo(cuenta):
        return "demo"
    return "electronica" if dsign.configurado() else "papel"


def pdf_documentos(e: Expediente) -> Tuple[bytes, List[dict]]:
    """Carta de intención + contrato unidos en UN PDF; las zonas de firma de ambos con su página real."""
    from pypdf import PdfReader, PdfWriter

    from ..routers.contratacion import _datos_carta_intencion
    from .pdf import pdf_carta_intencion, pdf_contrato

    d = _datos_carta_intencion(e)
    carta, zonas_c = pdf_carta_intencion(d, con_zonas=True)
    contrato, zonas_k = pdf_contrato({**d, "borrador": False}, con_zonas=True)
    w = PdfWriter()
    n_carta = 0
    for fuente in (carta, contrato):
        lector = PdfReader(io.BytesIO(fuente))
        for pagina in lector.pages:
            w.add_page(pagina)
        if fuente is carta:
            n_carta = len(lector.pages)
    salida = io.BytesIO()
    w.write(salida)
    zonas = [dict(z) for z in zonas_c] + [{**z, "pagina": z["pagina"] + n_carta} for z in zonas_k]
    return salida.getvalue(), zonas


def _imagen_firma(dato: str) -> bytes:
    """data:image/png;base64,… → bytes PNG validados (tamaño y cabecera)."""
    crudo = (dato or "").split(",", 1)[1] if (dato or "").startswith("data:") else (dato or "")
    try:
        png = base64.b64decode(crudo, validate=True)
    except Exception:  # noqa: BLE001
        raise ValueError("La firma dibujada no es una imagen válida.")
    if not png.startswith(b"\x89PNG") or len(png) > MAX_FIRMA_BYTES:
        raise ValueError("La firma dibujada debe ser una imagen PNG de menos de 400 KB.")
    return png


def estampar_demo(pdf: bytes, zonas: List[dict], firmas: dict) -> bytes:
    """Estampa cada firma (imagen o texto) y su fecha en sus zonas y agrega la marca de agua de demostración a TODAS
    las páginas. `firmas` = {rol_zona: {"imagen": bytes|None, "texto": str, "fecha": str}}."""
    from fpdf import FPDF
    from pypdf import PdfReader, PdfWriter

    lector = PdfReader(io.BytesIO(pdf))
    w = PdfWriter()
    for i, pagina in enumerate(lector.pages, start=1):
        ancho_pt, alto_pt = float(pagina.mediabox.width), float(pagina.mediabox.height)
        capa = FPDF(unit="pt", format=(ancho_pt, alto_pt))
        capa.set_auto_page_break(False)
        capa.add_page()
        # marca de agua diagonal, gris claro
        capa.set_text_color(200, 200, 200)
        capa.set_font("Helvetica", "B", 34)
        texto_marca = MARCA_AGUA_DEMO.replace("·", "-").encode("latin-1", "replace").decode("latin-1")
        with capa.rotation(35, ancho_pt / 2, alto_pt / 2):
            capa.text(ancho_pt / 2 - capa.get_string_width(texto_marca) / 2, alto_pt / 2, texto_marca)
        capa.set_text_color(20, 20, 60)
        for z in zonas:
            if z.get("pagina") != i:
                continue
            f = firmas.get(z.get("rol")) or {}
            if z.get("tipo") == "firma":
                if f.get("imagen"):
                    capa.image(io.BytesIO(f["imagen"]), x=z["x"], y=z["y"], w=z["ancho"], h=z["alto"], keep_aspect_ratio=True)
                elif f.get("texto"):
                    capa.set_font("Times", "I", 18)
                    t = f["texto"].encode("latin-1", "replace").decode("latin-1")
                    capa.text(z["x"] + 4, z["y"] + z["alto"] - 8, t[:60])
            elif z.get("tipo") == "fecha" and f.get("fecha"):
                capa.set_font("Helvetica", "", 9)
                capa.text(z["x"] + 2, z["y"] + z["alto"] - 4, f["fecha"])
        overlay = PdfReader(io.BytesIO(bytes(capa.output()))).pages[0]
        pagina.merge_page(overlay)
        w.add_page(pagina)
    salida = io.BytesIO()
    w.write(salida)
    return salida.getvalue()


def firma_viva(db: Session, e: Expediente) -> Optional[FirmaDocumento]:
    return (db.query(FirmaDocumento)
            .filter(FirmaDocumento.expediente_id == e.id, FirmaDocumento.documento == "documentos", FirmaDocumento.estado == "enviada")
            .order_by(FirmaDocumento.id.desc()).first())


def crear_demo(db: Session, e: Expediente, cuenta_id: int, u, nombre_cand: str, correo_cand: str) -> FirmaDocumento:
    f = FirmaDocumento(
        cuenta_id=cuenta_id, expediente_id=e.id, documento="documentos", modo="demo",
        signature_request_id=f"demo-{secrets.token_hex(8)}", estado="enviada", test_mode=True, creado_por=u.nombre, eventos=[],
        firmantes=[{"rol": "rh", "nombre": u.nombre, "correo": u.correo or "", "signature_id": "demo-rh", "estado": "pendiente"},
                   {"rol": "candidato", "nombre": nombre_cand, "correo": correo_cand or "", "signature_id": "demo-candidato", "estado": "pendiente"}],
    )
    db.add(f)
    db.flush()
    registrar(db, u.nombre, "firma_solicitada", "expediente", str(e.id), {"documento": "documentos", "modo": "demo", "correo_rh": u.correo or ""})
    return f


def firmar_demo(db: Session, f: FirmaDocumento, rol: str, imagen: str = "", texto: str = "") -> FirmaDocumento:
    """Registra la firma de `rol` (dibujo PNG o nombre escrito). Con ambos firmantes genera el PDF final con marca de
    agua y lo guarda como «Contrato firmado». No hace commit. ValueError = dato inválido / ya firmado."""
    from ..models import TIPO_CONTRATO_FIRMADO
    from . import onboarding as onb

    if f.modo != "demo" or f.estado != "enviada":
        raise ValueError("Esta solicitud ya no admite firmas.")
    x = next((y for y in (f.firmantes or []) if y.get("rol") == rol), None)
    if x is None:
        raise ValueError("Firmante no encontrado.")
    if x.get("estado") == "firmado":
        raise ValueError("Ya firmaste este documento.")
    texto = (texto or "").strip()[:80]
    png = _imagen_firma(imagen) if imagen else None
    if not png and len(texto) < 3:
        raise ValueError("Dibuja tu firma o escribe tu nombre completo.")
    ahora = datetime.now(timezone.utc)
    f.firmantes = [{**y, "estado": "firmado", "firmado_en": ahora.isoformat(), "firma_texto": texto,
                    "firma_imagen": base64.b64encode(png).decode() if png else ""} if y is x else y for y in f.firmantes]
    f.eventos = list(f.eventos or []) + [{"fecha": ahora.isoformat(), "tipo": f"firmado_demo_{rol}"}]
    e = db.get(Expediente, f.expediente_id)
    registrar(db, x.get("nombre") or rol, "documento_firmado_demo", "expediente", str(e.id), {"firma": f.id, "rol": rol})
    if all(y.get("estado") == "firmado" for y in f.firmantes):
        from ..fechas import local

        pdf, zonas = pdf_documentos(e)
        firmas = {}
        for y in f.firmantes:
            cuando = local(datetime.fromisoformat(y["firmado_en"])) if y.get("firmado_en") else None
            firmas[ROL_ZONA.get(y["rol"], y["rol"])] = {
                "imagen": base64.b64decode(y["firma_imagen"]) if y.get("firma_imagen") else None,
                "texto": y.get("firma_texto") or "", "fecha": cuando.strftime("%d/%m/%Y") if cuando else "",
            }
        final = estampar_demo(pdf, zonas, firmas)
        doc = onb.guardar_documento_firmado(db, e, TIPO_CONTRATO_FIRMADO, final, f"documentos-firmados-demo-{e.id}.pdf",
                                            "Firma de demostración (sin validez legal)", canal="firma_demo")
        f.documento_id, f.estado, f.firmada_en = doc.id, "descargada", ahora
        onb.sincronizar_contrato(db, e)
        registrar(db, "sistema", "documentos_firmados", "expediente", str(e.id), {"firma": f.id, "modo": "demo"})
    return f


def firmado(db: Session, e: Expediente) -> bool:
    """¿Ya quedaron firmados los documentos de contratación (cualquier modo)?"""
    from ..models import TIPO_CONTRATO_FIRMADO
    from . import onboarding as onb

    if any(d.interno and d.tipo == TIPO_CONTRATO_FIRMADO for d in (e.documentos or [])):
        return True
    return onb.firma_contrato(db, e) is not None
