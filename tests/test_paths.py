"""Tests for finding music files on disk."""

from luxtunes.core.paths import find_file, nearest_existing_folder


def test_find_file_exact_and_case_insensitive(tmp_path):
    song = tmp_path / "Music" / "Bright Harbor" / "Dear Entropy" / "01 Tidewater.mp3"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"")
    assert find_file(str(song)) == str(song)
    wrong_case = tmp_path / "Music" / "bright harbor" / "DEAR ENTROPY" / "01 Tidewater.mp3"
    assert find_file(str(wrong_case)) == str(song)


def test_find_file_missing(tmp_path):
    assert find_file(str(tmp_path / "Music" / "Nope" / "song.mp3")) is None
    assert find_file(None) is None


def test_nearest_existing_folder(tmp_path):
    (tmp_path / "Music").mkdir()
    expected = tmp_path / "Music" / "Missing Artist" / "Missing Album" / "song.mp3"
    assert nearest_existing_folder(str(expected)) == str(tmp_path / "Music")


def test_file_addresses_keep_commas_inside_one_path():
    from luxtunes.core.paths import file_uri
    uri = file_uri("/mnt/Drive 8TB/Music/01 - The Lion, the Witch and the Wardrobe.m4b")
    assert uri.startswith("file:///mnt/Drive%208TB/") and "," not in uri and "%2C" in uri


def test_a_big_log_keeps_its_newest_part(tmp_path):
    from luxtunes.core.paths import trim_log
    log = tmp_path / "crash.log"
    log.write_bytes(b"".join(b"line %06d\n" % i for i in range(200_000)))     # about 2.2 MB
    trim_log(log)
    text = log.read_bytes()
    assert len(text) <= 256 * 1024 + 100
    assert text.startswith(b"(older entries removed)\nline ") and text.endswith(b"line 199999\n")
    small = tmp_path / "small.log"
    small.write_bytes(b"one crash\n")
    trim_log(small)
    assert small.read_bytes() == b"one crash\n"                                 # left alone


def test_the_appimages_own_ffmpeg_is_used_when_none_is_installed(tmp_path, monkeypatch):
    import shutil
    import stat

    from luxtunes.core import paths
    bundled = tmp_path / "usr" / "lib" / "ffmpeg" / "ffmpeg"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\n")
    bundled.chmod(bundled.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(paths, "WINDOWS", False)
    monkeypatch.setenv("APPDIR", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: None)          # nothing installed
    assert paths.find_program("ffmpeg") == str(bundled)
    assert paths.find_program("ffprobe") is None                       # not in this AppImage
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    assert paths.find_program("ffmpeg") == "/usr/bin/ffmpeg"          # an installed one wins
    monkeypatch.delenv("APPDIR")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert paths.find_program("ffmpeg") is None                        # not an AppImage
