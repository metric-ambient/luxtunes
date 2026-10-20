"""CD import: disc IDs, MusicBrainz answers, encoder commands, and a whole
import with a pretend cdparanoia (no CD drive needed)."""

import os
import shutil
import stat
import sys
import threading

import pytest

pytest.importorskip("mutagen")

from luxtunes.core import cd, db, encoders, library, organize, tags  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def test_musicbrainz_disc_id():
    # The worked example from MusicBrainz's "Disc ID Calculation" page.
    toc = cd.Toc(1, 6, [150, 15363, 32314, 46592, 63414, 80489], 95462)
    assert toc.disc_id() == "49HHV7Eb8UKF3aQiNmu1GR8vKTY-"
    assert toc.toc_param() == "1 6 95462 150 15363 32314 46592 63414 80489"
    assert toc.sectors(0) == 15213 and toc.sectors(5) == 95462 - 80489


def test_toc_with_a_data_track_at_the_end():
    entries = [(1, 0, False), (2, 15000, False), (3, 40000, True), (cd.CDROM_LEADOUT, 90000, False)]
    toc = cd.toc_from_entries(entries)
    assert (toc.first, toc.last) == (1, 2)
    assert toc.offsets == [150, 15150]
    assert toc.leadout == 40000 - 11400 + 150          # the audio ends before the data session
    disc = cd.blank_disc("/dev/sr0", toc)
    assert [t.name for t in disc.tracks] == ["Track 01", "Track 02"]
    assert disc.tracks[0].total_time == 15000 * 1000 // 75
    with pytest.raises(cd.CdError):
        cd.toc_from_entries([(1, 0, True), (cd.CDROM_LEADOUT, 100, False)])


def musicbrainz_answer(disc_id):
    def credit(name, mbid="a1"):
        return [{"name": name, "joinphrase": "", "artist": {"id": mbid, "name": name}}]
    tracks = [{"position": i + 1, "title": title, "artist-credit": credit(artist),
               "recording": {"title": title, "genres": [{"name": "indie rock", "count": 2}]}}
              for i, (title, artist) in enumerate([("The City", "The 1975"), ("M.O.N.E.Y.", "The 1975")])]
    return {"releases": [
        {"id": "wrong", "title": "Other", "media": [{"position": 1, "discs": [], "tracks": tracks[:1]}]},
        {"id": "rel-1", "title": "The 1975", "date": "2013-09-02", "artist-credit": credit("The 1975"),
         "release-group": {"id": "rg-1", "genres": []},
         "genres": [{"name": "pop rock", "count": 1}, {"name": "indie pop", "count": 5}],
         "media": [{"position": 1, "discs": [{"id": disc_id}], "tracks": tracks},
                   {"position": 2, "discs": [], "tracks": tracks}]},
    ]}


def test_apply_musicbrainz_answer():
    toc = cd.Toc(1, 2, [150, 20000], 40000)
    disc = cd.blank_disc("/dev/sr0", toc)
    assert cd.apply_release(disc, musicbrainz_answer(toc.disc_id()))
    assert disc.album == "The 1975" and disc.artist == "The 1975" and disc.year == 2013
    assert (disc.disc_number, disc.disc_count) == (1, 2)
    assert disc.genre == "Indie Pop" and not disc.compilation and disc.album_artist == ""
    assert [t.name for t in disc.tracks] == ["The City", "M.O.N.E.Y."]
    assert disc.release_id == "rel-1" and disc.name == "The 1975"
    fields = disc.fields(disc.tracks[1])
    assert fields["track_number"] == 2 and fields["track_count"] == 2 and fields["artist"] == "The 1975"

    # Various Artists: a compilation, and the album artist is kept.
    answer = musicbrainz_answer(toc.disc_id())
    answer["releases"][1]["artist-credit"] = [{"name": "Various Artists",
                                               "artist": {"id": cd.VARIOUS_ARTISTS}}]
    disc = cd.blank_disc("/dev/sr0", toc)
    cd.apply_release(disc, answer)
    assert disc.compilation and disc.album_artist == "Various Artists"
    assert disc.tracks[0].artist == "The 1975"

    unknown = cd.blank_disc("/dev/sr0", toc)
    assert not cd.apply_release(unknown, {"releases": []}) and unknown.name == "Audio CD"


def test_insert_action_setting(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    assert cd.insert_action(conn) == "ask"
    cd.set_insert_action(conn, "import_eject")
    assert cd.insert_action(conn) == "import_eject"


def test_import_settings_round_trip_and_commands(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    s = encoders.load(conn)
    assert (s.encoder, s.setting, s.mp3_bit_rate) == (encoders.MP3, encoders.CUSTOM, 320)
    s.mp3_vbr, s.mp3_quality, s.mp3_sample_rate, s.mp3_channels = True, "High", 48000, "Mono"
    encoders.save(conn, s)
    again = encoders.load(conn)
    assert again == s
    cmd = encoders.ffmpeg_command("ffmpeg", ["-i", "in.wav"], "out.mp3", again)
    assert cmd[cmd.index("-q:a") + 1] == "2" and cmd[cmd.index("-ar") + 1] == "48000"
    assert cmd[cmd.index("-ac") + 1] == "1" and "highpass=f=10" in cmd
    assert "VBR, high quality" in encoders.details(again)

    s = encoders.ImportSettings(encoder=encoders.MP3, setting="Moderate Quality (160 kbps)")
    cmd = encoders.ffmpeg_command("ffmpeg", [], "x.mp3", s)
    assert cmd[cmd.index("-b:a") + 1] == "160k"
    assert encoders.details(s).startswith("80 kbps (mono)/160 kbps (stereo), joint stereo")
    s = encoders.ImportSettings(encoder=encoders.AAC, setting="Higher Quality (256 kbps)")
    assert encoders.extension(s) == ".m4a" and "256k" in encoders.ffmpeg_command("f", [], "x", s)
    assert encoders.setting_names(encoders.ALAC) == ["Automatic"]
    assert encoders.setting_names(encoders.WAV) == ["Automatic", "Custom..."]
    # Bad saved values fall back to something valid.
    renamed = encoders.ImportSettings.from_json('{"encoder": "AAC Encoder", "setting": "iTunes Plus"}')
    assert renamed.setting == "Higher Quality (256 kbps)"
    odd = encoders.ImportSettings.from_json('{"encoder": "AIFF Encoder", "setting": "Poor Quality (128 kbps)"}')
    assert odd.setting == "Automatic"


@needs_ffmpeg
@pytest.mark.parametrize("encoder", encoders.ENCODERS)
def test_every_encoder_makes_a_taggable_file(tmp_path, encoder):
    import subprocess
    settings = encoders.ImportSettings(encoder=encoder, setting=encoders.setting_names(encoder)[0])
    target = str(tmp_path / ("song" + encoders.extension(settings)))
    source = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=44100", "-ac", "2"]
    subprocess.run(encoders.ffmpeg_command("ffmpeg", source, target, settings), check=True)
    tags.write_tags(target, {"name": "Tone", "artist": "Test"})
    fields = tags.read_fields(target)
    assert fields["name"] == "Tone" and fields["total_time"] > 900


FAKE_CDPARANOIA = """#!{python}
import sys
args = sys.argv[1:]
track = int(args[-2])
if track == 3:
    sys.stderr.write("Unable to read track 3\\n")
    sys.exit(1)
import math, os, struct
sectors = int({sectors!r}[track - 1])
out = sys.stdout.buffer
frames = sectors * 588
# Some drives hand cdparanoia big-endian audio it doesn't notice; pretend to be one.
order = ">hh" if os.environ.get("FAKE_CD_BIG_ENDIAN") else "<hh"
silent = int(os.environ.get("FAKE_CD_SILENT_FRAMES", "0"))
for i in range(0, frames, 588):
    block = b"".join(struct.pack(order, s, s) for s in
                     (0 if i + j < silent else int(8000 * math.sin(2 * math.pi * 440 * (i + j) / 44100))
                      for j in range(588)))
    out.write(block)
"""


def fake_cdparanoia(tmp_path, monkeypatch, sectors):
    folder = tmp_path / "bin"
    folder.mkdir()
    script = folder / "cdparanoia"
    script.write_text(FAKE_CDPARANOIA.format(python=sys.executable, sectors=sectors))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")


@needs_ffmpeg
def test_import_a_cd(tmp_path, monkeypatch):
    sectors = [75 * 2, 75, 75]                         # 2 s, 1 s, and a track that can't be read
    fake_cdparanoia(tmp_path, monkeypatch, sectors)
    conn = db.connect(tmp_path / "library.db")
    root = tmp_path / "Media"
    organize.set_media_folder(conn, root)
    offsets = [150, 300, 375]
    toc = cd.Toc(1, 3, offsets, 450)
    disc = cd.blank_disc("/dev/sr0", toc)
    disc.album, disc.artist, disc.year, disc.genre = "The 1975", "The 1975", 2013, "Indie"
    disc.tracks[0].name, disc.tracks[1].name = "The City", "M.O.N.E.Y."
    disc.artwork = b"\xff\xd8\xff\xe0" + b"\x00" * 64            # a (tiny) JPEG
    settings = encoders.ImportSettings()                          # MP3, 320 kbps
    seen = []

    track_id = cd.import_track(conn, disc, disc.tracks[0], settings, progress=seen.append)
    song = library.load_track(conn, track_id)
    expected = root / "Music" / "The 1975" / "The 1975" / "01 The City.mp3"
    assert song["location"] == str(expected) and expected.exists()
    assert song["name"] == "The City" and song["album"] == "The 1975" and song["year"] == 2013
    assert song["track_number"] == 1 and song["track_count"] == 3 and song["media_kind"] == "music"
    assert 1900 < song["total_time"] < 2300 and song["bit_rate"] == 320
    assert seen and seen[-1].fraction == 1.0 and seen[-1].speed > 0
    assert tags.read_details(str(expected))["artworks"]
    assert [p.name for p in expected.parent.iterdir()] == ["01 The City.mp3"]   # no leftovers

    # A track the drive can't read: an error, and nothing left behind.
    with pytest.raises(cd.CdError, match="Unable to read track 3"):
        cd.import_track(conn, disc, disc.tracks[2], settings)
    assert sorted(p.name for p in expected.parent.iterdir()) == ["01 The City.mp3"]

    # Stopping removes the half-made file and the folders made for it.
    disc.album = "Stopped Album"
    stop = threading.Event()
    stop.set()
    with pytest.raises(cd.ImportStopped):
        cd.import_track(conn, disc, disc.tracks[1], settings, stop=stop)
    assert not (root / "Music" / "The 1975" / "Stopped Album").exists()
    assert library.count_tracks(conn, "music") == 1


def test_guess_byte_order():
    import array
    import math
    tone = array.array("h", [int(8000 * math.sin(2 * math.pi * 440 * i / 44100))
                             for i in range(4410) for _ in (0, 1)])
    native = tone.tobytes()
    tone.byteswap()
    swapped = tone.tobytes()
    other = "big" if sys.byteorder == "little" else "little"
    assert cd.guess_byte_order(native) == sys.byteorder
    assert cd.guess_byte_order(swapped) == other
    assert cd.guess_byte_order(bytes(4000)) is None            # silence: can't tell yet


@needs_ffmpeg
@pytest.mark.parametrize("big_endian", [False, True])
def test_ripped_audio_is_never_static(tmp_path, monkeypatch, big_endian):
    """Whichever byte order the drive delivers (after some silence), the
    file holds the clean tone, not noise."""
    import array
    import subprocess
    fake_cdparanoia(tmp_path, monkeypatch, [75 * 4])
    if big_endian:
        monkeypatch.setenv("FAKE_CD_BIG_ENDIAN", "1")
    monkeypatch.setenv("FAKE_CD_SILENT_FRAMES", str(44100))          # 1 s of silence first
    track = cd.CdTrack(1, 75 * 4, "Tone")
    target = str(tmp_path / "tone.mp3")
    cd.rip_and_encode("/dev/sr0", track, encoders.ImportSettings(), target)   # MP3 320 kbps
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", target, "-f", "s16le", "-ac", "1", "-"],
                             capture_output=True, check=True).stdout
    samples = array.array("h", decoded[:len(decoded) - len(decoded) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    music = samples[44100 + 4410: 44100 * 3]                        # past the silence
    crossings = sum(1 for a, b in zip(music, music[1:]) if (a < 0) != (b < 0))
    seconds = len(music) / 44100
    assert 800 < crossings / seconds < 960                          # a 440 Hz tone: ~880 a second
