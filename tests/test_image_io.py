from pathlib import Path

import pytest
from PySide6.QtGui import QImage, QPixmap

from stickon.services.image_io import (
    PURE_REF_IMAGE_EXTENSIONS,
    PdfImportEstimate,
    can_import_image_path,
    ensure_image_plugins,
    heif_import_available,
    is_gif_path,
    is_pdf_path,
    is_pure_ref_image_path,
    load_still_pixmap,
    parse_pdf_source_path,
    pdf_import_needs_memory_warning,
    pdf_import_warning_message,
    pdf_password_protected_message,
    pdf_source_path,
)


def test_pure_ref_extension_set_includes_common_formats() -> None:
    for ext in (".png", ".jpg", ".webp", ".avif", ".heic", ".psd", ".svg", ".tga", ".exr", ".pdf"):
        assert ext in PURE_REF_IMAGE_EXTENSIONS


def test_is_gif_path() -> None:
    assert is_gif_path("ref.GIF")
    assert not is_gif_path("ref.png")


def test_is_pure_ref_image_path() -> None:
    assert is_pure_ref_image_path("x.jxl")
    assert not is_pure_ref_image_path("x.mp4")


def test_can_import_rejects_missing_file(tmp_path: Path) -> None:
    assert not can_import_image_path(tmp_path / "nope.png")


def test_load_still_pixmap_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "tile.png"
    img = QImage(4, 4, QImage.Format.Format_RGB32)
    img.fill(0xFF8040)
    assert img.save(str(p))
    pm = load_still_pixmap(p)
    assert pm is not None
    assert not pm.isNull()
    assert pm.width() == 4


def test_load_still_pixmap_skips_gif(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "a.gif"
    p.write_bytes(b"GIF89a")
    assert load_still_pixmap(p) is None


def test_ensure_image_plugins_idempotent() -> None:
    ensure_image_plugins()
    ensure_image_plugins()
    assert heif_import_available() is True


def test_pdf_source_path_roundtrip(tmp_path: Path) -> None:
    pdf = tmp_path / "refs.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    sp = pdf_source_path(pdf, 3)
    base, page = parse_pdf_source_path(sp)
    assert base.resolve() == pdf.resolve()
    assert page == 3


def test_is_pdf_path() -> None:
    assert is_pdf_path("doc.PDF")
    assert not is_pdf_path("doc.png")


def test_pdf_import_memory_warning_threshold() -> None:
    small = PdfImportEstimate(page_count=5, estimated_rgba_bytes=50 * 1024 * 1024, disk_bytes=1)
    large = PdfImportEstimate(page_count=40, estimated_rgba_bytes=180 * 1024 * 1024, disk_bytes=1)
    assert not pdf_import_needs_memory_warning(small)
    assert pdf_import_needs_memory_warning(large)


def test_pdf_import_warning_message_mentions_pages() -> None:
    est = PdfImportEstimate(page_count=12, estimated_rgba_bytes=250 * 1024 * 1024, disk_bytes=1)
    msg = pdf_import_warning_message(est, "refs.pdf")
    assert "12 pages" in msg
    assert "Import anyway?" in msg


def test_pdf_password_protected_message() -> None:
    msg = pdf_password_protected_message("secret.pdf")
    assert msg == '"secret.pdf" is password-protected.'
