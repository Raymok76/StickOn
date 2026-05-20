"""Load still images using PureRef-style extensions (Qt, then Pillow fallback)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QImageReader, QPixmap
from PySide6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions

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
        ".pdf",
    }
)

# Max longest edge when rasterizing a PDF page (reference preview, not print).
_PDF_RENDER_MAX_SIDE = 4096
_PDF_RENDER_DPI = 150.0
_POINTS_PER_INCH = 72.0
_RGBA_BYTES_PER_PIXEL = 4
# Warn when estimated pixmap RAM (all pages) exceeds this (import peak can be higher).
_PDF_IMPORT_WARN_RGBA_BYTES = 200 * 1024 * 1024
_PDF_IMPORT_PEAK_FACTOR = 1.25


@dataclass(frozen=True)
class PdfImportEstimate:
    page_count: int
    estimated_rgba_bytes: int
    disk_bytes: int

    @property
    def estimated_peak_bytes(self) -> int:
        return int(self.estimated_rgba_bytes * _PDF_IMPORT_PEAK_FACTOR)

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


def is_pdf_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() == ".pdf"


def pdf_source_path(file_path: Path | str, page_index: int) -> str:
    """Stable source id for a page inside a PDF (used on canvas nodes)."""
    return f"{Path(file_path).resolve()}::page={page_index}"


def parse_pdf_source_path(source_path: str | None) -> tuple[Path, int | None]:
    """Split ``path::page=N`` into file path and optional page index."""
    if not source_path:
        raise ValueError("empty source path")
    marker = "::page="
    if marker in source_path:
        base, page_s = source_path.rsplit(marker, 1)
        return Path(base), int(page_s)
    return Path(source_path), None


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


def _pdf_render_size(doc: QPdfDocument, page: int) -> QSize:
    pt = doc.pagePointSize(page)
    if pt.isEmpty():
        return QSize(1024, 1024)
    w_pt = float(pt.width())
    h_pt = float(pt.height())
    scale = _PDF_RENDER_DPI / _POINTS_PER_INCH
    pw = max(1, int(w_pt * scale))
    ph = max(1, int(h_pt * scale))
    longest = max(pw, ph)
    if longest > _PDF_RENDER_MAX_SIDE:
        shrink = _PDF_RENDER_MAX_SIDE / longest
        pw = max(1, int(pw * shrink))
        ph = max(1, int(ph * shrink))
    return QSize(pw, ph)


def pdf_load_error(path: Path | str) -> QPdfDocument.Error | None:
    """Return None if the PDF can be opened for import; otherwise the Qt error code."""
    p = Path(path)
    if not p.is_file():
        return QPdfDocument.Error.FileNotFound
    doc = QPdfDocument()
    err = doc.load(str(p))
    if err != QPdfDocument.Error.None_:
        return err
    if doc.pageCount() < 1:
        return QPdfDocument.Error.InvalidFileFormat
    return None


def pdf_is_password_protected(path: Path | str) -> bool:
    return pdf_load_error(path) == QPdfDocument.Error.IncorrectPassword


def pdf_password_protected_message(path: Path | str) -> str:
    return f'"{Path(path).name}" is password-protected.'


def _open_pdf_document(path: Path) -> QPdfDocument | None:
    if pdf_load_error(path) is not None:
        return None
    doc = QPdfDocument()
    doc.load(str(path))
    return doc


def estimate_pdf_import(path: Path | str) -> PdfImportEstimate | None:
    """Estimate in-memory RGBA size if every page is rasterized at import settings."""
    p = Path(path)
    if not p.is_file():
        return None
    doc = _open_pdf_document(p)
    if doc is None:
        return None
    n = doc.pageCount()
    rgba_total = 0
    for i in range(n):
        sz = _pdf_render_size(doc, i)
        rgba_total += sz.width() * sz.height() * _RGBA_BYTES_PER_PIXEL
    return PdfImportEstimate(
        page_count=n,
        estimated_rgba_bytes=rgba_total,
        disk_bytes=p.stat().st_size,
    )


def pdf_import_needs_memory_warning(estimate: PdfImportEstimate) -> bool:
    return estimate.estimated_peak_bytes >= _PDF_IMPORT_WARN_RGBA_BYTES


def format_megabytes(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.0f}" if num_bytes >= 100 * 1024 * 1024 else f"{num_bytes / (1024 * 1024):.1f}"


def pdf_import_warning_message(estimate: PdfImportEstimate, file_path: Path | str) -> str:
    name = Path(file_path).name
    pages = estimate.page_count
    page_word = "page" if pages == 1 else "pages"
    mem_mb = format_megabytes(estimate.estimated_rgba_bytes)
    peak_mb = format_megabytes(estimate.estimated_peak_bytes)
    return (
        f'"{name}" has {pages} {page_word}. Importing will rasterize every page '
        f"(about {mem_mb} MB of image data, up to ~{peak_mb} MB peak while loading).\n\n"
        "Import anyway?"
    )


def _render_pdf_page(doc: QPdfDocument, page_index: int) -> QPixmap | None:
    if page_index < 0 or page_index >= doc.pageCount():
        return None
    size = _pdf_render_size(doc, page_index)
    img = doc.render(page_index, size, QPdfDocumentRenderOptions())
    if img.isNull():
        return None
    pm = QPixmap.fromImage(img)
    return pm if not pm.isNull() else None


def load_pdf_page_pixmap(path: Path | str, page_index: int) -> QPixmap | None:
    """Rasterize one PDF page to a pixmap."""
    p = Path(path)
    if not p.is_file():
        return None
    doc = _open_pdf_document(p)
    if doc is None:
        return None
    return _render_pdf_page(doc, page_index)


def load_pdf_pixmaps(path: Path | str) -> list[QPixmap]:
    """Rasterize every page in a PDF (one pixmap per page)."""
    p = Path(path)
    doc = _open_pdf_document(p)
    if doc is None:
        return []
    out: list[QPixmap] = []
    for i in range(doc.pageCount()):
        pm = _render_pdf_page(doc, i)
        if pm is not None:
            out.append(pm)
    return out


def load_still_pixmap(path: Path | str) -> QPixmap | None:
    """Decode a non-animated still image to a pixmap (GIF excluded — use QMovie)."""
    p = Path(path)
    if not p.is_file() or is_gif_path(p):
        return None
    if is_pdf_path(p):
        return load_pdf_page_pixmap(p, 0)
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
