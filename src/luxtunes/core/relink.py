"""Finding other missing files after you locate one ("search for
the remaining files in this location?").

When you point LuxTunes at one missing song's file, the old and new paths
usually differ in the same way for its neighbours: a drive or folder was
renamed or moved ("E:/Users/Alex/Music" became "/home/alex/Music"). So,
for every other missing song, we try, in order:

  1. the same change of folder applied to its old path;
  2. a file with the same name in the folder you chose;
  3. a file in that folder whose tags have the same title and artist.

Only songs whose files are missing are considered, and nothing is changed
here: the caller shows what was found and saves it if you agree.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath, PureWindowsPath

from luxtunes.core.paths import find_file

AUDIO_VIDEO = {".mp3", ".m4a", ".m4b", ".m4p", ".aac", ".flac", ".wav", ".aif", ".aiff", ".ogg",
               ".oga", ".opus", ".m4v", ".mp4", ".mov", ".mkv", ".avi", ".webm", ".wmv", ".mpg", ".mpeg"}


def _parts(path: str) -> list[str]:
    """Path components, for Windows-style ("E:\\a\\b" or "E:/a/b") and Linux paths."""
    if len(path) > 1 and path[1] == ":" or "\\" in path:
        return list(PureWindowsPath(path).parts)
    return list(PurePosixPath(path).parts)


def folder_change(old_path: str, new_path: str) -> tuple[list[str], list[str]] | None:
    """The part of the old path that was replaced, and its replacement: the
    paths are compared from the end, and what's left over on each side is the
    change. ("E:/Music/A/B/01.mp3", "/home/b/Music/A/B/01.mp3") ->
    (["E:\\\\"], ["/", "home", "b"]) after the common "Music/A/B/01.mp3"."""
    old, new = _parts(old_path), _parts(new_path)
    common = 0
    while (common < min(len(old), len(new)) - 1
           and old[-1 - common] == new[-1 - common]):
        common += 1
    if common == 0:
        # Different file names: still use the folders (the songs moved together).
        return old[:-1], new[:-1]
    return old[:-common], new[:-common]


def apply_change(old_location: str, change) -> str | None:
    """Where `old_location` would be after the same change, or None if it
    didn't live under the changed folder."""
    old_prefix, new_prefix = change
    parts = _parts(old_location)
    if len(parts) <= len(old_prefix):
        return None
    if [p.casefold() for p in parts[:len(old_prefix)]] != [p.casefold() for p in old_prefix]:
        return None
    return os.path.join(*new_prefix, *parts[len(old_prefix):])


def _tag_index(folder: str) -> dict[tuple[str, str], str]:
    """(title, artist) -> file, for the music files directly in `folder`."""
    index = {}
    try:
        from luxtunes.core.tags import TagError, read_fields
    except ImportError:
        return index
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return index
    for name in names:
        path = os.path.join(folder, name)
        if os.path.splitext(name)[1].lower() not in AUDIO_VIDEO or not os.path.isfile(path):
            continue
        try:
            fields = read_fields(path)
        except (TagError, OSError):
            continue
        key = ((fields.get("name") or "").strip().casefold(),
               (fields.get("artist") or "").strip().casefold())
        if key[0]:
            index.setdefault(key, path)
    return index


def find_others(old_location: str, new_path: str, missing: list[dict],
                use_tags: bool = True) -> dict[int, str]:
    """For the missing tracks, the files that look like theirs: track id -> path."""
    change = folder_change(old_location or "", new_path)
    folder = os.path.dirname(new_path)
    try:
        by_name = {n.casefold(): os.path.join(folder, n) for n in os.listdir(folder)}
    except OSError:
        by_name = {}
    tag_index = None
    taken = {os.path.realpath(new_path)}
    found = {}
    for track in missing:
        location = track.get("location") or ""
        candidate = None
        moved = apply_change(location, change) if change and location else None
        if moved:
            candidate = find_file(moved)
        if candidate is None and location:
            candidate = by_name.get(_parts(location)[-1].casefold())
        if candidate is None and use_tags and track.get("name"):
            if tag_index is None:
                tag_index = _tag_index(folder)
            candidate = tag_index.get(((track.get("name") or "").strip().casefold(),
                                       (track.get("artist") or "").strip().casefold()))
        if candidate and os.path.isfile(candidate) and os.path.realpath(candidate) not in taken:
            taken.add(os.path.realpath(candidate))
            found[track["id"]] = candidate
    return found
