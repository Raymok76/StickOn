from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from stickon.ui.canvas_view import CanvasView


class DrawColorPickerDialog(QDialog):
    """Pick drawing stroke color with optional reset to default."""

    def __init__(self, current: QColor, default_color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Drawing color")
        self._chosen = QColor(current)
        self._default_color = QColor(default_color)

        self._swatch = QLabel(self)
        self._swatch.setMinimumSize(160, 44)
        self._sync_swatch()

        pick_btn = QPushButton("Pick color…", self)
        pick_btn.clicked.connect(self._pick)

        reset_btn = QPushButton("Reset to default", self)
        reset_btn.clicked.connect(self._reset)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

        col = QVBoxLayout(self)
        col.addWidget(self._swatch)
        col.addWidget(pick_btn)
        col.addWidget(reset_btn)
        col.addWidget(bb)

    def _sync_swatch(self) -> None:
        self._swatch.setStyleSheet(
            "QLabel { background-color: %s; border: 2px solid #444; border-radius: 4px; }"
            % self._chosen.name(QColor.NameFormat.HexRgb)
        )

    def _pick(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        c = QColorDialog.getColor(self._chosen, self, "Drawing color")
        if c.isValid():
            self._chosen = c
            self._sync_swatch()

    def _reset(self) -> None:
        self._chosen = QColor(self._default_color)
        self._sync_swatch()

    def chosen_color(self) -> QColor:
        return QColor(self._chosen)


class PencilToolButton(QWidget):
    """Horizontal pencil stub; tip shows current ink color."""

    def __init__(
        self,
        ink_supplier: Callable[[], QColor],
        on_click: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ink_supplier = ink_supplier
        self._on_click = on_click
        self.setFixedSize(52, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        ink = self._ink_supplier()
        if not isinstance(ink, QColor):
            ink = QColor(255, 60, 60)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        body = QRectF(14.0, 11.0, 26.0, 8.0)
        p.setPen(QPen(QColor(70, 70, 70), 1.0))
        p.setBrush(QColor(235, 210, 175))
        p.drawRoundedRect(body, 2.0, 2.0)

        ferrule = QRectF(38.0, 11.5, 6.0, 7.0)
        p.setBrush(QColor(190, 190, 200))
        p.drawRoundedRect(ferrule, 1.0, 1.0)

        tip = QPolygonF(
            [
                QPointF(14.0, 11.0),
                QPointF(6.0, 15.0),
                QPointF(14.0, 19.0),
            ]
        )
        p.setPen(QPen(QColor(55, 55, 55), 1.0))
        p.setBrush(ink)
        p.drawPolygon(tip)


def _eraser_icon_pixmap(active: bool = False) -> QPixmap:
    pm = QPixmap(28, 28)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    body = QPolygonF(
        [
            QPointF(7.0, 16.0),
            QPointF(14.0, 7.0),
            QPointF(22.0, 15.0),
            QPointF(15.0, 23.0),
        ]
    )
    body_color = QColor(248, 128, 163) if active else QColor(255, 176, 204)
    p.setBrush(body_color)
    p.setPen(QPen(QColor(95, 95, 95), 1.2))
    p.drawPolygon(body)

    tip = QPolygonF(
        [
            QPointF(15.0, 23.0),
            QPointF(22.0, 15.0),
            QPointF(25.0, 18.0),
            QPointF(18.0, 25.0),
        ]
    )
    p.setBrush(QColor(245, 245, 245))
    p.drawPolygon(tip)

    p.setPen(QPen(QColor(255, 255, 255), 1.6))
    p.drawLine(10, 18, 18, 10)
    p.drawLine(13, 21, 21, 13)
    p.end()
    return pm


def _exit_icon_pixmap() -> QPixmap:
    pm = QPixmap(22, 22)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(120, 30, 30), 1.2))
    p.setBrush(QColor(255, 236, 236))
    p.drawEllipse(QRectF(1.0, 1.0, 20.0, 20.0))
    p.setPen(QPen(QColor(176, 40, 40), 2.0))
    p.drawLine(7, 7, 15, 15)
    p.drawLine(15, 7, 7, 15)
    p.end()
    return pm


class DrawModeToolbar(QFrame):
    """Bottom-right toolbar visible while canvas draw mode is on."""

    def __init__(self, canvas: CanvasView, parent: QWidget) -> None:
        super().__init__(parent)
        self._canvas = canvas
        self.setObjectName("drawModeToolbar")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(
            "#drawModeToolbar { background: #ffffff; border: 1px solid #555555; border-radius: 6px; }"
        )
        self._dragging = False
        self._drag_click_offset = QPointF()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)

        self._drag_handle = QLabel("::", self)
        self._drag_handle.setFixedSize(26, 30)
        self._drag_handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drag_handle.setCursor(Qt.CursorShape.SizeAllCursor)
        self._drag_handle.setToolTip("Drag toolbar")
        self._drag_handle.setStyleSheet(
            "QLabel { color: #444444; font-weight: 800; font-size: 16px; background: #ebebeb; "
            "border: 1px solid #8a8a8a; border-radius: 5px; }"
        )
        self._drag_handle.installEventFilter(self)
        lay.addWidget(self._drag_handle)

        sep0 = QFrame(self)
        sep0.setFrameShape(QFrame.Shape.VLine)
        sep0.setFrameShadow(QFrame.Shadow.Sunken)
        lay.addWidget(sep0)

        self._pencil_widget = PencilToolButton(lambda: canvas.draw_pen_color, self._open_color_dialog, self)
        lay.addWidget(self._pencil_widget)

        sep1 = QFrame(self)
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        lay.addWidget(sep1)

        self._grp_width = QButtonGroup(self)
        self._grp_width.setExclusive(True)
        self._btn_thin = QPushButton("Thin", self)
        self._btn_thick = QPushButton("Thick", self)
        self._btn_thicker = QPushButton("Thicker", self)
        for b in (self._btn_thin, self._btn_thick, self._btn_thicker):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCheckable(True)
            b.setFixedHeight(26)
            self._grp_width.addButton(b)
            lay.addWidget(b)

        self._btn_thin.setChecked(True)
        self._btn_thin.clicked.connect(lambda: canvas.set_draw_width_preset(0))
        self._btn_thick.clicked.connect(lambda: canvas.set_draw_width_preset(1))
        self._btn_thicker.clicked.connect(lambda: canvas.set_draw_width_preset(2))

        sep2 = QFrame(self)
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        lay.addWidget(sep2)

        self._btn_eraser = QPushButton(self)
        self._btn_eraser.setFixedSize(116, 30)
        self._btn_eraser.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_eraser.setCheckable(True)
        self._btn_eraser.setFlat(False)
        self._btn_eraser.setStyleSheet(
            "QPushButton { padding: 0 10px; border: 1px solid #7a7a7a; border-radius: 5px; "
            "background: #f4f4f4; color: #232323; font-weight: 600; text-align: left; } "
            "QPushButton:checked { background: #c62828; border-color: #9a1f1f; color: #ffffff; }"
        )
        self._btn_eraser.setIcon(QIcon(_eraser_icon_pixmap(False)))
        self._btn_eraser.setIconSize(QSize(24, 24))
        self._btn_eraser.setText("Eraser OFF")
        self._btn_eraser.setToolTip("Eraser OFF - draw adds strokes")
        self._btn_eraser.clicked.connect(self._toggle_eraser)
        lay.addWidget(self._btn_eraser)

        sep3 = QFrame(self)
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setFrameShadow(QFrame.Shadow.Sunken)
        lay.addWidget(sep3)

        clear_btn = QPushButton("Clear all", self)
        clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        clear_btn.setToolTip("Remove every markup stroke")
        clear_btn.clicked.connect(self._clear_all)
        lay.addWidget(clear_btn)

        sep4 = QFrame(self)
        sep4.setFrameShape(QFrame.Shape.VLine)
        sep4.setFrameShadow(QFrame.Shadow.Sunken)
        lay.addWidget(sep4)

        self._btn_exit_draw = QPushButton(self)
        self._btn_exit_draw.setFixedSize(32, 30)
        self._btn_exit_draw.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_exit_draw.setIcon(QIcon(_exit_icon_pixmap()))
        self._btn_exit_draw.setIconSize(QSize(18, 18))
        self._btn_exit_draw.setToolTip("Exit draw mode")
        self._btn_exit_draw.clicked.connect(self._exit_draw_mode)
        lay.addWidget(self._btn_exit_draw)

        self.sync_from_canvas()

    def sync_from_canvas(self) -> None:
        c = self._canvas
        self._btn_thin.blockSignals(True)
        self._btn_thick.blockSignals(True)
        self._btn_thicker.blockSignals(True)
        self._btn_thin.setChecked(c.draw_width_preset == 0)
        self._btn_thick.setChecked(c.draw_width_preset == 1)
        self._btn_thicker.setChecked(c.draw_width_preset == 2)
        self._btn_thin.blockSignals(False)
        self._btn_thick.blockSignals(False)
        self._btn_thicker.blockSignals(False)

        self._btn_eraser.blockSignals(True)
        self._btn_eraser.setChecked(c.draw_eraser_active)
        self._btn_eraser.blockSignals(False)
        self._refresh_eraser_button()
        self._pencil_widget.update()

    def _open_color_dialog(self) -> None:
        dlg = DrawColorPickerDialog(
            self._canvas.draw_pen_color,
            self._canvas.draw_pen_default_color,
            self.window(),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._canvas.draw_pen_color = dlg.chosen_color()
        self._pencil_widget.update()

    def _toggle_eraser(self) -> None:
        self._canvas.draw_eraser_active = self._btn_eraser.isChecked()
        self._refresh_eraser_button()

    def _refresh_eraser_button(self) -> None:
        is_on = self._btn_eraser.isChecked()
        self._btn_eraser.setText("Eraser ON" if is_on else "Eraser OFF")
        self._btn_eraser.setIcon(QIcon(_eraser_icon_pixmap(is_on)))
        self._btn_eraser.setToolTip(
            "Eraser ON - drag to remove strokes"
            if is_on
            else "Eraser OFF - draw adds strokes"
        )

    def _clear_all(self) -> None:
        r = QMessageBox.question(
            self.window(),
            "Clear drawings",
            "Remove all markup strokes from the canvas?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        self._canvas.clear_all_draw_items()

    def _exit_draw_mode(self) -> None:
        self._canvas.exit_draw_mode()

    def eventFilter(self, watched, event: QEvent) -> bool:
        if watched is self._drag_handle and isinstance(event, QMouseEvent):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._dragging = True
                toolbar_top_left_global = self.mapToGlobal(self.rect().topLeft())
                self._drag_click_offset = event.globalPosition() - QPointF(toolbar_top_left_global)
                event.accept()
                return True
            if (
                event.type() == QEvent.Type.MouseMove
                and self._dragging
                and (event.buttons() & Qt.MouseButton.LeftButton)
            ):
                parent = self.parentWidget()
                if parent is not None:
                    new_top_left_global = event.globalPosition() - self._drag_click_offset
                    parent_pos = parent.mapFromGlobal(new_top_left_global.toPoint())
                    self._canvas.set_draw_toolbar_position(parent_pos)
                event.accept()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._dragging = False
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_handle.geometry().contains(
            event.position().toPoint()
        ):
            self._dragging = True
            self._drag_click_offset = event.position()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            local_top_left = event.position() - self._drag_click_offset
            self._canvas.set_draw_toolbar_position(local_top_left.toPoint() + self.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)
