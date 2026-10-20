"""Bringing what happened on the iPod into the library, at the start of a sync.

iPod classics count plays and skips since the last sync in a separate file
("Play Counts"). For each song we get those *recent* numbers plus when it
was last played/skipped and its rating. The rules, which never lose
anything from the library:

  * plays and skips are ADDED to the library's counts (never replaced, so an
    empty or freshly restored iPod can't reset anything);
  * Last Played / Last Skipped only move forward in time;
  * a rating is copied only if it was changed on the iPod since the last sync;
  * songs are matched by Persistent ID (the iPod keeps the library's ID
    for every song), and songs the library doesn't have are ignored.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class IPodTrackStats:
    dbid: int                     # the song's Persistent ID, as a number
    recent_plays: int = 0
    recent_skips: int = 0
    last_played: int = 0          # Unix time, 0 = never
    last_skipped: int = 0
    rating: int = 0               # 0-100, as it is on the iPod now
    rating_at_sync: int = 0       # as it was when the iPod was last synced


@dataclass
class MergeResult:
    plays: int = 0
    skips: int = 0
    ratings: int = 0
    songs: int = 0
    changed: dict = field(default_factory=dict)     # track id -> fields changed


def persistent_id(dbid: int) -> str:
    return f"{dbid & 0xFFFFFFFFFFFFFFFF:016X}"


def _iso(unix: int) -> str | None:
    if not unix or unix <= 0:
        return None
    return datetime.fromtimestamp(unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def merge(conn: sqlite3.Connection, ipod_tracks) -> MergeResult:
    """Add the iPod's recent plays/skips/ratings to the library, in one transaction."""
    result = MergeResult()
    with conn:
        for item in ipod_tracks:
            if not (item.recent_plays or item.recent_skips or item.rating != item.rating_at_sync):
                continue
            row = conn.execute(
                "SELECT id, play_count, play_date, skip_count, skip_date, rating "
                "FROM tracks WHERE persistent_id = ?", (persistent_id(item.dbid),)).fetchone()
            if row is None:
                continue
            changes = {}
            if item.recent_plays > 0:
                changes["play_count"] = (row["play_count"] or 0) + item.recent_plays
                result.plays += item.recent_plays
                played = _iso(item.last_played)
                if played and played > (row["play_date"] or ""):
                    changes["play_date"] = played
            if item.recent_skips > 0:
                changes["skip_count"] = (row["skip_count"] or 0) + item.recent_skips
                result.skips += item.recent_skips
                skipped = _iso(item.last_skipped)
                if skipped and skipped > (row["skip_date"] or ""):
                    changes["skip_date"] = skipped
            if item.rating != item.rating_at_sync and 0 <= item.rating <= 100:
                changes["rating"] = item.rating
                changes["rating_computed"] = 0
                result.ratings += 1
            if changes:
                assignments = ", ".join(f"{k} = ?" for k in changes)
                conn.execute(f"UPDATE tracks SET {assignments} WHERE id = ?",
                             [*changes.values(), row["id"]])
                result.changed[row["id"]] = changes
                result.songs += 1
    return result
