"""Edit menu work on the library: pasting into Library sections, and Undo's snapshots."""

import pytest

pytest.importorskip("mutagen")

from luxtunes.core import db, edits, library, organize  # noqa: E402


def _library(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    organize.set_media_folder(conn, tmp_path / "Media")
    song = tmp_path / "Media" / "Music" / "Artist" / "Album" / "01 Talk.mp3"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"audio")
    film = tmp_path / "Media" / "Movies" / "Film.avi"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"video")
    for pid, name, kind, path in (("00000000000000A1", "Talk", "music", song),
                                  ("00000000000000A2", "Film", "movie", film)):
        conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, track_number, "
                     "media_kind, location, play_count) VALUES (?, ?, 'Artist', 'Album', 1, ?, ?, 4)",
                     (pid, name, kind, str(path)))
    conn.commit()
    tracks = {t["name"]: t for t in library.load_tracks(conn)}
    return conn, tracks, song


def test_what_can_go_where(tmp_path):
    conn, tracks, _ = _library(tmp_path)
    talk, film = tracks["Talk"], tracks["Film"]
    assert edits.can_go(talk, "podcast") == edits.OK
    assert edits.can_go(talk, "music") == edits.SAME
    assert "isn't a video file" in edits.can_go(talk, "tvshow")
    assert "is a video file" in edits.can_go(film, "audiobook")
    assert edits.can_go(film, "tvshow") == edits.OK            # Movies <-> TV Shows is fine
    assert edits.can_go(dict(talk, media_kind="audiobook"), "music") == edits.OK   # back again
    assert edits.can_go(film, "music") == edits.OK              # a music video


def test_a_pasted_copy_is_a_new_file_in_the_new_section(tmp_path):
    conn, tracks, song = _library(tmp_path)
    new_id = edits.duplicate_as(conn, tracks["Talk"]["id"], "podcast")
    copy = library.load_track(conn, new_id)
    assert copy["media_kind"] == "podcast" and copy["play_count"] == 0
    assert copy["persistent_id"] != tracks["Talk"]["persistent_id"]
    assert copy["location"] == str(tmp_path / "Media" / "Podcasts" / "Album" / "Talk.mp3")
    assert open(copy["location"], "rb").read() == b"audio"
    assert copy["bookmarkable"] == 1                           # a podcast remembers its place
    original = library.load_track(conn, tracks["Talk"]["id"])
    assert original["media_kind"] == "music" and original["location"] == str(song)
    song.unlink()
    with pytest.raises(FileNotFoundError):
        edits.duplicate_as(conn, tracks["Talk"]["id"], "audiobook")


def test_undo_brings_deleted_entries_and_playlist_order_back(tmp_path):
    conn, tracks, _ = _library(tmp_path)
    ids = [tracks["Talk"]["id"], tracks["Film"]["id"]]
    playlist = library.create_playlist(conn, "Mix")
    library.add_to_playlist(conn, playlist, [ids[1], ids[0], ids[1]])
    snapshot = edits.snapshot_tracks(conn, [ids[0]])
    library.delete_tracks(conn, [ids[0]])
    assert edits.playlist_order(conn, playlist) == [ids[1], ids[1]]
    edits.restore_tracks(conn, snapshot)
    assert library.load_track(conn, ids[0])["play_count"] == 4
    assert edits.playlist_order(conn, playlist) == [ids[1], ids[0], ids[1]]
    edits.set_playlist_order(conn, playlist, [ids[0], 9999])   # unknown ids are left out
    assert edits.playlist_order(conn, playlist) == [ids[0]]
