from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
    QFormLayout,
)

from stickon.scene.items.note_item import NoteAppearance


# Raster / legacy faces that crash or spam DirectWrite when Qt draws QFontComboBox previews on Windows.
_LEGACY_FAMILY_SKIP = frozenset(
    {
        "Fixedsys",
        "System",
        "Terminal",
        "MS Sans Serif",
        "Small Fonts",
        "Modern",
        "Roman",
        "Script",
    }
)


def _font_family_choices() -> list[str]:
    db = QFontDatabase()
    names: list[str] = []
    for fam in db.families():
        if fam in _LEGACY_FAMILY_SKIP:
            continue
        try:
            if db.isSmoothlyScalable(fam):
                names.append(fam)
        except (TypeError, AttributeError):
            names.append(fam)
    names.sort(key=str.casefold)
    if not names:
        names = sorted((f for f in db.families() if f not in _LEGACY_FAMILY_SKIP), key=str.casefold)
    return names


class FontFamilyPreviewDelegate(QStyledItemDelegate):
    """Render each row with its font face (list is scalable fonts only, minus legacy skip list)."""

    _preview_suffix = "    · AaBbYyZz 919293949596979899"

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        if opt.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, opt.palette.highlight())
            fg = opt.palette.color(QPalette.ColorRole.HighlightedText)
        else:
            painter.fillRect(option.rect, opt.palette.color(QPalette.ColorRole.Base))
            fg = opt.palette.color(QPalette.ColorRole.Text)

        fam = index.data(Qt.ItemDataRole.DisplayRole)
        painter.save()
        if isinstance(fam, str) and fam.strip():
            fnt = QFont(fam)
            fnt.setPointSize(11)
            painter.setFont(fnt)
            painter.setPen(fg)
            painter.drawText(
                option.rect.adjusted(8, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                fam + self._preview_suffix,
            )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        sh = super().sizeHint(option, index)
        return QSize(sh.width(), max(sh.height(), 28))


class FontSettingsDialog(QDialog):
    """Configure sticky-note typography, colors, and frame border."""

    def __init__(self, appearance: NoteAppearance, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("StickOn — Font Setting")
        self.resize(540, 420)

        self._text_color = QColor(appearance.text_color)
        self._bg_color = QColor(appearance.bg_color)
        self._border_color = QColor(appearance.border_color)

        hdr = QLabel("Font Setting")
        hdr.setStyleSheet(
            "background-color: #344c7d; color: #f8faff; padding: 8px 10px; "
            "font-weight: 600; border-radius: 4px;"
        )

        self._font_combo = QComboBox(self)
        fams = _font_family_choices()
        self._font_combo.addItems(fams)

        view = self._font_combo.view()
        view.setItemDelegate(FontFamilyPreviewDelegate(view))
        self._font_combo.currentIndexChanged.connect(lambda _i: self._sync_font_combo_display_face())
        self._select_font_family_in_combo(appearance.font_family)

        self._size_spin = QSpinBox(self)
        self._size_spin.setRange(8, 120)
        self._size_spin.setValue(max(8, min(120, appearance.font_point_size)))

        self._btn_text_color = QPushButton("Choose…")
        self._btn_bg_color = QPushButton("Choose…")
        self._btn_border_color = QPushButton("Choose…")
        self._btn_text_color.clicked.connect(self._pick_text_color)
        self._btn_bg_color.clicked.connect(self._pick_bg_color)
        self._btn_border_color.clicked.connect(self._pick_border_color)

        style_box = QGroupBox("Text style")
        style_grid = QGridLayout(style_box)
        self._chk_bold = QPushButton("Bold")
        self._chk_italic = QPushButton("Italic")
        self._chk_underline = QPushButton("Underline")
        self._chk_strike = QPushButton("Strikethrough")
        for b in (self._chk_bold, self._chk_italic, self._chk_underline, self._chk_strike):
            b.setCheckable(True)
            b.setAutoDefault(False)
            b.setDefault(False)
        self._chk_bold.setChecked(appearance.bold)
        self._chk_italic.setChecked(appearance.italic)
        self._chk_underline.setChecked(appearance.underline)
        self._chk_strike.setChecked(appearance.strike_out)

        self._btn_normal = QPushButton("Normal")
        self._btn_normal.setToolTip("Turn off bold and italic")
        self._btn_normal.setAutoDefault(False)
        self._btn_normal.clicked.connect(self._clear_normal_weight)

        style_grid.addWidget(self._btn_normal, 0, 0)
        style_grid.addWidget(self._chk_bold, 0, 1)
        style_grid.addWidget(self._chk_italic, 0, 2)
        style_grid.addWidget(self._chk_underline, 1, 0)
        style_grid.addWidget(self._chk_strike, 1, 1)

        self._border_width = QDoubleSpinBox(self)
        self._border_width.setRange(0.0, 24.0)
        self._border_width.setSingleStep(0.25)
        self._border_width.setDecimals(2)
        self._border_width.setValue(max(0.0, min(24.0, appearance.border_width)))

        form = QFormLayout()
        row_font = QHBoxLayout()
        row_font.addWidget(self._font_combo, stretch=1)
        form.addRow("Font:", row_font)
        form.addRow("Size (pt):", self._size_spin)

        form.addRow("Text color:", self._row_color_widget(self._btn_text_color, self._text_color))
        form.addRow(style_box)
        form.addRow("Background:", self._row_color_widget(self._btn_bg_color, self._bg_color))
        form.addRow("Border width:", self._border_width)
        form.addRow("Border color:", self._row_color_widget(self._btn_border_color, self._border_color))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        self._btn_reset_defaults = QPushButton("Reset to defaults")
        self._btn_reset_defaults.setAutoDefault(False)
        self._btn_reset_defaults.setDefault(False)
        self._btn_reset_defaults.clicked.connect(self._on_reset_to_defaults_clicked)

        bottom = QHBoxLayout()
        bottom.addWidget(self._btn_reset_defaults)
        bottom.addStretch()
        bottom.addWidget(buttons)

        lay = QVBoxLayout(self)
        lay.addWidget(hdr)
        lay.addLayout(form)
        lay.addLayout(bottom)

        self._refresh_color_buttons()

    def _select_font_family_in_combo(self, family: str) -> None:
        cur = family.strip()
        self._font_combo.blockSignals(True)
        try:
            if cur and self._font_combo.findText(cur) < 0:
                self._font_combo.insertItem(0, cur)
                self._font_combo.setCurrentIndex(0)
            elif cur:
                self._font_combo.setCurrentText(cur)
        finally:
            self._font_combo.blockSignals(False)
        self._sync_font_combo_display_face()

    def _apply_appearance_to_form(self, app: NoteAppearance) -> None:
        self._text_color = QColor(app.text_color)
        self._bg_color = QColor(app.bg_color)
        self._border_color = QColor(app.border_color)
        self._select_font_family_in_combo(app.font_family)
        self._size_spin.setValue(max(8, min(120, app.font_point_size)))
        self._chk_bold.setChecked(app.bold)
        self._chk_italic.setChecked(app.italic)
        self._chk_underline.setChecked(app.underline)
        self._chk_strike.setChecked(app.strike_out)
        self._border_width.setValue(max(0.0, min(24.0, app.border_width)))
        self._refresh_color_buttons()

    def _on_reset_to_defaults_clicked(self) -> None:
        r = QMessageBox.question(
            self,
            "Reset font settings",
            "Restore all fields on this screen to the built-in defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        self._apply_appearance_to_form(NoteAppearance.builtin())

    def _sync_font_combo_display_face(self) -> None:
        fam = self._font_combo.currentText().strip()
        if fam:
            f = QFont(fam)
            f.setPointSize(11)
            self._font_combo.setFont(f)

    def _row_color_widget(self, btn: QPushButton, _c: QColor) -> QWidget:
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel()
        lbl.setFixedSize(28, 22)
        btn._swatch_label = lbl  # noqa: SLF001
        hl.addWidget(lbl)
        hl.addWidget(btn)
        hl.addStretch()
        return w

    def _refresh_color_buttons(self) -> None:
        self._apply_swatch(self._btn_text_color, self._text_color)
        self._apply_swatch(self._btn_bg_color, self._bg_color)
        self._apply_swatch(self._btn_border_color, self._border_color)

    @staticmethod
    def _apply_swatch(btn: QPushButton, c: QColor) -> None:
        lbl = getattr(btn, "_swatch_label", None)
        if isinstance(lbl, QLabel):
            lbl.setStyleSheet(
                f"background-color: {c.name(QColor.NameFormat.HexArgb)}; "
                "border: 1px solid #888; border-radius: 3px;"
            )

    def _pick_text_color(self) -> None:
        c = QColorDialog.getColor(
            self._text_color,
            self,
            "Text color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if c.isValid():
            self._text_color = c
            self._refresh_color_buttons()

    def _pick_bg_color(self) -> None:
        c = QColorDialog.getColor(
            self._bg_color,
            self,
            "Background color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if c.isValid():
            self._bg_color = c
            self._refresh_color_buttons()

    def _pick_border_color(self) -> None:
        c = QColorDialog.getColor(
            self._border_color,
            self,
            "Border color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if c.isValid():
            self._border_color = c
            self._refresh_color_buttons()

    def _clear_normal_weight(self) -> None:
        self._chk_bold.setChecked(False)
        self._chk_italic.setChecked(False)

    def result_appearance(self) -> NoteAppearance:
        fam = self._font_combo.currentText().strip()
        if not fam:
            fam = NoteAppearance.builtin().font_family
        return NoteAppearance(
            font_family=fam,
            font_point_size=int(self._size_spin.value()),
            text_color=QColor(self._text_color),
            bg_color=QColor(self._bg_color),
            border_width=float(self._border_width.value()),
            border_color=QColor(self._border_color),
            bold=self._chk_bold.isChecked(),
            italic=self._chk_italic.isChecked(),
            underline=self._chk_underline.isChecked(),
            strike_out=self._chk_strike.isChecked(),
        )

    def keyPressEvent(self, event) -> None:
        def refresh_from_host() -> None:
            host = self.parent()
            getter = getattr(host, "_current_font_settings_appearance", None)
            if callable(getter):
                latest = getter()
                if isinstance(latest, NoteAppearance):
                    self._apply_appearance_to_form(latest)

        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and event.key() == Qt.Key.Key_Z:
            host = self.parent()
            exec_fn = getattr(host, "_execute", None)
            if callable(exec_fn):
                exec_fn("edit.redo" if shift else "edit.undo")
                refresh_from_host()
                event.accept()
                return
        if ctrl and not shift and event.key() == Qt.Key.Key_Y:
            host = self.parent()
            exec_fn = getattr(host, "_execute", None)
            if callable(exec_fn):
                exec_fn("edit.redo")
                refresh_from_host()
                event.accept()
                return
        super().keyPressEvent(event)
