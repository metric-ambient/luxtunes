"""Create MP3 Version: FLAC -> 320 kbps MP3 beside the original."""

import shutil
import subprocess

import pytest

pytest.importorskip("mutagen")

from luxtunes.core import convert, db, library, organize, tags  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
       b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84"
       b"\x00\x00\x00\x00IEND\xaeB`\x82")


def test_target_and_command(tmp_path):
    flac = tmp_path / "06 Robbers.flac"
    flac.write_bytes(b"x")
    assert convert.mp3_target(str(flac)) == tmp_path / "06 Robbers.mp3"
    (tmp_path / "06 Robbers.mp3").write_bytes(b"x")
    assert convert.mp3_target(str(flac)) == tmp_path / "06 Robbers 1.mp3"
    cmd = convert.command("ffmpeg", "a.flac", "a.mp3", 96000)
    assert cmd[-1] == "a.mp3" and "320k" in cmd and "44100" in cmd
    assert "44100" not in convert.command("ffmpeg", "a.flac", "a.mp3", 44100)
    assert convert.can_convert({"location": "/x/Song.FLAC"})
    assert not convert.can_convert({"location": "/x/Song.mp3"})


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_real_conversion(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    root = tmp_path / "Media"
    organize.set_media_folder(conn, root)
    folder = root / "Music" / "The 1975" / "The 1975"
    folder.mkdir(parents=True)
    flac = folder / "06 Robbers.flac"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(flac)], check=True)
    tags.write_tags(str(flac), {"name": "Robbers", "artist": "The 1975", "album": "The 1975",
                                "track_number": 6}, lyrics="La la", artworks=[PNG])
    conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, track_number, rating, "
                 "location) VALUES ('P1', 'Robbers', 'The 1975', 'The 1975', 6, 80, ?)", (str(flac),))
    conn.commit()
    source = library.load_track(conn, 1)

    target = convert.mp3_target(str(flac))
    subprocess.run(convert.command("ffmpeg", str(flac), str(target), 44100), check=True)
    new_id = convert.finish(conn, source, str(flac), str(target))

    new = library.load_track(conn, new_id)
    assert new["location"] == str(folder / "06 Robbers.mp3")
    assert new["name"] == "Robbers" and new["kind"] == "MPEG audio file" and new["rating"] == 80
    assert new["bit_rate"] == 320
    details = tags.read_details(new["location"])
    assert details["lyrics"] == "La la" and details["artworks"] == [PNG]
    assert details["tag_version"] == "v2.3"
    assert flac.exists()


def test_which_versions_each_format_gets():
    def offered(location, kind="", media_kind="music"):
        track = {"location": location, "kind": kind, "media_kind": media_kind}
        return [to for to in convert.FORMATS if convert.can_convert(track, to)]
    for lossless in ("/x/a.flac", "/x/a.wav", "/x/a.aiff", "/x/a.ogg", "/x/a.opus"):
        assert offered(lossless) == ["mp3", "aac"]
    assert offered("/x/a.m4a", "Apple Lossless audio file") == ["mp3", "aac"]
    assert offered("/x/a.mp3", "MPEG audio file") == ["aac"]
    assert offered("/x/a.m4a", "AAC audio file") == ["mp3"]
    assert offered("/x/a.m4b", "AAC audio file", "audiobook") == ["mp3"]
    assert offered("/x/a.m4p", "Protected AAC audio file") == []
    assert offered("/x/film.m4v", "MPEG-4 video file", "movie") == []
    assert convert.target_for("/x/a.mp3", "aac").name == "a.m4a"
    cmd = convert.command("ffmpeg", "a.mp3", "a.m4a", 44100, to="aac")
    assert cmd[-1] == "a.m4a" and "256k" in cmd and "aac" in cmd


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_real_aac_version_of_an_mp3(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    organize.set_media_folder(conn, tmp_path / "Media")
    song = tmp_path / "Media" / "Music" / "A" / "B" / "01 Song.mp3"
    song.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-codec:a", "libmp3lame", str(song)],
                   check=True)
    tags.write_tags(str(song), {"name": "Song", "artist": "A", "album": "B"}, artworks=[PNG])
    conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, kind, location) "
                 "VALUES ('P1', 'Song', 'A', 'B', 'MPEG audio file', ?)", (str(song),))
    conn.commit()
    source = library.load_track(conn, 1)
    target = convert.target_for(str(song), "aac")
    subprocess.run(convert.command("ffmpeg", str(song), str(target), 44100, to="aac"), check=True)
    new = library.load_track(conn, convert.finish(conn, source, str(song), str(target)))
    assert new["location"].endswith("01 Song.m4a") and new["kind"] == "AAC audio file"
    assert new["name"] == "Song" and tags.read_details(new["location"])["artworks"] == [PNG]


def test_ipod_video_name_and_settings(tmp_path):
    from luxtunes.core import convert
    source = tmp_path / "Film.mkv"
    source.write_bytes(b"x")
    assert convert.ipod_video_target(str(source)).name == "Film (iPod).m4v"
    (tmp_path / "Film (iPod).m4v").write_bytes(b"x")
    assert convert.ipod_video_target(str(source)).name == "Film (iPod) 2.m4v"
    command = convert.ipod_video_command("ffmpeg", str(source), "out.m4v")
    assert command[command.index("-profile:v") + 1] == "baseline"
    assert "640" in command[command.index("-vf") + 1] and command[-1] == "out.m4v"
    assert convert.can_make_ipod_video({"media_kind": "movie", "location": "/a.mkv"})
    assert not convert.can_make_ipod_video({"media_kind": "music", "location": "/a.mp3"})


def test_conversion_copies_stay_beside_the_original_and_can_replace_it(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    organize.set_media_folder(conn, tmp_path / "Media")
    episode = tmp_path / "Media" / "TV Shows" / "Show" / "Season 1" / "01 Pilot.avi"
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"x")
    conn.execute("INSERT INTO tracks (persistent_id, name, media_kind, show, season, location, "
                 "play_count, rating) VALUES ('00000000000000E1', 'Pilot', 'tvshow', 'Show', 1, "
                 "?, 7, 80)", (str(episode),))
    conn.commit()
    original = library.load_tracks(conn)[0]
    playlist = library.create_playlist(conn, "Watch")
    library.add_to_playlist(conn, playlist, [original["id"]])
    copy = episode.with_name("01 Pilot (iPod).m4v")
    copy.write_bytes(b"x")
    copy_id = convert.finish_video(conn, original, str(copy))
    made = library.load_track(conn, copy_id)
    assert made["location"] == str(copy)                     # not moved under Movies
    assert made["media_kind"] == "tvshow" and made["show"] == "Show"

    assert not convert.delete_original(conn)                  # off unless turned on
    convert.set_delete_original(conn, True)
    assert convert.delete_original(conn)
    convert.replace_original(conn, original["id"], copy_id)
    assert library.load_track(conn, original["id"]) is None
    made = library.load_track(conn, copy_id)
    assert made["play_count"] == 7
    items = [r[0] for r in conn.execute("SELECT track_id FROM playlist_items")]
    assert items == [copy_id]
