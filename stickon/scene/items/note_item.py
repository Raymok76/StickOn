from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QBrush,
    QPen,
    QPainterPath,
    QFont,
    QTextOption,
    QTextCursor,
    QTextCharFormat,
    QFontInfo,
    QKeyEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsSceneMouseEvent,
    QGraphicsTextItem,
    QWidget,
    QStyleOptionGraphicsItem,
)

from stickon.scene.items.image_item import new_node_id
from stickon.scene.items.note_selection_format_bar import NoteTextSelectionBar


@dataclass
class NoteAppearance:
    """Serializable styling for sticky notes (text + frame)."""

    font_family: str
    font_point_size: int
    text_color: QColor
    bg_color: QColor
    border_width: float
    border_color: QColor
    bold: bool
    italic: bool
    underline: bool
    strike_out: bool

    @classmethod
    def builtin(cls) -> NoteAppearance:
        base_font = QFont()
        return cls(
            font_family=base_font.family(),
            font_point_size=32,
            text_color=QColor(30, 30, 30),
            bg_color=QColor(255, 255, 200, 220),
            border_width=1.0,
            border_color=QColor(100, 100, 100),
            bold=False,
            italic=False,
            underline=False,
            strike_out=False,
        )

    @classmethod
    def from_note(cls, it: NoteNodeItem) -> NoteAppearance:
        f = it.font()
        pts = QFontInfo(f).pointSize()
        if pts <= 0:
            pts = max(8, int(round(it.font().pixelSize() * 72.0 / 96.0)) if it.font().pixelSize() > 0 else 32)
        c = QTextCursor(it.document())
        c.movePosition(QTextCursor.MoveOperation.Start)
        cf = c.charFormat()
        bold = f.bold() or cf.fontWeight() >= QFont.Weight.Bold
        italic = cf.fontItalic() or f.italic()
        underline = cf.underlineStyle() != QTextCharFormat.UnderlineStyle.NoUnderline
        strike_out = cf.fontStrikeOut()
        tc = cf.foreground().color() if cf.foreground().style() != Qt.BrushStyle.NoBrush else it.defaultTextColor()
        return cls(
            font_family=f.family(),
            font_point_size=max(6, min(120, pts)),
            text_color=QColor(tc),
            bg_color=QColor(it.bg_color),
            border_width=float(it.border_width),
            border_color=QColor(it.border_color),
            bold=bold,
            italic=italic,
            underline=underline,
            strike_out=strike_out,
        )

    def apply_to(self, it: NoteNodeItem) -> None:
        it.bg_color = QColor(self.bg_color)
        it.border_width = max(0.0, float(self.border_width))
        it.border_color = QColor(self.border_color)
        f = QFont(self.font_family, self.font_point_size)
        f.setBold(self.bold)
        f.setItalic(self.italic)
        it.setFont(f)
        it.setDefaultTextColor(QColor(self.text_color))

        fmt = QTextCharFormat()
        fmt.setFontFamily(self.font_family)
        fmt.setFontPointSize(self.font_point_size)
        fmt.setFontWeight(QFont.Weight.Bold if self.bold else QFont.Weight.Normal)
        fmt.setFontItalic(self.italic)
        fmt.setUnderlineStyle(
            QTextCharFormat.UnderlineStyle.SingleUnderline
            if self.underline
            else QTextCharFormat.UnderlineStyle.NoUnderline
        )
        fmt.setFontStrikeOut(self.strike_out)
        fmt.setForeground(QBrush(self.text_color))

        doc = it.document()
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.mergeCharFormat(fmt)

    def to_json_dict(self) -> dict:
        def rgba(c: QColor) -> list[int]:
            return [c.red(), c.green(), c.blue(), c.alpha()]

        return {
            "font_family": self.font_family,
            "font_point_size": int(self.font_point_size),
            "text_color": rgba(self.text_color),
            "bg_color": rgba(self.bg_color),
            "border_width": float(self.border_width),
            "border_color": rgba(self.border_color),
            "bold": self.bold,
            "italic": self.italic,
            "underline": self.underline,
            "strike_out": self.strike_out,
        }

    @classmethod
    def from_json_dict(cls, d: object) -> NoteAppearance | None:
        if not isinstance(d, dict):
            return None

        def qc(x: object, fallback: QColor) -> QColor:
            if isinstance(x, list) and len(x) >= 3:
                a = int(x[3]) if len(x) > 3 else 255
                return QColor(int(x[0]), int(x[1]), int(x[2]), a)
            return QColor(fallback)

        b = cls.builtin()
        try:
            ff = str(d.get("font_family", b.font_family))
            fps = int(d.get("font_point_size", b.font_point_size))
            bw = float(d.get("border_width", b.border_width))
            return cls(
                font_family=ff,
                font_point_size=max(6, min(120, fps)),
                text_color=qc(d.get("text_color"), b.text_color),
                bg_color=qc(d.get("bg_color"), b.bg_color),
                border_width=max(0.0, min(48.0, bw)),
                border_color=qc(d.get("border_color"), b.border_color),
                bold=bool(d.get("bold", False)),
                italic=bool(d.get("italic", False)),
                underline=bool(d.get("underline", False)),
                strike_out=bool(d.get("strike_out", False)),
            )
        except (TypeError, ValueError):
            return None


class NoteNodeItem(QGraphicsTextItem):
    _SELECT_PAD_SCENE_UNITS = 14.0

    def __init__(self, text: str = "Note", node_id: str | None = None) -> None:
        super().__init__(text)
        self.node_id = node_id or new_node_id()
        self._text_selection_bar: NoteTextSelectionBar | None = None
        self._text_selection_cursor_sig: bool = False
        self.bg_color = QColor(255, 255, 200, 220)
        self.border_width = 1.0
        self.border_color = QColor(100, 100, 100)
        f = self.font()
        f.setPointSize(32)
        self.setFont(f)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        doc = self.document()
        doc.setDefaultTextOption(QTextOption(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop))
        self.setDefaultTextColor(QColor(30, 30, 30))

    def set_background_color(self, c: QColor) -> None:
        self.bg_color = c

    def core_bounds(self) -> QRectF:
        """Text layout bounds without selection padding (used for resize handles)."""
        return QGraphicsTextItem.boundingRect(self)

    def boundingRect(self) -> QRectF:
        br = self.core_bounds()
        if self.isSelected():
            m = self._SELECT_PAD_SCENE_UNITS
            return br.adjusted(-m, -m, m, m)
        return br

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self.boundingRect(), 4, 4)
        return path

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            self.prepareGeometryChange()
        return super().itemChange(change, value)

    def _ensure_text_selection_bar_signals(self) -> None:
        if self._text_selection_cursor_sig:
            return
        self.document().cursorPositionChanged.connect(self._on_text_document_cursor_changed)
        self._text_selection_cursor_sig = True

    def _teardown_text_selection_bar_signals(self) -> None:
        if not self._text_selection_cursor_sig:
            return
        try:
            self.document().cursorPositionChanged.disconnect(self._on_text_document_cursor_changed)
        except TypeError:
            pass
        self._text_selection_cursor_sig = False

    def _on_text_document_cursor_changed(self, _cursor: QTextCursor) -> None:
        self._sync_text_selection_bar()

    def _sync_text_selection_bar(self) -> None:
        if self.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction:
            self._hide_text_selection_bar()
            return
        cur = self.textCursor()
        if not cur.hasSelection():
            if self._text_selection_bar:
                self._text_selection_bar.hide()
            return
        bar = self._text_selection_bar_or_create()
        if bar is None:
            return
        bar.sync_checks_from_cursor()
        bar.show()
        bar.raise_()

    def _schedule_sync_text_selection_bar(self) -> None:
        if self.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction:
            return
        QTimer.singleShot(0, self._sync_text_selection_bar)

    def _text_selection_bar_or_create(self) -> NoteTextSelectionBar | None:
        scene = self.scene()
        if scene is None:
            return None
        views = scene.views()
        if not views:
            return None
        vp = views[0].viewport()
        if self._text_selection_bar is None or self._text_selection_bar.parent() is not vp:
            if self._text_selection_bar is not None:
                self._text_selection_bar.hide()
                self._text_selection_bar.deleteLater()
                self._text_selection_bar = None
            self._text_selection_bar = NoteTextSelectionBar(self, vp)
        return self._text_selection_bar

    def _hide_text_selection_bar(self) -> None:
        if self._text_selection_bar:
            self._text_selection_bar.hide()

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        if bool(event.buttons() & Qt.MouseButton.LeftButton):
            self._schedule_sync_text_selection_bar()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._schedule_sync_text_selection_bar()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        super().keyPressEvent(event)
        self._schedule_sync_text_selection_bar()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        super().keyReleaseEvent(event)
        self._schedule_sync_text_selection_bar()

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._ensure_text_selection_bar_signals()
            super().mouseDoubleClickEvent(event)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def finalize_text_edit_visual(self) -> None:
        """Leave edit mode and drop selection/caret visuals (safe to call repeatedly)."""
        self._hide_text_selection_bar()
        self._teardown_text_selection_bar_signals()
        cur = QTextCursor(self.document())
        cur.clearSelection()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cur)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.clearFocus()
        self.update()

    def focusOutEvent(self, event):
        if QApplication.activeModalWidget() is not None:
            super().focusOutEvent(event)
            return
        self.finalize_text_edit_visual()
        super().focusOutEvent(event)

    def paint(
        self,
        painter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.save()
        painter.setBrush(QBrush(self.bg_color))
        if self.border_width > 0:
            painter.setPen(QPen(self.border_color, float(self.border_width)))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        br_bg = self.core_bounds()
        painter.drawRoundedRect(br_bg, 4, 4)
        painter.restore()
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.save()
            pen = QPen(QColor(80, 160, 255), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            br = self.core_bounds()
            painter.drawRect(br)
            wt = painter.worldTransform()
            px_per = max(math.hypot(wt.m11(), wt.m12()), math.hypot(wt.m21(), wt.m22()), 1e-6)
            half = min(5.0 / px_per, self._SELECT_PAD_SCENE_UNITS * 0.45)
            handle_pen = QPen(QColor(80, 140, 230), 1.0)
            painter.setPen(handle_pen)
            painter.setBrush(QColor(250, 252, 255))
            for cx, cy in (
                (br.left(), br.top()),
                (br.right(), br.top()),
                (br.right(), br.bottom()),
                (br.left(), br.bottom()),
            ):
                painter.drawRect(cx - half, cy - half, 2 * half, 2 * half)
            painter.restore()
