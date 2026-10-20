"""Android sync, into a folder standing in for the phone (no phone needed)."""

import json
import sqlite3

import pytest

pytest.importorskip("mutagen")

from luxtunes.core import db, library, tags  # noqa: E402
from luxtunes.core.android import device, sync  # noqa: E402
from luxtunes.core.ipod import settings  # noqa: E402

FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


def small_library(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    for i, name in enumerate(("Robbers", "Chocolate", "Wasteland")):
        path = tmp_path / f"{i}.mp3"
        path.write_bytes(FRAME * 100)
        tags.write_tags(str(path), {"name": name, "artist": "The 1975"})
        conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, location, size, kind, "
                     "total_time, play_count, rating) VALUES (?, ?, 'The 1975', 'The 1975', ?, ?, "
                     "'MPEG audio file', 2600, ?, 60)",
                     (f"ABCDEF00000000{i:02d}", name, str(path), path.stat().st_size, i))
    conn.commit()
    library.add_to_playlist(conn, library.create_playlist(conn, "Mix"), [3, 1])
    return conn


def test_finds_phones_the_desktop_shows(tmp_path):
    gvfs = tmp_path / "gvfs"
    (gvfs / "mtp:host=Google_Pixel_7_28141FDH2000XY" / "Internal shared storage").mkdir(parents=True)
    (gvfs / "mtp:host=Google_Pixel_7_28141FDH2000XY" / "SD card").mkdir()
    (gvfs / "smb-share:server=nas").mkdir()
    [phone] = device.find_phones(gvfs)
    assert (phone.maker, phone.model, phone.serial) == ("Google", "Pixel 7", "28141FDH2000XY")
    assert phone.key == "android:28141FDH2000XY" and phone.name == "Pixel 7"
    assert phone.uri == "mtp://Google_Pixel_7_28141FDH2000XY/"
    assert phone.storage().endswith("Internal shared storage")
    assert phone.storage("SD card").endswith("SD card")
    assert device.parse_host("mtp:host=SAMSUNG_SAMSUNG_Android_R58M12")[:2] == ("Samsung", "Android")


def fake_phone(tmp_path):
    folder = tmp_path / "phone"
    (folder / "Internal shared storage").mkdir(parents=True)
    return device.Phone(str(folder), "Google", "Pixel 7", "SERIAL1",
                        {"Internal shared storage": str(folder / "Internal shared storage")})


def run(conn, phone, **change):
    record = settings.load_device(conn, phone.key, phone.name)
    for key, value in change.items():
        setattr(record.settings, key, value)
    return sync.sync(conn, phone, record)


def test_sync_puts_files_playlists_and_details_on_the_phone(tmp_path):
    conn = small_library(tmp_path)
    phone = fake_phone(tmp_path)
    root = tmp_path / "phone" / "Internal shared storage" / "LuxTunes"
    report = run(conn, phone)
    assert (report.added, report.removed) == (3, 0) and not report.problems, report.problems
    songs = sorted(p.name for p in (root / "LuxTunes Media" / "Music").rglob("*.mp3"))
    assert songs == ["Chocolate.mp3", "Robbers.mp3", "Wasteland.mp3"]
    playlist = (root / "Playlists" / "Mix.m3u8").read_text()
    assert playlist.startswith("#EXTM3U") and "../LuxTunes Media/Music/The 1975/" in playlist
    assert playlist.index("Wasteland") < playlist.index("Robbers")    # playlist order kept
    with sqlite3.connect(root / "library.db") as db:
        rows = dict(db.execute("SELECT name, rating FROM tracks"))
        assert rows == {"Robbers": 60, "Chocolate": 60, "Wasteland": 60}
        assert db.execute("SELECT name FROM playlists").fetchall() == [("Mix",)]
        assert db.execute("SELECT COUNT(*) FROM playlist_items").fetchone()[0] == 2
        info = dict(db.execute("SELECT key, value FROM info"))
        assert info["format_version"] == "1" and info["library_id"]
    again = run(conn, phone)                                        # nothing changed
    assert (again.added, again.removed) == (0, 0)


def test_plays_and_ratings_come_back_and_only_our_files_are_removed(tmp_path):
    conn = small_library(tmp_path)
    phone = fake_phone(tmp_path)
    root = tmp_path / "phone" / "Internal shared storage" / "LuxTunes"
    run(conn, phone)
    pid = {r["name"]: r["persistent_id"] for r in conn.execute("SELECT * FROM tracks")}
    (root / "changes.json").write_text(json.dumps({"version": 1, "tracks": [
        {"id": pid["Robbers"], "plays": 2, "last_played": "2026-10-20T10:00:00Z"},
        {"id": pid["Chocolate"], "rating": 100}]}))
    mine = root.parent / "Music" / "my own song.mp3"                # outside the LuxTunes folder
    mine.parent.mkdir()
    mine.write_bytes(b"not LuxTunes'")
    report = run(conn, phone, music_mode="selected", playlists=[], artists=[], genres=[],
                 albums=[])
    got = {r["name"]: (r["play_count"], r["rating"]) for r in conn.execute("SELECT * FROM tracks")}
    assert got["Robbers"] == (2, 60) and got["Chocolate"] == (1, 100)
    assert json.loads((root / "changes.json").read_text())["tracks"] == []   # can't count twice
    assert report.removed == 3 and mine.exists()
    assert not list((root / "LuxTunes Media").rglob("*.mp3"))
    assert not (root / "Playlists" / "Mix.m3u8").exists()
    assert all((tmp_path / f"{i}.mp3").exists() for i in range(3))   # the library's files stay


def test_edited_songs_are_copied_again(tmp_path):
    import os
    conn = small_library(tmp_path)
    phone = fake_phone(tmp_path)
    run(conn, phone)
    source = tmp_path / "0.mp3"
    os.utime(source, ns=(1, 1))                       # e.g. tags rewritten after Get Info
    report = run(conn, phone)
    assert report.added == 1


def test_notices_a_file_transfer_device(tmp_path):
    usb = tmp_path / "usb"
    (usb / "1-2").mkdir(parents=True)
    (usb / "1-2:1.0").mkdir()
    (usb / "1-2:1.0" / "bInterfaceClass").write_text("08\n")
    assert not device.mtp_usb_connected(str(usb))
    (usb / "1-3:1.0").mkdir()
    (usb / "1-3:1.0" / "bInterfaceClass").write_text("ff\n")
    (usb / "1-3:1.0" / "interface").write_text("MTP\n")
    assert device.mtp_usb_connected(str(usb))


def test_unselected_media_stays_until_delete_is_turned_on(tmp_path):
    conn = small_library(tmp_path)
    phone = fake_phone(tmp_path)
    root = tmp_path / "phone" / "Internal shared storage" / "LuxTunes"
    media = root / "LuxTunes Media"
    run(conn, phone)
    stray = media / "Music" / "left behind.mp3"                     # e.g. from a lost record
    stray.write_bytes(b"x")
    nothing = dict(music_mode="selected", playlists=[], artists=[], genres=[], albums=[])
    kept = run(conn, phone, android_delete_unselected=False, **nothing)
    assert kept.removed == 0 and len(list(media.rglob("*.mp3"))) == 4
    removals = []
    cleaned = sync.sync(conn, phone, _record(conn, phone, **nothing),
                        confirm=lambda add, remove, why: removals.extend(why) or True)
    assert cleaned.removed == 4 and not list(media.rglob("*.mp3"))
    assert any(r.reason == "Not synced by LuxTunes" and r.name == "left behind.mp3"
               for r in removals)
    assert media.is_dir() and not (media / "Music").exists()        # empty folders go too


def _record(conn, phone, **change):
    record = settings.load_device(conn, phone.key, phone.name)
    for key, value in change.items():
        setattr(record.settings, key, value)
    return record


def test_nothing_outside_the_luxtunes_folder_is_ever_deleted(tmp_path):
    conn = small_library(tmp_path)
    phone = fake_phone(tmp_path)
    storage = tmp_path / "phone" / "Internal shared storage"
    root = storage / "LuxTunes"
    run(conn, phone)
    photo = storage / "DCIM" / "photo.jpg"
    photo.parent.mkdir()
    photo.write_bytes(b"photo")
    # A tampered record pointing outside, and a link out of the media folder.
    manifest = json.loads((root / ".luxtunes-sync.json").read_text())
    manifest["files"]["FFFF000000000001"] = {"path": "../DCIM/photo.jpg", "name": "x"}
    manifest["covers"].append("../DCIM/photo.jpg")
    manifest["playlists"].append("../DCIM/photo.jpg")
    (root / ".luxtunes-sync.json").write_text(json.dumps(manifest))
    (root / "LuxTunes Media" / "Music" / "link.jpg").symlink_to(photo)
    report = run(conn, phone)
    assert photo.read_bytes() == b"photo"
    assert any("not inside the LuxTunes folder" in p for p in report.problems)
    manifest = json.loads((root / ".luxtunes-sync.json").read_text())
    assert "FFFF000000000001" not in manifest["files"]                # the bad entry is dropped
    for bad in ("../DCIM/photo.jpg", str(photo), "LuxTunes Media/Music/link.jpg"):
        with pytest.raises(sync.OutsideFolder):
            sync.contained(root, bad)
    with pytest.raises(sync.OutsideFolder):
        sync.contained(storage, "DCIM/photo.jpg")                   # only a LuxTunes folder


def test_a_chosen_playlist_brings_its_songs_and_videos(tmp_path):
    conn = small_library(tmp_path)
    video = tmp_path / "film.mkv"                       # a phone plays it; an iPod couldn't
    video.write_bytes(b"video")
    conn.execute("INSERT INTO tracks (persistent_id, name, location, size, media_kind) "
                 "VALUES ('ABCDEF0000000099', 'Film', ?, 5, 'movie')", (str(video),))
    conn.commit()
    mix = conn.execute("SELECT id, persistent_id FROM playlists WHERE name = 'Mix'").fetchone()
    film = conn.execute("SELECT id FROM tracks WHERE name = 'Film'").fetchone()[0]
    library.add_to_playlist(conn, mix[0], [film])
    phone = fake_phone(tmp_path)
    root = tmp_path / "phone" / "Internal shared storage" / "LuxTunes"
    # Only the playlist is chosen: no artists, albums or genres, no movies.
    report = run(conn, phone, music_mode="selected", playlists=[mix[1]], artists=[], albums=[],
                 genres=[], sync_movies=False)
    assert report.added == 3 and not report.problems, report.problems
    names = sorted(p.name for p in (root / "LuxTunes Media").rglob("*.*"))
    assert names == ["Film.mkv", "Robbers.mp3", "Wasteland.mp3"]
    playlist = (root / "Playlists" / "Mix.m3u8").read_text()
    assert "Film.mkv" in playlist and "Chocolate" not in playlist
