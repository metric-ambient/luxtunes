"""Add File / Add Folder to Library."""

import pytest

pytest.importorskip("mutagen")

from luxtunes.core import add_files, db, library, organize, tags  # noqa: E402

FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


def mp3(path, **fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FRAME * 40)
    if fields:
        tags.write_tags(str(path), fields, lyrics="Hello")
    return str(path)


def setup(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    organize.set_media_folder(conn, tmp_path / "Media")
    return conn, tmp_path / "Media"


def test_read_fields(tmp_path):
    path = mp3(tmp_path / "a.mp3", name="Robbers", artist="The 1975", album="The 1975",
               year=2013, track_number=6, track_count=16, disc_number=1, disc_count=1,
               bpm=90, compilation=0, genre="Indie", comments="Nice")
    fields = tags.read_fields(path)
    assert fields["name"] == "Robbers" and fields["artist"] == "The 1975"
    assert (fields["track_number"], fields["track_count"]) == (6, 16)
    assert (fields["disc_number"], fields["disc_count"]) == (1, 1)
    assert fields["year"] == 2013 and fields["bpm"] == 90 and fields["comments"] == "Nice"
    assert fields["lyrics"] == "Hello" and fields["kind"] == "MPEG audio file"
    assert fields["bit_rate"] == 128 and fields["sample_rate"] == 44100 and fields["total_time"] > 0


def test_add_folder_copies_into_media_folder(tmp_path):
    conn, root = setup(tmp_path)
    mp3(tmp_path / "Downloads" / "x.mp3", name="Robbers", artist="The 1975",
        album="The 1975", track_number=6)
    mp3(tmp_path / "Downloads" / "sub" / "untagged song.mp3")
    (tmp_path / "Downloads" / "cover.jpg").write_bytes(b"not music")
    result = add_files.add_paths(conn, [str(tmp_path / "Downloads")])
    assert len(result.added) == 2 and not result.failed
    first = library.load_track(conn, result.added[0])
    assert first["location"] == str(root / "Music" / "The 1975" / "The 1975" / "06 Robbers.mp3")
    assert first["date_added"] and first["media_kind"] == "music" and first["lyrics"] == "Hello"
    second = library.load_track(conn, result.added[1])
    assert second["name"] == "untagged song"
    assert second["location"].endswith("Unknown Artist/Unknown Album/untagged song.mp3")
    assert (tmp_path / "Downloads" / "x.mp3").exists()           # original left alone

    # Adding the same folder again adds nothing new.
    again = add_files.add_paths(conn, [str(tmp_path / "Downloads")])
    assert again.added == [] and sorted(again.already) == sorted(result.added)


def test_files_always_end_up_in_the_media_folder(tmp_path):
    from luxtunes.core.db import set_setting
    conn, root = setup(tmp_path)
    set_setting(conn, "copy_when_adding", "0")          # the old "don't copy" choice: ignored
    path = mp3(tmp_path / "Elsewhere" / "x.mp3", name="Song", artist="A", album="B")
    result = add_files.add_paths(conn, [path])
    assert library.load_track(conn, result.added[0])["location"].startswith(str(root))
    assert (tmp_path / "Elsewhere" / "x.mp3").exists()          # copied; the original stays
    assert add_files.add_paths(conn, [path]).already == result.added
    bad = tmp_path / "Elsewhere" / "bad.mp3"
    bad.write_bytes(b"not audio")
    assert len(add_files.add_paths(conn, [str(bad)]).failed) == 1


def test_move_original_files_on_import(tmp_path):
    conn, root = setup(tmp_path)
    assert not add_files.move_when_adding(conn)                 # off unless turned on
    add_files.set_move_when_adding(conn, True)
    path = mp3(tmp_path / "Downloads" / "x.mp3", name="Song", artist="A", album="B")
    result = add_files.add_paths(conn, [str(tmp_path / "Downloads")])
    moved = library.load_track(conn, result.added[0])["location"]
    assert moved == str(root / "Music" / "A" / "B" / "Song.mp3")
    assert not (tmp_path / "Downloads" / "x.mp3").exists() and (root / "Music" / "A" / "B" /
                                                               "Song.mp3").exists()
    assert path not in moved


def test_untagged_video_is_added_as_a_movie(tmp_path):
    import shutil
    import subprocess
    conn, root = setup(tmp_path)
    clip = tmp_path / "Downloads" / "Home Movie.mkv"
    clip.parent.mkdir(parents=True)
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x48:d=2",
                        "-c:v", "mpeg4", str(clip)], check=True)
    else:
        clip.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 200)
    result = add_files.add_paths(conn, [str(clip.parent)])
    assert len(result.added) == 1 and not result.failed
    track = library.load_track(conn, result.added[0])
    assert track["media_kind"] == "movie" and track["name"] == "Home Movie"
    assert track["location"] == str(root / "Movies" / "Home Movie.mkv")
    if shutil.which("ffprobe"):
        assert 1500 < track["total_time"] < 2500


def test_podcasts_and_audiobooks_are_recognised(tmp_path):
    from mutagen.id3 import ID3, PCST

    conn, root = setup(tmp_path)
    episode = mp3(tmp_path / "Downloads" / "episode.mp3", name="Ep 1", artist="Host",
                  album="The Show")
    flagged = ID3(episode)
    flagged.add(PCST(value=1))                # the podcast flag iTunes writes into MP3s
    flagged.save(episode)
    book = mp3(tmp_path / "Downloads" / "book.m4b", name="Chapter 1", artist="A. Writer",
               album="Big Book")
    plain = mp3(tmp_path / "Downloads" / "song.mp3", name="Song", artist="Band", album="LP")
    assert tags.media_kind_hint(episode) == "podcast" and tags.media_kind_hint(book) == "audiobook"
    assert tags.media_kind_hint(plain) is None
    result = add_files.add_paths(conn, [episode, book, plain])
    added = {t["name"]: t for t in (library.load_track(conn, i) for i in result.added)}
    assert added["Ep 1"]["media_kind"] == "podcast" and added["Ep 1"]["bookmarkable"] == 1
    assert added["Ep 1"]["location"] == str(root / "Podcasts" / "The Show" / "Ep 1.mp3")
    assert added["Chapter 1"]["media_kind"] == "audiobook"
    assert added["Chapter 1"]["location"].startswith(str(root / "Audiobooks" / "A. Writer" / "Big Book"))
    assert added["Song"]["media_kind"] == "music" and not added["Song"]["bookmarkable"]


def test_changing_the_media_kind(tmp_path):
    conn, root = setup(tmp_path)
    song = add_files.add_paths(conn, [mp3(tmp_path / "x.mp3", name="Talk", artist="Host")]).added[0]
    library.update_track_fields(conn, song, {"media_kind": "podcast"})
    track = library.load_track(conn, song)
    assert track["media_kind"] == "podcast"
    assert track["bookmarkable"] == 1 and track["skip_when_shuffling"] == 1    # podcast defaults
    library.update_track_fields(conn, song, {"media_kind": "audiobook", "bookmarkable": 0})
    assert library.load_track(conn, song)["bookmarkable"] == 0                 # your choice wins
    with pytest.raises(ValueError):
        library.update_track_fields(conn, song, {"media_kind": "ringtone"})


def test_a_stopped_copy_leaves_no_half_file(tmp_path):
    import threading

    source = tmp_path / "big.mp3"
    source.write_bytes(b"x" * (3 * add_files.CHUNK))
    target = tmp_path / "copy.mp3"
    stop = threading.Event()
    stop.set()
    with pytest.raises(add_files.AddStopped):
        add_files.copy_file(str(source), target, stop)
    assert not target.exists()
    add_files.copy_file(str(source), target)
    assert target.read_bytes() == source.read_bytes()


def test_one_odd_file_doesnt_stop_the_rest(tmp_path, monkeypatch):
    conn, root = setup(tmp_path)
    for i in range(3):
        mp3(tmp_path / "In" / f"{i}.mp3", name=f"Song {i}", artist="A")
    real = add_files.read_fields

    def odd(path):
        if path.endswith("1.mp3"):
            raise RuntimeError("a file mutagen chokes on")
        return real(path)

    monkeypatch.setattr(add_files, "read_fields", odd)
    seen = []
    result = add_files.add_paths(conn, [str(tmp_path / "In")],
                                 progress=lambda done, total, path: seen.append(path) or True)
    assert len(result.added) == 2 and len(result.failed) == 1 and len(seen) == 3
    assert "RuntimeError" in result.failed[0] and not result.stopped


def test_empty_tag_frames_dont_stop_a_file(tmp_path):
    from mutagen.id3 import COMM, ID3, TCOM, TPE2

    conn, root = setup(tmp_path)
    path = mp3(tmp_path / "In" / "1-06 Song.mp3", name="Song", artist="Band")
    id3 = ID3(path)
    # Frames with no text at all (some taggers write these): read as empty.
    id3.add(COMM(encoding=3, lang="eng", desc="", text=[]))
    id3.add(TCOM(encoding=3, text=[]))
    id3.add(TPE2(encoding=3, text=[]))
    id3.save(path, v2_version=3)
    fields = tags.read_fields(path)
    assert fields["name"] == "Song" and fields.get("comments") is None
    result = add_files.add_paths(conn, [str(tmp_path / "In")])
    assert len(result.added) == 1 and not result.failed


def test_songs_already_in_the_library_are_skipped_and_the_rest_added(tmp_path):
    conn, root = setup(tmp_path)
    mp3(tmp_path / "Old" / "a.mp3", name="Robbers", artist="The 1975", album="The 1975")
    first = add_files.add_paths(conn, [str(tmp_path / "Old")])
    # Edited since (so its copy in the media folder no longer matches the original's size).
    library.update_track_fields(conn, first.added[0], {"comments": "edited"})
    from luxtunes.core import tag_sync
    tag_sync.write_pending(conn, first.added[0])
    # A bigger import later: the same song again (in two places), plus a new one.
    mp3(tmp_path / "All" / "Old" / "a.mp3", name="Robbers", artist="The 1975", album="The 1975")
    mp3(tmp_path / "All" / "Copy" / "a again.mp3", name="Robbers", artist="The 1975",
        album="The 1975")
    mp3(tmp_path / "All" / "New" / "b.mp3", name="Chocolate", artist="The 1975", album="The 1975")
    result = add_files.add_paths(conn, [str(tmp_path / "All")])
    assert len(result.already) == 2 and not result.failed and not result.stopped
    assert [library.load_track(conn, t)["name"] for t in result.added] == ["Chocolate"]
    assert library.count_tracks(conn, "music") == 2
