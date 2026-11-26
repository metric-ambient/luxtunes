"""iTunesDB (iPod_Control/iTunes/iTunesDB): the iPod's list of songs and
playlists. Reads and writes it the way libgpod does.

Ported from ZenPod's itunesdb.rs (ishiichan-dayo, GPL-3.0-or-later), which
follows libgpod's itdb_itunesdb.c. As there:
  - a record that was read keeps its header bytes as they were, and only the
    fields LuxTunes sets are changed, so nothing iTunes wrote is lost;
  - new records are laid out as libgpod writes them;
  - the sections (mhsd) are written in libgpod's order: 1, 3, 2, 4, 8, 6, 10,
    5, (9).

Songs carry libgpod's field names (title, tracklen, time_played...), so the
sync code reads the same with either engine. Times are Unix seconds, as
libgpod gives them; the file holds Mac times (from 1904).
"""

from __future__ import annotations

import os
import time

from luxtunes.core.ipod.itdb.binary import (
    Corrupt,
    chunk,
    mac_to_unix,
    rd16,
    rd32,
    rd64,
    rdf,
    unix_to_mac,
    wr16,
    wr32,
    wr64,
    wrf,
)

MHBD_HLEN = 0xF4
MHIT_HLEN = 0x248
MHYP_HLEN = 108
FIRST_TRACK_ID = 52

MEDIATYPE_AUDIO = 1 << 0
MEDIATYPE_MOVIE = 1 << 1
MEDIATYPE_PODCAST = 1 << 2
MEDIATYPE_AUDIOBOOK = 1 << 3
MEDIATYPE_MUSICVIDEO = 1 << 5
MEDIATYPE_TVSHOW = 1 << 6

# String records (mhod) of a song, by libgpod's field name.
STRINGS = {
    "title": 1, "ipod_path": 2, "album": 3, "artist": 4, "genre": 5, "filetype": 6,
    "comment": 8, "category": 9, "composer": 12, "grouping": 13, "description": 14,
    "subtitle": 18, "tvshow": 19, "tvepisode": 20, "tvnetwork": 21, "albumartist": 22,
    "sort_artist": 23, "keywords": 24, "sort_title": 27, "sort_album": 28,
    "sort_albumartist": 29, "sort_composer": 30, "sort_tvshow": 31,
}
MHOD_TITLE, MHOD_PATH, MHOD_ALBUM, MHOD_ARTIST, MHOD_GENRE = 1, 2, 3, 4, 5
MHOD_EQ = 7
MHOD_COMPOSER = 12
MHOD_ALBUM_ARTIST = 22
MHOD_SORT_ARTIST, MHOD_SORT_TITLE, MHOD_SORT_ALBUM = 23, 27, 28
MHOD_SORT_ALBUM_ARTIST, MHOD_SORT_COMPOSER = 29, 30
MHOD_SPLPREF, MHOD_SPLRULES = 50, 51
MHOD_LIBINDEX, MHOD_LIBJUMP = 52, 53
MHOD_PLAYLIST = 100

TRACK_STRING_ORDER = (1, 4, 3, 6, 8, 2, 5, 9, 12, 13, 14, 18, 19, 20, 21, 22, 24, 23, 27, 28,
                      29, 30, 31)


def is_string_mhod(kind: int) -> bool:
    return (1 <= kind <= 14 or 18 <= kind <= 31 or 200 <= kind <= 202 or kind == 300) \
        and kind not in (7, 10, 11)


# Number fields of a song (mhit), by libgpod's field name:
# (offset, size in bytes, signed, Mac time).
FIELDS = {
    "id": (0x10, 4, False, False), "visible": (0x14, 4, False, False),
    "filetype_marker": (0x18, 4, False, False),
    "type1": (0x1C, 1, False, False), "type2": (0x1D, 1, False, False),
    "compilation": (0x1E, 1, False, False), "rating": (0x1F, 1, False, False),
    "time_modified": (0x20, 4, False, True), "size": (0x24, 4, False, False),
    "tracklen": (0x28, 4, True, False), "track_nr": (0x2C, 4, True, False),
    "tracks": (0x30, 4, True, False), "year": (0x34, 4, True, False),
    "bitrate": (0x38, 4, True, False), "samplerate_low": (0x3C, 2, False, False),
    "volume": (0x40, 4, True, False), "starttime": (0x44, 4, False, False),
    "stoptime": (0x48, 4, False, False), "soundcheck": (0x4C, 4, False, False),
    "playcount": (0x50, 4, False, False), "playcount2": (0x54, 4, False, False),
    "time_played": (0x58, 4, False, True), "cd_nr": (0x5C, 4, True, False),
    "cds": (0x60, 4, True, False), "drm_userid": (0x64, 4, False, False),
    "time_added": (0x68, 4, False, True), "bookmark_time": (0x6C, 4, False, False),
    "dbid": (0x70, 8, False, False), "checked": (0x78, 1, False, False),
    "app_rating": (0x79, 1, False, False), "BPM": (0x7A, 2, True, False),
    "artwork_count": (0x7C, 2, False, False), "unk126": (0x7E, 2, False, False),
    "artwork_size": (0x80, 4, False, False), "unk132": (0x84, 4, False, False),
    "time_released": (0x8C, 4, False, True), "unk144": (0x90, 2, False, False),
    "explicit_flag": (0x92, 2, False, False), "skipcount": (0x9C, 4, False, False),
    "last_skipped": (0xA0, 4, False, True), "has_artwork": (0xA4, 1, False, False),
    "skip_when_shuffling": (0xA5, 1, False, False),
    "remember_playback_position": (0xA6, 1, False, False), "flag4": (0xA7, 1, False, False),
    "dbid2": (0xA8, 8, False, False), "lyrics_flag": (0xB0, 1, False, False),
    "movie_flag": (0xB1, 1, False, False), "mark_unplayed": (0xB2, 1, False, False),
    "pregap": (0xB8, 4, False, False), "samplecount": (0xBC, 8, False, False),
    "postgap": (0xC8, 4, False, False), "mediatype": (0xD0, 4, False, False),
    "season_nr": (0xD4, 4, False, False), "episode_nr": (0xD8, 4, False, False),
    "gapless_data": (0xF8, 4, False, False), "gapless_track_flag": (0x100, 2, False, False),
    "gapless_album_flag": (0x102, 2, False, False), "album_id": (0x120, 4, False, False),
    "mhii_link": (0x160, 4, False, False), "artist_id": (0x1E0, 4, False, False),
    "composer_id": (0x1F4, 4, False, False),
}


def _number_field(offset: int, size: int, signed: bool, mac: bool):
    def get(self):
        value = int.from_bytes(self.hdr[offset:offset + size], "little", signed=signed)
        return mac_to_unix(value) if mac else value

    def set_(self, value):
        value = int(value or 0)
        if mac:
            value = unix_to_mac(value)
        bits = 8 * size
        if signed:
            low, high = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
            value = max(low, min(high, value))
            value &= (1 << bits) - 1
        else:
            value = max(0, min((1 << bits) - 1, value))
        self.hdr[offset:offset + size] = value.to_bytes(size, "little")
    return property(get, set_)


def _string_field(kind: int):
    def get(self):
        return self.strings.get(kind) or None

    def set_(self, value):
        text = "" if value is None else str(value).strip()
        if text:
            self.strings[kind] = text
        else:
            self.strings.pop(kind, None)
    return property(get, set_)


class Track:
    """A song on the iPod (mhit and its mhods)."""

    def __init__(self, hdr: bytearray | None = None, dbid: int = 0):
        if hdr is None:
            hdr = bytearray(MHIT_HLEN)
            hdr[0:4] = b"mhit"
            wr32(hdr, 0x04, MHIT_HLEN)
            wr32(hdr, 0x14, 1)                    # visible
            hdr[0xA4] = 2                         # no artwork
            hdr[0xB2] = 1                         # not marked unplayed
            wr32(hdr, 0xD0, MEDIATYPE_AUDIO)
            wr64(hdr, 0x134, 0x808080808080)
            wr32(hdr, 0x168, 1)
            wr64(hdr, 0x70, dbid)
            wr64(hdr, 0xA8, dbid)
        self.hdr = hdr
        self.strings: dict[int, str] = {}
        self.raw_mhods: list[bytes] = []          # kept as they were (chapters, podcast URLs...)
        # From "Play Counts" (as libgpod): plays and skips on the iPod since the last sync.
        self.recent_playcount = 0
        self.recent_skipcount = 0

    @property
    def contents(self):
        """The track itself (libgpod gives pointers; the sync code reads .contents)."""
        return self

    @property
    def samplerate(self) -> int:
        return rd16(self.hdr, 0x3E)

    @samplerate.setter
    def samplerate(self, hz) -> None:
        hz = max(0, min(int(hz or 0), 0xFFFF))
        wr16(self.hdr, 0x3C, 0)
        wr16(self.hdr, 0x3E, hz)
        wrf(self.hdr, 0x88, hz)

    @property
    def samplerate2(self) -> float:
        return rdf(self.hdr, 0x88)

    @property
    def kind(self) -> str:
        media = self.mediatype
        if media & (MEDIATYPE_MOVIE | MEDIATYPE_MUSICVIDEO | MEDIATYPE_TVSHOW):
            return "video"
        if media & MEDIATYPE_AUDIOBOOK:
            return "audiobook"
        if media & MEDIATYPE_PODCAST:
            return "podcast"
        return "music"

    def set_artwork(self, image_id: int, original_size: int) -> None:
        self.artwork_count = 1
        self.artwork_size = original_size
        self.has_artwork = 1
        self.mhii_link = image_id

    def clear_artwork(self) -> None:
        self.artwork_count = 0
        self.artwork_size = 0
        self.has_artwork = 2
        self.mhii_link = 0

    def __repr__(self) -> str:
        return f"<Track {self.dbid:016X} {self.title!r}>"


for _name, _spec in FIELDS.items():
    setattr(Track, _name, _number_field(*_spec))
for _name, _kind in STRINGS.items():
    setattr(Track, _name, _string_field(_kind))


class Playlist:
    """A playlist on the iPod (mhyp). items: its Track objects, in order."""

    def __init__(self, name: str = "", playlist_id: int = 0, master: bool = False,
                 hdr: bytearray | None = None):
        if hdr is None:
            hdr = bytearray(MHYP_HLEN)
            hdr[0:4] = b"mhyp"
            wr32(hdr, 0x04, MHYP_HLEN)
            hdr[0x14] = 1 if master else 0
            wr32(hdr, 0x18, unix_to_mac(int(time.time())))
            wr64(hdr, 0x1C, playlist_id or random_id())
            wr16(hdr, 0x28, 1)
        self.hdr = hdr
        self.name = name
        self.items: list[Track] = []
        self.spl_mhods: list[bytes] = []          # a smart playlist's settings, kept as they were

    @property
    def contents(self):
        return self

    @property
    def id(self) -> int:
        return rd64(self.hdr, 0x1C)

    @property
    def is_master(self) -> bool:
        return self.hdr[0x14] == 1

    @property
    def is_podcasts(self) -> bool:
        return rd16(self.hdr, 0x2A) == 1

    def set_podcasts(self) -> None:
        wr16(self.hdr, 0x2A, 1)

    @property
    def is_smart(self) -> bool:
        return bool(self.spl_mhods)

    def __repr__(self) -> str:
        return f"<Playlist {self.name!r} {len(self.items)} songs>"


def random_id() -> int:
    while True:
        value = int.from_bytes(os.urandom(8), "little")
        if value:
            return value


# ---- Reading ------------------------------------------------------------------------------

class ITunesDB:
    """The whole song list. playlists[0] is always the master playlist."""

    def __init__(self, header: bytearray, tracks: list[Track], playlists: list[Playlist],
                 preserved: list[tuple[int, bytes]]):
        self.header = header
        self.tracks = tracks
        self.playlists = playlists
        self.preserved = preserved                # mhsd 5 and 9, written back as they were

    @classmethod
    def new_empty(cls, name: str = "iPod") -> ITunesDB:
        h = bytearray(MHBD_HLEN)
        h[0:4] = b"mhbd"
        wr32(h, 0x04, MHBD_HLEN)
        wr32(h, 0x0C, 1)
        wr32(h, 0x10, 0x30)
        wr64(h, 0x18, random_id())
        wr16(h, 0x20, 2)                          # platform: Windows (FAT32)
        wr16(h, 0x22, 0x0263)                     # as iTunes 9
        wr64(h, 0x24, random_id())
        h[0x46:0x48] = b"en"
        wr64(h, 0x48, random_id())
        return cls(h, [], [Playlist(name or "iPod", master=True)], [])

    @property
    def hashing_scheme(self) -> int:
        return rd16(self.header, 0x30)

    @property
    def master(self) -> Playlist:
        return self.playlists[0]

    def new_dbid(self) -> int:
        taken = {t.dbid for t in self.tracks}
        while True:
            value = random_id()
            if value not in taken:
                return value

    @classmethod
    def parse(cls, data: bytes) -> ITunesDB:
        data = memoryview(bytes(data))
        if len(data) < 0x20 or bytes(data[0:4]) != b"mhbd":
            raise Corrupt("this isn't an iTunesDB (no mhbd header)")
        if rd32(data, 0x0C) > 2:
            raise Corrupt("this iTunesDB is written in a way LuxTunes can't read")
        hlen = rd32(data, 4)
        if hlen < 0x68 or hlen > len(data):
            raise Corrupt("the iTunesDB header has a bad length")
        header = bytearray(data[:hlen])
        if len(header) < MHBD_HLEN:
            header.extend(bytes(MHBD_HLEN - len(header)))
        tracks: list[Track] = []
        lists: dict[int, memoryview] = {}
        preserved = []
        pos = hlen
        for _ in range(rd32(data, 0x14)):
            section = chunk(data, pos, b"mhsd")
            body = section[rd32(section, 4):]
            kind = rd32(section, 0x0C)
            if kind == 1:
                tracks = _parse_tracks(body)
            elif kind in (2, 3):
                lists[kind] = body
            elif kind in (5, 9):
                preserved.append((kind, bytes(section)))
            pos += len(section)
        by_id = {}
        for track in tracks:
            by_id.setdefault(track.id, track)
        body = lists.get(2) if 2 in lists else lists.get(3)
        playlists = _parse_playlists(body, by_id) if body is not None else []
        masters = [p for p in playlists if p.is_master]
        if masters:
            playlists.remove(masters[0])
            playlists.insert(0, masters[0])
        else:
            playlists.insert(0, Playlist("iPod", master=True))
        return cls(header, tracks, playlists, preserved)

    # ---- Writing --------------------------------------------------------------------------

    def serialize(self) -> bytes:
        """The file's bytes, unsigned (the signature is added afterwards)."""
        self._prepare()
        out = bytearray(self.header)
        wr32(out, 0x04, len(self.header))
        if rd32(out, 0x10) < 0x30:
            wr32(out, 0x10, 0x30)
        wr16(out, 0x30, 0)                        # signatures: cleared, added again if needed
        out[0x58:0x6C] = bytes(20)
        wr16(out, 0x70, 0)
        out[0x72:0xA0] = bytes(0xA0 - 0x72)
        if len(out) >= 0xAB + 57:
            out[0xAB:0xAB + 57] = bytes(57)
        sections = [_mhsd(1, self._write_tracks())]
        grouped, flat = self._write_playlists()
        sections.append(_mhsd(3, grouped))
        sections.append(_mhsd(2, flat))
        albums, artists = self._write_albums_artists()
        sections.append(_mhsd(4, albums))
        sections.append(_mhsd(8, artists))
        sections.append(_mhsd(6, _list_header(b"mhlt", 0)))
        sections.append(_mhsd(10, _list_header(b"mhlt", 0)))
        kept = dict(self.preserved)
        sections.append(kept.get(5) or _mhsd(5, _list_header(b"mhlp", 0)))
        if 9 in kept:
            sections.append(kept[9])
        for section in sections:
            out += section
        wr32(out, 0x08, len(out))
        wr32(out, 0x14, len(sections))
        return bytes(out)

    def _prepare(self) -> None:
        """Number the songs, fill the master playlist, give albums and artists ids."""
        self.master.items = list(self.tracks)
        present = {id(t) for t in self.tracks}
        for playlist in self.playlists[1:]:
            playlist.items = [t for t in playlist.items if id(t) in present]
        id24 = rd64(self.header, 0x24)
        albums: dict = {}
        artists: dict = {}
        composers: dict = {}
        for i, t in enumerate(self.tracks):
            t.id = FIRST_TRACK_ID + i
            album = t.album
            t.album_id = albums.setdefault((album, t.albumartist or t.artist or ""),
                                           len(albums) + 1) if album else 0
            t.artist_id = artists.setdefault(t.artist, len(artists) + 1) if t.artist else 0
            t.composer_id = composers.setdefault(t.composer, len(composers) + 1) \
                if t.composer else 0
            wr64(t.hdr, 0x124, id24)
            wr32(t.hdr, 0x12C, t.size)

    def _write_tracks(self) -> bytes:
        out = bytearray(_list_header(b"mhlt", len(self.tracks)))
        for t in self.tracks:
            start = len(out)
            hdr = bytearray(t.hdr)
            wr32(hdr, 0x04, len(hdr))
            out += hdr
            count = 0
            for kind in TRACK_STRING_ORDER:
                text = t.strings.get(kind)
                if text:
                    out += string_mhod(kind, text)
                    count += 1
            for raw in t.raw_mhods:
                out += raw
                count += 1
            wr32(out, start + 0x08, len(out) - start)
            wr32(out, start + 0x0C, count)
        return bytes(out)

    def _write_playlists(self) -> tuple[bytes, bytes]:
        """(for mhsd 3, for mhsd 2): only the Podcasts playlist differs (mhsd 3
        groups its episodes under a heading per podcast, as libgpod does)."""
        grouped = bytearray(_list_header(b"mhlp", len(self.playlists)))
        flat = bytearray(grouped)
        for playlist in self.playlists:
            hdr = bytearray(playlist.hdr)
            wr32(hdr, 0x04, len(hdr))
            wr16(hdr, 0x28, 1)
            body = hdr + string_mhod(MHOD_TITLE, playlist.name) + long_playlist_mhod()
            count = 2
            if playlist.is_master and self.tracks:
                for sort_type, order, letters in self._library_indices():
                    body += mhod52(sort_type, order) + mhod53(sort_type, letters)
                    count += 2
            for raw in playlist.spl_mhods:
                body += raw
                count += 1
            wr32(body, 0x0C, count)
            items = bytearray()
            for position, t in enumerate(playlist.items):
                items += mhip(t.id, position)
            flat += _finish_playlist(body, items, len(playlist.items))
            if playlist.is_podcasts:
                group_items, n = self._podcast_groups(playlist.items)
                grouped += _finish_playlist(body, group_items, n)
            else:
                grouped += _finish_playlist(body, items, len(playlist.items))
        return bytes(grouped), bytes(flat)

    def _podcast_groups(self, items: list[Track]) -> tuple[bytes, int]:
        groups: list[tuple[str, list[int]]] = []
        for t in items:
            album = t.album or ""
            for name, ids in groups:
                if name == album:
                    ids.append(t.id)
                    break
            else:
                groups.append((album, [t.id]))
        next_id = FIRST_TRACK_ID + len(self.tracks) + 1
        out = bytearray()
        n = 0
        for album, ids in groups:
            group_id = next_id
            next_id += 1
            out += podcast_group_mhip(group_id, album)
            n += 1
            for track_id in ids:
                entry = bytearray(mhip(track_id, next_id))
                wr32(entry, 0x14, next_id)
                wr32(entry, 0x20, group_id)
                next_id += 1
                out += entry
                n += 1
        return bytes(out), n

    def _library_indices(self) -> list:
        """The master playlist's sorted indexes (mhod 52) and jump tables (mhod 53):
        what the iPod's Songs, Artists, Albums, Genres and Composers menus use."""
        def pick(t, sort_kind, kind):
            return t.strings.get(sort_kind) or t.strings.get(kind) or ""
        keys = []
        for t in self.tracks:
            keys.append((pick(t, MHOD_SORT_TITLE, MHOD_TITLE),
                         pick(t, MHOD_SORT_ARTIST, MHOD_ARTIST),
                         pick(t, MHOD_SORT_ALBUM, MHOD_ALBUM),
                         t.strings.get(MHOD_GENRE) or "",
                         pick(t, MHOD_SORT_COMPOSER, MHOD_COMPOSER),
                         t.cd_nr, t.track_nr))
        folded = [tuple(s.lower() for s in k[:5]) for k in keys]
        specs = ((0x03, (0,), 0), (0x05, (1, 2), 1), (0x04, (2,), 2), (0x07, (3, 1, 2), 3),
                 (0x12, (4, 2), 4))
        result = []
        for sort_type, fields, letter_field in specs:
            if sort_type == 0x03:
                def key(i, fields=fields):
                    return tuple(folded[i][f] for f in fields) + (folded[i][0],)
            else:
                def key(i, fields=fields):
                    return tuple(folded[i][f] for f in fields) + (keys[i][5], keys[i][6],
                                                                   folded[i][0])
            order = sorted(range(len(keys)), key=key)
            letters: list[list[int]] = []
            for position, index in enumerate(order):
                letter = jump_letter(keys[index][letter_field])
                if letters and letters[-1][0] == letter:
                    letters[-1][2] += 1
                else:
                    letters.append([letter, position, 1])
            result.append((sort_type, order, letters))
        return result

    def _write_albums_artists(self) -> tuple[bytes, bytes]:
        albums: dict[int, Track] = {}
        artists: dict[int, Track] = {}
        for t in self.tracks:
            if t.album_id:
                albums.setdefault(t.album_id, t)
            if t.artist_id:
                artists.setdefault(t.artist_id, t)
        mhla = bytearray(_list_header(b"mhla", len(albums)))
        for album_id, t in sorted(albums.items()):
            start = len(mhla)
            h = bytearray(88)
            h[0:4] = b"mhia"
            wr32(h, 4, 88)
            wr32(h, 0x10, album_id)
            wr64(h, 0x14, random_id())
            wr32(h, 0x1C, 2)
            mhla += h
            n = 0
            for kind, text in ((200, t.album), (201, t.albumartist or t.artist),
                               (202, t.sort_albumartist or t.sort_artist)):
                if text:
                    mhla += string_mhod(kind, text)
                    n += 1
            wr32(mhla, start + 8, len(mhla) - start)
            wr32(mhla, start + 12, n)
        mhli = bytearray(_list_header(b"mhli", len(artists)))
        for artist_id, t in sorted(artists.items()):
            start = len(mhli)
            h = bytearray(80)
            h[0:4] = b"mhii"
            wr32(h, 4, 80)
            wr32(h, 0x10, artist_id)
            wr64(h, 0x14, random_id())
            wr32(h, 0x1C, 2)
            mhli += h
            mhli += string_mhod(300, t.artist)
            wr32(mhli, start + 8, len(mhli) - start)
            wr32(mhli, start + 12, 1)
        return bytes(mhla), bytes(mhli)


def _parse_tracks(body) -> list[Track]:
    if len(body) < 12 or bytes(body[0:4]) != b"mhlt":
        raise Corrupt("the song list (mhlt) is missing")
    count = rd32(body, 8)
    pos = rd32(body, 4)
    tracks = []
    for _ in range(count):
        record = chunk(body, pos, b"mhit")
        hlen = rd32(record, 4)
        hdr = bytearray(record[:hlen] if hlen <= len(record) else record)
        if len(hdr) < MHIT_HLEN:
            hdr.extend(bytes(MHIT_HLEN - len(hdr)))
        t = Track(hdr)
        at = hlen
        for _ in range(rd32(record, 0x0C)):
            m = chunk(record, at, b"mhod")
            kind = rd32(m, 0x0C)
            if is_string_mhod(kind):
                text = decode_string_mhod(m)
                if text is not None:
                    t.strings[kind] = text
            else:
                t.raw_mhods.append(bytes(m))
            at += len(m)
        pos += len(record)
        tracks.append(t)
    return tracks


def _parse_playlists(body, by_id: dict[int, Track]) -> list[Playlist]:
    if len(body) < 12 or bytes(body[0:4]) != b"mhlp":
        raise Corrupt("the playlists (mhlp) are missing")
    pos = rd32(body, 4)
    out = []
    for _ in range(rd32(body, 8)):
        record = chunk(body, pos, b"mhyp")
        hlen = rd32(record, 4)
        hdr = bytearray(record[:hlen] if hlen <= len(record) else record)
        if len(hdr) < MHYP_HLEN:
            hdr.extend(bytes(MHYP_HLEN - len(hdr)))
        playlist = Playlist(hdr=hdr)
        at = hlen
        while at + 12 <= len(record):
            tag = bytes(record[at:at + 4])
            length = rd32(record, at + 8)
            if length < 12 or at + length > len(record):
                break
            sub = record[at:at + length]
            if tag == b"mhod":
                kind = rd32(sub, 0x0C)
                if kind == MHOD_TITLE:
                    playlist.name = decode_string_mhod(sub) or ""
                elif kind in (MHOD_SPLPREF, MHOD_SPLRULES):
                    playlist.spl_mhods.append(bytes(sub))
            elif tag == b"mhip" and rd32(sub, 0x10) != 0x100:   # (0x100: a podcast heading)
                t = by_id.get(rd32(sub, 0x18))
                if t is not None:
                    playlist.items.append(t)
            at += length
        pos += len(record)
        out.append(playlist)
    return out


def decode_string_mhod(m) -> str | None:
    hlen = rd32(m, 4)
    encoding = rd32(m, hlen)
    length = rd32(m, hlen + 4)
    raw = bytes(m[hlen + 16:hlen + 16 + length])
    if len(raw) < length:
        return None
    if encoding == 2:
        return raw.decode("utf-8", "replace")
    return raw[:len(raw) // 2 * 2].decode("utf-16-le", "replace")


# ---- Records, as libgpod writes them ------------------------------------------------------

def _list_header(tag: bytes, count: int) -> bytes:
    h = bytearray(92)
    h[0:4] = tag
    wr32(h, 4, 92)
    wr32(h, 8, count)
    return bytes(h)


def _mhsd(kind: int, body: bytes) -> bytes:
    h = bytearray(96)
    h[0:4] = b"mhsd"
    wr32(h, 4, 96)
    wr32(h, 8, 96 + len(body))
    wr32(h, 12, kind)
    return bytes(h) + body


def _finish_playlist(body: bytes, items: bytes, count: int) -> bytes:
    out = bytearray(body) + items
    wr32(out, 0x08, len(out))
    wr32(out, 0x10, count)
    return bytes(out)


def string_mhod(kind: int, text: str) -> bytes:
    data = text.encode("utf-16-le")
    m = bytearray(40)
    m[0:4] = b"mhod"
    wr32(m, 4, 24)
    wr32(m, 8, 40 + len(data))
    wr32(m, 12, kind)
    wr32(m, 24, 1)                                # position / UTF-16
    wr32(m, 28, len(data))
    wr32(m, 32, 1)
    return bytes(m) + data


def long_playlist_mhod() -> bytes:
    """libgpod's mk_long_mhod_id_playlist: iTunes' view settings (fixed)."""
    words = [0x18, 0x288, MHOD_PLAYLIST] + [0] * 6 + [0x010084, 0x05, 0x09, 0x03, 0x120001]
    for value in (0xc80002, 0x3c000d, 0x7d0004, 0x7d0003, 0x640008):
        words += [0, 0, 0, value]
    words += [0, 0, 0, 0x640017, 0x01, 0, 0, 0x500014, 0x01, 0, 0, 0x7d0015, 0x01]
    words += [0] * 114
    out = b"mhod" + b"".join(w.to_bytes(4, "little") for w in words)
    assert len(out) == 0x288
    return out


def mhod52(sort_type: int, order: list[int]) -> bytes:
    m = bytearray(72)
    m[0:4] = b"mhod"
    wr32(m, 4, 24)
    wr32(m, 8, 72 + 4 * len(order))
    wr32(m, 12, MHOD_LIBINDEX)
    wr32(m, 24, sort_type)
    wr32(m, 28, len(order))
    return bytes(m) + b"".join(i.to_bytes(4, "little") for i in order)


def mhod53(sort_type: int, letters) -> bytes:
    m = bytearray(40)
    m[0:4] = b"mhod"
    wr32(m, 4, 24)
    wr32(m, 8, 40 + 12 * len(letters))
    wr32(m, 12, MHOD_LIBJUMP)
    wr32(m, 24, sort_type)
    wr32(m, 28, len(letters))
    for letter, start, count in letters:
        m += letter.to_bytes(2, "little") + bytes(2) + start.to_bytes(4, "little") \
            + count.to_bytes(4, "little")
    return bytes(m)


def jump_letter(text: str) -> int:
    """The jump table's letter: the first letter or digit, upper case (digits: '0')."""
    for ch in text:
        if ch.isalnum():
            if ch.isdigit():
                return ord("0")
            up = ch.upper()[:1] or ch
            code = ord(up)
            return code if code <= 0xFFFF else ord("0")
    return ord("0")


def mhip(track_id: int, position: int) -> bytes:
    m = bytearray(76 + 44)
    m[0:4] = b"mhip"
    wr32(m, 4, 76)
    wr32(m, 8, 76 + 44)
    wr32(m, 12, 1)
    wr32(m, 24, track_id)
    m[76:80] = b"mhod"
    wr32(m, 80, 24)
    wr32(m, 84, 44)
    wr32(m, 88, MHOD_PLAYLIST)
    wr32(m, 100, position)
    return bytes(m)


def podcast_group_mhip(group_id: int, album: str) -> bytes:
    title = string_mhod(MHOD_TITLE, album)
    m = bytearray(76)
    m[0:4] = b"mhip"
    wr32(m, 4, 76)
    wr32(m, 12, 1)
    wr32(m, 16, 0x100)                            # a podcast's heading
    wr32(m, 20, group_id)
    m += title
    wr32(m, 8, len(m))
    return bytes(m)


# ---- What the iPod wrote since the last sync --------------------------------------------

def merge_play_counts(db: ITunesDB, data: bytes) -> int:
    """'Play Counts': plays, skips, ratings and bookmarks made on the iPod, one
    entry per song in the database's order. Added as libgpod does: playcount
    grows by the plays and recent_playcount holds them. Returns how many songs."""
    if len(data) < 16 or data[0:4] != b"mhdp":
        return 0
    hlen, size, count = rd32(data, 4), rd32(data, 8), rd32(data, 12)
    if size < 0x0C:
        return 0
    merged = 0
    for i, t in enumerate(db.tracks[:count]):
        at = hlen + i * size
        if at + size > len(data):
            break
        plays, last, bookmark = rd32(data, at), rd32(data, at + 4), rd32(data, at + 8)
        if size >= 0x10:
            rating = rd32(data, at + 12)
            if rating != 0xFFFFFFFF and rating <= 100:
                t.rating = rating
        if last:
            t.hdr[0x58:0x5C] = last.to_bytes(4, "little")
        if bookmark:
            t.bookmark_time = bookmark
        if plays:
            t.playcount = t.playcount + plays
            t.recent_playcount = plays
        if size >= 0x1C:
            skips = rd32(data, at + 20)
            if skips:
                t.skipcount = t.skipcount + skips
                t.recent_skipcount = skips
            skipped = rd32(data, at + 24)
            if skipped:
                t.hdr[0xA0:0xA4] = skipped.to_bytes(4, "little")
        merged += 1
    return merged


def merge_play_counts_plist(db: ITunesDB, data: bytes) -> int:
    """PlayCounts.plist (what newer nanos write instead), matched by persistent ID."""
    import plistlib
    try:
        value = plistlib.loads(data)
    except Exception:
        return 0
    entries = value.get("tracks") if isinstance(value, dict) else None
    if not isinstance(entries, list):
        return 0
    by_dbid = {t.dbid: t for t in db.tracks}

    def number(entry, key):
        v = entry.get(key, 0)
        return int(v) & 0xFFFFFFFFFFFFFFFF if isinstance(v, int) else 0
    merged = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        t = by_dbid.get(number(entry, "persistentID"))
        if t is None:
            continue
        plays = number(entry, "playCount")
        if plays:
            t.playcount = t.playcount + plays
            t.recent_playcount = plays
        played = number(entry, "playMacOSDate")
        if played:
            t.hdr[0x58:0x5C] = (played & 0xFFFFFFFF).to_bytes(4, "little")
        skips = number(entry, "skipCount")
        if skips:
            t.skipcount = t.skipcount + skips
            t.recent_skipcount = skips
        skipped = number(entry, "skipMacOSDate")
        if skipped:
            t.hdr[0xA0:0xA4] = (skipped & 0xFFFFFFFF).to_bytes(4, "little")
        rating = number(entry, "userRating")
        if 1 <= rating <= 100:
            t.rating = rating
        bookmark = number(entry, "bookmarkTimeInMS")
        if bookmark:
            t.bookmark_time = bookmark
        merged += 1
    return merged


def parse_otg(db: ITunesDB, data: bytes) -> list[Track]:
    """An On-The-Go playlist (made on the iPod): its songs."""
    if len(data) < 16 or data[0:4] != b"mhpo":
        return []
    hlen, size, count = rd32(data, 4), rd32(data, 8), rd32(data, 12)
    if size < 4:
        return []
    count = min(count, max(0, len(data) - hlen) // size)
    out = []
    for i in range(count):
        index = rd32(data, hlen + i * size)
        if index < len(db.tracks):
            out.append(db.tracks[index])
    return out
