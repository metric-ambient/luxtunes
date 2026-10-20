"""Keep LuxTunes Media folder organized (Preferences → Advanced).

Every song file inside the media folder lives at

    <media folder>/Music/<Album Artist>/<Album>/<disc-track Name>.<ext>

using the same naming rules as the media folder an imported iTunes library
already has, so imported files stay where they are and new edits land where
they belong. The rules were checked against a real library of several
thousand songs:

  * Artist folder: Album Artist, else Artist, else "Unknown Artist";
    "Compilations" for songs marked "Part of a compilation".
  * Album folder: Album, else "Unknown Album".
  * File name: "07 Name", or "1-07 Name" when the song has a disc number and
    the album has more than one disc (or the disc count is blank).
  * Folder names are cut to 40 characters and file names (without the
    extension) to 36, then trailing spaces are removed.
  * / \\ : * ? " < > | ; and curly quotes become "_". A leading "." becomes
    "_", and so does a trailing "." on a folder name.
  * Folder names are matched ignoring capitalisation (Windows ignores it),
    so "Riot!" and "RIOT!" share one folder, whichever exists first.
  * If another file already has the name, " 1", " 2", ... is added.

Movies go to <media folder>/Movies/<Name>.<ext>. Files outside the media
folder are never moved. Folders left empty by a move are removed.

No Qt in here, so it is unit-tested directly.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from luxtunes.core import library
from luxtunes.core.paths import find_file

log = logging.getLogger(__name__)

DEFAULT_MEDIA_FOLDER = "~/Music/LuxTunes/LuxTunes Media"
FOLDER_MAX = 40
FILE_MAX = 36
INVALID = set('/\\:*?"<>|;') | {"‘", "’", "´", "“", "”"}
# Files that don't count when deciding whether a folder is empty.
JUNK_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}

KEY_ENABLED = "keep_media_organized"
KEY_FOLDER = "media_folder"


# ---- Settings (kept in the library) ---------------------------------

def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM library_info WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute("INSERT INTO library_info (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def is_enabled(conn: sqlite3.Connection) -> bool:
    """"Keep LuxTunes Media folder organized" (on unless turned off)."""
    return _get(conn, KEY_ENABLED) != "0"


def set_enabled(conn: sqlite3.Connection, on: bool) -> None:
    _set(conn, KEY_ENABLED, "1" if on else "0")


def media_folder(conn: sqlite3.Connection) -> Path:
    return Path(os.path.expanduser(_get(conn, KEY_FOLDER) or DEFAULT_MEDIA_FOLDER))


def set_media_folder(conn: sqlite3.Connection, folder: str | os.PathLike) -> None:
    _set(conn, KEY_FOLDER, str(Path(os.path.expanduser(str(folder)))))


# ---- Names ----------------------------------------------------------------------

def safe_name(text: str, limit: int, folder: bool) -> str:
    """One folder or file name, made by the naming rules above."""
    text = "".join("_" if (ch in INVALID or ord(ch) < 32) else ch for ch in text)
    if text.startswith("."):
        text = "_" + text[1:]
    text = text[:limit].rstrip(" \u00a0")
    if folder and text.endswith("."):
        text = text[:-1] + "_"
    return text or "_"


def _number(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def file_stem(track: dict) -> str:
    """'07 Name' or '1-07 Name' (before cutting to length)."""
    name = track.get("name") or "Untitled"
    number = _number(track.get("track_number"))
    if not number:
        return name
    prefix = f"{number:02d} "
    disc, discs = _number(track.get("disc_number")), _number(track.get("disc_count"))
    if disc and (discs is None or discs > 1):
        prefix = f"{disc}-{prefix}"
    return prefix + name


def relative_parts(track: dict) -> list[str]:
    """Where the song belongs, as folder names plus the file name (no extension)."""
    if track.get("media_kind") == "movie":
        return ["Movies", safe_name(track.get("name") or "Untitled", FILE_MAX, folder=False)]
    if track.get("media_kind") == "tvshow":
        # TV Shows/<Show>/Season <n>/<episode> <Name>
        show = track.get("show") or track.get("album_artist") or track.get("artist") or "Unknown TV Show"
        season = _number(track.get("season"))
        season_folder = f"Season {season}" if season else (track.get("album") or "Unknown Season")
        episode = _number(track.get("episode_number"))
        name = track.get("name") or "Untitled"
        stem = f"{episode:02d} {name}" if episode else name
        return ["TV Shows", safe_name(show, FOLDER_MAX, True), safe_name(season_folder, FOLDER_MAX, True),
                safe_name(stem, FILE_MAX, False)]
    if track.get("compilation"):
        artist = "Compilations"
    else:
        artist = track.get("album_artist") or track.get("artist") or "Unknown Artist"
    album = track.get("album") or "Unknown Album"
    return ["Music", safe_name(artist, FOLDER_MAX, True), safe_name(album, FOLDER_MAX, True),
            safe_name(file_stem(track), FILE_MAX, False)]


def _key(name: str) -> str:
    """Names compared the way Windows compares them: ignoring case and
    how accented letters are encoded (é as one character or e + accent)."""
    return unicodedata.normalize("NFC", name).casefold()


def _existing_folder(parent: Path, name: str) -> str:
    """`name`, or the spelling of a folder already there that differs only in case."""
    if (parent / name).is_dir():
        return name
    wanted = _key(name)
    try:
        for entry in os.scandir(parent):
            if entry.is_dir() and _key(entry.name) == wanted:
                return entry.name
    except OSError:
        pass
    return name


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def ideal_path(root: Path, track: dict, extension: str) -> Path:
    """Where the song belongs, before any " 1" is added for a name clash."""
    parts = relative_parts(track)
    folder = root
    for name in parts[:-1]:
        folder = folder / _existing_folder(folder, name)
    return folder / (parts[-1] + extension)


def target_path(root: Path, track: dict, current: str) -> Path:
    """The organized path for this song's file (which is at `current`)."""
    parts = relative_parts(track)
    extension = Path(current).suffix
    target = ideal_path(root, track, extension)
    folder = target.parent
    copy = 1
    while target.exists() and not _same_file(target, Path(current)):
        target = folder / f"{parts[-1]} {copy}{extension}"
        copy += 1
    return target


def is_inside(path: str | os.PathLike, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# ---- Moving files -----------------------------------------------------------------

class OrganizeError(Exception):
    """A file couldn't be moved (permissions, disk full, ...)."""


@dataclass
class Move:
    track_id: int
    name: str
    source: str
    target: str


def planned_move(conn: sqlite3.Connection, track: dict, root: Path | None = None) -> Move | None:
    """The move that would organize this song, or None if it's already in place,
    its file is missing, or it lives outside the media folder."""
    root = root or media_folder(conn)
    current = find_file(track.get("location"))
    if current is None or not is_inside(current, root):
        return None
    target = target_path(root, track, current)
    if unicodedata.normalize("NFC", str(target)) == unicodedata.normalize("NFC", current):
        return None
    return Move(track["id"], track.get("name") or "", current, str(target))


def _remove_empty_folders(folder: Path, root: Path) -> None:
    """Remove `folder` and its parents while they're empty, stopping at
    <root>/Music (or Movies) so the main folders always stay."""
    root = root.resolve()
    folder = folder.resolve()
    while folder.parent != root and folder != root and is_inside(folder, root):
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        if any(e.name.lower() not in JUNK_FILES or e.is_dir() for e in entries):
            return
        try:
            for e in entries:
                os.remove(e.path)
            folder.rmdir()
        except OSError:
            return
        log.info("removed empty folder %s", folder)
        folder = folder.parent


def apply_move(conn: sqlite3.Connection, move: Move, root: Path | None = None) -> None:
    """Move the file, then point the library at its new place. Raises OrganizeError."""
    root = root or media_folder(conn)
    source, target = Path(move.source), Path(move.target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not _same_file(source, target):
            raise OrganizeError(f"{target.name} already exists")
        if _same_file(source, target):
            # Only the capitalisation changes: go through a temporary name.
            temporary = source.with_name(source.name + ".luxtunes-rename")
            os.rename(source, temporary)
            os.rename(temporary, target)
        else:
            shutil.move(str(source), str(target))
    except OSError as error:
        raise OrganizeError(error.strerror or str(error)) from error
    library.set_location(conn, move.track_id, str(target))
    log.info("organized %r: %s -> %s", move.name, source, target)
    if source.parent != target.parent:
        _remove_empty_folders(source.parent, root)


def organize_track(conn: sqlite3.Connection, track_id: int) -> str | None:
    """After an edit: move the song's file to where it now belongs (if "Keep
    LuxTunes Media folder organized" is on). Returns None, or why it failed."""
    if not is_enabled(conn):
        return None
    track = library.load_track(conn, track_id)
    if track is None:
        return None
    move = planned_move(conn, track)
    if move is None:
        return None
    try:
        apply_move(conn, move)
    except OrganizeError as error:
        log.warning("couldn't organize %r: %s", move.name, error)
        return str(error)
    return None


def plan_library(conn: sqlite3.Connection) -> list[Move]:
    """Every move "Organize Library" would make (a preview: nothing moves)."""
    root = media_folder(conn)
    tracks = (dict(r) for r in conn.execute("SELECT * FROM tracks ORDER BY id"))
    return [m for m in (planned_move(conn, t, root) for t in tracks) if m is not None]


def organize_library(conn: sqlite3.Connection, track_ids: list[int],
                     progress=None) -> tuple[list[int], list[str]]:
    """Organize these songs' files ("Organize Library"). Each target is worked
    out just before its move, so earlier moves (new folders, names now
    taken) are taken into account. progress(done, total) may return False
    to stop early. Returns (ids of the songs moved, problems)."""
    root = media_folder(conn)
    moved, problems = [], []
    for done, track_id in enumerate(track_ids):
        if progress is not None and progress(done, len(track_ids)) is False:
            break
        track = library.load_track(conn, track_id)
        move = planned_move(conn, track, root) if track else None
        if move is None:
            continue
        try:
            apply_move(conn, move, root)
            moved.append(track_id)
        except OrganizeError as error:
            problems.append(f"{move.name}: {error}")
    return moved, problems
