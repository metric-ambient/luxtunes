"""iPod support that doesn't need an iPod: finding one, its settings, the
sync plan, and merging plays from the iPod into the library."""

import struct

import pytest

from luxtunes.core import db, library
from luxtunes.core.ipod import device, plan, settings, stats


# ---- A tiny fake iPod ----------------------------------------------------------------

def mhod_string(kind, text):
    raw = text.encode("utf-16-le")
    body = struct.pack("<IIII", 1, len(raw), 0, 0) + raw
    return b"mhod" + struct.pack("<IIIII", 0x18, 0x18 + len(body), kind, 0, 0) + body


def itunesdb(name, songs):
    title = mhod_string(1, name)
    header = 0x6C
    mhyp = b"mhyp" + struct.pack("<IIII", header, header + len(title), 1, 0) + b"\x01"
    mhyp = mhyp.ljust(header, b"\x00") + title
    mhlp = b"mhlp" + struct.pack("<II", 0x5C, 1)
    mhlp = mhlp.ljust(0x5C, b"\x00") + mhyp
    mhlt = (b"mhlt" + struct.pack("<II", 0x5C, songs)).ljust(0x5C, b"\x00")
    sets = b""
    for kind, child in ((1, mhlt), (2, mhlp)):
        sets += (b"mhsd" + struct.pack("<III", 0x60, 0x60 + len(child), kind)).ljust(0x60, b"\x00") + child
    head = (b"mhbd" + struct.pack("<II", 0xBC, 0xBC + len(sets)) + b"\x00" * 8
            + struct.pack("<I", 2)).ljust(0xBC, b"\x00")
    return head + sets


def make_ipod(tmp_path, name="IPOD"):
    mount = tmp_path / "media" / "user" / "IPOD"
    (mount / "iPod_Control" / "iTunes").mkdir(parents=True)
    (mount / "iPod_Control" / "Device").mkdir()
    (mount / "iPod_Control" / "Music" / "F00").mkdir(parents=True)
    (mount / "iPod_Control" / "Music" / "F00" / "ABCD.mp3").write_bytes(b"x" * 1000)
    (mount / "iPod_Control" / "Device" / "SysInfo").write_text(
        "ModelNumStr: xC297\nFirewireGuid: 0x000A270012345678\npszSerialNumber: TEST0000XYZ\n"
        "visibleBuildID: 0x02048000 (2.0.4)\n")
    (mount / "iPod_Control" / "iTunes" / "iTunesDB").write_bytes(itunesdb(name, 1234))
    mounts = tmp_path / "mounts"
    mounts.write_text(f"/dev/sda1 / ext4 rw 0 0\n/dev/sdb2 {str(mount).replace(' ', '\\040')} vfat rw 0 0\n")
    return mount, str(mounts)


def test_find_and_read_ipod(tmp_path):
    mount, mounts = make_ipod(tmp_path)
    [ipod] = device.find_ipods(mounts)
    assert ipod.mountpoint == str(mount) and ipod.device == "/dev/sdb2"
    assert ipod.name == "IPOD" and ipod.song_count == 1234
    assert ipod.model_name == "iPod classic (7th generation)" and ipod.colour == "black"
    assert ipod.serial == "TEST0000XYZ" and ipod.software_version == "2.0.4"
    assert ipod.key == "000A270012345678" and ipod.format_name == "Windows" and ipod.needs_hash
    space = device.space(ipod)
    assert space.media == 1000 and space.capacity > 0
    assert device.format_gb(160_000_000_000 - 248_000_000) == "148.78 GB"


def test_unnamed_or_damaged_database_falls_back_to_folder_name(tmp_path):
    mount, mounts = make_ipod(tmp_path)
    (mount / "iPod_Control" / "iTunes" / "iTunesDB").write_bytes(b"garbage")
    assert device.find_ipods(mounts)[0].name == "IPOD"


# ---- Settings --------------------------------------------------------------------------

def test_settings_are_remembered_per_ipod(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    record = settings.load_device(conn, "GUID1", "IPOD")
    assert record.is_new and record.settings.music_mode == "all"
    record.settings.music_mode = "selected"
    record.settings.playlists = ["ABC"]
    settings.save_settings(conn, record.id, record.settings)
    settings.rename_device(conn, record.id, "Alex's iPod")
    again = settings.load_device(conn, "GUID1", "IPOD")
    assert not again.is_new and again.settings.playlists == ["ABC"]
    assert again.name == "Alex's iPod" and again.pending_name == "Alex's iPod"
    assert settings.SyncSettings.from_json('{"music_mode": "selected", "unknown": 1}').music_mode == "selected"


# ---- The plan --------------------------------------------------------------------------

def library_with_files(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    songs = [("A1", "One", "Band", "Alpha", "Rock", 1), ("A2", "Two", "Band", "Alpha", "Rock", 0),
             ("B1", "Three", "Other", "Beta", "Pop", 1)]
    for pid, name, artist, album, genre, enabled in songs:
        path = tmp_path / f"{pid}.mp3"
        path.write_bytes(b"x" * 100)
        conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, genre, enabled, size, "
                     "location) VALUES (?, ?, ?, ?, ?, ?, 100, ?)",
                     (pid, name, artist, album, genre, enabled, str(path)))
    conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, location, size) "
                 "VALUES ('GONE', 'Missing', 'Band', 'Alpha', '/nowhere.mp3', 100)")
    movie = tmp_path / "m.m4v"
    movie.write_bytes(b"x" * 500)
    conn.execute("INSERT INTO tracks (persistent_id, name, media_kind, location, size, date_added) "
                 "VALUES ('M1', 'Film', 'movie', ?, 500, '2020-01-01T00:00:00Z')", (str(movie),))
    conn.commit()
    playlist = library.create_playlist(conn, "Mix")
    ids = {r[1]: r[0] for r in conn.execute("SELECT id, persistent_id FROM tracks")}
    library.add_to_playlist(conn, playlist, [ids["B1"], ids["A1"]])
    return conn, ids


def test_entire_library_plan(tmp_path):
    conn, ids = library_with_files(tmp_path)
    p = plan.build_plan(conn, settings.SyncSettings(sync_movies=True))
    assert p.track_ids == {ids["A1"], ids["A2"], ids["B1"], ids["M1"]}
    assert [t["persistent_id"] for t in p.missing] == ["GONE"]
    assert p.music_bytes == 300 and p.video_bytes == 500
    [mix] = p.playlists
    assert mix["name"] == "Mix" and mix["track_ids"] == [ids["B1"], ids["A1"]]   # playlist order kept


def test_selected_and_checked_only(tmp_path):
    conn, ids = library_with_files(tmp_path)
    s = settings.SyncSettings(music_mode="selected", genres=["Pop"], sync_movies=False)
    assert plan.build_plan(conn, s).track_ids == {ids["B1"]}
    s = settings.SyncSettings(music_mode="selected", artists=["Band"], sync_movies=False,
                              only_checked=True)
    assert plan.build_plan(conn, s).track_ids == {ids["A1"]}
    s = settings.SyncSettings(music_mode="selected", playlists=[plan.syncable_playlists(conn)[0]["persistent_id"]],
                              sync_movies=False)
    p = plan.build_plan(conn, s)
    assert p.track_ids == {ids["A1"], ids["B1"]} and len(p.playlists) == 1
    s = settings.SyncSettings(sync_music=False, sync_movies=True,
                              movies_rule="1 most recent unwatched")
    assert plan.build_plan(conn, s).track_ids == {ids["M1"]}
    choices = plan.library_choices(conn)
    assert choices.artists == ["Band", "Other"] and choices.genres == ["Pop", "Rock"]
    assert [label for _, label in choices.albums] == ["Band - Alpha", "Other - Beta"]


def test_entire_library_with_only_the_ticked_playlists(tmp_path):
    conn, ids = library_with_files(tmp_path)
    other = library.create_playlist(conn, "Other List")
    library.add_to_playlist(conn, other, [ids["A2"]])
    lists = {p["name"]: p["persistent_id"] for p in plan.syncable_playlists(conn)}
    s = settings.SyncSettings(music_mode="library_playlists", playlists=[lists["Mix"]],
                              sync_movies=False)
    p = plan.build_plan(conn, s)
    assert p.track_ids == {ids["A1"], ids["A2"], ids["B1"]}          # every song
    assert [x["name"] for x in p.playlists] == ["Mix"]               # but only Mix
    s.playlists = []
    assert plan.build_plan(conn, s).playlists == []
    assert settings.SyncSettings().music_mode == "all"                # the default


def test_autofill(tmp_path):
    conn, ids = library_with_files(tmp_path)
    s = settings.SyncSettings(music_mode="selected", genres=["Pop"], autofill=True, sync_movies=False)
    p = plan.build_plan(conn, s, free_space=plan.AUTOFILL_RESERVE + 250, seed=1)
    assert ids["B1"] in p.track_ids and len(p.track_ids) == 2      # room for one more 100-byte song


# ---- Merging plays from the iPod ----------------------------------------------------

def test_merge_adds_and_never_resets(tmp_path):
    conn, ids = library_with_files(tmp_path)
    conn.execute("UPDATE tracks SET play_count = 10, play_date = '2026-01-01T00:00:00Z', "
                 "skip_count = 2, rating = 60 WHERE id = ?", (ids["A1"],))
    conn.commit()
    dbid = int("A1".rjust(16, "0"), 16)
    conn.execute("UPDATE tracks SET persistent_id = ? WHERE id = ?", (stats.persistent_id(dbid), ids["A1"]))
    conn.commit()
    t = stats.IPodTrackStats
    result = stats.merge(conn, [
        t(dbid, recent_plays=3, last_played=1_800_000_000, recent_skips=1, last_skipped=1_000,
          rating=80, rating_at_sync=60),
        t(0xDEADBEEF, recent_plays=99),                    # not in the library: ignored
    ])
    song = library.load_track(conn, ids["A1"])
    assert song["play_count"] == 13 and song["skip_count"] == 3 and song["rating"] == 80
    assert song["play_date"] == "2027-01-15T08:00:00Z"          # moved forward
    assert result.plays == 3 and result.songs == 1
    # An empty / restored iPod (no recent plays, ratings as synced) changes nothing.
    stats.merge(conn, [t(dbid, rating=0, rating_at_sync=0)])
    again = library.load_track(conn, ids["A1"])
    assert (again["play_count"], again["skip_count"], again["rating"]) == (13, 3, 80)
    # An older last-played time never moves Last Played backwards.
    stats.merge(conn, [t(dbid, recent_plays=1, last_played=100, rating=80, rating_at_sync=80)])
    assert library.load_track(conn, ids["A1"])["play_date"] == "2027-01-15T08:00:00Z"


# ---- Mounting ---------------------------------------------------------------------------

LSBLK = """{"blockdevices": [
  {"path": "/dev/sda", "fstype": null, "mountpoint": null, "vendor": "ATA     ", "model": "Disk",
   "type": "disk", "children": [{"path": "/dev/sda1", "fstype": "ext4", "mountpoint": "/"}]},
  {"path": "/dev/sdb", "fstype": null, "mountpoint": null, "vendor": "Apple   ", "model": "iPod",
   "type": "disk", "children": [
     {"path": "/dev/sdb1", "fstype": null, "mountpoint": null},
     {"path": "/dev/sdb2", "fstype": "vfat", "mountpoint": null}]},
  {"path": "/dev/sdc", "fstype": null, "mountpoint": null, "vendor": "Apple   ", "model": "iPod",
   "type": "disk", "children": [{"path": "/dev/sdc2", "fstype": "vfat", "mountpoint": "/media/b/X"}]}
]}"""


def test_finds_unmounted_ipods_only(tmp_path):
    assert device.unmounted_ipod_partitions(LSBLK) == ["/dev/sdb2"]
    assert device.unmounted_ipod_partitions("not json") == []
    usb = tmp_path / "usb"
    (usb / "1-2").mkdir(parents=True)
    (usb / "1-2" / "idVendor").write_text("05ac\n")
    assert device.apple_usb_connected(str(usb))
    (usb / "1-2" / "idVendor").write_text("046d\n")
    assert not device.apple_usb_connected(str(usb))


def test_tv_rules_and_ipod_playable_videos(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    for show, season, ep, played in (("Show A", 1, 1, 1), ("Show A", 1, 2, 0), ("Show A", 1, 3, 0),
                                     ("Show B", 2, 1, 0)):
        path = tmp_path / f"{show}{ep}.m4v"
        path.write_bytes(b"x" * 10)
        conn.execute("INSERT INTO tracks (persistent_id, name, media_kind, show, season, "
                     "episode_number, play_count, location, size) VALUES (?, ?, 'tvshow', ?, ?, ?, ?, ?, 10)",
                     (f"{show[-1]}{ep}", f"Ep {ep}", show, season, ep, played, str(path)))
    mkv = tmp_path / "film.mkv"
    mkv.write_bytes(b"x")
    conn.execute("INSERT INTO tracks (persistent_id, name, media_kind, location, size) "
                 "VALUES ('MKV', 'Film', 'movie', ?, 1)", (str(mkv),))
    conn.commit()

    def chosen(rule):
        s = settings.SyncSettings(sync_music=False, sync_tv=True, tv_rule=rule)
        return sorted(t["persistent_id"] for t in plan.build_plan(conn, s).tracks)

    assert chosen("all") == ["A1", "A2", "A3", "B1"]          # and no .mkv movie
    assert chosen("all unwatched") == ["A2", "A3", "B1"]
    assert chosen("1 newest") == ["A3", "B1"]                  # per show
    assert chosen("1 oldest unwatched") == ["A2", "B1"]
    off = settings.SyncSettings(sync_music=False, sync_tv=False)
    assert plan.build_plan(conn, off).count("tvshow") == 0
    assert all(m["persistent_id"] != "MKV" for m in plan.library_choices(conn).movies)


def test_finds_the_programs_keeping_an_ipod_busy(tmp_path):
    mount = tmp_path / "media" / "IPOD"
    (mount / "iPod_Control").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    proc = tmp_path / "proc"

    def process(pid, name, cwd, fds=(), maps=""):
        base = proc / str(pid)
        (base / "fd").mkdir(parents=True)
        (base / "comm").write_text(name + "\n")
        (base / "cwd").symlink_to(cwd)
        for n, target in enumerate(fds):
            (base / "fd" / str(n)).symlink_to(target)
        (base / "maps").write_text(maps)

    process(10, "nemo", tmp_path / "home", fds=[tmp_path / "home", mount / "iPod_Control"])
    process(11, "bash", mount)                                    # a terminal in the iPod
    process(12, "player", tmp_path / "home",
            maps=f"7f00-7f10 r--s 00000000 08:21 123   {mount}/Song One.mp3\n")
    process(13, "other", tmp_path / "home", fds=[tmp_path / "media" / "IPOD2"])
    (proc / "self").mkdir()                                       # not a process
    found = {(pid, name) for pid, name, _ in device.users_of(str(mount), str(proc))}
    assert found == {(10, "nemo"), (11, "bash"), (12, "player")}
    assert device.is_busy_error("org.freedesktop.UDisks2.Error.DeviceBusy: target is busy")
    assert not device.is_busy_error("Not authorized") and not device.is_busy_error(None)


def test_podcasts_and_audiobooks_in_the_plan(tmp_path):
    conn, ids = library_with_files(tmp_path)
    rows = [("P1", "Ep 1", "Show A", "podcast", "2026-01-01", 2), ("P2", "Ep 2", "Show A", "podcast",
            "2026-02-01", 0), ("P3", "Ep 3", "Show A", "podcast", "2026-03-01", 0),
            ("P4", "Pilot", "Show B", "podcast", "2025-01-01", 0),
            ("K1", "Part 1", "Big Book", "audiobook", None, 0),
            ("K2", "Part 2", "Big Book", "audiobook", None, 0),
            ("K3", "Tiny Story", None, "audiobook", None, 0)]
    for pid, name, album, kind, added, plays in rows:
        path = tmp_path / f"{pid}.m4a"
        path.write_bytes(b"x" * 10)
        conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, media_kind, "
                     "date_added, play_count, location, size) VALUES (?, ?, 'Writer', ?, ?, ?, ?, "
                     "?, 10)", (pid, name, album, kind, added, plays, str(path)))
    conn.commit()
    ids = {r[1]: r[0] for r in conn.execute("SELECT id, persistent_id FROM tracks")}

    def chosen(**options):
        s = settings.SyncSettings(**{"sync_music": False, "sync_podcasts": True,
                                     "sync_books": True, **options})
        return {pid for pid, i in ids.items() if i in plan.build_plan(conn, s).track_ids}

    books = {"K1", "K2", "K3"}
    assert chosen() == {"P2", "P3", "P4"} | books                 # default: all unplayed + books
    assert chosen(podcasts_rule="1 most recent", sync_books=False) == {"P3", "P4"}
    assert chosen(podcasts_rule="1 least recent unplayed", sync_books=False) == {"P2", "P4"}
    assert chosen(podcasts_scope="selected podcasts", podcast_shows=["Show B"],
                  podcast_episodes=["P1"], sync_books=False) == {"P4", "P1"}
    big_book = plan.book_key({"artist": "Writer", "album": "Big Book"})
    assert chosen(sync_podcasts=False, books_mode="selected", books=[big_book]) == {"K1", "K2"}
    p = plan.build_plan(conn, settings.SyncSettings(sync_movies=False))
    assert p.video_bytes == 0                                     # podcasts and books are audio

    choices = plan.library_choices(conn)
    assert choices.shows == ["Show A", "Show B"]
    assert [e["persistent_id"] for e in choices.episodes][:2] == ["P3", "P2"]   # newest first
    assert [label for _, label in choices.books] == ["Big Book — Writer", "Tiny Story — Writer"]


def test_old_settings_still_load():
    s = settings.SyncSettings.from_json('{"sync_contacts": true, "books_mode": "all"}')
    assert s.podcasts_scope == "all podcasts" and s.books == []


def test_which_songs_are_converted_for_the_ipod():
    s = settings.SyncSettings()
    flac = {"location": "/m/a.flac", "bit_rate": 900, "total_time": 60_000, "size": 6_000_000}
    mp3 = {"location": "/m/b.mp3", "bit_rate": 320, "total_time": 60_000, "size": 2_400_000}
    alac = {"location": "/m/c.m4a", "kind": "Apple Lossless audio file", "bit_rate": 800}
    protected = {"location": "/m/d.m4p", "bit_rate": 320}
    movie = {"location": "/m/e.mp4", "media_kind": "movie", "bit_rate": 2000}
    # Off: only what the iPod can't play at all (FLAC) is converted.
    assert [plan.transfer_kbps(t, s) for t in (flac, mp3, alac, protected, movie)] == \
        [256, None, None, None, None]
    s.convert, s.convert_kbps = True, 128
    assert [plan.transfer_kbps(t, s) for t in (flac, mp3, alac, protected, movie)] == \
        [128, 128, 128, None, None]
    assert plan.transfer_size(mp3, s) == 960_000              # 60 s at 128 kbps
    assert plan.transfer_kbps({"location": "/m/f.mp3", "bit_rate": 128}, s) is None


def test_a_new_device_syncs_only_music():
    s = settings.SyncSettings()
    assert s.sync_music
    assert not (s.sync_movies or s.sync_tv or s.sync_podcasts or s.sync_books)
    # A saved device keeps its own choices.
    saved = settings.SyncSettings.from_json('{"sync_movies": true}')
    assert saved.sync_movies and not saved.sync_tv


# ---- Models and what they can do -------------------------------------------------------

def test_models_are_told_apart_by_usb_product_without_libgpod():
    from luxtunes.core.ipod import models
    nano1 = models.identify("", "120a")
    assert nano1.name == "iPod nano (1st generation)"
    assert nano1.family.photos and not nano1.family.video and not nano1.family.unsupported
    assert models.identify("", "1262").family.signature == models.HASH58      # nano 3G
    assert models.identify("", "1266").family.unsupported                       # nano 6G
    assert models.identify("", "1300").family.name == "iPod shuffle (1st generation)"
    assert not models.identify("", "1300").family.photos
    unknown = models.identify("", "")
    assert unknown.name == "iPod" and unknown.family.video                      # as before
    # Built in for the classics, so they're named even without libgpod.
    classic = models.identify("xC297", "")
    assert classic.name == "iPod classic (7th generation)" and classic.capacity_label == "160 GB"
    assert models.stand_in_model_number("120a") is None          # no signature: nothing needed


def test_libgpod_knows_every_model_number():
    from luxtunes.core.ipod import gpod, models
    if not gpod.available():
        pytest.skip("libgpod isn't installed")
    models.model_table.cache_clear()
    nano = models.identify("MA004", "")
    assert nano.name == "iPod nano (1st generation)" and nano.capacity_label == "2 GB"
    assert nano.colour == "white"
    assert models.identify("M9282", "").name == "iPod (4th generation)"
    assert models.identify("MA079", "").family.photos                           # iPod photo
    # A nano 3G whose SysInfo doesn't say its model still gets signed.
    stand_in = models.stand_in_model_number("1262")
    assert stand_in and models.identify(stand_in, "").family_key == "nano_3"


def test_an_ipod_without_video_gets_no_movies(tmp_path):
    conn, ids = library_with_files(tmp_path)
    mix = conn.execute("SELECT id FROM playlists WHERE name = 'Mix'").fetchone()[0]
    library.add_to_playlist(conn, mix, [ids["M1"]])
    s = settings.SyncSettings(sync_movies=True, music_mode="selected",
                              playlists=[plan.syncable_playlists(conn)[0]["persistent_id"]])
    assert ids["M1"] in plan.build_plan(conn, s).track_ids
    assert ids["M1"] not in plan.build_plan(conn, s, video=False).track_ids   # not even via a playlist


def test_read_ipod_knows_the_model_and_its_abilities(tmp_path):
    mount, mounts = make_ipod(tmp_path)
    (mount / "iPod_Control" / "Device" / "SysInfo").write_text("pszSerialNumber: X\n")
    [ipod] = device.find_ipods(mounts)
    assert ipod.model_name == "iPod" and ipod.supports_video          # unknown: as before
    from luxtunes.core.ipod import models
    ipod.identity = models.identify("", "120a")
    assert not ipod.supports_video and ipod.supports_photos and not ipod.needs_hash
    ipod.identity = models.identify("", "1263")
    assert ipod.needs_hash


def test_an_ipod_that_doesnt_say_its_model_gets_its_size_from_its_disk():
    from luxtunes.core.ipod import models
    gib = 1024 ** 3
    assert models.size_from_disk("nano_1", int(1.9 * gib)) == 2           # your 2 GB nano
    assert models.size_from_disk("nano_1", int(3.8 * gib)) == 4
    assert models.size_from_disk("classic", int(149 * gib)) == 160
    assert models.size_from_disk("shuffle_1", int(0.48 * gib)) == 0.5
    assert models.size_from_disk("unknown", 10 * gib) is None
    nano = models.Identity("nano_1", models.FAMILIES["nano_1"], 2)
    assert nano.capacity_label == "2 GB"
    assert models.Identity("shuffle_1", models.FAMILIES["shuffle_1"], 0.5).capacity_label == "512 MB"
