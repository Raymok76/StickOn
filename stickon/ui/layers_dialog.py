from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QKeyEvent, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from stickon.scene.items.image_item import ImageNodeItem
from stickon.scene.items.note_item import NoteNodeItem
from stickon.scene.items.draw_item import DrawNodeItem
from stickon.ui.canvas_view import CanvasView


@dataclass
class _LayerRow:
    key: str
    label: str
    thumb: QPixmap
    items: list[ImageNodeItem | NoteNodeItem | DrawNodeItem]
    z_top: float


class LayersDialog(QDialog):
    """Reorder visual layers (top row = front-most layer)."""

    _ITEM_ICON_SIZE = QSize(56, 56)
    _Z_STEP = 1.0

    def __init__(self, canvas: CanvasView, parent=None) -> None:
        super().__init__(parent)
        self._canvas = canvas
        self._rows_by_key: dict[str, _LayerRow] = {}
        self.setWindowTitle("Layers")
        self.resize(360, 520)

        self._list = QListWidget(self)
        self._list.setIconSize(self._ITEM_ICON_SIZE)
        self._list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setDragEnabled(True)
        self._list.setAcceptDrops(True)
        self._list.setDropIndicatorShown(True)
        self._list.model().rowsMoved.connect(self._on_rows_moved)

        top_hint = QLabel("Top layer")
        top_hint.setStyleSheet("color: #cccccc;")
        bottom_hint = QLabel("Bottom layer")
        bottom_hint.setStyleSheet("color: #8f8f8f;")

        self._rename_btn = QPushButton("Rename Draw Layer", self)
        self._rename_btn.clicked.connect(self._rename_selected_draw_layer)
        self._rename_btn.setEnabled(False)
        self._list.currentItemChanged.connect(lambda *_: self._sync_rename_button_state())

        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._rename_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(top_hint)
        layout.addWidget(self._list, stretch=1)
        layout.addWidget(bottom_hint)
        layout.addLayout(btn_row)

        self._reload_from_scene()

    def _layers_by_display_order(self) -> list[_LayerRow]:
        scene = self._canvas.graphics_scene()
        item_rows: list[_LayerRow] = []
        draw_groups: dict[str, list[DrawNodeItem]] = {}
        for it in scene.items():
            if it.parentItem() is not None:
                continue
            if isinstance(it, (ImageNodeItem, NoteNodeItem)):
                item_rows.append(
                    _LayerRow(
                        key=f"item:{it.node_id}",
                        label="",
                        thumb=QPixmap(),
                        items=[it],
                        z_top=float(it.zValue()),
                    )
                )
            elif isinstance(it, DrawNodeItem):
                gid = it.draw_layer_id or f"legacy:{it.node_id}"
                draw_groups.setdefault(gid, []).append(it)

        draw_rows: list[_LayerRow] = []
        for gid, members in draw_groups.items():
            members.sort(key=lambda x: (x.zValue(), x.node_id))
            z_top = max(float(x.zValue()) for x in members)
            draw_rows.append(
                _LayerRow(
                    key=f"draw:{gid}",
                    label=self._display_name_draw_layer(gid, members),
                    thumb=self._thumbnail_for_draw_layer(members),
                    items=members,
                    z_top=z_top,
                )
            )

        rows = item_rows + draw_rows
        rows.sort(key=lambda row: (row.z_top, row.key), reverse=True)
        return rows

    def _reload_from_scene(self) -> None:
        self._list.clear()
        rows = self._layers_by_display_order()
        self._rows_by_key = {row.key: row for row in rows}
        for idx, row in enumerate(rows, start=1):
            if len(row.items) == 1 and isinstance(row.items[0], (ImageNodeItem, NoteNodeItem)):
                name = self._display_name(row.items[0], idx)
                thumb = self._thumbnail_for(row.items[0])
            else:
                name = row.label
                thumb = row.thumb
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, row.key)
            item.setSizeHint(QSize(300, 62))
            self._list.addItem(item)
            self._list.setItemWidget(item, self._build_row_widget(name, thumb, row.key))
        self._sync_rename_button_state()

    def _build_row_widget(self, name: str, thumb: QPixmap, key: str) -> QWidget:
        row = QWidget(self._list)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(8)

        icon_lbl = QLabel(row)
        icon_lbl.setFixedSize(self._ITEM_ICON_SIZE)
        icon_lbl.setPixmap(thumb)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon_lbl)

        text_lbl = QLabel(name, row)
        text_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        text_lbl.setWordWrap(False)
        text_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(text_lbl, stretch=1)

        del_btn = QPushButton(row)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setToolTip("Delete this layer")
        del_btn.setFixedSize(26, 26)
        trash_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        if not trash_icon.isNull():
            del_btn.setIcon(trash_icon)
        else:
            del_btn.setText("🗑")
        del_btn.clicked.connect(lambda _checked=False, k=key: self._delete_layer_by_key(k))
        lay.addWidget(del_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        return row

    def _display_name(self, layer: ImageNodeItem | NoteNodeItem, ordinal: int) -> str:
        if isinstance(layer, NoteNodeItem):
            text = layer.toPlainText().strip().replace("\n", " ")
            if text:
                return f"Text: {text[:32]}"
            return f"Text {ordinal}"
        img = layer
        if img.source_path:
            stem = Path(img.source_path).name
            if stem:
                return stem
        return f"Image {ordinal}"

    def _thumbnail_for(self, layer: ImageNodeItem | NoteNodeItem) -> QPixmap:
        if isinstance(layer, NoteNodeItem):
            return self._note_thumbnail_for(layer)
        img = layer
        pm = img.pixmap()
        if pm.isNull():
            out = QPixmap(self._ITEM_ICON_SIZE)
            out.fill(Qt.GlobalColor.lightGray)
            return out
        return pm.scaled(
            self._ITEM_ICON_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _display_name_draw_layer(self, layer_id: str, members: list[DrawNodeItem]) -> str:
        custom_name = next((x.draw_layer_name for x in members if x.draw_layer_name), None)
        if custom_name:
            base = custom_name
        else:
            suffix = layer_id
            if layer_id.startswith("draw-layer-"):
                suffix = layer_id.removeprefix("draw-layer-")
            if layer_id.startswith("legacy:"):
                suffix = "legacy"
            base = f"Draw Layer {suffix}"
        count = len(members)
        noun = "stroke" if count == 1 else "strokes"
        return f"{base} ({count} {noun})"

    def _thumbnail_for_draw_layer(self, members: list[DrawNodeItem]) -> QPixmap:
        out = QPixmap(self._ITEM_ICON_SIZE)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        card = out.rect().adjusted(4, 4, -4, -4)
        painter.setBrush(QColor(245, 245, 245))
        painter.setPen(QPen(QColor(170, 170, 170), 1.0))
        painter.drawRoundedRect(card, 5, 5)
        pen_color = QColor(255, 60, 60)
        if members:
            pen_color = members[0].pen().color()
        pen = QPen(pen_color, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        y_mid = card.center().y()
        painter.drawLine(card.left() + 7, y_mid + 5, card.left() + 16, y_mid - 3)
        painter.drawLine(card.left() + 16, y_mid - 3, card.left() + 27, y_mid + 2)
        painter.drawLine(card.left() + 27, y_mid + 2, card.right() - 8, y_mid - 6)
        painter.setPen(QColor(70, 70, 70))
        f = QFont()
        f.setPointSize(8)
        painter.setFont(f)
        painter.drawText(card.adjusted(5, 3, -5, -3), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, f"{len(members)}")
        painter.end()
        return out

    def _note_thumbnail_for(self, note: NoteNodeItem) -> QPixmap:
        out = QPixmap(self._ITEM_ICON_SIZE)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        card = out.rect().adjusted(4, 4, -4, -4)
        painter.setBrush(note.bg_color)
        if note.border_width > 0:
            painter.setPen(QPen(note.border_color, 1.2))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(card, 5, 5)
        painter.setPen(QColor(40, 40, 40))
        f = QFont()
        f.setPointSize(9)
        painter.setFont(f)
        text = note.toPlainText().strip().replace("\n", " ")
        if not text:
            text = "Text"
        painter.drawText(card.adjusted(5, 5, -5, -5), Qt.AlignmentFlag.AlignLeft, text[:20])
        painter.end()
        return out

    def _on_rows_moved(self, *_args: object) -> None:
        self._apply_list_order_to_scene()

    def _sync_rename_button_state(self) -> None:
        cur = self._list.currentItem()
        key = cur.data(Qt.ItemDataRole.UserRole) if cur is not None else None
        self._rename_btn.setEnabled(isinstance(key, str) and key.startswith("draw:"))

    def _rename_selected_draw_layer(self) -> None:
        cur = self._list.currentItem()
        if cur is None:
            return
        key = cur.data(Qt.ItemDataRole.UserRole)
        if not isinstance(key, str) or not key.startswith("draw:"):
            return
        row = self._rows_by_key.get(key)
        if row is None:
            return
        current_name = next((x.draw_layer_name for x in row.items if x.draw_layer_name), None)
        if not current_name:
            current_name = row.label.rsplit(" (", 1)[0]
        new_name, ok = QInputDialog.getText(self, "Rename Draw Layer", "Layer name:", text=current_name)
        if not ok:
            return
        stripped = new_name.strip()
        if not stripped:
            return
        before_names: dict[DrawNodeItem, str | None] = {}
        after_names: dict[DrawNodeItem, str | None] = {}
        changed = False
        for it in row.items:
            if not isinstance(it, DrawNodeItem):
                continue
            before = it.draw_layer_name
            before_names[it] = before
            after_names[it] = stripped
            if before != stripped:
                changed = True
            it.draw_layer_name = stripped
        if not changed:
            return
        self._emit_draw_layer_rename_history(before_names, after_names, stripped)
        self._reload_from_scene()
        # Restore selection after list rebuild.
        for idx in range(self._list.count()):
            item = self._list.item(idx)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                self._list.setCurrentRow(idx)
                break

    def _delete_layer_by_key(self, key: str) -> None:
        row = self._rows_by_key.get(key)
        if row is None:
            return
        scene = self._canvas.graphics_scene()
        members = [it for it in row.items if it.scene() is scene]
        if not members:
            return
        snapshot = [
            (
                it,
                QPointF(it.pos()),
                float(it.rotation()),
                float(it.zValue()),
                float(it.scale()),
                QPointF(it.transformOriginPoint()),
                QTransform(it.transform()),
            )
            for it in members
        ]
        for it, *_ in snapshot:
            if isinstance(it, NoteNodeItem):
                it.finalize_text_edit_visual()
            if it.scene() is scene:
                scene.removeItem(it)
        self._emit_delete_layer_history(snapshot, row.label)
        self._canvas.viewport().update()
        self._reload_from_scene()

    def _apply_list_order_to_scene(self) -> None:
        rows = self._layers_by_display_order()
        if not rows:
            return
        by_key = {row.key: row for row in rows}
        order_top_to_bottom: list[str] = []
        for i in range(self._list.count()):
            key = self._list.item(i).data(Qt.ItemDataRole.UserRole)
            if isinstance(key, str):
                order_top_to_bottom.append(key)
        if not order_top_to_bottom:
            return
        all_items = [it for row in rows for it in row.items]
        if not all_items:
            return
        before_z: dict[ImageNodeItem | NoteNodeItem | DrawNodeItem, float] = {
            it: float(it.zValue()) for it in all_items
        }
        base_z = min(float(it.zValue()) for it in all_items)
        z_cursor = 0
        for key in reversed(order_top_to_bottom):
            row = by_key.get(key)
            if row is None:
                continue
            members = sorted(row.items, key=lambda x: (x.zValue(), x.node_id))
            for it in members:
                it.setZValue(base_z + z_cursor * self._Z_STEP)
                z_cursor += 1
        after_z: dict[ImageNodeItem | NoteNodeItem | DrawNodeItem, float] = {
            it: float(it.zValue()) for it in all_items
        }
        if any(abs(before_z[it] - after_z[it]) > 1e-6 for it in all_items):
            self._emit_reorder_history(before_z, after_z)
        self._canvas.viewport().update()

    def _emit_reorder_history(
        self,
        before_z: dict[ImageNodeItem | NoteNodeItem | DrawNodeItem, float],
        after_z: dict[ImageNodeItem | NoteNodeItem | DrawNodeItem, float],
    ) -> None:
        host = self.parent()
        fn = getattr(host, "_on_layers_dialog_reorder_committed", None)
        if callable(fn):
            fn(before_z, after_z)

    def _emit_draw_layer_rename_history(
        self,
        before_names: dict[DrawNodeItem, str | None],
        after_names: dict[DrawNodeItem, str | None],
        layer_name: str,
    ) -> None:
        host = self.parent()
        fn = getattr(host, "_on_layers_dialog_draw_layer_renamed", None)
        if callable(fn):
            fn(before_names, after_names, layer_name)

    def _emit_delete_layer_history(self, snapshot: list[tuple], label: str) -> None:
        host = self.parent()
        fn = getattr(host, "_on_layers_dialog_delete_layer_committed", None)
        if callable(fn):
            fn(snapshot, label)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and event.key() == Qt.Key_Z:
            host = self.parent()
            exec_fn = getattr(host, "_execute", None)
            if callable(exec_fn):
                exec_fn("edit.redo" if shift else "edit.undo")
                self._reload_from_scene()
                event.accept()
                return
        if ctrl and not shift and event.key() == Qt.Key_Y:
            host = self.parent()
            exec_fn = getattr(host, "_execute", None)
            if callable(exec_fn):
                exec_fn("edit.redo")
                self._reload_from_scene()
                event.accept()
                return
        super().keyPressEvent(event)
