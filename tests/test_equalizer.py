"""The equalizer's presets, settings and sound filter (no Qt)."""

import numpy as np
import pytest

from luxtunes.core import db, equalizer, library
from luxtunes.core.equalizer import Preset, Settings, StreamFilter

RATE = 48000


def test_built_in_presets():
    names = [p.name for p in equalizer.BUILT_IN]
    assert names == sorted(names, key=str.casefold) and len(names) == 22
    assert "Flat" in names and "R&B" in names and "Vocal Booster" in names
    for preset in equalizer.BUILT_IN:
        assert len(preset.bands) == 10 and all(-12 <= b <= 12 for b in preset.bands)
    assert Settings().find("rock").bands == (5.0, 4.0, 3.0, 1.5, -0.5, -1.0, 0.5, 2.5, 3.5, 4.5)


def test_values_are_kept_in_range():
    preset = Preset("Loud", (20,) * 10, -30)
    assert preset.bands == (12.0,) * 10 and preset.preamp == -12.0
    with pytest.raises(ValueError):
        Preset("Short", (1, 2))


def test_settings_round_trip_and_bad_text():
    settings = Settings(on=True)
    settings.set_manual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], -2)
    settings.make_preset("  My   Sound ")
    again = Settings.from_json(settings.to_json())
    assert again.on and again.preset == "My Sound"
    assert again.current.bands[:3] == (1.0, 2.0, 3.0) and again.current.preamp == -2.0
    assert "My Sound" in again.names()
    for text in (None, "", "not json", "[1, 2]", '{"presets": [{"name": "x"}]}'):
        assert Settings.from_json(text).names() == Settings().names()


def test_moving_a_slider_means_manual():
    settings = Settings(preset="Rock")
    settings.set_manual([0] * 9 + [3], 1)
    assert settings.preset == equalizer.MANUAL and settings.current.bands[-1] == 3
    assert settings.find("Rock").bands[-1] == 4.5          # the preset itself is unchanged


def test_make_rename_delete():
    settings = Settings(preset="Jazz")
    settings.make_preset("jazz")                            # same name: replaces it
    assert settings.names().count("jazz") == 1 and "Jazz" not in settings.names()
    settings.rename("Jazz", "Smooth")
    assert settings.preset == "Smooth" and settings.find("jazz") is None
    with pytest.raises(ValueError):
        settings.rename("Smooth", "Rock")                   # taken
    for bad in ("", "  ", "Manual", "none"):
        with pytest.raises(ValueError):
            settings.make_preset(bad)
    sound = settings.current
    settings.delete("Smooth")                               # the chosen one: same sound, Manual
    assert settings.preset == equalizer.MANUAL and settings.current.bands == sound.bands
    assert settings.find("Smooth") is None


def test_preset_for_a_song():
    settings = Settings(preset="Pop")
    assert settings.for_song({"equalizer": "Rock"}) is None          # off
    settings.on = True
    assert settings.for_song({"equalizer": "rock"}).name == "Rock"   # its own
    assert settings.for_song({"equalizer": "Gone"}).name == "Pop"    # deleted: the window's
    assert settings.for_song({}).name == "Pop"


def _tone(freq, seconds=1.0, level=0.1):
    t = np.arange(int(RATE * seconds)) / RATE
    return np.repeat((level * np.sin(2 * np.pi * freq * t))[:, None], 2, axis=1).astype(np.float32)


def _gain_db(preset, freq):
    f = StreamFilter(RATE, 2)
    f.set_preset(preset)
    tone = _tone(freq)
    out = f.process(tone)
    rms = lambda a: np.sqrt(np.mean(a[RATE // 2:] ** 2))      # after it settles
    return 20 * np.log10(rms(out) / rms(tone))


def test_the_filter_changes_the_sound_as_drawn():
    bass = Settings().find("Bass Booster")
    for freq in (50, 125, 1000, 8000):
        assert _gain_db(bass, freq) == pytest.approx(equalizer.response(bass, RATE, [freq])[0],
                                                     abs=0.2)
    assert _gain_db(bass, 50) > 5 and abs(_gain_db(bass, 8000)) < 0.1
    quieter = Preset("Quieter", (0,) * 10, -6)
    assert _gain_db(quieter, 1000) == pytest.approx(-6, abs=0.05)


def test_flat_leaves_the_sound_alone():
    f = StreamFilter(RATE, 2)
    f.set_preset(Settings().find("Flat"))
    block = _tone(440, 0.1)
    assert np.array_equal(f.process(block), block)


def test_blocks_join_up_exactly():
    """Pieces of any size give the same sound as filtering all at once."""
    preset = Settings().find("Classical")
    h = equalizer.design(preset, RATE)
    audio = (np.random.default_rng(0).standard_normal((RATE // 2, 2)) * 0.05).astype(np.float32)
    f = StreamFilter(RATE, 2)
    f.set_preset(preset)
    pieces, at = [], 0
    for size in [1, 7, 480, 4096, 9000, 333] * 20:
        pieces.append(f.process(audio[at:at + size]))
        at += size
    out = np.concatenate(pieces)
    whole = np.stack([np.convolve(audio[:, c], h)[:len(out)] for c in range(2)], axis=1)
    assert np.abs(out - whole).max() < 1e-5


def test_switching_presets_mid_song_keeps_going():
    f = StreamFilter(RATE, 2)
    f.set_preset(Settings().find("Rock"))
    f.process(_tone(100, 0.2))
    f.set_preset(Settings().find("Flat"))
    out = f.process(_tone(100, 0.2))
    assert out.shape == (int(RATE * 0.2), 2) and np.abs(out).max() <= 1.0
    f.reset()
    assert np.array_equal(f.process(_tone(100, 0.1)), _tone(100, 0.1))


def test_loud_boosts_are_clipped():
    f = StreamFilter(RATE, 2)
    f.set_preset(Preset("Max", (12,) * 10, 12))
    assert np.abs(f.process(_tone(1000, 0.2, level=0.9))).max() <= 1.0


def test_songs_keep_their_preset_through_rename_and_delete(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    conn.execute("INSERT INTO tracks (persistent_id, name) VALUES ('00000000000000A1', 'One')")
    conn.execute("INSERT INTO tracks (persistent_id, name) VALUES ('00000000000000A2', 'Two')")
    conn.commit()
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks ORDER BY id")]
    library.update_track_fields(conn, ids[0], {"equalizer": " Rock "})
    library.update_track_fields(conn, ids[1], {"equalizer": ""})
    assert library.load_track(conn, ids[0])["equalizer"] == "Rock"
    assert library.load_track(conn, ids[1])["equalizer"] is None
    assert not library.load_track(conn, ids[0])["tags_dirty"]         # not a tag
    assert library.rename_equalizer_preset(conn, "rock", "Loud Rock") == [ids[0]]
    assert library.load_track(conn, ids[0])["equalizer"] == "Loud Rock"
    library.rename_equalizer_preset(conn, "Loud Rock", None)
    assert library.load_track(conn, ids[0])["equalizer"] is None


def test_settings_are_kept_in_the_library(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    assert not equalizer.has_saved(conn) and equalizer.load(conn).names() == Settings().names()
    equalizer.save(conn, Settings(on=True, preset="Deep"))
    equalizer.save(conn, Settings(on=True, preset="Latin"))          # replaces
    assert equalizer.has_saved(conn) and equalizer.load(conn).preset == "Latin"
