from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStyle, QWidget


_BAR_BG = "#e4e4e4"
_CHIP_RADIUS = 6


class ToggleChipLabel(QLabel):
    """Pill toggle: click switches feature (caller refreshes active styling via set_active)."""

    def __init__(
        self,
        on_toggle: Callable[[], None],
        *,
        active_on_color: str = "#d6ebff",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_toggle = on_toggle
        self._active_on_color = active_on_color
        self.setWordWrap(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_active(self, active: bool) -> None:
        r = _CHIP_RADIUS
        pv, ph = 4, 12
        if active:
            c = self._active_on_color
            self.setStyleSheet(
                f"background-color: {c}; border: 1px solid {c}; "
                f"border-radius: {r}px; padding: {pv}px {ph}px; color: #1a1a1a;"
            )
        else:
            self.setStyleSheet(
                f"background-color: {_BAR_BG}; border: 1px solid white; "
                f"border-radius: {r}px; padding: {pv}px {ph}px; color: #333;"
            )

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_toggle()
            event.accept()
            return
        super().mousePressEvent(event)


class DraggableTitleBar(QWidget):
    """Frameless-window drag region + maximize + close; toggle chips."""

    def __init__(
        self,
        host_window: QWidget,
        segment_widgets: list[QWidget],
        *,
        trailing_widgets: list[QWidget] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host_window
        self._dragging = False
        self._press_global = QPoint()
        self._win_origin = QPoint()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 6, 4)
        lay.setSpacing(8)

        for w in segment_widgets:
            w.setParent(self)
            lay.addWidget(w)

        lay.addStretch(1)

        trailing_align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
        for w in trailing_widgets or []:
            w.setParent(self)
            lay.addWidget(w, alignment=trailing_align)

        self._max_btn = QPushButton(self)
        self._max_btn.setObjectName("stickon_maximize")
        self._max_btn.setFixedSize(28, 28)
        self._max_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._max_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._max_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._max_btn.clicked.connect(self._on_maximize_clicked)
        self._refresh_maximize_icon(False)

        close_btn = QPushButton("×", self)
        close_btn.setObjectName("stickon_close")
        close_btn.setFixedSize(28, 28)
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        close_btn.clicked.connect(host_window.close)
        lay.addWidget(self._max_btn, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        self.setMinimumHeight(0)
        self.setMaximumHeight(34)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setStyleSheet(
            f"DraggableTitleBar {{ background-color: {_BAR_BG}; border-bottom: 1px solid #c0c0c0; }}"
            "QPushButton#stickon_maximize { border: 1px solid white; border-radius: 4px; "
            "background: transparent; padding: 0; }"
            "QPushButton#stickon_maximize:hover { background-color: #d0d0d0; border: 1px solid white; }"
            "QPushButton#stickon_maximize:pressed { background-color: #b0b0b0; border: 1px solid white; }"
            "QPushButton#stickon_close { font-size: 18px; border: 1px solid white; border-radius: 4px; "
            "background: transparent; padding: 0; color: #333; }"
            "QPushButton#stickon_close:hover { background-color: #d0d0d0; color: #000; border: 1px solid white; }"
            "QPushButton#stickon_close:pressed { background-color: #b0b0b0; border: 1px solid white; }"
        )

    def _on_maximize_clicked(self) -> None:
        fn = getattr(self._host, "_toggle_stickon_maximize", None)
        if callable(fn):
            fn()

    def set_maximized_visual(self, maximized: bool) -> None:
        """Update caption icon (maximize vs restore)."""
        self._refresh_maximize_icon(maximized)

    def _refresh_maximize_icon(self, maximized: bool) -> None:
        pix = (
            QStyle.StandardPixmap.SP_TitleBarNormalButton
            if maximized
            else QStyle.StandardPixmap.SP_TitleBarMaxButton
        )
        self._max_btn.setIcon(self._host.style().standardIcon(pix))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if getattr(self._host, "_stickon_maximized", False):
                super().mousePressEvent(event)
                return
            ws = getattr(self._host, "_win_state", None)
            if ws is not None and ws.lock_position:
                super().mousePressEvent(event)
                return
            self._dragging = True
            self._press_global = event.globalPosition().toPoint()
            self._win_origin = self._host.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_global
            self._host.move(self._win_origin + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        event.accept()
