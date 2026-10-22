"""The Equalizer window (View > Show Equalizer, Ctrl+T) and its Edit Presets list.

The window changes the equalizer.Settings it's given and says so with
`changed`; the main window saves them and tells the player. Renamed and
deleted presets are announced too, so songs using them can follow.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from luxtunes.core import equalizer
from luxtunes.ui import eq_output

STEPS = 10                                  # slider steps per dB


def _db(value: float) -> str:
    return "0" if not value else f"{value:+.1f}".replace(".0", "")


class _Slider(QSlider):
    def mouseDoubleClickEvent(self, event):
        self.setValue(0)


class BandSlider(QWidget):
    """One vertical slider with its value above and its name below.
    Double-click puts it back to 0 dB."""

    moved = Signal()

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.slider = _Slider(Qt.Orientation.Vertical)
        self.slider.setRange(int(-equalizer.LIMIT * STEPS), int(equalizer.LIMIT * STEPS))
        self.slider.setPageStep(STEPS)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBothSides)
        self.slider.setTickInterval(6 * STEPS)
        self.slider.setMinimumHeight(170)
        self.slider.valueChanged.connect(self._changed)
        self.value_label = QLabel("0")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_label.setFixedWidth(40)          # no re-layout as the number changes
        name_label = QLabel(name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.value_label)
        layout.addWidget(self.slider, 1, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(name_label)
        self._quiet = False

    def value(self) -> float:
        return self.slider.value() / STEPS

    def set_value(self, db: float) -> None:
        self._quiet = True
        self.slider.setValue(round(db * STEPS))
        self._quiet = False
        self._show()

    def _show(self):
        self.value_label.setText(_db(self.value()))
        self.slider.setToolTip(f"{_db(self.value())} dB")

    def _changed(self, _value):
        self._show()
        if not self._quiet:
            self.moved.emit()


class EqualizerWindow(QDialog):
    changed = Signal()
    preset_renamed = Signal(str, str)       # old, new
    preset_deleted = Signal(str)

    MAKE, EDIT = "\x00make", "\x00edit"

    def __init__(self, settings: equalizer.Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Equalizer")
        # While a slider is dragged, the sound follows at most ~30 times a
        # second instead of at every step.
        self._change_timer = QTimer(self)
        self._change_timer.setSingleShot(True)
        self._change_timer.setInterval(33)
        self._change_timer.timeout.connect(self.changed)
        self.setWindowFlag(Qt.WindowType.Tool)

        self.on = QCheckBox("On")
        self.on.toggled.connect(self._on_toggled)
        self.presets = QComboBox()
        self.presets.setMinimumWidth(190)
        self.presets.activated.connect(self._picked)
        top = QHBoxLayout()
        top.addWidget(self.on)
        top.addStretch(1)
        top.addWidget(self.presets)

        self.preamp = BandSlider("Preamp")
        self.preamp.moved.connect(self._moved)
        self.bands = [BandSlider(label) for label in equalizer.BAND_LABELS]
        for band in self.bands:
            band.moved.connect(self._moved)
        scale = QVBoxLayout()
        scale.setContentsMargins(0, 18, 0, 22)            # line up with the sliders' ends
        for n, text in enumerate(("+12 dB", "0 dB", "−12 dB")):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignRight)
            scale.addWidget(label)
            if n < 2:
                scale.addStretch(1)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        sliders = QHBoxLayout()
        sliders.addLayout(scale)
        sliders.addWidget(self.preamp)
        sliders.addWidget(line)
        for band in self.bands:
            sliders.addWidget(band)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(sliders, 1)
        layout.addWidget(self.note)
        if not eq_output.available():
            self.on.setEnabled(False)
            self.note.setText("The equalizer needs Qt 6.8 or newer (PySide6 6.8+).")
            self.note.setVisible(True)
        self.refresh()

    # ---- Showing the settings ----------------------------------------------------------

    def refresh(self) -> None:
        self.on.blockSignals(True)
        self.on.setChecked(self.settings.on)
        self.on.blockSignals(False)
        self._fill_presets()
        current = self.settings.current
        self.preamp.set_value(current.preamp)
        for band, value in zip(self.bands, current.bands):
            band.set_value(value)

    def _fill_presets(self) -> None:
        box = self.presets
        box.clear()
        box.addItem("Make Preset…", self.MAKE)
        box.addItem("Edit List…", self.EDIT)
        box.insertSeparator(box.count())
        box.addItem(equalizer.MANUAL, equalizer.MANUAL)
        box.insertSeparator(box.count())
        for name in self.settings.names():
            box.addItem(name, name)
        chosen = self.settings.current.name
        box.setCurrentIndex(max(0, box.findData(chosen)))

    def show_song(self, track: dict | None) -> None:
        """Say so when the song playing has its own preset (it wins over the window's)."""
        preset = self.settings.find((track or {}).get("equalizer"))
        note = (f"“{track.get('name') or 'This song'}” plays with its own preset, "
                f"{preset.name} (Get Info > Options)." if preset else "")
        if eq_output.available():
            self.note.setText(note)
            self.note.setVisible(bool(note))

    # ---- Changes -------------------------------------------------------------------------

    def _on_toggled(self, on: bool) -> None:
        self.settings.on = on
        self.changed.emit()

    def _moved(self) -> None:
        self.settings.set_manual([b.value() for b in self.bands], self.preamp.value())
        if self.presets.currentData() != equalizer.MANUAL:
            self.presets.setCurrentIndex(self.presets.findData(equalizer.MANUAL))
        if not self._change_timer.isActive():
            self._change_timer.start()

    def _picked(self, index: int) -> None:
        choice = self.presets.itemData(index)
        if choice == self.MAKE:
            self._fill_presets()            # show the current preset again meanwhile
            self.make_preset()
        elif choice == self.EDIT:
            self._fill_presets()
            EditPresetsDialog(self).exec()
        elif choice:
            self.settings.preset = choice
            self.refresh()
            self.changed.emit()

    def make_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Make Preset", "New Preset Name:")
        if not ok or not name.strip():
            return
        existing = self.settings.find(name.strip())
        if existing is not None and QMessageBox.question(
                self, "Make Preset", f"A preset called “{existing.name}” already exists. "
                "Do you want to replace it?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.settings.make_preset(name)
        except ValueError as problem:
            QMessageBox.warning(self, "Make Preset", str(problem))
            return
        self.refresh()
        self.changed.emit()


class EditPresetsDialog(QDialog):
    """Edit Presets: rename or delete presets."""

    def __init__(self, window: EqualizerWindow):
        super().__init__(window)
        self.window_ = window
        self.settings = window.settings
        self.setWindowTitle("Edit Presets")
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._rename())
        rename = QPushButton("Rename…")
        delete = QPushButton("Delete")
        done = QPushButton("Done")
        done.setDefault(True)
        rename.clicked.connect(self._rename)
        delete.clicked.connect(self._delete)
        done.clicked.connect(self.accept)
        self.list.currentRowChanged.connect(
            lambda row: (rename.setEnabled(row >= 0), delete.setEnabled(row >= 0)))
        buttons = QVBoxLayout()
        buttons.addWidget(rename)
        buttons.addWidget(delete)
        buttons.addStretch(1)
        buttons.addWidget(done)
        layout = QGridLayout(self)
        layout.addWidget(QLabel("Presets"), 0, 0)
        layout.addWidget(self.list, 1, 0)
        layout.addLayout(buttons, 1, 1)
        self.resize(340, 380)
        self._fill()

    def _fill(self, select: str | None = None) -> None:
        self.list.clear()
        self.list.addItems(self.settings.names())
        names = [n.casefold() for n in self.settings.names()]
        row = names.index(select.casefold()) if select and select.casefold() in names else 0
        self.list.setCurrentRow(row if names else -1)

    def _selected(self) -> str | None:
        item = self.list.currentItem()
        return item.text() if item else None

    def _rename(self) -> None:
        old = self._selected()
        if old is None:
            return
        new, ok = QInputDialog.getText(self, "Rename Preset", "New Preset Name:", text=old)
        if not ok or new.strip() == old:
            return
        try:
            self.settings.rename(old, new)
        except ValueError as problem:
            QMessageBox.warning(self, "Rename Preset", str(problem))
            return
        new = self.settings.find(new.strip()).name
        self._fill(new)
        self.window_.refresh()
        self.window_.preset_renamed.emit(old, new)
        self.window_.changed.emit()

    def _delete(self) -> None:
        name = self._selected()
        if name is None:
            return
        if QMessageBox.question(
                self, "Delete Preset", f"Are you sure you want to delete the preset “{name}”? "
                "Songs that use it will play with the equalizer's preset instead."
                ) != QMessageBox.StandardButton.Yes:
            return
        row = self.list.currentRow()
        self.settings.delete(name)
        names = self.settings.names()
        self._fill(names[min(row, len(names) - 1)] if names else None)
        self.window_.refresh()
        self.window_.preset_deleted.emit(name)
        self.window_.changed.emit()
