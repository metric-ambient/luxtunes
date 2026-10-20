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
AUTOFILL_RESERVE = 1024 ** 3      # leave 1 GB free when "Automatically fill free space" is on
SEP = "\x1f"


def album_key(track: dict) -> str:
    return f"{track.get('album_artist') or track.get('artist') or ''}{SEP}{track.get('album') or ''}"


def artist_of(track: dict) -> str:
    return track.get("album_artist") or track.get("artist") or ""


def syncable_playlists(conn: sqlite3.Connection) -> list[dict]:
    """Smart and normal playlists (not folders or the built-in ones), A-Z."""
    lists = [p for p in library.load_playlists(conn)
             if not (p["is_folder"] or p["is_master"] or p["distinguished_kind"])]
    lists.sort(key=lambda p: (0 if p["is_smart"] else 1, p["name"].casefold()))
    return lists


@dataclass
class Choices:
    """What the Music and Movies tabs list, with checkboxes."""
    playlists: list[dict]
    artists: list[str]
    albums: list[tuple[str, str]]       # (key, "Artist - Album")
    genres: list[str]
    movies: list[dict]


def library_choices(conn: sqlite3.Connection) -> Choices:
    music = library.load_tracks(conn, media_kind="music")
    artists = sorted({artist_of(t) for t in music if artist_of(t)}, key=sort_text)
    albums = {}
    for t in music:
        if t.get("album"):
            albums[album_key(t)] = f"{artist_of(t)} - {t['album']}" if artist_of(t) else t["album"]
    genres = sorted({t["genre"] for t in music if t.get("genre")}, key=str.casefold)
    movies = sorted([t for t in library.load_tracks(conn, media_kind="movie") if ipod_can_play(t)],
                    key=lambda t: sort_text(t.get("name")))
    return Choices(syncable_playlists(conn), artists,
                   sorted(albums.items(), key=lambda item: sort_text(item[1])), genres, movies)


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


def build_plan(conn: sqlite3.Connection, settings: SyncSettings,
               free_space: int | None = None, seed: int | None = None) -> Plan:
    """The plan for one sync. free_space (bytes the iPod could hold for
    songs) is only needed for "Automatically fill free space"."""
    plan = Plan()
    music = library.load_tracks(conn, media_kind="music")
    movies = [t for t in library.load_tracks(conn, media_kind="movie") if ipod_can_play(t)]
    episodes = [t for t in library.load_tracks(conn, media_kind="tvshow") if ipod_can_play(t)]
    if settings.only_checked:
        music = [t for t in music if t.get("enabled", 1)]
        movies = [t for t in movies if t.get("enabled", 1)]
        episodes = [t for t in episodes if t.get("enabled", 1)]

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
            allowed = {t["id"]: t for t in music}
            for p in chosen_lists:
                for track_id in library.playlist_track_ids(conn, p["id"]):
                    if track_id in allowed:
                        wanted[track_id] = allowed[track_id]
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

    for track in wanted.values():
        if find_file(track.get("location")) is None:
            plan.missing.append(track)
        else:
            plan.tracks.append(track)
    plan.music_bytes = sum(t.get("size") or 0 for t in plan.tracks if t.get("media_kind") == "music")
    plan.video_bytes = sum(t.get("size") or 0 for t in plan.tracks if t.get("media_kind") != "music")

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
