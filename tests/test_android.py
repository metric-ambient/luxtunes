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
        assert info["format_version"] == "2" and info["library_id"]
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


# ---- Format v2: smart rules out, playlists back --------------------------------------------

def _db(root):
    return sqlite3.connect(root / "library.db")


def _changes(root, conn, playlists=(), tracks=(), library=None):
    lid = library if library is not None else sync.library_id(conn)
    (root / "changes.json").write_text(json.dumps(
        {"version": 2, "library_id": lid, "tracks": list(tracks), "playlists": list(playlists)}))


def _pid(conn, name):
    return conn.execute("SELECT persistent_id FROM tracks WHERE name = ?", (name,)).fetchone()[0]


def _list(conn, name):
    row = conn.execute("SELECT id FROM playlists WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    return [t["name"] for t in library.load_tracks(conn, playlist_id=row[0])]


def _phone_root(tmp_path):
    return tmp_path / "phone" / "Internal shared storage" / "LuxTunes"


def test_smart_rules_and_rule_columns_reach_the_phone_unchanged(tmp_path):
    from luxtunes.core import smart
    conn = small_library(tmp_path)
    mix = conn.execute("SELECT persistent_id FROM playlists WHERE name = 'Mix'").fetchone()[0]
    rules = smart.SmartPlaylist(smart.Info(), smart.Group(False, [
        smart.Rule(0x28, smart.IS, mix), smart.Rule(0x23, smart.GREATER, 0)]))
    smart_id = smart.create(conn, "In Mix", rules)
    stored = conn.execute("SELECT persistent_id, smart_info, smart_criteria FROM playlists "
                          "WHERE id = ?", (smart_id,)).fetchone()
    phone = fake_phone(tmp_path)
    run(conn, phone, music_mode="selected", playlists=[stored[0]], artists=["The 1975"],
        albums=[], genres=[])
    with _db(_phone_root(tmp_path)) as out:
        info, criteria = out.execute("SELECT smart_info, smart_criteria FROM playlists "
                                     "WHERE id = ?", (stored[0],)).fetchone()
        assert (info, criteria) == (stored[1], stored[2])                  # byte for byte
        columns = {r[1] for r in out.execute("PRAGMA table_info(tracks)")}
        assert {"bpm", "kind", "date_modified", "purchased"} <= columns
        # "Mix" isn't synced, but the rule needs its songs: sent for reference only.
        assert out.execute("SELECT name FROM reference_playlists").fetchall() == [("Mix",)]
        assert out.execute("SELECT COUNT(*) FROM reference_items").fetchone()[0] == 2
        assert out.execute("SELECT COUNT(*) FROM playlists WHERE name = 'Mix'").fetchone()[0] == 0


def test_playlists_made_changed_and_deleted_on_the_phone(tmp_path):
    from luxtunes.core import smart
    conn = small_library(tmp_path)
    phone = fake_phone(tmp_path)
    root = _phone_root(tmp_path)
    other = smart.create(conn, "Loved", smart.SmartPlaylist(smart.Info(), smart.Group(False, [
        smart.Rule(0x19, smart.GREATER, 80)])))
    old = library.create_playlist(conn, "Old")
    library.add_to_playlist(conn, old, [1])
    pids = {r["name"]: r["persistent_id"] for r in library.load_playlists(conn)}
    choose = dict(music_mode="selected", playlists=[pids["Mix"], pids["Loved"], pids["Old"]],
                  artists=[], albums=[], genres=[])
    run(conn, phone, **choose)
    new_rules = smart.encode(smart.SmartPlaylist(smart.Info(), smart.Group(False, [
        smart.Rule(0x19, smart.GREATER, 40)])))
    import base64
    b64 = lambda b: base64.b64encode(b).decode()
    _changes(root, conn, playlists=[
        {"id": "9F8E7D6C5B4A3921", "action": "create", "name": "Road Trip", "is_smart": 0,
         "items": [_pid(conn, "Chocolate"), _pid(conn, "Robbers"), "00FF00FF00FF00FF"]},
        {"id": pids["Mix"], "action": "update", "name": "Mix 2", "is_smart": 0,
         "items": [_pid(conn, "Robbers")]},
        {"id": pids["Loved"], "action": "update", "name": "Liked", "is_smart": 1,
         "smart_info": b64(new_rules[0]), "smart_criteria": b64(new_rules[1])},
        {"id": pids["Old"], "action": "delete"}],
        tracks=[{"id": _pid(conn, "Robbers"), "plays": 1}])
    asked = []
    record = settings.load_device(conn, phone.key, phone.name)
    for key, value in choose.items():
        setattr(record.settings, key, value)
    report = sync.sync(conn, phone, record, confirm_playlists=lambda names: asked.extend(names)
                       or True)
    assert asked == ["Old"] and _list(conn, "Old") is None
    assert _list(conn, "Road Trip") == ["Chocolate", "Robbers"]            # unknown id ignored
    assert conn.execute("SELECT persistent_id FROM playlists WHERE name = 'Road Trip'"
                        ).fetchone()[0] == "9F8E7D6C5B4A3921"
    assert _list(conn, "Mix 2") == ["Robbers"]
    assert sorted(_list(conn, "Liked")) == ["Chocolate", "Robbers", "Wasteland"]   # rated 60
    assert report.phone_playlists.created == ["Road Trip"]
    # The new playlist joined this phone's selection, so it went straight back.
    assert "9F8E7D6C5B4A3921" in settings.load_device(conn, phone.key).settings.playlists
    with _db(root) as out:
        assert out.execute("SELECT name FROM playlists WHERE id = '9F8E7D6C5B4A3921'"
                           ).fetchone() == ("Road Trip",)
    assert json.loads((root / "changes.json").read_text()) == \
        {"version": 2, "tracks": [], "playlists": []}
    assert conn.execute("SELECT play_count FROM tracks WHERE name = 'Robbers'").fetchone()[0] == 1


def test_songs_the_phone_couldnt_see_stay_in_a_playlist_it_edited(tmp_path):
    conn = small_library(tmp_path)
    phone = fake_phone(tmp_path)
    root = _phone_root(tmp_path)
    mix = conn.execute("SELECT id, persistent_id FROM playlists WHERE name = 'Mix'").fetchone()
    (tmp_path / "1.mp3").unlink()                     # Chocolate can't go to the phone
    library.add_to_playlist(conn, mix[0], [2])        # Mix: Wasteland, Robbers, Chocolate
    run(conn, phone)
    _changes(root, conn, playlists=[{"id": mix[1], "action": "update", "name": "Mix",
                                     "is_smart": 0, "items": [_pid(conn, "Robbers")]}])
    run(conn, phone)
    assert _list(conn, "Mix") == ["Robbers", "Chocolate"]   # Wasteland removed on the phone


def test_playlist_changes_need_the_same_library_and_a_yes_to_delete(tmp_path):
    conn = small_library(tmp_path)
    phone = fake_phone(tmp_path)
    root = _phone_root(tmp_path)
    run(conn, phone)
    mix = conn.execute("SELECT persistent_id FROM playlists WHERE name = 'Mix'").fetchone()[0]
    _changes(root, conn, library="FFFFFFFFFFFFFFFF",
             playlists=[{"id": mix, "action": "delete"}],
             tracks=[{"id": _pid(conn, "Robbers"), "plays": 2}])
    report = run(conn, phone)
    assert _list(conn, "Mix") is not None
    assert any("different LuxTunes library" in p for p in report.problems)
    assert conn.execute("SELECT play_count FROM tracks WHERE name = 'Robbers'").fetchone()[0] == 2
    # Same library, but you say no: kept, and it goes back to the phone.
    _changes(root, conn, playlists=[{"id": mix, "action": "delete"}])
    record = settings.load_device(conn, phone.key, phone.name)
    report = sync.sync(conn, phone, record, confirm_playlists=lambda names: False)
    assert _list(conn, "Mix") is not None and report.phone_playlists.kept == ["Mix"]
    assert (root / "Playlists" / "Mix.m3u8").exists()


def test_the_phone_cant_touch_what_it_wasnt_sent_or_send_nonsense(tmp_path):
    conn = small_library(tmp_path)
    phone = fake_phone(tmp_path)
    root = _phone_root(tmp_path)
    folder = library.create_folder(conn)
    folder_pid = conn.execute("SELECT persistent_id FROM playlists WHERE id = ?",
                              (folder,)).fetchone()[0]
    master = conn.execute("SELECT persistent_id FROM playlists WHERE is_master").fetchone()
    run(conn, phone)
    entries = [{"id": folder_pid, "action": "delete"},
               {"id": "nonsense", "action": "create", "name": "x"},
               {"id": "ABABABABABABABAB", "action": "create", "name": "Bad", "is_smart": 1,
                "smart_info": "!!!", "smart_criteria": "AAAA"}]
    if master:
        entries.append({"id": master[0], "action": "delete"})
    _changes(root, conn, playlists=entries)
    report = run(conn, phone)
    assert conn.execute("SELECT 1 FROM playlists WHERE id = ?", (folder,)).fetchone()
    assert _list(conn, "Bad") is None
    assert len(report.problems) >= 3


def test_version_1_files_still_work_and_new_plays_arent_lost(tmp_path):
    from luxtunes.core.android import changes
    conn = small_library(tmp_path)
    phone = fake_phone(tmp_path)
    root = _phone_root(tmp_path)
    run(conn, phone)
    robbers = _pid(conn, "Robbers")
    (root / "changes.json").write_text(json.dumps(
        {"version": 1, "tracks": [{"id": robbers, "plays": 2}]}))
    run(conn, phone)
    assert conn.execute("SELECT play_count FROM tracks WHERE name = 'Robbers'").fetchone()[0] == 2
    assert json.loads((root / "changes.json").read_text()) == \
        {"version": 2, "tracks": [], "playlists": []}
    # The phone wrote again between reading and emptying: only the new plays stay.
    merged = changes.PhoneChanges(text="old", raw_tracks=[{"id": robbers, "plays": 2}])
    later = json.dumps({"version": 2, "tracks": [{"id": robbers, "plays": 3}]})
    left = changes.emptied(merged, later)
    assert left["tracks"] == [{"id": robbers, "plays": 1, "skips": 0}]
