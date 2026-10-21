"""Tests for the right-click menu's library operations and the schema-3 cleanup."""

import plistlib
import sqlite3
from pathlib import Path

from luxtunes.core import db, library
from luxtunes.importer.itunes_xml import import_library

FIXTURE = Path(__file__).parent / "fixtures" / "small_library.xml"


def setup(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    import_library(conn, FIXTURE, tmp_path / "media")
    return conn


def pid(conn, persistent_id):
    return conn.execute("SELECT id FROM tracks WHERE persistent_id = ?", (persistent_id,)).fetchone()[0]


def playlist(conn, name):
    return next(p for p in library.load_playlists(conn) if p["name"] == name)


def names_in(conn, name):
    return [t["name"] for t in library.load_tracks(conn, playlist_id=playlist(conn, name)["id"])]


def test_add_to_playlist_appends(tmp_path):
    conn = setup(tmp_path)
    library.add_to_playlist(conn, playlist(conn, "Road Trip")["id"], [pid(conn, "AAAA000000000002")])
    assert names_in(conn, "Road Trip") == ["Various Track", "Opening Song", "Various Track", "Second Song"]


def test_remove_from_playlist_closes_gaps(tmp_path):
    conn = setup(tmp_path)
    road_trip = playlist(conn, "Road Trip")["id"]
    library.remove_from_playlist(conn, road_trip, [0])   # first "Various Track" only
    tracks = library.load_tracks(conn, playlist_id=road_trip)
    assert [t["name"] for t in tracks] == ["Opening Song", "Various Track"]
    assert [t["playlist_position"] for t in tracks] == [0, 1]


def test_delete_tracks_removes_from_playlists(tmp_path):
    conn = setup(tmp_path)
    library.delete_tracks(conn, [pid(conn, "AAAA000000000003")])
    assert names_in(conn, "Road Trip") == ["Opening Song"]
    assert db.stats(conn)["tracks"] == 3
    assert library.record_play(conn, 99999) == {}       # deleted mid-play: no crash


def test_reset_plays(tmp_path):
    conn = setup(tmp_path)
    tid = pid(conn, "AAAA000000000001")
    library.reset_plays(conn, [tid])
    row = conn.execute(
        "SELECT play_count, play_date, skip_count, skip_date FROM tracks WHERE id = ?", (tid,)
    ).fetchone()
    assert tuple(row) == (0, None, 0, None)


def test_playlists_containing(tmp_path):
    conn = setup(tmp_path)
    names = [p["name"] for p in library.playlists_containing(conn, pid(conn, "AAAA000000000001"))]
    # Built-in "Library" and "Music" lists are left out; smart "My Top Rated" (kind 201) is kept.
    assert names == ["My Top Rated", "Road Trip"]


def test_user_playlists(tmp_path):
    conn = setup(tmp_path)
    assert [p["name"] for p in library.load_playlists(conn) if library.is_user_playlist(p)] == \
        ["Empty Playlist", "Road Trip"]   # A-Z, as in the sidebar


def test_import_skips_purchased_and_itunes_dj(tmp_path):
    lib = plistlib.loads(FIXTURE.read_bytes())
    lib["Playlists"] += [
        {"Name": "Purchased", "Playlist ID": 90, "Playlist Persistent ID": "DDDD000000000019",
         "Distinguished Kind": 19, "All Items": True},
        {"Name": "iTunes DJ", "Playlist ID": 91, "Playlist Persistent ID": "DDDD000000000022",
         "Distinguished Kind": 22, "All Items": True, "Playlist Items": [{"Track ID": 101}]},
    ]
    xml = tmp_path / "lib.xml"
    xml.write_bytes(plistlib.dumps(lib))
    conn = db.connect(tmp_path / "library.db")
    import_library(conn, xml, tmp_path / "media")
    names = [p["name"] for p in library.load_playlists(conn)]
    assert "Purchased" not in names and "iTunes DJ" not in names


def test_upgrade_to_schema_3_drops_purchased_and_dj(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA)
    old.execute("ALTER TABLE tracks ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
    old.execute("PRAGMA user_version = 2")
    old.executemany(
        "INSERT INTO playlists (persistent_id, name, distinguished_kind) VALUES (?, ?, ?)",
        [("A", "Purchased", 19), ("B", "iTunes DJ", 22), ("C", "Mine", None)])
    old.commit()
    old.close()
    conn = db.connect(path)
    assert [r[0] for r in conn.execute("SELECT name FROM playlists")] == ["Mine"]


def test_playlist_track_ids_and_load_by_ids(tmp_path):
    conn = setup(tmp_path)
    one, three = pid(conn, "AAAA000000000001"), pid(conn, "AAAA000000000003")
    assert library.playlist_track_ids(conn, playlist(conn, "Road Trip")["id"]) == {one, three}
    assert [t["id"] for t in library.load_tracks_by_ids(conn, [three, 424242, one])] == [three, one]


def test_create_and_rename_playlist(tmp_path):
    conn = setup(tmp_path)
    first = library.create_playlist(conn)
    second = library.create_playlist(conn)
    names = {p["id"]: p["name"] for p in library.load_playlists(conn)}
    assert names[first] == "untitled playlist"
    assert names[second] == "untitled playlist 2"
    library.rename_playlist(conn, first, "  Aaa Road Mix ")
    library.rename_playlist(conn, second, "   ")          # ignored
    playlists = library.load_playlists(conn)
    names = [p["name"] for p in playlists]
    assert names[0] == "My Top Rated"                     # smart playlists first
    assert names.index("Aaa Road Mix") < names.index("Road Trip")  # then A-Z
    assert "untitled playlist 2" in names
    assert library.is_user_playlist(next(p for p in playlists if p["id"] == first))


def test_edit_song_fields(tmp_path):
    import pytest
    conn = setup(tmp_path)
    tid = pid(conn, "AAAA000000000001")
    saved = library.update_track_fields(conn, tid, {"name": "  New Name ", "year": "1999"})
    assert saved["name"] == "New Name" and saved["year"] == 1999
    row = conn.execute("SELECT name, year, tags_dirty FROM tracks WHERE id = ?", (tid,)).fetchone()
    assert tuple(row) == ("New Name", 1999, 1)
    library.update_track_fields(conn, tid, {"year": ""})          # clearing a field
    assert conn.execute("SELECT year FROM tracks WHERE id = ?", (tid,)).fetchone()[0] is None
    with pytest.raises(ValueError):
        library.update_track_fields(conn, tid, {"year": "nineteen"})
    with pytest.raises(KeyError):
        library.update_track_fields(conn, tid, {"play_count": "5"})


def test_delete_and_duplicate_playlist(tmp_path):
    conn = setup(tmp_path)
    road_trip = playlist(conn, "Road Trip")["id"]
    copy_id = library.duplicate_playlist(conn, road_trip)
    assert names_in(conn, "Road Trip copy") == names_in(conn, "Road Trip")
    assert library.is_user_playlist(playlist(conn, "Road Trip copy"))
    library.delete_playlist(conn, road_trip)
    names = [p["name"] for p in library.load_playlists(conn)]
    assert "Road Trip" not in names and "Road Trip copy" in names
    assert db.stats(conn)["tracks"] == 4                       # songs are untouched
    assert library.duplicate_playlist(conn, copy_id)            # "Road Trip copy copy" is fine


def test_upgrade_to_schema_7_fills_tv_fields(tmp_path):
    import json
    conn = db.connect(tmp_path / "new.db")
    conn.execute("INSERT INTO tracks (persistent_id, name, media_kind, extra_json) VALUES (?, ?, ?, ?)",
                 ("T1", "Pilot", "movie", json.dumps({"TV Show": True, "Series": "The Office",
                                                      "Season": 2, "Episode Order": 3,
                                                      "Episode": "S2E3"})))
    for column in ("show", "season", "episode_number", "episode_id", "equalizer"):   # as in v6
        conn.execute(f"ALTER TABLE tracks DROP COLUMN {column}")
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()
    conn = db.connect(tmp_path / "new.db")
    row = conn.execute("SELECT media_kind, show, season, episode_number, episode_id FROM tracks").fetchone()
    assert tuple(row) == ("tvshow", "The Office", 2, 3, "S2E3")
