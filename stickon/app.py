"""Application bootstrap and service wiring."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from stickon.services.image_io import ensure_image_plugins
from stickon.ui.main_window import MainWindow

_ICO_DIMS = re.compile(r"^(\d+)x(\d+)", re.I)


def _ico_path_sort_key(path: Path) -> tuple[int, int, str]:
    m = _ICO_DIMS.match(path.stem)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        return (w * h, w, path.name.lower())
    return (0, 0, path.name.lower())


def _program_icon_from_dir(ico_dir: Path) -> QIcon | None:
    if not ico_dir.is_dir():
        return None
    # Prefer multi-resolution WxH_*.ico (e.g. 16x16_01, 256x256_01) over a single app.ico,
    # otherwise app.ico shadows the rest and small sizes never load.
    dimensional = [p for p in ico_dir.glob("*.ico") if _ICO_DIMS.match(p.stem)]
    if dimensional:
        merged = QIcon()
        for p in sorted(dimensional, key=_ico_path_sort_key):
            merged.addFile(str(p))
        return merged if not merged.isNull() else None
    for name in ("app.ico", "icon.ico", "stickon.ico"):
        p = ico_dir / name
        if p.is_file():
            icon = QIcon(str(p))
            if not icon.isNull():
                return icon
    merged = QIcon()
    for p in sorted(ico_dir.glob("*.ico"), key=_ico_path_sort_key):
        merged.addFile(str(p))
    return merged if not merged.isNull() else None


def _program_icon() -> QIcon | None:
    """Load taskbar / window icons from ``assets/ico`` (``.ico`` files)."""
    pkg = Path(__file__).resolve().parent
    for ico_dir in (pkg / "assets" / "ico", pkg.parent / "assets" / "ico"):
        icon = _program_icon_from_dir(ico_dir)
        if icon is not None:
            return icon
    return None


class StickOnApplication:
    """Builds QApplication settings and the main window."""

    @staticmethod
    def build(argv: Sequence[str]) -> tuple[QApplication, MainWindow]:
        if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
            )
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)

        app = QApplication(list(argv))
        app.setApplicationName("StickOn")
        app.setOrganizationName("StickOn")

        icon = _program_icon()
        if icon is not None:
            app.setWindowIcon(icon)

        window = MainWindow()
        if icon is not None:
            window.setWindowIcon(icon)
        return app, window


def run_app(argv: Sequence[str] | None = None) -> int:
    ensure_image_plugins()
    args = argv if argv is not None else sys.argv
    app, window = StickOnApplication.build(args)
    window.show()
    return app.exec()
