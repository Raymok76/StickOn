from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QPointF, QRectF

from stickon.scene.items.image_item import ImageNodeItem

_PACK_SHRINK_MAX_ITERS = 48


class _PosItem(Protocol):
    def pos(self) -> QPointF: ...
    def setPos(self, pos: QPointF) -> None: ...
    def boundingRect(self) -> QRectF: ...


@dataclass
class LayoutService:
    spacing: float = 8.0

    @staticmethod
    def _scene_bounds(it: _PosItem) -> QRectF:
        """Axis-aligned scene rect for item's boundingRect() (handles padded bounds / rotation)."""
        br = it.boundingRect()
        mapper = getattr(it, "mapRectToScene", None)
        if callable(mapper):
            return mapper(br)
        return br.translated(it.pos())

    def align(self, items: list[_PosItem], direction: str) -> None:
        if len(items) < 2:
            return
        scene_rects = [self._scene_bounds(it) for it in items]
        union = scene_rects[0]
        for r in scene_rects[1:]:
            union = union.united(r)
        for it, sr in zip(items, scene_rects):
            if direction == "left":
                dx = union.left() - sr.left()
                it.setPos(it.pos() + QPointF(dx, 0))
            elif direction == "right":
                dx = union.right() - sr.right()
                it.setPos(it.pos() + QPointF(dx, 0))
            elif direction == "top":
                dy = union.top() - sr.top()
                it.setPos(it.pos() + QPointF(0, dy))
            elif direction == "bottom":
                dy = union.bottom() - sr.bottom()
                it.setPos(it.pos() + QPointF(0, dy))
            elif direction == "hcenter":
                dx = union.center().x() - sr.center().x()
                it.setPos(it.pos() + QPointF(dx, 0))
            elif direction == "vcenter":
                dy = union.center().y() - sr.center().y()
                it.setPos(it.pos() + QPointF(0, dy))

    def _pixmap_scene_rect(self, it: _PosItem) -> QRectF:
        if isinstance(it, ImageNodeItem):
            return it.mapRectToScene(it.pixmapBoundingRect())
        return self._scene_bounds(it)

    def _union_pixmap_rects(self, items: list[_PosItem]) -> QRectF:
        rects = [self._pixmap_scene_rect(it) for it in items]
        u = rects[0]
        for r in rects[1:]:
            u = u.united(r)
        return u

    def _pack_shelf_into(self, items: list[_PosItem], inner: QRectF) -> None:
        # Preserve left→right (then top→bottom) order; do not reorder by size (that swaps sides).
        ranked = sorted(
            items,
            key=lambda i: (
                self._pixmap_scene_rect(i).left(),
                self._pixmap_scene_rect(i).top(),
            ),
        )
        x = inner.left()
        y = inner.top()
        row_h = 0.0
        gap = self.spacing
        right_lim = inner.right()
        for it in ranked:
            pr = self._pixmap_scene_rect(it)
            w, h = pr.width(), pr.height()
            if x > inner.left() + 0.5 and x + w > right_lim + 0.5:
                x = inner.left()
                y += row_h + gap
                row_h = 0.0
            tl = QPointF(pr.left(), pr.top())
            target_tl = QPointF(x, y)
            it.setPos(it.pos() + (target_tl - tl))
            pr2 = self._pixmap_scene_rect(it)
            x = pr2.right() + gap
            row_h = max(row_h, pr2.height())

    def pack_optimal_in_viewport(self, items: list[_PosItem], bounds: QRectF) -> None:
        """Shelf-pack items inside bounds; shrink uniformly until the layout fits."""
        if not items:
            return
        inner = bounds.normalized().adjusted(self.spacing, self.spacing, -self.spacing, -self.spacing)
        if inner.width() < 8 or inner.height() < 8:
            return
        for _ in range(_PACK_SHRINK_MAX_ITERS):
            self._pack_shelf_into(items, inner)
            U = self._union_pixmap_rects(items)
            if U.width() < 1 or U.height() < 1:
                break
            if U.width() <= inner.width() + 0.5 and U.height() <= inner.height() + 0.5:
                shift = inner.center() - U.center()
                for it in items:
                    it.setPos(it.pos() + shift)
                return
            for it in items:
                it.setScale(max(0.02, float(it.scale()) * 0.9))

    def pack_optimal(self, items: list[_PosItem]) -> None:
        """Legacy hook — callers should use pack_optimal_in_viewport with scene bounds."""
        if not items:
            return
        U = self._union_pixmap_rects(items)
        self.pack_optimal_in_viewport(items, U.adjusted(-self.spacing, -self.spacing, self.spacing, self.spacing))
