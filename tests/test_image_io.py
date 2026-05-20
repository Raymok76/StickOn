from pathlib import Path

import pytest
from PySide6.QtGui import QImage, QPixmap

from stickon.services.image_io import (
    PURE_REF_IMAGE_EXTENSIONS,
    can_import_image_path,
    ensure_image_plugins,
    heif_import_available,
    is_gif_path,
    is_pure_ref_image_path,
    load_still_pixmap,
)


def test_pure_ref_extension_set_includes_common_formats() -> None:
    for ext in (".png", ".jpg", ".webp", ".avif", ".heic", ".psd", ".svg", ".tga", ".exr"):
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
