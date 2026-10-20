"""Import Settings: which encoder CDs are imported with, and how (the
Preferences → General → Import Settings…).

Every choice turns into an FFmpeg command. The audio coming in is CD audio
(44.1 kHz, 16-bit stereo). Saved in the library database (library_info),
so the background import sees the same settings. No Qt in here.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, fields

KEY = "cd_import_settings"

AAC, AIFF, ALAC, MP3, WAV = ("AAC Encoder", "AIFF Encoder", "Apple Lossless Encoder",
                             "MP3 Encoder", "WAV Encoder")
ENCODERS = [AAC, AIFF, ALAC, MP3, WAV]
CUSTOM = "Custom..."
AUTOMATIC = "Automatic"

# The Setting menu for each encoder: (label, stereo bit rate in kbps).
PRESETS = {
    AAC: [("High Quality (128 kbps)", 128), ("Higher Quality (256 kbps)", 256), ("Spoken Podcast", 64)],
    MP3: [("Poor Quality (128 kbps)", 128), ("Moderate Quality (160 kbps)", 160),
          ("High Quality (192 kbps)", 192), ("Higher Quality (256 kbps)", 256)],
    AIFF: [(AUTOMATIC, 0)],
    WAV: [(AUTOMATIC, 0)],
    ALAC: [(AUTOMATIC, 0)],
}

MP3_BIT_RATES = [16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
AAC_BIT_RATES = [16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 288, 320]
VBR_QUALITIES = ["Lowest", "Low", "Medium Low", "Medium", "Medium High", "High", "Highest"]
LAME_VBR = {"Lowest": 9, "Low": 7, "Medium Low": 6, "Medium": 4, "Medium High": 3, "High": 2,
            "Highest": 0}           # LAME's -V levels (0 is best)
SAMPLE_RATES = [0, 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000]   # 0 = Auto
CHANNELS = ["Auto", "Mono", "Stereo"]
STEREO_MODES = ["Joint Stereo", "Normal"]
SAMPLE_SIZES = [0, 8, 16]                                                         # 0 = Auto


# What Setting jumps to when you pick an encoder.
DEFAULT_SETTING = {AAC: "Higher Quality (256 kbps)", MP3: "High Quality (192 kbps)", AIFF: AUTOMATIC,
                   WAV: AUTOMATIC, ALAC: AUTOMATIC}


def setting_names(encoder: str) -> list[str]:
    names = [label for label, _ in PRESETS[encoder]]
    return names if encoder == ALAC else names + [CUSTOM]


def sample_rate_label(rate: int) -> str:
    return "Auto" if not rate else f"{rate / 1000:.3f} kHz"


@dataclass
class ImportSettings:
    encoder: str = MP3
    setting: str = CUSTOM
    error_correction: bool = False
    # MP3 Encoder → Custom...
    mp3_bit_rate: int = 320
    mp3_vbr: bool = False
    mp3_quality: str = "Medium"
    mp3_sample_rate: int = 0
    mp3_channels: str = "Auto"
    mp3_stereo_mode: str = "Joint Stereo"
    mp3_smart: bool = True
    mp3_filter: bool = True
    # AAC Encoder → Custom...
    aac_bit_rate: int = 256
    aac_sample_rate: int = 0
    aac_channels: str = "Auto"
    # AIFF / WAV Encoder → Custom...
    pcm_sample_rate: int = 0
    pcm_sample_size: int = 0
    pcm_channels: str = "Auto"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str | None) -> "ImportSettings":
        settings = cls()
        try:
            data = json.loads(text or "{}")
        except ValueError:
            data = {}
        known = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key in known:
                setattr(settings, key, value)
        if settings.setting == "iTunes Plus":            # its name before LuxTunes 0.2
            settings.setting = "Higher Quality (256 kbps)"
        if settings.encoder not in ENCODERS:
            settings.encoder = MP3
        if settings.setting not in setting_names(settings.encoder):
            settings.setting = setting_names(settings.encoder)[0]
        return settings

    @property
    def custom(self) -> bool:
        return self.setting == CUSTOM

    def preset_bit_rate(self) -> int:
        return dict(PRESETS[self.encoder]).get(self.setting, 0)


def load(conn: sqlite3.Connection) -> ImportSettings:
    row = conn.execute("SELECT value FROM library_info WHERE key = ?", (KEY,)).fetchone()
    return ImportSettings.from_json(row[0] if row else None)


def save(conn: sqlite3.Connection, settings: ImportSettings) -> None:
    with conn:
        conn.execute("INSERT INTO library_info (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (KEY, settings.to_json()))


def extension(settings: ImportSettings) -> str:
    return {AAC: ".m4a", ALAC: ".m4a", MP3: ".mp3", AIFF: ".aif", WAV: ".wav"}[settings.encoder]


@dataclass
class Plan:
    """What the encoder will actually do."""
    codec: str
    bit_rate: int = 0               # kbps for stereo (mono gets half); 0 = lossless/PCM
    vbr: bool = False
    quality: str = ""
    sample_rate: int = 0            # 0 = keep 44.1 kHz
    channels: str = "Auto"
    joint_stereo: bool = True
    filter_low: bool = False
    sample_size: int = 0


def plan(settings: ImportSettings) -> Plan:
    s = settings
    if s.encoder == MP3:
        if s.custom:
            sample_rate = s.mp3_sample_rate
            if s.mp3_smart and not s.mp3_vbr and not sample_rate:
                # Smart Encoding Adjustments: low bit rates sound better at a lower sample rate.
                sample_rate = 22050 if s.mp3_bit_rate <= 48 else 32000 if s.mp3_bit_rate <= 96 else 0
            return Plan("libmp3lame", s.mp3_bit_rate, s.mp3_vbr, s.mp3_quality, sample_rate,
                        s.mp3_channels, s.mp3_stereo_mode == "Joint Stereo", s.mp3_filter)
        return Plan("libmp3lame", s.preset_bit_rate(), filter_low=True)
    if s.encoder == AAC:
        if s.custom:
            return Plan("aac", s.aac_bit_rate, sample_rate=s.aac_sample_rate, channels=s.aac_channels)
        spoken = s.setting == "Spoken Podcast"
        return Plan("aac", s.preset_bit_rate(), sample_rate=22050 if spoken else 0)
    if s.encoder == ALAC:
        return Plan("alac")
    codec = "pcm_s16be" if s.encoder == AIFF else "pcm_s16le"
    if s.custom:
        size = s.pcm_sample_size
        if size == 8:
            codec = "pcm_s8" if s.encoder == AIFF else "pcm_u8"
        return Plan(codec, sample_rate=s.pcm_sample_rate, channels=s.pcm_channels, sample_size=size)
    return Plan(codec)


def ffmpeg_command(ffmpeg: str, input_args: list[str], target: str,
                   settings: ImportSettings) -> list[str]:
    """FFmpeg reading `input_args` and writing `target` with these settings.
    No tags are written here (LuxTunes writes them afterwards)."""
    p = plan(settings)
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *input_args,
           "-map_metadata", "-1", "-vn", "-codec:a", p.codec]
    mono = p.channels == "Mono"
    if p.channels in ("Mono", "Stereo"):
        cmd += ["-ac", "1" if mono else "2"]
    if p.sample_rate:
        cmd += ["-ar", str(p.sample_rate)]
    if p.codec == "libmp3lame":
        if p.vbr:
            cmd += ["-q:a", str(LAME_VBR.get(p.quality, 4))]
        else:
            cmd += ["-b:a", f"{p.bit_rate // 2 if mono else p.bit_rate}k"]
        cmd += ["-joint_stereo", "1" if p.joint_stereo else "0"]
        if p.filter_low:
            cmd += ["-af", "highpass=f=10"]
    elif p.codec == "aac":
        cmd += ["-b:a", f"{p.bit_rate // 2 if mono else p.bit_rate}k", "-movflags", "+faststart"]
    elif p.codec == "alac":
        cmd += ["-movflags", "+faststart"]
    if p.codec in ("aac", "alac"):
        cmd += ["-f", "ipod"]                     # .m4a
    elif p.codec.startswith("pcm") and settings.encoder == AIFF:
        cmd += ["-f", "aiff"]
    elif p.codec.startswith("pcm"):
        cmd += ["-f", "wav"]
    else:
        cmd += ["-f", "mp3", "-id3v2_version", "0", "-write_xing", "1"]
    return cmd + [target]


def details(settings: ImportSettings) -> str:
    """The text in the Details box."""
    p = plan(settings)
    rate = sample_rate_label(p.sample_rate) if p.sample_rate else "44.100 kHz"
    channels = {"Mono": "mono", "Stereo": "stereo"}.get(p.channels, "")
    if p.codec in ("libmp3lame", "aac"):
        if p.codec == "libmp3lame" and p.vbr:
            parts = [f"VBR, {p.quality.lower()} quality"]
        else:
            parts = [f"{p.bit_rate // 2} kbps (mono)/{p.bit_rate} kbps (stereo)"]
        if p.sample_rate:
            parts.append(rate)
        if channels:
            parts.append(channels)
        if p.codec == "libmp3lame":
            parts.append("joint stereo" if p.joint_stereo else "normal stereo")
            if p.filter_low:
                parts.append("frequencies below 10 Hz filtered")
            text = ", ".join(parts) + ", using LAME."
        else:
            text = ", ".join(parts) + ", using FFmpeg's AAC encoder."
        return text
    if p.codec == "alac":
        return "Apple Lossless: the CD's exact audio, about half the size of a WAV file."
    size = f"{p.sample_size}-bit" if p.sample_size else "16-bit"
    parts = [rate if p.sample_rate else "Automatic", size, channels or "stereo"]
    return ", ".join(parts) + " (uncompressed)."
