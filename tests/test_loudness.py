"""Sound Check: loudness corrections from tags or measured."""

import shutil
import subprocess

import pytest

from luxtunes.core import loudness


def test_numbers():
    summary = "Summary:\n\n  Integrated loudness:\n    I:         -23.0 LUFS\n"
    assert loudness.parse_ebur128(summary) == -23.0
    assert loudness.parse_ebur128("nothing") is None
    assert loudness.factor(None) == 1.0 and abs(loudness.factor(-6) - 0.501) < 0.001
    assert loudness.ipod_soundcheck(None) == 0 and loudness.ipod_soundcheck(0.0) == 1000
    assert loudness.ipod_soundcheck(-6.0) == 3981


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg")
def test_measuring_a_quiet_tone(tmp_path):
    tone = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=1000:duration=5:sample_rate=48000", str(tone)], check=True)
    assert 2.5 < loudness.measure(str(tone)) < 3.5     # -21 LUFS: up 3 dB to -18
    for volume, expected in (("8", -15.0), ("0.1", loudness.MAX_BOOST)):
        other = tmp_path / f"tone {volume}.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(tone), "-af",
                        f"volume={volume}", str(other)], check=True)
        assert abs(loudness.measure(str(other)) - expected) < 0.5   # loud: down; quiet: +6 at most


def test_replaygain_tag_wins(tmp_path):
    pytest.importorskip("mutagen")
    from mutagen.id3 import ID3, TXXX
    song = tmp_path / "song.mp3"
    song.write_bytes((b"\xff\xfb\x90\x64" + b"\x00" * 413) * 30)
    tags = ID3()
    tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-7.25 dB"]))
    tags.save(str(song))
    assert loudness.from_tags(str(song)) == -7.25
