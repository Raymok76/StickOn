"""Load still images using PureRef-style extensions (Qt, then Pillow fallback)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage, QImageReader, QPixmap

# PureRef 2 FAQ still-image formats, plus common aliases and camera RAW extensions.
PURE_REF_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".avif",
        ".avifs",
        ".bmp",
        ".dib",
        ".dds",
        ".gif",
        ".heic",
        ".heif",
        ".hif",
        ".ico",
        ".cur",
        ".jpg",
        ".jpeg",
        ".jfif",
        ".jpe",
        ".jp2",
        ".j2k",
        ".jpc",
        ".jpx",
        ".jxl",
        ".pbm",
        ".pgm",
        ".png",
        ".pnm",
        ".pam",
        ".pfm",
        ".ppm",
        ".psd",
        ".psb",
        ".qoi",
        ".arw",
        ".cr2",
        ".cr3",
        ".dng",
        ".nef",
        ".nrw",
        ".orf",
        ".raf",
        ".rw2",
        ".raw",
        ".srw",
        ".svg",
        ".svgz",
        ".tif",
        ".tiff",
        ".webp",
        ".xbm",
        ".xpm",
        ".tga",
        ".exr",
    }
)

_QT_IMAGE_EXTENSIONS: frozenset[str] | None = None
_PILLOW_PLUGINS_READY = False


def ensure_image_plugins() -> None:
    """Register optional Pillow decoders (HEIC/HEIF via pillow-heif). Safe to call repeatedly."""
    global _PILLOW_PLUGINS_READY
    if _PILLOW_PLUGINS_READY:
        return
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        pass
    _PILLOW_PLUGINS_READY = True


def heif_import_available() -> bool:
    """True when pillow-heif registered HEIF/HEIC with Pillow (bundled libheif in release builds)."""
    ensure_image_plugins()
    try:
        from PIL import Image
    except ImportError:
        return False
    exts = Image.registered_extensions()
    return ".heic" in exts and exts.get(".heic") == "HEIF"


def _qt_image_extensions() -> frozenset[str]:
    global _QT_IMAGE_EXTENSIONS
    if _QT_IMAGE_EXTENSIONS is None:
        exts: set[str] = set()
        for raw in QImageReader.supportedImageFormats():
            s = bytes(raw).decode("ascii", "ignore").lower()
            if s:
                exts.add(f".{s}")
        _QT_IMAGE_EXTENSIONS = frozenset(exts)
    return _QT_IMAGE_EXTENSIONS


def is_gif_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() == ".gif"


def is_pure_ref_image_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in PURE_REF_IMAGE_EXTENSIONS


def can_import_image_path(path: Path | str) -> bool:
    """Whether StickOn should try to open this file as a reference image."""
    p = Path(path)
    if not p.is_file():
        return False
    if is_pure_ref_image_path(p):
        return True
    reader = QImageReader(str(p))
    if reader.canRead():
        return True
    return _pillow_can_open(p)


def file_dialog_image_filter() -> str:
    """Qt file-dialog filter covering PureRef-listed still formats."""
    patterns = sorted(
        f"*.{ext.lstrip('.')}"
        for ext in PURE_REF_IMAGE_EXTENSIONS
        if ext != ".gif"
    )
    return f"Images ({' '.join(patterns)});;GIF (*.gif);;All files (*.*)"


def _pillow_can_open(path: Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    ensure_image_plugins()
    try:
        with Image.open(path) as im:
            # verify() breaks some HEIF files; load() exercises the full decoder.
            im.load()
        return True
    except OSError:
        return False


def _pil_to_qimage(im) -> QImage:  # PIL.Image.Image
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    elif im.mode == "RGB":
        im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    qimg = QImage(data, im.width, im.height, QImage.Format.Format_RGBA8888)
    return qimg.copy()


def _load_with_qt(path: Path) -> QImage | None:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    img = reader.read()
    if img.isNull():
        return None
    return img


def _load_with_pillow(path: Path) -> QImage | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    ensure_image_plugins()
    try:
        with Image.open(path) as im:
            im.load()
            return _pil_to_qimage(im)
    except OSError:
        return None


def load_still_pixmap(path: Path | str) -> QPixmap | None:
    """Decode a non-animated still image to a pixmap (GIF excluded — use QMovie)."""
    p = Path(path)
    if not p.is_file() or is_gif_path(p):
        return None
    img = _load_with_qt(p)
    if img is None:
        img = _load_with_pillow(p)
    if img is None or img.isNull():
        return None
    pm = QPixmap.fromImage(img)
    return pm if not pm.isNull() else None


def load_gif_poster_pixmap(path: Path | str, *, fallback_size: int = 32) -> QPixmap:
    """First frame / poster for GIF nodes."""
    p = Path(path)
    reader = QImageReader(str(p))
    reader.setAutoTransform(True)
    first = reader.read()
    if not first.isNull():
        pm = QPixmap.fromImage(first)
        if not pm.isNull() and pm.width() >= 2:
            return pm
    pm = QPixmap(fallback_size, fallback_size)
    pm.fill()
    return pm
