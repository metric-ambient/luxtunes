"""Edits saved in the library get written into the music file (only the changed fields)."""

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("mutagen")

from luxtunes.core import db, library, tag_sync  # noqa: E402
from luxtunes.core.tags import read_details  # noqa: E402
from luxtunes.importer.itunes_xml import import_library  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "small_library.xml"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"


def setup(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    import_library(conn, FIXTURE, tmp_path / "media")
    track_id = conn.execute("SELECT id FROM tracks WHERE persistent_id = 'AAAA000000000001'").fetchone()[0]
    song = tmp_path / "song.mp3"
    song.write_bytes((b"\xff\xfb\x90\x64" + b"\x00" * 413) * 30)
    library.set_location(conn, track_id, str(song))
    return conn, track_id, str(song)


def id3(path):
    from mutagen.id3 import ID3
    return ID3(path)


def test_edit_is_written_and_cleared(tmp_path):
    conn, track_id, path = setup(tmp_path)
    library.update_track_fields(conn, track_id, {"name": "Renamed", "track_number": "3"})
    assert library.pending_tag_fields(conn, track_id) == {"name", "track_number"}
    assert tag_sync.write_pending(conn, track_id) is None
    tags = id3(path)
    assert tags["TIT2"].text == ["Renamed"]
    assert tags["TRCK"].text == ["3/2"]          # track count (2) comes from the library
    assert "TALB" not in tags                     # untouched fields aren't written
    assert library.pending_tag_fields(conn, track_id) == set()
    assert library.load_track(conn, track_id)["size"] == Path(path).stat().st_size


def test_options_are_library_only(tmp_path):
    conn, track_id, path = setup(tmp_path)
    library.update_track_fields(conn, track_id, {"volume_adjustment": 150, "skip_when_shuffling": True,
                                                 "start_time": 1500, "media_kind": "music"})
    track = library.load_track(conn, track_id)
    assert track["volume_adjustment"] == 100 and track["skip_when_shuffling"] == 1
    assert track["start_time"] == 1500 and track["tags_dirty"] == 0   # nothing for the file


def test_lyrics_and_artwork(tmp_path):
    conn, track_id, path = setup(tmp_path)
    library.update_track_fields(conn, track_id, {"lyrics": "Hello\r\nWorld"})
    assert tag_sync.write_pending(conn, track_id, artworks=[JPEG]) is None
    details = read_details(path)
    assert details["lyrics"] == "Hello\nWorld" and details["artworks"] == [JPEG]


def test_missing_file_stays_pending_then_retries(tmp_path):
    conn, track_id, path = setup(tmp_path)
    library.update_track_fields(conn, track_id, {"album": "New Album"})
    moved = Path(path).rename(Path(path).with_name("elsewhere.mp3"))
    assert tag_sync.write_pending(conn, track_id) == "file not found"
    assert library.pending_tag_fields(conn, track_id) == {"album"}
    moved.rename(path)
    assert tag_sync.write_all_pending(conn) == (1, 0)
    assert id3(path)["TALB"].text == ["New Album"]


def test_upgrade_to_schema_5(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA)
    for column in ("enabled INTEGER NOT NULL DEFAULT 1", "tags_dirty INTEGER NOT NULL DEFAULT 0"):
        old.execute(f"ALTER TABLE tracks ADD COLUMN {column}")
    old.execute("PRAGMA user_version = 4")
    old.execute("INSERT INTO tracks (persistent_id, name, tags_dirty) VALUES ('X', 'Old', 1)")
    old.commit()
    old.close()
    conn = db.connect(path)
    track = dict(conn.execute("SELECT * FROM tracks").fetchone())
    assert track["bookmarkable"] == 0 and track["dirty_fields"] is None
    # Edited before field tracking existed: the list-editable fields are rewritten.
    assert "name" in library.pending_tag_fields(conn, track["id"])
