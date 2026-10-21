"""iPod sync with the real libgpod, on a fake iPod made in a temporary folder.

Skipped where libgpod isn't installed (sudo apt install libgpod-common)."""

import ctypes
import os
import struct

import pytest

pytest.importorskip("mutagen")
from luxtunes.core.ipod import gpod  # noqa: E402

if not gpod.available():
    pytest.skip("libgpod isn't installed", allow_module_level=True)

from luxtunes.core import db, library, tags  # noqa: E402
from luxtunes.core.ipod import device, settings, sync  # noqa: E402

FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413
MAC_EPOCH = 2082844800


def fake_ipod(tmp_path):
    mount = tmp_path / "IPOD"
    (mount / "iPod_Control" / "Device").mkdir(parents=True)
    # The FireWire ID LuxTunes reads from USB; libgpod needs it to sign a classic's database.
    (mount / "iPod_Control" / "Device" / "SysInfo").write_text(
        "ModelNumStr: xB147\nFirewireGuid: 0x000A27001387CE50\n")
    gpod.init_ipod(str(mount), "xB147", "Test iPod")          # an empty iPod classic
    return mount


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


def do_sync(conn, mount, **change):
    ipod = device.read_ipod(str(mount), "", "vfat")
    record = settings.load_device(conn, ipod.key, ipod.name)
    for key, value in change.items():
        setattr(record.settings, key, value)
    return sync.sync(conn, ipod, record)


def test_sync_copies_signs_and_reads_back(tmp_path):
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    report = do_sync(conn, mount)
    assert (report.added, report.removed, report.playlists) == (3, 0, 1)
    data = (mount / "iPod_Control/iTunes/iTunesDB").read_bytes()
    assert struct.unpack_from("<H", data, 0x30)[0] == 1 and data[0x58:0x6C] != bytes(20)   # signed
    with gpod.Database(str(mount)) as ipod_db:
        titles = sorted(gpod.get_str(t.contents, "title") for t in ipod_db.tracks())
        assert titles == ["Chocolate", "Robbers", "Wasteland"]
        for t in ipod_db.tracks():
            assert (mount / gpod.get_str(t.contents, "ipod_path").lstrip(":").replace(":", "/")).exists()
        names = [gpod.playlist_name(p) for p in ipod_db.playlists()]
        assert "Mix" in names


def test_plays_on_ipod_are_added_never_reset(tmp_path):
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    do_sync(conn, mount)
    with gpod.Database(str(mount)) as ipod_db:
        order = [gpod.get_str(t.contents, "title") for t in ipod_db.tracks()]
    plays = {"Robbers": 3, "Chocolate": 0, "Wasteland": 1}
    counts = b"mhdp" + struct.pack("<III", 0x60, 0x1C, len(order))
    counts = counts.ljust(0x60, b"\0")
    for title in order:
        rating = 100 if title == "Chocolate" else 60
        counts += struct.pack("<IIIIIII", plays[title], 1_800_000_000 + MAC_EPOCH if plays[title] else 0,
                              0, rating, 0, 0, 0)
    (mount / "iPod_Control/iTunes/Play Counts").write_bytes(counts)
    do_sync(conn, mount)
    got = {r["name"]: (r["play_count"], r["rating"]) for r in conn.execute("SELECT * FROM tracks")}
    assert got == {"Robbers": (3, 60), "Chocolate": (1, 100), "Wasteland": (3, 60)}
    assert not (mount / "iPod_Control/iTunes/Play Counts").exists()
    do_sync(conn, mount)                                     # nothing new: nothing changes
    again = {r["name"]: (r["play_count"], r["rating"]) for r in conn.execute("SELECT * FROM tracks")}
    assert again == got


def test_unselected_songs_leave_the_ipod_but_not_the_library(tmp_path):
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    do_sync(conn, mount)
    report = do_sync(conn, mount, music_mode="selected", playlists=[])
    assert report.removed == 3
    assert not list((mount / "iPod_Control/Music").rglob("*.mp3"))
    assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 3
    assert all((tmp_path / f"{i}.mp3").exists() for i in range(3))


def test_you_see_which_songs_come_off_and_why(tmp_path):
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    do_sync(conn, mount)
    # "Robbers" leaves the library, "Chocolate" is no longer chosen, and the
    # file of "Wasteland" can't be found on this computer.
    conn.execute("DELETE FROM tracks WHERE name = 'Robbers'")
    conn.commit()
    (tmp_path / "2.mp3").unlink()
    ipod = device.read_ipod(str(mount), "", "vfat")
    record = settings.load_device(conn, ipod.key, ipod.name)
    record.settings.music_mode = "selected"
    record.settings.playlists = []
    record.settings.artists = ["The 1975"]
    asked = []
    conn.execute("UPDATE tracks SET artist = 'Someone Else' WHERE name = 'Chocolate'")
    conn.commit()
    report = sync.sync(conn, ipod, record,
                       confirm=lambda adding, removing, removals: asked.append(removals) or False)
    assert report.cancelled
    reasons = {r.name: r.reason for r in asked[0]}
    assert reasons == {"Robbers": "Not in your LuxTunes library",
                       "Chocolate": "Not in your sync settings"}       # Wasteland stays
    assert {r.artist for r in asked[0]} == {"The 1975"} and {r.album for r in asked[0]} == {"The 1975"}
    with gpod.Database(str(mount)) as ipod_db:                         # cancelled: nothing changed
        assert len(ipod_db.tracks()) == 3


def test_podcasts_and_audiobooks_land_in_their_ipod_menus(tmp_path):
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    library.update_track_fields(conn, 1, {"media_kind": "podcast"})      # Robbers
    library.update_track_fields(conn, 2, {"media_kind": "audiobook"})    # Chocolate
    report = do_sync(conn, mount, sync_podcasts=True, sync_books=True)
    assert report.added == 3
    with gpod.Database(str(mount)) as ipod_db:
        kinds = {gpod.get_str(t.contents, "title"): t.contents.mediatype for t in ipod_db.tracks()}
        assert kinds == {"Robbers": gpod.MEDIATYPE_PODCAST, "Chocolate": gpod.MEDIATYPE_AUDIOBOOK,
                         "Wasteland": gpod.MEDIATYPE_AUDIO}
        podcasts = ipod_db.podcasts_playlist(create=False)
        assert podcasts and ipod_db.is_podcasts(podcasts)
        assert [gpod.get_str(t.contents, "title") for t in ipod_db.playlist_tracks(podcasts)] \
            == ["Robbers"]
        robbers = next(t.contents for t in ipod_db.tracks()
                       if gpod.get_str(t.contents, "title") == "Robbers")
        assert robbers.remember_playback_position == 1 and robbers.skip_when_shuffling == 1


def png(path, rgb=(200, 30, 30), w=40, h=30):
    import zlib
    raw = b"".join(b"\0" + bytes(rgb) * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_photos_go_one_way_to_the_ipod(tmp_path):
    from luxtunes.core.ipod import photos

    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    pictures = tmp_path / "Pictures"
    (pictures / "Holiday").mkdir(parents=True)
    (pictures / "Pets" / "Cats").mkdir(parents=True)
    png(pictures / "loose.png")
    png(pictures / "Holiday" / "a.png")
    png(pictures / "Holiday" / "b.png", rgb=(0, 0, 255))
    png(pictures / "Pets" / "Cats" / "c.png")
    (pictures / "notes.txt").write_text("not a photo")
    albums = photos.chosen_albums(str(pictures), "all", [])
    assert [(name, len(paths)) for name, paths in albums] == [(None, 1), ("Holiday", 2), ("Pets", 1)]

    report = do_sync(conn, mount, sync_photos=True, photos_folder=str(pictures),
                     full_resolution=True)
    assert report.photos == 4 and not report.problems
    database = photos.database_path(str(mount))
    assert database and database.exists()
    assert (mount / "Photos" / "Full Resolution" / "Holiday" / "b.png").exists()
    db = photos._open_db(str(mount))
    try:
        assert len(gpod.glist(db.contents.photos)) == 4
        names = sorted(ctypes.string_at(ctypes.cast(a, ctypes.POINTER(photos.PhotoAlbum))
                                        .contents.name).decode()
                       for a in gpod.glist(db.contents.photoalbums)[1:])   # [0]: Photo Library
        assert names == ["Holiday", "Pets"]
    finally:
        photos._lib().itdb_photodb_free(db)

    # Only "Pets", no full-resolution copies: the iPod follows; the computer's photos stay.
    do_sync(conn, mount, sync_photos=True, photos_folder=str(pictures), photos_mode="selected",
            photo_folders=["Pets"], full_resolution=False)
    assert not (mount / "Photos" / "Full Resolution").exists()
    # Photo syncing off: LuxTunes' photos come off the iPod.
    do_sync(conn, mount, sync_photos=False)
    db = photos._open_db(str(mount))
    try:
        assert len(gpod.glist(db.contents.photos)) == 0
    finally:
        photos._lib().itdb_photodb_free(db)
    assert (pictures / "Holiday" / "a.png").exists() and (pictures / "loose.png").exists()


def test_photo_orientation_from_exif(tmp_path):
    from luxtunes.core.ipod import photos

    def jpeg_with_orientation(value):
        tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1) \
            + struct.pack(">HHI", 0x0112, 3, 1) + struct.pack(">HH", value, 0) + b"\0\0\0\0"
        app1 = b"Exif\0\0" + tiff
        return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xda"

    for value, degrees in ((1, 0), (3, 180), (6, 270), (8, 90)):
        path = tmp_path / f"{value}.jpg"
        path.write_bytes(jpeg_with_orientation(value))
        assert photos.rotation_of(str(path)) == degrees


def test_phone_and_web_photos_are_converted(tmp_path):
    import shutil
    import subprocess

    from luxtunes.core.ipod import photos

    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    mount = fake_ipod(tmp_path)
    trip = tmp_path / "Pictures" / "Trip"
    trip.mkdir(parents=True)
    png(trip / "a.png")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=s=320x240:d=0.1", "-frames:v", "1", str(trip / "b.webp")], check=True)
    (trip / "c.heic").write_bytes(b"not a real photo")
    albums = photos.chosen_albums(str(tmp_path / "Pictures"), "all", [])
    assert [(name, len(paths)) for name, paths in albums] == [("Trip", 3)]
    added, problems = photos.sync(str(mount), albums, False)
    assert added == 2                                       # the PNG and the WebP
    assert len(problems) == 1 and problems[0].startswith("c.heic")


def test_songs_converted_on_the_way_to_the_ipod(tmp_path):
    import shutil
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    conn.execute("UPDATE tracks SET bit_rate = 320")
    conn.commit()
    originals = {i: (tmp_path / f"{i}.mp3").read_bytes() for i in range(3)}
    report = do_sync(conn, mount, convert=True, convert_kbps=128)
    assert report.added == 3 and not report.problems, report.problems
    with gpod.Database(str(mount)) as ipod_db:
        for t in ipod_db.tracks():
            assert t.contents.bitrate == 128
            assert gpod.get_str(t.contents, "filetype") == "AAC audio file"
            path = ipod_db.file_of(t)
            assert path.endswith(".m4a") and os.path.getsize(path) > 0
    # The library's own files are untouched.
    assert all((tmp_path / f"{i}.mp3").read_bytes() == data for i, data in originals.items())
