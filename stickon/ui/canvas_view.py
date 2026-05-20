from __future__ import annotations

import math
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QMimeData, QTimer, Signal
from PySide6.QtGui import (
    QClipboard,
    QColor,
    QContextMenuEvent,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QImage,
    QImageReader,
    QMouseEvent,
    QMovie,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView

from stickon.scene.items.draw_item import DrawNodeItem
from stickon.scene.items.group_item import GroupNodeItem
from stickon.scene.items.image_item import ImageNodeItem
from stickon.scene.items.note_item import NoteNodeItem
from stickon.services.image_io import (
    can_import_image_path,
    is_gif_path,
    load_gif_poster_pixmap,
    load_still_pixmap,
)
from stickon.services.project_service import sample_color_at_global

# Clipboard / drag payloads on Windows often expose PNG/JPEG bytes without hasImage().
_KNOWN_RASTER_MIME_TYPES = frozenset(
    {
        "application/x-qt-image",
        "application/x-moz-nativeimage",
        "PNG",
        "JFIF",
        "jpeg",
        "jpg",
        "webp",
        "GIF",
        "bmp",
        "tif",
        "TIFF",
    }
)


def _mime_has_importable_image_urls(mime: QMimeData) -> bool:
    for url in mime.urls():
        path = Path(url.toLocalFile())
        if can_import_image_path(path):
            return True
    return False


def _mime_data_contains_raster(mime: QMimeData | None) -> bool:
    if mime is None:
        return False
    if mime.hasImage():
        return True
    if mime.hasUrls() and _mime_has_importable_image_urls(mime):
        return True
    for fmt in mime.formats():
        low = fmt.lower()
        if low.startswith("image/") and "svg" not in low:
            return True
        if fmt in _KNOWN_RASTER_MIME_TYPES:
            return True
    return False


def _pixmap_from_mimedata(mime: QMimeData) -> QPixmap | None:
    if mime.hasImage():
        v = mime.imageData()
        if isinstance(v, QImage) and not v.isNull():
            pm = QPixmap.fromImage(v)
            if not pm.isNull():
                return pm
        if isinstance(v, QPixmap) and not v.isNull():
            return v
    for fmt in (
        "image/png",
        "image/x-png",
        "PNG",
        "png",
        "image/jpeg",
        "image/jpg",
        "image/pjpeg",
        "JFIF",
        "jpeg",
        "image/webp",
        "WEBP",
        "image/bmp",
        "image/x-ms-bmp",
        "image/x-bmp",
        "Windows Bitmap",
        "BMP",
        "BMF",
        "image/tiff",
        "image/x-tiff",
        "TIFF",
    ):
        if mime.hasFormat(fmt):
            data = mime.data(fmt)
            if data:
                img = QImage()
                if img.loadFromData(data):
                    pm = QPixmap.fromImage(img)
                    if not pm.isNull():
                        return pm
    for fmt in mime.formats():
        low = fmt.lower()
        if not low.startswith("image/") or "svg" in low:
            continue
        data = mime.data(fmt)
        if not data:
            continue
        img = QImage()
        if img.loadFromData(data):
            pm = QPixmap.fromImage(img)
            if not pm.isNull():
                return pm
    return None


def _visual_item_bounds(it: QGraphicsItem) -> QRectF:
    """Bounds used for transforms/crop — pixmap-only rect for images (no selection padding)."""
    if isinstance(it, ImageNodeItem):
        return it.pixmapBoundingRect()
    if isinstance(it, NoteNodeItem):
        return it.core_bounds()
    return it.boundingRect()


def _resize_local_bounds(it: QGraphicsItem) -> QRectF | None:
    if isinstance(it, ImageNodeItem):
        return it.pixmapBoundingRect()
    if isinstance(it, NoteNodeItem):
        return it.core_bounds()
    return None


class _Gesture(Enum):
    none = auto()
    pan = auto()
    rotate = auto()
    scale = auto()
    flip = auto()
    crop = auto()
    zoom_drag = auto()
    draw = auto()
    erase = auto()
    resize_image_corner = auto()


class CanvasView(QGraphicsView):
    request_fit_window_to_content = Signal()
    request_fit_image_into_viewport = Signal(object)
    transform_history_committed = Signal(object, object, str)
    draw_item_committed = Signal(object)
    draw_items_erased = Signal(object)

    _DRAW_LINE_WIDTHS = (2.0, 5.0, 12.0)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        # Let Qt clear the viewport background each repaint. For transparent/overlay
        # scene setups, forcing opaque paint can leave drag trails on Windows.
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)

        self._gesture = _Gesture.none
        self._pan_from = QPointF()
        self._rotate_item: QGraphicsItem | None = None
        self._rotate_from_angle = 0.0
        self._rotate_start_item_rot = 0.0
        self._scale_item: QGraphicsItem | None = None
        self._scale_anchor_dist = 1.0
        self._scale_start_scale = 1.0
        self._crop_start_scene = QPointF()
        self._crop_item: ImageNodeItem | None = None
        self._draw_path: QPainterPath | None = None
        self._draw_item: DrawNodeItem | None = None

        self._resize_item: QGraphicsItem | None = None
        self._resize_anchor_local = QPointF()
        self._resize_anchor_scene_start = QPointF()
        self._resize_handle_start_scene = QPointF()
        self._resize_start_scale = 1.0
        self._saved_view_drag_mode: QGraphicsView.DragMode | None = None
        self._pause_effect_for_default_drag = False
        self._history_capture_before: dict[QGraphicsItem, dict[str, object]] | None = None
        self._history_capture_label: str | None = None
        self._erase_removed_batch: list[DrawNodeItem] = []

        self._draw_mode = False
        self._current_draw_layer_id: str | None = None
        self._current_draw_layer_name: str | None = None
        self._draw_layer_seq = 0
        self._drag_mode_before_draw = QGraphicsView.DragMode.RubberBandDrag
        self._draw_toolbar = None
        self._draw_toolbar_user_pos: QPoint | None = None
        self._draw_default_pen_color = QColor(255, 60, 60)
        self._draw_pen_color = QColor(self._draw_default_pen_color)
        self._draw_width_preset = 0  # 0 thin, 1 thick, 2 thicker
        self._draw_eraser_active = False

        self._key_c = False
        self._key_z = False
        self._key_s = False
        self._key_d = False

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Drag/drop hits the internal viewport, not the QGraphicsView; handle there.
        self.viewport().installEventFilter(self)

    def leaveEvent(self, event: QEvent) -> None:
        self.viewport().unsetCursor()
        super().leaveEvent(event)

    def reset_pointer_interaction_state(self) -> None:
        """Clear in-progress manipulation targets (e.g. after clearing the scene)."""
        self._gesture = _Gesture.none
        self._rotate_item = None
        self._scale_item = None
        self._crop_item = None
        self._resize_item = None
        self._draw_path = None
        self._draw_item = None
        self._erase_removed_batch.clear()
        self._history_capture_before = None
        self._history_capture_label = None
        if self._saved_view_drag_mode is not None:
            self.setDragMode(self._saved_view_drag_mode)
            self._saved_view_drag_mode = None
        self._pause_effect_for_default_drag = False
        self._set_live_scene_effect_paused(False)

    def _set_live_scene_effect_paused(self, _paused: bool) -> None:
        """Reserved for pausing heavy effects during drags (currently unused)."""
        pass

    @staticmethod
    def mime_accepts_external_drop(mime: QMimeData | None) -> bool:
        if mime is None:
            return False
        return _mime_data_contains_raster(mime)

    def scene_image_count(self) -> int:
        return sum(1 for it in self._scene.items() if isinstance(it, ImageNodeItem))

    @property
    def draw_mode(self) -> bool:
        return self._draw_mode

    @draw_mode.setter
    def draw_mode(self, value: bool) -> None:
        v = bool(value)
        if v == self._draw_mode:
            self._sync_draw_toolbar_geometry()
            return
        was = self._draw_mode
        self._draw_mode = v
        if v and not was:
            self._drag_mode_before_draw = self.dragMode()
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self._current_draw_layer_id = self._new_draw_layer_id()
            self._current_draw_layer_name = f"Draw Layer {self._draw_layer_seq}"
            # Reset toolbar to default corner each time draw mode starts.
            self._draw_toolbar_user_pos = None
        elif not v and was:
            self._current_draw_layer_id = None
            self._current_draw_layer_name = None
            self.setDragMode(self._drag_mode_before_draw)
        self._sync_draw_toolbar_visibility()

    def _existing_draw_layer_ids(self) -> set[str]:
        out: set[str] = set()
        for it in self._scene.items():
            if isinstance(it, DrawNodeItem) and it.draw_layer_id:
                out.add(it.draw_layer_id)
        return out

    def _new_draw_layer_id(self) -> str:
        used = self._existing_draw_layer_ids()
        while True:
            self._draw_layer_seq += 1
            lid = f"draw-layer-{self._draw_layer_seq}"
            if lid not in used:
                return lid

    @property
    def draw_pen_default_color(self) -> QColor:
        return QColor(self._draw_default_pen_color)

    @property
    def draw_pen_color(self) -> QColor:
        return QColor(self._draw_pen_color)

    @draw_pen_color.setter
    def draw_pen_color(self, c: QColor) -> None:
        self._draw_pen_color = QColor(c)
        if self._draw_toolbar is not None:
            self._draw_toolbar._pencil_widget.update()

    @property
    def draw_width_preset(self) -> int:
        return self._draw_width_preset

    @property
    def draw_eraser_active(self) -> bool:
        return self._draw_eraser_active

    @draw_eraser_active.setter
    def draw_eraser_active(self, value: bool) -> None:
        self._draw_eraser_active = bool(value)
        if self._draw_toolbar is not None:
            self._draw_toolbar.sync_from_canvas()

    def set_draw_width_preset(self, idx: int) -> None:
        self._draw_width_preset = max(0, min(2, int(idx)))
        if self._draw_toolbar is not None:
            self._draw_toolbar.sync_from_canvas()

    def _draw_line_width(self) -> float:
        return float(self._DRAW_LINE_WIDTHS[self._draw_width_preset])

    def _apply_stroke_pen(self, item: DrawNodeItem) -> None:
        pen = QPen(
            QColor(self._draw_pen_color),
            self._draw_line_width(),
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        item.setPen(pen)

    def _erase_radius_scene(self) -> float:
        p0 = self.mapToScene(QPoint(0, 0))
        p1 = self.mapToScene(QPoint(16, 0))
        return max(4.0, math.hypot(p1.x() - p0.x(), p1.y() - p0.y()))

    def _erase_at_scene_point(self, scene_pt: QPointF) -> None:
        r = self._erase_radius_scene()
        probe_scene = QPainterPath()
        probe_scene.addEllipse(scene_pt, r, r)
        to_remove: list[DrawNodeItem] = []
        for it in list(self._scene.items()):
            if not isinstance(it, DrawNodeItem):
                continue
            probe_local = it.mapFromScene(probe_scene)
            if it.collidesWithPath(probe_local):
                to_remove.append(it)
        for it in to_remove:
            if it not in self._erase_removed_batch:
                self._erase_removed_batch.append(it)
            self._scene.removeItem(it)

    def clear_all_draw_items(self) -> None:
        for it in list(self._scene.items()):
            if isinstance(it, DrawNodeItem):
                self._scene.removeItem(it)

    def _ensure_draw_toolbar(self) -> None:
        if self._draw_toolbar is None:
            from stickon.ui.draw_mode_toolbar import DrawModeToolbar

            self._draw_toolbar = DrawModeToolbar(self, self.viewport())
        self._draw_toolbar.sync_from_canvas()

    def _sync_draw_toolbar_visibility(self) -> None:
        if self._draw_mode:
            self._ensure_draw_toolbar()
            assert self._draw_toolbar is not None
            self._draw_toolbar.show()
            self._draw_toolbar.raise_()
            self._position_draw_toolbar()
        elif self._draw_toolbar is not None:
            self._draw_toolbar.hide()

    def _sync_draw_toolbar_geometry(self) -> None:
        if self._draw_mode and self._draw_toolbar is not None:
            self._position_draw_toolbar()

    def _position_draw_toolbar(self) -> None:
        if self._draw_toolbar is None or not self._draw_toolbar.isVisible():
            return
        self._draw_toolbar.adjustSize()
        if self._draw_toolbar_user_pos is None:
            pos = self._default_draw_toolbar_pos()
        else:
            pos = self._clamp_draw_toolbar_pos(self._draw_toolbar_user_pos)
            self._draw_toolbar_user_pos = QPoint(pos)
        self._draw_toolbar.move(pos)

    def _default_draw_toolbar_pos(self) -> QPoint:
        assert self._draw_toolbar is not None
        vp = self.viewport()
        margin = 10
        return QPoint(
            max(0, vp.width() - self._draw_toolbar.width() - margin),
            max(0, vp.height() - self._draw_toolbar.height() - margin),
        )

    def _clamp_draw_toolbar_pos(self, pos: QPoint) -> QPoint:
        assert self._draw_toolbar is not None
        vp = self.viewport()
        max_x = max(0, vp.width() - self._draw_toolbar.width())
        max_y = max(0, vp.height() - self._draw_toolbar.height())
        return QPoint(min(max(pos.x(), 0), max_x), min(max(pos.y(), 0), max_y))

    def set_draw_toolbar_position(self, pos: QPoint) -> None:
        if self._draw_toolbar is None:
            return
        clamped = self._clamp_draw_toolbar_pos(pos)
        self._draw_toolbar_user_pos = QPoint(clamped)
        self._draw_toolbar.move(clamped)

    def exit_draw_mode(self) -> None:
        self.draw_mode = False

    def finalize_new_images(self, count_before: int, added: list[ImageNodeItem]) -> None:
        if not added:
            return
        if count_before == 0:
            if self._added_batch_fits_viewport(added):
                self.request_fit_window_to_content.emit()
            else:
                for it in added:
                    self.request_fit_image_into_viewport.emit(it)
        else:
            for it in added:
                self.request_fit_image_into_viewport.emit(it)

    def _added_batch_fits_viewport(self, added: list[ImageNodeItem]) -> bool:
        """True if the batch's bbox (in viewport pixels) fits inside the viewport.

        Uses all four corners of the union rect mapped through the view transform so
        rounding and rotation don't underestimate size. Requires **both** width and
        height to fit (not merely max(width,height)).
        """
        if not added:
            return True
        margin = 24
        vp_w = max(32, self.viewport().width() - margin)
        vp_h = max(32, self.viewport().height() - margin)
        u = QRectF()
        first = True
        for it in added:
            sr = it.mapRectToScene(it.pixmapBoundingRect())
            u = sr if first else u.united(sr)
            first = False
        corners = (
            QPointF(u.left(), u.top()),
            QPointF(u.right(), u.top()),
            QPointF(u.right(), u.bottom()),
            QPointF(u.left(), u.bottom()),
        )
        xs: list[float] = []
        ys: list[float] = []
        for p in corners:
            v = self.mapFromScene(p)
            xs.append(float(v.x()))
            ys.append(float(v.y()))
        bw = math.ceil(max(xs) - min(xs))
        bh = math.ceil(max(ys) - min(ys))
        return bw <= vp_w and bh <= vp_h

    def images_union_smaller_than_viewport(self, slack: int = 8) -> bool:
        """True if the union of all image pixmaps is strictly smaller than the viewport (both axes)."""
        images = [it for it in self._scene.items() if isinstance(it, ImageNodeItem)]
        if not images:
            return False
        vp_w = self.viewport().width() - slack
        vp_h = self.viewport().height() - slack
        if vp_w < 32 or vp_h < 32:
            return False
        u = QRectF()
        first = True
        for it in images:
            sr = it.mapRectToScene(it.pixmapBoundingRect())
            u = sr if first else u.united(sr)
            first = False
        corners = (
            QPointF(u.left(), u.top()),
            QPointF(u.right(), u.top()),
            QPointF(u.right(), u.bottom()),
            QPointF(u.left(), u.bottom()),
        )
        xs: list[float] = []
        ys: list[float] = []
        for p in corners:
            v = self.mapFromScene(p)
            xs.append(float(v.x()))
            ys.append(float(v.y()))
        bw = math.ceil(max(xs) - min(xs))
        bh = math.ceil(max(ys) - min(ys))
        return bw < vp_w and bh < vp_h

    def viewport_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def fit_new_image_into_viewport(self, it: ImageNodeItem) -> None:
        """Scale down (never up) so the pixmap fits the visible viewport, then center."""
        m = it._movie
        resume_movie = m is not None and m.state() == QMovie.MovieState.Running
        if resume_movie:
            m.setPaused(True)
        try:
            vr = self.viewport_scene_rect()
            if vr.width() < 4 or vr.height() < 4:
                return
            inner = vr.adjusted(12, 12, -12, -12)
            br_local = it.pixmapBoundingRect()
            oc = br_local.center()
            it.setTransformOriginPoint(oc.x(), oc.y())
            for _ in range(28):
                sr = it.mapRectToScene(br_local)
                if sr.width() < 1e-6 or sr.height() < 1e-6:
                    break
                if sr.width() <= inner.width() and sr.height() <= inner.height():
                    break
                k = min(inner.width() / sr.width(), inner.height() / sr.height(), 1.0) * 0.998
                if k >= 0.9999:
                    break
                it.setScale(max(0.02, it.scale() * k))
            sr = it.mapRectToScene(br_local)
            it.setPos(it.pos() + (inner.center() - sr.center()))
        finally:
            if resume_movie and it.source_path:
                sp = str(it.source_path)
                if Path(sp).suffix.lower() == ".gif":
                    it.set_gif_movie(QMovie(sp, parent=self))
                elif it._movie is not None:
                    it._movie.setPaused(False)

    def _restart_gifs_after_drop(self, items: tuple[ImageNodeItem, ...]) -> None:
        """New QMovie after drop/fit — avoids Windows decoder stall after nested processEvents."""
        for it in items:
            if it.scene() is None:
                continue
            sp = it.source_path
            if not sp or Path(sp).suffix.lower() != ".gif":
                continue
            it.set_gif_movie(QMovie(str(sp), parent=self))

    def apply_drop_mime(
        self,
        mime: QMimeData | None,
        origin_scene: QPointF,
        *,
        clipboard: QClipboard | None = None,
    ) -> list[ImageNodeItem]:
        if mime is None:
            return []
        before = self.scene_image_count()
        step = QPointF(24, 24)
        cur = origin_scene
        handled = False
        added: list[ImageNodeItem] = []
        if mime.hasUrls():
            for url in mime.urls():
                path = Path(url.toLocalFile())
                if can_import_image_path(path):
                    it = self.add_image_from_path(str(path), cur)
                    if it is not None:
                        added.append(it)
                    handled = True
                    cur += step
        if not handled:
            pm = _pixmap_from_mimedata(mime)
            if pm is not None and not pm.isNull():
                it = ImageNodeItem(pm, None)
                it.setPos(cur)
                self._scene.addItem(it)
                self._stack_new_image(it)
                added.append(it)
                handled = True
        if not handled and mime.hasText():
            t = mime.text().strip().strip('"')
            p = Path(t)
            if can_import_image_path(p):
                it = self.add_image_from_path(str(p), cur)
                if it is not None:
                    added.append(it)
                    handled = True
        if not handled and clipboard is not None:
            img = clipboard.image()
            if not img.isNull():
                pm = QPixmap.fromImage(img)
                if not pm.isNull():
                    it = ImageNodeItem(pm, None)
                    it.setPos(cur)
                    self._scene.addItem(it)
                    self._stack_new_image(it)
                    added.append(it)
        if added:
            self.ensure_notes_above_images()
        self.finalize_new_images(before, added)
        gifs_drop = tuple(it for it in added if it._movie is not None and it.source_path)
        if gifs_drop:
            QTimer.singleShot(120, lambda: self._restart_gifs_after_drop(gifs_drop))
        return added

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        win = self.window()
        fn = getattr(win, "_show_commands_context_menu_at", None)
        if callable(fn):
            fn(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self.viewport():
            return super().eventFilter(watched, event)
        if isinstance(event, QDragEnterEvent):
            if self.mime_accepts_external_drop(event.mimeData()):
                event.acceptProposedAction()
                return True
            return False
        if isinstance(event, QDragMoveEvent):
            if self.mime_accepts_external_drop(event.mimeData()):
                event.acceptProposedAction()
                return True
            return False
        if isinstance(event, QDropEvent):
            if self.mime_accepts_external_drop(event.mimeData()):
                pos = self.mapToScene(event.position().toPoint())
                self.apply_drop_mime(event.mimeData(), pos)
                event.acceptProposedAction()
                return True
            return False
        return False

    def graphics_scene(self) -> QGraphicsScene:
        return self._scene

    def _scene_anchor_for_window_resize_center(self) -> QPointF | None:
        """Scene point to keep under the viewport center when the window is resized."""
        images = [it for it in self._scene.items() if isinstance(it, ImageNodeItem)]
        if not images:
            return None
        if len(images) == 1:
            it = images[0]
            return it.mapRectToScene(_visual_item_bounds(it)).center()
        br = QRectF()
        first = True
        for it in images:
            r = it.mapRectToScene(_visual_item_bounds(it))
            if first:
                br = r
                first = False
            else:
                br = br.united(r)
        return br.center()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        vp = self.viewport()
        if vp.width() < 2 or vp.height() < 2:
            return
        anchor = self._scene_anchor_for_window_resize_center()
        if anchor is not None:
            self.centerOn(anchor)
        self._sync_draw_toolbar_geometry()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            dy = event.angleDelta().y()
            if dy != 0:
                mw = self.window()
                ws = getattr(mw, "_win_state", None)
                if ws is not None:
                    # Match keyboard opacity steps: 0.08 per typical 120° wheel notch.
                    ws.adjust_opacity(0.08 * (dy / 120.0))
                    event.accept()
                    return
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.scale(factor, factor)
        self._sync_draw_toolbar_geometry()
        event.accept()

    def _finalize_other_notes_text_edit(self, clicked: QGraphicsItem | None) -> None:
        """Leave edit mode on notes that lost activation (caret/selection otherwise lingers)."""
        for item in self._scene.items():
            if not isinstance(item, NoteNodeItem):
                continue
            if not (
                item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction
            ):
                continue
            if clicked is item:
                continue
            item.finalize_text_edit_visual()

    def _item_at_screen(self, pos: QPointF) -> QGraphicsItem | None:
        sp = self.mapToScene(pos.toPoint())
        return self._scene.itemAt(sp, self.viewportTransform())

    @staticmethod
    def _state_float_eq(a: float, b: float, eps: float = 1e-6) -> bool:
        return abs(a - b) <= eps

    @staticmethod
    def _capture_item_state(it: QGraphicsItem) -> dict[str, object]:
        state: dict[str, object] = {
            "pos": QPointF(it.pos()),
            "rotation": float(it.rotation()),
            "scale": float(it.scale()),
            "origin": QPointF(it.transformOriginPoint()),
            "transform": QTransform(it.transform()),
        }
        if isinstance(it, ImageNodeItem):
            cr = it.crop_rect
            state["crop_rect"] = QRectF(cr) if cr is not None else None
        return state

    @classmethod
    def _state_changed(cls, a: dict[str, object], b: dict[str, object]) -> bool:
        pa = a["pos"]
        pb = b["pos"]
        assert isinstance(pa, QPointF) and isinstance(pb, QPointF)
        if not cls._state_float_eq(pa.x(), pb.x()) or not cls._state_float_eq(pa.y(), pb.y()):
            return True
        ra = float(a["rotation"])
        rb = float(b["rotation"])
        if not cls._state_float_eq(ra, rb):
            return True
        sa = float(a["scale"])
        sb = float(b["scale"])
        if not cls._state_float_eq(sa, sb):
            return True
        oa = a["origin"]
        ob = b["origin"]
        assert isinstance(oa, QPointF) and isinstance(ob, QPointF)
        if not cls._state_float_eq(oa.x(), ob.x()) or not cls._state_float_eq(oa.y(), ob.y()):
            return True
        ta = a["transform"]
        tb = b["transform"]
        assert isinstance(ta, QTransform) and isinstance(tb, QTransform)
        tvals_a = (
            ta.m11(),
            ta.m12(),
            ta.m13(),
            ta.m21(),
            ta.m22(),
            ta.m23(),
            ta.m31(),
            ta.m32(),
            ta.m33(),
        )
        tvals_b = (
            tb.m11(),
            tb.m12(),
            tb.m13(),
            tb.m21(),
            tb.m22(),
            tb.m23(),
            tb.m31(),
            tb.m32(),
            tb.m33(),
        )
        if any(not cls._state_float_eq(float(x), float(y)) for x, y in zip(tvals_a, tvals_b)):
            return True
        if "crop_rect" in a or "crop_rect" in b:
            ca = a.get("crop_rect")
            cb = b.get("crop_rect")
            if (ca is None) != (cb is None):
                return True
            if ca is not None and cb is not None:
                assert isinstance(ca, QRectF) and isinstance(cb, QRectF)
                if not (
                    cls._state_float_eq(ca.x(), cb.x())
                    and cls._state_float_eq(ca.y(), cb.y())
                    and cls._state_float_eq(ca.width(), cb.width())
                    and cls._state_float_eq(ca.height(), cb.height())
                ):
                    return True
        return False

    def _begin_history_capture(self, items: list[QGraphicsItem], label: str) -> None:
        uniq: list[QGraphicsItem] = []
        seen: set[int] = set()
        for it in items:
            if it is None or it.scene() is not self._scene:
                continue
            oid = id(it)
            if oid in seen:
                continue
            seen.add(oid)
            uniq.append(it)
        if not uniq:
            self._history_capture_before = None
            self._history_capture_label = None
            return
        self._history_capture_before = {it: self._capture_item_state(it) for it in uniq}
        self._history_capture_label = label

    def _commit_history_capture(self) -> None:
        before = self._history_capture_before
        label = self._history_capture_label
        self._history_capture_before = None
        self._history_capture_label = None
        if not before or not label:
            return
        after: dict[QGraphicsItem, dict[str, object]] = {}
        changed = False
        for it, st_before in before.items():
            if it.scene() is not self._scene:
                continue
            st_after = self._capture_item_state(it)
            after[it] = st_after
            if self._state_changed(st_before, st_after):
                changed = True
        if changed and after:
            self.transform_history_committed.emit(before, after, label)

    def _move_capture_candidates(self, clicked_item: QGraphicsItem | None) -> list[QGraphicsItem]:
        candidates: list[QGraphicsItem] = []
        selected = [
            x
            for x in self._scene.selectedItems()
            if isinstance(x, (ImageNodeItem, NoteNodeItem, DrawNodeItem, GroupNodeItem))
        ]
        candidates.extend(selected)
        if isinstance(clicked_item, (ImageNodeItem, NoteNodeItem, DrawNodeItem, GroupNodeItem)):
            candidates.append(clicked_item)
        return candidates

    _RESIZE_HANDLE_HIT_PX = 10
    _RESIZE_HANDLE_OPPOSITE = {"nw": "se", "ne": "sw", "se": "nw", "sw": "ne"}
    _IMAGE_Z_STEP = 1.0

    def ensure_notes_above_images(self) -> None:
        """Keep notes and draw layers above every image (text/draw win)."""
        overlays = [
            x
            for x in self._scene.items()
            if isinstance(x, (NoteNodeItem, DrawNodeItem))
        ]
        imgs = [x for x in self._scene.items() if isinstance(x, ImageNodeItem)]
        if not overlays or not imgs:
            return
        max_img = max(i.zValue() for i in imgs)
        min_overlay = min(x.zValue() for x in overlays)
        if min_overlay <= max_img:
            shift = max_img - min_overlay + self._IMAGE_Z_STEP
            for x in overlays:
                x.setZValue(x.zValue() + shift)

    def _stack_new_image(self, it: ImageNodeItem) -> None:
        """Place a newly added image above other images, below notes and draw layers."""
        imgs = [
            x for x in self._scene.items() if isinstance(x, ImageNodeItem) and x is not it
        ]
        z = max((x.zValue() for x in imgs), default=0.0) + self._IMAGE_Z_STEP
        overlays = [
            x
            for x in self._scene.items()
            if isinstance(x, (NoteNodeItem, DrawNodeItem))
        ]
        if overlays:
            min_overlay = min(x.zValue() for x in overlays)
            if z >= min_overlay:
                shift = z - min_overlay + self._IMAGE_Z_STEP
                for x in overlays:
                    x.setZValue(x.zValue() + shift)
        it.setZValue(z)

    def _hit_image_overlay_controls(self, it: ImageNodeItem, view_pos: QPointF) -> str | None:
        lp = it.mapFromScene(self.mapToScene(view_pos.toPoint()))
        if it._movie is not None:
            gh = it.hit_gif_overlay(lp)
            if gh is not None:
                return gh
        return None

    def _hit_resize_handle(self, it: QGraphicsItem, view_pos: QPointF) -> str | None:
        """Return nw/ne/se/sw if view_pos is near that corner handle in viewport pixels."""
        br = _resize_local_bounds(it)
        if br is None:
            return None
        corners: dict[str, QPointF] = {
            "nw": QPointF(br.left(), br.top()),
            "ne": QPointF(br.right(), br.top()),
            "se": QPointF(br.right(), br.bottom()),
            "sw": QPointF(br.left(), br.bottom()),
        }
        hit = self._RESIZE_HANDLE_HIT_PX
        vp = QPointF(view_pos)
        best: str | None = None
        best_d = hit + 1.0
        for name, lp in corners.items():
            sp = it.mapToScene(lp)
            pv = QPointF(self.mapFromScene(sp))
            d = math.hypot(vp.x() - pv.x(), vp.y() - pv.y())
            if d <= hit and d < best_d:
                best_d = d
                best = name
        return best

    def _update_hover_cursor(self, view_pos: QPointF) -> None:
        if self.draw_mode:
            self.viewport().setCursor(
                Qt.CursorShape.CrossCursor
                if not self.draw_eraser_active
                else Qt.CursorShape.ArrowCursor
            )
            return
        it = self._item_at_screen(view_pos)
        if (
            isinstance(it, ImageNodeItem)
            and not self.draw_mode
            and self._hit_image_overlay_controls(it, view_pos) is not None
        ):
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            return
        if (
            isinstance(it, (ImageNodeItem, NoteNodeItem))
            and it.isSelected()
            and _resize_local_bounds(it) is not None
        ):
            hn = self._hit_resize_handle(it, view_pos)
            if hn in ("nw", "se"):
                self.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor)
                return
            if hn in ("ne", "sw"):
                self.viewport().setCursor(Qt.CursorShape.SizeBDiagCursor)
                return
        self.viewport().unsetCursor()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() == Qt.MouseButton.MiddleButton:
            self._gesture = _Gesture.pan
            self._pan_from = QPointF(event.position())
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and (event.modifiers() & Qt.KeyboardModifier.AltModifier)
            and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        ):
            self._gesture = _Gesture.pan
            self._pan_from = QPointF(event.position())
            event.accept()
            return

        it = self._item_at_screen(QPointF(event.position()))

        if event.button() in (
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.RightButton,
        ):
            self._finalize_other_notes_text_edit(it)

        if (
            event.button() == Qt.MouseButton.LeftButton
            and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            and not (event.modifiers() & Qt.KeyboardModifier.AltModifier)
            and isinstance(it, (ImageNodeItem, NoteNodeItem))
        ):
            it.setSelected(True)
            event.accept()
            return

        if event.button() == Qt.MouseButton.RightButton and it is None:
            event.accept()
            return

        if self.draw_mode and event.button() == Qt.MouseButton.LeftButton:
            self._set_live_scene_effect_paused(True)
            if self.draw_eraser_active:
                self._gesture = _Gesture.erase
                self._erase_removed_batch = []
                self._erase_at_scene_point(self.mapToScene(event.position().toPoint()))
                event.accept()
                return
            self._gesture = _Gesture.draw
            p = self.mapToScene(event.position().toPoint())
            self._draw_path = QPainterPath(p)
            self._draw_item = DrawNodeItem()
            self._apply_stroke_pen(self._draw_item)
            self._draw_item.draw_layer_id = self._current_draw_layer_id
            self._draw_item.draw_layer_name = self._current_draw_layer_name
            self._draw_item.setPath(self._draw_path)
            self._draw_item.setZValue(max((it.zValue() for it in self._scene.items()), default=0.0) + 1.0)
            self._scene.addItem(self._draw_item)
            event.accept()
            return

        vp_pos = QPointF(event.position())
        img_overlay_hit = self._item_at_screen(vp_pos)
        if (
            isinstance(img_overlay_hit, ImageNodeItem)
            and event.button() == Qt.MouseButton.LeftButton
            and not self.draw_mode
            and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            and not self._key_c
            and not self._key_z
        ):
            ov = self._hit_image_overlay_controls(img_overlay_hit, vp_pos)
            if ov is not None:
                if not img_overlay_hit.isSelected():
                    self._scene.clearSelection()
                    img_overlay_hit.setSelected(True)
                if ov == "gif_pause":
                    img_overlay_hit.gif_pause()
                elif ov == "gif_resume":
                    img_overlay_hit.gif_resume()
                elif ov == "gif_prev":
                    img_overlay_hit.gif_prev_frame()
                elif ov == "gif_next":
                    img_overlay_hit.gif_next_frame()
                event.accept()
                return

        if self._key_c and event.button() == Qt.MouseButton.LeftButton and isinstance(it, ImageNodeItem):
            self._begin_history_capture([it], "crop")
            self._gesture = _Gesture.crop
            self._set_live_scene_effect_paused(True)
            self._crop_item = it
            self._crop_start_scene = self.mapToScene(event.position().toPoint())
            event.accept()
            return

        if self._key_z and event.button() == Qt.MouseButton.LeftButton:
            self._gesture = _Gesture.zoom_drag
            self._set_live_scene_effect_paused(True)
            self._pan_from = QPointF(event.position())
            event.accept()
            return

        if self._key_s and event.button() == Qt.MouseButton.LeftButton:
            gp = event.globalPosition().toPoint()
            hex_color = sample_color_at_global(gp.x(), gp.y())
            self.window().setWindowTitle(f"StickOn — sampled {hex_color}")
            event.accept()
            return

        if (
            isinstance(it, (ImageNodeItem, NoteNodeItem))
            and it.isSelected()
            and event.button() == Qt.MouseButton.LeftButton
            and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            and not (event.modifiers() & Qt.KeyboardModifier.AltModifier)
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            and not self._key_c
            and not self._key_z
            and not self.draw_mode
        ):
            br_resize = _resize_local_bounds(it)
            if br_resize is not None:
                hn = self._hit_resize_handle(it, QPointF(event.position()))
                if hn is not None:
                    corners: dict[str, QPointF] = {
                        "nw": QPointF(br_resize.left(), br_resize.top()),
                        "ne": QPointF(br_resize.right(), br_resize.top()),
                        "se": QPointF(br_resize.right(), br_resize.bottom()),
                        "sw": QPointF(br_resize.left(), br_resize.bottom()),
                    }
                    opp = self._RESIZE_HANDLE_OPPOSITE[hn]
                    if self._saved_view_drag_mode is None:
                        self._saved_view_drag_mode = self.dragMode()
                    self.setDragMode(QGraphicsView.DragMode.NoDrag)
                    self._gesture = _Gesture.resize_image_corner
                    self._resize_item = it
                    self._begin_history_capture([it], "resize")
                    self._resize_anchor_local = corners[opp]
                    self._resize_anchor_scene_start = it.mapToScene(self._resize_anchor_local)
                    self._resize_handle_start_scene = it.mapToScene(corners[hn])
                    self._resize_start_scale = float(it.scale())
                    self._set_live_scene_effect_paused(True)
                    event.accept()
                    return

        if (
            it is not None
            and event.button() == Qt.MouseButton.LeftButton
            and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            and (event.modifiers() & Qt.KeyboardModifier.AltModifier)
        ):
            self._gesture = _Gesture.scale
            self._scale_item = it
            self._begin_history_capture([it], "scale")
            self._scale_start_scale = float(it.scale())
            br = it.mapRectToScene(_visual_item_bounds(it))
            c = br.center()
            mp = self.mapToScene(event.position().toPoint())
            self._scale_anchor_dist = max(1e-6, math.hypot(mp.x() - c.x(), mp.y() - c.y()))
            self._set_live_scene_effect_paused(True)
            event.accept()
            return

        if (
            it is not None
            and event.button() == Qt.MouseButton.LeftButton
            and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            and (event.modifiers() & Qt.KeyboardModifier.AltModifier)
        ):
            self._gesture = _Gesture.flip
            self._rotate_item = it
            self._begin_history_capture([it], "flip")
            self._pan_from = QPointF(event.position())
            self._set_live_scene_effect_paused(True)
            event.accept()
            return

        if (
            it is not None
            and event.button() == Qt.MouseButton.LeftButton
            and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            and not (event.modifiers() & Qt.KeyboardModifier.AltModifier)
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            self._gesture = _Gesture.rotate
            self._rotate_item = it
            self._begin_history_capture([it], "rotate")
            self._rotate_start_item_rot = float(it.rotation())
            br = it.mapRectToScene(_visual_item_bounds(it))
            c = br.center()
            mp = self.mapToScene(event.position().toPoint())
            self._rotate_from_angle = math.degrees(math.atan2(mp.y() - c.y(), mp.x() - c.x()))
            self._set_live_scene_effect_paused(True)
            event.accept()
            return

        # Plain item selection/move is handled by QGraphicsView/QGraphicsScene.
        # Pause live effects for that default drag path as well to avoid
        # compositor ghost trails around image corner handles.
        if (
            event.button() == Qt.MouseButton.LeftButton
            and it is not None
            and not self.draw_mode
            and not self._key_c
            and not self._key_z
            and not self._key_s
        ):
            self._pause_effect_for_default_drag = True
            self._set_live_scene_effect_paused(True)
            self._begin_history_capture(self._move_capture_candidates(it), "move")

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            vp_pos = QPointF(event.position())
            it = self._item_at_screen(vp_pos)
            if isinstance(it, NoteNodeItem):
                # Let note item handle its own double-click edit behavior.
                super().mouseDoubleClickEvent(event)
                return
            if isinstance(it, ImageNodeItem) and it._movie is not None:
                lp = it.mapFromScene(self.mapToScene(vp_pos.toPoint()))
                if it.hit_gif_overlay(lp) is not None:
                    event.accept()
                    return
            scene_pt = self.mapToScene(event.position().toPoint())
            win = self.window()
            fn = getattr(win, "_new_note", None)
            if callable(fn):
                fn(scene_pt)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._gesture == _Gesture.pan:
            cur = QPointF(event.position())
            delta = cur - self._pan_from
            self._pan_from = cur
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return

        if self._gesture == _Gesture.resize_image_corner and self._resize_item is not None:
            it = self._resize_item
            mp = self.mapToScene(event.position().toPoint())
            v0 = self._resize_handle_start_scene - self._resize_anchor_scene_start
            v1 = mp - self._resize_anchor_scene_start
            denom = v0.x() * v0.x() + v0.y() * v0.y()
            num = v1.x() * v0.x() + v1.y() * v0.y()
            min_factor = 0.05 / max(self._resize_start_scale, 1e-6)
            factor = max(min_factor, num / max(denom, 1e-6))
            new_scale = max(0.05, self._resize_start_scale * factor)
            it.setScale(new_scale)
            drift = self._resize_anchor_scene_start - it.mapToScene(self._resize_anchor_local)
            it.setPos(it.pos() + drift)
            self.viewport().update()
            event.accept()
            return

        if self._gesture == _Gesture.rotate and self._rotate_item is not None:
            it = self._rotate_item
            br = it.mapRectToScene(_visual_item_bounds(it))
            c = br.center()
            mp = self.mapToScene(event.position().toPoint())
            ang = math.degrees(math.atan2(mp.y() - c.y(), mp.x() - c.x()))
            delta_ang = ang - self._rotate_from_angle
            new_rot = self._rotate_start_item_rot + delta_ang
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                new_rot = round(new_rot / 45.0) * 45.0
            it.setRotation(new_rot)
            event.accept()
            return

        if self._gesture == _Gesture.scale and self._scale_item is not None:
            it = self._scale_item
            br = it.mapRectToScene(_visual_item_bounds(it))
            c = br.center()
            mp = self.mapToScene(event.position().toPoint())
            dist = max(1e-6, math.hypot(mp.x() - c.x(), mp.y() - c.y()))
            factor = dist / self._scale_anchor_dist
            it.setScale(max(0.05, self._scale_start_scale * factor))
            self.viewport().update()
            event.accept()
            return

        if self._gesture == _Gesture.flip and self._rotate_item is not None:
            it = self._rotate_item
            delta_event = QPointF(event.position()) - self._pan_from
            if abs(delta_event.x()) > abs(delta_event.y()):
                it.setTransform(it.transform().scale(-1, 1))
            else:
                it.setTransform(it.transform().scale(1, -1))
            event.accept()
            return

        if self._gesture == _Gesture.crop and self._crop_item is not None:
            cur = self.mapToScene(event.position().toPoint())
            r = QRectF(self._crop_start_scene, cur).normalized()
            local = self._crop_item.mapFromScene(r).boundingRect()
            self._crop_item.set_crop_rect(local.intersected(self._crop_item.pixmapBoundingRect()))
            event.accept()
            return

        if self._gesture == _Gesture.zoom_drag:
            delta = QPointF(event.position()) - self._pan_from
            self._pan_from = QPointF(event.position())
            f = 1.0 + (-delta.y()) * 0.005
            f = max(0.5, min(f, 2.0))
            self.scale(f, f)
            event.accept()
            return

        if self._gesture == _Gesture.erase:
            self._erase_at_scene_point(self.mapToScene(event.position().toPoint()))
            event.accept()
            return

        if self._gesture == _Gesture.draw and self._draw_path is not None and self._draw_item is not None:
            p = self.mapToScene(event.position().toPoint())
            self._draw_path.lineTo(p)
            self._draw_item.setPath(self._draw_path)
            event.accept()
            return

        if self._key_d and (event.buttons() & Qt.MouseButton.LeftButton):
            mp = self.mapToScene(event.position().toPoint())
            self.window().setWindowTitle(f"StickOn — scene ({mp.x():.1f}, {mp.y():.1f})")
            event.accept()
            return

        if self._gesture == _Gesture.none:
            self._update_hover_cursor(QPointF(event.position()))

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            ended_gesture = self._gesture
            draw_item_finished: DrawNodeItem | None = None
            if ended_gesture == _Gesture.draw and self._draw_item is not None:
                draw_item_finished = self._draw_item
            self._gesture = _Gesture.none
            self._rotate_item = None
            self._scale_item = None
            self._crop_item = None
            self._resize_item = None
            self._draw_path = None
            self._draw_item = None
            if self._saved_view_drag_mode is not None:
                self.setDragMode(self._saved_view_drag_mode)
                self._saved_view_drag_mode = None
            self._pause_effect_for_default_drag = False
            self._set_live_scene_effect_paused(False)
            if draw_item_finished is not None:
                path_done = draw_item_finished.path()
                if path_done.elementCount() <= 1:
                    self._scene.removeItem(draw_item_finished)
                else:
                    self.draw_item_committed.emit(draw_item_finished)
            if ended_gesture == _Gesture.erase and self._erase_removed_batch:
                self.draw_items_erased.emit(list(self._erase_removed_batch))
            self._erase_removed_batch = []
            self._commit_history_capture()
        super().mouseReleaseEvent(event)

    def add_image_from_path(self, path_str: str, at: QPointF | None = None) -> ImageNodeItem | None:
        path = Path(path_str)
        if not can_import_image_path(path):
            return None
        if at is None:
            at = self.mapToScene(self.viewport().rect().center())
        if is_gif_path(path):
            pm = load_gif_poster_pixmap(path)
            it = ImageNodeItem(pm, None)
            it.source_path = str(path)
            it.set_gif_movie(QMovie(str(path), parent=self))
        else:
            pm = load_still_pixmap(path)
            if pm is None:
                return None
            it = ImageNodeItem(pm, None)
            it.source_path = str(path)
        it.setPos(at)
        self._scene.addItem(it)
        self._stack_new_image(it)
        self.ensure_notes_above_images()
        return it

    def keyPressEvent(self, event) -> None:
        k = event.key()
        if k == Qt.Key_Delete and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            # Delegate to main window so Delete uses one code path and undo; avoids
            # native dialogs returning focus with a stray Delete wiping the selection.
            host = self.window()
            suppress = getattr(host, "delete_shortcuts_suppressed", None)
            if callable(suppress) and suppress():
                event.accept()
                return
            handler = getattr(host, "_delete_selected_with_history", None)
            if callable(handler) and handler():
                event.accept()
                return
        if k == Qt.Key_C and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._key_c = True
        if k == Qt.Key_Z and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._key_z = True
        if k == Qt.Key_S and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._key_s = True
        if k == Qt.Key_D and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._key_d = True
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        k = event.key()
        if k == Qt.Key_C:
            self._key_c = False
        if k == Qt.Key_Z:
            self._key_z = False
        if k == Qt.Key_S:
            self._key_s = False
        if k == Qt.Key_D:
            self._key_d = False
        super().keyReleaseEvent(event)
