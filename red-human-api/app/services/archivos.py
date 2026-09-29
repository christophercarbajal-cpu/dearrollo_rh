"""Validación y almacenamiento de archivos (CVs de prospectos y documentos de expediente).

Toda subida pasa por aquí antes de tocar disco o la IA: se valida extensión,
tamaño y **firma binaria** (un `.pdf` que en realidad es otra cosa se rechaza),
y se guarda con un nombre saneado dentro de `red-human-api/uploads/`.
"""

import base64
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, UploadFile

MAX_BYTES = 10 * 1024 * 1024  # 10 MB
MIN_BYTES = 512  # por debajo de esto no hay documento legible

# extensión → (mime, firmas binarias aceptadas)
FORMATOS = {
    "pdf": ("application/pdf", [b"%PDF-"]),
    "png": ("image/png", [b"\x89PNG\r\n\x1a\n"]),
    "jpg": ("image/jpeg", [b"\xff\xd8\xff"]),
    "jpeg": ("image/jpeg", [b"\xff\xd8\xff"]),
    "webp": ("image/webp", [b"RIFF"]),
}

EXTENSIONES_OK = ", ".join(sorted({e.upper() for e in FORMATOS}))

# Formatos frecuentes que el modelo de visión no puede leer — se rechazan con instrucción concreta.
PISTAS = {
    "heic": "Las fotos HEIC del iPhone no se pueden leer. En Ajustes › Cámara › Formatos elige «Más compatible», o comparte la foto como JPG.",
    "heif": "Convierte la imagen a JPG o PNG antes de subirla.",
    "doc": "Exporta el documento a PDF antes de subirlo.",
    "docx": "Exporta el documento a PDF antes de subirlo.",
    "pages": "Exporta el documento a PDF antes de subirlo.",
    "zip": "Sube cada archivo por separado, no comprimido.",
    "rar": "Sube cada archivo por separado, no comprimido.",
}

RAIZ_UPLOADS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")


@dataclass(frozen=True)
class ArchivoValidado:
    contenido: bytes
    nombre: str  # nombre original saneado
    extension: str  # sin punto, en minúsculas
    mime: str
    tamano: int

    @property
    def b64(self) -> str:
        return base64.standard_b64encode(self.contenido).decode()

    @property
    def es_imagen(self) -> bool:
        return self.mime.startswith("image/")


def _sanear(nombre: str) -> str:
    plano = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    limpio = re.sub(r"[^A-Za-z0-9._-]+", "_", plano).strip("._-")
    return (limpio or "archivo")[:120]


# Evaluaciones unificadas (2026-09-29): el resultado admite además Word (especificación, sección 5). Solo para
# adjuntos que lee una PERSONA — los documentos del expediente siguen sin Word (el modelo de visión no lo lee).
FORMATOS_ADJUNTO_EVALUACION = {
    **FORMATOS,
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", [bytes.fromhex("504b0304")]),
    "doc": ("application/msword", [bytes.fromhex("d0cf11e0a1b11ae1")]),
}


def _firma_valida(contenido: bytes, extension: str, formatos: Optional[dict] = None) -> bool:
    firmas = (formatos or FORMATOS)[extension][1]
    cabecera = contenido[:32]
    if extension == "webp":
        return cabecera.startswith(b"RIFF") and contenido[8:12] == b"WEBP"
    return any(cabecera.startswith(f) for f in firmas)


async def validar(archivo: UploadFile, etiqueta: str = "archivo") -> ArchivoValidado:
    """Lee la subida y la valida. Lanza HTTPException con mensaje para RH si algo falla."""
    return validar_bytes(await archivo.read(), archivo.filename or "archivo", etiqueta)


def validar_bytes(contenido: bytes, nombre_original: str, etiqueta: str = "archivo", formatos: Optional[dict] = None) -> ArchivoValidado:
    """Misma validación que `validar` para un binario ya en memoria (2026-09-15: documentos que
    llegan por WhatsApp y se descargan de Meta) — extensión admitida, tamaño y firma binaria."""
    nombre = _sanear(nombre_original or "archivo")
    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""

    formatos = formatos or FORMATOS
    if extension not in formatos:
        pista = f"Acepta: {', '.join(sorted({e.upper() for e in formatos}))}." if formatos is not FORMATOS else PISTAS.get(extension, f"Acepta: {EXTENSIONES_OK}.")
        raise HTTPException(415, f"No se puede procesar «{nombre}». {pista}")

    tamano = len(contenido)
    if tamano > MAX_BYTES:
        raise HTTPException(413, f"El {etiqueta} pesa {tamano // 1024 // 1024} MB; el máximo son 10 MB.")
    if tamano < MIN_BYTES:
        raise HTTPException(422, f"El {etiqueta} está vacío o dañado ({tamano} bytes).")
    if not _firma_valida(contenido, extension, formatos):
        raise HTTPException(
            422,
            f"El {etiqueta} dice ser .{extension} pero su contenido no corresponde. "
            "Vuelve a exportarlo o súbelo en otro formato.",
        )

    return ArchivoValidado(
        contenido=contenido,
        nombre=nombre,
        extension=extension,
        mime=formatos[extension][0],
        tamano=tamano,
    )


def guardar(archivo: ArchivoValidado, carpeta: str, base: str) -> str:
    """Escribe el archivo en `uploads/<carpeta>/<base>.<ext>` y regresa la ruta absoluta."""
    destino = os.path.join(RAIZ_UPLOADS, carpeta)
    os.makedirs(destino, exist_ok=True)
    ruta = os.path.join(destino, f"{_sanear(base)}.{archivo.extension}")
    with open(ruta, "wb") as f:
        f.write(archivo.contenido)
    return ruta


def guardar_bytes(contenido: bytes, carpeta: str, nombre: str) -> str:
    """Guarda un binario tal cual (adjuntos de cursos, 2026-09-16) en `uploads/<carpeta>/<nombre saneado>`."""
    destino = os.path.join(RAIZ_UPLOADS, carpeta)
    os.makedirs(destino, exist_ok=True)
    ruta = os.path.join(destino, _sanear(nombre))
    with open(ruta, "wb") as f:
        f.write(contenido)
    return ruta


def existe(ruta: Optional[str]) -> bool:
    return bool(ruta) and os.path.isfile(ruta)
