from datetime import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.clock import SAO_PAULO
from finance import receipt_files
from finance.receipt_files import JPEG, PDF, PNG, WEBP, ReceiptFileError

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
PDF_BYTES = b"%PDF-1.7\n" + b"\x00" * 32


def upload(content: bytes, name: str = "comprovante.png", content_type: str = "image/png"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class TestSniff:
    @pytest.mark.parametrize(
        ("content", "kind"),
        [(PNG_BYTES, PNG), (JPEG_BYTES, JPEG), (WEBP_BYTES, WEBP), (PDF_BYTES, PDF)],
    )
    def test_detects_accepted_types(self, content: bytes, kind) -> None:
        assert receipt_files.sniff(content[:16]) == kind

    @pytest.mark.parametrize(
        "content",
        [b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"GIF89a", b"<html>", b"PK\x03\x04"],
    )
    def test_rejects_everything_else(self, content: bytes) -> None:
        assert receipt_files.sniff(content) is None


class TestInspect:
    def test_trusts_the_bytes_not_the_name_or_claimed_type(self) -> None:
        file = upload(PDF_BYTES, name="foto.png", content_type="image/png")
        assert receipt_files.inspect(file) == PDF
        assert file.tell() == 0

    def test_html_disguised_as_image_is_rejected(self) -> None:
        with pytest.raises(ReceiptFileError, match="foto"):
            receipt_files.inspect(upload(b"<script>alert(1)</script>", name="x.jpg"))

    def test_empty_file_is_rejected(self) -> None:
        with pytest.raises(ReceiptFileError, match="vazio"):
            receipt_files.inspect(upload(b""))

    def test_size_limit(self) -> None:
        big = PNG_BYTES + b"\x00" * (receipt_files.MAX_BYTES - len(PNG_BYTES) + 1)
        with pytest.raises(ReceiptFileError, match="10 MB"):
            receipt_files.inspect(upload(big))
        just_fits = big[:-1]
        assert receipt_files.inspect(upload(just_fits)) == PNG


class TestNames:
    def test_storage_name_is_random_and_uses_the_detected_extension(self) -> None:
        now = datetime(2026, 10, 13, 9, 0, tzinfo=SAO_PAULO)
        first = receipt_files.storage_name(PDF, now)
        second = receipt_files.storage_name(PDF, now)
        assert first.startswith("comprovantes/2026/10/")
        assert first.endswith(".pdf")
        assert first != second

    def test_display_name_drops_paths_and_is_capped(self) -> None:
        assert receipt_files.display_name(upload(PNG_BYTES, name="a/b/pix.png")) == "pix.png"
        long_name = "x" * 300 + ".png"
        assert len(receipt_files.display_name(upload(PNG_BYTES, name=long_name))) == 120
