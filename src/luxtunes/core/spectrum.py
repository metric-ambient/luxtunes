"""What the visualizer listens to: the loudness of a few frequency bands, from
the audio as it plays.

Fed the decoded audio in pieces (float stereo); about 60 times a second of
audio (at most once a piece) it looks at the last FFT_SIZE samples and
reports BANDS levels, from the bass up, each 0..1. No Qt in here, so it's
unit-tested directly.
"""

from __future__ import annotations

import numpy as np

FFT_SIZE = 2048
BANDS = 16
LOW_HZ, HIGH_HZ = 40.0, 16_000.0
PER_SECOND = 60                     # reports per second of audio
FLOOR_DB = -70.0                    # quieter than this counts as silence


class Analyzer:
    def __init__(self, rate: int):
        self.rate = int(rate) or 48_000
        self._ring = np.zeros(FFT_SIZE, dtype=np.float32)
        self._window = np.hanning(FFT_SIZE).astype(np.float32)
        self._since = 0
        self._hop = max(1, self.rate // PER_SECOND)
        freqs = np.fft.rfftfreq(FFT_SIZE, 1 / self.rate)
        edges = np.geomspace(LOW_HZ, min(HIGH_HZ, self.rate / 2 - 1), BANDS + 1)
        # Each band: the FFT bins inside it (at least one, so bass bands aren't empty).
        self._bins = []
        for low, high in zip(edges, edges[1:]):
            inside = np.nonzero((freqs >= low) & (freqs < high))[0]
            if inside.size == 0:
                inside = np.array([int(np.argmin(np.abs(freqs - (low + high) / 2)))])
            self._bins.append(inside)

    def feed(self, block: np.ndarray) -> np.ndarray | None:
        """block: (frames, channels) float. Returns the band levels when a
        new report is due, otherwise None."""
        if block is None or len(block) == 0:
            return None
        mono = block.mean(axis=1) if block.ndim == 2 else block
        mono = mono.astype(np.float32, copy=False)[-FFT_SIZE:]
        self._ring = np.roll(self._ring, -len(mono))
        self._ring[-len(mono):] = mono
        self._since += len(block)
        if self._since < self._hop:
            return None
        self._since = min(self._since - self._hop, self._hop - 1)   # one report per piece at most
        return self.levels()

    def levels(self) -> np.ndarray:
        spectrum = np.abs(np.fft.rfft(self._ring * self._window)) / (FFT_SIZE / 4)
        power = np.array([float(np.sqrt(np.mean(spectrum[b] ** 2))) for b in self._bins])
        db = 20 * np.log10(np.maximum(power, 1e-9))
        return np.clip((db - FLOOR_DB) / -FLOOR_DB, 0.0, 1.0).astype(np.float32)
