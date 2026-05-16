from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QStyle, QStyleOptionGraphicsItem

from stickon.scene.items.draw_item import DrawNodeItem
from stickon.scene.items.image_item import ImageNodeItem
from stickon.scene.items.note_item import NoteNodeItem


_EXPORTABLE_PAINT_TYPES = (ImageNodeItem, NoteNodeItem, DrawNodeItem)


def _collect_items_for_selection_export(
    scene: QGraphicsScene,
    roots: Sequence[QGraphicsItem],
) -> list[QGraphicsItem]:
    selected_set = set(roots)
    for root in roots:
        stack = list(root.childItems())
        while stack:
            child = stack.pop()
            selected_set.add(child)
            stack.extend(child.childItems())
    ordered_bottom_first = reversed(list(scene.items()))
    return [
        it
        for it in ordered_bottom_first
        if it in selected_set and isinstance(it, _EXPORTABLE_PAINT_TYPES)
    ]


def _render_selected_items_non_mutating(
    target: QRectF,
    ordered_items: Sequence[QGraphicsItem],
    *,
    opaque_background: bool,
) -> QImage:
    w = max(1, int(target.width()))
    h = max(1, int(target.height()))
    if opaque_background:
        image = QImage(w, h, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
    else:
        image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.translate(-target.left(), -target.top())
    opt = QStyleOptionGraphicsItem()
    opt.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Active
    opt.exposedRect = QRectF(target)
    for it in ordered_items:
        painter.save()
        painter.setWorldTransform(it.sceneTransform(), combine=True)
        opt.rect = it.boundingRect().toAlignedRect()
        it.paint(painter, opt, None)
        painter.restore()
    painter.end()
    return image


class ExportService:
    @staticmethod
    def _format_from_suffix(suffix: str) -> tuple[str, bool]:
        """Return (Qt save format name, use opaque RGB background)."""
        s = suffix.lower()
        if s in (".jpg", ".jpeg"):
            return "JPEG", True
        if s == ".bmp":
            return "BMP", True
        return "PNG", False

    @staticmethod
    def _render_scene_rect(scene: QGraphicsScene, target: QRectF, *, opaque_background: bool) -> QImage:
        w = max(1, int(target.width()))
        h = max(1, int(target.height()))
        if opaque_background:
            image = QImage(w, h, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
        else:
            image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        scene.render(p, target=QRectF(0, 0, w, h), source=target)
        p.end()
        return image

    @staticmethod
    def export_scene(scene: QGraphicsScene, path: str | Path, rect: QRectF | None = None) -> None:
        path = Path(path)
        fmt, opaque = ExportService._format_from_suffix(path.suffix)
        target = rect or scene.itemsBoundingRect().adjusted(-10, -10, 10, 10)
        image = ExportService._render_scene_rect(scene, target, opaque_background=opaque)
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "JPEG":
            image.save(str(path), "JPEG", 92)
        else:
            image.save(str(path), fmt)

    @staticmethod
    def export_item_selection(scene: QGraphicsScene, path: str | Path, items: Sequence[QGraphicsItem]) -> None:
        """Rasterize only ``items`` (and ancestor chains) into one image, cropped to their scene bounds."""
        path = Path(path)
        roots = [it for it in items if it.scene() is scene]
        if not roots:
            return
        ordered = _collect_items_for_selection_export(scene, roots)
        if not ordered:
            return
        united = ordered[0].sceneBoundingRect()
        for it in ordered[1:]:
            united = united.united(it.sceneBoundingRect())
        target = united.adjusted(-10, -10, 10, 10)
        fmt, opaque = ExportService._format_from_suffix(path.suffix)
        image = _render_selected_items_non_mutating(
            target,
            ordered,
            opaque_background=opaque,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "JPEG":
            image.save(str(path), "JPEG", 92)
        else:
            image.save(str(path), fmt)
