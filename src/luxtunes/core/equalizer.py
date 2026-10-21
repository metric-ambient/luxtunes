"""The equalizer: presets, their settings, and the sound filter they make.

Ten bands (32 Hz to 16 kHz, one octave apart) plus a preamp, each -12 to
+12 dB. A preset's sound is the ten bands as peaking filters in a row; that
curve is turned into one FIR filter (the filters' own impulse response, so
it sounds the same as running them one after another) and applied to the
audio a block at a time with FFT convolution (overlap-add).

Settings are kept as JSON in the library (library_info "equalizer"), so
syncing can send them to a phone. A song can have its own preset
(tracks.equalizer, a preset name), used while the equalizer is on.

No Qt in here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np

BANDS = (32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)      # Hz
BAND_LABELS = ("32", "64", "125", "250", "500", "1K", "2K", "4K", "8K", "16K")
LIMIT = 12.0               # dB, either way
Q = 1.41                   # one octave wide
MANUAL = "Manual"


@dataclass(frozen=True)
class Preset:
    name: str
    bands: tuple[float, ...] = (0.0,) * len(BANDS)
    preamp: float = 0.0

    def __post_init__(self):
        bands = tuple(_clamp(b) for b in self.bands)
        if len(bands) != len(BANDS):
            raise ValueError("a preset has ten bands")
        object.__setattr__(self, "bands", bands)
        object.__setattr__(self, "preamp", _clamp(self.preamp))

    @property
    def flat(self) -> bool:
        return not self.preamp and not any(self.bands)

    def renamed(self, name: str) -> "Preset":
        return Preset(name, self.bands, self.preamp)

    def to_dict(self) -> dict:
        return {"name": self.name, "preamp": self.preamp, "bands": list(self.bands)}

    @classmethod
    def from_dict(cls, data: dict) -> "Preset":
        return cls(str(data["name"]), tuple(float(b) for b in data["bands"]),
                   float(data.get("preamp") or 0))


def _clamp(db) -> float:
    return round(max(-LIMIT, min(LIMIT, float(db))), 1)


# The usual curves for these names (dB, 32 Hz ... 16 kHz).
BUILT_IN = [Preset(name, bands) for name, bands in (
    ("Acoustic", (5.0, 4.9, 3.95, 1.05, 2.15, 1.75, 3.5, 4.1, 3.55, 2.15)),
    ("Bass Booster", (5.5, 4.25, 3.5, 2.5, 1.25, 0, 0, 0, 0, 0)),
    ("Bass Reducer", (-5.5, -4.25, -3.5, -2.5, -1.25, 0, 0, 0, 0, 0)),
    ("Classical", (4.75, 3.75, 3.0, 2.5, -1.75, -1.75, 0, 2.25, 3.25, 3.75)),
    ("Dance", (3.57, 6.55, 4.99, 0, 1.92, 3.65, 5.15, 4.54, 3.59, 0)),
    ("Deep", (4.95, 3.55, 1.75, 1.0, 2.85, 2.5, 1.45, -2.15, -3.55, -4.6)),
    ("Electronic", (4.25, 3.8, 1.2, 0, -2.15, 2.25, 0.85, 1.25, 3.95, 4.8)),
    ("Flat", (0,) * 10),
    ("Hip-Hop", (5.0, 4.25, 1.5, 3.0, -1.0, -1.0, 1.5, -0.5, 2.0, 3.0)),
    ("Jazz", (4.0, 3.0, 1.5, 2.25, -1.5, -1.5, 0, 1.5, 3.0, 3.75)),
    ("Latin", (4.5, 3.0, 0, 0, -1.5, -1.5, -1.5, 0, 3.0, 4.5)),
    ("Loudness", (6.0, 4.0, 0, 0, -2.0, 0, -1.0, -5.0, 5.0, 1.0)),
    ("Lounge", (-3.0, -1.5, -0.5, 1.5, 4.0, 2.5, 0, -1.5, 2.0, 1.0)),
    ("Piano", (3.0, 2.0, 0, 2.5, 3.0, 1.5, 3.5, 4.5, 3.0, 3.5)),
    ("Pop", (-1.5, -1.0, 0, 2.0, 4.0, 4.0, 2.0, 0, -1.0, -1.5)),
    ("R&B", (2.62, 6.92, 5.65, 1.33, -2.19, -1.5, 2.32, 2.65, 3.0, 3.75)),
    ("Rock", (5.0, 4.0, 3.0, 1.5, -0.5, -1.0, 0.5, 2.5, 3.5, 4.5)),
    ("Small Speakers", (5.5, 4.25, 3.5, 2.5, 1.25, 0, -1.25, -2.5, -3.5, -4.25)),
    ("Spoken Word", (-3.46, -0.47, 0, 0.69, 3.46, 4.61, 4.84, 4.28, 2.54, 0)),
    ("Treble Booster", (0, 0, 0, 0, 0, 1.25, 2.5, 3.5, 4.25, 5.5)),
    ("Treble Reducer", (0, 0, 0, 0, 0, -1.25, -2.5, -3.5, -4.25, -5.5)),
    ("Vocal Booster", (-1.5, -3.0, -3.0, 1.5, 3.75, 3.75, 3.0, 1.5, 0, -1.5)),
)]


# ---- Settings -------------------------------------------------------------------------------

@dataclass
class Settings:
    on: bool = False
    preset: str = "Flat"                    # chosen preset's name, or MANUAL
    manual: Preset = field(default_factory=lambda: Preset(MANUAL))
    presets: list[Preset] = field(default_factory=lambda: list(BUILT_IN))

    def find(self, name: str | None) -> Preset | None:
        if not name:
            return None
        folded = name.casefold()
        return next((p for p in self.presets if p.name.casefold() == folded), None)

    @property
    def current(self) -> Preset:
        """What the equalizer window shows."""
        return self.find(self.preset) or self.manual

    def for_song(self, track: dict | None) -> Preset | None:
        """What to play this song with: its own preset (if it still exists),
        else the window's. None while the equalizer is off."""
        if not self.on:
            return None
        return self.find((track or {}).get("equalizer")) or self.current

    def names(self) -> list[str]:
        return [p.name for p in self.presets]

    # Changes (the window's menu) ---------------------------------------------------------

    def set_manual(self, bands, preamp) -> None:
        """Moving a slider: the window now shows Manual with these values."""
        self.manual = Preset(MANUAL, tuple(bands), preamp)
        self.preset = MANUAL

    def make_preset(self, name: str) -> Preset:
        """Make Preset…: save what the window shows under a name (replacing a
        preset of that name)."""
        name = clean_name(name)
        preset = self.current.renamed(name)
        old = self.find(name)
        if old is not None:
            self.presets[self.presets.index(old)] = preset
        else:
            self.presets.append(preset)
            self.presets.sort(key=lambda p: p.name.casefold())
        self.preset = name
        return preset

    def rename(self, old: str, new: str) -> None:
        new = clean_name(new)
        preset = self.find(old)
        clash = self.find(new)
        if preset is None:
            raise KeyError(old)
        if clash is not None and clash is not preset:
            raise ValueError(f"There's already a preset called “{clash.name}”.")
        self.presets[self.presets.index(preset)] = preset.renamed(new)
        self.presets.sort(key=lambda p: p.name.casefold())
        if self.preset.casefold() == old.casefold():
            self.preset = new

    def delete(self, name: str) -> None:
        preset = self.find(name)
        if preset is None:
            return
        if self.preset.casefold() == name.casefold():
            self.manual = preset.renamed(MANUAL)      # the sound stays as it was
            self.preset = MANUAL
        self.presets.remove(preset)

    # Stored form ----------------------------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps({"on": self.on, "preset": self.preset,
                           "manual": self.manual.to_dict(),
                           "presets": [p.to_dict() for p in self.presets]})

    @classmethod
    def from_json(cls, text: str | None) -> "Settings":
        """Settings as stored; the defaults for anything missing or unreadable."""
        settings = cls()
        try:
            data = json.loads(text) if text else {}
            if not isinstance(data, dict):
                return settings
            settings.on = bool(data.get("on", False))
            if isinstance(data.get("presets"), list):
                settings.presets = [Preset.from_dict(p) for p in data["presets"]]
            if isinstance(data.get("manual"), dict):
                settings.manual = Preset.from_dict({**data["manual"], "name": MANUAL})
            settings.preset = str(data.get("preset") or "Flat")
        except (TypeError, ValueError, KeyError):
            return cls()
        return settings


def load(conn) -> Settings:
    row = conn.execute("SELECT value FROM library_info WHERE key = 'equalizer'").fetchone()
    return Settings.from_json(row[0] if row else None)


def save(conn, settings: Settings) -> None:
    with conn:
        conn.execute("INSERT INTO library_info (key, value) VALUES ('equalizer', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (settings.to_json(),))


def has_saved(conn) -> bool:
    return conn.execute("SELECT 1 FROM library_info WHERE key = 'equalizer'").fetchone() is not None


def clean_name(name: str) -> str:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("A preset needs a name.")
    if name.casefold() in (MANUAL.casefold(), "none"):
        raise ValueError(f"“{name}” can't be used as a preset name.")
    return name


# ---- The sound -------------------------------------------------------------------------------

def _peaking(f0: float, gain_db: float, rate: int, z: np.ndarray) -> np.ndarray:
    """A peaking filter's response at the points z (on the unit circle)."""
    a = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * f0 / rate
    alpha = np.sin(w0) / (2 * Q)
    cos = np.cos(w0)
    zi, zi2 = 1 / z, 1 / z ** 2
    top = (1 + alpha * a) - 2 * cos * zi + (1 - alpha * a) * zi2
    bottom = (1 + alpha / a) - 2 * cos * zi + (1 - alpha / a) * zi2
    return top / bottom


def response(preset: Preset, rate: int, freqs) -> np.ndarray:
    """The preset's gain (dB) at the given frequencies; for tests and drawing."""
    z = np.exp(1j * 2 * np.pi * np.asarray(freqs, dtype=float) / rate)
    total = np.full(z.shape, 10 ** (preset.preamp / 20), dtype=complex)
    for f0, gain in zip(BANDS, preset.bands):
        if gain and f0 < rate * 0.45:
            total *= _peaking(f0, gain, rate, z)
    return 20 * np.log10(np.abs(total))


def taps_for(rate: int) -> int:
    """Filter length: about 85 ms, long enough for the 32 Hz band to die away."""
    return 1 << int(np.ceil(np.log2(rate * 0.085)))


def design(preset: Preset, rate: int) -> np.ndarray:
    """The FIR filter for a preset at this sample rate (float32)."""
    if preset.flat:
        return np.ones(1, dtype=np.float32)
    taps = taps_for(rate)
    size = taps * 4                                  # fine enough to avoid wrap-around
    z = np.exp(1j * 2 * np.pi * np.arange(size // 2 + 1) / size)
    total = np.full(z.shape, 10 ** (preset.preamp / 20), dtype=complex)
    for f0, gain in zip(BANDS, preset.bands):
        if gain and f0 < rate * 0.45:
            total *= _peaking(f0, gain, rate, z)
    impulse = np.fft.irfft(total, size)[:taps]
    fade = taps // 8                                 # end smoothly
    impulse[-fade:] *= np.cos(np.linspace(0, np.pi / 2, fade)) ** 2
    return impulse.astype(np.float32)


class StreamFilter:
    """Runs audio through a preset, block by block, without gaps or clicks
    between blocks. Blocks are float32 arrays of shape (frames, channels)."""

    def __init__(self, rate: int, channels: int):
        self.rate, self.channels = rate, channels
        self.preset: Preset | None = None
        self._h = np.ones(1, dtype=np.float32)
        self._spectra: dict[int, np.ndarray] = {}
        self._tail = np.zeros((0, channels), dtype=np.float32)

    def set_preset(self, preset: Preset) -> None:
        if preset == self.preset:
            return
        self.preset = preset
        self._h = design(preset, self.rate)
        self._spectra = {}                    # the ringing of earlier audio carries on

    def reset(self) -> None:
        """Forget earlier audio (after a seek or a new song)."""
        self._tail = np.zeros((0, self.channels), dtype=np.float32)

    def process(self, block: np.ndarray) -> np.ndarray:
        frames = len(block)
        if frames == 0:
            return block
        h = self._h
        if len(h) == 1:
            out = block * h[0]
            if len(self._tail):
                full = np.zeros((max(frames, len(self._tail)), self.channels), dtype=np.float32)
                full[:frames] = out
                full[:len(self._tail)] += self._tail
                out, self._tail = full[:frames], full[frames:]
        else:
            length = frames + len(h) - 1
            size = 1 << int(np.ceil(np.log2(length)))
            spectrum = self._spectra.get(size)
            if spectrum is None:
                spectrum = self._spectra[size] = np.fft.rfft(h, size)[:, None]
            wet = np.fft.irfft(np.fft.rfft(block, size, axis=0) * spectrum, size, axis=0)
            full = np.zeros((max(length, len(self._tail)), self.channels), dtype=np.float32)
            full[:length] = wet[:length]
            full[:len(self._tail)] += self._tail
            out, self._tail = full[:frames], full[frames:]
        return np.clip(out, -1.0, 1.0).astype(np.float32, copy=False)
