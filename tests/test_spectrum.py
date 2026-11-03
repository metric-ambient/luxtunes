"""The visualizer's band levels (core/spectrum.py)."""

import numpy as np

from luxtunes.core import spectrum


def _tone(hz, seconds=1.0, rate=48_000):
    t = np.arange(int(rate * seconds)) / rate
    mono = (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    return np.stack([mono, mono], axis=1)


def _last_levels(analyzer, audio, piece=480):
    levels, reports = None, 0
    for start in range(0, len(audio), piece):
        new = analyzer.feed(audio[start:start + piece])
        if new is not None:
            levels, reports = new, reports + 1
    return levels, reports


def test_a_low_note_lights_the_bass_and_a_high_one_the_treble():
    low, _ = _last_levels(spectrum.Analyzer(48_000), _tone(60))
    high, _ = _last_levels(spectrum.Analyzer(48_000), _tone(5_000))
    assert int(np.argmax(low)) <= 2 and int(np.argmax(high)) >= 10
    assert low.shape == (spectrum.BANDS,) and 0 <= low.min() and high.max() <= 1


def test_about_sixty_reports_a_second_and_silence_is_dark():
    analyzer = spectrum.Analyzer(44_100)
    quiet, reports = _last_levels(analyzer, np.zeros((44_100, 2), dtype=np.float32))
    assert 55 <= reports <= 61
    assert quiet.max() == 0
