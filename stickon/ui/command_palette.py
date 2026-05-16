from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from stickon.core.commands import Command
from stickon.core.input_router import parse_shortcut


# Legacy: stripped from stored shortcut strings for note.new (see stored_shortcut_chord_only).
NOTE_NEW_PALETTE_HINT_SUFFIX = ", double click in canvas"


def stored_shortcut_chord_only(command_id: str, stored: str | None) -> str:
    """Keyboard chord only; strips palette-only hint if it was ever stored on note.new."""
    if not stored:
        return ""
    s = stored.strip()
    if command_id != "note.new":
        return s
    suf = NOTE_NEW_PALETTE_HINT_SUFFIX
    low_suf = suf.lower()
    while len(s) >= len(suf) and s.lower().endswith(low_suf):
        s = s[: -len(suf)].strip()
    return s


def _tick_pixmap(on: bool, size: int = 18) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    if not on:
        return pm
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(30, 140, 45))
    pen.setWidthF(2.25)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawLine(4, 9, 8, 13)
    p.drawLine(8, 13, 14, 5)
    p.end()
    return pm


_PALETTE_OPEN_ROW_BG = QColor(52, 92, 182)
_PALETTE_OPEN_ROW_FG = QColor(248, 250, 255)


_MODIFIER_KEYS = frozenset(
    {
        Qt.Key.Key_Shift,
        Qt.Key.Key_Control,
        Qt.Key.Key_Alt,
        Qt.Key.Key_Meta,
        Qt.Key.Key_AltGr,
    }
)


class RecordShortcutDialog(QDialog):
    """Prompt for a key chord; Esc rejects. Valid chords must parse for InputRouter."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New shortcut")
        self.setModal(True)
        self._chosen: str | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        hint = QLabel(
            "Press the new shortcut keys for this command.\n"
            "Esc cancels without changing anything.",
            self,
        )
        hint.setWordWrap(True)
        preview = QLabel("(waiting…)", self)
        preview.setStyleSheet("font-weight: 600; padding: 8px;")
        self._preview = preview

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(preview)
        lay.addWidget(buttons)

        self.resize(420, 160)

    def chosen_shortcut(self) -> str | None:
        return self._chosen

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        if event.key() in _MODIFIER_KEYS:
            return
        comb = event.keyCombination()
        seq = QKeySequence(comb)
        portable = seq.toString(QKeySequence.SequenceFormat.PortableText).strip()
        self._preview.setText(portable or "(waiting…)")
        if not portable:
            return
        if parse_shortcut(portable) is None:
            QMessageBox.warning(
                self,
                "Shortcut not supported",
                "That key combination cannot be used as a command shortcut here. Try another.",
            )
            return
        self._chosen = portable
        self.accept()


class _PaletteListWidget(QListWidget):
    """Double-click starts shortcut customization when a callback is set."""

    def __init__(self, palette: CommandPaletteDialog) -> None:
        super().__init__(palette)
        self._palette = palette

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        cb = self._palette.shortcut_customizer
        if cb is not None:
            it = self.itemAt(event.pos())
            if it is not None:
                cid = it.data(Qt.ItemDataRole.UserRole)
                if isinstance(cid, str):
                    cb(cid)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class CommandPaletteDialog(QDialog):
    def __init__(
        self,
        commands: list[Command],
        parent=None,
        *,
        shortcut_customizer: Callable[[str], None] | None = None,
        reset_shortcuts_to_defaults: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("StickOn — Command Key")
        self.resize(520, 400)
        self.shortcut_customizer = shortcut_customizer
        self._reset_shortcuts_to_defaults = reset_shortcuts_to_defaults
        self._commands = [c for c in commands if getattr(c, "palette", True)]
        self._selected_id: str | None = None

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Type to filter…")
        self._list = _PaletteListWidget(self)

        hdr = QLabel("Command Key — Ctrl+Shift+P")
        hdr.setStyleSheet(
            "background-color: #344c7d; color: #f8faff; padding: 8px 10px; "
            "font-weight: 600; border-radius: 4px;"
        )

        lay = QVBoxLayout(self)
        lay.addWidget(hdr)
        lay.addWidget(self._filter)
        lay.addWidget(self._list)

        bottom = QHBoxLayout()
        self._btn_reset = QPushButton("Reset to defaults")
        self._btn_reset.setAutoDefault(False)
        self._btn_reset.setDefault(False)
        self._btn_reset.setEnabled(reset_shortcuts_to_defaults is not None)
        self._btn_reset.clicked.connect(self._on_reset_clicked)
        bottom.addWidget(self._btn_reset)
        bottom.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        lay.addLayout(bottom)

        self._filter.textChanged.connect(self._apply_filter)
        self._list.itemActivated.connect(self._pick)

        icon_sz = 18
        self._list.setIconSize(QSize(icon_sz, icon_sz))
        self._empty_icon = QIcon(QPixmap(icon_sz, icon_sz))

        self._populate(self._commands)

    @staticmethod
    def _shortcut_cell(cmd: Command) -> str:
        return (cmd.shortcut or "").strip() or "—"

    def refresh_commands(self, commands: list[Command]) -> None:
        self._commands = [c for c in commands if getattr(c, "palette", True)]
        self._apply_filter(self._filter.text())

    def _on_reset_clicked(self) -> None:
        if self._reset_shortcuts_to_defaults is None:
            return
        r = QMessageBox.question(
            self,
            "Reset shortcuts",
            "Restore all command shortcuts to their built-in defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        self._reset_shortcuts_to_defaults()

    def _populate(self, cmds: list[Command]) -> None:
        icon_sz = self._list.iconSize().width()
        self._list.clear()
        empty_pm = QPixmap(icon_sz, icon_sz)
        empty_pm.fill(Qt.GlobalColor.transparent)
        empty_icon = QIcon(empty_pm)
        for c in cmds:
            it = QListWidgetItem(f"{c.title}  ({self._shortcut_cell(c)})")
            it.setData(Qt.ItemDataRole.UserRole, c.id)
            if c.is_checked is not None:
                it.setIcon(QIcon(_tick_pixmap(bool(c.is_checked()), icon_sz)))
            else:
                it.setIcon(empty_icon)
            if c.id == "palette.open":
                it.setBackground(QBrush(_PALETTE_OPEN_ROW_BG))
                it.setForeground(QBrush(_PALETTE_OPEN_ROW_FG))
            self._list.addItem(it)

    def _apply_filter(self, text: str) -> None:
        t = text.strip().lower()
        if not t:
            self._populate(self._commands)
            return
        filtered = [
            c
            for c in self._commands
            if t in c.title.lower()
            or t in c.id.lower()
        ]
        self._populate(filtered)

    def _pick(self, item: QListWidgetItem | None = None) -> None:
        it = item or self._list.currentItem()
        if it is None:
            return
        self._selected_id = it.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def selected_command_id(self) -> str | None:
        return self._selected_id

    def keyPressEvent(self, event) -> None:
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and event.key() == Qt.Key.Key_Z:
            host = self.parent()
            exec_fn = getattr(host, "_execute", None)
            if callable(exec_fn):
                exec_fn("edit.redo" if shift else "edit.undo")
                reg = getattr(host, "_registry", None)
                if reg is not None and hasattr(reg, "all"):
                    self.refresh_commands(reg.all())
                event.accept()
                return
        if ctrl and not shift and event.key() == Qt.Key.Key_Y:
            host = self.parent()
            exec_fn = getattr(host, "_execute", None)
            if callable(exec_fn):
                exec_fn("edit.redo")
                reg = getattr(host, "_registry", None)
                if reg is not None and hasattr(reg, "all"):
                    self.refresh_commands(reg.all())
                event.accept()
                return
        super().keyPressEvent(event)
