"""Smart playlists: their rules and which songs they hold.

A smart playlist is kept as two binary blocks, "Smart Info" and "Smart
Criteria": the format an imported iTunes library XML uses, so imported
smart playlists keep working exactly as they were.

Smart Info (112 bytes): live updating, match rules, limit on, limit type,
limit sort ("selected by"), limit value, match only checked items, and the
"least …" flag.

Smart Criteria: "SLst", version, number of rules, all (0) / any (1), then
the rules. Each rule: field id, operator, 44 spare bytes, data length, data.
Text values are UTF-16 (big-endian); numbers and dates are 68 bytes:
from-value, from-date, from-units, to-value, to-date, to-units (64-bit
each), then 20 spare bytes. "In the last 2 weeks" is from-value = a marker
(0x2dae2dae2dae2dae), from-date = -2, from-units = 604800 seconds. A nested
group (the "…" button) is a rule with field 0 whose data is another "SLst".
The field and operator numbers match libgpod's (itdb.h), which uses the
same ones for iPod smart playlists; they were checked against six real
iTunes smart playlists byte for byte.

No Qt in here.
"""

from __future__ import annotations

import random
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---- Fields ------------------------------------------------------------------------

TEXT, NUMBER, DATE, RATING, TIME, SIZE, BOOL, MEDIA, PLAYLIST = (
    "text", "number", "date", "rating", "time", "size", "bool", "media", "playlist")


@dataclass(frozen=True)
class Field:
    id: int
    label: str
    kind: str
    column: str | None          # where the song keeps it (None: LuxTunes has no such data)


FIELDS = {f.id: f for f in [
    Field(0x03, "Album", TEXT, "album"),
    Field(0x47, "Album Artist", TEXT, "album_artist"),
    Field(0x5a, "Album Rating", RATING, "album_rating"),
    Field(0x04, "Artist", TEXT, "artist"),
    Field(0x05, "Bit Rate", NUMBER, "bit_rate"),
    Field(0x23, "BPM", NUMBER, "bpm"),
    Field(0x0e, "Comments", TEXT, "comments"),
    Field(0x1f, "Compilation", BOOL, "compilation"),
    Field(0x12, "Composer", TEXT, "composer"),
    Field(0x10, "Date Added", DATE, "date_added"),
    Field(0x0a, "Date Modified", DATE, "date_modified"),
    Field(0x18, "Disc Number", NUMBER, "disc_number"),
    Field(0x08, "Genre", TEXT, "genre"),
    Field(0x27, "Grouping", TEXT, "grouping"),
    Field(0x09, "Kind", TEXT, "kind"),
    Field(0x17, "Last Played", DATE, "play_date"),
    Field(0x45, "Last Skipped", DATE, "skip_date"),
    Field(0x3c, "Media Kind", MEDIA, "media_kind"),
    Field(0x02, "Name", TEXT, "name"),
    Field(0x28, "Playlist", PLAYLIST, None),
    Field(0x16, "Plays", NUMBER, "play_count"),
    Field(0x39, "Podcast", BOOL, None),
    Field(0x29, "Purchased", BOOL, "purchased"),
    Field(0x19, "Rating", RATING, "rating"),
    Field(0x06, "Sample Rate", NUMBER, "sample_rate"),
    Field(0x3f, "Season", NUMBER, "season"),
    Field(0x3e, "Show", TEXT, "show"),
    Field(0x0c, "Size", SIZE, "size"),
    Field(0x44, "Skips", NUMBER, "skip_count"),
    Field(0x4f, "Sort Album", TEXT, "sort_album"),
    Field(0x51, "Sort Album Artist", TEXT, "sort_album_artist"),
    Field(0x50, "Sort Artist", TEXT, "sort_artist"),
    Field(0x52, "Sort Composer", TEXT, "sort_composer"),
    Field(0x4e, "Sort Name", TEXT, "sort_name"),
    Field(0x0d, "Time", TIME, "total_time"),
    Field(0x0b, "Track Number", NUMBER, "track_number"),
    Field(0x07, "Year", NUMBER, "year"),
]}
GROUP_FIELD = 0

# ---- Operators (with the words the editor shows) -----------------------------------------

IS, IS_NOT = 0x00000001, 0x02000001
GREATER, LESS, RANGE = 0x00000010, 0x00000040, 0x00000100
IN_LAST, NOT_IN_LAST = 0x00000200, 0x02000200
BIT_IS, BIT_IS_NOT = 0x00000400, 0x02000400
S_IS, S_CONTAINS, S_STARTS, S_ENDS = 0x01000001, 0x01000002, 0x01000004, 0x01000008
S_IS_NOT, S_NOT_CONTAINS = 0x03000001, 0x03000002

OPERATORS = {
    TEXT: [(S_CONTAINS, "contains"), (S_NOT_CONTAINS, "does not contain"), (S_IS, "is"),
           (S_IS_NOT, "is not"), (S_STARTS, "starts with"), (S_ENDS, "ends with")],
    NUMBER: [(IS, "is"), (IS_NOT, "is not"), (GREATER, "is greater than"), (LESS, "is less than"),
             (RANGE, "is in the range")],
    DATE: [(IS, "is"), (IS_NOT, "is not"), (GREATER, "is after"), (LESS, "is before"),
           (IN_LAST, "is in the last"), (NOT_IN_LAST, "is not in the last"),
           (RANGE, "is in the range")],
    BOOL: [(IS, "is true"), (IS_NOT, "is false")],
    MEDIA: [(BIT_IS, "is"), (BIT_IS_NOT, "is not")],
    PLAYLIST: [(IS, "is"), (IS_NOT, "is not")],
}
OPERATORS[RATING] = OPERATORS[TIME] = OPERATORS[SIZE] = OPERATORS[NUMBER]

MEDIA_KINDS = [(0x01, "Music"), (0x02, "Movie"), (0x04, "Podcast"), (0x08, "Audiobook"),
               (0x20, "Music Video"), (0x40, "TV Show")]
LUX_MEDIA = {"music": 0x01, "movie": 0x02, "tvshow": 0x40}

DATE_MARKER = 0x2DAE2DAE2DAE2DAE
DAY, WEEK, MONTH = 86400, 604800, 2628000
DATE_UNITS = [(DAY, "days"), (WEEK, "weeks"), (MONTH, "months")]
MAC_EPOCH = 2082844800             # seconds from 1904-01-01 (the format's dates) to 1970-01-01

# ---- Limits --------------------------------------------------------------------------

LIMIT_MINUTES, LIMIT_MB, LIMIT_ITEMS, LIMIT_HOURS, LIMIT_GB = 1, 2, 3, 4, 5
LIMIT_TYPES = [(LIMIT_ITEMS, "items"), (LIMIT_MINUTES, "minutes"), (LIMIT_HOURS, "hours"),
               (LIMIT_MB, "MB"), (LIMIT_GB, "GB")]
# "selected by": (code, reversed, label). The "least …" ones are the "most …"
# code with the reverse flag, as the format stores them.
SORT_RANDOM, SORT_NAME, SORT_ALBUM, SORT_ARTIST, SORT_GENRE = 0x02, 0x05, 0x06, 0x07, 0x09
SORT_ADDED, SORT_PLAYS, SORT_PLAYED, SORT_RATING = 0x15, 0x19, 0x1A, 0x1C
LIMIT_SORTS = [
    (SORT_RANDOM, False, "random"), (SORT_ALBUM, False, "album"), (SORT_ARTIST, False, "artist"),
    (SORT_GENRE, False, "genre"), (SORT_NAME, False, "name"),
    (SORT_RATING, False, "highest rating"), (SORT_RATING, True, "lowest rating"),
    (SORT_PLAYED, False, "most recently played"), (SORT_PLAYED, True, "least recently played"),
    (SORT_PLAYS, False, "most often played"), (SORT_PLAYS, True, "least often played"),
    (SORT_ADDED, False, "most recently added"), (SORT_ADDED, True, "least recently added"),
]


# ---- The rules ------------------------------------------------------------------------

@dataclass
class Rule:
    field: int
    op: int
    value: object = None          # text, number, unix time, rating 0-100, ms, bytes, mask...
    value2: object = None         # the end of "is in the range"
    amount: int = 0               # "in the last <amount> <units>"
    units: int = WEEK
    raw: bytes | None = None      # a rule LuxTunes doesn't understand: kept exactly

    @property
    def known(self) -> bool:
        return self.raw is None and self.field in FIELDS

    @property
    def kind(self) -> str | None:
        f = FIELDS.get(self.field)
        return f.kind if f else None


@dataclass
class Group:
    match_any: bool = False
    rules: list = field(default_factory=list)       # Rule or Group


@dataclass
class Info:
    live: bool = True
    use_rules: bool = True
    limit: bool = False
    limit_type: int = LIMIT_ITEMS
    limit_sort: int = SORT_RANDOM
    limit_value: int = 25
    checked_only: bool = False
    reverse: bool = False


@dataclass
class SmartPlaylist:
    info: Info = field(default_factory=Info)
    rules: Group = field(default_factory=Group)

    @property
    def understood(self) -> bool:
        """Every rule is one LuxTunes knows (else the playlist keeps its last songs)."""
        def ok(group):
            return all(ok(r) if isinstance(r, Group) else r.known for r in group.rules)
        return ok(self.rules)


def default_playlist() -> SmartPlaylist:
    """What File → New Smart Playlist… starts with: Artist contains ""."""
    return SmartPlaylist(Info(), Group(False, [Rule(0x04, S_CONTAINS, "")]))


# ---- Reading and writing the format ------------------------------------------------

class SmartFormatError(ValueError):
    pass


INFO_SIZE = 112
HEADER_SIZE = 136
RULE_PAD = 44
NUMBER_DATA = struct.Struct(">QqQQqQ20x")       # 68 bytes


def decode_info(data: bytes | None) -> Info:
    if not data or len(data) < 14:
        return Info()
    info = Info(live=bool(data[0]), use_rules=bool(data[1]), limit=bool(data[2]),
                limit_type=data[3] or LIMIT_ITEMS, limit_sort=data[7] or SORT_RANDOM,
                limit_value=struct.unpack(">I", data[8:12])[0], checked_only=bool(data[12]),
                reverse=bool(data[13]))
    return info


def encode_info(info: Info) -> bytes:
    data = bytearray(INFO_SIZE)
    data[0], data[1], data[2], data[3] = int(info.live), int(info.use_rules), int(info.limit), info.limit_type
    data[7] = info.limit_sort
    data[8:12] = struct.pack(">I", max(0, int(info.limit_value)))
    data[12], data[13] = int(info.checked_only), int(info.reverse)
    return bytes(data)


def _mac_to_unix(seconds: int) -> int:
    return seconds - MAC_EPOCH


def _unix_to_mac(seconds: int) -> int:
    return int(seconds) + MAC_EPOCH


def decode_criteria(data: bytes | None) -> Group:
    if not data:
        return Group()
    group, _ = _decode_group(data, 0)
    return group


def _decode_group(data: bytes, start: int) -> tuple[Group, int]:
    if data[start:start + 4] != b"SLst":
        raise SmartFormatError("not smart playlist rules")
    count, conjunction = struct.unpack(">II", data[start + 8:start + 16])
    group = Group(match_any=conjunction == 1)
    at = start + HEADER_SIZE
    for _ in range(count):
        if at + 56 > len(data):
            raise SmartFormatError("the rules end too soon")
        field_id, op = struct.unpack(">II", data[at:at + 8])
        length = struct.unpack(">I", data[at + 52:at + 56])[0]
        body = data[at + 56:at + 56 + length]
        whole = data[at:at + 56 + length]
        at += 56 + length
        group.rules.append(_decode_rule(field_id, op, body, whole))
    return group, at


def _decode_rule(field_id: int, op: int, body: bytes, whole: bytes):
    if field_id == GROUP_FIELD:
        try:
            sub, _ = _decode_group(body, 0)
            return sub
        except (SmartFormatError, struct.error):
            return Rule(field_id, op, raw=whole)
    f = FIELDS.get(field_id)
    if f is None:
        return Rule(field_id, op, raw=whole)
    if f.kind == TEXT:
        return Rule(field_id, op, body.decode("utf-16-be", "replace"))
    if len(body) != NUMBER_DATA.size:
        return Rule(field_id, op, raw=whole)
    a, a_date, a_units, b, b_date, b_units = NUMBER_DATA.unpack(body)
    if f.kind == DATE:
        if a == DATE_MARKER:
            return Rule(field_id, op, amount=-a_date, units=a_units or DAY)
        return Rule(field_id, op, _mac_to_unix(a), _mac_to_unix(b))
    if f.kind == PLAYLIST:
        return Rule(field_id, op, f"{a:016X}")
    return Rule(field_id, op, a, b if op & ~0x02000000 == RANGE else None)


def encode_criteria(group: Group) -> bytes:
    out = bytearray(b"SLst")
    out += struct.pack(">HHII", 1, 1, len(group.rules), 1 if group.match_any else 0)
    out += bytes(HEADER_SIZE - len(out))
    for rule in group.rules:
        out += _encode_rule(rule)
    return bytes(out)


def _encode_rule(rule) -> bytes:
    if isinstance(rule, Group):
        body = encode_criteria(rule)
        pad = bytearray(RULE_PAD)
        pad[0] = 1                                        # how a nested group is written
        return struct.pack(">II", GROUP_FIELD, IS) + bytes(pad) + struct.pack(">I", len(body)) + body
    if rule.raw is not None:
        return rule.raw
    kind = rule.kind
    if kind == TEXT:
        body = (rule.value or "").encode("utf-16-be")
    elif kind == DATE and rule.op in (IN_LAST, NOT_IN_LAST):
        body = NUMBER_DATA.pack(DATE_MARKER, -int(rule.amount), int(rule.units),
                                DATE_MARKER, 0, 1)
    elif kind == DATE:
        a = _unix_to_mac(rule.value or 0)
        b = _unix_to_mac(rule.value2 if rule.value2 is not None else (rule.value or 0))
        body = NUMBER_DATA.pack(a, 0, 1, b, 0, 1)
    elif kind == PLAYLIST:
        number = int(rule.value or "0", 16)
        body = NUMBER_DATA.pack(number, 0, 1, number, 0, 1)
    else:
        a = int(rule.value or 0)
        b = int(rule.value2) if rule.value2 is not None else a
        body = NUMBER_DATA.pack(a, 0, 1, b, 0, 1)
    return (struct.pack(">II", rule.field, rule.op) + bytes(RULE_PAD)
            + struct.pack(">I", len(body)) + body)


def decode(info: bytes | None, criteria: bytes | None) -> SmartPlaylist:
    return SmartPlaylist(decode_info(info), decode_criteria(criteria))


def encode(playlist: SmartPlaylist) -> tuple[bytes, bytes]:
    return encode_info(playlist.info), encode_criteria(playlist.rules)


# ---- Which songs match --------------------------------------------------------------------

def _unix(value) -> float | None:
    """A song's date (ISO text in the library) as seconds since 1970."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _song_value(track: dict, f: Field):
    if f.column is None:
        return None
    # Ratings count even when they come from the album (checked against the
    # imported "My Top Rated").
    return track.get(f.column)


class _Context:
    def __init__(self, conn, now: float, stack: tuple = ()):
        self.conn = conn
        self.now = now
        self.stack = stack
        self._members: dict[str, set[int]] = {}

    def members(self, persistent_id: str) -> set[int]:
        """The songs of the playlist a "Playlist is …" rule points to."""
        key = (persistent_id or "").upper()
        if key not in self._members:
            self._members[key] = playlist_members(self.conn, key, self.now, self.stack)
        return self._members[key]


def rule_matches(rule, track: dict, ctx: _Context) -> bool:
    if isinstance(rule, Group):
        return group_matches(rule, track, ctx)
    if not rule.known:
        return True
    f = FIELDS[rule.field]
    op = rule.op
    negate = bool(op & 0x02000000)
    if f.kind == PLAYLIST:
        inside = track["id"] in ctx.members(rule.value or "")
        return inside != negate
    if f.kind == TEXT:
        have = (str(_song_value(track, f) or "")).casefold()
        want = (rule.value or "").casefold()
        base = op & ~0x02000000
        if base == S_CONTAINS:
            hit = want in have
        elif base == S_STARTS:
            hit = have.startswith(want)
        elif base == S_ENDS:
            hit = have.endswith(want)
        else:
            hit = have == want
        return hit != negate
    if f.kind == MEDIA:
        mask = LUX_MEDIA.get(track.get("media_kind") or "music", 0)
        return bool(mask & int(rule.value or 0)) != negate
    if f.kind == BOOL:
        on = bool(_song_value(track, f))
        return on != negate if int(rule.value or 1) else on == negate
    if f.kind == DATE:
        when = _unix(_song_value(track, f))
        base = op & ~0x02000000
        if base == IN_LAST:
            hit = when is not None and when >= ctx.now - rule.amount * rule.units
        elif when is None:
            hit = False
        elif base == GREATER:
            hit = when > (rule.value or 0)
        elif base == LESS:
            hit = when < (rule.value or 0)
        else:                                    # is / is in the range: inclusive
            start = rule.value or 0
            end = rule.value2 if rule.value2 is not None else start
            hit = min(start, end) <= when <= max(start, end)
        return hit != negate
    value = _song_value(track, f) or 0
    base = op & ~0x02000000
    if base == GREATER:
        hit = value > int(rule.value or 0)
    elif base == LESS:
        hit = value < int(rule.value or 0)
    elif base == RANGE:
        low, high = sorted((int(rule.value or 0), int(rule.value2 if rule.value2 is not None else rule.value or 0)))
        hit = low <= value <= high
    else:
        hit = value == int(rule.value or 0)
    return hit != negate


def group_matches(group: Group, track: dict, ctx: _Context) -> bool:
    if not group.rules:
        return True
    results = (rule_matches(r, track, ctx) for r in group.rules)
    return any(results) if group.match_any else all(results)


def _limit_key(sort: int):
    if sort == SORT_NAME:
        return lambda t: (t.get("sort_name") or t.get("name") or "").casefold()
    if sort == SORT_ALBUM:
        return lambda t: ((t.get("sort_album") or t.get("album") or "").casefold(),
                          t.get("disc_number") or 0, t.get("track_number") or 0)
    if sort == SORT_ARTIST:
        return lambda t: (t.get("sort_artist") or t.get("artist") or "").casefold()
    if sort == SORT_GENRE:
        return lambda t: (t.get("genre") or "").casefold()
    if sort == SORT_ADDED:          # "most …" first: newest / most / highest
        return lambda t: -(_unix(t.get("date_added")) or 0)
    if sort == SORT_PLAYS:       # ties: the more recently played first (matches imported playlists)
        return lambda t: (-(t.get("play_count") or 0), -(_unix(t.get("play_date")) or 0))
    if sort == SORT_PLAYED:
        return lambda t: -(_unix(t.get("play_date")) or 0)
    if sort == SORT_RATING:
        return lambda t: -(0 if t.get("rating_computed") else (t.get("rating") or 0))
    return None


def _apply_limit(tracks: list[dict], info: Info, seed) -> list[dict]:
    if not info.limit:
        return tracks                                 # "selected by" only matters with a limit
    if info.limit_sort == SORT_RANDOM:
        tracks = list(tracks)
        random.Random(seed).shuffle(tracks)          # the same pick each time it's refreshed
    else:
        key = _limit_key(info.limit_sort)
        if key is not None:
            tracks = sorted(tracks, key=key, reverse=info.reverse)
    limit = max(0, int(info.limit_value))
    if info.limit_type == LIMIT_ITEMS:
        return tracks[:limit]
    size_of = {LIMIT_MINUTES: lambda t: (t.get("total_time") or 0) / 60000,
               LIMIT_HOURS: lambda t: (t.get("total_time") or 0) / 3600000,
               LIMIT_MB: lambda t: (t.get("size") or 0) / 1024 ** 2,
               LIMIT_GB: lambda t: (t.get("size") or 0) / 1024 ** 3}[info.limit_type]
    chosen, used = [], 0.0
    for t in tracks:
        amount = size_of(t)
        if used + amount > limit:
            continue                                  # skip what doesn't fit
        chosen.append(t)
        used += amount
    return chosen


def evaluate(conn: sqlite3.Connection, playlist: SmartPlaylist, tracks: list[dict] | None = None,
             now: float | None = None, seed=0, stack: tuple = ()) -> list[int]:
    """The ids of the songs this smart playlist holds, in its order."""
    from luxtunes.core import library
    if tracks is None:
        tracks = library.load_tracks(conn)
    ctx = _Context(conn, time.time() if now is None else now, stack)
    info = playlist.info
    chosen = [t for t in tracks
              if (not info.checked_only or t.get("enabled", 1))
              and (not info.use_rules or group_matches(playlist.rules, t, ctx))]
    return [t["id"] for t in _apply_limit(chosen, info, seed)]


def playlist_members(conn, persistent_id: str, now: float, stack: tuple = ()) -> set[int]:
    """Songs in any playlist (a folder's: all of its playlists'), for "Playlist is …"."""
    row = conn.execute("SELECT id, is_smart, is_folder, smart_info, smart_criteria, persistent_id "
                       "FROM playlists WHERE upper(persistent_id) = ?", (persistent_id,)).fetchone()
    if row is None or persistent_id in stack:          # missing, or a loop of playlists
        return set()
    if row["is_folder"]:
        members: set[int] = set()
        for child in conn.execute("SELECT persistent_id FROM playlists WHERE parent_persistent_id = ?",
                                  (row["persistent_id"],)):
            members |= playlist_members(conn, (child[0] or "").upper(), now, stack + (persistent_id,))
        return members
    if row["is_smart"] and row["smart_criteria"]:
        try:
            playlist = decode(row["smart_info"], row["smart_criteria"])
        except (SmartFormatError, struct.error):
            playlist = None
        if playlist is not None and playlist.understood:
            return set(evaluate(conn, playlist, now=now, seed=row["id"],
                                stack=stack + (persistent_id,)))
    return {r[0] for r in conn.execute("SELECT track_id FROM playlist_items WHERE playlist_id = ?",
                                       (row["id"],))}


# ---- Keeping smart playlists up to date ------------------------------------------------

def load(conn: sqlite3.Connection, playlist_id: int) -> SmartPlaylist | None:
    row = conn.execute("SELECT smart_info, smart_criteria FROM playlists WHERE id = ? AND is_smart",
                       (playlist_id,)).fetchone()
    if row is None:
        return None
    try:
        return decode(row[0], row[1])
    except (SmartFormatError, struct.error):
        return None


def save(conn: sqlite3.Connection, playlist_id: int, playlist: SmartPlaylist) -> None:
    info, criteria = encode(playlist)
    with conn:
        conn.execute("UPDATE playlists SET is_smart = 1, smart_info = ?, smart_criteria = ? WHERE id = ?",
                     (info, criteria, playlist_id))
    refresh(conn, playlist_id, force=True)


def create(conn: sqlite3.Connection, name: str, playlist: SmartPlaylist) -> int:
    from luxtunes.core import library
    playlist_id = library.create_playlist(conn, name)
    save(conn, playlist_id, playlist)
    return playlist_id


def refresh(conn: sqlite3.Connection, playlist_id: int, force: bool = False,
            tracks: list[dict] | None = None) -> bool:
    """Bring a smart playlist's songs up to date (if Live updating is on, or
    `force`, e.g. right after editing). Songs it already had keep their place;
    new ones go at the end. Returns True if the songs changed."""
    playlist = load(conn, playlist_id)
    if playlist is None or not playlist.understood or not (playlist.info.live or force):
        return False
    wanted = evaluate(conn, playlist, tracks, seed=playlist_id)
    current = [r[0] for r in conn.execute(
        "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position", (playlist_id,))]
    keep = set(wanted)
    order = [t for t in current if t in keep]
    seen = set(order)
    order += [t for t in wanted if t not in seen]
    if order == current:
        return False
    with conn:
        conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        conn.executemany("INSERT INTO playlist_items (playlist_id, position, track_id) VALUES (?, ?, ?)",
                         [(playlist_id, i, t) for i, t in enumerate(order)])
    return True


def refresh_all(conn: sqlite3.Connection) -> int:
    """Every live smart playlist (before an iPod sync, say). Returns how many changed."""
    from luxtunes.core import library
    tracks = library.load_tracks(conn)
    ids = [r[0] for r in conn.execute("SELECT id FROM playlists WHERE is_smart = 1")]
    return sum(refresh(conn, i, tracks=tracks) for i in ids)


# ---- Words ------------------------------------------------------------------------------

def describe(playlist: SmartPlaylist) -> str:
    """A one-line summary, e.g. for tooltips: "Year is in the range 1990 to 1999"."""
    def rule_text(rule) -> str:
        if isinstance(rule, Group):
            inner = (" or " if rule.match_any else " and ").join(rule_text(r) for r in rule.rules)
            return f"({inner})"
        if not rule.known:
            return "(a rule LuxTunes doesn't know)"
        f = FIELDS[rule.field]
        op = dict(OPERATORS[f.kind]).get(rule.op, "?")
        if f.kind == DATE and rule.op in (IN_LAST, NOT_IN_LAST):
            return f"{f.label} {op} {rule.amount} {dict(DATE_UNITS).get(rule.units, 'units')}"
        if f.kind == BOOL:
            return f"{f.label} {op}"
        if f.kind == MEDIA:
            return f"{f.label} {op} {dict(MEDIA_KINDS).get(int(rule.value or 0), rule.value)}"
        value = rule.value
        if f.kind == DATE:
            value = datetime.fromtimestamp(rule.value or 0).strftime("%Y-%m-%d")
        if f.kind == RATING:
            value = f"{int(rule.value or 0) // 20} stars"
        text = f"{f.label} {op} {value!r}" if f.kind == TEXT else f"{f.label} {op} {value}"
        if rule.op == RANGE:
            end = rule.value2
            if f.kind == DATE:
                end = datetime.fromtimestamp(rule.value2 or 0).strftime("%Y-%m-%d")
            text += f" to {end}"
        return text
    joiner = " or " if playlist.rules.match_any else " and "
    return joiner.join(rule_text(r) for r in playlist.rules.rules)
