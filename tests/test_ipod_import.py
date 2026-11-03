"""Adding an iPod's songs (synced with another computer) to the library."""

import pytest

pytest.importorskip("mutagen")

from luxtunes.core import add_files, db, library, organize, tags  # noqa: E402
from luxtunes.core.ipod import import_songs  # noqa: E402

FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


def mp3(path, **fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FRAME * 40)
    if fields:
        tags.write_tags(str(path), fields)
    return str(path)


def song(path, dbid, **fields):
    return import_songs.IPodSong(str(path), dbid, fields, {
        "media_kind": "music", "play_count": 12, "play_date": "2024-05-01T10:00:00Z",
        "skip_count": 2, "rating": 80, "date_added": "2019-03-04T05:06:07Z"})


def test_only_songs_not_in_the_library_are_imported_with_their_details(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    organize.set_media_folder(conn, tmp_path / "Media")
    have = add_files.add_paths(conn, [mp3(tmp_path / "mine" / "a.mp3", name="Mine", artist="A",
                                          album="B")]).added[0]
    pid = library.load_track(conn, have)["persistent_id"]
    music = tmp_path / "IPOD" / "iPod_Control" / "Music" / "F01"
    on_ipod = [
        song(mp3(music / "AAAA.mp3", name="Mine", artist="A", album="B"), int(pid, 16)),  # same song
        song(mp3(music / "BBBB.m4a"), 7, name="Mine", artist="A", album="B"),    # a copy of it
        song(mp3(music / "CCCC.mp3", name="New Song", artist="C"), 0xABCDEF, name="New Song",
             artist="C", album="From iPod"),
        song(music / "DDDD.m4p", 9, name="Store Song"),                         # protected
    ]
    (music / "DDDD.m4p").write_bytes(b"x")
    new = import_songs.new_songs(conn, on_ipod)
    assert [s.dbid for s in new] == [0xABCDEF]
    assert import_songs.protected_count(on_ipod) == 1

    result = import_songs.import_songs(conn, new)
    assert len(result.added) == 1 and not result.failed
    track = library.load_track(conn, result.added[0])
    assert track["name"] == "New Song" and track["album"] == "From iPod"   # filled in by the iPod
    assert track["persistent_id"] == "0000000000ABCDEF"    # the next sync knows it's the same song
    assert (track["play_count"], track["skip_count"], track["rating"]) == (12, 2, 80)
    assert track["date_added"] == "2019-03-04T05:06:07Z"
    assert track["location"].startswith(str(tmp_path / "Media"))         # copied off the iPod
    assert (music / "CCCC.mp3").exists()                                 # the iPod is untouched
    assert import_songs.new_songs(conn, on_ipod) == []                   # nothing left to add
