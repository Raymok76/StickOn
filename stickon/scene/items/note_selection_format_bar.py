from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QDialog, QColorDialog, QHBoxLayout, QPushButton, QGraphicsView, QWidget

if TYPE_CHECKING:
    from stickon.scene.items.note_item import NoteNodeItem


def _selection_rect_in_document(cur: QTextCursor) -> QRectF:
    """Bounding rect of the selection in document/layout coordinates.

    QTextCursor.selectionBoundingRect exists in Qt C++ but is not bound in some
    PySide6 versions; approximate by unioning QAbstractTextDocumentLayout
    rectangles for blocks that intersect the selection.
    """
    if not cur.hasSelection():
        return QRectF()
    doc = cur.document()
    layout = doc.documentLayout()
    if layout is None:
        return QRectF()
    lo = min(cur.anchor(), cur.position())
    hi = max(cur.anchor(), cur.position())
    out = QRectF()
    block = doc.findBlock(lo)
    while block.isValid() and block.position() < hi:
        out |= layout.blockBoundingRect(block)
        block = block.next()
    return out


def _viewport_pos_for_doc_point(view: QGraphicsView, note, doc_pt: QPointF) -> QPoint:
    """Map a point in note document space to viewport-local widget coordinates."""
    scene_pt = note.mapToScene(doc_pt)
    on_view = view.mapFromScene(scene_pt)
    return view.viewport().mapFrom(view, on_view)


class NoteTextSelectionBar(QWidget):
    """One-row bar above the current text selection inside a sticky note.

    Parented to the canvas viewport so it stacks above the scene and stays inside
    an always-on-top main window (a separate Qt.Popup would render behind it).
    """

    def __init__(self, note: NoteNodeItem, parent: QWidget) -> None:
        super().__init__(parent)
        self._note = note
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName("noteTextSelBar")
        self.setStyleSheet(
            "#noteTextSelBar { background: #ffffff; border: 1px solid #333333; border-radius: 5px; }"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)

        self._bold = QPushButton("B", self)
        self._italic = QPushButton("I", self)
        self._under = QPushButton("U", self)
        for b in (self._bold, self._italic, self._under):
            b.setFixedSize(22, 22)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCheckable(True)
            f = QFont(b.font())
            f.setBold(b is self._bold)
            f.setItalic(b is self._italic)
            f.setUnderline(b is self._under)
            b.setFont(f)

        self._color = QPushButton(self)
        self._color.setFixedSize(22, 22)
        self._color.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._sync_color_swatch()

        lay.addWidget(self._bold)
        lay.addWidget(self._italic)
        lay.addWidget(self._under)
        lay.addWidget(self._color)

        self._bold.toggled.connect(lambda on: self._merge_weight(on))
        self._italic.toggled.connect(lambda on: self._merge_italic(on))
        self._under.toggled.connect(lambda on: self._merge_underline(on))
        self._color.clicked.connect(self._pick_color)

        self._repos_timer = QTimer(self)
        self._repos_timer.setInterval(90)
        self._repos_timer.timeout.connect(self.reposition_to_selection)

    def _sync_color_swatch(self) -> None:
        cur = self._note.textCursor()
        cf = cur.charFormat()
        if cf.foreground().style() != Qt.BrushStyle.NoBrush:
            c = cf.foreground().color()
        else:
            c = QColor(self._note.defaultTextColor())
        self._color.setStyleSheet(
            "QPushButton { background-color: %s; border: 1px solid #666; border-radius: 3px; }" % c.name(QColor.NameFormat.HexArgb)
        )

    def sync_checks_from_cursor(self) -> None:
        cur = self._note.textCursor()
        if not cur.hasSelection():
            return
        cf = cur.charFormat()
        self._bold.blockSignals(True)
        self._italic.blockSignals(True)
        self._under.blockSignals(True)
        self._bold.setChecked(cf.fontWeight() >= QFont.Weight.Bold)
        self._italic.setChecked(cf.fontItalic())
        self._under.setChecked(cf.underlineStyle() != QTextCharFormat.UnderlineStyle.NoUnderline)
        self._bold.blockSignals(False)
        self._italic.blockSignals(False)
        self._under.blockSignals(False)
        self._sync_color_swatch()

    def reposition_to_selection(self) -> None:
        if not self.isVisible():
            return
        cur = self._note.textCursor()
        if not cur.hasSelection():
            return

        sr = _selection_rect_in_document(cur)
        if sr.isEmpty():
            return
        top_center_doc = QPointF(sr.center().x(), sr.top())

        scene = self._note.scene()
        if scene is None:
            return
        views = scene.views()
        if not views:
            return
        view = views[0]
        vp = view.viewport()
        if self.parent() is not vp:
            return
        local = _viewport_pos_for_doc_point(view, self._note, top_center_doc)

        self.adjustSize()
        x = local.x() - self.width() // 2
        y = local.y() - self.height() - 6

        margin = 4
        vr = vp.rect().adjusted(margin, margin, -margin, -margin)
        if vr.isValid():
            x = max(vr.left(), min(x, vr.right() - self.width()))
            y = max(vr.top(), min(y, vr.bottom() - self.height()))

        self.move(QPoint(int(x), int(y)))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.reposition_to_selection()
        self._repos_timer.start()

    def hideEvent(self, event) -> None:
        self._repos_timer.stop()
        super().hideEvent(event)

    def _merge_selected(self, fmt: QTextCharFormat) -> None:
        cur = self._note.textCursor()
        if not cur.hasSelection():
            return
        cur.mergeCharFormat(fmt)
        self._note.setTextCursor(cur)

    def _merge_weight(self, bold: bool) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        self._merge_selected(fmt)

    def _merge_italic(self, italic: bool) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(italic)
        self._merge_selected(fmt)

    def _merge_underline(self, on: bool) -> None:
        fmt = QTextCharFormat()
        fmt.setUnderlineStyle(
            QTextCharFormat.UnderlineStyle.SingleUnderline if on else QTextCharFormat.UnderlineStyle.NoUnderline
        )
        self._merge_selected(fmt)

    def _pick_color(self) -> None:
        cur = self._note.textCursor()
        if not cur.hasSelection():
            return
        cf = cur.charFormat()
        if cf.foreground().style() != Qt.BrushStyle.NoBrush:
            start = QColor(cf.foreground().color())
        else:
            start = QColor(self._note.defaultTextColor())
        views = self._note.scene().views() if self._note.scene() else []
        dlg_parent = views[0] if views else None
        dlg = QColorDialog(start, dlg_parent)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        c = dlg.selectedColor()
        if not c.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setForeground(QBrush(c))
        self._merge_selected(fmt)
        self._sync_color_swatch()
