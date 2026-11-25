"""A log of each iPod or phone sync, so what went wrong can be seen afterwards.

Each sync writes its own file in the "Sync logs" folder beside the library
(~/.local/share/luxtunes/Sync logs/): when each step started, every file
copied with its size, time and speed, what was removed, and any problem in
full. Only the newest KEEP logs stay. Help → Show Sync Logs opens the folder.

The logs only describe your own library (song names, folders) and stay on
this computer; they're private (readable only by you).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from luxtunes.core.paths import make_private

FOLDER = "Sync logs"
KEEP = 30
FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s"


def folder_for(db_path: str | None) -> Path | None:
    """The logs folder beside the library, or None for a library in memory."""
    if not db_path or db_path == ":memory:":
        return None
    return Path(db_path).expanduser().parent / FOLDER


def folder_of(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    return folder_for(row[2] if row else None)


def _safe(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_." else "_" for c in name).strip()
    return keep[:60] or "device"


class SyncLog:
    """with SyncLog(folder, "Pixel 7") as path: ... Everything LuxTunes logs
    meanwhile (from any part of it) goes into the file, in full detail."""

    def __init__(self, folder: Path | None, device_name: str):
        self.folder = folder
        self.device_name = device_name
        self.path: Path | None = None
        self._handler: logging.Handler | None = None
        self._old_level = None

    def __enter__(self) -> Path | None:
        if self.folder is None:
            return None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            make_private(self.folder)
            stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
            self.path = self.folder / f"{stamp} {_safe(self.device_name)}.log"
            handler = logging.FileHandler(self.path, encoding="utf-8")
        except OSError:
            self.path = None
            return None
        make_private(self.path)
        handler.setFormatter(logging.Formatter(FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("luxtunes")
        self._old_level = logger.level
        if logger.getEffectiveLevel() > logging.DEBUG:
            logger.setLevel(logging.DEBUG)       # the file gets everything...
        for terminal in logging.getLogger().handlers:
            if terminal.level == logging.NOTSET:
                terminal.setLevel(logging.INFO)  # ...the terminal stays as quiet as before
        logger.addHandler(handler)
        self._handler = handler
        logging.getLogger("luxtunes.sync_log").info("sync of “%s” started (LuxTunes %s)",
                                                     self.device_name, _version())
        return self.path

    def __exit__(self, kind, error, trace):
        log = logging.getLogger("luxtunes.sync_log")
        if error is not None and kind is not None:
            log.error("the sync ended with an error", exc_info=(kind, error, trace))
        log.info("sync of “%s” ended", self.device_name)
        if self._handler is not None:
            logger = logging.getLogger("luxtunes")
            logger.removeHandler(self._handler)
            self._handler.close()
            if self._old_level is not None:
                logger.setLevel(self._old_level)
        _keep_newest(self.folder)
        return False


def _version() -> str:
    from luxtunes import __version__
    return __version__


def _keep_newest(folder: Path | None, keep: int = KEEP) -> None:
    if folder is None:
        return
    try:
        logs = sorted(folder.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in logs[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass
