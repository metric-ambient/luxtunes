"""Tests for play/skip/rating bookkeeping, schema upgrades, and the song
list's formatting, sorting and search rules."""

import sqlite3
from pathlib import Path

from luxtunes.core import db, library
from luxtunes.importer.itunes_xml import import_library
from luxtunes.ui import formatting

FIXTURE = Path(__file__).parent / "fixtures" / "small_library.xml"


def imported(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    import_library(conn, FIXTURE, tmp_path / "media")
    return conn


def track_id(conn, persistent_id):
    return conn.execute("SELECT id FROM tracks WHERE persistent_id = ?", (persistent_id,)).fetchone()[0]


# ---- Library bookkeeping ---------------------------------------------------

def test_record_play_and_skip(tmp_path):
    conn = imported(tmp_path)
    tid = track_id(conn, "AAAA000000000001")
    result = library.record_play(conn, tid)
    assert result["play_count"] == 13
    assert result["play_date"].endswith("Z")
    assert library.record_skip(conn, tid)["skip_count"] == 3


def test_set_rating_clears_computed_flag(tmp_path):
    conn = imported(tmp_path)
    tid = track_id(conn, "AAAA000000000002")   # has an inherited (computed) rating
    library.set_rating(conn, tid, 60)
    row = conn.execute("SELECT rating, rating_computed FROM tracks WHERE id = ?", (tid,)).fetchone()
    assert tuple(row) == (60, 0)


def test_load_tracks_and_playlists(tmp_path):
    conn = imported(tmp_path)
    assert len(library.load_tracks(conn, media_kind="music")) == 3
    playlists = library.load_playlists(conn)
    assert "Library" not in [p["name"] for p in playlists]
    road_trip = next(p for p in playlists if p["name"] == "Road Trip")
    names = [t["name"] for t in library.load_tracks(conn, playlist_id=road_trip["id"])]
    assert names == ["Various Track", "Opening Song", "Various Track"]


def test_enabled_defaults_to_checked(tmp_path):
    conn = imported(tmp_path)
    assert conn.execute("SELECT MIN(enabled) FROM tracks").fetchone()[0] == 1


def test_upgrade_from_schema_1(tmp_path):
    """A library made by the previous version gains the new column automatically."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA)
    old.execute("PRAGMA user_version = 1")
    old.execute("INSERT INTO tracks (persistent_id, name) VALUES ('X', 'Old Song')")
    old.commit()
    old.close()

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT name, enabled FROM tracks").fetchone()[:] == ("Old Song", 1)


# ---- Formatting ------------------------------------------------------------

def test_durations():
    assert formatting.format_duration(271000) == "4:31"
    assert formatting.format_duration(52000) == "0:52"
    assert formatting.format_duration(3723000) == "1:02:03"
    assert formatting.format_duration(None) == ""


def test_dates_are_shown_in_local_time(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    import time
    time.tzset()
    assert formatting.format_date("2019-10-07T03:12:11Z") == "10/6/2019 11:12 PM"
    assert formatting.format_date(None) == ""


def test_summary():
    assert formatting.library_summary(5000, 14.2 * 86400 * 1000, 35_000_000_000) == \
        "5,000 songs, 14.2 days, 35.00 GB"
    assert formatting.library_summary(1, 180000, 5_000_000) == "1 song, 3.0 minutes, 5.0 MB"


# ---- Sorting ---------------------------------------------------------------

def song(name, artist, album, disc=1, track=1, **extra):
    return {"name": name, "artist": artist, "album": album,
            "disc_number": disc, "track_number": track, **extra}


def test_sort_ignores_leading_the_and_case():
    assert formatting.sort_text("The Devil and God") == "devil and god"
    assert formatting.sort_text("A Day to Remember") == "day to remember"
    assert formatting.sort_text("The") == "the"


def test_artist_sort_keeps_albums_in_track_order():
    songs = [
        song("Track 2", "Underoath", "They're Only Chasing Safety", track=2),
        song("Jesus", "Brand New", "The Devil and God Are Raging Inside Me", track=3),
        song("Track 1", "Underoath", "They're Only Chasing Safety", track=1),
        song("Sowing Season", "Brand New", "The Devil and God Are Raging Inside Me", track=1),
        song("Disc 2 opener", "Brand New", "The Devil and God Are Raging Inside Me", disc=2, track=1),
    ]
    ordered = sorted(songs, key=formatting.sort_key("artist"))
    assert [s["name"] for s in ordered] == [
        "Sowing Season", "Jesus", "Disc 2 opener", "Track 1", "Track 2",
    ]


def test_album_artist_groups_compilation_tracks():
    songs = [
        song("B", "Zed", "Mix", track=2, album_artist="Various"),
        song("A", "Alpha", "Mix", track=1, album_artist="Various"),
        song("C", "Middle", "Solo"),
    ]
    ordered = sorted(songs, key=formatting.sort_key("artist"))
    assert [s["name"] for s in ordered] == ["C", "A", "B"]


def test_numeric_columns_handle_missing_values():
    songs = [song("x", "a", "b", rating=None), song("y", "a", "b", rating=100)]
    ordered = sorted(songs, key=formatting.sort_key("rating"))
    assert [s["name"] for s in ordered] == ["x", "y"]


# ---- Search ----------------------------------------------------------------

def test_search_needs_every_word():
    blob = formatting.search_text(song("Sowing Season", "Brand New", "The Devil and God", genre="Rock"))
    assert formatting.matches(blob, ["brand", "sowing"])
    assert formatting.matches(blob, ["rock"])
    assert not formatting.matches(blob, ["brand", "underoath"])
    assert formatting.matches(blob, [])


def test_selection_summary():
    assert formatting.library_summary(12, 54.8 * 60 * 1000, 127_700_000, of_total=35) == \
        "12 of 35 songs, 54.8 minutes, 127.7 MB"


def test_start_stop_times():
    assert formatting.format_time_ms(68937) == "1:08.937"
    assert formatting.format_time_ms(0) == "0:00.000"
    assert formatting.parse_time_ms("1:08.937") == 68937
    assert formatting.parse_time_ms("68.5") == 68500
    assert formatting.parse_time_ms("1:00:01") == 3601000
    assert formatting.parse_time_ms("") is None
    for bad in ("abc", "1:2:3:4", "-5"):
        try:
            formatting.parse_time_ms(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_changing_a_field_clears_its_old_sort_field(tmp_path):
    from luxtunes.core import db, library
    conn = db.connect(tmp_path / "library.db")
    conn.execute("INSERT INTO tracks (persistent_id, name, artist, sort_artist, album, sort_album) "
                 "VALUES ('P', 'Pressure', 'The 1975', '1975', 'The 1975', '1975')")
    conn.commit()
    library.update_track_fields(conn, 1, {"artist": "The 1980"})
    track = library.load_track(conn, 1)
    assert track["sort_artist"] is None and track["sort_album"] == "1975"
    assert library.pending_tag_fields(conn, 1) == {"artist", "sort_artist"}
    # Setting both in one edit keeps your sort value; an unchanged value clears nothing.
    library.update_track_fields(conn, 1, {"album": "X", "sort_album": "Custom"})
    library.update_track_fields(conn, 1, {"album": "X"})
    assert library.load_track(conn, 1)["sort_album"] == "Custom"
