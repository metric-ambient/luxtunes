"""A small ctypes binding to libgpod (the library Rhythmbox and gtkpod use).

libgpod reads and writes the iPod's database (iTunesDB), including the
signature 6th/7th-generation iPod classics require, and copies song files
onto the iPod. It's a C library whose data lives in C structures, so those
structures are copied here field by field from libgpod 0.8.3's itdb.h. Their
layout was checked against the real header with a compiled C program (see
tests/test_gpod.py), because a wrong field position would corrupt the iPod's
database.

Needs the system package libgpod (sudo apt install libgpod-common); without
it, `available()` is False and LuxTunes says what to install.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import logging
from ctypes import (
    POINTER, Structure, byref, c_char_p, c_float, c_int, c_int16, c_int32, c_uint8, c_uint16,
    c_uint32, c_uint64, c_void_p,
)

from pathlib import Path

log = logging.getLogger(__name__)

time_t = ctypes.c_long          # 64-bit on 64-bit Linux
gboolean = c_int


class GList(Structure):
    pass


GList._fields_ = [("data", c_void_p), ("next", POINTER(GList)), ("prev", POINTER(GList))]


class GError(Structure):
    _fields_ = [("domain", c_uint32), ("code", c_int), ("message", c_char_p)]


class Itdb_iTunesDB(Structure):
    _fields_ = [
        ("tracks", POINTER(GList)),
        ("playlists", POINTER(GList)),
        ("filename", c_char_p),
        ("device", c_void_p),
        ("version", c_uint32),
        ("id", c_uint64),
        ("tzoffset", c_int32),
        ("reserved_int2", c_int32),
        ("priv", c_void_p),
        ("reserved2", c_void_p),
        ("usertype", c_uint64),
        ("userdata", c_void_p),
        ("userdata_duplicate", c_void_p),
        ("userdata_destroy", c_void_p),
    ]


class Itdb_SPLPref(Structure):
    _fields_ = [
        ("liveupdate", c_uint8), ("checkrules", c_uint8), ("checklimits", c_uint8),
        ("limittype", c_uint32), ("limitsort", c_uint32), ("limitvalue", c_uint32),
        ("matchcheckedonly", c_uint8), ("reserved_int1", c_int32), ("reserved_int2", c_int32),
        ("reserved1", c_void_p), ("reserved2", c_void_p),
    ]


class Itdb_SPLRules(Structure):
    _fields_ = [
        ("unk004", c_uint32), ("match_operator", c_uint32), ("rules", POINTER(GList)),
        ("reserved_int1", c_int32), ("reserved_int2", c_int32),
        ("reserved1", c_void_p), ("reserved2", c_void_p),
    ]


class Itdb_Playlist(Structure):
    _fields_ = [
        ("itdb", POINTER(Itdb_iTunesDB)),
        ("name", c_void_p),             # gchar*, owned by libgpod
        ("type", c_uint8), ("flag1", c_uint8), ("flag2", c_uint8), ("flag3", c_uint8),
        ("num", c_int),
        ("members", POINTER(GList)),
        ("is_spl", gboolean),
        ("timestamp", time_t),
        ("id", c_uint64),
        ("sortorder", c_uint32),
        ("podcastflag", c_uint32),
        ("splpref", Itdb_SPLPref),
        ("splrules", Itdb_SPLRules),
        ("reserved100", c_void_p), ("reserved101", c_void_p),
        ("reserved_int1", c_int32), ("reserved_int2", c_int32),
        ("priv", c_void_p), ("reserved2", c_void_p),
        ("usertype", c_uint64), ("userdata", c_void_p),
        ("userdata_duplicate", c_void_p), ("userdata_destroy", c_void_p),
    ]


STRING_FIELDS = ("title", "ipod_path", "album", "artist", "genre", "filetype", "comment",
                 "category", "composer", "grouping", "description", "podcasturl", "podcastrss")


class Itdb_Track(Structure):
    _fields_ = [
        ("itdb", POINTER(Itdb_iTunesDB)),
        ("title", c_void_p), ("ipod_path", c_void_p), ("album", c_void_p), ("artist", c_void_p),
        ("genre", c_void_p), ("filetype", c_void_p), ("comment", c_void_p), ("category", c_void_p),
        ("composer", c_void_p), ("grouping", c_void_p), ("description", c_void_p),
        ("podcasturl", c_void_p), ("podcastrss", c_void_p),
        ("chapterdata", c_void_p),
        ("subtitle", c_void_p), ("tvshow", c_void_p), ("tvepisode", c_void_p),
        ("tvnetwork", c_void_p), ("albumartist", c_void_p), ("keywords", c_void_p),
        ("sort_artist", c_void_p), ("sort_title", c_void_p), ("sort_album", c_void_p),
        ("sort_albumartist", c_void_p), ("sort_composer", c_void_p), ("sort_tvshow", c_void_p),
        ("id", c_uint32), ("size", c_uint32), ("tracklen", c_int32), ("cd_nr", c_int32),
        ("cds", c_int32), ("track_nr", c_int32), ("tracks", c_int32), ("bitrate", c_int32),
        ("samplerate", c_uint16), ("samplerate_low", c_uint16), ("year", c_int32),
        ("volume", c_int32), ("soundcheck", c_uint32),
        ("time_added", time_t), ("time_modified", time_t), ("time_played", time_t),
        ("bookmark_time", c_uint32), ("rating", c_uint32), ("playcount", c_uint32),
        ("playcount2", c_uint32), ("recent_playcount", c_uint32), ("transferred", gboolean),
        ("BPM", c_int16), ("app_rating", c_uint8), ("type1", c_uint8), ("type2", c_uint8),
        ("compilation", c_uint8), ("starttime", c_uint32), ("stoptime", c_uint32),
        ("checked", c_uint8), ("dbid", c_uint64), ("drm_userid", c_uint32), ("visible", c_uint32),
        ("filetype_marker", c_uint32), ("artwork_count", c_uint16), ("artwork_size", c_uint32),
        ("samplerate2", c_float), ("unk126", c_uint16), ("unk132", c_uint32),
        ("time_released", time_t), ("unk144", c_uint16), ("explicit_flag", c_uint16),
        ("unk148", c_uint32), ("unk152", c_uint32), ("skipcount", c_uint32),
        ("recent_skipcount", c_uint32), ("last_skipped", c_uint32),
        ("has_artwork", c_uint8), ("skip_when_shuffling", c_uint8),
        ("remember_playback_position", c_uint8), ("flag4", c_uint8), ("dbid2", c_uint64),
        ("lyrics_flag", c_uint8), ("movie_flag", c_uint8), ("mark_unplayed", c_uint8),
        ("unk179", c_uint8), ("unk180", c_uint32), ("pregap", c_uint32),
        ("samplecount", c_uint64), ("unk196", c_uint32), ("postgap", c_uint32),
        ("unk204", c_uint32), ("mediatype", c_uint32), ("season_nr", c_uint32),
        ("episode_nr", c_uint32), ("unk220", c_uint32), ("unk224", c_uint32),
        ("unk228", c_uint32), ("unk232", c_uint32), ("unk236", c_uint32), ("unk240", c_uint32),
        ("unk244", c_uint32), ("gapless_data", c_uint32), ("unk252", c_uint32),
        ("gapless_track_flag", c_uint16), ("gapless_album_flag", c_uint16), ("obsolete", c_uint16),
        ("artwork", c_void_p), ("mhii_link", c_uint32),
        ("reserved_int1", c_int32), ("reserved_int2", c_int32), ("reserved_int3", c_int32),
        ("reserved_int4", c_int32), ("reserved_int5", c_int32), ("reserved_int6", c_int32),
        ("priv", c_void_p), ("reserved2", c_void_p), ("reserved3", c_void_p),
        ("reserved4", c_void_p), ("reserved5", c_void_p), ("reserved6", c_void_p),
        ("usertype", c_uint64), ("userdata", c_void_p),
        ("userdata_duplicate", c_void_p), ("userdata_destroy", c_void_p),
    ]


# All string fields of Itdb_Track (they're gchar* that libgpod frees with g_free).
TRACK_STRINGS = [name for name, kind in Itdb_Track._fields_
                 if kind is c_void_p and name not in ("chapterdata", "artwork", "priv", "reserved2",
                                                      "reserved3", "reserved4", "reserved5",
                                                      "reserved6", "userdata", "userdata_duplicate",
                                                      "userdata_destroy")]

MEDIATYPE_AUDIO = 1 << 0
MEDIATYPE_MOVIE = 1 << 1
MEDIATYPE_PODCAST = 1 << 2
MEDIATYPE_AUDIOBOOK = 1 << 3
MEDIATYPE_MUSICVIDEO = 1 << 5
MEDIATYPE_TVSHOW = 1 << 6


class Itdb_IpodInfo(Structure):
    _fields_ = [("model_number", c_char_p), ("capacity", ctypes.c_double),
                ("ipod_model", c_int32), ("ipod_generation", c_int32), ("musicdirs", c_uint32),
                ("reserved_int1", c_int32), ("reserved_int2", c_int32),
                ("reserved1", c_void_p), ("reserved2", c_void_p)]


class GpodError(Exception):
    pass


_lib = None
_glib = None
_has_thumbnails = None          # itdb_track_has_thumbnails, where libgpod has it


def _load():
    global _lib, _glib, _has_thumbnails
    if _lib is not None:
        return _lib
    name = ctypes.util.find_library("gpod") or "libgpod.so.4"
    try:
        lib = ctypes.CDLL(name)
        glib = ctypes.CDLL(ctypes.util.find_library("glib-2.0") or "libglib-2.0.so.0")
    except OSError as error:
        raise GpodError("libgpod isn't installed (sudo apt install libgpod-common)") from error

    def fn(name, restype, *argtypes):
        f = getattr(lib, name)
        f.restype, f.argtypes = restype, list(argtypes)

    PDB, PPL, PTR = POINTER(Itdb_iTunesDB), POINTER(Itdb_Playlist), POINTER(Itdb_Track)
    PERR = POINTER(POINTER(GError))
    fn("itdb_parse", PDB, c_char_p, PERR)
    fn("itdb_new", PDB)
    fn("itdb_free", None, PDB)
    fn("itdb_write", gboolean, PDB, PERR)
    fn("itdb_set_mountpoint", None, PDB, c_char_p)
    fn("itdb_init_ipod", gboolean, c_char_p, c_char_p, c_char_p, PERR)
    fn("itdb_track_new", PTR)
    fn("itdb_track_add", None, PDB, PTR, c_int32)
    fn("itdb_track_remove", None, PTR)
    fn("itdb_track_set_thumbnails_from_data", gboolean, PTR, c_void_p, c_uint64)
    fn("itdb_track_remove_thumbnails", None, PTR)
    try:
        fn("itdb_track_has_thumbnails", gboolean, PTR)
        _has_thumbnails = lib.itdb_track_has_thumbnails
    except AttributeError:              # older libgpod: read the track's artwork instead
        _has_thumbnails = None
    fn("itdb_cp_track_to_ipod", gboolean, PTR, c_char_p, PERR)
    fn("itdb_filename_on_ipod", c_void_p, PTR)
    fn("itdb_playlist_new", PPL, c_char_p, gboolean)
    fn("itdb_playlist_add", None, PDB, PPL, c_int32)
    fn("itdb_playlist_remove", None, PPL)
    fn("itdb_playlist_add_track", None, PPL, PTR, c_int32)
    fn("itdb_playlist_remove_track", None, PPL, PTR)
    fn("itdb_playlist_mpl", PPL, PDB)
    fn("itdb_playlist_is_mpl", gboolean, PPL)
    fn("itdb_playlist_is_podcasts", gboolean, PPL)
    fn("itdb_playlist_podcasts", PPL, PDB)
    fn("itdb_playlist_set_podcasts", None, PPL)
    fn("itdb_device_set_sysinfo", None, c_void_p, c_char_p, c_char_p)
    fn("itdb_device_get_sysinfo", c_void_p, c_void_p, c_char_p)
    fn("itdb_device_write_sysinfo", gboolean, c_void_p, PERR)
    fn("itdb_info_get_ipod_info_table", POINTER(Itdb_IpodInfo))
    fn("itdb_info_get_ipod_model_name_string", c_char_p, c_int32)
    glib.g_strdup.restype, glib.g_strdup.argtypes = c_void_p, [c_char_p]
    glib.g_free.restype, glib.g_free.argtypes = None, [c_void_p]
    glib.g_error_free.restype, glib.g_error_free.argtypes = None, [POINTER(GError)]
    _lib, _glib = lib, glib
    return lib


def ipod_models() -> list[tuple[str, float, str, int]]:
    """libgpod's table of every iPod model it knows: (model number, capacity
    in GB, model name such as "Nano (Black)", generation number)."""
    lib = _load()
    table = lib.itdb_info_get_ipod_info_table()
    found = []
    i = 0
    while table[i].model_number:
        entry = table[i]
        name = lib.itdb_info_get_ipod_model_name_string(entry.ipod_model) or b""
        found.append((entry.model_number.decode(), entry.capacity, name.decode(),
                      entry.ipod_generation))
        i += 1
    return found


def available() -> bool:
    try:
        _load()
        return True
    except GpodError:
        return False


# ---- Helpers --------------------------------------------------------------------------

def _raise(error_ptr, what: str):
    message = what
    if error_ptr:
        message = f"{what}: {error_ptr.contents.message.decode('utf-8', 'replace')}"
        _glib.g_error_free(error_ptr)
    raise GpodError(message)


def glist(head) -> list:
    """The data pointers of a GList, in order."""
    items, node = [], head
    while node:
        items.append(node.contents.data)
        node = node.contents.next
    return items


def get_str(track: Itdb_Track, field: str) -> str | None:
    pointer = getattr(track, field)
    return ctypes.string_at(pointer).decode("utf-8", "replace") if pointer else None


def set_str(track: Itdb_Track, field: str, value) -> None:
    """Set a gchar* field: libgpod owns the string, so it's made with g_strdup."""
    old = getattr(track, field)
    if old:
        _glib.g_free(old)
    text = None if value in (None, "") else str(value)
    setattr(track, field, _glib.g_strdup(text.encode("utf-8")) if text is not None else None)


def playlist_name(playlist) -> str:
    pointer = playlist.contents.name
    return ctypes.string_at(pointer).decode("utf-8", "replace") if pointer else ""


def free_c_string(pointer) -> str | None:
    if not pointer:
        return None
    text = ctypes.string_at(pointer).decode("utf-8", "replace")
    _glib.g_free(pointer)
    return text


# ---- The database ---------------------------------------------------------------------

@contextlib.contextmanager
def _model_known(mountpoint: str, model: str | None):
    """While inside: SysInfo names `model` if it named none. Afterwards SysInfo
    is exactly as it was (or gone again, if there wasn't one)."""
    sysinfo = Path(mountpoint) / "iPod_Control" / "Device" / "SysInfo"
    try:
        before = sysinfo.read_bytes() if sysinfo.exists() else None
    except OSError:
        before = None
    names_one = before is not None and any(
        line.split(b":", 1)[0].strip() == b"ModelNumStr" and line.split(b":", 1)[1:] != [b""]
        for line in before.splitlines() if b":" in line)
    if not model or names_one:
        yield
        return
    try:
        sysinfo.parent.mkdir(parents=True, exist_ok=True)
        text = (before or b"").rstrip(b"\n")
        sysinfo.write_bytes((text + b"\n" if text else b"") + f"ModelNumStr: {model}\n".encode())
    except OSError as error:
        log.warning("couldn't tell libgpod the iPod's model: %s", error)
        yield
        return
    try:
        yield
    finally:
        try:
            if before is None:
                sysinfo.unlink()
            else:
                sysinfo.write_bytes(before)
        except OSError as error:
            log.warning("couldn't put the iPod's SysInfo back: %s", error)


class Database:
    """An iPod's iTunesDB, opened with libgpod. Use as a context manager."""

    def __init__(self, mountpoint: str, model_hint: str | None = None):
        """model_hint: a model number for an iPod whose SysInfo doesn't name one.
        libgpod only reads the existing album artwork when it knows the model
        while it reads the database; without it, saving would keep only the
        artwork added in this sync. So the hint is put in SysInfo just while
        the database is read, and SysInfo is put back as it was straight after."""
        lib = _load()
        self.mountpoint = mountpoint
        error = POINTER(GError)()
        with _model_known(mountpoint, model_hint):
            self.db = lib.itdb_parse(mountpoint.encode(), byref(error))
        if not self.db:
            _raise(error, "couldn't read the iPod's database")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.db:
            _lib.itdb_free(self.db)
            self.db = None

    # Device info (SysInfo): libgpod needs the FireWire GUID for 6G/7G signatures.
    @property
    def _device(self):
        return self.db.contents.device

    def sysinfo(self, field: str) -> str | None:
        return free_c_string(_lib.itdb_device_get_sysinfo(self._device, field.encode()))

    def set_sysinfo(self, field: str, value: str) -> None:
        _lib.itdb_device_set_sysinfo(self._device, field.encode(), value.encode())

    def write_sysinfo(self) -> None:
        error = POINTER(GError)()
        if not _lib.itdb_device_write_sysinfo(self._device, byref(error)):
            _raise(error, "couldn't write the iPod's SysInfo")

    # Tracks and playlists
    def tracks(self) -> list:
        return [ctypes.cast(p, POINTER(Itdb_Track)) for p in glist(self.db.contents.tracks)]

    def playlists(self) -> list:
        return [ctypes.cast(p, POINTER(Itdb_Playlist)) for p in glist(self.db.contents.playlists)]

    def master(self):
        return _lib.itdb_playlist_mpl(self.db)

    def name(self) -> str:
        """The iPod's name (the title of its main playlist)."""
        master = self.master()
        return playlist_name(master) if master else ""

    def set_name(self, name: str) -> None:
        set_str(self.master().contents, "name", name)

    def new_track(self):
        track = _lib.itdb_track_new()
        _lib.itdb_track_add(self.db, track, -1)
        _lib.itdb_playlist_add_track(self.master(), track, -1)
        return track

    def copy_file(self, track, path: str) -> None:
        error = POINTER(GError)()
        if not _lib.itdb_cp_track_to_ipod(track, path.encode(), byref(error)):
            _raise(error, "couldn't copy the song to the iPod")

    def file_of(self, track) -> str | None:
        return free_c_string(_lib.itdb_filename_on_ipod(track))

    def remove_track(self, track) -> None:
        """Take a song off the iPod: out of every playlist, then the database
        (which frees it). The file itself is deleted by the caller first."""
        for playlist in self.playlists():
            _lib.itdb_playlist_remove_track(playlist, track)
        _lib.itdb_track_remove(track)

    def has_artwork(self, track) -> bool:
        """The iPod has album artwork for this track (as read from its ArtworkDB)."""
        if _has_thumbnails is not None:
            return bool(_has_thumbnails(track))
        artwork = track.contents.artwork            # Itdb_Artwork: its first field is the thumbnail
        return bool(artwork) and bool(ctypes.cast(artwork, POINTER(c_void_p))[0])

    def set_artwork(self, track, data: bytes) -> bool:
        buffer = ctypes.create_string_buffer(data, len(data))
        return bool(_lib.itdb_track_set_thumbnails_from_data(track, buffer, len(data)))

    def remove_playlist(self, playlist) -> None:
        _lib.itdb_playlist_remove(playlist)

    def new_playlist(self, name: str):
        playlist = _lib.itdb_playlist_new(name.encode("utf-8"), 0)
        _lib.itdb_playlist_add(self.db, playlist, -1)
        return playlist

    def add_to_playlist(self, playlist, track) -> None:
        _lib.itdb_playlist_add_track(playlist, track, -1)

    def is_master(self, playlist) -> bool:
        return bool(_lib.itdb_playlist_is_mpl(playlist))

    def is_podcasts(self, playlist) -> bool:
        return bool(_lib.itdb_playlist_is_podcasts(playlist))

    def podcasts_playlist(self, create: bool = True):
        """The iPod's special Podcasts playlist (what its Podcasts menu shows),
        made if it isn't there yet (with create). None if there's none."""
        playlist = _lib.itdb_playlist_podcasts(self.db)
        if not playlist and not create:
            return None
        if not playlist:
            playlist = _lib.itdb_playlist_new(b"Podcasts", 0)
            _lib.itdb_playlist_set_podcasts(playlist)
            _lib.itdb_playlist_add(self.db, playlist, -1)
        return playlist

    def playlist_tracks(self, playlist) -> list:
        return [ctypes.cast(p, POINTER(Itdb_Track)) for p in glist(playlist.contents.members)]

    def remove_from_playlist(self, playlist, track) -> None:
        _lib.itdb_playlist_remove_track(playlist, track)

    def write(self) -> None:
        error = POINTER(GError)()
        if not _lib.itdb_write(self.db, byref(error)):
            _raise(error, "couldn't save the iPod's database")


def init_ipod(mountpoint: str, model_number: str, name: str) -> None:
    """Create a fresh, empty iPod database (like a restore, but only the
    database; used for tests on a fake iPod folder)."""
    lib = _load()
    error = POINTER(GError)()
    if not lib.itdb_init_ipod(mountpoint.encode(), model_number.encode(), name.encode(), byref(error)):
        _raise(error, "couldn't set up the iPod")
