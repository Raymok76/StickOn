from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QStandardPaths,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QCursor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QGuiApplication,
    QKeyEvent,
    QMovie,
    QMouseEvent,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGraphicsView,
    QMainWindow,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from stickon.core.commands import Command, CommandMetadataStore, CommandRegistry
from stickon.core.history import HistoryEntry, HistoryManager
from stickon.core.input_router import InputRouter
from stickon.core.window_state import WindowStateController
from stickon.scene.items.group_item import GroupNodeItem
from stickon.scene.items.draw_item import DrawNodeItem
from stickon.scene.items.image_item import ImageNodeItem
from stickon.scene.items.note_item import NoteAppearance, NoteNodeItem
from stickon.services.export_service import ExportService
from stickon.services.layout_service import LayoutService
from stickon.services.project_service import load_scene_from_path, save_scene_to_path
from stickon.services.session_service import autosession_path
from stickon.ui.canvas_view import CanvasView
from stickon.ui.font_settings_dialog import FontSettingsDialog
from stickon.ui.command_palette import (
    CommandPaletteDialog,
    RecordShortcutDialog,
    stored_shortcut_chord_only,
)
from stickon.ui.layers_dialog import LayersDialog
from stickon.ui.title_bar import DraggableTitleBar, ToggleChipLabel, _BAR_BG, _CHIP_RADIUS


def _assets_commands_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "commands.json"


def _shortcut_overrides_path() -> Path:
    root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    return Path(root) / "StickOn" / "shortcut_overrides.json"


def _note_defaults_path() -> Path:
    root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    return Path(root) / "StickOn" / "note_defaults.json"


_CONTEXT_MENU_ALIGN_IDS_ORDER = (
    "layout.align_top",
    "layout.align_bottom",
    "layout.align_left",
    "layout.align_right",
)
_CONTEXT_MENU_ALIGN_IDS = frozenset(_CONTEXT_MENU_ALIGN_IDS_ORDER)

_CONTEXT_MENU_GIF_IDS_ORDER = (
    "gif.pause",
    "gif.resume",
    "gif.next_frame",
    "gif.prev_frame",
)
_CONTEXT_MENU_GIF_IDS = frozenset(_CONTEXT_MENU_GIF_IDS_ORDER)

_FIT_SIZE_SETTLE_PASSES = 3


_CONTEXT_MENU_EXCLUDE_IDS = frozenset(
    {
        "window.lock",
        "window.click_through",
        "window.click_through_off",
        "window.fit_content",
        "node.group",
    }
)


class _WinClickThroughNativeFilter(QAbstractNativeEventFilter):
    """WM_NCHITTEST early — canvas uses HTTRANSPARENT; title bar / resize rim stay HTCLIENT."""

    def __init__(self, main_window: Any) -> None:
        super().__init__()
        self._mw = main_window

    def nativeEventFilter(self, event_type, message):  # noqa: ANN001
        if sys.platform != "win32":
            return False, 0
        mw = self._mw
        if not mw._win_state.click_through:
            return False, 0
        try:
            et_raw = bytes(event_type).decode("latin-1", errors="ignore").lower()
        except Exception:
            return False, 0
        if "windows" not in et_raw or "msg" not in et_raw:
            return False, 0
        from stickon.utils import win32_clickthrough as wct

        msg = wct.parse_windows_msg(message)
        if msg is None or int(msg.message) != wct.WM_NCHITTEST:
            return False, 0
        root = int(mw.winId())
        if not wct.hwnd_targets_root_window(int(msg.hwnd), root):
            return False, 0
        ht = wct.nc_hit_test_click_through(
            root,
            int(msg.lParam),
            title_bar_height_px=mw._title_bar.height(),
            margin_px=mw._resize_margin,
        )
        if ht is None:
            return False, 0
        return True, int(ht)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("StickOn")
        self.resize(1200, 800)
        self._schedule_fit_after_show = True
        self._pending_view_state: dict[str, float] | None = None

        self._history = HistoryManager()
        self._registry = CommandRegistry()
        self._layout = LayoutService()
        self._meta = CommandMetadataStore.load(str(_assets_commands_path()))
        self._last_text_edit_note: NoteNodeItem | None = None

        self._shortcut_overrides = self._load_shortcut_overrides()
        self._shortcut_map = self._build_shortcut_map()
        self._router = InputRouter(self._shortcut_map)
        self._note_appearance_defaults = self._load_note_defaults()

        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAcceptDrops(True)

        _min_side = 32
        self._resize_margin = 6
        self.setMinimumSize(_min_side, _min_side)

        self._win_state = WindowStateController(self)

        root = QWidget(self)
        self._central_root = root
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._seg_lock = ToggleChipLabel(
            lambda: self._toggle_lock_from_bar(),
            active_on_color="#c7dbff",
            parent=root,
        )
        self._seg_lock.setText("Position lock")
        self._seg_through = ToggleChipLabel(
            lambda: self._toggle_click_through_from_bar(),
            active_on_color="#ffd4b8",
            parent=root,
        )
        self._seg_through.setText("Click-through")
        self._btn_fit_content = QPushButton("Fit content", root)
        self._btn_fit_content.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_fit_content.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_fit_content.clicked.connect(self._on_fit_content_clicked)
        r = _CHIP_RADIUS
        self._btn_fit_content.setStyleSheet(
            f"QPushButton {{ background-color: {_BAR_BG}; border: 1px solid white; "
            f"border-radius: {r}px; padding: 4px 12px; color: #333; }}"
            f"QPushButton:hover {{ background-color: #d8f0e4; }}"
            f"QPushButton:pressed {{ background-color: #c6f0d6; border: 1px solid white; }}"
        )
        self._stickon_maximized = False
        self._geom_before_stickon_max = QRect()
        self._title_bar = DraggableTitleBar(
            self,
            [self._seg_lock, self._seg_through, self._btn_fit_content],
            root,
        )
        lay.addWidget(self._title_bar)

        self._canvas = CanvasView(self)
        lay.addWidget(self._canvas, stretch=1)
        self._canvas.graphics_scene().focusItemChanged.connect(self._on_scene_focus_item_changed)
        self._canvas.transform_history_committed.connect(self._on_canvas_transform_history_committed)
        self._canvas.draw_item_committed.connect(self._on_canvas_draw_item_committed)
        self._canvas.draw_items_erased.connect(self._on_canvas_draw_items_erased)

        self._canvas.setMouseTracking(True)
        self._canvas.viewport().setMouseTracking(True)
        self._title_bar.setMouseTracking(True)

        self.setCentralWidget(root)

        self._resize_active = False
        self._resize_edges = Qt.Edge(0)
        self._resize_start_geom = QRect()
        self._resize_press_global = QPoint()
        self._canvas.installEventFilter(self)
        self._canvas.viewport().installEventFilter(self)
        self._title_bar.installEventFilter(self)

        self._fit_debounce = QTimer(self)
        self._fit_debounce.setSingleShot(True)
        self._fit_debounce.setInterval(50)
        self._fit_debounce.timeout.connect(self._fit_window_to_content)
        self._canvas.request_fit_window_to_content.connect(self._schedule_fit_window_to_content)
        self._canvas.request_fit_image_into_viewport.connect(self._fit_new_image_into_viewport_slot)
        self._title_bar_full_height = self._title_bar.maximumHeight()
        self._title_bar_anim = QPropertyAnimation(self._title_bar, b"maximumHeight", self)
        self._title_bar_anim.setDuration(170)
        self._title_bar_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._title_bar_hide_timer = QTimer(self)
        self._title_bar_hide_timer.setSingleShot(True)
        self._title_bar_hide_timer.setInterval(3000)
        self._title_bar_hide_timer.timeout.connect(self._hide_title_bar_if_pointer_is_outside)
        self._clickthrough_hover_timer = QTimer(self)
        self._clickthrough_hover_timer.setInterval(40)
        self._clickthrough_hover_timer.timeout.connect(self._sync_clickthrough_passthrough)
        self._clickthrough_passthrough_enabled: bool | None = None

        self._register_commands()
        self._sync_command_shortcut_labels_from_map()
        self._refresh_status_labels()
        self._restore_autosession_if_any()

        self._win_clickthrough_filter: _WinClickThroughNativeFilter | None = None
        if sys.platform == "win32":
            app_inst = QApplication.instance()
            if app_inst is not None:
                self._win_clickthrough_filter = _WinClickThroughNativeFilter(self)
                app_inst.installNativeEventFilter(self._win_clickthrough_filter)
            self._sync_clickthrough_passthrough()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._clickthrough_hover_timer.isActive():
            self._clickthrough_hover_timer.stop()
        if sys.platform == "win32" and self._win_clickthrough_filter is not None:
            app_inst = QApplication.instance()
            if app_inst is not None:
                app_inst.removeNativeEventFilter(self._win_clickthrough_filter)
            self._win_clickthrough_filter = None
        try:
            if self._canvas.draw_mode:
                self._canvas.draw_mode = False
            g = self.geometry()
            save_scene_to_path(
                self._canvas.graphics_scene(),
                autosession_path(),
                window_geometry=(g.x(), g.y(), g.width(), g.height()),
                view_state=self._capture_canvas_view_state(),
            )
        except Exception:
            # Autosave must not prevent shutdown (e.g. older PySide APIs, disk errors).
            pass
        super().closeEvent(event)

    def _restore_autosession_if_any(self) -> None:
        path = autosession_path()
        if not path.is_file():
            return
        try:
            m = load_scene_from_path(self._canvas.graphics_scene(), path)
        except OSError:
            return
        self._canvas.ensure_notes_above_images()
        self.setWindowTitle("StickOn — last session")
        self._prune_missing_gif_sources()
        win = m.get("window")
        if isinstance(win, dict):
            try:
                x, y, w, h = int(win["x"]), int(win["y"]), int(win["w"]), int(win["h"])
                if w >= self.minimumWidth() and h >= self.minimumHeight():
                    self.setGeometry(x, y, w, h)
                    self._schedule_fit_after_show = False
            except (KeyError, TypeError, ValueError):
                pass
        self._pending_view_state = self._parse_canvas_view_state(m.get("view"))
        if self._pending_view_state is not None:
            self._schedule_fit_after_show = False

    def _prune_missing_gif_sources(self) -> None:
        scene = self._canvas.graphics_scene()
        for it in list(scene.items()):
            if not isinstance(it, ImageNodeItem):
                continue
            sp = it.source_path
            if sp and it._movie is not None and not Path(sp).is_file():
                scene.removeItem(it)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if CanvasView.mime_accepts_external_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if CanvasView.mime_accepts_external_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        if not CanvasView.mime_accepts_external_drop(mime):
            super().dropEvent(event)
            return
        gpos = event.globalPosition().toPoint()
        vp = self._canvas.viewport()
        local_in_vp = vp.mapFromGlobal(gpos)
        if vp.rect().contains(local_in_vp):
            scene_pt = self._canvas.mapToScene(local_in_vp)
        else:
            scene_pt = self._canvas.mapToScene(self._canvas.viewport().rect().center())
        self._canvas.apply_drop_mime(mime, scene_pt)
        event.acceptProposedAction()

    def nativeEvent(self, eventType, message):
        """Fallback WM_NCHITTEST handling if the app NativeEventFilter is skipped."""
        if sys.platform == "win32" and self._win_state.click_through:
            try:
                et_raw = bytes(eventType).decode("latin-1", errors="ignore").lower()
            except Exception:
                et_raw = ""
            if "windows" in et_raw and "msg" in et_raw:
                from stickon.utils import win32_clickthrough as wct

                msg = wct.parse_windows_msg(message)
                if msg is not None and int(msg.message) == wct.WM_NCHITTEST:
                    root = int(self.winId())
                    if wct.hwnd_targets_root_window(int(msg.hwnd), root):
                        ht = wct.nc_hit_test_click_through(
                            root,
                            int(msg.lParam),
                            title_bar_height_px=self._title_bar.height(),
                            margin_px=self._resize_margin,
                        )
                        if ht is not None:
                            return True, int(ht)
        return super().nativeEvent(eventType, message)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in (self._canvas, self._canvas.viewport()):
            if event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove):
                self._show_title_bar()
            if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if self._dispatch_main_shortcuts(event):
                    return True
            if event.type() == QEvent.Type.Leave:
                if not self._win_state.click_through:
                    self._title_bar_hide_timer.start()
        if watched == self._title_bar:
            if event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove):
                self._title_bar_hide_timer.stop()
            elif event.type() == QEvent.Type.Leave:
                if not self._win_state.click_through:
                    self._title_bar_hide_timer.start()
        watched_resize = (self._canvas, self._canvas.viewport(), self._title_bar)
        if watched in watched_resize:
            et = event.type()
            if et == QEvent.Type.Leave:
                if watched in (self._canvas, self._canvas.viewport()):
                    self._canvas.unsetCursor()
                elif watched == self._title_bar:
                    self._title_bar.setCursor(
                        Qt.CursorShape.ArrowCursor
                        if self._stickon_maximized
                        else Qt.CursorShape.SizeAllCursor
                    )
                return False
        if isinstance(event, QMouseEvent) and watched in watched_resize:
            if self._resize_active:
                return False
            et = event.type()
            gp = event.globalPosition().toPoint()
            edges = self._edges_at_global(gp)
            if self._stickon_maximized:
                edges = Qt.Edge(0)
            if et == QEvent.Type.MouseMove and not event.buttons():
                if watched in (self._canvas, self._canvas.viewport()):
                    self._canvas.setCursor(self._cursor_for_edges(edges))
                else:
                    if self._stickon_maximized:
                        tb_cur = Qt.CursorShape.ArrowCursor
                    elif edges:
                        tb_cur = self._cursor_for_edges(edges)
                    else:
                        tb_cur = Qt.CursorShape.SizeAllCursor
                    self._title_bar.setCursor(tb_cur)
                return False
            if (
                et == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and edges
            ):
                self._begin_window_resize(edges, gp)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _animate_title_bar_height(self, target_height: int) -> None:
        cur = self._title_bar.maximumHeight()
        target = max(0, min(int(target_height), self._title_bar_full_height))
        if cur == target:
            return
        if self._title_bar_anim.state() != QPropertyAnimation.State.Stopped:
            self._title_bar_anim.stop()
        self._title_bar_anim.setStartValue(cur)
        self._title_bar_anim.setEndValue(target)
        self._title_bar_anim.setEasingCurve(
            QEasingCurve.Type.OutCubic if target > cur else QEasingCurve.Type.InCubic
        )
        self._title_bar_anim.start()

    def _show_title_bar(self) -> None:
        if self._win_state.click_through:
            self._title_bar_hide_timer.stop()
            self._animate_title_bar_height(self._title_bar_full_height)
            return
        self._title_bar_hide_timer.stop()
        self._animate_title_bar_height(self._title_bar_full_height)

    def _hide_title_bar_if_pointer_is_outside(self) -> None:
        if self._win_state.click_through:
            self._title_bar_hide_timer.stop()
            self._animate_title_bar_height(self._title_bar_full_height)
            return
        gp = QCursor.pos()
        for w in (self._canvas.viewport(), self._title_bar):
            lp = w.mapFromGlobal(gp)
            if w.rect().contains(lp):
                return
        self._animate_title_bar_height(0)

    def _toggle_stickon_maximize(self) -> None:
        """Fill available screen area or restore geometry from before maximize."""
        if self._stickon_maximized:
            self.setGeometry(self._geom_before_stickon_max)
            self._stickon_maximized = False
        else:
            self._geom_before_stickon_max = self.geometry()
            scr = self.screen()
            if scr is None:
                scr = QGuiApplication.primaryScreen()
            ag = scr.availableGeometry() if scr is not None else QRect(50, 50, 1000, 700)
            self.setGeometry(ag)
            self._stickon_maximized = True
        self._title_bar.set_maximized_visual(self._stickon_maximized)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resize_active:
            self._continue_window_resize(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resize_active and event.button() == Qt.MouseButton.LeftButton:
            self._resize_active = False
            self._resize_edges = Qt.Edge(0)
            self.releaseMouse()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _edges_at_global(self, gp: QPoint) -> Qt.Edge:
        fr = self.frameGeometry()
        x = gp.x() - fr.left()
        y = gp.y() - fr.top()
        w = fr.width()
        h = fr.height()
        M = self._resize_margin
        corner_m = max(M, 14)
        in_left = x <= M
        in_top = y <= M
        in_right = x >= w - M
        in_bottom = y >= h - M
        in_left_corner = x <= corner_m
        in_top_corner = y <= corner_m
        in_right_corner = x >= w - corner_m
        in_bottom_corner = y >= h - corner_m
        e = Qt.Edge(0)
        # Make corner hotspots easier to hit than straight edge hotspots.
        if in_left or (in_left_corner and (in_top_corner or in_bottom_corner)):
            e |= Qt.LeftEdge
        if in_top or (in_top_corner and (in_left_corner or in_right_corner)):
            e |= Qt.TopEdge
        if in_right or (in_right_corner and (in_top_corner or in_bottom_corner)):
            e |= Qt.RightEdge
        if in_bottom or (in_bottom_corner and (in_left_corner or in_right_corner)):
            e |= Qt.BottomEdge
        return e

    def _cursor_for_edges(self, edges: Qt.Edge) -> Qt.CursorShape:
        if not edges:
            return Qt.CursorShape.ArrowCursor
        has_l = bool(edges & Qt.LeftEdge)
        has_r = bool(edges & Qt.RightEdge)
        has_t = bool(edges & Qt.TopEdge)
        has_b = bool(edges & Qt.BottomEdge)
        if (has_t and has_l) or (has_b and has_r):
            return Qt.CursorShape.SizeFDiagCursor
        if (has_t and has_r) or (has_b and has_l):
            return Qt.CursorShape.SizeBDiagCursor
        if has_l or has_r:
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.SizeVerCursor

    def _begin_window_resize(self, edges: Qt.Edge, gp: QPoint) -> None:
        self._resize_active = True
        self._resize_edges = edges
        self._resize_start_geom = self.geometry()
        self._resize_press_global = gp
        self.grabMouse()

    def _continue_window_resize(self, gp: QPoint) -> None:
        dg = gp - self._resize_press_global
        r = QRect(self._resize_start_geom)
        mw = self.minimumWidth()
        mh = self.minimumHeight()
        if self._resize_edges & Qt.LeftEdge:
            r.setLeft(r.left() + dg.x())
        if self._resize_edges & Qt.RightEdge:
            r.setRight(r.right() + dg.x())
        if self._resize_edges & Qt.TopEdge:
            r.setTop(r.top() + dg.y())
        if self._resize_edges & Qt.BottomEdge:
            r.setBottom(r.bottom() + dg.y())
        if r.width() < mw:
            if self._resize_edges & Qt.LeftEdge:
                r.setLeft(r.right() - mw + 1)
            else:
                r.setRight(r.left() + mw - 1)
        if r.height() < mh:
            if self._resize_edges & Qt.TopEdge:
                r.setTop(r.bottom() - mh + 1)
            else:
                r.setBottom(r.top() + mh - 1)
        self.setGeometry(r.normalized())

    def _toggle_lock_from_bar(self) -> None:
        self._win_state.toggle_lock_position()
        self._refresh_status_labels()

    def _toggle_click_through_from_bar(self) -> None:
        self._win_state.toggle_click_through()
        self._refresh_status_labels()

    def _on_fit_content_clicked(self) -> None:
        self._fit_window_to_content(keep_current_view=True)

    def _register_commands(self) -> None:
        def reg(
            cid: str,
            fn: Callable[[dict[str, Any]], None],
            *,
            is_checked: Callable[[], bool] | None = None,
        ) -> None:
            title = cid
            shortcut = None
            for e in self._meta.entries:
                if e.get("id") == cid:
                    title = str(e.get("title", cid))
                    shortcut = e.get("shortcut") or None
                    break
            self._registry.register(
                Command(
                    id=cid,
                    title=title,
                    handler=fn,
                    shortcut=str(shortcut) if shortcut else None,
                    is_checked=is_checked,
                )
            )

        reg("palette.open", lambda ctx: self._open_palette())
        reg(
            "window.always_on_top",
            lambda ctx: self._win_state.toggle_always_on_top(),
            is_checked=lambda: self._win_state.always_on_top,
        )
        reg(
            "window.always_on_bottom",
            lambda ctx: self._win_state.toggle_always_on_bottom(),
            is_checked=lambda: self._win_state.always_on_bottom,
        )
        reg(
            "window.click_through",
            lambda ctx: self._win_state.toggle_click_through(),
            is_checked=lambda: self._win_state.click_through,
        )
        reg("window.opacity_up", lambda ctx: self._win_state.adjust_opacity(0.08))
        reg("window.opacity_down", lambda ctx: self._win_state.adjust_opacity(-0.08))
        reg(
            "window.lock",
            lambda ctx: self._win_state.toggle_lock_position(),
            is_checked=lambda: self._win_state.lock_position,
        )
        reg("window.click_through_off", lambda ctx: self._win_state.set_click_through(False))
        reg("window.fit_content", lambda ctx: self._on_fit_content_clicked())
        reg("layout.pack", lambda ctx: self._pack())
        reg("layout.layers", lambda ctx: self._open_layers_dialog())
        reg("layout.align_left", lambda ctx: self._align("left"))
        reg("layout.align_right", lambda ctx: self._align("right"))
        reg("layout.align_top", lambda ctx: self._align("top"))
        reg("layout.align_bottom", lambda ctx: self._align("bottom"))
        reg("node.group", lambda ctx: self._group())
        reg("note.new", lambda ctx: self._new_note())
        reg(
            "draw.toggle",
            lambda ctx: self._toggle_draw(),
            is_checked=lambda: self._canvas.draw_mode,
        )
        reg("edit.undo", lambda ctx: self._history.undo())
        reg("edit.redo", lambda ctx: self._history.redo())
        reg("edit.select_all", lambda ctx: self._select_all())
        reg("export.scene", lambda ctx: self._export_scene())
        reg("gif.pause", lambda ctx: self._gif_pause())
        reg("gif.resume", lambda ctx: self._gif_resume())
        reg("gif.next_frame", lambda ctx: self._gif_step(1))
        reg("gif.prev_frame", lambda ctx: self._gif_step(-1))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._win_state.sync_win32_topmost_from_state()
        self._refresh_status_labels()
        if self._schedule_fit_after_show:
            self._schedule_fit_window_to_content()
        else:
            self._apply_pending_view_state()

    def _refresh_status_labels(self) -> None:
        self._seg_lock.set_active(self._win_state.lock_position)
        self._seg_through.set_active(self._win_state.click_through)
        if self._win_state.click_through:
            self._title_bar_hide_timer.stop()
            self._animate_title_bar_height(self._title_bar_full_height)
        self._sync_clickthrough_passthrough()

    def _is_in_resize_border_zone(self, gp: QPoint) -> bool:
        return bool(self._edges_at_global(gp))

    def _is_clickthrough_interactive_zone(self, gp: QPoint) -> bool:
        if self._global_title_bar_rect().contains(gp):
            return True
        return self._is_in_resize_border_zone(gp)

    def _sync_clickthrough_passthrough(self) -> None:
        if sys.platform != "win32":
            return
        from stickon.utils.win32_clickthrough import set_clickthrough_passthrough

        desired_passthrough = False
        if self._win_state.click_through:
            if not self._clickthrough_hover_timer.isActive():
                self._clickthrough_hover_timer.start()
            desired_passthrough = not self._is_clickthrough_interactive_zone(QCursor.pos())
        elif self._clickthrough_hover_timer.isActive():
            self._clickthrough_hover_timer.stop()

        if desired_passthrough == self._clickthrough_passthrough_enabled:
            return
        set_clickthrough_passthrough(int(self.winId()), desired_passthrough)
        self._clickthrough_passthrough_enabled = desired_passthrough

    def _schedule_fit_window_to_content(self) -> None:
        self._fit_debounce.start()

    @staticmethod
    def _suspend_running_gif_movies(scene) -> list[tuple[ImageNodeItem, QMovie.MovieState]]:
        snap: list[tuple[ImageNodeItem, QMovie.MovieState]] = []
        for it in scene.items():
            if not isinstance(it, ImageNodeItem) or it._movie is None:
                continue
            st = it._movie.state()
            snap.append((it, st))
            if st == QMovie.MovieState.Running:
                it._movie.setPaused(True)
        return snap

    @staticmethod
    def _restore_gif_movie_states(
        snap: list[tuple[ImageNodeItem, QMovie.MovieState]], canvas: QGraphicsView
    ) -> None:
        for it, st in snap:
            if st != QMovie.MovieState.Running:
                continue
            path = it.source_path
            if path and Path(path).suffix.lower() == ".gif":
                it.set_gif_movie(QMovie(str(path), parent=canvas))
            elif it._movie is not None:
                it._movie.setPaused(False)

    def _fit_window_to_content(self, *, keep_current_view: bool = False) -> None:
        scene = self._canvas.graphics_scene()
        br = scene.itemsBoundingRect()
        if br.isEmpty():
            return
        br = br.normalized()

        gif_snap = self._suspend_running_gif_movies(scene)
        try:
            if keep_current_view:
                margin_px = 16
                corners = (
                    QPointF(br.left(), br.top()),
                    QPointF(br.right(), br.top()),
                    QPointF(br.right(), br.bottom()),
                    QPointF(br.left(), br.bottom()),
                )
                xs: list[float] = []
                ys: list[float] = []
                for p in corners:
                    v = self._canvas.mapFromScene(p)
                    xs.append(float(v.x()))
                    ys.append(float(v.y()))
                vp_need_w = max(32, int(math.ceil(max(xs) - min(xs))) + 2 * margin_px)
                vp_need_h = max(32, int(math.ceil(max(ys) - min(ys))) + 2 * margin_px)
            else:
                margin = 16.0
                br = br.adjusted(-margin, -margin, margin, margin)
                self._canvas.resetTransform()
                vp_need_w = max(32, int(math.ceil(br.width())))
                vp_need_h = max(32, int(math.ceil(br.height())))

            scr = self.screen()
            if scr is None:
                scr = QGuiApplication.primaryScreen()
            avail_w = avail_h = 10**9
            if scr is not None:
                ag = scr.availableGeometry()
                avail_w = max(self.minimumWidth(), ag.width())
                avail_h = max(self.minimumHeight(), ag.height())

            # Viewport target size + measured chrome; iterate briefly so margins converge.
            for _ in range(_FIT_SIZE_SETTLE_PASSES):
                extra_w = max(0, self.width() - self._canvas.viewport().width())
                extra_h = max(0, self.height() - self._canvas.viewport().height())
                target_w = min(max(self.minimumWidth(), vp_need_w + extra_w), avail_w)
                target_h = min(max(self.minimumHeight(), vp_need_h + extra_h), avail_h)
                self.resize(target_w, target_h)
                # Flush posted resize/layout updates without pumping full user input.
                QCoreApplication.sendPostedEvents(self)

            if not keep_current_view:
                self._canvas.fitInView(br, Qt.AspectRatioMode.KeepAspectRatio)
        finally:
            self._restore_gif_movie_states(gif_snap, self._canvas)

    def _capture_canvas_view_state(self) -> dict[str, float]:
        tr = self._canvas.transform()
        center = self._canvas.mapToScene(self._canvas.viewport().rect().center())
        return {
            "m11": float(tr.m11()),
            "m12": float(tr.m12()),
            "m21": float(tr.m21()),
            "m22": float(tr.m22()),
            "center_x": float(center.x()),
            "center_y": float(center.y()),
        }

    def _parse_canvas_view_state(self, raw: object) -> dict[str, float] | None:
        if not isinstance(raw, dict):
            return None
        try:
            state = {
                "m11": float(raw["m11"]),
                "m12": float(raw["m12"]),
                "m21": float(raw["m21"]),
                "m22": float(raw["m22"]),
                "center_x": float(raw["center_x"]),
                "center_y": float(raw["center_y"]),
            }
        except (KeyError, TypeError, ValueError):
            return None
        vals = (
            state["m11"],
            state["m12"],
            state["m21"],
            state["m22"],
            state["center_x"],
            state["center_y"],
        )
        if any(not math.isfinite(v) for v in vals):
            return None
        return state

    def _apply_pending_view_state(self) -> None:
        if self._pending_view_state is None:
            return
        st = self._pending_view_state
        self._pending_view_state = None
        self._canvas.setTransform(
            QTransform(
                st["m11"],
                st["m12"],
                0.0,
                st["m21"],
                st["m22"],
                0.0,
                0.0,
                0.0,
                1.0,
            )
        )
        self._canvas.centerOn(QPointF(st["center_x"], st["center_y"]))

    def _execute(self, command_id: str) -> None:
        ctx: dict[str, Any] = {"window": self}
        try:
            self._registry.execute(command_id, ctx)
        except KeyError:
            pass
        finally:
            self._refresh_status_labels()

    def _load_shortcut_overrides(self) -> dict[str, str]:
        path = _shortcut_overrides_path()
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                chord = stored_shortcut_chord_only(k, v.strip())
                if chord:
                    out[k] = chord
        return out

    def _save_shortcut_overrides(self) -> None:
        path = _shortcut_overrides_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._shortcut_overrides, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _build_shortcut_map(self) -> dict[str, str]:
        shortcut_map: dict[str, str] = {}
        for e in self._meta.entries:
            sid = (e.get("shortcut") or "").strip()
            cid = e.get("id")
            if sid and cid:
                shortcut_map[sid] = cid
        # Opacity up: some layouts emit Key_Equal with Ctrl+Shift instead of Key_Plus.
        shortcut_map["Ctrl+Shift+="] = "window.opacity_up"

        override_cids = frozenset(self._shortcut_overrides.keys())
        for k, v in list(shortcut_map.items()):
            if v in override_cids:
                del shortcut_map[k]

        for cid, sc in self._shortcut_overrides.items():
            sc = sc.strip()
            if not sc:
                continue
            shortcut_map.pop(sc, None)
            shortcut_map[sc] = cid
        return shortcut_map

    def _sync_command_shortcut_labels_from_map(self) -> None:
        meta_by_id = self._meta.by_id()
        for cmd in self._registry.all():
            if cmd.id in self._shortcut_overrides:
                cmd.shortcut = stored_shortcut_chord_only(cmd.id, self._shortcut_overrides[cmd.id])
                continue
            entry = meta_by_id.get(cmd.id, {})
            sc = entry.get("shortcut")
            raw = str(sc).strip() if sc else ""
            cmd.shortcut = stored_shortcut_chord_only(cmd.id, raw) or None

    def _apply_shortcut_override(self, command_id: str, portable: str) -> None:
        chord = stored_shortcut_chord_only(command_id, portable.strip())
        if not chord:
            return
        before = dict(self._shortcut_overrides)
        after = dict(before)
        after[command_id] = chord
        if before == after:
            return

        def apply(overrides: dict[str, str]) -> None:
            self._set_shortcut_overrides_state(overrides)

        apply(after)

        def redo() -> None:
            apply(after)

        def undo() -> None:
            apply(before)

        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label="shortcut override"))

    def _reset_shortcut_overrides_to_defaults(self) -> None:
        before = dict(self._shortcut_overrides)
        if not before:
            return
        after: dict[str, str] = {}
        self._set_shortcut_overrides_state(after)

        def redo() -> None:
            self._set_shortcut_overrides_state(after)

        def undo() -> None:
            self._set_shortcut_overrides_state(before)

        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label="shortcut defaults"))

    def _set_shortcut_overrides_state(self, overrides: dict[str, str]) -> None:
        self._shortcut_overrides = dict(overrides)
        path = _shortcut_overrides_path()
        if self._shortcut_overrides:
            self._save_shortcut_overrides()
        else:
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass
        self._shortcut_map = self._build_shortcut_map()
        self._router = InputRouter(self._shortcut_map)
        self._sync_command_shortcut_labels_from_map()

    def _load_note_defaults(self) -> NoteAppearance:
        built = NoteAppearance.builtin()
        path = _note_defaults_path()
        if not path.is_file():
            return built
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return built
        merged = NoteAppearance.from_json_dict(raw)
        return merged if merged is not None else built

    def _save_note_defaults(self, appearance: NoteAppearance) -> None:
        path = _note_defaults_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(appearance.to_json_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _open_note_font_settings(self) -> None:
        notes = [it for it in self._selected_items() if isinstance(it, NoteNodeItem)]
        notes_before = {n: NoteAppearance.from_note(n) for n in notes}
        defaults_before = NoteAppearance(
            font_family=self._note_appearance_defaults.font_family,
            font_point_size=self._note_appearance_defaults.font_point_size,
            text_color=QColor(self._note_appearance_defaults.text_color),
            bg_color=QColor(self._note_appearance_defaults.bg_color),
            border_width=float(self._note_appearance_defaults.border_width),
            border_color=QColor(self._note_appearance_defaults.border_color),
            bold=self._note_appearance_defaults.bold,
            italic=self._note_appearance_defaults.italic,
            underline=self._note_appearance_defaults.underline,
            strike_out=self._note_appearance_defaults.strike_out,
        )
        base = NoteAppearance.from_note(notes[0]) if notes else self._note_appearance_defaults
        dlg = FontSettingsDialog(base, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.result_appearance()
        self._note_appearance_defaults = result
        self._save_note_defaults(result)
        for n in notes:
            result.apply_to(n)

        notes_after = {n: NoteAppearance.from_note(n) for n in notes}
        defaults_after = NoteAppearance(
            font_family=result.font_family,
            font_point_size=result.font_point_size,
            text_color=QColor(result.text_color),
            bg_color=QColor(result.bg_color),
            border_width=float(result.border_width),
            border_color=QColor(result.border_color),
            bold=result.bold,
            italic=result.italic,
            underline=result.underline,
            strike_out=result.strike_out,
        )
        changed_defaults = defaults_before.to_json_dict() != defaults_after.to_json_dict()
        changed_notes = any(
            notes_before[n].to_json_dict() != notes_after[n].to_json_dict() for n in notes
        )
        if not changed_defaults and not changed_notes:
            return

        def apply_snapshot(
            defaults_snapshot: NoteAppearance,
            note_snapshot: dict[NoteNodeItem, NoteAppearance],
        ) -> None:
            self._note_appearance_defaults = NoteAppearance(
                font_family=defaults_snapshot.font_family,
                font_point_size=defaults_snapshot.font_point_size,
                text_color=QColor(defaults_snapshot.text_color),
                bg_color=QColor(defaults_snapshot.bg_color),
                border_width=float(defaults_snapshot.border_width),
                border_color=QColor(defaults_snapshot.border_color),
                bold=defaults_snapshot.bold,
                italic=defaults_snapshot.italic,
                underline=defaults_snapshot.underline,
                strike_out=defaults_snapshot.strike_out,
            )
            self._save_note_defaults(self._note_appearance_defaults)
            for note, app in note_snapshot.items():
                try:
                    if note.scene() is self._canvas.graphics_scene():
                        app.apply_to(note)
                except RuntimeError:
                    continue

        def redo() -> None:
            apply_snapshot(defaults_after, notes_after)

        def undo() -> None:
            apply_snapshot(defaults_before, notes_before)

        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label="font settings"))

    def _current_font_settings_appearance(self) -> NoteAppearance:
        notes = [it for it in self._selected_items() if isinstance(it, NoteNodeItem)]
        if notes:
            return NoteAppearance.from_note(notes[0])
        return self._note_appearance_defaults

    def _on_palette_shortcut_customize(self, command_id: str, palette_dlg: CommandPaletteDialog) -> None:
        rec = RecordShortcutDialog(self)
        if rec.exec() != QDialog.DialogCode.Accepted:
            return
        portable = rec.chosen_shortcut()
        if not portable:
            return
        self._apply_shortcut_override(command_id, portable)
        palette_dlg.refresh_commands(self._registry.all())

    def _open_palette(self) -> None:
        dlg_holder: list[CommandPaletteDialog | None] = [None]

        def customize(cid: str) -> None:
            d = dlg_holder[0]
            if d is not None:
                self._on_palette_shortcut_customize(cid, d)

        def reset_defaults() -> None:
            self._reset_shortcut_overrides_to_defaults()
            d = dlg_holder[0]
            if d is not None:
                d.refresh_commands(self._registry.all())

        dlg = CommandPaletteDialog(
            self._registry.all(),
            self,
            shortcut_customizer=customize,
            reset_shortcuts_to_defaults=reset_defaults,
        )
        dlg_holder[0] = dlg
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cid = dlg.selected_command_id()
            if cid:
                self._execute(cid)

    def _dispatch_main_shortcuts(self, event: QKeyEvent) -> bool:
        if self._dispatch_note_text_undo_redo(event):
            return True

        if (
            event.key() == Qt.Key_Delete
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            return self._delete_selected_with_history()

        cid = self._router.match_key_event(event)
        if cid:
            self._execute(cid)
            return True
        # Windows users often expect Ctrl+Y for redo in addition to Ctrl+Shift+Z.
        if (
            event.key() == Qt.Key_Y
            and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        ):
            self._execute("edit.redo")
            return True
        if event.key() == Qt.Key_V and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._paste_clipboard()
            return True
        return False

    def _on_scene_focus_item_changed(self, new, _old, _reason) -> None:
        if isinstance(new, NoteNodeItem):
            self._last_text_edit_note = new

    def _apply_canvas_item_states(self, states: dict[object, dict[str, object]]) -> None:
        for it, st in states.items():
            try:
                if not hasattr(it, "scene"):
                    continue
                if it.scene() is not self._canvas.graphics_scene():
                    continue
                pos = st.get("pos")
                origin = st.get("origin")
                tr = st.get("transform")
                if isinstance(origin, QPointF):
                    it.setTransformOriginPoint(origin)
                if "scale" in st:
                    it.setScale(float(st["scale"]))
                if tr is not None:
                    it.setTransform(QTransform(tr))
                if isinstance(pos, QPointF):
                    it.setPos(pos)
                if "rotation" in st:
                    it.setRotation(float(st["rotation"]))
                if isinstance(it, ImageNodeItem):
                    crop = st.get("crop_rect")
                    if crop is None:
                        it.set_crop_rect(None)
                    elif isinstance(crop, QRectF):
                        it.set_crop_rect(QRectF(crop))
            except RuntimeError:
                # Ignore wrappers whose C++ object was already deleted.
                continue

    def _on_canvas_transform_history_committed(
        self,
        before_states: object,
        after_states: object,
        label: str,
    ) -> None:
        if not isinstance(before_states, dict) or not isinstance(after_states, dict):
            return

        def redo() -> None:
            self._apply_canvas_item_states(after_states)

        def undo() -> None:
            self._apply_canvas_item_states(before_states)

        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label=label))

    def _on_canvas_draw_item_committed(self, item_obj: object) -> None:
        if not isinstance(item_obj, DrawNodeItem):
            return
        scene = self._canvas.graphics_scene()
        item = item_obj

        def redo() -> None:
            try:
                if item.scene() is None:
                    scene.addItem(item)
            except RuntimeError:
                return

        def undo() -> None:
            try:
                if item.scene() is scene:
                    scene.removeItem(item)
            except RuntimeError:
                return

        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label="draw stroke"))

    def _on_canvas_draw_items_erased(self, items_obj: object) -> None:
        if not isinstance(items_obj, list):
            return
        items: list[DrawNodeItem] = [x for x in items_obj if isinstance(x, DrawNodeItem)]
        if not items:
            return
        scene = self._canvas.graphics_scene()

        def redo() -> None:
            for it in items:
                try:
                    if it.scene() is scene:
                        scene.removeItem(it)
                except RuntimeError:
                    continue

        def undo() -> None:
            for it in items:
                try:
                    if it.scene() is None:
                        scene.addItem(it)
                except RuntimeError:
                    continue

        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label="erase stroke"))

    def _on_layers_dialog_reorder_committed(
        self,
        before_z: object,
        after_z: object,
    ) -> None:
        if not isinstance(before_z, dict) or not isinstance(after_z, dict):
            return
        scene = self._canvas.graphics_scene()

        def apply_z(snapshot: dict[object, object]) -> None:
            for it, z in snapshot.items():
                try:
                    if hasattr(it, "scene") and it.scene() is scene:
                        it.setZValue(float(z))
                except RuntimeError:
                    continue
            self._canvas.viewport().update()

        def redo() -> None:
            apply_z(after_z)

        def undo() -> None:
            apply_z(before_z)

        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label="reorder layers"))

    def _on_layers_dialog_draw_layer_renamed(
        self,
        before_names: object,
        after_names: object,
        layer_name: str,
    ) -> None:
        if not isinstance(before_names, dict) or not isinstance(after_names, dict):
            return
        scene = self._canvas.graphics_scene()

        def apply_names(snapshot: dict[object, object]) -> None:
            for it, name in snapshot.items():
                if not isinstance(it, DrawNodeItem):
                    continue
                try:
                    if it.scene() is scene:
                        it.draw_layer_name = str(name) if isinstance(name, str) and name else None
                except RuntimeError:
                    continue

        def redo() -> None:
            apply_names(after_names)

        def undo() -> None:
            apply_names(before_names)

        self._history.push(
            HistoryEntry(do_redo=redo, undo=undo, label=f"rename {layer_name}")
        )

    def _on_layers_dialog_delete_layer_committed(self, snapshot_obj: object, label: str) -> None:
        if not isinstance(snapshot_obj, list) or not snapshot_obj:
            return
        scene = self._canvas.graphics_scene()
        snapshot = snapshot_obj

        def redo() -> None:
            for it, *_rest in snapshot:
                try:
                    if isinstance(it, NoteNodeItem):
                        it.finalize_text_edit_visual()
                    if hasattr(it, "scene") and it.scene() is scene:
                        scene.removeItem(it)
                except RuntimeError:
                    continue
            self._canvas.viewport().update()

        def undo() -> None:
            for it, pos, rot, z, sc, origin, tr in snapshot:
                try:
                    if hasattr(it, "scene") and it.scene() is None:
                        scene.addItem(it)
                    it.setTransformOriginPoint(origin)
                    it.setScale(sc)
                    it.setTransform(tr)
                    it.setPos(pos)
                    it.setRotation(rot)
                    it.setZValue(z)
                except RuntimeError:
                    continue
            self._canvas.viewport().update()

        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label=f"delete {label or 'layer'}"))

    def _dispatch_note_text_undo_redo(self, event: QKeyEvent) -> bool:
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if not ctrl:
            return False

        is_undo = event.key() == Qt.Key_Z and not shift
        is_redo = (event.key() == Qt.Key_Z and shift) or (event.key() == Qt.Key_Y and not shift)
        if not (is_undo or is_redo):
            return False

        scene = self._canvas.graphics_scene()
        focus_item = scene.focusItem()
        target: NoteNodeItem | None = None
        if (
            isinstance(focus_item, NoteNodeItem)
            and focus_item.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction
        ):
            target = focus_item
            self._last_text_edit_note = focus_item
        elif self._last_text_edit_note is not None:
            try:
                if self._last_text_edit_note.scene() is scene:
                    target = self._last_text_edit_note
            except RuntimeError:
                self._last_text_edit_note = None
                target = None

        if target is None:
            return False

        doc = target.document()
        if is_undo and doc.isUndoAvailable():
            doc.undo()
            return True
        if is_redo and doc.isRedoAvailable():
            doc.redo()
            return True
        return False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._dispatch_main_shortcuts(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def _paste_clipboard(self) -> None:
        cb = QGuiApplication.clipboard()
        assert cb is not None
        mime = cb.mimeData()
        if mime is None:
            return
        scene = self._canvas.graphics_scene()
        pos = self._canvas.mapToScene(self._canvas.viewport().rect().center())
        before = self._canvas.scene_image_count()
        if mime.hasImage():
            from PySide6.QtGui import QImage, QPixmap

            img = cb.image()
            if not img.isNull():
                pm = QPixmap.fromImage(img)
                it = ImageNodeItem(pm, None)
                it.setPos(pos)
                scene.addItem(it)
                self._canvas.finalize_new_images(before, [it])
        elif mime.hasText():
            t = mime.text().strip().strip('"')
            p = Path(t)
            if p.is_file():
                it = self._canvas.add_image_from_path(str(p), pos)
                if it is not None:
                    self._canvas.finalize_new_images(before, [it])

    def _fit_new_image_into_viewport_slot(self, it: object) -> None:
        if isinstance(it, ImageNodeItem):
            self._canvas.fit_new_image_into_viewport(it)

    def _global_title_bar_rect(self) -> QRect:
        tb = self._title_bar
        return QRect(tb.mapToGlobal(QPoint(0, 0)), tb.size())

    def _show_commands_context_menu_at(self, global_pos: QPoint) -> None:
        if self._global_title_bar_rect().contains(global_pos):
            return
        menu = QMenu(self)
        cmds = [c for c in self._registry.all() if c.id not in _CONTEXT_MENU_EXCLUDE_IDS]
        palette_cmd = next((c for c in cmds if c.id == "palette.open"), None)
        rest = [c for c in cmds if c.id != "palette.open"]
        if palette_cmd is not None:
            pal_act = QAction(palette_cmd.title, self)
            pal_act.triggered.connect(lambda: self._execute("palette.open"))
            menu.addAction(pal_act)
            menu.addSeparator()
        by_id = {c.id: c for c in cmds}
        for cmd in rest:
            if cmd.id in _CONTEXT_MENU_ALIGN_IDS:
                continue
            if cmd.id == "layout.pack":
                act_pack = QAction(cmd.title, self)
                act_pack.triggered.connect(lambda checked=False, cid=cmd.id: self._execute(cid))
                menu.addAction(act_pack)
                layers_cmd = by_id.get("layout.layers")
                if layers_cmd is not None:
                    layers_act = QAction(layers_cmd.title, self)
                    layers_act.triggered.connect(
                        lambda checked=False, cid=layers_cmd.id: self._execute(cid)
                    )
                    menu.addAction(layers_act)
                align_menu = menu.addMenu("Group Alignment")
                for aid in _CONTEXT_MENU_ALIGN_IDS_ORDER:
                    acmd = by_id.get(aid)
                    if acmd is not None:
                        sa = QAction(acmd.title, self)
                        sa.triggered.connect(lambda checked=False, cid=aid: self._execute(cid))
                        align_menu.addAction(sa)
                continue
            if cmd.id == "gif.pause":
                gif_menu = menu.addMenu("GIF")
                for gid in _CONTEXT_MENU_GIF_IDS_ORDER:
                    gcmd = by_id.get(gid)
                    if gcmd is not None:
                        ga = QAction(gcmd.title, self)
                        ga.triggered.connect(lambda checked=False, cid=gid: self._execute(cid))
                        gif_menu.addAction(ga)
                continue
            if cmd.id in _CONTEXT_MENU_GIF_IDS:
                continue
            if cmd.id == "layout.layers":
                continue
            if cmd.id == "draw.toggle":
                menu.addSeparator()
            if cmd.id == "note.new":
                menu.addSeparator()
            title = cmd.title
            if cmd.id == "note.new":
                title = f"{cmd.title} (double-click)"
            act = QAction(title, self)
            act.triggered.connect(lambda checked=False, cid=cmd.id: self._execute(cid))
            menu.addAction(act)
            if cmd.id == "draw.toggle":
                menu.addSeparator()
            if cmd.id == "note.new":
                font_act = QAction("Font Setting", self)
                font_act.triggered.connect(self._open_note_font_settings)
                menu.addAction(font_act)
            if cmd.id == "window.always_on_bottom":
                menu.addSeparator()
        menu.exec(global_pos)

    def contextMenuEvent(self, event) -> None:
        self._show_commands_context_menu_at(event.globalPos())

    def _selected_items(self) -> list:
        return list(self._canvas.graphics_scene().selectedItems())

    def _pack(self) -> None:
        scene = self._canvas.graphics_scene()
        images = [it for it in scene.items() if isinstance(it, ImageNodeItem)]
        if not images:
            return
        scene.clearSelection()
        for it in images:
            it.setSelected(True)
        bounds = self._canvas.viewport_scene_rect()
        old: list[tuple[ImageNodeItem, QPointF, float, QPointF]] = [
            (it, QPointF(it.pos()), float(it.scale()), QPointF(it.transformOriginPoint()))
            for it in images
        ]

        def redo() -> None:
            self._layout.pack_optimal_in_viewport(images, bounds)

        def undo() -> None:
            for it, p, sc, op in old:
                it.setPos(p)
                it.setScale(sc)
                it.setTransformOriginPoint(op)

        redo()
        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label="pack"))

    def _open_layers_dialog(self) -> None:
        scene = self._canvas.graphics_scene()
        has_layers = any(
            isinstance(it, (ImageNodeItem, NoteNodeItem, DrawNodeItem)) for it in scene.items()
        )
        if not has_layers:
            return
        dlg = LayersDialog(self._canvas, self)
        dlg.exec()

    def _align(self, direction: str) -> None:
        items = self._selected_items()
        if len(items) < 2:
            return
        old = [(it, QPointF(it.pos())) for it in items]

        def redo() -> None:
            self._layout.align(items, direction)

        def undo() -> None:
            for it, p in old:
                it.setPos(p)

        redo()
        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label=f"align {direction}"))

    def _group(self) -> None:
        items = self._selected_items()
        if len(items) < 2:
            return
        grp = GroupNodeItem()
        scene = self._canvas.graphics_scene()
        scene.addItem(grp)
        for it in items:
            grp.addToGroup(it)

    def _next_topmost_z(self) -> float:
        scene = self._canvas.graphics_scene()
        return max((it.zValue() for it in scene.items()), default=0.0) + 1.0

    def _new_note(self, scene_pos: QPointF | None = None) -> None:
        if scene_pos is None:
            pos = self._canvas.mapToScene(self._canvas.viewport().rect().center())
        else:
            pos = scene_pos
        note = NoteNodeItem("Note")
        self._note_appearance_defaults.apply_to(note)
        note.setPos(pos)
        scene = self._canvas.graphics_scene()
        note.setZValue(self._next_topmost_z())
        scene.clearSelection()
        scene.addItem(note)
        note.setSelected(True)

        def redo() -> None:
            if note.scene() is None:
                scene.addItem(note)
            scene.clearSelection()
            note.setSelected(True)

        def undo() -> None:
            note.finalize_text_edit_visual()
            if note.scene() is scene:
                scene.removeItem(note)

        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label="new note"))

    def _delete_selected_with_history(self) -> bool:
        scene = self._canvas.graphics_scene()
        selected = [
            it
            for it in scene.selectedItems()
            if it.parentItem() is None and isinstance(it, (ImageNodeItem, NoteNodeItem, DrawNodeItem))
        ]
        if not selected:
            return False

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
            for it in selected
        ]

        def redo() -> None:
            for it, *_rest in snapshot:
                if isinstance(it, NoteNodeItem):
                    it.finalize_text_edit_visual()
                if it.scene() is scene:
                    scene.removeItem(it)

        def undo() -> None:
            scene.clearSelection()
            for it, pos, rot, z, sc, origin, tr in snapshot:
                if it.scene() is None:
                    scene.addItem(it)
                it.setTransformOriginPoint(origin)
                it.setScale(sc)
                it.setTransform(tr)
                it.setPos(pos)
                it.setRotation(rot)
                it.setZValue(z)
                it.setSelected(True)

        redo()
        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label="delete selected"))
        return True

    def _select_all(self) -> None:
        scene = self._canvas.graphics_scene()
        for it in scene.items():
            if isinstance(it, ImageNodeItem):
                it.setSelected(True)

    def _toggle_draw(self) -> None:
        self._canvas.draw_mode = not self._canvas.draw_mode

    def _export_scene(self) -> None:
        path_str, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Scene",
            "",
            "PNG (*.png);;JPEG (*.jpg *.jpeg)",
        )
        if not path_str:
            return
        path = Path(path_str)
        suf = path.suffix.lower()
        if suf not in (".png", ".jpg", ".jpeg"):
            filt = selected_filter.upper()
            path = path.with_suffix(".jpg" if "JPEG" in filt else ".png")
        ExportService.export_scene(self._canvas.graphics_scene(), path)

    def _gif_pause(self) -> None:
        for it in self._selected_items():
            if isinstance(it, ImageNodeItem):
                it.gif_pause()

    def _gif_resume(self) -> None:
        for it in self._selected_items():
            if isinstance(it, ImageNodeItem):
                it.gif_resume()

    def _gif_step(self, delta: int) -> None:
        for it in self._selected_items():
            if isinstance(it, ImageNodeItem) and it._movie is not None:
                m = it._movie
                fc = m.frameCount()
                if fc <= 0:
                    continue
                idx = m.currentFrameNumber() + delta
                idx = max(0, min(idx, fc - 1))
                m.jumpToFrame(idx)
