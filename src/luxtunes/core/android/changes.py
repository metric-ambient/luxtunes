"""changes.json: what a player on the phone sends back at the next sync.

Format version 2 (version 1 files are still read the same way):

    {"version": 2, "library_id": "...",
     "tracks": [{"id", "plays", "last_played", "skips", "last_skipped", "rating"}],
     "playlists": [{"id", "action": "create"|"update"|"delete", "name", "is_smart",
                    "items": [track ids]  or  "smart_info"/"smart_criteria" (base64)}],
     "equalizer": {"on", "preset", "manual": {"preamp", "bands"},
                   "presets": [{"name", "preamp", "bands"}]}}   (optional)

Tracks merge with the same rules as an iPod's play counts. Playlists are only
applied when the file was made against this library, only to playlists
LuxTunes sent to that phone (or new ones), and deletions are confirmed first.
Anything malformed is skipped and reported, never stored. See
docs/android-library-format.md. No Qt in here.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from luxtunes.core import library, smart
from luxtunes.core.ipod import stats

CHANGES = "changes.json"
VERSION = 2
ID = re.compile(r"^[0-9A-Fa-f]{16}$")
ACTIONS = ("create", "update", "delete")
NAME_MAX = 255


@dataclass
class PlaylistChange:
    id: str
    action: str
    name: str | None = None
    is_smart: bool = False
    items: list[str] = field(default_factory=list)
    smart_info: bytes | None = None
    smart_criteria: bytes | None = None
    raw: dict = field(default_factory=dict)       # as the phone wrote it


@dataclass
class PhoneChanges:
    text: str | None = None                       # the file as read (None: no file)
    library_id: str | None = None
    tracks: list = field(default_factory=list)    # stats.IPodTrackStats
    raw_tracks: list = field(default_factory=list)
    playlists: list[PlaylistChange] = field(default_factory=list)
    equalizer: object = None                      # equalizer.Settings made on the phone
    raw_equalizer: object = None                  # as the phone wrote it
    problems: list[str] = field(default_factory=list)


@dataclass
class PlaylistResult:
    created: list[str] = field(default_factory=list)      # names
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)         # deletions you said no to
    new_ids: list[str] = field(default_factory=list)      # persistent ids of created ones


def _unix(text) -> int:
    if not text:
        return 0
    try:
        return int(datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def _count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _track(entry) -> stats.IPodTrackStats | None:
    if not isinstance(entry, dict) or not ID.match(str(entry.get("id") or "")):
        return None
    rated = "rating" in entry
    try:
        rating = max(0, min(100, int(entry.get("rating") or 0))) if rated else 0
    except (TypeError, ValueError):
        rated, rating = False, 0
    return stats.IPodTrackStats(
        dbid=int(entry["id"], 16), recent_plays=_count(entry.get("plays")),
        recent_skips=_count(entry.get("skips")), last_played=_unix(entry.get("last_played")),
        last_skipped=_unix(entry.get("last_skipped")), rating=rating,
        rating_at_sync=-1 if rated else 0)


def _blob(text) -> bytes | None:
    try:
        return base64.b64decode(str(text), validate=True)
    except (binascii.Error, ValueError):
        return None


def _playlist(entry, problems: list[str]) -> PlaylistChange | None:
    if not isinstance(entry, dict):
        return None
    pid = str(entry.get("id") or "")
    action = entry.get("action")
    label = entry.get("name") or pid or "a playlist"
    if not ID.match(pid) or action not in ACTIONS:
        problems.append(f"Playlist change from the phone skipped ({label}): not understood.")
        return None
    change = PlaylistChange(pid.upper(), action, raw=entry)
    if action == "delete":
        return change
    name = entry.get("name")
    change.name = str(name).strip()[:NAME_MAX] if isinstance(name, str) and name.strip() else None
    change.is_smart = bool(entry.get("is_smart"))
    if change.is_smart:
        info, criteria = _blob(entry.get("smart_info")), _blob(entry.get("smart_criteria"))
        try:
            if info is None or criteria is None:
                raise smart.SmartFormatError("missing rules")
            smart.decode(info, criteria)
        except (smart.SmartFormatError, struct.error, ValueError):
            problems.append(f"Smart playlist “{label}” from the phone skipped: its rules "
                            "couldn't be read.")
            return None
        change.smart_info, change.smart_criteria = info, criteria
    else:
        items = entry.get("items") or []
        if not isinstance(items, list):
            items = []
        change.items = [str(i).upper() for i in items if ID.match(str(i))]
    return change


def _equalizer(entry, problems: list[str]):
    """The phone's whole equalizer (on, chosen preset, Manual values, preset
    list), or None if it can't be read (reported, and nothing changes)."""
    from luxtunes.core import equalizer
    try:
        if not isinstance(entry, dict) or not isinstance(entry.get("presets"), list):
            raise ValueError("not understood")
        presets, names = [], set()
        for p in entry["presets"]:
            name = equalizer.clean_name(p["name"])
            if name.casefold() in names:
                raise ValueError(f"two presets called “{name}”")
            names.add(name.casefold())
            presets.append(equalizer.Preset.from_dict({**p, "name": name}))
        manual = entry.get("manual") or {}
        settings = equalizer.Settings(
            on=bool(entry.get("on")), presets=presets,
            manual=equalizer.Preset.from_dict({"bands": [0] * 10, **manual,
                                               "name": equalizer.MANUAL}))
        chosen = str(entry.get("preset") or equalizer.MANUAL)
        settings.preset = chosen if settings.find(chosen) else equalizer.MANUAL
        return settings
    except (TypeError, ValueError, KeyError) as error:
        problems.append(f"The equalizer from the phone couldn't be read ({error}); "
                        "LuxTunes' equalizer was left as it is.")
        return None


def apply_equalizer(conn: sqlite3.Connection, changes: PhoneChanges) -> bool:
    """Make LuxTunes' equalizer the phone's. Songs whose own preset the phone
    no longer has lose it. True if anything was applied."""
    from luxtunes.core import equalizer
    phone = changes.equalizer
    if phone is None:
        return False
    before = equalizer.load(conn)
    kept = {p.name.casefold() for p in phone.presets}
    for preset in before.presets:
        if preset.name.casefold() not in kept:
            library.rename_equalizer_preset(conn, preset.name, None)
    equalizer.save(conn, phone)
    return True


def read(root: Path) -> PhoneChanges:
    """changes.json from the phone's LuxTunes folder (empty if there's none)."""
    result = PhoneChanges()
    try:
        result.text = (root / CHANGES).read_text(encoding="utf-8")
        data = json.loads(result.text)
    except OSError:
        return result
    except ValueError:
        result.problems.append("The phone's changes.json couldn't be read; it was left as it is.")
        result.text = None                        # never empty a file we couldn't read
        return result
    if not isinstance(data, dict):
        return result
    lid = data.get("library_id")
    result.library_id = str(lid) if lid else None
    for entry in data.get("tracks") or []:
        item = _track(entry)
        if item is not None:
            result.tracks.append(item)
            result.raw_tracks.append(entry)
    if "equalizer" in data:
        result.equalizer = _equalizer(data["equalizer"], result.problems)
        if result.equalizer is not None:
            result.raw_equalizer = data["equalizer"]
    seen = set()
    for entry in data.get("playlists") or []:
        change = _playlist(entry, result.problems)
        if change is not None and change.id not in seen:      # one entry per playlist
            seen.add(change.id)
            result.playlists.append(change)
    return result


# ---- Applying playlist changes -----------------------------------------------------------

def editable(row) -> bool:
    """Your own playlists and smart playlists (imported default smart playlists
    count too); never folders, the hidden Library list or library sections."""
    kind = row["distinguished_kind"]
    return not row["is_folder"] and not row["is_master"] and (kind is None or kind >= 200)


def _row(conn, pid: str):
    return conn.execute("SELECT * FROM playlists WHERE upper(persistent_id) = ?",
                        (pid,)).fetchone()


def deletions(conn: sqlite3.Connection, changes: PhoneChanges, sent: set[str]) -> list[str]:
    """Names of the playlists the phone deleted that would really be deleted here."""
    names = []
    for change in changes.playlists:
        row = _row(conn, change.id) if change.action == "delete" else None
        if row is not None and change.id in sent and editable(row):
            names.append(row["name"])
    return names


def apply_playlists(conn: sqlite3.Connection, changes: PhoneChanges, sent: set[str],
                    on_phone: set[str], allow_delete: bool = True,
                    problems: list[str] | None = None) -> PlaylistResult:
    """Apply the phone's playlist changes. sent: persistent ids of the playlists
    LuxTunes last sent to this phone (only those may be changed or deleted).
    on_phone: track persistent ids that were on the phone, so songs it couldn't
    see stay in a playlist it edited."""
    problems = problems if problems is not None else []
    result = PlaylistResult()
    track_ids = {r[0].upper(): r[1] for r in conn.execute(
        "SELECT persistent_id, id FROM tracks WHERE persistent_id IS NOT NULL")}
    touched_smart = []
    for change in changes.playlists:
        row = _row(conn, change.id)
        if row is not None and change.action == "create" and not editable(row):
            change.id = _free_id(conn)            # the id is taken: a new one (the phone copes)
            row = None
        elif row is not None and change.action != "create" and \
                (change.id not in sent or not editable(row)):
            problems.append(f"The phone tried to change “{row['name']}”, which LuxTunes "
                            "didn't send it; left as it is.")
            continue
        if change.action == "delete":
            if row is None:
                continue                                         # already gone
            if not allow_delete:
                result.kept.append(row["name"])
                continue
            library.delete_playlist(conn, row["id"])
            result.deleted.append(row["name"])
            continue
        if row is not None and bool(row["is_smart"]) != change.is_smart:
            problems.append(f"“{row['name']}” from the phone skipped: a playlist can't change "
                            "between smart and plain.")
            continue
        if row is None:                                          # create (or update of a gone one)
            name = change.name or "untitled playlist"
            with conn:
                cursor = conn.execute(
                    "INSERT INTO playlists (persistent_id, name, position) VALUES (?, ?, "
                    "(SELECT COALESCE(MAX(position) + 1, 0) FROM playlists))",
                    (change.id, name))
            playlist_id = cursor.lastrowid
            result.created.append(name)
            result.new_ids.append(change.id)
        else:
            playlist_id = row["id"]
            if change.name and change.name != row["name"]:
                with conn:
                    conn.execute("UPDATE playlists SET name = ? WHERE id = ?",
                                 (change.name, playlist_id))
            result.updated.append(change.name or row["name"])
        if change.is_smart:
            with conn:
                conn.execute("UPDATE playlists SET is_smart = 1, smart_info = ?, "
                             "smart_criteria = ? WHERE id = ?",
                             (change.smart_info, change.smart_criteria, playlist_id))
            touched_smart.append(playlist_id)
        else:
            _set_items(conn, playlist_id, change.items, track_ids, on_phone,
                       existing=row is not None)
    for playlist_id in touched_smart:
        smart.refresh(conn, playlist_id, force=True)
    return result


def _free_id(conn) -> str:
    from luxtunes.core.db import new_persistent_id
    while True:
        pid = new_persistent_id()
        if _row(conn, pid) is None:
            return pid


def _set_items(conn, playlist_id: int, items: list[str], track_ids: dict, on_phone: set[str],
               existing: bool) -> None:
    """The phone's order, then (in their old order) songs the phone couldn't see."""
    new = [track_ids[pid] for pid in items if pid in track_ids]
    if existing:
        by_id = {v: k for k, v in track_ids.items()}
        old = [r[0] for r in conn.execute("SELECT track_id FROM playlist_items WHERE "
                                          "playlist_id = ? ORDER BY position", (playlist_id,))]
        new += [t for t in old if by_id.get(t) not in on_phone]
    with conn:
        conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        conn.executemany("INSERT INTO playlist_items (playlist_id, position, track_id) "
                         "VALUES (?, ?, ?)", [(playlist_id, i, t) for i, t in enumerate(new)])


# ---- Emptying the file --------------------------------------------------------------------

def emptied(merged: PhoneChanges, now_text: str | None) -> dict:
    """The file to write once `merged` is in the library. If the phone wrote to
    it meanwhile (now_text differs), what it added since is kept: plays and
    skips above what was merged, other ratings, other playlist entries."""
    empty = {"version": VERSION, "tracks": [], "playlists": []}
    if now_text is None or now_text == merged.text:
        return empty
    try:
        data = json.loads(now_text)
    except ValueError:
        return empty
    if not isinstance(data, dict):
        return empty
    done = {str(e.get("id")).upper(): e for e in merged.raw_tracks}
    for entry in data.get("tracks") or []:
        if not isinstance(entry, dict):
            continue
        before = done.get(str(entry.get("id")).upper())
        if before is None:
            empty["tracks"].append(entry)
            continue
        left = dict(entry)
        left["plays"] = _count(entry.get("plays")) - _count(before.get("plays"))
        left["skips"] = _count(entry.get("skips")) - _count(before.get("skips"))
        if "rating" in entry and entry.get("rating") == before.get("rating"):
            left.pop("rating")
        if left["plays"] > 0 or left["skips"] > 0 or "rating" in left:
            left["plays"], left["skips"] = max(0, left["plays"]), max(0, left["skips"])
            empty["tracks"].append(left)
    applied = {json.dumps(c.raw, sort_keys=True) for c in merged.playlists}
    for entry in data.get("playlists") or []:
        if json.dumps(entry, sort_keys=True) not in applied:
            empty["playlists"].append(entry)
    # An equalizer the phone wrote after the one taken stays for next time.
    if "equalizer" in data and json.dumps(data["equalizer"], sort_keys=True) != \
            json.dumps(merged.raw_equalizer, sort_keys=True):
        empty["equalizer"] = data["equalizer"]
    if data.get("library_id"):
        empty["library_id"] = data["library_id"]
    return empty
