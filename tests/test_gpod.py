"""iPod sync with the real libgpod, on a fake iPod made in a temporary folder.

Skipped where libgpod isn't installed (sudo apt install libgpod-common)."""

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
