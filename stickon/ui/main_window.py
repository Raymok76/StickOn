from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QCoreApplication,
    QDateTime,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QStandardPaths,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
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
    QPainter,
    QPixmap,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGraphicsItem,
    QGraphicsView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionGraphicsItem,
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
from stickon.services.io_service import save_pur
from stickon.services.layout_service import LayoutService
from stickon.services.project_service import load_scene_from_path, save_scene_to_path, scene_to_pur_data
from stickon.services.session_service import (
    autosession_path,
    legacy_autosession_path,
    resolved_autosession_path_for_read,
)
from stickon.ui.canvas_view import CanvasView, _visual_item_bounds
from stickon.ui.font_settings_dialog import FontSettingsDialog
from stickon.ui.command_palette import (
    CommandPaletteDialog,
    RecordShortcutDialog,
    stored_shortcut_chord_only,
)
from stickon.ui.image_overlay_window import ImageOverlayWindow
from stickon.ui.layers_dialog import LayersDialog
from stickon.ui.title_bar import DraggableTitleBar, ToggleChipLabel, _BAR_BG, _CHIP_RADIUS

_OVERLAY_SELECTION_TYPES = (ImageNodeItem, NoteNodeItem, DrawNodeItem)


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
        "scene.clear_all",
        "node.group",
    }
)

_CONTEXT_MENU_SHORTCUT_SUFFIX_IDS = frozenset(
    {
        "edit.paste_clipboard",
    }
)

# Built-in only: Command Key / shortcut_overrides cannot reassign these (wheel is separate, canvas).
_NON_CUSTOMIZABLE_COMMAND_IDS = frozenset(
    {
        "edit.paste_clipboard",
        "window.opacity_up",
        "window.opacity_down",
        "note.new",
    }
)

_PASTE_CLIPBOARD_CMD_ID = "edit.paste_clipboard"
_PASTE_CLIPBOARD_SHORTCUT_FIXED = "Ctrl+V"

_OPACITY_UP_CMD_ID = "window.opacity_up"
_OPACITY_DOWN_CMD_ID = "window.opacity_down"
_OPACITY_UP_SHORTCUT_LABEL = "Ctrl+Shift++"
_OPACITY_DOWN_SHORTCUT_LABEL = "Ctrl+Shift+-"
_OPACITY_UP_ALIASES = frozenset({_OPACITY_UP_SHORTCUT_LABEL, "Ctrl+Shift+="})

_NOTE_NEW_CMD_ID = "note.new"
_NOTE_NEW_SHORTCUT_FIXED = "Ctrl+N"


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
        self._btn_clear_all = QPushButton("Clear all", root)
        self._btn_clear_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_clear_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear_all.clicked.connect(self._on_clear_all_clicked)
        self._btn_clear_all.setStyleSheet(
            f"QPushButton {{ background-color: {_BAR_BG}; border: 1px solid white; "
            f"border-radius: {r}px; padding: 4px 12px; color: #333; }}"
            f"QPushButton:hover {{ background-color: #ffe0e0; }}"
            f"QPushButton:pressed {{ background-color: #ffcccc; border: 1px solid white; }}"
        )
        self._stickon_maximized = False
        self._geom_before_stickon_max = QRect()
        self._title_bar = DraggableTitleBar(
            self,
            [self._seg_lock, self._seg_through, self._btn_fit_content],
            trailing_widgets=[self._btn_clear_all],
            parent=root,
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
        self._saved_scene_background_brush: QBrush | None = None
        self._saved_view_background_brush: QBrush | None = None
        self._image_overlay_windows: list[ImageOverlayWindow] = []
        self._delete_shortcut_suppressed = False
        self._global_shortcuts_suppressed = False
        self._file_dialog_guard_release = QTimer(self)
        self._file_dialog_guard_release.setSingleShot(True)
        self._file_dialog_guard_release.timeout.connect(self._clear_file_dialog_shortcut_guard)
        self._post_save_scene_guard = QTimer(self)
        self._post_save_scene_guard.setSingleShot(False)
        self._post_save_scene_guard.setInterval(250)
        self._post_save_scene_guard.timeout.connect(self._on_post_save_scene_guard_tick)
        self._post_save_scene_guard_deadline = 0
        self._post_save_manifest_guard = QTimer(self)
        self._post_save_manifest_guard.setSingleShot(False)
        self._post_save_manifest_guard.setInterval(250)
        self._post_save_manifest_guard.timeout.connect(self._on_post_save_manifest_guard_tick)
        self._post_save_manifest_guard_deadline = 0
        self._post_save_manifest_snapshot: dict[str, Any] | None = None
        self._post_save_blobs_snapshot: dict[str, bytes] | None = None
        self._post_save_layer_snapshot: list[
            tuple[
                object,
                QPointF,
                float,
                float,
                float,
                QPointF,
                QTransform,
                GroupNodeItem | None,
            ]
        ] = []

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
        self._close_all_image_overlays()
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
            try:
                leg = legacy_autosession_path()
                if leg.is_file():
                    leg.unlink()
            except OSError:
                pass
        except Exception:
            # Autosave must not prevent shutdown (e.g. older PySide APIs, disk errors).
            pass
        super().closeEvent(event)

    def _restore_autosession_if_any(self) -> None:
        path = resolved_autosession_path_for_read()
        if path is None:
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

    def _on_clear_all_clicked(self) -> None:
        reply = QMessageBox.question(
            self,
            "Clear all",
            "Clear all images and layers (notes and drawings)?\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._clear_all_scene_layers()

    def _clear_all_scene_layers(self) -> None:
        self._close_all_image_overlays()
        self._disarm_post_save_scene_guard()
        self._last_text_edit_note = None
        self._canvas.reset_pointer_interaction_state()
        scene = self._canvas.graphics_scene()
        scene.clearSelection()
        persist_types = (ImageNodeItem, NoteNodeItem, DrawNodeItem, GroupNodeItem)
        for it in list(scene.items()):
            if not isinstance(it, persist_types) or it.parentItem() is not None:
                continue
            if isinstance(it, NoteNodeItem):
                it.finalize_text_edit_visual()
            if isinstance(it, ImageNodeItem):
                it.set_gif_movie(None)
            scene.removeItem(it)
        self._history.clear()
        self._canvas.viewport().update()
        self._schedule_fit_window_to_content()

    def _register_commands(self) -> None:
        def reg(
            cid: str,
            fn: Callable[[dict[str, Any]], None],
            *,
            is_checked: Callable[[], bool] | None = None,
            palette: bool = True,
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
                    palette=palette,
                )
            )

        # Registration order is CommandRegistry.all() iteration order (palette, Layers dialog).
        # Context menu uses .all() minus _CONTEXT_MENU_EXCLUDE_IDS — list window actions that appear
        # there first (separator is drawn after Always on Bottom in _show_commands_context_menu_at).

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
        reg("edit.paste_clipboard", lambda ctx: self._paste_clipboard_from_command(), palette=False)
        reg("window.opacity_up", lambda ctx: self._win_state.adjust_opacity(0.08))
        reg("window.opacity_down", lambda ctx: self._win_state.adjust_opacity(-0.08))
        reg(
            "window.click_through",
            lambda ctx: self._win_state.toggle_click_through(),
            is_checked=lambda: self._win_state.click_through,
        )
        reg(
            "window.lock",
            lambda ctx: self._win_state.toggle_lock_position(),
            is_checked=lambda: self._win_state.lock_position,
        )
        reg("window.click_through_off", lambda ctx: self._win_state.set_click_through(False))
        reg("window.fit_content", lambda ctx: self._on_fit_content_clicked())
        reg("scene.clear_all", lambda ctx: self._on_clear_all_clicked())
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
        reg("overlay.selection", lambda ctx: self._overlay_selection())
        reg("edit.undo", lambda ctx: self._history.undo())
        reg("edit.redo", lambda ctx: self._history.redo())
        reg("edit.select_all", lambda ctx: self._select_all())
        reg("export.scene", lambda ctx: self._export_scene())
        reg("export.selection", lambda ctx: self._export_selected_layers())
        reg("project.save", lambda ctx: self._save_project())
        reg("project.load", lambda ctx: self._load_project())
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
                if k in _NON_CUSTOMIZABLE_COMMAND_IDS:
                    continue
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
        self._enforce_fixed_command_shortcuts(shortcut_map)
        return shortcut_map

    @staticmethod
    def _enforce_fixed_command_shortcuts(shortcut_map: dict[str, str]) -> None:
        for k, v in list(shortcut_map.items()):
            if v == _PASTE_CLIPBOARD_CMD_ID and k != _PASTE_CLIPBOARD_SHORTCUT_FIXED:
                del shortcut_map[k]
        shortcut_map[_PASTE_CLIPBOARD_SHORTCUT_FIXED] = _PASTE_CLIPBOARD_CMD_ID

        for k, v in list(shortcut_map.items()):
            if v == _OPACITY_UP_CMD_ID and k not in _OPACITY_UP_ALIASES:
                del shortcut_map[k]
        for chord in _OPACITY_UP_ALIASES:
            shortcut_map[chord] = _OPACITY_UP_CMD_ID

        for k, v in list(shortcut_map.items()):
            if v == _OPACITY_DOWN_CMD_ID and k != _OPACITY_DOWN_SHORTCUT_LABEL:
                del shortcut_map[k]
        shortcut_map[_OPACITY_DOWN_SHORTCUT_LABEL] = _OPACITY_DOWN_CMD_ID

        for k, v in list(shortcut_map.items()):
            if v == _NOTE_NEW_CMD_ID and k != _NOTE_NEW_SHORTCUT_FIXED:
                del shortcut_map[k]
        shortcut_map[_NOTE_NEW_SHORTCUT_FIXED] = _NOTE_NEW_CMD_ID

    def _sync_command_shortcut_labels_from_map(self) -> None:
        meta_by_id = self._meta.by_id()
        for cmd in self._registry.all():
            if cmd.id == _PASTE_CLIPBOARD_CMD_ID:
                cmd.shortcut = _PASTE_CLIPBOARD_SHORTCUT_FIXED
                continue
            if cmd.id == _OPACITY_UP_CMD_ID:
                cmd.shortcut = _OPACITY_UP_SHORTCUT_LABEL
                continue
            if cmd.id == _OPACITY_DOWN_CMD_ID:
                cmd.shortcut = _OPACITY_DOWN_SHORTCUT_LABEL
                continue
            if cmd.id == _NOTE_NEW_CMD_ID:
                cmd.shortcut = _NOTE_NEW_SHORTCUT_FIXED
                continue
            if cmd.id in self._shortcut_overrides:
                cmd.shortcut = stored_shortcut_chord_only(cmd.id, self._shortcut_overrides[cmd.id])
                continue
            entry = meta_by_id.get(cmd.id, {})
            sc = entry.get("shortcut")
            raw = str(sc).strip() if sc else ""
            cmd.shortcut = stored_shortcut_chord_only(cmd.id, raw) or None

    def _apply_shortcut_override(self, command_id: str, portable: str) -> None:
        if command_id in _NON_CUSTOMIZABLE_COMMAND_IDS:
            return
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
        self._shortcut_overrides = {
            k: v for k, v in dict(overrides).items() if k not in _NON_CUSTOMIZABLE_COMMAND_IDS
        }
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
        if command_id == _PASTE_CLIPBOARD_CMD_ID:
            QMessageBox.information(
                self,
                "Shortcut fixed",
                "Paste from clipboard is always Ctrl+V and cannot be reassigned.",
            )
            return
        if command_id == _OPACITY_UP_CMD_ID:
            QMessageBox.information(
                self,
                "Shortcuts fixed",
                "Opacity Up is always Ctrl+Shift++ (Ctrl+Shift+= on some keyboards) and Ctrl+wheel up; "
                "those shortcuts cannot be reassigned.",
            )
            return
        if command_id == _OPACITY_DOWN_CMD_ID:
            QMessageBox.information(
                self,
                "Shortcuts fixed",
                "Opacity Down is always Ctrl+Shift+- and Ctrl+wheel down; those shortcuts cannot be reassigned.",
            )
            return
        if command_id == _NOTE_NEW_CMD_ID:
            QMessageBox.information(
                self,
                "Shortcuts fixed",
                "New Note is always Ctrl+N. Creating a note by double-clicking the canvas is fixed and "
                "cannot be reassigned.",
            )
            return
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

    def _purge_invisible_overlay_windows(self) -> None:
        """Drop closed overlays from tracking; close() clears isVisible before destroyed fires."""
        try:
            before = list(self._image_overlay_windows)
        except RuntimeError:
            self._image_overlay_windows = []
            return
        survivors: list[ImageOverlayWindow] = []
        for w in before:
            try:
                if w.isVisible():
                    survivors.append(w)
            except RuntimeError:
                continue
        self._image_overlay_windows = survivors

    def _dismiss_image_overlays_with_ctrl_o_hotkey(self, event: QKeyEvent) -> bool:
        if not self._image_overlay_windows:
            return False
        if event.key() != Qt.Key_O:
            return False
        m = event.modifiers()
        if not (m & Qt.KeyboardModifier.ControlModifier):
            return False
        if m & (
            Qt.KeyboardModifier.ShiftModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        ):
            return False
        self._close_all_image_overlays()
        return True

    def _refocus_canvas_after_overlay_spawn(self) -> None:
        try:
            self.raise_()
            self.activateWindow()
            self._canvas.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        except RuntimeError:
            pass

    def _dispatch_main_shortcuts(self, event: QKeyEvent) -> bool:
        self._purge_invisible_overlay_windows()
        self._overlay_restore_canvas_background_if_idle()
        if self._dispatch_note_text_undo_redo(event):
            return True
        if self.shortcuts_temporarily_suppressed():
            mods = event.modifiers()
            has_global_mod = bool(
                mods
                & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.AltModifier
                    | Qt.KeyboardModifier.MetaModifier
                )
            )
            if event.key() == Qt.Key_Delete or has_global_mod:
                return True

        if (
            event.key() == Qt.Key_Escape
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and self._image_overlay_windows
        ):
            self._close_all_image_overlays()
            return True

        if self._dismiss_image_overlays_with_ctrl_o_hotkey(event):
            return True

        if (
            event.key() == Qt.Key_V
            and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            and self._scene_note_in_text_edit()
        ):
            return False

        if (
            event.key() == Qt.Key_Delete
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            if self.delete_shortcuts_suppressed():
                return True
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

    def _scene_note_in_text_edit(self) -> bool:
        fi = self._canvas.graphics_scene().focusItem()
        return (
            isinstance(fi, NoteNodeItem)
            and fi.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction
        )

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

    def _paste_clipboard_from_command(self) -> None:
        if self._scene_note_in_text_edit():
            return
        self._paste_clipboard()

    def _paste_clipboard(self) -> None:
        cb = QGuiApplication.clipboard()
        assert cb is not None
        mime = cb.mimeData()
        if mime is None:
            return
        scene = self._canvas.graphics_scene()
        before_scene_items = set(scene.items())
        pos = self._canvas.mapToScene(self._canvas.viewport().rect().center())
        self._canvas.apply_drop_mime(mime, pos, clipboard=cb)
        pasted_images = [
            it
            for it in scene.items()
            if isinstance(it, ImageNodeItem) and it not in before_scene_items
        ]
        if pasted_images:
            self._push_paste_history(pasted_images, "paste image")
            return

        if not mime.hasText():
            return
        text = mime.text()
        if not text or not text.strip():
            return
        note = self._create_note_from_text(text, pos)
        self._push_paste_history([note], "paste note")

    def _create_note_from_text(self, text: str, pos: QPointF) -> NoteNodeItem:
        scene = self._canvas.graphics_scene()
        note = NoteNodeItem(text)
        self._note_appearance_defaults.apply_to(note)
        note.setPos(pos)
        note.setZValue(self._next_topmost_z())
        scene.clearSelection()
        scene.addItem(note)
        note.setSelected(True)
        return note

    def _push_paste_history(
        self,
        items: list[ImageNodeItem | NoteNodeItem],
        label: str,
    ) -> None:
        scene = self._canvas.graphics_scene()
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
            for it in items
        ]

        def redo() -> None:
            scene.clearSelection()
            for it, pos, rot, z, sc, origin, tr in snapshot:
                try:
                    if it.scene() is None:
                        scene.addItem(it)
                    it.setTransformOriginPoint(origin)
                    it.setScale(sc)
                    it.setTransform(tr)
                    it.setPos(pos)
                    it.setRotation(rot)
                    it.setZValue(z)
                    it.setSelected(True)
                except RuntimeError:
                    continue

        def undo() -> None:
            for it, *_rest in snapshot:
                try:
                    if isinstance(it, NoteNodeItem):
                        it.finalize_text_edit_visual()
                    if it.scene() is scene:
                        scene.removeItem(it)
                except RuntimeError:
                    continue

        self._history.push(HistoryEntry(do_redo=redo, undo=undo, label=label))

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
        has_overlay_candidate = any(
            isinstance(x, _OVERLAY_SELECTION_TYPES) for x in self._selected_items()
        )
        has_image_selected = self._has_image_node_in_selection()
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
                layers_cmd = by_id.get("layout.layers")
                if layers_cmd is not None:
                    layers_act = QAction(layers_cmd.title, self)
                    layers_act.triggered.connect(
                        lambda checked=False, cid=layers_cmd.id: self._execute(cid)
                    )
                    menu.addAction(layers_act)
                act_pack = QAction(cmd.title, self)
                act_pack.triggered.connect(lambda checked=False, cid=cmd.id: self._execute(cid))
                act_pack.setEnabled(has_image_selected)
                menu.addAction(act_pack)
                align_menu = menu.addMenu("Group Alignment")
                align_menu.setEnabled(has_image_selected)
                for aid in _CONTEXT_MENU_ALIGN_IDS_ORDER:
                    acmd = by_id.get(aid)
                    if acmd is not None:
                        sa = QAction(acmd.title, self)
                        sa.triggered.connect(lambda checked=False, cid=aid: self._execute(cid))
                        align_menu.addAction(sa)
                continue
            if cmd.id in _CONTEXT_MENU_GIF_IDS:
                continue
            if cmd.id == "layout.layers":
                continue
            if cmd.id == "project.save":
                menu.addSeparator()
            if cmd.id == "draw.toggle":
                menu.addSeparator()
            if cmd.id == _NOTE_NEW_CMD_ID:
                menu.addSeparator()
            title = cmd.title
            if cmd.id == _NOTE_NEW_CMD_ID:
                title = f"{cmd.title} (double-click)"
            elif cmd.id == _OPACITY_UP_CMD_ID:
                title = f"{cmd.title} (Ctrl+wheel up)"
            elif cmd.id == _OPACITY_DOWN_CMD_ID:
                title = f"{cmd.title} (Ctrl+wheel down)"
            elif cmd.id in _CONTEXT_MENU_SHORTCUT_SUFFIX_IDS and cmd.shortcut:
                title = f"{cmd.title} ({cmd.shortcut})"
            act = QAction(title, self)
            act.triggered.connect(lambda checked=False, cid=cmd.id: self._execute(cid))
            menu.addAction(act)
            if cmd.id == "overlay.selection":
                act.setEnabled(has_overlay_candidate)
            if cmd.id == "export.selection":
                act.setEnabled(self._has_exportable_selection())
            if cmd.id == "edit.undo":
                act.setEnabled(self._history.can_undo())
            if cmd.id == "edit.redo":
                act.setEnabled(self._history.can_redo())
            if cmd.id == "draw.toggle":
                has_gif_selected = self._has_gif_image_in_selection()
                gif_menu = menu.addMenu("GIF")
                gif_menu.setEnabled(has_gif_selected)
                for gid in _CONTEXT_MENU_GIF_IDS_ORDER:
                    gcmd = by_id.get(gid)
                    if gcmd is not None:
                        ga = QAction(gcmd.title, self)
                        ga.triggered.connect(lambda checked=False, cid=gid: self._execute(cid))
                        gif_menu.addAction(ga)
                menu.addSeparator()
            if cmd.id == _NOTE_NEW_CMD_ID:
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

    def _has_image_node_in_selection(self) -> bool:
        return any(isinstance(x, ImageNodeItem) for x in self._selected_items())

    def _has_gif_image_in_selection(self) -> bool:
        return any(
            isinstance(x, ImageNodeItem) and x._movie is not None for x in self._selected_items()
        )

    def _scene_item_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for it in self._canvas.graphics_scene().items():
            name = type(it).__name__
            counts[name] = counts.get(name, 0) + 1
        return counts

    def _capture_layer_snapshot(
        self,
    ) -> list[
        tuple[
            object,
            QPointF,
            float,
            float,
            float,
            QPointF,
            QTransform,
            GroupNodeItem | None,
        ]
    ]:
        scene = self._canvas.graphics_scene()
        persist_types = (ImageNodeItem, NoteNodeItem, DrawNodeItem, GroupNodeItem)
        return [
            (
                it,
                QPointF(it.pos()),
                float(it.rotation()),
                float(it.zValue()),
                float(it.scale()),
                QPointF(it.transformOriginPoint()),
                QTransform(it.transform()),
                it.parentItem() if isinstance(it.parentItem(), GroupNodeItem) else None,
            )
            for it in scene.items()
            if isinstance(it, persist_types)
        ]

    def _restore_layers_from_snapshot_if_missing(
        self,
        snapshot: list[
            tuple[
                object,
                QPointF,
                float,
                float,
                float,
                QPointF,
                QTransform,
                GroupNodeItem | None,
            ]
        ],
    ) -> bool:
        """Attempt to restore missing items from snapshot. Returns True if all items restored."""
        if not snapshot:
            return True
        live_scene = self._canvas.graphics_scene()
        snapshot_ids = {
            getattr(it, "node_id", None)
            for it, *_rest in snapshot
            if isinstance(getattr(it, "node_id", None), str)
        }
        persist_types = (ImageNodeItem, NoteNodeItem, DrawNodeItem, GroupNodeItem)
        live_ids = {
            getattr(it, "node_id")
            for it in live_scene.items()
            if isinstance(it, persist_types) and isinstance(getattr(it, "node_id", None), str)
        }
        missing_ids = snapshot_ids - live_ids
        if not missing_ids:
            return True
        restored_count = 0
        for it, pos, rot, z, sc, origin, tr, parent_group in snapshot:
            nid = getattr(it, "node_id", None)
            if not isinstance(nid, str) or nid not in missing_ids:
                continue
            try:
                if hasattr(it, "scene") and it.scene() is None:
                    live_scene.addItem(it)
                    if it.scene() is not live_scene:
                        continue
                if hasattr(it, "setTransformOriginPoint"):
                    it.setTransformOriginPoint(origin)
                if hasattr(it, "setScale"):
                    it.setScale(sc)
                if hasattr(it, "setTransform"):
                    it.setTransform(tr)
                if hasattr(it, "setPos"):
                    it.setPos(pos)
                if hasattr(it, "setRotation"):
                    it.setRotation(rot)
                if hasattr(it, "setZValue"):
                    it.setZValue(z)
                if (
                    parent_group is not None
                    and parent_group.scene() is live_scene
                    and hasattr(it, "parentItem")
                    and it.parentItem() is None
                ):
                    parent_group.addToGroup(it)
                restored_count += 1
            except RuntimeError:
                continue
        return restored_count == len(missing_ids)

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
        sel = list(scene.selectedItems())
        groups_sel = {it for it in sel if isinstance(it, GroupNodeItem)}
        selected: list = []
        for it in sel:
            if isinstance(it, GroupNodeItem):
                selected.append(it)
        for it in sel:
            if not isinstance(it, (ImageNodeItem, NoteNodeItem, DrawNodeItem)):
                continue
            p = it.parentItem()
            under_selected_group = False
            while p is not None:
                if isinstance(p, GroupNodeItem) and p in groups_sel:
                    under_selected_group = True
                    break
                p = p.parentItem()
            if not under_selected_group:
                selected.append(it)
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
                if isinstance(it, ImageNodeItem):
                    it.set_gif_movie(None)
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

    @staticmethod
    def _overlay_item_local_paint_rect(it: QGraphicsItem) -> QRectF:
        vb = QRectF(_visual_item_bounds(it))
        if isinstance(it, DrawNodeItem):
            ew = float(it.pen().widthF()) * 0.52 + 1.75
            vb.adjust(-ew, -ew, ew, ew)
        vb = vb.normalized()
        if vb.width() < 1e-3:
            vb.setWidth(1.0)
        if vb.height() < 1e-3:
            vb.setHeight(1.0)
        return vb

    @staticmethod
    def _overlay_scene_geometry_rect(it: QGraphicsItem) -> QRectF:
        if isinstance(it, ImageNodeItem):
            br = it.pixmapBoundingRect()
            cr = it.crop_rect
            if cr is not None and cr.isValid():
                br = cr.intersected(br)
            if br.isEmpty() or not br.isValid():
                br = it.pixmapBoundingRect()
            return it.mapRectToScene(br)
        return it.mapRectToScene(_visual_item_bounds(it))

    @staticmethod
    def _overlay_items_sorted_paint_order(items: list[QGraphicsItem]) -> list[QGraphicsItem]:
        """Ascending z-order: lower layers first — later overlay windows raised on top of earlier."""

        def _nid(obj: QGraphicsItem) -> str:
            nid = getattr(obj, "node_id", None)
            return nid if isinstance(nid, str) else ""

        return sorted(items, key=lambda i: (i.zValue(), _nid(i)))

    def _overlay_viewport_geometry_for_item(self, it: QGraphicsItem) -> QRectF:
        scene_r = MainWindow._overlay_scene_geometry_rect(it)
        poly = self._canvas.mapFromScene(scene_r)
        return QRectF(poly.boundingRect()).normalized()

    def _overlay_screen_geometries_matching_canvas_layout(
        self,
        ordered_items: list[QGraphicsItem],
        avail: QRect,
    ) -> list[QRect]:
        layout = [self._overlay_viewport_geometry_for_item(it) for it in ordered_items]
        U = layout[0]
        for r in layout[1:]:
            U = U.united(r)
        if U.width() < 1.0:
            U.setWidth(1.0)
        if U.height() < 1.0:
            U.setHeight(1.0)

        margin = 8
        inner_w = max(1.0, float(avail.width() - 2 * margin))
        inner_h = max(1.0, float(avail.height() - 2 * margin))
        scale = min(inner_w / U.width(), inner_h / U.height())

        scaled_w = U.width() * scale
        scaled_h = U.height() * scale
        origin_x = float(avail.x() + margin) + (inner_w - scaled_w) * 0.5 - U.left() * scale
        origin_y = float(avail.y() + margin) + (inner_h - scaled_h) * 0.5 - U.top() * scale

        geoms: list[QRect] = []
        for r in layout:
            x = int(round(origin_x + r.left() * scale))
            y = int(round(origin_y + r.top() * scale))
            w = max(32, int(round(r.width() * scale)))
            h = max(32, int(round(r.height() * scale)))
            g = QRect(x, y, w, h).intersected(avail)
            if g.width() < 32 or g.height() < 32:
                g = QRect(x, y, w, h)
            geoms.append(g)
        return geoms

    @staticmethod
    def _pixmap_and_gif_for_overlay(it: ImageNodeItem) -> tuple[QPixmap, str | None]:
        pm = it.pixmap()
        cr = it.crop_rect
        if cr is not None and cr.isValid():
            br = it.pixmapBoundingRect()
            inter = cr.intersected(br)
            r = inter.toAlignedRect()
            if r.width() > 0 and r.height() > 0:
                pm = pm.copy(r)
        src = it.source_path
        gif = src if src and Path(src).suffix.lower() == ".gif" else None
        return pm, gif

    def _overlay_render_item_snapshot(self, it: QGraphicsItem) -> QPixmap:
        scene = it.scene()
        if scene is None:
            return QPixmap()
        if isinstance(it, NoteNodeItem):
            it.finalize_text_edit_visual()

        was_sel = it.isSelected()
        if was_sel:
            it.setSelected(False)

        try:
            # Render only this item onto a transparent buffer so semi-transparent UI
            # layers do not bake in whatever sits behind them on the canvas.
            r = MainWindow._overlay_item_local_paint_rect(it)
            w_pix = max(32, min(8192, int(math.ceil(r.width()))))
            h_pix = max(32, min(8192, int(math.ceil(r.height()))))
            pm = QPixmap(w_pix, h_pix)
            pm.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.translate(-r.left(), -r.top())

            opt = QStyleOptionGraphicsItem()
            opt.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Active
            opt.rect = r.toAlignedRect()
            opt.exposedRect = QRectF(r)

            it.paint(painter, opt, None)
            painter.end()
            return pm
        finally:
            if was_sel:
                it.setSelected(True)

    def _overlay_pixmap_payload(self, it: QGraphicsItem) -> tuple[QPixmap, str | None]:
        if isinstance(it, ImageNodeItem):
            return MainWindow._pixmap_and_gif_for_overlay(it)
        pm = self._overlay_render_item_snapshot(it)
        return pm, None

    def _overlay_target_available_geometry(self) -> QRect:
        g = self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))
        screen = QGuiApplication.screenAt(g)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QRect(0, 0, 1200, 800)
        return screen.availableGeometry()

    def _overlay_apply_transparent_canvas(self) -> bool:
        self._purge_invisible_overlay_windows()
        self._overlay_restore_canvas_background_if_idle()
        if self._image_overlay_windows:
            return True
        try:
            self._saved_scene_background_brush = QBrush(self._canvas.graphics_scene().backgroundBrush())
            self._saved_view_background_brush = QBrush(self._canvas.backgroundBrush())
            transparent = QColor(0, 0, 0, 0)
            self._canvas.graphics_scene().setBackgroundBrush(transparent)
            self._canvas.setBackgroundBrush(transparent)
            self._canvas.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._canvas.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._canvas.viewport().setAutoFillBackground(False)
        except RuntimeError:
            self._saved_scene_background_brush = None
            self._saved_view_background_brush = None
            return False
        return True

    def _overlay_restore_canvas_background_if_idle(self) -> None:
        if self._image_overlay_windows:
            return

        had_saved = (
            self._saved_scene_background_brush is not None
            or self._saved_view_background_brush is not None
        )
        if not had_saved:
            return

        try:
            if self._saved_scene_background_brush is not None:
                self._canvas.graphics_scene().setBackgroundBrush(self._saved_scene_background_brush)
            if self._saved_view_background_brush is not None:
                self._canvas.setBackgroundBrush(self._saved_view_background_brush)
        except RuntimeError:
            pass
        finally:
            self._saved_scene_background_brush = None
            self._saved_view_background_brush = None

        try:
            self._canvas.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            self._canvas.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        except RuntimeError:
            pass

    def _overlay_selection(self) -> None:
        scene = self._canvas.graphics_scene()
        items = [
            it
            for it in scene.selectedItems()
            if isinstance(it, _OVERLAY_SELECTION_TYPES)
        ]
        if not items:
            return

        ordered = MainWindow._overlay_items_sorted_paint_order(items)
        placements: list[tuple[QGraphicsItem, QPixmap, str | None]] = []
        for it in ordered:
            pm, gif_path = self._overlay_pixmap_payload(it)
            if pm.isNull():
                continue
            placements.append((it, pm, gif_path))
        if not placements:
            return

        if not self._overlay_apply_transparent_canvas():
            return

        avail = self._overlay_target_available_geometry()
        layout_items = [p[0] for p in placements]
        geoms = self._overlay_screen_geometries_matching_canvas_layout(layout_items, avail)

        for (_, pm, gif_path), geom in zip(placements, geoms, strict=True):
            win = ImageOverlayWindow(pm, gif_path, self)
            win.setGeometry(geom)
            self._image_overlay_windows.append(win)
            win.destroyed.connect(
                lambda *_, w_ref=win: self._on_image_overlay_window_destroyed(w_ref)
            )
            win.show()

        QTimer.singleShot(0, self._refocus_canvas_after_overlay_spawn)

    def _on_image_overlay_window_destroyed(self, win: ImageOverlayWindow) -> None:
        try:
            self._image_overlay_windows.remove(win)
        except ValueError:
            pass
        self._purge_invisible_overlay_windows()
        self._overlay_restore_canvas_background_if_idle()

    def _close_all_image_overlays(self) -> None:
        for w in list(self._image_overlay_windows):
            try:
                w.close()
            except RuntimeError:
                pass
        self._purge_invisible_overlay_windows()
        self._overlay_restore_canvas_background_if_idle()
        QTimer.singleShot(0, self._refocus_canvas_after_overlay_spawn)

    def _export_scene(self) -> None:
        scene = self._canvas.graphics_scene()
        pre_layers = self._capture_layer_snapshot()
        pre_manifest, pre_blobs = scene_to_pur_data(scene)
        scene.clearSelection()
        self._begin_file_dialog_shortcut_guard()
        try:
            path_str, selected_filter = QFileDialog.getSaveFileName(
                self,
                "Export Scene",
                "",
                "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp)",
            )
        finally:
            self._end_file_dialog_shortcut_guard()
        self._arm_post_save_manifest_guard(pre_manifest, pre_blobs, duration_ms=8000)
        if not self._restore_layers_from_snapshot_if_missing(pre_layers):
            self._restore_scene_from_manifest_snapshot_if_wiped()
        if not path_str:
            self._arm_post_scene_guard_from_snapshot(pre_layers, duration_ms=7000)
            return
        path = Path(path_str)
        suf = path.suffix.lower()
        if suf not in (".png", ".jpg", ".jpeg", ".bmp"):
            filt = selected_filter.upper()
            if "JPEG" in filt:
                path = path.with_suffix(".jpg")
            elif "BMP" in filt:
                path = path.with_suffix(".bmp")
            else:
                path = path.with_suffix(".png")
        ExportService.export_scene(scene, path)
        if not self._restore_layers_from_snapshot_if_missing(pre_layers):
            self._restore_scene_from_manifest_snapshot_if_wiped()
        self._arm_post_scene_guard_from_snapshot(pre_layers, duration_ms=7000)

    def _exportable_selection(self) -> list:
        return [
            it
            for it in self._selected_items()
            if isinstance(it, (ImageNodeItem, NoteNodeItem, DrawNodeItem, GroupNodeItem))
        ]

    def _has_exportable_selection(self) -> bool:
        return bool(self._exportable_selection())

    def _export_selected_layers(self) -> None:
        scene = self._canvas.graphics_scene()
        pre_layers = self._capture_layer_snapshot()
        pre_manifest, pre_blobs = scene_to_pur_data(scene)
        targets = self._exportable_selection()
        if not targets:
            return
        self._begin_file_dialog_shortcut_guard()
        try:
            path_str, selected_filter = QFileDialog.getSaveFileName(
                self,
                "Export Selected",
                "",
                "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp)",
            )
        finally:
            self._end_file_dialog_shortcut_guard()
        self._arm_post_save_manifest_guard(pre_manifest, pre_blobs, duration_ms=8000)
        if not self._restore_layers_from_snapshot_if_missing(pre_layers):
            self._restore_scene_from_manifest_snapshot_if_wiped()
        if not path_str:
            self._arm_post_scene_guard_from_snapshot(pre_layers, duration_ms=7000)
            return
        targets = [t for t in targets if t.scene() is scene]
        if not targets:
            self._arm_post_scene_guard_from_snapshot(pre_layers, duration_ms=7000)
            return
        path = Path(path_str)
        suf = path.suffix.lower()
        if suf not in (".png", ".jpg", ".jpeg", ".bmp"):
            filt = selected_filter.upper()
            if "JPEG" in filt:
                path = path.with_suffix(".jpg")
            elif "BMP" in filt:
                path = path.with_suffix(".bmp")
            else:
                path = path.with_suffix(".png")
        ExportService.export_item_selection(scene, path, targets)
        if not self._restore_layers_from_snapshot_if_missing(pre_layers):
            self._restore_scene_from_manifest_snapshot_if_wiped()
        self._arm_post_scene_guard_from_snapshot(pre_layers, duration_ms=7000)

    def _save_project(self) -> None:
        scene = self._canvas.graphics_scene()
        pre_manifest, pre_blobs = scene_to_pur_data(scene)
        pre_node_ids = {str(n.get("id")) for n in pre_manifest.get("nodes", []) if n.get("id")}
        scene.clearSelection()
        self._begin_file_dialog_shortcut_guard()
        try:
            path_str, _selected = QFileDialog.getSaveFileName(
                self,
                "Save Project",
                "",
                "StickOn project (*.sti)",
            )
        finally:
            self._end_file_dialog_shortcut_guard()
        self._arm_post_save_manifest_guard(pre_manifest, pre_blobs, duration_ms=8000)
        if not path_str:
            self._restore_scene_from_manifest_snapshot_if_wiped()
            return
        path = Path(path_str)
        if path.suffix.lower() != ".sti":
            path = path.with_suffix(".sti")
        try:
            g = self.geometry()
            live_scene = self._canvas.graphics_scene()
            persist_types = (ImageNodeItem, NoteNodeItem, DrawNodeItem, GroupNodeItem)
            live_ids = {
                getattr(it, "node_id")
                for it in live_scene.items()
                if isinstance(it, persist_types) and isinstance(getattr(it, "node_id", None), str)
            }
            missing_ids = pre_node_ids - live_ids
            if pre_node_ids and missing_ids:
                m = dict(pre_manifest)
                m["window"] = {"x": int(g.x()), "y": int(g.y()), "w": int(g.width()), "h": int(g.height())}
                m["view"] = self._capture_canvas_view_state()
                save_pur(path, m, pre_blobs)
                load_scene_from_path(live_scene, path)
            else:
                save_scene_to_path(
                    live_scene,
                    path,
                    window_geometry=(g.x(), g.y(), g.width(), g.height()),
                    view_state=self._capture_canvas_view_state(),
                )
            self.setWindowTitle(f"StickOn — {path.name}")
        except OSError:
            QMessageBox.critical(self, "Save Project", "Could not write StickOn project file.")

    def _load_project(self) -> None:
        self._disarm_post_save_scene_guard()
        self._canvas.graphics_scene().clearSelection()
        self._begin_file_dialog_shortcut_guard()
        try:
            path_str, _selected = QFileDialog.getOpenFileName(
                self,
                "Load Project",
                "",
                "StickOn project (*.sti *.pur)",
            )
        finally:
            self._end_file_dialog_shortcut_guard()
        if not path_str:
            return
        self._close_all_image_overlays()
        try:
            m = load_scene_from_path(self._canvas.graphics_scene(), path_str)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Load Project",
                f"Could not open StickOn project file.\n{exc}",
            )
            return
        self._history.clear()
        self._last_text_edit_note = None
        self._canvas.exit_draw_mode()
        self._canvas.ensure_notes_above_images()
        self._prune_missing_gif_sources()
        path = Path(path_str)
        self.setWindowTitle(f"StickOn — {path.name}")
        win = m.get("window")
        if isinstance(win, dict):
            try:
                x, y, w, h = int(win["x"]), int(win["y"]), int(win["w"]), int(win["h"])
                if w >= self.minimumWidth() and h >= self.minimumHeight():
                    self.setGeometry(x, y, w, h)
            except (KeyError, TypeError, ValueError):
                pass
        self._pending_view_state = self._parse_canvas_view_state(m.get("view"))
        self._apply_pending_view_state()

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

    def _begin_file_dialog_shortcut_guard(self) -> None:
        if self._file_dialog_guard_release.isActive():
            self._file_dialog_guard_release.stop()
        self._delete_shortcut_suppressed = True
        self._global_shortcuts_suppressed = True

    def _end_file_dialog_shortcut_guard(self) -> None:
        # Deferred native-dialog key events can arrive late on Windows.
        # Keep guard active for a few seconds or until user clicks back into canvas.
        self._file_dialog_guard_release.start(6000)

    def _clear_file_dialog_shortcut_guard(self) -> None:
        self._delete_shortcut_suppressed = False
        self._global_shortcuts_suppressed = False

    def delete_shortcuts_suppressed(self) -> bool:
        return self._delete_shortcut_suppressed

    def shortcuts_temporarily_suppressed(self) -> bool:
        return self._global_shortcuts_suppressed

    def _arm_post_scene_guard_from_snapshot(
        self,
        snapshot: list[
            tuple[
                object,
                QPointF,
                float,
                float,
                float,
                QPointF,
                QTransform,
                GroupNodeItem | None,
            ]
        ],
        *,
        duration_ms: int = 6000,
    ) -> None:
        self._post_save_layer_snapshot = list(snapshot)
        if not self._post_save_layer_snapshot:
            self._disarm_post_save_scene_guard()
            return
        self._post_save_scene_guard_deadline = max(1, int(QDateTime.currentMSecsSinceEpoch())) + max(
            500, int(duration_ms)
        )
        self._post_save_scene_guard.start()

    def _arm_post_save_scene_guard(self, *, duration_ms: int = 6000) -> None:
        snapshot = self._capture_layer_snapshot()
        self._arm_post_scene_guard_from_snapshot(snapshot, duration_ms=duration_ms)

    def _disarm_post_save_scene_guard(self) -> None:
        if self._post_save_scene_guard.isActive():
            self._post_save_scene_guard.stop()
        self._post_save_scene_guard_deadline = 0
        self._post_save_layer_snapshot = []

    def _arm_post_save_manifest_guard(
        self,
        manifest: dict[str, Any],
        blobs: dict[str, bytes],
        *,
        duration_ms: int = 8000,
    ) -> None:
        if self._post_save_manifest_guard.isActive():
            self._post_save_manifest_guard.stop()
        self._post_save_manifest_snapshot = dict(manifest)
        self._post_save_blobs_snapshot = dict(blobs)
        self._post_save_manifest_guard_deadline = max(1, int(QDateTime.currentMSecsSinceEpoch())) + max(
            500, int(duration_ms)
        )
        self._post_save_manifest_guard.start()

    def _disarm_post_save_manifest_guard(self) -> None:
        if self._post_save_manifest_guard.isActive():
            self._post_save_manifest_guard.stop()
        self._post_save_manifest_guard_deadline = 0
        self._post_save_manifest_snapshot = None
        self._post_save_blobs_snapshot = None

    def _restore_scene_from_manifest_snapshot_if_wiped(self) -> None:
        manifest = self._post_save_manifest_snapshot
        blobs = self._post_save_blobs_snapshot
        if manifest is None or blobs is None:
            return
        expected_nodes = manifest.get("nodes", [])
        if not expected_nodes:
            return
        expected_ids = {str(n.get("id")) for n in expected_nodes if n.get("id")}
        scene = self._canvas.graphics_scene()
        persist_types = (ImageNodeItem, NoteNodeItem, DrawNodeItem, GroupNodeItem)
        live_ids = {
            getattr(it, "node_id")
            for it in scene.items()
            if isinstance(it, persist_types) and isinstance(getattr(it, "node_id", None), str)
        }
        missing_ids = expected_ids - live_ids
        if not missing_ids:
            return
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="stickon-save-restore-", suffix=".sti", delete=False) as tf:
                tmp_path = Path(tf.name)
            save_pur(tmp_path, manifest, blobs)
            load_scene_from_path(scene, tmp_path)
        except (OSError, ValueError):
            return
        finally:
            if tmp_path is not None:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass

    def _on_post_save_scene_guard_tick(self) -> None:
        if not self._post_save_layer_snapshot:
            self._disarm_post_save_scene_guard()
            return
        now = int(QDateTime.currentMSecsSinceEpoch())
        self._restore_layers_from_snapshot_if_missing(self._post_save_layer_snapshot)
        if now >= self._post_save_scene_guard_deadline:
            self._disarm_post_save_scene_guard()

    def _on_post_save_manifest_guard_tick(self) -> None:
        if self._post_save_manifest_snapshot is None or self._post_save_blobs_snapshot is None:
            self._disarm_post_save_manifest_guard()
            return
        now = int(QDateTime.currentMSecsSinceEpoch())
        self._restore_scene_from_manifest_snapshot_if_wiped()
        if now >= self._post_save_manifest_guard_deadline:
            self._disarm_post_save_manifest_guard()
