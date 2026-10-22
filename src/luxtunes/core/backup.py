"""Weekly copies of the library file, so years of ratings, play counts and
playlists survive a damaged file.

At every start: if the newest copy is a week old or more (or there's none),
a new one is made, named by date ("library 2026-10-22.db"), in a "Backups"
folder beside the library. Only the newest four are kept (about a month).
File → Library → Restore from Backup… puts one back (restore below); the
library as it was is kept beside the backups first.

No Qt in here.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

FOLDER = "Backups"
KEEP = 4
EVERY = timedelta(days=7)
NAME = re.compile(r"^library (\d{4})-(\d{2})-(\d{2})\.db$")


def library_path(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else None       # "" for an in-memory library


def folder_for(conn: sqlite3.Connection) -> Path | None:
    source = library_path(conn)
    return source.parent / FOLDER if source else None


class RestoreError(Exception):
    """The chosen file isn't a LuxTunes library this version can use."""


def check(path: Path) -> int:
    """How many songs the backup holds; RestoreError if it isn't usable."""
    from luxtunes.core.db import SCHEMA_VERSION
    try:
        other = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            version = other.execute("PRAGMA user_version").fetchone()[0]
            songs = other.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            other.execute("SELECT COUNT(*) FROM playlists").fetchone()
        finally:
            other.close()
    except sqlite3.Error:
        raise RestoreError("That file isn't a LuxTunes library.") from None
    if version > SCHEMA_VERSION:
        raise RestoreError("That library is from a newer LuxTunes. Update LuxTunes first.")
    return songs


def restore(conn: sqlite3.Connection, path: Path, today: date | None = None) -> Path:
    """Replace the library with the backup at `path`, in place (LuxTunes keeps
    running). The library as it was is saved first, as "library <date> before
    restore.db" beside the backups (never removed automatically). Returns that file."""
    from luxtunes.core import db
    path = Path(path)
    check(path)
    folder = folder_for(conn)
    if folder is None:
        raise RestoreError("This library isn't stored in a file.")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (today or date.today()).isoformat()
    kept = folder / f"library {stamp} before restore.db"
    n = 2
    while kept.exists():
        kept = folder / f"library {stamp} before restore {n}.db"
        n += 1
    copy = sqlite3.connect(kept)
    try:
        conn.backup(copy)
    finally:
        copy.close()
    source = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        source.backup(conn)                 # the backup's contents into the open library
    finally:
        source.close()
    db._migrate(conn)                       # an older backup gets this version's columns
    log.info("library restored from %s (the previous one kept as %s)", path, kept)
    return kept


def backups(folder: Path) -> list[tuple[date, Path]]:
    """The backups in the folder, oldest first."""
    found = []
    if folder.is_dir():
        for path in folder.iterdir():
            match = NAME.match(path.name)
            if match:
                try:
                    found.append((date(*map(int, match.groups())), path))
                except ValueError:
                    continue
    return sorted(found)


def weekly(conn: sqlite3.Connection, today: date | None = None) -> Path | None:
    """Make this week's backup if it's due, then keep only the newest four.
    Returns the new backup, or None if none was needed (or it couldn't be made)."""
    today = today or date.today()
    source = library_path(conn)
    if source is None:
        return None
    folder = source.parent / FOLDER
    existing = backups(folder)
    if existing and today - existing[-1][0] < EVERY:
        return None
    target = folder / f"library {today.isoformat()}.db"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".db.part")
        copy = sqlite3.connect(partial)
        try:
            conn.backup(copy)              # consistent even while the library is open
        finally:
            copy.close()
        partial.replace(target)
    except (OSError, sqlite3.Error) as error:
        log.warning("library backup failed: %s", error)
        return None
    log.info("library backed up to %s", target)
    for _, old in backups(folder)[:-KEEP]:
        try:
            old.unlink()
            log.info("removed old library backup %s", old)
        except OSError as error:
            log.warning("couldn't remove old backup %s: %s", old, error)
    return target
