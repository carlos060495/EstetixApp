"""Validación y saneado de imágenes subidas (logo del salón).

Principios (DIRECTRICES §4.2/§4.6): no se confía ni en la extensión ni en el
Content-Type que declara el cliente — el tipo real se detecta por magic bytes
y la imagen se RE-ENCODEA con Pillow, lo que elimina EXIF/metadata y
neutraliza payloads incrustados. SVG queda excluido por diseño (XML ejecutable).
"""

from __future__ import annotations

import logging
import io

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger("estetix.images")

MAX_LOGO_BYTES = 2 * 1024 * 1024  # espejo del límite del bucket 'salon-assets'
MAX_LOGO_DIM = 512  # cap de dimensiones del logo re-encodeado
_MAX_SOURCE_DIM = 6000  # rechazo previo a decodificar (anti decompression-bomb)

# Formato Pillow → (extensión, MIME). Solo raster: SVG no permitido.
_ALLOWED_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
}


class InvalidImageError(ValueError):
    """La imagen no es un PNG/JPEG/WebP válido o excede los límites."""


def _sniff_magic(data: bytes) -> str | None:
    """MIME real por magic bytes; None si no es png/jpeg/webp."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def reencode_logo(data: bytes) -> tuple[bytes, str, str]:
    """Valida y re-encodea un logo. Devuelve (bytes, extensión, mime).

    Lanza InvalidImageError con mensaje legible si el archivo no es una
    imagen soportada, está corrupto o tiene dimensiones desmesuradas.
    """
    if _sniff_magic(data) is None:
        raise InvalidImageError("Formato no soportado. Usa una imagen PNG, JPEG o WebP.")

    try:
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError) as e:
        # Sin el contenido del fichero, claro: solo qué tipo de error dio Pillow.
        logger.warning("Logo rechazado al abrirlo: %s: %s", type(e).__name__, e)
        raise InvalidImageError("El archivo no es una imagen válida.") from e

    fmt = (img.format or "").upper()
    if fmt not in _ALLOWED_FORMATS:
        raise InvalidImageError("Formato no soportado. Usa una imagen PNG, JPEG o WebP.")
    ext, mime = _ALLOWED_FORMATS[fmt]

    # img.size sale de la cabecera sin decodificar los píxeles: corta las
    # bombas de descompresión antes de gastar memoria.
    if img.width > _MAX_SOURCE_DIM or img.height > _MAX_SOURCE_DIM:
        raise InvalidImageError("La imagen es demasiado grande (máximo 6000×6000 píxeles).")
    if img.width < 1 or img.height < 1:
        raise InvalidImageError("El archivo no es una imagen válida.")

    try:
        # Conserva la orientación visual antes de tirar el EXIF al re-guardar.
        img = ImageOps.exif_transpose(img)

        if fmt == "JPEG":
            img = img.convert("RGB")
        elif img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")

        img.thumbnail((MAX_LOGO_DIM, MAX_LOGO_DIM))

        out = io.BytesIO()
        if fmt == "JPEG":
            img.save(out, format="JPEG", quality=90, optimize=True)
        elif fmt == "PNG":
            img.save(out, format="PNG", optimize=True)
        else:
            img.save(out, format="WEBP", quality=90)
    except OSError as e:
        logger.warning("Logo rechazado al reescribirlo: %s", e)
        raise InvalidImageError("No se pudo procesar la imagen; puede estar corrupta.") from e

    return out.getvalue(), ext, mime
