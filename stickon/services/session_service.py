from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths


def _autosession_dir() -> Path:
    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    root.mkdir(parents=True, exist_ok=True)
    return root


def autosession_path() -> Path:
    """Path for the current-format last-session file (``.sti``)."""
    return _autosession_dir() / "last_session.sti"


def legacy_autosession_path() -> Path:
    """Pre-renamed autosave filename; still read once for upgrade, removed after save to ``.sti``."""
    return _autosession_dir() / "last_session.pur"


def resolved_autosession_path_for_read() -> Path | None:
    """Prefer ``last_session.sti``, fall back to ``last_session.pur`` if present."""
    cur = autosession_path()
    if cur.is_file():
        return cur
    leg = legacy_autosession_path()
    if leg.is_file():
        return leg
    return None
