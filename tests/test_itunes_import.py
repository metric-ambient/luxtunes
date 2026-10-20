"""Tests for the iTunes XML importer, using a small FAKE library.

tests/fixtures/small_library.xml has 4 tracks (one of them a movie) and
5 playlists, written in exactly the iTunes library XML format.
"""

from pathlib import Path

from luxtunes.core import db
from luxtunes.importer.itunes_xml import PathMapper, import_library

FIXTURE = Path(__file__).parent / "fixtures" / "small_library.xml"


def make_media_folder(tmp_path: Path) -> Path:
    """Create a fake 'LuxTunes Media' folder containing two of the fixture's files.

    The album folder is deliberately capitalised differently from the XML
    ("first album" vs "First Album") to test case-insensitive matching.
    """
    media = tmp_path / "LuxTunes Media"
    album = media / "Music" / "Fake Band" / "first album"
    album.mkdir(parents=True)
    (album / "01 Opening Song.mp3").write_bytes(b"")
    (album / "02 Second Song.mp3").write_bytes(b"")
    return media


def run_import(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    media = make_media_folder(tmp_path)
    result = import_library(conn, FIXTURE, media)
    return conn, media, result


def test_counts(tmp_path):
    conn, _, result = run_import(tmp_path)
    assert result.tracks == 4
    assert result.playlists == 5
    stats = db.stats(conn)
    assert stats["tracks"] == 4
    assert stats["music"] == 3
    assert stats["playlists"] == 4        # excludes the hidden master "Library"
    assert stats["smart_playlists"] == 1
    assert stats["rated"] == 1            # track 102's rating is inherited, so not counted


def test_track_metadata(tmp_path):
    conn, _, _ = run_import(tmp_path)
    t = conn.execute("SELECT * FROM tracks WHERE persistent_id = 'AAAA000000000001'").fetchone()
    assert t["name"] == "Opening Song"
    assert t["album_artist"] == "Fake Band"
    assert t["rating"] == 100
    assert t["play_count"] == 12
    assert t["play_date"] == "2024-11-05T05:19:44Z"
    assert t["skip_count"] == 2
    assert t["date_added"] == "2019-10-07T03:12:11Z"
    assert t["total_time"] == 180000
    assert t["comments"] == "A comment"
    assert t["itunes_track_id"] == 101


def test_flags_and_extras(tmp_path):
    conn, _, result = run_import(tmp_path)
    row = lambda pid: conn.execute("SELECT * FROM tracks WHERE persistent_id = ?", (pid,)).fetchone()
    assert row("AAAA000000000002")["rating_computed"] == 1
    assert row("AAAA000000000003")["compilation"] == 1
    assert row("AAAA000000000004")["media_kind"] == "movie"
    # Unknown iTunes keys are kept, not thrown away.
    assert "Some Future Key" in row("AAAA000000000003")["extra_json"]
    assert result.unknown_keys == {"Some Future Key"}


def test_locations_and_missing_files(tmp_path):
    conn, media, result = run_import(tmp_path)
    loc = conn.execute(
        "SELECT location FROM tracks WHERE persistent_id = 'AAAA000000000001'"
    ).fetchone()[0]
    # Found despite "First Album" (XML) vs "first album" (disk).
    assert loc == str(media / "Music" / "Fake Band" / "first album" / "01 Opening Song.mp3")
    # The compilation track and the movie aren't on disk.
    assert len(result.missing_files) == 2


def test_playlists_keep_order_and_duplicates(tmp_path):
    conn, _, _ = run_import(tmp_path)
    names = conn.execute(
        """SELECT t.name FROM playlist_items pi
           JOIN playlists p ON p.id = pi.playlist_id
           JOIN tracks t ON t.id = pi.track_id
           WHERE p.name = 'Road Trip' ORDER BY pi.position"""
    ).fetchall()
    assert [n[0] for n in names] == ["Various Track", "Opening Song", "Various Track"]


def test_reimport_updates_instead_of_duplicating(tmp_path):
    conn, media, _ = run_import(tmp_path)
    conn.execute("UPDATE tracks SET play_count = 0")
    import_library(conn, FIXTURE, media)
    assert db.stats(conn)["tracks"] == 4
    assert conn.execute(
        "SELECT play_count FROM tracks WHERE persistent_id = 'AAAA000000000001'"
    ).fetchone()[0] == 12
    assert conn.execute("SELECT COUNT(*) FROM playlist_items").fetchone()[0] == 11  # 4 + 3 + 1 + 3 + 0


def test_path_mapper_handles_windows_urls(tmp_path):
    mapper = PathMapper("file://localhost/D:/Music/iTunes/iTunes%20Media/", tmp_path)
    mapped = mapper.map("file://localhost/d:/Music/iTunes/iTunes%20Media/Music/A%20B/C.mp3")
    assert mapped == str(tmp_path / "Music" / "A B" / "C.mp3")


def test_media_kinds_from_the_xml():
    from luxtunes.importer.itunes_xml import _media_kind
    assert _media_kind({"Kind": "MPEG audio file"}) == "music"
    assert _media_kind({"Podcast": True, "Kind": "MPEG audio file"}) == "podcast"
    assert _media_kind({"Kind": "Protected AAC audio file",
                        "Location": "file://localhost/C:/Media/Book.m4b"}) == "audiobook"
    assert _media_kind({"Kind": "Audible file"}) == "audiobook"
    assert _media_kind({"TV Show": True, "Has Video": True}) == "tvshow"
    assert _media_kind({"Movie": True}) == "movie"
