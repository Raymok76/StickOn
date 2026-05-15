from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths


def autosession_path() -> Path:
    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    root.mkdir(parents=True, exist_ok=True)
    return root / "last_session.pur"
