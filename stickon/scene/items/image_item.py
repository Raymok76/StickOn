from __future__ import annotations

import math
import uuid
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QMovie, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem, QStyleOptionGraphicsItem, QWidget

if TYPE_CHECKING:
    pass


def new_node_id() -> str:
    return uuid.uuid4().hex


class ImageNodeItem(QGraphicsPixmapItem):
    """Image node with optional non-destructive crop ( QRectF in item coordinates )."""

    # Handles + dashed frame are painted slightly outside the pixmap rect; without extra
    # margin, Qt's dirty rects miss those pixels and leaves ghost trails while resizing.
    _SELECT_PAD_ITEM_UNITS = 18.0
    _GIF_WRAP_SCAN_LIMIT = 10001

    def __init__(self, pixmap: QPixmap, node_id: str | None = None) -> None:
        super().__init__(pixmap)
        self.node_id = node_id or new_node_id()
        self.source_path: str | None = None
        self._movie: QMovie | None = None
        self._gif_last_frame_cache: int | None = None  # when frameCount()==0, index before wrap-to-start
        self.crop_rect: QRectF | None = None  # intersect with boundingRect
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)

    def pixmapBoundingRect(self) -> QRectF:
        """Local rect of the pixmap only (ignores selection chrome padding)."""
        return super().boundingRect()

    def boundingRect(self) -> QRectF:
        r = self.pixmapBoundingRect()
        if self.isSelected():
            m = self._SELECT_PAD_ITEM_UNITS
            return r.adjusted(-m, -m, m, m)
        return r

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            self.prepareGeometryChange()
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            sc = self.scene()
            if sc is not None:
                for v in sc.views():
                    v.viewport().update()
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged
            and value is None
            and self._movie is not None
        ):
            # Ensure GIF decoder resources are released when item leaves the scene.
            self.set_gif_movie(None)
        return super().itemChange(change, value)

    def set_gif_movie(self, movie: QMovie | None) -> None:
        old_movie = self._movie
        if old_movie is not None:
            try:
                old_movie.frameChanged.disconnect(self._on_movie_frame)
            except (TypeError, RuntimeError):
                pass
            old_movie.stop()
            if old_movie is not movie:
                old_movie.deleteLater()
        self._movie = movie
        self._gif_last_frame_cache = None
        if movie is not None:
            if movie.parent() is None:
                sc = self.scene()
                if sc is not None:
                    movie.setParent(sc)
            movie.setCacheMode(QMovie.CacheMode.CacheAll)
            movie.frameChanged.connect(self._on_movie_frame)
            movie.start()
            self._on_movie_frame()

    def _on_movie_frame(self, *_args: object) -> None:
        if self._movie is not None:
            pm = self._movie.currentPixmap()
            if not pm.isNull():
                self.setPixmap(pm)

    def _gif_seek_frame(self, idx: int) -> None:
        """Seek to frame index with fallback — jumpToFrame can intermittently fail while paused."""
        m = self._movie
        if m is None:
            return
        fc = m.frameCount()
        idx = max(0, idx)
        if fc > 0:
            idx = min(idx, fc - 1)

        def _seek_once(*, paused: bool) -> bool:
            m.setPaused(paused)
            ret = m.jumpToFrame(idx)
            # For known frame counts, trust the actual position over return value.
            if fc > 0:
                return m.currentFrameNumber() == idx
            return ret is not False

        ok = _seek_once(paused=True)
        if not ok:
            ok = _seek_once(paused=False)
            m.setPaused(True)

        # Rare Qt timing edge: index still not latched even after a retry.
        # Nudge through a neighboring frame, then re-seek to the target.
        if not ok and fc > 1:
            alt = idx - 1 if idx > 0 else idx + 1
            m.setPaused(False)
            m.jumpToFrame(alt)
            m.jumpToFrame(idx)
            m.setPaused(True)

        self._on_movie_frame()

    def gif_pause(self) -> None:
        if self._movie:
            self._movie.setPaused(True)
            self.update()

    def gif_resume(self) -> None:
        if self._movie:
            self._movie.setPaused(False)
            self.update()

    def gif_prev_frame(self) -> None:
        if not self._movie:
            return
        m = self._movie
        fc = m.frameCount()
        n = max(0, m.currentFrameNumber())

        if fc >= 1:
            self._gif_seek_frame((n - 1 + fc) % fc)
        elif n > 0:
            self._gif_seek_frame(n - 1)
        elif self._gif_last_frame_cache is not None:
            self._gif_seek_frame(self._gif_last_frame_cache)
        else:
            start = m.currentFrameNumber()
            m.setPaused(False)
            try:
                for _ in range(self._GIF_WRAP_SCAN_LIMIT):
                    prev_at = m.currentFrameNumber()
                    if not m.jumpToNextFrame():
                        break
                    if m.currentFrameNumber() == start:
                        self._gif_last_frame_cache = prev_at
                        break
            finally:
                m.setPaused(True)
            if self._gif_last_frame_cache is not None:
                self._gif_seek_frame(self._gif_last_frame_cache)
        self.update()

    def gif_next_frame(self) -> None:
        if not self._movie:
            return
        m = self._movie
        fc = m.frameCount()
        n = max(0, m.currentFrameNumber())

        if fc >= 1:
            self._gif_seek_frame((n + 1) % fc)
        else:
            m.setPaused(False)
            try:
                if not m.jumpToNextFrame():
                    m.jumpToFrame(n + 1)
            finally:
                m.setPaused(True)
            self._on_movie_frame()
        self.update()

    _GIF_CENTER_RADIUS_MUL = 1.32  # center play/pause circle vs side frame-step circles
    _GIF_PLAYING_PAUSE_MUL = 1.1  # single pause button vs base radius

    def _gif_base_radius_side(self, br: QRectF) -> float:
        """Base radius for side (frame) controls; layout scales down to fit pixmap width."""
        min_side = min(br.width(), br.height())
        K = self._GIF_CENTER_RADIUS_MUL
        g0 = 0.35  # gap = g0 * r_side
        # Total width: 4*r_side + 2*gap + 2*r_center = r_side*(4 + 2*g0 + 2*K)
        w_factor = 4.0 + 2.0 * g0 + 2.0 * K
        r_from_w = (br.width() * 0.88) / max(w_factor, 1e-6)
        r_from_h = (min_side * 0.26) / max(K, 1e-6)
        r_side = min(r_from_w, r_from_h)
        return max(r_side, min_side * 0.055)

    def overlay_gif_centers_and_radius(
        self,
    ) -> tuple[float, QPointF] | tuple[float, float, QPointF, QPointF, QPointF]:
        """
        Item-space radii and centers.
        Playing: (r_pause, pause_center).
        Paused: (r_side, r_center, prev_c, play_c, next_c).
        """
        br = self.pixmapBoundingRect()
        cy = br.center().y()
        cx = br.center().x()
        if self._movie is None:
            return (0.0, QPointF(cx, cy))
        if self._movie.state() != QMovie.MovieState.Paused:
            r_base = self._gif_base_radius_side(br)
            r_pause = r_base * self._GIF_CENTER_RADIUS_MUL * self._GIF_PLAYING_PAUSE_MUL
            return (r_pause, QPointF(cx, cy))
        r_side = self._gif_base_radius_side(br)
        r_center = r_side * self._GIF_CENTER_RADIUS_MUL
        gap = 0.35 * r_side
        c_prev = QPointF(cx - (r_side + gap + r_center), cy)
        c_play = QPointF(cx, cy)
        c_next = QPointF(cx + (r_center + gap + r_side), cy)
        return (r_side, r_center, c_prev, c_play, c_next)

    def hit_gif_overlay(self, lp: QPointF) -> str | None:
        """Hit test in item coords; playing → gif_pause circle; paused → prev / resume / next circles."""
        if self._movie is None:
            return None
        layout = self.overlay_gif_centers_and_radius()

        if len(layout) == 2:
            r_pause, c = layout

            def hit_playing(center: QPointF, rad: float) -> bool:
                return math.hypot(lp.x() - center.x(), lp.y() - center.y()) <= rad

            return "gif_pause" if hit_playing(c, r_pause) else None
        r_side, r_center, c_prev, c_play, c_next = layout
        if math.hypot(lp.x() - c_prev.x(), lp.y() - c_prev.y()) <= r_side:
            return "gif_prev"
        if math.hypot(lp.x() - c_play.x(), lp.y() - c_play.y()) <= r_center:
            return "gif_resume"
        if math.hypot(lp.x() - c_next.x(), lp.y() - c_next.y()) <= r_side:
            return "gif_next"
        return None

    def set_crop_rect(self, rect: QRectF | None) -> None:
        self.crop_rect = rect
        self.update()

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        painter.save()
        if self.crop_rect is not None and self.crop_rect.isValid():
            r = self.crop_rect.intersected(self.boundingRect())
            painter.setClipRect(r)
        super().paint(painter, option, widget)
        painter.restore()
        if self.isSelected():
            pen = QPen(QColor(80, 160, 255), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            br = self.pixmapBoundingRect()
            painter.drawRect(br)
            wt = painter.worldTransform()
            px_per = max(math.hypot(wt.m11(), wt.m12()), math.hypot(wt.m21(), wt.m22()), 1e-6)
            # Keep handle paint strictly within our padded boundingRect.
            # If handles can grow beyond _SELECT_PAD_ITEM_UNITS at low zoom,
            # Qt dirty-region updates may miss those pixels and leave trails.
            half = min(5.0 / px_per, self._SELECT_PAD_ITEM_UNITS * 0.45)
            handle_pen = QPen(QColor(80, 140, 230), 1.0)
            painter.setPen(handle_pen)
            painter.setBrush(QColor(250, 252, 255))
            for cx, cy in (
                (br.left(), br.top()),
                (br.right(), br.top()),
                (br.right(), br.bottom()),
                (br.left(), br.bottom()),
            ):
                painter.drawRect(QRectF(cx - half, cy - half, 2 * half, 2 * half))

            if self._movie is not None:
                layout = self.overlay_gif_centers_and_radius()
                ctrl_pen = QPen(QColor(40, 90, 160), max(1.2 / px_per, 0.8))
                ctrl_fill = QColor(255, 255, 255, 210)
                painter.setPen(ctrl_pen)
                painter.setBrush(ctrl_fill)

                def draw_circle(center: QPointF, rad: float) -> None:
                    painter.drawEllipse(center, rad, rad)

                bar_pen = QPen(QColor(35, 35, 35), max(1.0 / px_per, 0.7))

                def draw_glyph(center: QPointF, rad: float, text: str) -> None:
                    painter.save()
                    f = painter.font()
                    f.setPixelSize(max(int(rad * 1.45), 9))
                    painter.setFont(f)
                    painter.setPen(bar_pen)
                    painter.drawText(
                        QRectF(center.x() - rad, center.y() - rad, 2 * rad, 2 * rad),
                        Qt.AlignmentFlag.AlignCenter,
                        text,
                    )
                    painter.restore()

                if len(layout) == 2:
                    pr, pc = layout
                    draw_circle(pc, pr)
                    painter.setPen(bar_pen)
                    painter.setBrush(QColor(35, 35, 35))
                    bar_w = pr * 0.14
                    bar_h = pr * 0.55
                    gap = pr * 0.12
                    painter.drawRect(QRectF(pc.x() - gap * 0.5 - bar_w, pc.y() - bar_h * 0.5, bar_w, bar_h))
                    painter.drawRect(QRectF(pc.x() + gap * 0.5, pc.y() - bar_h * 0.5, bar_w, bar_h))
                else:
                    r_side, r_center, c_prev, c_play, c_next = layout
                    draw_circle(c_prev, r_side)
                    draw_circle(c_play, r_center)
                    draw_circle(c_next, r_side)
                    draw_glyph(c_prev, r_side, "\u25c2")
                    draw_glyph(c_play, r_center, "\u25b6")
                    draw_glyph(c_next, r_side, "\u25b8")
