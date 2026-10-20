"""What a sync puts on the iPod, worked out from its settings (no iPod needed).

The result is a Plan: the library songs and videos that should be on the
iPod, and the playlists to recreate there. The sync then copies what's
missing, removes from the *iPod* whatever isn't in the plan, and updates the
rest. Nothing here (or in the sync) ever removes anything from the library.
"""

from __future__ import annotations

import random
import re
import sqlite3
from dataclasses import dataclass, field

from luxtunes.core import library
from luxtunes.core.ipod.settings import SyncSettings
from luxtunes.core.paths import find_file
from luxtunes.ui.formatting import sort_text

IPOD_VIDEO_EXTENSIONS = (".m4v", ".mp4", ".mov")   # what an iPod Classic can play
# Audio the iPod plays as it is. Anything else (FLAC, Ogg, Opus...) is turned
# into AAC on its way to the iPod; the library's file is never changed.
IPOD_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".m4p", ".aac", ".wav", ".aif", ".aiff",
                         ".aa", ".aax")
UNPLAYABLE_KBPS = 256          # for files the iPod can't play, when no rate is chosen
AUTOFILL_RESERVE = 1024 ** 3      # leave 1 GB free when "Automatically fill free space" is on
SEP = "\x1f"


def album_key(track: dict) -> str:
    return f"{track.get('album_artist') or track.get('artist') or ''}{SEP}{track.get('album') or ''}"


def artist_of(track: dict) -> str:
    return track.get("album_artist") or track.get("artist") or ""


def book_key(track: dict) -> str:
    """One audiobook: its author and title (the album, or the name for a one-file book)."""
    return f"{artist_of(track)}{SEP}{track.get('album') or track.get('name') or ''}"


def podcast_show(track: dict) -> str:
    return track.get("album") or track.get("artist") or "Unknown Podcast"


def _newest_first(episodes: list[dict]) -> list[dict]:
    return sorted(episodes, key=lambda e: e.get("release_date") or e.get("date_added") or "",
                  reverse=True)


def syncable_playlists(conn: sqlite3.Connection) -> list[dict]:
    """Smart and normal playlists (not folders or the built-in ones), A-Z."""
    lists = [p for p in library.load_playlists(conn)
             if not (p["is_folder"] or p["is_master"] or p["distinguished_kind"])]
    lists.sort(key=lambda p: (0 if p["is_smart"] else 1, p["name"].casefold()))
    return lists


@dataclass
class Choices:
    """What the Music, Movies, Podcasts and Books tabs list, with checkboxes."""
    playlists: list[dict]
    artists: list[str]
    albums: list[tuple[str, str]]       # (key, "Artist - Album")
    genres: list[str]
    movies: list[dict]
    shows: list[str] = field(default_factory=list)            # podcasts, A-Z
    episodes: list[dict] = field(default_factory=list)        # newest first
    books: list[tuple[str, str]] = field(default_factory=list)   # (key, "Book — Author")


def library_choices(conn: sqlite3.Connection, phone: bool = False) -> Choices:
    """phone: an Android device, which plays any video format."""
    music = library.load_tracks(conn, media_kind="music")
    artists = sorted({artist_of(t) for t in music if artist_of(t)}, key=sort_text)
    albums = {}
    for t in music:
        if t.get("album"):
            albums[album_key(t)] = f"{artist_of(t)} - {t['album']}" if artist_of(t) else t["album"]
    genres = sorted({t["genre"] for t in music if t.get("genre")}, key=str.casefold)
    movies = sorted([t for t in library.load_tracks(conn, media_kind="movie")
                     if phone or ipod_can_play(t)],
                    key=lambda t: sort_text(t.get("name")))
    episodes = _newest_first(library.load_tracks(conn, media_kind="podcast"))
    shows = sorted({podcast_show(e) for e in episodes}, key=sort_text)
    books = {}
    for t in library.load_tracks(conn, media_kind="audiobook"):
        title = t.get("album") or t.get("name") or "Untitled"
        books[book_key(t)] = f"{title} — {artist_of(t)}" if artist_of(t) else title
    return Choices(syncable_playlists(conn), artists,
                   sorted(albums.items(), key=lambda item: sort_text(item[1])), genres, movies,
                   shows, episodes, sorted(books.items(), key=lambda item: sort_text(item[1])))


@dataclass
class Plan:
    tracks: list[dict] = field(default_factory=list)       # library tracks for the iPod
    playlists: list[dict] = field(default_factory=list)    # {"name", "persistent_id", "track_ids"}
    missing: list[dict] = field(default_factory=list)      # wanted, but the file can't be found
    music_bytes: int = 0
    video_bytes: int = 0

    @property
    def track_ids(self) -> set[int]:
        return {t["id"] for t in self.tracks}

    def count(self, kind: str) -> int:
        return sum(1 for t in self.tracks if t.get("media_kind") == kind)


def _movie_rule(rule: str, movies: list[dict]) -> list[dict]:
    """'3 most recent unwatched' -> those movies."""
    if rule == "all":
        return movies
    chosen = movies
    if "unwatched" in rule:
        chosen = [m for m in chosen if not m.get("play_count")]
    match = re.match(r"(\d+) (most|least) recent", rule)
    if match:
        newest_first = match.group(2) == "most"
        chosen = sorted(chosen, key=lambda m: m.get("date_added") or "", reverse=newest_first)
        chosen = chosen[:int(match.group(1))]
    return chosen


def transfer_kbps(song: dict, settings: SyncSettings) -> int | None:
    """The AAC bit rate a song is converted to on its way to the iPod, or None
    to copy it as it is. With "Convert higher bit rate songs to …" on, songs
    above that rate (and lossless ones) are converted; protected (.m4p) songs
    never are."""
    if (song.get("media_kind") or "music") not in library.AUDIO_KINDS:
        return None
    location = (song.get("location") or "").lower()
    if location.endswith((".m4p", ".aa", ".aax")):
        return None
    if not location.endswith(IPOD_AUDIO_EXTENSIONS):
        return settings.convert_kbps if settings.convert else UNPLAYABLE_KBPS
    if not settings.convert:
        return None
    lossless = location.endswith((".wav", ".aif", ".aiff")) or \
        "lossless" in (song.get("kind") or "").lower()
    if lossless or (song.get("bit_rate") or 0) > settings.convert_kbps:
        return settings.convert_kbps
    return None


def transfer_size(song: dict, settings: SyncSettings, kbps_for=None) -> int:
    """About how many bytes the song takes on the device. kbps_for: the
    device's own conversion rule (default: the iPod's)."""
    kbps = (kbps_for or transfer_kbps)(song, settings)
    if kbps is None:
        return song.get("size") or 0
    return (song.get("total_time") or 0) * kbps // 8      # ms × kbit/s ÷ 8 = bytes


def ipod_can_play(track: dict) -> bool:
    """Videos only go to the iPod in formats it plays (.mkv, .avi... stay home)."""
    if track.get("media_kind") not in ("movie", "tvshow"):
        return True
    return (track.get("location") or "").lower().endswith(IPOD_VIDEO_EXTENSIONS)


def show_of(track: dict) -> str:
    return track.get("show") or track.get("album") or track.get("name") or ""


def _episode_order(e: dict):
    return (e.get("season") or 0, e.get("episode_number") or e.get("track_number") or 0,
            e.get("date_added") or "")


def _tv_rule(rule: str, episodes: list[dict]) -> list[dict]:
    """'3 newest' / '5 oldest unwatched' / 'all unwatched' -> those episodes,
    applied to each show separately."""
    shows: dict[str, list[dict]] = {}
    for e in episodes:
        shows.setdefault(show_of(e), []).append(e)
    chosen = []
    for show in shows.values():
        show.sort(key=_episode_order)
        picked = show
        if "unwatched" in rule:
            picked = [e for e in picked if not e.get("play_count")]
        match = re.match(r"(\d+) (newest|oldest)", rule)
        if match:
            n = int(match.group(1))
            picked = picked[-n:][::-1] if match.group(2) == "newest" else picked[:n]
        chosen.extend(picked)
    return chosen


def _podcast_rule(rule: str, episodes: list[dict]) -> list[dict]:
    """'3 most recent' / '1 least recent unplayed' / 'all unplayed' -> those
    episodes, for each show separately."""
    shows: dict[str, list[dict]] = {}
    for e in episodes:
        shows.setdefault(podcast_show(e), []).append(e)
    chosen = []
    for show in shows.values():
        picked = _newest_first(show)
        if "unplayed" in rule:
            picked = [e for e in picked if not e.get("play_count")]
        match = re.match(r"(\d+) (most|least) recent", rule)
        if match:
            n = int(match.group(1))
            picked = picked[:n] if match.group(2) == "most" else picked[::-1][:n]
        chosen.extend(picked)
    return chosen


def build_plan(conn: sqlite3.Connection, settings: SyncSettings,
               free_space: int | None = None, seed: int | None = None, kbps_for=None,
               phone: bool = False) -> Plan:
    """The plan for one sync. free_space (bytes the device could hold for
    songs) is only needed for "Automatically fill free space". phone: an
    Android device, which plays any video format."""
    plan = Plan()
    playable = (lambda t: True) if phone else ipod_can_play
    music = library.load_tracks(conn, media_kind="music")
    movies = [t for t in library.load_tracks(conn, media_kind="movie") if playable(t)]
    episodes = [t for t in library.load_tracks(conn, media_kind="tvshow") if playable(t)]
    podcasts = library.load_tracks(conn, media_kind="podcast")
    books = library.load_tracks(conn, media_kind="audiobook")
    if settings.only_checked:
        music, movies, episodes, podcasts, books = (
            [t for t in kind if t.get("enabled", 1)]
            for kind in (music, movies, episodes, podcasts, books))

    wanted: dict[int, dict] = {}
    playlists = syncable_playlists(conn)
    if settings.sync_music:
        if settings.music_mode == "all":
            for t in music:
                wanted[t["id"]] = t
            chosen_lists = playlists
        else:
            chosen = set(settings.playlists)
            chosen_lists = [p for p in playlists if p["persistent_id"] in chosen]
        # Everything in a chosen playlist goes, whether or not its artist, album
        # or genre is chosen, and whatever it is (videos, podcasts, books too),
        # so choosing just the playlists you listen to is enough. ("All songs"
        # already has every song; its playlists don't bring videos along.)
        kinds = (music,) if settings.music_mode == "all" else \
            (music, movies, episodes, podcasts, books)
        allowed = {t["id"]: t for kind in kinds for t in kind}
        for p in chosen_lists:
            for track_id in library.playlist_track_ids(conn, p["id"]):
                if track_id in allowed:
                    wanted[track_id] = allowed[track_id]
        if settings.music_mode != "all":
            artists, albums, genres = set(settings.artists), set(settings.albums), set(settings.genres)
            for t in music:
                if artist_of(t) in artists or album_key(t) in albums or t.get("genre") in genres:
                    wanted[t["id"]] = t
    else:
        chosen_lists = []

    if settings.sync_movies:
        picked = set(settings.movies)
        chosen_movies = _movie_rule(settings.movies_rule, movies) if settings.movies_auto else []
        chosen_movies = chosen_movies + [m for m in movies if m["persistent_id"] in picked]
        for m in chosen_movies:
            wanted[m["id"]] = m

    if settings.sync_tv and settings.tv_auto:
        for e in _tv_rule(settings.tv_rule, episodes):
            wanted[e["id"]] = e

    if settings.sync_podcasts:
        chosen_episodes = []
        if settings.podcasts_auto:
            pool = podcasts
            if settings.podcasts_scope == "selected podcasts":
                shows = set(settings.podcast_shows)
                pool = [e for e in podcasts if podcast_show(e) in shows]
            chosen_episodes = _podcast_rule(settings.podcasts_rule, pool)
        picked = set(settings.podcast_episodes)
        chosen_episodes += [e for e in podcasts if e["persistent_id"] in picked]
        for e in chosen_episodes:
            wanted[e["id"]] = e

    if settings.sync_books:
        if settings.books_mode == "selected":
            keys = set(settings.books)
            books = [b for b in books if book_key(b) in keys]
        for b in books:
            wanted[b["id"]] = b

    for track in wanted.values():
        if find_file(track.get("location")) is None:
            plan.missing.append(track)
        else:
            plan.tracks.append(track)
    # Audio = songs, podcasts and audiobooks; video = movies and TV shows.
    audio = [t for t in plan.tracks if (t.get("media_kind") or "music") in library.AUDIO_KINDS]
    plan.music_bytes = sum(transfer_size(t, settings, kbps_for) for t in audio)
    plan.video_bytes = sum(t.get("size") or 0 for t in plan.tracks
                           if (t.get("media_kind") or "music") not in library.AUDIO_KINDS)

    if settings.sync_music and settings.autofill and free_space is not None:
        _autofill(plan, music, free_space, seed)

    on_ipod = plan.track_ids
    for p in chosen_lists:
        ids = [i for i in _ordered_ids(conn, p["id"]) if i in on_ipod]
        plan.playlists.append({"name": p["name"], "persistent_id": p["persistent_id"],
                               "is_smart": bool(p["is_smart"]), "track_ids": ids})
    return plan


def _ordered_ids(conn, playlist_id: int) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position", (playlist_id,))]


def _autofill(plan: Plan, music: list[dict], free_space: int, seed) -> None:
    """Add more songs (higher rated first, then at random) until the iPod is
    nearly full."""
    budget = free_space - AUTOFILL_RESERVE - plan.music_bytes - plan.video_bytes
    chosen = plan.track_ids
    rng = random.Random(seed)
    candidates = [t for t in music if t["id"] not in chosen]
    rng.shuffle(candidates)
    candidates.sort(key=lambda t: -(t.get("rating") or 0) if not t.get("rating_computed") else 0)
    for t in candidates:
        size = t.get("size") or 0
        if size and size <= budget and find_file(t.get("location")):
            plan.tracks.append(t)
            plan.music_bytes += size
            budget -= size
