"""Radio: right-click a song → Start Radio → From This Song / Album / Artist
makes a mix of related songs from your library, under PLAYING → Radio.

How songs are picked (everything is worked out here, from the library on
this computer; nothing is saved but the radio itself and your likes):

- Genre closeness is the backbone (core/genres.py): how close a song's
  genres are to the radio's.
- Your own listening adds to it: songs in the same playlists as the
  starting songs, songs you play and rate, the same artist, a similar year.
- Discover sets your listening aside: songs you rarely or never play
  come first, and sharing playlists counts slightly against a song, so
  the radio reaches past what you usually hear (still related by genre).
- Likes and dislikes (the heart and the circle on each radio song) are
  remembered with the genres of the radio they were given in. A dislike
  keeps a song off radios near those genres (and counts against it on
  others); a like favours it on radios near them. Discover keeps them too.

A radio starts with RADIO_SIZE songs and grows by TOP_UP whenever play gets
within TOP_UP_WHEN songs of the end, never repeating a song. Songs that are
unchecked, set to skip when shuffling, not music, or another copy of a song
already in the radio (an MP3 and its AAC version) are left out; artists and
albums are spread out as in a shuffle. No Qt in here.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from luxtunes.core import albums, genres, library
from luxtunes.core.db import get_setting, set_setting
from luxtunes.core.library import now_utc
from luxtunes.core.shuffle import album_key, artist_key, fresh_rng, spread_shuffle

RADIO_SIZE = 50
TOP_UP = 25
TOP_UP_WHEN = 10
KINDS = ("song", "album", "artist")
STATE = "radio"                 # library_info key: the current radio (JSON)
NEAR = 0.3                      # feedback from a radio at least this close counts in full
# At most this share of a radio by the artist it was started from (an
# artist's radio is mostly theirs), and by any other one artist.
SEED_ARTIST_SHARE = {"song": 0.2, "album": 0.25, "artist": 0.4}
OTHER_ARTIST_SHARE = 0.1

# How much each thing counts: (normal, Discover).
WEIGHTS = {
    "genre":    (0.45, 0.60),
    "artist":   (0.15, 0.05),
    "playlist": (0.20, -0.10),
    "plays":    (0.10, 0.0),
    "unheard":  (0.0, 0.20),
    "rating":   (0.05, 0.0),
    "year":     (0.05, 0.10),
    "liked":    (0.50, 0.30),
    "disliked": (0.50, 0.50),
}


@dataclass
class Radio:
    kind: str                   # "song", "album" or "artist"
    seed_id: int                # the song it was started from
    title: str                  # "Walls Radio", "Album Name Radio", "Emery Radio"
    genre_ids: list[int]        # the catalogue genres it's built around
    track_ids: list[int] = field(default_factory=list)
    discover: bool = False

    def to_json(self) -> str:
        return json.dumps({"kind": self.kind, "seed": self.seed_id, "title": self.title,
                           "genres": _genre_ids_out(self.genre_ids), "tracks": self.track_ids,
                           "discover": self.discover})

    @classmethod
    def from_json(cls, text: str | None) -> "Radio | None":
        try:
            d = json.loads(text) if text else None
            if not d or d.get("kind") not in KINDS:
                return None
            return cls(d["kind"], int(d["seed"]), str(d["title"]),
                       _genre_ids_in(d.get("genres", [])),
                       [int(t) for t in d.get("tracks", [])], bool(d.get("discover")))
        except (ValueError, KeyError, TypeError):
            return None


def _genre_ids_out(indexes) -> list[str]:
    """Genres are saved by their lasting catalogue ids, not indexes."""
    try:
        return genres.catalogue().ids(indexes)
    except (OSError, ValueError):
        return []


def _genre_ids_in(saved) -> list[int]:
    try:
        return genres.catalogue().indexes(str(g) for g in saved)
    except (OSError, ValueError):
        return []


# ---- The current radio ---------------------------------------------------------------

def current(conn: sqlite3.Connection) -> Radio | None:
    return Radio.from_json(get_setting(conn, STATE))


def _save(conn: sqlite3.Connection, radio: Radio | None) -> None:
    set_setting(conn, STATE, radio.to_json() if radio else None)


def tracks(conn: sqlite3.Connection) -> list[dict]:
    """The current radio's songs, in order (songs deleted from the library
    meanwhile are left out). Each has radio_opinion (1, -1 or 0)."""
    radio = current(conn)
    if radio is None:
        return []
    by_id = {t["id"]: t for t in library.load_tracks_by_ids(conn, radio.track_ids)}
    opinions = _opinions(conn)
    out = []
    for track_id in radio.track_ids:
        track = by_id.get(track_id)
        if track is not None:
            track["radio_opinion"] = opinions.get(track_id, (0,))[0]
            out.append(track)
    return out


def title_for(kind: str, track: dict) -> str:
    if kind == "album":
        name = track.get("album") or "Unknown Album"
    elif kind == "artist":
        name = track.get("artist") or track.get("album_artist") or "Unknown Artist"
    else:
        name = track.get("name") or "Untitled"
    return f"{name} Radio"


def start(conn: sqlite3.Connection, kind: str, track: dict, discover: bool = False,
          rng: random.Random | None = None) -> Radio:
    """A new radio from this song / its album / its artist, replacing the
    current one."""
    if kind not in KINDS:
        raise ValueError(kind)
    radio = Radio(kind, int(track["id"]), title_for(kind, track), [], discover=discover)
    return _fill(conn, radio, rng)


def refresh(conn: sqlite3.Connection, discover: bool | None = None,
            rng: random.Random | None = None) -> Radio | None:
    """A new mix from the same start (Refresh, or Discover turned on or off)."""
    old = current(conn)
    if old is None:
        return None
    seed = library.load_track(conn, old.seed_id)
    if seed is None:
        return None
    radio = Radio(old.kind, old.seed_id, old.title, [],
                  discover=old.discover if discover is None else discover)
    return _fill(conn, radio, rng)


def _fill(conn, radio: Radio, rng) -> Radio:
    library_tracks = library.load_tracks(conn, media_kind="music")
    seed = next((t for t in library_tracks if t["id"] == radio.seed_id), None) \
        or library.load_track(conn, radio.seed_id)
    seeds = seed_tracks(library_tracks, radio.kind, seed)
    radio.genre_ids = seed_genres(seeds)
    if radio.kind == "song":                    # a song's radio starts with that song
        picked = [radio.seed_id] + pick(conn, library_tracks, radio, seeds, RADIO_SIZE - 1,
                                        {radio.seed_id}, rng)
    else:
        picked = pick(conn, library_tracks, radio, seeds, RADIO_SIZE, set(), rng)
    radio.track_ids = picked
    _save(conn, radio)
    return radio


def extend(conn: sqlite3.Connection, count: int = TOP_UP,
           rng: random.Random | None = None) -> list[int]:
    """Add more songs to the end of the radio (none it already has). Returns them."""
    radio = current(conn)
    if radio is None:
        return []
    library_tracks = library.load_tracks(conn, media_kind="music")
    seed = next((t for t in library_tracks if t["id"] == radio.seed_id), None)
    if seed is None:
        return []
    seeds = seed_tracks(library_tracks, radio.kind, seed)
    added = pick(conn, library_tracks, radio, seeds, count, set(radio.track_ids), rng)
    radio.track_ids += added
    _save(conn, radio)
    return added


def remove(conn: sqlite3.Connection, track_ids) -> None:
    radio = current(conn)
    if radio is not None:
        gone = set(track_ids)
        radio.track_ids = [t for t in radio.track_ids if t not in gone]
        _save(conn, radio)


def save_as_playlist(conn: sqlite3.Connection) -> int | None:
    """Make a playlist (named after the radio) with the radio's songs. Its id."""
    radio = current(conn)
    if radio is None:
        return None
    songs = [t["id"] for t in tracks(conn)]
    playlist_id = library.create_playlist(conn, radio.title)
    library.add_to_playlist(conn, playlist_id, songs)
    return playlist_id


# ---- Which songs ---------------------------------------------------------------------

def seed_tracks(library_tracks: list[dict], kind: str, seed: dict | None) -> list[dict]:
    """The songs a radio starts from: the song, its album or its artist's songs."""
    if seed is None:
        return []
    if kind == "album":
        key = albums.album_key(seed)
        found = [t for t in library_tracks if albums.album_key(t) == key]
    elif kind == "artist":
        name = (seed.get("artist") or seed.get("album_artist") or "").casefold()
        found = [t for t in library_tracks
                 if name and name in ((t.get("artist") or "").casefold(),
                                      (t.get("album_artist") or "").casefold())]
    else:
        found = [seed]
    return found or [seed]


def seed_genres(seeds: list[dict], most: int = 5) -> list[int]:
    """The catalogue genres the starting songs have, the commonest first."""
    try:
        catalogue = genres.catalogue()
    except (OSError, ValueError):
        return []
    counts: Counter = Counter()
    for track in seeds:
        for genre_id in catalogue.genre_ids(track.get("genre")):
            counts[genre_id] += 1
    return [g for g, _ in counts.most_common(most)]


def _version_key(track: dict) -> tuple:
    return ((track.get("name") or "").strip().casefold(), (track.get("artist") or "").strip().casefold())


def _eligible(track: dict) -> bool:
    return (track.get("media_kind") or "music") == "music" and track.get("enabled", 1) \
        and not track.get("skip_when_shuffling")


def _playlist_closeness(conn, seeds: list[dict]) -> dict[int, float]:
    """How much each song shares your own playlists with the starting songs
    (big playlists count less), 0..1."""
    seed_ids = {t["id"] for t in seeds}
    rows = conn.execute(
        "SELECT pi.playlist_id, pi.track_id FROM playlist_items pi JOIN playlists p "
        "ON p.id = pi.playlist_id WHERE p.is_smart = 0 AND p.is_master = 0 AND p.is_folder = 0 "
        "AND p.distinguished_kind IS NULL").fetchall()
    members: dict[int, set] = defaultdict(set)
    for playlist_id, track_id in rows:
        members[playlist_id].add(track_id)
    score: dict[int, float] = defaultdict(float)
    for songs in members.values():
        if songs & seed_ids and len(songs) > 1:
            weight = 1 / math.log(len(songs) + 1)
            for track_id in songs:
                score[track_id] += weight
    top = max(score.values(), default=0) or 1
    return {t: s / top for t, s in score.items()}


def _opinions(conn) -> dict[int, tuple[int, list[int]]]:
    rows = conn.execute("SELECT track_id, opinion, genres FROM radio_feedback").fetchall()
    out = {}
    for track_id, opinion, genre_text in rows:
        try:
            genre_ids = _genre_ids_in(json.loads(genre_text or "[]"))
        except (ValueError, TypeError):
            genre_ids = []
        out[track_id] = (int(opinion), genre_ids)
    return out


def scores(conn, library_tracks: list[dict], radio: Radio, seeds: list[dict]) -> dict[int, float]:
    """Every eligible song's score for this radio (higher: more likely);
    songs left out entirely aren't in the result."""
    which = 1 if radio.discover else 0
    w = {k: v[which] for k, v in WEIGHTS.items()}
    try:
        catalogue = genres.catalogue()
        near = catalogue.related(radio.genre_ids) if radio.genre_ids else {}
    except (OSError, ValueError):
        catalogue, near = None, {}
    genre_cache: dict[str, float] = {}

    def genre_score(field_text):
        if catalogue is None or not near or not field_text:
            return 0.0
        if field_text not in genre_cache:
            genre_cache[field_text] = max((near.get(g, 0.0) for g in catalogue.genre_ids(field_text)),
                                          default=0.0)
        return genre_cache[field_text]

    def feedback_reach(feedback_genres):
        """How much a like/dislike given on another radio applies here (0..1)."""
        if not feedback_genres or not near:
            return 0.2
        closeness = max((near.get(g, 0.0) for g in feedback_genres), default=0.0)
        return 1.0 if closeness >= NEAR else 0.2 + closeness

    seed_ids = {t["id"] for t in seeds}
    seed_artists = {artist_key(t) for t in seeds} | {(t.get("artist") or "").casefold() for t in seeds}
    seed_artists.discard("")
    years = [t["year"] for t in seeds if t.get("year")]
    seed_year = sorted(years)[len(years) // 2] if years else None
    together = _playlist_closeness(conn, seeds)
    opinions = _opinions(conn)
    most_plays = max((t.get("play_count") or 0 for t in library_tracks), default=0)
    plays_scale = math.log1p(most_plays) or 1

    result = {}
    for track in library_tracks:
        if not _eligible(track):
            continue
        track_id = track["id"]
        genre = genre_score(track.get("genre"))
        artist = 1.0 if (artist_key(track) in seed_artists
                         or (track.get("artist") or "").casefold() in seed_artists) else 0.0
        shared = together.get(track_id, 0.0)
        if radio.kind == "artist" and track_id in seed_ids:
            artist = 1.0
        # Not related at all (no genre link, artist or playlist): not on this radio.
        if genre < 0.01 and not artist and (shared <= 0 or radio.discover):
            continue
        plays = math.log1p(track.get("play_count") or 0) / plays_scale
        rating = (track.get("rating") or 0) / 100 if not track.get("rating_computed") else 0.0
        year = math.exp(-abs(track["year"] - seed_year) / 10) \
            if seed_year and track.get("year") else 0.0
        # Your listening only counts as far as the song is related at all, so a
        # favourite from another genre doesn't land on every radio.
        related = max(genre, artist, 0.25 if not near else 0.0)
        personal = (w["playlist"] * shared + w["plays"] * plays + w["unheard"] * (1 - plays)
                    + w["rating"] * rating + w["year"] * year)
        artist_weight = w["artist"] * (2.3 if radio.kind == "artist" else 1.0)
        score = w["genre"] * genre + artist_weight * artist + personal * related
        if track_id in opinions:
            opinion, feedback_genres = opinions[track_id]
            reach = feedback_reach(feedback_genres)
            if opinion < 0:
                if reach >= 1.0:
                    continue                    # disliked on a radio like this one
                score -= w["disliked"] * reach
            else:
                score += w["liked"] * reach
        if score > 0:
            result[track_id] = score
    return result


def pick(conn, library_tracks: list[dict], radio: Radio, seeds: list[dict], count: int,
         exclude: set, rng: random.Random | None = None) -> list[int]:
    """`count` songs for the radio, drawn from the best-scoring ones (better
    scores more likely), without songs in `exclude` or second copies of a song,
    then spread out by artist and album."""
    rng = rng or fresh_rng()
    by_id = {t["id"]: t for t in library_tracks}
    taken_versions = {_version_key(by_id[i]) for i in exclude if i in by_id}
    scored = scores(conn, library_tracks, radio, seeds)
    ranked = sorted((i for i in scored if i not in exclude), key=lambda i: -scored[i])
    # Draw from the closest songs: those within half of the best score, or
    # further down only when there aren't three times as many as needed.
    best = scored[ranked[0]] if ranked else 0
    for share in (0.5, 0.35, 0.2, 0.0):
        close = [i for i in ranked if scored[i] >= best * share]
        if len(close) >= count * 3:
            break
    ranked = close if ranked else []
    # How many songs each artist may have, counting those already on the radio.
    total = len(exclude) + count
    seed_artists = {artist_key(t) for t in seeds}
    seed_cap = max(2, round(total * SEED_ARTIST_SHARE[radio.kind]))
    other_cap = max(2, round(total * OTHER_ARTIST_SHARE))
    per_artist = Counter(artist_key(by_id[i]) for i in exclude if i in by_id)
    chosen: list[int] = []
    pool = list(ranked)
    while pool and len(chosen) < count:
        # Songs whose artist is full can't come up any more.
        pool = [i for i in pool if per_artist[artist_key(by_id[i])] <
                (seed_cap if artist_key(by_id[i]) in seed_artists else other_cap)]
        if not pool:
            break
        weights = [scored[i] ** 2 for i in pool]
        index = rng.choices(range(len(pool)), weights=weights)[0]
        track_id = pool.pop(index)
        track = by_id[track_id]
        version = _version_key(track)
        who = artist_key(track)
        if version in taken_versions or \
                per_artist[who] >= (seed_cap if who in seed_artists else other_cap):
            continue
        taken_versions.add(version)
        per_artist[who] += 1
        chosen.append(track_id)
    top = max((scored[i] for i in chosen), default=1) or 1
    return spread_shuffle(chosen, key=lambda i: artist_key(by_id[i]),
                          album=lambda i: album_key(by_id[i]),
                          weight=lambda i: max(0.05, scored[i] / top), rng=rng)


# ---- Likes and dislikes --------------------------------------------------------------

def set_opinion(conn: sqlite3.Connection, track_id: int, opinion: int) -> None:
    """1: liked, -1: disliked, 0: neither (forgotten). Remembered with the
    current radio's genres and title."""
    with conn:
        if opinion == 0:
            conn.execute("DELETE FROM radio_feedback WHERE track_id = ?", (track_id,))
            return
        radio = current(conn)
        conn.execute(
            "INSERT OR REPLACE INTO radio_feedback (track_id, opinion, genres, radio, date) "
            "VALUES (?, ?, ?, ?, ?)",
            (track_id, 1 if opinion > 0 else -1, json.dumps(_genre_ids_out(radio.genre_ids) if radio else []),
             radio.title if radio else "", now_utc()))


def feedback_tracks(conn: sqlite3.Connection) -> list[dict]:
    """Every liked or disliked song, for View → Radio Likes & Dislikes: each with
    radio_opinion and radio_from (the radio it was given on)."""
    rows = conn.execute("SELECT track_id, opinion, radio FROM radio_feedback").fetchall()
    found = {t["id"]: t for t in library.load_tracks_by_ids(conn, [r[0] for r in rows])}
    out = []
    for track_id, opinion, radio_title in rows:
        track = found.get(track_id)
        if track is not None:
            track["radio_opinion"] = int(opinion)
            track["radio_from"] = radio_title or ""
            out.append(track)
    return out
