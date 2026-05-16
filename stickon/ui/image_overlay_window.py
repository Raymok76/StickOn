from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent, QMovie, QPixmap
from PySide6.QtWidgets import QLabel, QWidget


class ImageOverlayWindow(QWidget):
    """Borderless always-on-top window showing one image (or GIF) with transparent margins."""

    def __init__(
        self,
        pixmap: QPixmap,
        gif_path: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)
        self._base_pixmap = pixmap
        self._gif_path = gif_path
        self._movie: QMovie | None = None
        self._drag_offset: QPoint | None = None

        is_gif = bool(
            gif_path
            and Path(gif_path).suffix.lower() == ".gif"
        )
        self._label = QLabel(self)
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._label.setAutoFillBackground(False)

        if is_gif:
            self._movie = QMovie(str(gif_path), parent=self)
            self._movie.setCacheMode(QMovie.CacheMode.CacheAll)
            self._label.setScaledContents(True)
            self._label.setMovie(self._movie)
            self._movie.start()
        else:
            self._refresh_static_pixmap()

    def _refresh_static_pixmap(self) -> None:
        if self._base_pixmap.isNull():
            return
        scaled = self._base_pixmap.scaled(
            self._label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._label.setGeometry(self.rect())
        if self._movie is not None:
            self._movie.setScaledSize(self._label.size())
        else:
            self._refresh_static_pixmap()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
        super().mouseReleaseEvent(event)
