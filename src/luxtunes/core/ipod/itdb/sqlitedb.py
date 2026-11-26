"""The SQLite library of the iPod nano 5th–7th generation
(iPod_Control/iTunes/iTunes Library.itlp): what these nanos actually read
their songs from. Made fresh from the song list at every save, as libgpod
and iTunes do.

Ported from ZenPod's sqlitedb.rs (ishiichan-dayo, GPL-3.0-or-later), which
follows libgpod's itdb_sqlite.c. For the nano 6th and 7th generation the
nano's own "post-process" SQL, extra columns and version numbers are then
applied from hash_ab_nanos.py, as LuxTunes has done after libgpod.
"""

from __future__ import annotations

import hashlib
import logging
import os
import plistlib
import shutil
import sqlite3
import tempfile
from pathlib import Path

from luxtunes.core.ipod.itdb import sqlite_schema
from luxtunes.core.ipod.itdb.binary import rd16, rd32, rd64
from luxtunes.core.ipod.itdb.itunesdb import (
    MEDIATYPE_MOVIE,
    MEDIATYPE_MUSICVIDEO,
    MEDIATYPE_TVSHOW,
    ITunesDB,
    Track,
)

log = logging.getLogger(__name__)

ITLP = "iTunes Library.itlp"
FILES = ("Dynamic.itdb", "Extras.itdb", "Genius.itdb", "Library.itdb", "Locations.itdb",
         "Locations.itdb.cbk")
POST_PROCESS_KEY = "com.apple.mobile.iTunes.SQLMusicLibraryPostProcessCommands"
APPLE_EPOCH = 978_307_200             # 2001-01-01, where these databases count time from
VIDEO = MEDIATYPE_MOVIE | MEDIATYPE_MUSICVIDEO | MEDIATYPE_TVSHOW


def _i32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value


def _i64(value: int) -> int:
    value &= 0xFFFFFFFFFFFFFFFF
    return value - (1 << 64) if value & (1 << 63) else value


class _Context:
    def __init__(self, db: ITunesDB):
        self.db = db
        self.tzoffset = rd32(db.header, 0x6C)

    def time(self, unix: int) -> int:
        return _i32(int(unix) - APPLE_EPOCH - self.tzoffset)


def _open_new(folder: Path, name: str, schema) -> sqlite3.Connection:
    path = folder / name
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA synchronous = OFF")
    conn.executescript("".join(schema))
    conn.execute("DROP TABLE IF EXISTS sqlite_stat4")
    return conn


def _insert(conn, sql: str, values, name: str, fatal: bool = False) -> None:
    try:
        conn.execute(sql, values)
    except sqlite3.Error as error:
        if fatal:
            raise
        log.warning("%s: %s", name, error)


def _dynamic(folder: Path, cx: _Context) -> None:
    conn = _open_new(folder, "Dynamic.itdb", sqlite_schema.DYNAMIC)
    conn.execute("BEGIN")
    for t in cx.db.tracks:
        if not t.ipod_path:
            continue
        _insert(conn, 'INSERT INTO "item_stats" (item_pid,has_been_played,date_played,'
                'play_count_user,play_count_recent,date_skipped,skip_count_user,'
                'skip_count_recent,bookmark_time_ms,bookmark_time_ms_common,user_rating,'
                'user_rating_common) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (_i64(t.dbid), int(t.playcount > 0), cx.time(t.time_played), _i32(t.playcount),
                 0, cx.time(t.last_skipped), _i32(t.skipcount), 0, float(t.bookmark_time),
                 float(t.bookmark_time), t.rating, t.app_rating), "Dynamic.itdb")
    for p in cx.db.playlists:
        play_order = 39 if rd32(p.hdr, 0x2C) == 24 else 7
        _insert(conn, 'INSERT INTO "container_ui" VALUES(?,?,?,?,?,?,?)',
                (_i64(p.id), play_order, 0, 1, 0, 0, 0), "Dynamic.itdb")
    conn.execute("COMMIT")
    conn.close()


def _extras(folder: Path, cx: _Context) -> None:
    conn = _open_new(folder, "Extras.itdb", sqlite_schema.EXTRAS)
    conn.execute("BEGIN")
    for t in cx.db.tracks:
        chapters = next((m for m in t.raw_mhods if rd32(m, 0x0C) == 17), None)
        _insert(conn, 'INSERT INTO "chapter" VALUES(?,?)', (_i64(t.dbid), chapter_blob(chapters)),
                "Extras.itdb")
    conn.execute("COMMIT")
    conn.close()


def _genius(folder: Path) -> None:
    _open_new(folder, "Genius.itdb", sqlite_schema.GENIUS).close()


# Orders: each distinct name gets 100, 200, 300… in sorted order.
def _sort_value(t: Track, field: str) -> str | None:
    def first(*names):
        return next((getattr(t, n) for n in names if getattr(t, n)), None)
    return {
        "title": lambda: first("sort_title", "title"),
        "artist": lambda: first("sort_artist", "artist"),
        "genre": lambda: t.genre,
        "composer": lambda: first("sort_composer", "composer"),
        "album_artist": lambda: first("sort_artist", "artist"),
        "album_by_artist": lambda: first("sort_albumartist", "albumartist", "sort_artist",
                                         "artist"),
    }[field]()


def _orders(tracks, field: str) -> dict[str, int]:
    names = sorted({(v.lower(), v) for v in (_sort_value(t, field) for t in tracks) if v})
    return {name: (i + 1) * 100 for i, (_, name) in enumerate(names)}


def _strip_article(text: str) -> str:
    for article in ("the ", "an ", "a "):
        if text.startswith(article) and text[len(article):].strip():
            return text[len(article):].lstrip()
    return text


def _album_rank_key(t: Track) -> tuple[int, str]:
    key = t.sort_album.strip().lower() if t.sort_album else \
        _strip_article((t.album or "").strip().lower())
    group = 2 if not key else 0 if key[0].isalpha() else 1
    return group, key


def _derived_pid(library: int, kind: str, key: str) -> int:
    digest = hashlib.sha1(library.to_bytes(8, "little") + kind.encode() + b"\0"
                          + key.encode()).digest()
    return int.from_bytes(digest[:8], "little") or 1


def genius_cuid(db: ITunesDB) -> str | None:
    raw = dict(db.preserved).get(9)
    if not raw:
        return None
    body = raw[rd32(raw, 4):]
    end = body.find(b"\0")
    return body[:end if end >= 0 else len(body)].decode("utf-8", "replace")


def _library(folder: Path, cx: _Context) -> None:
    db = cx.db
    h = db.header
    library_pid = rd64(h, 0x48)
    conn = _open_new(folder, "Library.itdb", sqlite_schema.LIBRARY)
    conn.execute("BEGIN")
    version = max(rd32(h, 0x10), 0x30)
    conn.execute('INSERT INTO "version_info" VALUES(?,?,?,?,?,?,?)',
                 (1, 2 if version >= 0x28 else 1, version, 0, 0, 0, rd16(h, 0x20)))
    genres: dict[str, int] = {}
    for t in db.tracks:
        if t.genre and t.genre not in genres:
            genres[t.genre] = len(genres) + 1
            conn.execute('INSERT INTO "genre_map" (id,genre,genre_order) VALUES(?,?,0)',
                         (genres[t.genre], t.genre))
    present = {id(t) for t in db.tracks}
    for position, p in enumerate(db.playlists):
        kinds = 0
        for order, t in enumerate(x for x in p.items if id(x) in present):
            _insert(conn, 'INSERT INTO "item_to_container" VALUES(?,?,?,?)',
                    (_i64(t.dbid), _i64(p.id), order, None), "Library.itdb")
            kinds |= t.mediatype
        created = cx.time(mac_unix(rd32(p.hdr, 0x18)))
        _insert(conn, 'INSERT INTO "container" VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,'
                'NULL,NULL,NULL,NULL,NULL,NULL,NULL)',
                (_i64(p.id), 11 if p.is_podcasts else 0, created, created, p.name, position, 0,
                 _i32(kinds), 0, int(p.is_podcasts or p.is_master), 0), "Library.itdb")
    conn.execute('INSERT INTO "db_info" VALUES(?,?,?,?,?,?,?,?)',
                 (_i64(library_pid), _i64(db.master.id), None, rd16(h, 0xA0), rd16(h, 0xA2),
                  genius_cuid(db), None, None))

    fields = ("title", "artist", "genre", "composer", "album_artist", "album_by_artist")
    orders = {f: _orders(db.tracks, f) for f in fields}

    def order(t, f):
        value = _sort_value(t, f)
        return 100 if value is None else orders[f].get(value, 0)
    ranks = {k: (i + 1) * 100 for i, k in enumerate(sorted({_album_rank_key(t)
                                                              for t in db.tracks}))}
    item_sql = (
        'INSERT INTO "item" (pid,media_kind,date_modified,year,is_compilation,remember_bookmark,'
        'exclude_from_shuffle,artwork_status,artwork_cache_id,start_time_ms,stop_time_ms,'
        'total_time_ms,total_burn_time_ms,track_number,track_count,disc_number,disc_count,bpm,'
        'relative_volume,eq_preset,radio_stream_status,genre_id,album_pid,artist_pid,'
        'composer_pid,title,artist,album,album_artist,composer,sort_title,sort_artist,'
        'sort_album,sort_album_artist,sort_composer,title_order,artist_order,album_order,'
        'genre_order,composer_order,album_artist_order,album_by_artist_order,'
        'series_name_order,comment,grouping,description,description_long) '
        'VALUES(' + ",".join("?" * 47) + ')')
    for t in db.tracks:
        album_pid = _derived_pid(library_pid, "album",
                                 f"{t.album}\0{t.albumartist or t.artist or ''}") \
            if t.album else 0
        artist_pid = _derived_pid(library_pid, "artist", t.artist) if t.artist else 0
        composer_pid = _derived_pid(library_pid, "composer", t.composer) if t.composer else 0
        album_order = ranks.get(_album_rank_key(t), 0)
        conn.execute(item_sql, (
            _i64(t.dbid), _i32(t.mediatype), cx.time(t.time_modified), _i32(t.year),
            t.hdr[0x1E], t.hdr[0xA6], t.hdr[0xA5], t.has_artwork, _i32(t.mhii_link),
            float(t.starttime), float(t.stoptime), float(_i32(t.tracklen)), None,
            _i32(t.track_nr), _i32(t.tracks), _i32(t.cd_nr), _i32(t.cds), t.BPM, t.volume,
            None, None, genres.get(t.genre, 0) if t.genre else 0, _i64(album_pid),
            _i64(artist_pid), _i64(composer_pid), t.title, t.artist, t.album, t.albumartist,
            t.composer, t.sort_title or t.title, t.sort_artist or t.artist,
            t.sort_album or t.album, t.sort_albumartist or t.albumartist,
            t.sort_composer or t.composer, order(t, "title"), order(t, "artist"), album_order,
            order(t, "genre"), order(t, "composer"), order(t, "album_artist"),
            order(t, "album_by_artist"), 100, t.comment, t.grouping, t.description, None))
        _insert(conn, 'INSERT OR IGNORE INTO "location_kind_map" VALUES(?,?)',
                (_i32(t.mediatype), t.filetype), "Library.itdb")
        audio_format = {0x4D503320: 301, 0x4D344120: 502, 0x4D345220: 502,
                        0x4D503420: 502}.get(t.filetype_marker, 0)
        _insert(conn, 'INSERT INTO "avformat_info" VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (_i64(t.dbid), 0, audio_format, _i32(t.bitrate), float(t.samplerate), 0,
                 t.gapless_track_flag, _i32(t.pregap), _i32(t.postgap), _i32(t.gapless_data),
                 0, 0, _i32(t.soundcheck)), "Library.itdb")
        if t.album:
            _insert(conn, 'INSERT OR IGNORE INTO "album" VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (_i64(album_pid), 2, 0, 0, _i64(artist_pid), 0, t.album, album_order, 0,
                     None, 0), "Library.itdb")
        if t.artist:
            _insert(conn, 'INSERT OR IGNORE INTO "artist" VALUES(?,?,?,?,?,?,?)',
                    (_i64(artist_pid), 2, 0, 0, t.artist, order(t, "artist"), t.artist),
                    "Library.itdb")
        if t.composer:
            _insert(conn, 'INSERT OR IGNORE INTO "composer" (pid,name,name_order,sort_name) '
                    'VALUES(?,?,?,?)', (_i64(composer_pid), t.composer, order(t, "composer"),
                                        t.composer), "Library.itdb")
        if t.mediatype & VIDEO:
            _insert(conn, 'INSERT INTO "video_info" VALUES(' + ",".join("?" * 22) + ')',
                    (_i64(t.dbid), 0, 0, 0, 0, 0, 0, 0, _i32(t.season_nr), 0, 0, 0, 0, 0, 0,
                     t.tvshow, t.sort_tvshow or t.tvshow, t.tvepisode,
                     _i32((t.season_nr << 16) | t.episode_nr), t.tvnetwork, None, None),
                    "Library.itdb")
    try:
        conn.execute("UPDATE album SET artwork_item_pid = (SELECT item.pid FROM item WHERE "
                     "item.artwork_cache_id != 0 AND item.album_pid = album.pid LIMIT 1)")
    except sqlite3.Error as error:
        log.warning("Library.itdb: %s", error)
    conn.execute("COMMIT")
    conn.close()


def mac_unix(mac: int) -> int:
    from luxtunes.core.ipod.itdb.binary import mac_to_unix
    return mac_to_unix(mac)


def location_path(ipod_path: str) -> str:
    """":iPod_Control:Music:F00:ABCD.mp3" -> "F00/ABCD.mp3" (from the base location)."""
    colons = [i for i, c in enumerate(ipod_path) if c == ":"]
    start = colons[2] + 1 if len(colons) > 2 else 0
    return ipod_path[start:].replace(":", "/")


def _locations(folder: Path, cx: _Context) -> None:
    conn = _open_new(folder, "Locations.itdb", sqlite_schema.LOCATIONS)
    conn.execute("BEGIN")
    conn.execute('INSERT INTO "base_location" (id, path) VALUES (?,?)', (1, "iPod_Control/Music"))
    for t in cx.db.tracks:
        if not t.ipod_path:
            continue
        _insert(conn, 'INSERT INTO "location" (item_pid, sub_id, base_location_id, '
                'location_type, location, extension, kind_id, date_created, file_size) '
                'VALUES(?,?,?,?,?,?,?,?,?)',
                (_i64(t.dbid), 0, 1, 0x46494C45, location_path(t.ipod_path),
                 _i32(t.filetype_marker), _i32(t.mediatype), cx.time(t.time_modified),
                 _i32(t.size)), "Locations.itdb")
    conn.execute("COMMIT")
    conn.close()


# ---- Chapters (the Extras database keeps one entry per song) -------------------------------

def _parse_chapters(m: bytes):
    d = m[rd32(m, 4):]

    def be(o):
        return int.from_bytes(d[o:o + 4], "big") if o + 4 <= len(d) else None

    def tag(o, t):
        return d[o + 4:o + 8] == t
    if len(d) < 12:
        return None
    unk = [rd32(d, 0), rd32(d, 4), rd32(d, 8)]
    chapters = []
    s = 12
    if tag(s, b"sean"):
        n = ((be(s + 12) or 0) - 1) & 0xFFFFFFFF
        s += 20
        for _ in range(min(n, 10000)):
            if not tag(s, b"chap"):
                break
            start, children = be(s + 8), be(s + 12)
            if start is None or children is None:
                return None
            s += 20
            for _ in range(min(children, 100)):
                length = be(s)
                if length is None:
                    return None
                if tag(s, b"name"):
                    count = int.from_bytes(d[s + 20:s + 22], "big")
                    raw = d[s + 22:s + 22 + count * 2]
                    text = raw.decode("utf-16-be", "replace").split("\0")[0]
                    chapters.append((max(start, 1), text))
                if length == 0:
                    break
                s += length
    return unk, chapters


def _build_chapter_blob(unk, chapters) -> bytes:
    b = bytearray()
    for u in unk:
        b += (u & 0xFFFFFFFF).to_bytes(4, "little")
    atom = len(b)

    def be(v):
        b.extend((v & 0xFFFFFFFF).to_bytes(4, "big"))
    be(0)
    b += b"sean"
    be(1)
    be(len(chapters) + 1)
    be(0)
    for start, title in chapters:
        units = title.encode("utf-16-be")
        n = len(units) // 2
        be(42 + 2 * n)
        b += b"chap"
        be(start)
        be(1)
        be(0)
        be(22 + 2 * n)
        b += b"name"
        be(1)
        b += bytes(8)
        b += n.to_bytes(2, "big") + units
    be(28)
    b += b"hedr"
    be(1)
    be(0)
    b += bytes(8)
    be(1)
    b[atom:atom + 4] = (len(b) - atom).to_bytes(4, "big")
    return bytes(b)


def chapter_blob(mhod: bytes | None) -> bytes:
    parsed = _parse_chapters(mhod) if mhod else None
    unk, chapters = parsed if parsed else ([0, 0, 0], [])
    return _build_chapter_blob(unk, chapters)


# ---- The nano's own SQL ("post-process commands") ----------------------------------------

def iphone_sort_key(value: bytes | None) -> bytes:
    """libgpod's iPhoneSortKey(), which the nano's own SQL calls."""
    if value is None:
        return bytes((0x31, 0, 0, 0))
    words, length = 0, 0
    for c in value:
        if chr(c).isascii() and chr(c).isalnum():
            length += 1
        elif c == 0x20:
            words += 1
            length += 1
        else:
            length += 2
    if not value:
        size, wo = 4, 0
    else:
        wo = 1 + length + 3
        size = wo + (words + 1) * 2 + 1
    buf = bytearray(size)
    buf[0] = 0x31
    if size <= 4:
        buf[1] = buf[2] = 1
        return bytes(buf)

    def put(i, v):
        if 0 <= i < size:
            buf[i] = v & 0xFF
    put(0, 0x30)
    bi, word_count, word_len = 1, 0, 0
    for c in value:
        c = ord(chr(c).upper()) if 0x61 <= c <= 0x7A else c
        word_len += 1
        if chr(c).isascii() and chr(c).isalnum():
            put(bi, c + c - 0x55)
            bi += 1
            continue
        if c == 0x20:
            put(bi, 0x06)
            bi += 1
            word_len -= 1
            put(wo + word_count * 2, 0x8F)
            put(wo + word_count * 2 + 1, 0x86 - word_len)
            word_count += 1
            word_len = 0
            continue
        pair = {0x3A: (0x07, 0xD8), 0x2D: (0x07, 0x90), 0x2C: (0x07, 0xB2), 0x2E: (0x08, 0x51),
                0x27: (0x07, 0x31)}.get(c, (0x07, 0x90))
        put(bi, pair[0])
        put(bi + 1, pair[1])
        bi += 2
    put(wo + word_count * 2, 0x8F)
    put(wo + word_count * 2 + 1, 3 + word_len)
    put(wo - 3, 0x01)
    put(wo - 2, len(value) + 4)
    put(wo - 1, 0x01)
    return bytes(buf)


def iphone_sort_section(value) -> int:
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, (bytes, bytearray)) and len(value) >= 2 and value[0] == 0x30 \
            and 0x2D <= value[1] <= 0x5F:
        return (value[1] - 0x2D) // 2
    return 26


def _sort_key_sql(value):
    if value is None:
        return bytes((0x31, 0x01, 0x01, 0x00))
    if isinstance(value, str):
        return iphone_sort_key(value.encode("utf-8").split(b"\0")[0])
    return None


def _attach_all(conn: sqlite3.Connection, folder: Path) -> None:
    for name in ("Dynamic.itdb", "Extras.itdb", "Genius.itdb", "Locations.itdb"):
        conn.execute("ATTACH DATABASE ? AS ?", (str(folder / name), name))
    conn.create_function("iPhoneSortKey", 1, _sort_key_sql)
    conn.create_function("iPhoneSortSection", 1, iphone_sort_section)


def post_process_commands(sysinfo_extended: bytes) -> list[str]:
    """The nano's own SQL, from its SysInfoExtended: the newest command set, in order."""
    try:
        info = plistlib.loads(sysinfo_extended)
        commands = info[POST_PROCESS_KEY]
        sql, sets = commands["SQLCommands"], commands["UserVersionCommandSets"]
    except Exception:
        return []

    def number(key):
        try:
            return int(str(key).strip(), 0)
        except ValueError:
            return 0
    best = max((k for k in sets if number(k) > 0), key=number, default=None)
    names = sets[best].get("Commands", []) if best is not None else []
    return [sql[name] for name in names if name in sql]


def _run_sql(folder: Path, statements: list[str]) -> None:
    conn = sqlite3.connect(folder / "Library.itdb", isolation_level=None)
    try:
        _attach_all(conn, folder)
        done = 0
        for statement in statements:
            try:
                conn.executescript(statement)
                done += 1
            except sqlite3.Error as error:
                log.debug("nano post-process: %s", error)
        log.info("nano post-process: %d of %d commands", done, len(statements))
    finally:
        conn.close()


def _finish_for_nano(folder: Path, nano) -> None:
    """As LuxTunes did after libgpod: the nano's SQL, its extra columns, versions."""
    from luxtunes.core.ipod import hash_ab_nanos
    conn = sqlite3.connect(folder / "Library.itdb", isolation_level=None)
    try:
        _attach_all(conn, folder)
        conn.execute("BEGIN")
        for name, sql in nano.sql():
            try:
                conn.execute(sql)
            except sqlite3.OperationalError as error:
                log.debug("nano post-process %s: %s", name, error)
        for table, column in nano.missing_columns:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass
        for sql in hash_ab_nanos.LIBGPOD_EXTRAS:
            conn.execute(sql)
        conn.execute("UPDATE version_info SET major = ?, minor = ?", nano.library_version)
        conn.execute("COMMIT")
    finally:
        conn.close()
    for name, version in nano.user_versions.items():
        other = sqlite3.connect(folder / name, isolation_level=None)
        try:
            other.execute(f"PRAGMA user_version = {int(version)}")
        finally:
            other.close()


def locations_cbk(locations: bytes, sign) -> bytes:
    """Locations.itdb.cbk: the signature, the SHA1 of the blocks' SHA1s, then those."""
    sha1s = b"".join(hashlib.sha1(locations[i:i + 1024]).digest()
                     for i in range(0, len(locations) - len(locations) % 1024, 1024))
    if not sha1s:
        raise ValueError("Locations.itdb is too small")
    final = hashlib.sha1(sha1s).digest()
    return sign(final) + final + sha1s


def build(db: ITunesDB, folder: Path, sign, nano=None, post_process: list[str] = ()) -> None:
    """Make the library's files in `folder`. sign(sha1) -> the signature for
    Locations.itdb.cbk; nano: a hash_ab_nanos.Nano (6th/7th generation)."""
    cx = _Context(db)
    _dynamic(folder, cx)
    _extras(folder, cx)
    _genius(folder)
    _library(folder, cx)
    _locations(folder, cx)
    if nano is not None:
        _finish_for_nano(folder, nano)
    elif post_process:
        _run_sql(folder, list(post_process))
    (folder / "Locations.itdb.cbk").write_bytes(
        locations_cbk((folder / "Locations.itdb").read_bytes(), sign))


def write(itunes: Path, db: ITunesDB, sign, nano=None, post_process: list[str] = ()) -> None:
    """Build the library beside this computer's temporary files, then put each
    file on the iPod (written beside the old one and flushed), then swap them
    all in."""
    from luxtunes.core.ipod.itdb.database import TEMP_SUFFIX
    itlp = itunes / ITLP
    if itlp.exists() and not itlp.is_dir():
        raise OSError(f"{ITLP} isn't a folder")
    itlp.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="luxtunes-itlp-") as scratch:
        build(db, Path(scratch), sign, nano, post_process)
        staged = [(itlp / (name + TEMP_SUFFIX), itlp / name) for name in FILES]
        try:
            for name, (part, _) in zip(FILES, staged):
                shutil.copyfile(Path(scratch) / name, part)
                with open(part, "rb+") as f:
                    os.fsync(f.fileno())
        except BaseException:
            for part, _ in staged:
                part.unlink(missing_ok=True)
            raise
        for part, target in staged:
            os.replace(part, target)
