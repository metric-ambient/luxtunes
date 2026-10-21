"""Plays the player's audio through the equalizer.

QMediaPlayer has no way to change its sound, but (Qt 6.8 and newer) it can
hand over each piece of decoded audio as it plays it (QAudioBufferOutput).
While the equalizer is in use, the player's own output is muted and those
pieces go through the filter (core/equalizer.py) to an audio sink of ours.
The player's muted output keeps the timing, so the pieces arrive at exactly
the pace they're played.
"""

from __future__ import annotations

import ctypes
import logging

import numpy as np
from PySide6.QtCore import QObject, QTimer
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

from luxtunes.core import equalizer

try:
    from PySide6.QtMultimedia import QAudioBufferOutput
except ImportError:                       # Qt older than 6.8
    QAudioBufferOutput = None

log = logging.getLogger(__name__)

CHANNELS = 2
BUFFER_SECONDS = 0.2          # the sink's own buffer
MAX_BEHIND_SECONDS = 0.4      # more waiting than this (the window was busy): drop the oldest


def available() -> bool:
    return QAudioBufferOutput is not None


def _format(rate: int) -> QAudioFormat:
    fmt = QAudioFormat()
    fmt.setSampleRate(rate)
    fmt.setChannelCount(CHANNELS)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    return fmt


def _raw(buffer) -> bytes:
    """The buffer's bytes (PySide6 versions hand them over differently)."""
    size = buffer.byteCount()
    data = buffer.constData()
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)[:size]
    if hasattr(data, "toBytes"):                  # shiboken VoidPtr
        try:
            return bytes(data.toBytes())[:size]
        except Exception:
            pass
    return ctypes.string_at(int(data), size)


_DTYPES = {
    QAudioFormat.SampleFormat.Float: (np.float32, 1.0, 0.0),
    QAudioFormat.SampleFormat.Int16: (np.int16, 1 / 32768, 0.0),
    QAudioFormat.SampleFormat.Int32: (np.int32, 1 / 2147483648, 0.0),
    QAudioFormat.SampleFormat.UInt8: (np.uint8, 1 / 128, -128.0),
}


def samples(buffer) -> np.ndarray | None:
    """A QAudioBuffer as float32 stereo, shape (frames, 2)."""
    fmt = buffer.format()
    kind = _DTYPES.get(fmt.sampleFormat())
    channels = fmt.channelCount()
    if kind is None or channels < 1:
        return None
    dtype, scale, offset = kind
    data = np.frombuffer(_raw(buffer), dtype=dtype)
    data = data[: len(data) // channels * channels].reshape(-1, channels)
    data = (data.astype(np.float32) + offset) * scale
    if channels == 1:
        return np.repeat(data, 2, axis=1)
    return np.ascontiguousarray(data[:, :2])


class EqualizerOutput(QObject):
    def __init__(self, media_player, parent=None):
        super().__init__(parent)
        self._media_player = media_player
        self._rate = 0
        self._sink = None
        self._io = None
        self._filter = None
        self._preset = equalizer.Preset("Flat")
        self._volume = 1.0
        self._pending = bytearray()
        self._paused = False
        self._suspended = False            # our sink is suspended
        self._buffers = None
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._drain)

    @property
    def active(self) -> bool:
        return self._buffers is not None

    def start(self) -> None:
        if self.active or not available():
            return
        device = QMediaDevices.defaultAudioOutput()
        rate = device.preferredFormat().sampleRate() or 48000
        self._open(rate)
        self._buffers = QAudioBufferOutput(_format(rate), self)
        self._buffers.audioBufferReceived.connect(self._on_buffer)
        self._media_player.setAudioBufferOutput(self._buffers)
        self._timer.start()
        log.info("equalizer output started (%d Hz)", rate)

    def stop(self) -> None:
        if not self.active:
            return
        self._media_player.setAudioBufferOutput(None)
        self._buffers.deleteLater()
        self._buffers = None
        self._timer.stop()
        self._close()
        log.info("equalizer output stopped")

    def set_preset(self, preset: equalizer.Preset) -> None:
        self._preset = preset
        if self._filter is not None:
            self._filter.set_preset(preset)

    def set_volume(self, volume: float) -> None:
        self._volume = volume
        if self._sink is not None:
            self._sink.setVolume(volume)

    def flush(self) -> None:
        """Drop what's waiting to be heard (a seek or a new song)."""
        self._pending.clear()
        if self._filter is not None:
            self._filter.reset()
        if self._sink is not None:
            self._sink.stop()
            self._io = self._sink.start()
            self._suspended = False
            self.pause(self._paused)

    def pause(self, paused: bool) -> None:
        self._paused = paused
        if self._sink is None or paused == self._suspended:
            return
        if paused:
            self._sink.suspend()
        else:
            self._sink.resume()
        self._suspended = paused

    # ---- Internals -----------------------------------------------------------------

    def _open(self, rate: int) -> None:
        self._close()
        fmt = _format(rate)
        self._rate = rate
        self._sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt, self)
        self._sink.setBufferSize(int(rate * BUFFER_SECONDS) * fmt.bytesPerFrame())
        self._sink.setVolume(self._volume)
        self._io = self._sink.start()
        self._suspended = False
        self.pause(self._paused)
        self._filter = equalizer.StreamFilter(rate, CHANNELS)
        self._filter.set_preset(self._preset)

    def _close(self) -> None:
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
        self._sink = self._io = self._filter = None
        self._pending.clear()

    def _on_buffer(self, buffer) -> None:
        if not buffer.isValid() or buffer.frameCount() == 0:
            return                                # the end of a song
        try:
            rate = buffer.format().sampleRate()
            if rate and rate != self._rate:
                self._open(rate)
            block = samples(buffer)
            if block is None:
                return
            self._pending += self._filter.process(block).tobytes()
        except Exception:
            log.exception("equalizer: a piece of audio couldn't be processed")
            return
        frame = 4 * CHANNELS
        most = int(self._rate * MAX_BEHIND_SECONDS) * frame
        if len(self._pending) > most:
            del self._pending[: len(self._pending) - most]
        self._drain()

    def _drain(self) -> None:
        if self._io is None or not self._pending:
            return
        frame = 4 * CHANNELS
        free = self._sink.bytesFree() // frame * frame
        if free <= 0:
            return
        chunk = bytes(self._pending[:free])
        written = self._io.write(chunk)
        if written and written > 0:
            del self._pending[:written]
