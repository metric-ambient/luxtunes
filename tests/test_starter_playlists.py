"""The example smart playlists a new library starts with."""

import time

import pytest

pytest.importorskip("mutagen")

from luxtunes.core import db, library, smart, starter_playlists  # noqa: E402

NAMES = ["90’s Music", "My Top Rated", "Recently Added", "Recently Played",
         "Top 25 Most Played"]


def names(conn):
    return sorted(p["name"] for p in library.load_playlists(conn) if p["is_smart"])


def song(conn, name, **fields):
    row = {"persistent_id": f"{abs(hash(name)):016X}"[:16], "name": name, "media_kind": "music",
           **fields}
    with conn:
        conn.execute(f"INSERT INTO tracks ({', '.join(row)}) VALUES "
                     f"({', '.join('?' for _ in row)})", list(row.values()))


def test_a_new_library_gets_them_once(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    assert len(starter_playlists.add_if_new(conn)) == 5
    assert names(conn) == NAMES
    # Deleted by you: they don't come back.
    for p in library.load_playlists(conn):
        library.delete_playlist(conn, p["id"])
    assert starter_playlists.add_if_new(conn) == []


def test_an_existing_library_doesnt_get_them(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    song(conn, "Something")
    assert starter_playlists.add_if_new(conn) == []
    assert names(conn) == []


def test_they_pick_the_right_songs(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    ids = starter_playlists.add_if_new(conn)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    song(conn, "Nineties", year=1994, rating=100, play_count=3, date_added=now, play_date=now)
    song(conn, "Old", year=1975, rating=40, play_count=0, date_added="2001-01-01T00:00:00Z")
    song(conn, "Film", year=1995, rating=100, play_count=9, media_kind="movie", date_added=now)
    by_name = {}
    for p in library.load_playlists(conn):
        if p["id"] in ids:
            smart.refresh(conn, p["id"], force=True)
            tracks = library.load_tracks(conn, playlist_id=p["id"])
            by_name[p["name"]] = sorted(t["name"] for t in tracks)
    assert len(by_name) == 5 and all(v == ["Nineties"] for v in by_name.values()), by_name
    added = smart.load(conn, ids[2])
    assert (added.rules.rules[0].amount, added.rules.rules[0].units) == (2, smart.MONTH)
    top = smart.load(conn, ids[-1])
    assert top.info.limit and top.info.limit_value == 25


def test_an_itunes_import_replaces_them(tmp_path):
    from pathlib import Path

    from luxtunes.importer.itunes_xml import import_library

    conn = db.connect(tmp_path / "library.db")
    starter_playlists.add_if_new(conn)
    fixture = Path(__file__).parent / "fixtures" / "small_library.xml"
    import_library(conn, fixture, tmp_path / "media")
    smart_names = [n.replace("’", "'") for n in names(conn)]
    for name in set(smart_names):
        assert smart_names.count(name) == 1, name           # no doubles
