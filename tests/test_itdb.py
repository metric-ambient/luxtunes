"""LuxTunes' own iPod engine (core/ipod/itdb): reading and writing the song
list and album artwork, without libgpod, on a fake iPod in a temporary
folder. tests/test_gpod.py checks the same files against libgpod itself."""

import struct
import zlib
from pathlib import Path

import pytest

pytest.importorskip("mutagen")

from luxtunes.core import db, library, tags
from luxtunes.core.ipod import device, settings, sync
from luxtunes.core.ipod.itdb import artwork, hash58, itunesdb
from luxtunes.core.ipod.itdb.binary import rd16, rd32
from luxtunes.core.ipod.itdb.database import Database, find_on_ipod

FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413
MAC_EPOCH = 2082844800
GUID = "000A27001387CE50"


def png(rgb=(200, 30, 30), w=40, h=30) -> bytes:
    """A small one-colour PNG, made by hand (no image library needed)."""
    def block(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + block(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + block(b"IDAT", zlib.compress(rows)) + block(b"IEND", b""))


def can_decode() -> bool:
    return artwork.decode(png()) is not None


# ---- The song list ------------------------------------------------------------------------

def sample_db():
    database = itunesdb.ITunesDB.new_empty("Test iPod")
    for title, artist, album in (("Paper Lanterns", "The Example Band", "First Light"),
                                 ("Harbor Lights", "The Example Band", "First Light"),
                                 ("Quiet Field", "Ann Placeholder", "Open Country")):
        t = itunesdb.Track(dbid=database.new_dbid())
        t.title, t.artist, t.album = title, artist, album
        t.ipod_path = ":iPod_Control:Music:F00:ABCD.mp3"
        t.tracklen, t.size, t.samplerate = 180_000, 4_000_000, 44100
        t.time_added = 1_700_000_000
        database.tracks.append(t)
    mix = itunesdb.Playlist("Road Mix")
    mix.items = [database.tracks[2], database.tracks[0]]
    database.playlists.append(mix)
    return database


def test_round_trip_keeps_songs_playlists_and_fields():
    database = sample_db()
    data = database.serialize()
    assert rd32(data, 8) == len(data)
    again = itunesdb.ITunesDB.parse(data)
    assert [t.title for t in again.tracks] == ["Paper Lanterns", "Harbor Lights", "Quiet Field"]
    t = again.tracks[0]
    assert (t.artist, t.album, t.tracklen, t.size, t.samplerate, t.time_added) == \
        ("The Example Band", "First Light", 180_000, 4_000_000, 44100, 1_700_000_000)
    assert again.master.name == "Test iPod" and len(again.master.items) == 3
    assert again.playlists[1].name == "Road Mix"
    assert [x.title for x in again.playlists[1].items] == ["Quiet Field", "Paper Lanterns"]
    assert len(again.serialize()) == len(data)              # (only random ids differ)
    # Songs on the same album share an album id; ids start at 52, as libgpod's.
    assert [x.id for x in again.tracks] == [52, 53, 54]
    assert again.tracks[0].album_id == again.tracks[1].album_id != again.tracks[2].album_id


def test_fields_iTunes_wrote_are_kept():
    database = sample_db()
    database.tracks[0].hdr[0x1F0] = 0x5A                     # something LuxTunes doesn't know
    database.tracks[0].raw_mhods.append(itunesdb.string_mhod(7, "#!#3#!#"))   # an EQ setting
    again = itunesdb.ITunesDB.parse(database.serialize())
    assert again.tracks[0].hdr[0x1F0] == 0x5A
    assert again.tracks[0].raw_mhods == database.tracks[0].raw_mhods


def test_numbers_are_kept_in_range():
    t = itunesdb.Track()
    t.volume = -300
    assert t.volume == -300
    t.rating = 300
    assert t.rating == 255
    t.BPM = -5
    assert t.BPM == -5
    t.time_played = 0
    assert t.time_played == 0 and t.hdr[0x58:0x5C] == bytes(4)


def test_podcasts_are_grouped_by_show_for_the_ipod():
    database = sample_db()
    for t in database.tracks[:2]:
        t.mediatype = itunesdb.MEDIATYPE_PODCAST
    podcasts = itunesdb.Playlist("Podcasts")
    podcasts.set_podcasts()
    podcasts.items = database.tracks[:2]
    database.playlists.insert(1, podcasts)
    data = database.serialize()
    again = itunesdb.ITunesDB.parse(data)
    assert again.playlists[1].is_podcasts
    assert [t.title for t in again.playlists[1].items] == ["Paper Lanterns", "Harbor Lights"]
    # Section 3 holds a heading (0x100) for the show, then its episodes under it.
    at = rd32(data, 4)
    while rd32(data, at + 0x0C) != 3:
        at += rd32(data, at + 8)
    section = data[at:at + rd32(data, at + 8)]
    flags = [rd32(section, i + 0x10) for i in range(len(section) - 4)
             if section[i:i + 4] == b"mhip"]
    assert flags.count(0x100) == 1


def test_library_indexes_sort_songs_for_the_ipod_menus():
    database = sample_db()
    indexes = {kind: order for kind, order, _ in database._library_indices()}
    titles = [database.tracks[i].title for i in indexes[0x03]]
    assert titles == ["Harbor Lights", "Paper Lanterns", "Quiet Field"]
    artists = [database.tracks[i].artist for i in indexes[0x05]]
    assert artists == ["Ann Placeholder", "The Example Band", "The Example Band"]


def test_damaged_song_lists_are_refused():
    data = bytearray(sample_db().serialize())
    with pytest.raises(itunesdb.Corrupt):
        itunesdb.ITunesDB.parse(bytes(data[:500]))
    data[rd32(data, 4) + 8] = 0xFF                          # a section longer than the file
    with pytest.raises(itunesdb.Corrupt):
        itunesdb.ITunesDB.parse(bytes(data))
    with pytest.raises(itunesdb.Corrupt):
        itunesdb.ITunesDB.parse(b"not an ipod database")


def test_hash58_signature():
    data = b"mhbd" + bytes(i % 251 for i in range(1020))
    signed = hash58.sign(data, hash58.parse_guid("0x" + GUID))
    assert rd16(signed, 0x30) == 1
    assert signed[0x18:0x20] == data[0x18:0x20] and signed[0x32:0x46] == data[0x32:0x46]
    # This implementation gives libgpod's signature byte for byte (checked against a song
    # list libgpod 0.8.3 wrote; tests/test_gpod.py checks it against libgpod live).
    assert signed[0x58:0x6C].hex() == "e1628fa2926a2261cc21759626a62c66852fe15c"
    assert signed[0x6C:] == data[0x6C:]


def test_play_counts_are_added_as_libgpod_does():
    database = sample_db()
    database.tracks[0].playcount = 4
    counts = bytearray(b"mhdp" + struct.pack("<III", 0x60, 0x1C, 3)).ljust(0x60, b"\0")
    for plays, rating in ((2, 80), (0, 0xFFFFFFFF), (1, 20)):
        counts += struct.pack("<IIIIIII", plays, 1_800_000_000 + MAC_EPOCH if plays else 0, 0,
                              rating, 0, 0, 0)
    assert itunesdb.merge_play_counts(database, bytes(counts)) == 3
    first, second, third = database.tracks
    assert (first.playcount, first.recent_playcount, first.rating) == (6, 2, 80)
    assert first.time_played == 1_800_000_000
    assert (second.recent_playcount, second.rating) == (0, 0)
    assert (third.playcount, third.rating) == (1, 20)


def test_on_the_go_playlists():
    database = sample_db()
    otg = b"mhpo" + struct.pack("<III", 0x14, 4, 2) + b"\0" * 4 + struct.pack("<II", 2, 0)
    assert [t.title for t in itunesdb.parse_otg(database, otg)] == ["Quiet Field",
                                                                    "Paper Lanterns"]


# ---- Covers --------------------------------------------------------------------------------

def test_artwork_database_round_trip():
    art = artwork.ArtworkDB()
    art.images.append(artwork.Image(bytearray(artwork._new_mhii_header()), 100, 7, 1234,
                                    [artwork.Thumb(1055, ":F1055_1.ithmb", 0, 32768, 0, 0,
                                                   128, 128)]))
    art.files.append((1055, 32768))
    again = artwork.ArtworkDB.parse(art.serialize())
    assert again.images[0].thumbs[0] == art.images[0].thumbs[0]
    assert (again.images[0].id, again.images[0].song_id, again.files) == (100, 7, [(1055, 32768)])
    assert rd32(art.serialize(), 0x1C) == 100              # the last id used


def test_pictures_are_packed_as_rgb565():
    if not can_decode():
        pytest.skip("no image decoder")
    fmt = artwork.ArtFormat(1061, 55, 55, 4)
    pixels = artwork.pack(artwork.decode(png((200, 30, 30), 80, 60)), fmt)
    assert len(pixels) == fmt.frame_size == 112 * 55        # rows padded to 4 bytes
    back = artwork.unpack(pixels, fmt)
    r, g, b = (int(v) for v in back[27, 27])
    assert abs(r - 200) < 9 and abs(g - 30) < 9 and abs(b - 30) < 9


# ---- The whole iPod -------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def backups_here(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))   # (where the song list is backed up)


def fake_ipod(tmp_path, model="xB147"):
    mount = tmp_path / "IPOD"
    (mount / "iPod_Control" / "Device").mkdir(parents=True)
    (mount / "iPod_Control" / "Device" / "SysInfo").write_text(
        f"ModelNumStr: {model}\nFirewireGuid: 0x{GUID}\n")
    return mount


def small_library(tmp_path, with_art=False):
    conn = db.connect(tmp_path / "library.db")
    for i, name in enumerate(("Paper Lanterns", "Harbor Lights", "Quiet Field")):
        path = tmp_path / f"{i}.mp3"
        path.write_bytes(FRAME * 100)
        tags.write_tags(str(path), {"name": name, "artist": "The Example Band"})
        if with_art:
            from mutagen.id3 import APIC, ID3
            id3 = ID3(str(path))
            id3.add(APIC(encoding=3, mime="image/png", type=3, desc="",
                         data=png((20, 40, 200) if i < 2 else (240, 200, 10))))
            id3.save(str(path))
        conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, location, size, "
                     "kind, total_time, play_count, rating) VALUES (?, ?, 'The Example Band', ?, "
                     "?, ?, 'MPEG audio file', 2600, ?, 60)",
                     (f"ABCDEF00000000{i:02d}", name, "First Light" if i < 2 else "Open Country",
                      str(path), path.stat().st_size, i))
    conn.commit()
    library.add_to_playlist(conn, library.create_playlist(conn, "Road Mix"), [3, 1])
    settings.set_new_engine(conn, True)
    return conn


def do_sync(conn, mount, **change):
    ipod = device.read_ipod(str(mount), "", "vfat")
    record = settings.load_device(conn, ipod.key, ipod.name)
    for key, value in change.items():
        setattr(record.settings, key, value)
    return sync.sync(conn, ipod, record)


def open_ipod(mount):
    return Database(str(mount), "classic_1", GUID, "hash58")


def test_sync_copies_signs_and_reads_back(tmp_path):
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    report = do_sync(conn, mount)
    assert (report.added, report.removed, report.playlists) == (3, 0, 1) and not report.problems
    data = (mount / "iPod_Control/iTunes/iTunesDB").read_bytes()
    assert rd16(data, 0x30) == 1
    assert hash58.sign(data, hash58.parse_guid(GUID)) == data          # the right signature
    with open_ipod(mount) as ipod_db:
        assert sorted(t.title for t in ipod_db.tracks()) == ["Harbor Lights", "Paper Lanterns",
                                                            "Quiet Field"]
        for t in ipod_db.tracks():
            assert t.size and (mount / t.ipod_path.lstrip(":").replace(":", "/")).exists()
            assert t.filetype_marker == int.from_bytes(b"MP3 ", "big")
        names = [p.name for p in ipod_db.playlists()]
        assert "Road Mix" in names
    do_sync(conn, mount)
    backups = list((tmp_path / "data" / "luxtunes" / "iPod backups").glob("*/*"))
    assert len(backups) == 1                                  # a way back, before each sync
    assert (backups[0] / "iTunesDB").read_bytes() == data


def test_plays_on_ipod_are_added_never_reset(tmp_path):
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    do_sync(conn, mount)
    with open_ipod(mount) as ipod_db:
        order = [t.title for t in ipod_db.tracks()]
    plays = {"Paper Lanterns": 3, "Harbor Lights": 0, "Quiet Field": 1}
    counts = bytearray(b"mhdp" + struct.pack("<III", 0x60, 0x1C, len(order))).ljust(0x60, b"\0")
    for title in order:
        rating = 100 if title == "Harbor Lights" else 60
        counts += struct.pack("<IIIIIII", plays[title],
                              1_800_000_000 + MAC_EPOCH if plays[title] else 0, 0, rating, 0, 0, 0)
    (mount / "iPod_Control/iTunes/Play Counts").write_bytes(bytes(counts))
    do_sync(conn, mount)
    got = {r["name"]: (r["play_count"], r["rating"]) for r in conn.execute("SELECT * FROM tracks")}
    assert got == {"Paper Lanterns": (3, 60), "Harbor Lights": (1, 100), "Quiet Field": (3, 60)}
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
    with open_ipod(mount) as ipod_db:
        assert ipod_db.tracks() == []


def test_podcasts_and_audiobooks_land_in_their_ipod_menus(tmp_path):
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    library.update_track_fields(conn, 1, {"media_kind": "podcast"})
    library.update_track_fields(conn, 2, {"media_kind": "audiobook"})
    assert do_sync(conn, mount, sync_podcasts=True, sync_books=True).added == 3
    with open_ipod(mount) as ipod_db:
        kinds = {t.title: t.mediatype for t in ipod_db.tracks()}
        assert kinds == {"Paper Lanterns": itunesdb.MEDIATYPE_PODCAST,
                         "Harbor Lights": itunesdb.MEDIATYPE_AUDIOBOOK,
                         "Quiet Field": itunesdb.MEDIATYPE_AUDIO}
        podcasts = ipod_db.podcasts_playlist(create=False)
        assert [t.title for t in ipod_db.playlist_tracks(podcasts)] == ["Paper Lanterns"]


def test_covers_are_shared_by_an_album_and_kept_between_syncs(tmp_path):
    if not can_decode():
        pytest.skip("no image decoder")
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path, with_art=True)
    do_sync(conn, mount)
    art_folder = mount / "iPod_Control/Artwork"
    sizes = {p.name: p.stat().st_size for p in art_folder.glob("*.ithmb")}
    per_picture = {f.id: f.frame_size for f in artwork.formats_for("classic_1")}
    # Two pictures (the album's, written once, and the other song's), each in every size.
    assert sizes == {f"F{i}_1.ithmb": 2 * n for i, n in per_picture.items()}
    with open_ipod(mount) as ipod_db:
        by_title = {t.title: t for t in ipod_db.tracks()}
        assert all(ipod_db.has_artwork(t) for t in by_title.values())
        assert ipod_db.artwork_formats(by_title["Quiet Field"]) == set(per_picture)
        first, second = by_title["Paper Lanterns"], by_title["Harbor Lights"]
        assert first.mhii_link != second.mhii_link                  # own image records…
        a, b = ipod_db._image_of(first), ipod_db._image_of(second)
        assert [(t.file, t.offset) for t in a.thumbs] == [(t.file, t.offset) for t in b.thumbs]
    do_sync(conn, mount)                                             # …and nothing redone
    assert {p.name: p.stat().st_size for p in art_folder.glob("*.ithmb")} == sizes


def test_a_cover_comes_back_from_the_ipod(tmp_path):
    if not can_decode():
        pytest.skip("no image decoder")
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path, with_art=True)
    do_sync(conn, mount)
    with open_ipod(mount) as ipod_db:
        t = next(t for t in ipod_db.tracks() if t.title == "Quiet Field")
        thumb = next(x for x in ipod_db._image_of(t).thumbs if x.format_id == 1055)
        pixels = artwork.read_thumb(str(mount / "iPod_Control/Artwork"), thumb)
        r, g, b = (int(v) for v in artwork.unpack(pixels, artwork.ArtFormat(1055, 128, 128))[64, 64])
        assert abs(r - 240) < 9 and abs(g - 200) < 9 and b < 20


def test_a_damaged_artwork_list_is_left_alone(tmp_path):
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path, with_art=can_decode())
    (mount / "iPod_Control/Artwork").mkdir(parents=True)
    (mount / "iPod_Control/Artwork/ArtworkDB").write_bytes(b"mhfd" + b"\xff" * 40)
    do_sync(conn, mount)
    assert (mount / "iPod_Control/Artwork/ArtworkDB").read_bytes() == b"mhfd" + b"\xff" * 40


def test_a_song_list_that_wont_read_is_never_overwritten(tmp_path):
    mount, conn = fake_ipod(tmp_path), small_library(tmp_path)
    (mount / "iPod_Control/iTunes").mkdir(parents=True)
    damaged = b"mhbd" + struct.pack("<IIIII", 0xF4, 99999, 1, 0x30, 5) + b"\0" * 0x100
    (mount / "iPod_Control/iTunes/iTunesDB").write_bytes(damaged)
    with pytest.raises(sync.gpod.GpodError):
        do_sync(conn, mount)
    assert (mount / "iPod_Control/iTunes/iTunesDB").read_bytes() == damaged


def test_files_are_found_whatever_their_letter_case(tmp_path):
    (tmp_path / "iPod_Control" / "Music" / "F03").mkdir(parents=True)
    (tmp_path / "iPod_Control" / "Music" / "F03" / "abcd.mp3").write_bytes(b"x")
    found = find_on_ipod(tmp_path, ":iPod_Control:Music:F03:ABCD.mp3")
    assert found and Path(found).read_bytes() == b"x"
    assert find_on_ipod(tmp_path, ":iPod_Control:..:..:etc:passwd") is None


def test_compressed_song_lists_of_newer_nanos(tmp_path):
    from luxtunes.core.ipod.itdb import cdb
    plain = sample_db().serialize()
    packed = cdb.compress(plain)
    assert cdb.is_compressed(packed) and not cdb.is_compressed(plain)
    assert rd32(packed, 8) == len(packed)
    assert cdb.decompress(packed) == plain
    # Read (to import songs) from a nano that keeps only iTunesCDB.
    itunes = tmp_path / "NANO" / "iPod_Control" / "iTunes"
    itunes.mkdir(parents=True)
    (itunes / "iTunesCDB").write_bytes(packed)
    (itunes / "iTunesDB").write_bytes(b"")
    with Database(str(tmp_path / "NANO"), "nano_7", read_only=True) as ipod_db:
        assert len(ipod_db.tracks()) == 3
        with pytest.raises(sync.gpod.GpodError):
            ipod_db.write()
    with pytest.raises(sync.gpod.GpodError):                 # not to write, yet
        Database(str(tmp_path / "NANO"), "nano_7")


# ---- iPod nano 5th–7th generation (iTunesCDB, SQLite, hash72 / hashAB) ------------------------

def sync_nano(conn, mount, family_key, **change):
    import dataclasses

    from luxtunes.core.ipod import models
    ipod = device.read_ipod(str(mount), "", "vfat")
    ipod = dataclasses.replace(ipod, identity=models.Identity(family_key,
                                                              models.FAMILIES[family_key]))
    record = settings.load_device(conn, ipod.key, ipod.name)
    for key, value in change.items():
        setattr(record.settings, key, value)
    return sync.sync(conn, ipod, record)


def read_library(mount):
    import sqlite3
    itlp = mount / "iPod_Control/iTunes/iTunes Library.itlp"
    conn = sqlite3.connect(itlp / "Library.itdb")
    titles = sorted(r[0] for r in conn.execute("SELECT title FROM item"))
    version = conn.execute("SELECT major, minor FROM version_info").fetchone()
    containers = [r[0] for r in conn.execute("SELECT name FROM container")]
    conn.close()
    locations = sqlite3.connect(itlp / "Locations.itdb")
    paths = [r[0] for r in locations.execute("SELECT location FROM location")]
    locations.close()
    return titles, version, containers, paths


@pytest.mark.parametrize("family_key", ["nano_6", "nano_7"])
def test_a_nano_6th_or_7th_generation_is_written_and_signed_with_hash_ab(tmp_path, family_key):
    from luxtunes.core.ipod import hash_ab_nanos, hashab
    from luxtunes.core.ipod.itdb import cdb
    mount, conn = fake_ipod(tmp_path, model="xMC525"), small_library(tmp_path)
    report = sync_nano(conn, mount, family_key)
    assert report.added == 3 and not [p for p in report.problems if "Photos" not in p]
    itunes = mount / "iPod_Control/iTunes"
    data = (itunes / "iTunesCDB").read_bytes()
    guid = bytes.fromhex(GUID)
    assert cdb.is_compressed(data) and hashab.song_list_is_signed(data, guid)
    nano = hash_ab_nanos.NANOS[family_key]
    assert rd32(data, 0x10) == nano.song_list_version and rd16(data, 0x70) == 4
    assert not (itunes / "iTunesDB").exists()               # iTunes leaves none on these
    titles, version, containers, paths = read_library(mount)
    assert titles == ["Harbor Lights", "Paper Lanterns", "Quiet Field"]
    assert version == nano.library_version and "Road Mix" in containers
    assert all((mount / "iPod_Control/Music" / p).exists() for p in paths)
    locations = (itunes / "iTunes Library.itlp/Locations.itdb").read_bytes()
    cbk = (itunes / "iTunes Library.itlp/Locations.itdb.cbk").read_bytes()
    assert hashab.locations_checksum(locations, guid, hashab.random_part(cbk[:57])) == cbk
    # Read back by the engine, as the next sync does: nothing to add, nothing lost.
    assert sync_nano(conn, mount, family_key).added == 0
    assert read_library(mount)[0] == titles


def test_a_nano_5th_generation_is_signed_with_hash72_and_hash58(tmp_path):
    from luxtunes.core.ipod.itdb import hash72
    mount, conn = fake_ipod(tmp_path, model="xMC031"), small_library(tmp_path)
    guid = bytes.fromhex(GUID)
    info = hash72.HashInfo.new(guid, b"0123456789ab", bytes(range(16)))
    (mount / "iPod_Control/Device/HashInfo").write_bytes(info.to_bytes())
    assert sync_nano(conn, mount, "nano_5").added == 3
    itunes = mount / "iPod_Control/iTunes"
    data = (itunes / "iTunesCDB").read_bytes()
    assert hash72.extract_from_db(data, guid) == info                  # signed with its secret
    assert hash58.sign(data, guid) == data
    assert (itunes / "iTunesDB").read_bytes() == b""
    assert read_library(mount)[0] == ["Harbor Lights", "Paper Lanterns", "Quiet Field"]


def test_a_nano_5th_generation_never_synced_with_itunes_is_refused(tmp_path):
    mount, conn = fake_ipod(tmp_path, model="xMC031"), small_library(tmp_path)
    with pytest.raises(sync.gpod.GpodError, match="iTunes once"):
        sync_nano(conn, mount, "nano_5")
    assert not (mount / "iPod_Control/iTunes/iTunesCDB").exists()


def test_the_secret_comes_back_from_a_song_list_itunes_signed(tmp_path):
    from luxtunes.core.ipod.itdb import cdb, hash72
    guid = bytes.fromhex(GUID)
    info = hash72.HashInfo.new(guid, b"abcdefghijkl", bytes(16))
    signed = hash72.sign(cdb.compress(sample_db().serialize()), info)
    itunes = tmp_path / "NANO" / "iPod_Control" / "iTunes"
    itunes.mkdir(parents=True)
    (itunes / "iTunesCDB").write_bytes(signed)
    Database(str(tmp_path / "NANO"), "nano_5", GUID, "hash72")
    kept = tmp_path / "NANO" / "iPod_Control" / "Device" / "HashInfo"
    assert hash72.HashInfo.parse(kept.read_bytes(), guid) == info


def test_aes_matches_the_standard():
    from luxtunes.core.ipod.itdb import hash72
    key, plain = bytes(range(16)), bytes.fromhex("00112233445566778899aabbccddeeff")
    cipher = hash72.aes_encrypt(key, plain)
    assert cipher.hex() == "69c4e0d86a7b0430d8cdb78070b4c55a"           # FIPS-197, C.1
    assert hash72.aes_decrypt(key, cipher) == plain
    zero = hash72.HashInfo.new(bytes.fromhex("000A27001A2B3C4D"), bytes(12), bytes(16))
    assert zero.sign(bytes(20))[14:].hex() == (                        # ZenPod's reference
        "34a8f220c7c4330b3ceab271e37a9663a613c096ba015e1ad9fc68ec9e279c2c")


def test_sort_keys_for_the_nanos_own_sql():
    from luxtunes.core.ipod.itdb.sqlitedb import iphone_sort_key, iphone_sort_section
    assert iphone_sort_key(b"ab") == bytes([0x30, 0x2D, 0x2F, 0x01, 0x06, 0x01, 0x8F, 0x05, 0])
    assert iphone_sort_key(b"A b") == bytes([0x30, 0x2D, 0x06, 0x2F, 0x01, 0x07, 0x01, 0x8F,
                                             0x85, 0x8F, 0x04, 0])
    assert iphone_sort_key(b"") == bytes([0x31, 1, 1, 0])
    assert iphone_sort_section(iphone_sort_key(b"Zoo")) == 25
    assert iphone_sort_section(iphone_sort_key(b"123")) == 26
