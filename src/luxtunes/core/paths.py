"""Finding music files on disk."""

from __future__ import annotations

import os
from pathlib import Path


def data_home() -> Path:
    """~/.local/share, or $XDG_DATA_HOME: where LuxTunes keeps its own files."""
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def cache_home() -> Path:
    """~/.cache, or $XDG_CACHE_HOME: files LuxTunes can always make again."""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def make_private(path: Path) -> None:
    """Only you can read it: folders 700, files 600 (as far as the disk allows)."""
    try:
        path.chmod(0o700 if path.is_dir() else 0o600)
    except OSError:
        pass                    # e.g. a FAT drive, which has no permissions


def trim_log(path: Path, most: int = 1024 * 1024, keep: int = 256 * 1024) -> None:
    """A log over `most` bytes is cut back to its newest `keep` bytes."""
    try:
        if path.stat().st_size <= most:
            return
        with open(path, "rb") as handle:
            handle.seek(-keep, os.SEEK_END)
            tail = handle.read()
    except OSError:
        return
    tail = tail[tail.find(b"\n") + 1:]          # start at a whole line
    try:
        path.write_bytes(b"(older entries removed)\n" + tail)
    except OSError:
        pass


def resolve_case_insensitive(path: Path) -> Path:
    """Find `path` on disk even if folder/file capitalisation differs.

    Windows ignores case, so a library imported from Windows can contain
    "What Separates Me From You" in the XML while the folder on disk is
    "What Separates Me from You". Linux treats those as different names.
    Returns the real on-disk path if a match is found, otherwise `path` unchanged.
    """
    path = Path(path)
    if path.exists():
        return path
    current = Path(path.anchor) if path.is_absolute() else Path(".")
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        candidate = current / part
        if not candidate.exists():
            try:
                match = next(
                    (p for p in current.iterdir() if p.name.lower() == part.lower()), None
                )
            except OSError:
                return path
            if match is None:
                return path
            candidate = match
        current = candidate
    return current


def find_file(location: str | None) -> str | None:
    """The real path of a track's file, or None if it can't be found."""
    if not location:
        return None
    if os.path.exists(location):
        return location
    resolved = resolve_case_insensitive(Path(location))
    return str(resolved) if resolved.exists() else None


def file_uri(path: str) -> str:
    """file:///… with every special character encoded. dbus-send splits its
    array arguments at commas, so a comma left as it is ("The Lion, the
    Witch…") would hand the file manager two broken paths."""
    return Path(os.path.abspath(path)).as_uri()


def nearest_existing_folder(location: str | None) -> str:
    """The closest folder that exists on the way to `location` (for file dialogs)."""
    folder = Path(location).parent if location else Path.home()
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return str(folder if folder.exists() else Path.home())
