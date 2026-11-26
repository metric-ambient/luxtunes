"""View → Appearance…: the style (Dark, Material, Light), its three colours
(background, text, accent) and Auto Accent, as in the LuxTunes app for
Android (its Settings → Appearance). Changes show at once and are kept."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from luxtunes.ui import theme

SLOTS = (("Background", 0, theme.BACKGROUND_SUGGESTIONS), ("Text", 1, theme.TEXT_SUGGESTIONS),
         ("Accent", 2, theme.ACCENT_SUGGESTIONS))


class StylePreview(QWidget):
    """A small drawing of the window in a style: toolbar, sidebar, a few rows
    (one selected, one playing). Click to use it; right-click to reset its colours."""
    chosen = Signal(str)
    reset = Signal(str)

    def __init__(self, style: str, parent=None):
        super().__init__(parent)
        self.style_key = style
        self.colors = theme.DEFAULTS[style]
        self.selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Use {theme.STYLE_NAMES[style]}. Right-click to reset its colours.")
        self.setFixedHeight(146)
        self.setMinimumWidth(150)

    def sizeHint(self):
        return QSize(170, 146)

    def set_state(self, colors: tuple, selected: bool):
        self.colors, self.selected = colors, selected
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.chosen.emit(self.style_key)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction(f"Reset {theme.STYLE_NAMES[self.style_key]} Colours",
                       lambda: self.reset.emit(self.style_key))
        menu.exec(event.globalPos())

    def paintEvent(self, event):
        background, text, accent = self.colors
        c = theme.build_colors(self.style_key, background, text, accent)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -22)
        ring = QColor(theme.current["highlight"]) if self.selected else QColor(theme.current["border"])
        p.setPen(QPen(ring, 3 if self.selected else 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(outer, 8, 8)
        box = outer.adjusted(5, 5, -5, -5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["base"]))
        p.drawRoundedRect(box, 3, 3)
        # Toolbar with the play button
        bar = QRectF(box.left(), box.top(), box.width(), 18)
        p.setBrush(QColor(c["toolbar_top"]))
        p.drawRect(bar)
        p.setBrush(QColor(c["play_bg"]))
        p.drawEllipse(QRectF(bar.left() + 6, bar.top() + 3, 12, 12))
        p.setBrush(QColor(c["lcd_top"]))
        p.drawRoundedRect(QRectF(bar.center().x() - 24, bar.top() + 3, 48, 12), 2, 2)
        # Sidebar
        side = QRectF(box.left(), bar.bottom(), box.width() * 0.28, box.bottom() - bar.bottom())
        p.setBrush(QColor(c["sidebar"]))
        p.drawRect(side)
        p.setBrush(QColor(c["sidebar_sel_top"]))
        p.drawRoundedRect(QRectF(side.left() + 3, side.top() + 8, side.width() - 6, 7), 2, 2)
        for i in range(3):
            p.setBrush(QColor(c["text_dim"]))
            p.drawRect(QRectF(side.left() + 5, side.top() + 22 + i * 10, side.width() * 0.55, 3))
        # Song rows: one striped, one selected, one playing
        left = side.right() + 4
        width = box.right() - left - 4
        for i in range(5):
            y = bar.bottom() + 5 + i * 12
            if y + 10 > box.bottom():
                break
            row = QRectF(side.right(), y - 2, box.right() - side.right(), 11)
            if i == 1:
                p.setBrush(QColor(c["highlight"]))
                p.drawRect(row)
            elif i % 2:
                p.setBrush(QColor(c["alt_base"]))
                p.drawRect(row)
            colour = c["highlight_text"] if i == 1 else c["now_playing_text"] if i == 3 else text
            p.setBrush(QColor(colour))
            p.drawRect(QRectF(left, y + 2, width * (0.7 if i % 2 else 0.5), 3))
        p.setPen(QColor(theme.current["text"] if self.selected else theme.current["text_dim"]))
        font = p.font()
        font.setBold(self.selected)
        p.setFont(font)
        label = ("✓ " if self.selected else "") + theme.STYLE_NAMES[self.style_key]
        p.drawText(QRectF(0, outer.bottom() + 2, self.width(), 20), Qt.AlignmentFlag.AlignCenter, label)
        p.end()


class Swatch(QWidget):
    """A colour in a circle."""
    clicked = Signal()

    def __init__(self, color: str, size: int = 34, parent=None):
        super().__init__(parent)
        self.color, self.chosen = color, False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, color: str, chosen: bool = False):
        self.color, self.chosen = color, chosen
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = 3 if self.chosen else 1
        ring = theme.current["text"] if self.chosen else theme.current["text_dim"]
        p.setPen(QPen(QColor(ring), width))
        p.setBrush(QColor(self.color))
        p.drawEllipse(QRectF(self.rect()).adjusted(width, width, -width, -width))
        p.end()


class ColorPicker(QDialog):
    """Pick a colour: the app's suggestions, any colour (More Colours…), or a hex value."""

    def __init__(self, title: str, current: str, suggestions, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.original = current.upper()
        self.picked = self.original
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        self.swatches = []
        for i, colour in enumerate(suggestions):
            swatch = Swatch(colour)
            swatch.setToolTip(colour)
            swatch.clicked.connect(lambda c=colour: self._choose(c))
            grid.addWidget(swatch, i // 5, i % 5)
            self.swatches.append(swatch)
        layout.addLayout(grid)
        more = QPushButton("More Colours…")
        more.clicked.connect(self._more)
        layout.addWidget(more)
        row = QHBoxLayout()
        self.old = Swatch(self.original, 30)
        self.new = Swatch(self.original, 30)
        self.hex = QLineEdit(self.original[1:])
        self.hex.setInputMask(">HHHHHH")
        self.hex.setMaximumWidth(90)
        self.hex.textEdited.connect(self._typed)
        row.addWidget(QLabel(self.original))
        row.addWidget(self.old)
        row.addWidget(QLabel("→"))
        row.addWidget(self.new)
        row.addWidget(QLabel("#"))
        row.addWidget(self.hex)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(buttons)
        self._choose(self.original)

    def _choose(self, colour: str):
        self.picked = colour.upper()
        self.new.set_color(self.picked)
        if self.hex.text() != self.picked[1:]:
            self.hex.setText(self.picked[1:])
        for swatch in self.swatches:
            swatch.set_color(swatch.color, swatch.color.upper() == self.picked)
        self.ok.setEnabled(True)

    def _typed(self, text: str):
        if len(text) == 6:
            self._choose("#" + text)
        else:
            self.ok.setEnabled(False)

    def _more(self):
        colour = QColorDialog.getColor(QColor(self.picked), self, self.windowTitle())
        if colour.isValid():
            self._choose(colour.name())


class AppearanceWindow(QDialog):
    """Not modal: the window behind changes as you choose."""

    def __init__(self, look: theme.Look, on_change, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Appearance")
        self.look = look
        self.on_change = on_change
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        styles = QHBoxLayout()
        self.previews = {}
        for style in theme.STYLES:
            preview = StylePreview(style)
            preview.chosen.connect(self._use_style)
            preview.reset.connect(self._reset_style)
            styles.addWidget(preview)
            self.previews[style] = preview
        layout.addLayout(styles)

        # The chosen style's three colours, side by side under the previews.
        colours = QHBoxLayout()
        self.rows = []
        for name, index, suggestions in SLOTS:
            swatch = Swatch("#000000", 30)
            swatch.setToolTip(f"Choose the {name.lower()} colour")
            swatch.clicked.connect(lambda n=name, i=index, s=suggestions: self._edit(n, i, s))
            label = QLabel()
            colours.addStretch(1)
            colours.addWidget(swatch)
            colours.addWidget(label)
            self.rows.append((swatch, label, name))
        colours.addStretch(1)
        layout.addLayout(colours)

        self.auto = QCheckBox("Auto Accent: take the accent from the playing song's album art")
        self.auto.setToolTip("When the art has no colour that stands out on the background, your "
                             "own accent colour is used. Works in all three styles.")
        self.auto.toggled.connect(self._auto_toggled)
        layout.addWidget(self.auto)

        # Linux: LuxTunes' own title bar (with the menus in it), or the desktop's.
        import sys
        settings = getattr(parent, "settings", None)
        if sys.platform.startswith("linux") and settings is not None:
            own = QCheckBox("LuxTunes' own title bar, with the menus in it")
            own.setToolTip("Off: the desktop's title bar and a menu bar under it. "
                           "Takes effect the next time LuxTunes opens.")
            own.setChecked(settings.value("window/own_title_bar", True, type=bool))
            own.toggled.connect(lambda on: settings.setValue("window/own_title_bar", on))
            layout.addWidget(own)

        buttons = QHBoxLayout()
        self.reset_button = QPushButton()
        self.reset_button.clicked.connect(lambda: self._reset_style(self.look.style))
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.close)
        buttons.addWidget(self.reset_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._show()

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 1)
        label.setFont(font)
        return label

    @staticmethod
    def _note(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("deviceNote")              # the dimmed note colour
        return label

    def _show(self):
        for style, preview in self.previews.items():
            preview.set_state(self.look.of(style), style == self.look.style)
        for (swatch, label, name), value in zip(self.rows, self.look.current):
            swatch.set_color(value)
            label.setText(f"{name}<br><span style='color:{theme.current['text_dim']}'>"
                          f"{value}</span>")
        self.auto.blockSignals(True)
        self.auto.setChecked(self.look.auto_accent)
        self.auto.blockSignals(False)
        self.reset_button.setText(f"Reset {theme.STYLE_NAMES[self.look.style]} Colours")

    def _changed(self, look: theme.Look):
        self.look = look
        self.on_change(look)
        self._show()

    def _use_style(self, style: str):
        from dataclasses import replace
        if style != self.look.style:
            self._changed(replace(self.look, style=style))

    def _reset_style(self, style: str):
        from dataclasses import replace
        look = self.look.with_colors(style, theme.DEFAULTS[style])
        self._changed(replace(look, style=style))

    def _auto_toggled(self, on: bool):
        from dataclasses import replace
        self._changed(replace(self.look, auto_accent=on))

    def _edit(self, name: str, index: int, suggestions):
        current = list(self.look.current)
        picker = ColorPicker(name, current[index], suggestions, self)
        if picker.exec() == QDialog.DialogCode.Accepted and picker.picked != current[index]:
            current[index] = picker.picked
            self._changed(self.look.with_colors(self.look.style, tuple(current)))
