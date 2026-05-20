# PyInstaller spec for StickOn (Windows GUI, PySide6).
# Run from repo root: uv run pyinstaller stickon.spec --noconfirm
# Output: dist/StickOn.exe (single-file; unpacks to a temp folder at startup)
#
# Hooks pull in only used Qt DLLs/widgets (much smaller than collect_all(PySide6)).
#
# Icon: Windows embeds one .ico in the EXE. If `stickon/assets/ico/` has WxH_*.ico
# (e.g. 16x16_01, 32x32_01), we merge them with Pillow so Explorer gets all sizes.

import re
from pathlib import Path

# Provided by PyInstaller when this spec is executed (directory containing the `.spec`).
SPEC_ROOT = Path(SPECPATH).resolve()
_ICO_DIMS = re.compile(r"^(\d+)x(\d+)", re.I)


def _ico_sort_key(path: Path) -> tuple[int, int, str]:
    m = _ICO_DIMS.match(path.stem)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        return (w * h, w, path.name.lower())
    return (0, 0, path.name.lower())


def _dimensional_ico_paths(ico_dir: Path) -> list[Path]:
    if not ico_dir.is_dir():
        return []
    paths = [p for p in ico_dir.glob("*.ico") if _ICO_DIMS.match(p.stem)]
    return sorted(paths, key=_ico_sort_key)


def _merge_into_single_ico(sources: list[Path], destination: Path) -> bool:
    """Combine single-size .ico files into one multi-resolution .ico for the PE icon resource."""
    if not sources:
        return False
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        images = []
        for p in sources:
            im = Image.open(p)
            if im.mode == "P" and "transparency" in im.info:
                im = im.convert("RGBA")
            elif im.mode == "P":
                im = im.convert("RGBA")
            elif im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            images.append(im)
        # Pillow: largest image first + explicit sizes => all WxH variants embedded.
        rev = list(reversed(images))
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Prefer DIB/BMP frames (not PNG-in-ICO). Some Explorer views cache badly or
        # fall back to the generic app icon for PNG-compressed .ico resources.
        if len(rev) == 1:
            rev[0].save(destination, format="ICO", bitmap_format="bmp")
        else:
            sz = [(im.width, im.height) for im in rev]
            rev[0].save(
                destination,
                format="ICO",
                bitmap_format="bmp",
                sizes=sz,
                append_images=rev[1:],
            )
    except OSError:
        return False
    return destination.is_file()


def _exe_icon_path(ico_dir: Path, work_root: Path) -> str | None:
    dim = _dimensional_ico_paths(ico_dir)
    merged = work_root / "stickon_merged_for_exe.ico"
    if dim and _merge_into_single_ico(dim, merged):
        return str(merged)
    if not ico_dir.is_dir():
        return None
    for name in ("app.ico", "icon.ico", "stickon.ico"):
        p = ico_dir / name
        if p.is_file():
            return str(p)
    best: Path | None = None
    best_area = -1
    for p in ico_dir.glob("*.ico"):
        m = _ICO_DIMS.match(p.stem)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            area = w * h
            if area > best_area:
                best_area = area
                best = p
    return str(best) if best else None


# Windows Explorer reads the icon resource embedded here (runtime QIcon is separate).
_APP_ICO = _exe_icon_path(SPEC_ROOT / "stickon" / "assets" / "ico", Path(workpath))

a = Analysis(
    [str(SPEC_ROOT / "stickon" / "main.py")],
    pathex=[str(SPEC_ROOT)],
    binaries=[],
    datas=[(str(SPEC_ROOT / "stickon" / "assets"), "stickon/assets")],
    hiddenimports=[
        "pillow_heif",
        "pillow_heif._libheif_cffi",
        "_cffi_backend",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="StickOn",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=_APP_ICO,
)
