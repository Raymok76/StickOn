from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class HistoryEntry:
    do_redo: Callable[[], None]
    undo: Callable[[], None]
    label: str = ""


class HistoryManager:
    def __init__(self, max_undo: int = 200) -> None:
        self._undo: list[HistoryEntry] = []
        self._redo: list[HistoryEntry] = []
        self._max = max_undo

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def push(self, entry: HistoryEntry) -> None:
        self._undo.append(entry)
        if len(self._undo) > self._max:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        e = self._undo.pop()
        e.undo()
        self._redo.append(e)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        e = self._redo.pop()
        e.do_redo()
        self._undo.append(e)
        return True
