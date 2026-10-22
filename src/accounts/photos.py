"""Profile photos: validated, squared, shrunk and stripped of metadata before they are stored.

The type comes from decoding the file, never from its name or the browser's claimed content
type. What gets stored is always a fresh small JPEG that we encoded ourselves, so EXIF (GPS
included) and anything hidden in the original never reach the bucket. Files live in the same
private bucket as the Pix receipts and are only served to signed-in players.
"""

import io
import logging
import uuid
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile
from django.core.files.storage import Storage, storages
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

if TYPE_CHECKING:
    from accounts.models import Player

logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024
MAX_SIZE_LABEL = "10 MB"
MAX_PIXELS = 50_000_000  # rejects decompression bombs before the pixels are decoded
ACCEPT_ATTR = "image/jpeg,image/png,image/webp"
ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
SIDE = 256
JPEG_QUALITY = 82
CONTENT_TYPE = "image/jpeg"
REJECTED = "Envie uma foto em JPG, PNG ou WebP."


class PhotoError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def photo_storage() -> Storage:
    """The private bucket (see STORAGES["receipts"]). Resolved lazily so tests can swap it."""
    return storages["receipts"]


def process(upload: UploadedFile) -> bytes:
    """A SIDE x SIDE JPEG of the upload, or PhotoError saying why it was rejected."""
    if not upload.size:
        raise PhotoError("O arquivo está vazio.")
    if upload.size > MAX_BYTES:
        raise PhotoError(f"Foto grande demais. O limite é {MAX_SIZE_LABEL}.")
    upload.seek(0)
    try:
        # Only these decoders ever see the bytes: any other format is refused unopened.
        with Image.open(upload, formats=ALLOWED_FORMATS) as image:
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise PhotoError("Foto grande demais. Tire uma menor ou reduza a imagem.")
            image.load()
            upright = ImageOps.exif_transpose(image)
            square = ImageOps.fit(upright.convert("RGB"), (SIDE, SIDE), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise PhotoError(REJECTED) from None
    finally:
        upload.seek(0)
    out = io.BytesIO()
    square.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)  # no exif= : metadata dropped
    return out.getvalue()


def storage_name() -> str:
    return f"fotos/{uuid.uuid4().hex}.jpg"


def store(data: bytes) -> str:
    """Write the processed photo; returns the name it was stored under."""
    return photo_storage().save(storage_name(), ContentFile(data))


def delete(name: str) -> None:
    """Best effort: a photo left behind in the bucket is harmless, a failed save is not."""
    if not name:
        return
    try:
        photo_storage().delete(name)
    except Exception:
        logger.exception("Could not delete profile photo %s from storage", name)


def replace(player: "Player", data: bytes | None) -> None:
    """Point the player at a new photo (or at none when data is None) and drop the old file."""
    old = player.photo
    player.photo = store(data) if data else ""
    player.save(update_fields=["photo"])
    if old and old != player.photo:
        delete(old)
