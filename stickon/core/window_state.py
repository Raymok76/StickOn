from __future__ import annotations

import ctypes
import sys
from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    pass

# Win32 for z-order / extended styles
if sys.platform == "win32":
    HWND_BOTTOM = 1
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040


class ZOrderMode(str, Enum):
    normal = "normal"
    top = "top"
    bottom = "bottom"


class WindowStateController:
    """Windows-oriented window flags: topmost, bottom, click-through, opacity, move lock."""

    def __init__(self, window: QWidget) -> None:
        self._window = window
        self._always_on_top = True
        self._always_on_bottom = False
        self._click_through = False
        self._lock_position = False

    @property
    def click_through(self) -> bool:
        return self._click_through

    @property
    def lock_position(self) -> bool:
        return self._lock_position

    @property
    def always_on_top(self) -> bool:
        return self._always_on_top

    @property
    def always_on_bottom(self) -> bool:
        return self._always_on_bottom

    def _hwnd(self) -> int:
        return int(self._window.winId())

    def set_always_on_top(self, on: bool) -> None:
        self._always_on_top = on
        if on:
            self._always_on_bottom = False
        win = self._window
        geom = win.geometry()
        flags = win.windowFlags()
        if on:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        win.setWindowFlags(flags)
        win.setGeometry(geom)
        win.show()
        if sys.platform == "win32":
            self._win32_set_z_topmost(bool(on))

    def set_always_on_bottom(self, on: bool) -> None:
        self._always_on_bottom = on
        win = self._window
        geom = win.geometry()
        if on:
            self._always_on_top = False
            flags = win.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint
            win.setWindowFlags(flags)
        else:
            flags = win.windowFlags()
            win.setWindowFlags(flags)
        win.setGeometry(geom)
        win.show()
        if sys.platform == "win32":
            self._win32_set_z_bottom(on)

    def sync_win32_topmost_from_state(self) -> None:
        if sys.platform == "win32":
            self._win32_set_z_topmost(self._always_on_top)

    def toggle_always_on_top(self) -> None:
        self.set_always_on_top(not self._always_on_top)

    def toggle_always_on_bottom(self) -> None:
        self.set_always_on_bottom(not self._always_on_bottom)

    def set_opacity(self, value: float) -> None:
        v = max(0.1, min(1.0, value))
        self._window.setWindowOpacity(v)

    def adjust_opacity(self, delta: float) -> None:
        self.set_opacity(self._window.windowOpacity() + delta)

    def set_click_through(self, on: bool) -> None:
        self._click_through = on
        if sys.platform == "win32":
            from stickon.utils.win32_clickthrough import set_clickthrough

            set_clickthrough(self._hwnd(), on)
        else:
            canvas = getattr(self._window, "_canvas", None)
            if canvas is not None:
                canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
            else:
                self._window.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)

    def toggle_click_through(self) -> None:
        self.set_click_through(not self._click_through)

    def set_lock_position(self, on: bool) -> None:
        """Locks desktop position only (blocks title-bar drag); does not fix window size."""
        self._lock_position = on

    def toggle_lock_position(self) -> None:
        self.set_lock_position(not self._lock_position)

    def _win32_set_z_topmost(self, on: bool) -> None:
        user32 = ctypes.windll.user32
        hwnd = self._hwnd()
        after = HWND_TOPMOST if on else HWND_NOTOPMOST
        user32.SetWindowPos(
            hwnd,
            after,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _win32_set_z_bottom(self, on: bool) -> None:
        if not on:
            return
        user32 = ctypes.windll.user32
        hwnd = self._hwnd()
        user32.SetWindowPos(
            hwnd,
            HWND_BOTTOM,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
