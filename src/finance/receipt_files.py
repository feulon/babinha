"""Receipt uploads: what we accept and how files are named in the private bucket.

The type is detected from the file's first bytes, never from its name or the browser's
claimed content type. Files are stored under a random name; the original name is only shown.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.core.files.uploadedfile import UploadedFile

MAX_BYTES = 10 * 1024 * 1024
MAX_SIZE_LABEL = "10 MB"
ACCEPT_ATTR = "image/jpeg,image/png,image/webp,application/pdf"
ORIGINAL_NAME_MAX = 120


@dataclass(frozen=True)
class FileKind:
    content_type: str
    extension: str


JPEG = FileKind("image/jpeg", "jpg")
PNG = FileKind("image/png", "png")
WEBP = FileKind("image/webp", "webp")
PDF = FileKind("application/pdf", "pdf")
KINDS_BY_CONTENT_TYPE = {kind.content_type: kind for kind in (JPEG, PNG, WEBP, PDF)}


class ReceiptFileError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def sniff(head: bytes) -> FileKind | None:
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if head.startswith(b"%PDF-"):
        return PDF
    return None


def inspect(upload: UploadedFile) -> FileKind:
    """The detected kind of an accepted upload, or ReceiptFileError saying why it was rejected."""
    if upload.size is None or upload.size == 0:
        raise ReceiptFileError("O arquivo está vazio.")
    if upload.size > MAX_BYTES:
        raise ReceiptFileError(f"Arquivo grande demais. O limite é {MAX_SIZE_LABEL}.")
    upload.seek(0)
    kind = sniff(upload.read(16))
    upload.seek(0)
    if kind is None:
        raise ReceiptFileError("Envie uma foto (JPG, PNG ou WebP) ou um PDF do comprovante.")
    return kind


def storage_name(kind: FileKind, now: datetime) -> str:
    return f"comprovantes/{now:%Y/%m}/{uuid.uuid4().hex}.{kind.extension}"


def display_name(upload: UploadedFile) -> str:
    return (upload.name or "").rsplit("/", 1)[-1][:ORIGINAL_NAME_MAX]
