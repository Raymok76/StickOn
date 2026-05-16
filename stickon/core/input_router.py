from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent


@dataclass
class KeyChord:
    ctrl: bool = False
    shift: bool = False
    alt: bool = False
    key: int | None = None  # Qt.Key_*

    def match(
        self,
        ctrl: bool,
        shift: bool,
        alt: bool,
        key: int,
    ) -> bool:
        return (
            self.ctrl == ctrl
            and self.shift == shift
            and self.alt == alt
            and self.key == key
        )


def parse_shortcut(s: str) -> KeyChord | None:
    if not s or not s.strip():
        return None
    s = s.strip()
    # "Ctrl+Shift++" splits badly on plain '+'; normalize trailing "++" key to a named token.
    if re.search(r"\+\+\s*$", s):
        s = re.sub(r"\+\+\s*$", "+plus", s)
    parts = [p.strip().lower() for p in re.split(r"\+", s) if p.strip()]
    ctrl = "ctrl" in parts or "control" in parts
    shift = "shift" in parts
    alt = "alt" in parts
    key_tokens = [p for p in parts if p not in {"ctrl", "control", "shift", "alt"}]
    if not key_tokens:
        return None
    tok = key_tokens[-1]
    key_map = {
        "a": Qt.Key_A,
        "b": Qt.Key_B,
        "c": Qt.Key_C,
        "d": Qt.Key_D,
        "e": Qt.Key_E,
        "f": Qt.Key_F,
        "g": Qt.Key_G,
        "i": Qt.Key_I,
        "n": Qt.Key_N,
        "p": Qt.Key_P,
        "plus": Qt.Key_Plus,
        "s": Qt.Key_S,
        "t": Qt.Key_T,
        "w": Qt.Key_W,
        "y": Qt.Key_Y,
        "z": Qt.Key_Z,
        "+": Qt.Key_Plus,
        "=": Qt.Key_Equal,
        "-": Qt.Key_Minus,
        "left": Qt.Key_Left,
        "right": Qt.Key_Right,
        "up": Qt.Key_Up,
        "down": Qt.Key_Down,
        "escape": Qt.Key_Escape,
        "del": Qt.Key_Delete,
        "delete": Qt.Key_Delete,
    }
    k = key_map.get(tok, None)
    if k is None and len(tok) == 1:
        k = ord(tok.upper())
    if k is None:
        return None
    return KeyChord(ctrl=ctrl, shift=shift, alt=alt, key=k)


class InputRouter:
    """Maps Qt key events to command ids using metadata shortcuts."""

    def __init__(self, shortcut_to_command: dict[str, str]) -> None:
        self._shortcut_to_command = shortcut_to_command
        self._parsed: list[tuple[KeyChord, str]] = []
        for sc, cid in shortcut_to_command.items():
            ch = parse_shortcut(sc)
            if ch and ch.key is not None:
                self._parsed.append((ch, cid))

    def match_key_event(self, event: object) -> str | None:
        if not isinstance(event, QKeyEvent):
            return None
        if event.type() != event.Type.KeyPress:
            return None
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        key = int(event.key())
        for ch, cid in self._parsed:
            if ch.match(ctrl, shift, alt, key):
                return cid
        return None
