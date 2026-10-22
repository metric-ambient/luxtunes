"""The smart playlists a new library starts with: everyday examples of what
smart playlists can do. They're ordinary smart playlists (edit or delete
them freely) and are only ever added once, to a brand-new, empty library.

Importing an iTunes library brings its own versions of these; any starter
playlist with the same name is then replaced by the imported one. No Qt in here.
"""

from __future__ import annotations

import sqlite3

from luxtunes.core import smart
from luxtunes.core.db import get_setting as _get, set_setting
from luxtunes.core.smart import Group, Info, Rule, SmartPlaylist

KEY = "starter_playlists"              # library_info: their persistent ids, once added

YEAR, MEDIA_KIND, RATING, DATE_ADDED, LAST_PLAYED, PLAYS = 0x07, 0x3C, 0x19, 0x10, 0x17, 0x16
MUSIC, MUSIC_VIDEO = 0x01, 0x20


def _music() -> Group:
    """Media Kind is Music or Music Video (so movies and podcasts stay out)."""
    return Group(True, [Rule(MEDIA_KIND, smart.BIT_IS, MUSIC),
                        Rule(MEDIA_KIND, smart.BIT_IS, MUSIC_VIDEO)])


def playlists() -> list[tuple[str, SmartPlaylist]]:
    two_weeks = dict(amount=2, units=smart.WEEK)
    return [
        ("90’s Music", SmartPlaylist(Info(), Group(False, [
            Rule(YEAR, smart.RANGE, 1990, 1999), _music()]))),
        ("My Top Rated", SmartPlaylist(Info(), Group(False, [
            Rule(RATING, smart.GREATER, 60), _music()]))),            # 4 and 5 stars
        ("Recently Added", SmartPlaylist(Info(), Group(False, [
            Rule(DATE_ADDED, smart.IN_LAST, amount=2, units=smart.MONTH), _music()]))),
        ("Recently Played", SmartPlaylist(Info(), Group(False, [
            Rule(LAST_PLAYED, smart.IN_LAST, **two_weeks), _music()]))),
        ("Top 25 Most Played", SmartPlaylist(
            Info(limit=True, limit_type=smart.LIMIT_ITEMS, limit_sort=smart.SORT_PLAYS,
                 limit_value=25),
            Group(False, [Rule(PLAYS, smart.GREATER, 0), _music()]))),
    ]


def add_if_new(conn: sqlite3.Connection) -> list[int]:
    """Add the starter playlists to a library that's brand new (no songs, no
    playlists of its own) and has never had them. Returns their ids."""
    if _get(conn, KEY) is not None:
        return []
    if conn.execute("SELECT 1 FROM tracks LIMIT 1").fetchone() or conn.execute(
            "SELECT 1 FROM playlists WHERE NOT is_master AND distinguished_kind IS NULL "
            "LIMIT 1").fetchone():
        return []
    ids = [smart.create(conn, name, playlist) for name, playlist in playlists()]
    pids = [conn.execute("SELECT persistent_id FROM playlists WHERE id = ?", (i,)).fetchone()[0]
            for i in ids]
    set_setting(conn, KEY, " ".join(pids))
    return ids


def _plain(name: str) -> str:
    return name.replace("\u2019", "'").casefold()


def make_way_for(conn: sqlite3.Connection, names: set[str]) -> int:
    """Before an import that brings smart playlists with these names: remove
    the starter playlists of the same name (the imported ones replace them).
    Doesn't commit. Returns how many were removed."""
    pids = (_get(conn, KEY) or "").split()
    if not pids or not names:
        return 0
    wanted = {_plain(n) for n in names}
    removed = 0
    for pid in pids:
        row = conn.execute("SELECT id, name FROM playlists WHERE persistent_id = ? AND is_smart",
                           (pid,)).fetchone()
        if row and _plain(row[1] or "") in wanted:
            conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (row[0],))
            conn.execute("DELETE FROM playlists WHERE id = ?", (row[0],))
            removed += 1
    return removed
