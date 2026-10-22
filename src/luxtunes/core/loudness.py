"""Sound Check: songs play at about the same loudness.

Each song gets a correction in dB (tracks.loudness): taken from its
ReplayGain tag when it has one, or else from the volume note older libraries
leave in the file (iTunNORM), or else measured with FFmpeg (EBU R128). The
target is -18 LUFS, as ReplayGain 2. The player applies it only while
Preferences → Playback → Sound Check is on, and never boosts a song by more
than MAX_BOOST (most corrections turn loud songs down).

The correction also goes to iPods (their own Sound Check setting) and phones.
No Qt in here.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess

TARGET_LUFS = -18.0
MAX_BOOST = 6.0
MAX_CUT = -18.0

_DB = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*dB", re.IGNORECASE)


def _clamp(gain: float) -> float:
    return round(max(MAX_CUT, min(MAX_BOOST, gain)), 2)


def _replaygain(audio) -> float | None:
    """REPLAYGAIN_TRACK_GAIN from ID3 (TXXX), Vorbis comments or MP4 freeform tags."""
    tags = getattr(audio, "tags", None)
    if not tags:
        return None
    candidates = []
    try:
        for key in tags.keys():
            name = str(key).lower()
            if "replaygain_track_gain" in name:
                candidates.append(tags[key])
    except Exception:
        return None
    for value in candidates:
        texts = getattr(value, "text", value)
        if not isinstance(texts, (list, tuple)):
            texts = [texts]
        for text in texts:
            if isinstance(text, bytes):
                text = text.decode("utf-8", "replace")
            match = _DB.search(str(text))
            if match:
                return float(match.group(1))
    return None


def _itunnorm(audio) -> float | None:
    """Older players' volume note: ten hex numbers; the first two are
    1000 × 10^(-gain/10) for the left and right channels."""
    tags = getattr(audio, "tags", None)
    if not tags:
        return None
    texts = []
    try:
        for key in tags.keys():
            if "itunnorm" in str(key).lower():
                value = tags[key]
                items = getattr(value, "text", value)
                if isinstance(items, (bytes, str)) or not isinstance(items, (list, tuple)):
                    items = [items]
                for item in items:
                    texts.append(item.decode("latin-1") if isinstance(item, bytes) else str(item))
    except Exception:
        return None
    for text in texts:
        numbers = text.split()
        try:
            loudest = max(int(numbers[0], 16), int(numbers[1], 16))
        except (IndexError, ValueError):
            continue
        if loudest > 0:
            return -10 * math.log10(loudest / 1000)
    return None


def from_tags(path: str) -> float | None:
    """The correction the file's own tags give, or None."""
    try:
        import mutagen
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None:
        return None
    gain = _replaygain(audio)
    if gain is None:
        gain = _itunnorm(audio)
    return None if gain is None else _clamp(gain)


def ffmpeg_command(ffmpeg: str, path: str) -> list[str]:
    return [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0",
            "-vn", "-af", "ebur128=framelog=quiet", "-f", "null", "-"]


def parse_ebur128(output: str) -> float | None:
    """The integrated loudness (LUFS) from FFmpeg's ebur128 summary."""
    summary = output[output.rfind("Summary:"):] if "Summary:" in output else output
    match = re.search(r"\bI:\s*([-+]?\d+(?:\.\d+)?)\s*LUFS", summary)
    return float(match.group(1)) if match else None


def measure(path: str, timeout: float = 600) -> float | None:
    """Measure with FFmpeg (needs it installed). The correction, or None."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    try:
        done = subprocess.run(ffmpeg_command(ffmpeg, path), capture_output=True, text=True,
                              timeout=timeout, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    lufs = parse_ebur128(done.stderr or "")
    if lufs is None or lufs < -70:            # silence: leave it alone
        return None
    return _clamp(TARGET_LUFS - lufs)


def find(path: str) -> float | None:
    """Tags first (instant), else measure."""
    gain = from_tags(path)
    return gain if gain is not None else measure(path)


def factor(gain: float | None) -> float:
    """The volume multiplier for a correction in dB."""
    return 10 ** ((gain or 0.0) / 20)


def ipod_soundcheck(gain: float | None) -> int:
    """The iPod's own Sound Check value for a correction (0 = none)."""
    if gain is None:
        return 0
    return max(1, round(1000 * 10 ** (-gain / 10)))

