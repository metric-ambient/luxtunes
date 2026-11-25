"""An iPod that also runs Rockbox (or RepriseOS): finding it, playlists for
Rockbox, covers and tags it can read, and plays from its scrobbler log."""

import shutil
import subprocess

import pytest

from luxtunes.core import db
from luxtunes.core.ipod import rockbox

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="FFmpeg isn't installed")


def test_other_firmware(tmp_path):
    assert rockbox.other_firmware(str(tmp_path)) == []
    (tmp_path / ".rockbox").mkdir()
    assert rockbox.other_firmware(str(tmp_path)) == ["Rockbox"]
    (tmp_path / "osos-cfw.bin").write_bytes(b"x")
    assert rockbox.other_firmware(str(tmp_path)) == ["Rockbox", "RepriseOS"]
    assert rockbox.has_rockbox(str(tmp_path))


def test_playlists_for_rockbox(tmp_path):
    music = tmp_path / "iPod_Control" / "Music" / "F01"
    music.mkdir(parents=True)
    (music / "ABCD.mp3").write_bytes(b"x")
    (tmp_path / "Playlists").mkdir()
    mine = tmp_path / "Playlists" / "Road Trip.m3u8"
    mine.write_text("#EXTM3U\n/Music/song.mp3\n")              # your own: never touched
    song = {"path": str(music / "ABCD.mp3"), "seconds": 201.6, "artist": "Slow Orbit",
            "name": "Harbor Lights"}
    count = rockbox.write_playlists(str(tmp_path), [("Road Trip", [song]), ("A/B: Mix?", [song]),
                                                    ("a/b: mix?", [])])
    assert count == 3
    assert mine.read_text() == "#EXTM3U\n/Music/song.mp3\n"
    written = (tmp_path / "Playlists" / "Road Trip (2).m3u8").read_text().splitlines()
    assert written[0] == "#EXTM3U" and written[1].startswith("#LuxTunes")
    assert written[2:] == ["#EXTINF:201,Slow Orbit - Harbor Lights", "/iPod_Control/Music/F01/ABCD.mp3"]
    assert (tmp_path / "Playlists" / "A_B_ Mix_.m3u8").exists()
    assert (tmp_path / "Playlists" / "a_b_ mix_ (2).m3u8").exists()      # FAT ignores case
    # Next sync without those: LuxTunes' old ones go, yours stays.
    assert rockbox.write_playlists(str(tmp_path), [("Road Trip", [song])]) == 1
    names = sorted(p.name for p in (tmp_path / "Playlists").iterdir())
    assert names == ["Road Trip (2).m3u8", "Road Trip.m3u8"]


def _picture(tmp_path, name, *options):
    path = tmp_path / name
    subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=teal:s=1200x900", "-frames:v", "1", *options, str(path)], check=True)
    return path.read_bytes()


@needs_ffmpeg
def test_covers_rockbox_can_show(tmp_path):
    png = _picture(tmp_path, "a.png")
    big = _picture(tmp_path, "b.jpg", "-pix_fmt", "yuvj420p")
    assert rockbox.jpeg_shape(png) is None and not rockbox.rockbox_can_show(png)
    assert rockbox.jpeg_shape(big) == (False, 1200, 900)
    assert not rockbox.rockbox_can_show(big)                       # too big
    small = rockbox.baseline_jpeg(png, FFMPEG)
    assert rockbox.jpeg_shape(small) == (False, 500, 375) and rockbox.rockbox_can_show(small)
    progressive = bytearray(small)
    at = progressive.index(b"\xff\xc0")
    progressive[at + 1] = 0xC2
    assert rockbox.jpeg_shape(bytes(progressive))[0] and not rockbox.rockbox_can_show(bytes(progressive))


@needs_ffmpeg
def test_fix_file_gives_tags_and_cover(tmp_path):
    pytest.importorskip("mutagen")
    from luxtunes.core import artwork, tags
    copy = tmp_path / "copy.mp3"           # like a song converted for the iPod: no tags
    subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=d=1", "-map_metadata", "-1", str(copy)], check=True)
    original = tmp_path / "original.mp3"
    shutil.copy(copy, original)
    tags.write_tags(str(original), {"name": "Harbor Lights"},
                    artworks=[_picture(tmp_path, "c.png")])
    song = {"name": "Harbor Lights", "artist": "Slow Orbit", "album": "Lantern Valley",
            "track_number": 3}
    assert rockbox.fix_file(str(copy), song, str(original), FFMPEG)
    details = tags.read_fields(str(copy))
    assert details["name"] == "Harbor Lights" and details["artist"] == "Slow Orbit"
    assert rockbox.rockbox_can_show(artwork.read_embedded_artwork(str(copy)))
    assert artwork.read_embedded_artwork(str(original))[:4] == b"\x89PNG"   # the library's: untouched
    assert not rockbox.fix_file(str(copy), song, str(original), FFMPEG)     # nothing left to do
    key = rockbox.checked_key(str(copy), song)
    assert not rockbox.details_changed(key, song)
    assert rockbox.details_changed(key, {**song, "name": "Harbor Lights (Live)"})


LOG = """#AUDIOSCROBBLER/1.1
#TZ/UTC
#CLIENT/Rockbox ipod6g $Revision$
Slow Orbit\tLantern Valley\tHarbor Lights\t3\t200\tL\t1767225600\t
Slow Orbit\tLantern Valley\tHarbor Lights\t3\t200\tS\t1767229200\t
Paper Coast\tSomewhere\tUnknown Song\t1\t180\tL\t1767232800\t
"""


def test_plays_from_the_scrobbler_log(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, play_count) "
                 "VALUES ('00000000000000A1', 'Harbor Lights', 'Slow Orbit', 'Lantern Valley', 4)")
    conn.commit()
    (tmp_path / ".scrobbler.log").write_text(LOG)
    state = {}
    plays, seen = rockbox.read_scrobbler_log(str(tmp_path), state)
    assert len(plays) == 3 and plays[0]["when"] == 1767225600 and plays[1]["skipped"]
    [item] = rockbox.plays_to_stats(conn, plays)          # the unknown song is left out
    assert (item.recent_plays, item.recent_skips, item.last_played) == (1, 1, 1767225600)
    # Read again: nothing new. A new play: only that one.
    state["scrobbler"] = seen
    assert rockbox.read_scrobbler_log(str(tmp_path), state)[0] == []
    with open(tmp_path / ".scrobbler.log", "a") as log:
        log.write("Slow Orbit\tLantern Valley\tHarbor Lights\t3\t200\tL\t1767240000\t\n")
    plays, seen = rockbox.read_scrobbler_log(str(tmp_path), state)
    assert [p["when"] for p in plays] == [1767240000]
    # Another tool emptied the log and Rockbox started a new one: all of it is new.
    (tmp_path / ".scrobbler.log").write_text(LOG.split("Slow")[0] + LOG.splitlines()[3] + "\n")
    state["scrobbler"] = seen
    assert len(rockbox.read_scrobbler_log(str(tmp_path), state)[0]) == 1


def test_state_on_the_ipod(tmp_path):
    (tmp_path / "iPod_Control" / "iTunes").mkdir(parents=True)
    assert rockbox.load_state(str(tmp_path)) == {}
    rockbox.save_state(str(tmp_path), {"checked": {"A1": "1:2|x"}})
    assert rockbox.load_state(str(tmp_path)) == {"checked": {"A1": "1:2|x"}}
