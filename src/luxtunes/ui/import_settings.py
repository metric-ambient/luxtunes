"""Import Settings… (from the CD page or Preferences → General):
Import Using (the encoder), Setting (a preset or Custom...), the Details of
what that means, and "Use error correction when reading Audio CDs".
Choosing Custom... opens the encoder's own window beside it.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from luxtunes.core import encoders
from luxtunes.core.encoders import ImportSettings


def _combo(items, current, label=str) -> QComboBox:
    combo = QComboBox()
    for item in items:
        combo.addItem(label(item), item)
    index = combo.findData(current)
    combo.setCurrentIndex(max(0, index))
    return combo


def _ok_cancel(dialog: QDialog, extra: QPushButton | None = None) -> QHBoxLayout:
    row = QHBoxLayout()
    if extra is not None:
        row.addWidget(extra)
    row.addStretch(1)
    ok, cancel = QPushButton("OK"), QPushButton("Cancel")
    ok.setDefault(True)
    ok.clicked.connect(dialog.accept)
    cancel.clicked.connect(dialog.reject)
    row.addWidget(ok)
    row.addWidget(cancel)
    return row


class CustomEncoderDialog(QDialog):
    """MP3 Encoder / AAC Encoder / AIFF Encoder / WAV Encoder custom settings."""

    def __init__(self, settings: ImportSettings, parent=None):
        super().__init__(parent)
        self.settings = copy.deepcopy(settings)
        self.encoder = settings.encoder
        self.setWindowTitle(self.encoder)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        s = self.settings
        rates = lambda r: encoders.sample_rate_label(r)
        if self.encoder == encoders.MP3:
            self.bit_rate = _combo(encoders.MP3_BIT_RATES, s.mp3_bit_rate, lambda k: f"{k} kbps")
            self.vbr = QCheckBox("Use Variable Bit Rate Encoding (VBR)")
            self.vbr.setChecked(s.mp3_vbr)
            self.quality = _combo(encoders.VBR_QUALITIES, s.mp3_quality)
            note = QLabel("(With VBR enabled, the Quality setting decides the bit rate.)")
            note.setObjectName("dialogNote")
            self.sample_rate = _combo(encoders.SAMPLE_RATES, s.mp3_sample_rate, rates)
            self.channels = _combo(encoders.CHANNELS, s.mp3_channels)
            self.stereo_mode = _combo(encoders.STEREO_MODES, s.mp3_stereo_mode)
            self.smart = QCheckBox("Smart Encoding Adjustments")
            self.smart.setChecked(s.mp3_smart)
            self.smart.setToolTip("Lowers the sample rate for very low bit rates, which sounds better.")
            self.filter = QCheckBox("Filter Frequencies Below 10 Hz")
            self.filter.setChecked(s.mp3_filter)
            form.addRow("Stereo Bit Rate:", self.bit_rate)
            form.addRow("", self.vbr)
            form.addRow("Quality:", self.quality)
            form.addRow(note)
            form.addRow("Sample Rate:", self.sample_rate)
            form.addRow("Channels:", self.channels)
            form.addRow("Stereo Mode:", self.stereo_mode)
            form.addRow("", self.smart)
            form.addRow("", self.filter)
            self.vbr.toggled.connect(self.quality.setEnabled)
            self.quality.setEnabled(s.mp3_vbr)
        elif self.encoder == encoders.AAC:
            self.bit_rate = _combo(encoders.AAC_BIT_RATES, s.aac_bit_rate, lambda k: f"{k} kbps")
            self.sample_rate = _combo(encoders.SAMPLE_RATES, s.aac_sample_rate, rates)
            self.channels = _combo(encoders.CHANNELS, s.aac_channels)
            form.addRow("Stereo Bit Rate:", self.bit_rate)
            form.addRow("Sample Rate:", self.sample_rate)
            form.addRow("Channels:", self.channels)
        else:                                   # AIFF / WAV
            self.sample_rate = _combo(encoders.SAMPLE_RATES, s.pcm_sample_rate, rates)
            self.sample_size = _combo(encoders.SAMPLE_SIZES, s.pcm_sample_size,
                                      lambda b: f"{b} bit" if b else "Auto")
            self.channels = _combo(encoders.CHANNELS, s.pcm_channels)
            form.addRow("Sample Rate:", self.sample_rate)
            form.addRow("Sample Size:", self.sample_size)
            form.addRow("Channels:", self.channels)

        defaults = QPushButton("Use Default Settings")
        defaults.clicked.connect(self._defaults)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addSpacing(8)
        layout.addLayout(_ok_cancel(self, defaults))

    def _defaults(self):
        fresh = ImportSettings()
        if self.encoder == encoders.MP3:
            pairs = [(self.bit_rate, fresh.mp3_bit_rate), (self.quality, fresh.mp3_quality),
                     (self.sample_rate, fresh.mp3_sample_rate), (self.channels, fresh.mp3_channels),
                     (self.stereo_mode, fresh.mp3_stereo_mode)]
            self.vbr.setChecked(fresh.mp3_vbr)
            self.smart.setChecked(fresh.mp3_smart)
            self.filter.setChecked(fresh.mp3_filter)
        elif self.encoder == encoders.AAC:
            pairs = [(self.bit_rate, fresh.aac_bit_rate), (self.sample_rate, fresh.aac_sample_rate),
                     (self.channels, fresh.aac_channels)]
        else:
            pairs = [(self.sample_rate, fresh.pcm_sample_rate), (self.sample_size, fresh.pcm_sample_size),
                     (self.channels, fresh.pcm_channels)]
        for combo, value in pairs:
            combo.setCurrentIndex(max(0, combo.findData(value)))

    def result_settings(self) -> ImportSettings:
        s = self.settings
        if self.encoder == encoders.MP3:
            s.mp3_bit_rate = self.bit_rate.currentData()
            s.mp3_vbr = self.vbr.isChecked()
            s.mp3_quality = self.quality.currentData()
            s.mp3_sample_rate = self.sample_rate.currentData()
            s.mp3_channels = self.channels.currentData()
            s.mp3_stereo_mode = self.stereo_mode.currentData()
            s.mp3_smart = self.smart.isChecked()
            s.mp3_filter = self.filter.isChecked()
        elif self.encoder == encoders.AAC:
            s.aac_bit_rate = self.bit_rate.currentData()
            s.aac_sample_rate = self.sample_rate.currentData()
            s.aac_channels = self.channels.currentData()
        else:
            s.pcm_sample_rate = self.sample_rate.currentData()
            s.pcm_sample_size = self.sample_size.currentData()
            s.pcm_channels = self.channels.currentData()
        s.setting = encoders.CUSTOM
        return s


class ImportSettingsDialog(QDialog):
    def __init__(self, settings: ImportSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Settings")
        self.settings = copy.deepcopy(settings)
        self._previous_setting = settings.setting

        self.encoder = _combo(encoders.ENCODERS, settings.encoder)
        self.setting = QComboBox()
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.details.setMinimumHeight(56)
        self.details.setFrameShape(QFrame.Shape.StyledPanel)
        self.details.setContentsMargins(6, 4, 6, 4)
        details_box = QVBoxLayout()
        title = QLabel("Details")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        details_box.addWidget(title)
        details_box.addWidget(self.details)

        self.error_correction = QCheckBox("Use error correction when reading Audio CDs")
        self.error_correction.setChecked(settings.error_correction)
        hint = QLabel("Use this option if you experience problems with the audio quality from "
                      "Audio CDs. This may reduce the speed of importing.")
        hint.setWordWrap(True)
        hint.setContentsMargins(22, 0, 0, 0)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("Import Using:", self.encoder)
        form.addRow("Setting:", self.setting)
        form.addRow("", details_box)

        help_button = QPushButton("?")
        help_button.setFixedWidth(28)
        help_button.clicked.connect(self._help)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error_correction)
        layout.addWidget(hint)
        layout.addSpacing(8)
        layout.addLayout(_ok_cancel(self, help_button))
        self.setMinimumWidth(420)

        self._fill_settings()
        self.encoder.currentIndexChanged.connect(self._encoder_changed)
        self.setting.activated.connect(self._setting_chosen)

    def _fill_settings(self):
        self.setting.blockSignals(True)
        self.setting.clear()
        names = encoders.setting_names(self.settings.encoder)
        self.setting.addItems(names)
        if self.settings.setting not in names:
            self.settings.setting = encoders.DEFAULT_SETTING[self.settings.encoder]
        self.setting.setCurrentText(self.settings.setting)
        self._previous_setting = self.settings.setting
        self.setting.blockSignals(False)
        self.details.setText(encoders.details(self.settings))

    def _encoder_changed(self):
        """A different encoder starts at its standard setting (AAC: Higher Quality)."""
        self.settings.encoder = self.encoder.currentData()
        self.settings.setting = encoders.DEFAULT_SETTING[self.settings.encoder]
        self._fill_settings()

    def _setting_chosen(self, index: int):
        name = self.setting.itemText(index)
        if name == encoders.CUSTOM:
            dialog = CustomEncoderDialog(self.settings, self)
            dialog.move(self.frameGeometry().topRight().x() + 8, self.frameGeometry().top())
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.settings = dialog.result_settings()
            else:
                self.setting.setCurrentText(self._previous_setting)   # nothing changed
                return
        else:
            self.settings.setting = name
        self._previous_setting = self.settings.setting
        self.details.setText(encoders.details(self.settings))

    def _help(self):
        QMessageBox.information(
            self, "Import Settings",
            "MP3: plays everywhere, including every iPod.\n"
            "AAC: a little better than MP3 at the same bit rate (.m4a).\n"
            "Apple Lossless: the CD's exact sound at about half the size of WAV (.m4a; iPod "
            "Classics play it).\n"
            "AIFF / WAV: the CD's exact sound, uncompressed (large files).\n\n"
            "Higher bit rates sound better and make bigger files. 256 or 320 kbps is hard to tell "
            "from the CD.")

    def result_settings(self) -> ImportSettings:
        self.settings.error_correction = self.error_correction.isChecked()
        return self.settings
