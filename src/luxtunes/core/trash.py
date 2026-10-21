"""Moving files to the Trash (gio trash), saying why when it can't.

The desktop's Trash works per drive: on another drive the file goes to a
.Trash-<your user number> folder at the top of that drive, which needs to be
allowed there. Some drives (e.g. mounted by the system under /mnt with the top
folder owned by root) have no Trash LuxTunes can use; then the file stays.

No Qt in here.
"""

from __future__ import annotations

import os
import subprocess

from luxtunes.core.paths import find_file

NO_TRASH = "no_trash"          # the drive has no Trash that can be used
MISSING = "missing"            # the file isn't where the library says
FAILED = "failed"              # anything else (permissions, ...)


def _mount_of(path: str) -> str:
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def explain(location: str | None, kind: str, message: str = "") -> str:
    """What to tell you about a file that wasn't moved to the Trash."""
    if kind == MISSING:
        return (f"It couldn't be found at “{location or 'no location'}”. It may have been moved, "
                "renamed or deleted already, or its drive isn't connected.")
    if kind == NO_TRASH:
        return (f"The drive it's on (“{_mount_of(location)}”) has no Trash LuxTunes can use, "
                "so the file was left where it is.")
    return f"It couldn't be moved to the Trash: {message or 'unknown error'}."


def move_to_trash(location: str | None) -> tuple[str, str] | None:
    """Move a song's file to the Trash. None if it worked, else (kind, gio's message)."""
    path = find_file(location)
    if path is None:
        return MISSING, ""
    try:
        done = subprocess.run(["gio", "trash", path], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return FAILED, "the “gio” command isn't installed"
    except (OSError, subprocess.SubprocessError) as error:
        return FAILED, str(error)
    if done.returncode == 0:
        return None
    message = (done.stderr or "").strip().splitlines()
    message = message[-1] if message else f"gio exited with {done.returncode}"
    # gio's message starts "gio: file:///…: "; keep what it says after that.
    if message.startswith("gio: ") and ": " in message[5:]:
        message = message[5:].split(": ", 1)[1]
    lowered = message.lower()
    if "trash" in lowered and ("not supported" in lowered or "unable to find or create" in lowered
                               or "trash directory" in lowered):
        return NO_TRASH, message
    if not os.path.exists(path):
        return MISSING, message
    return FAILED, message


def delete_permanently(location: str | None) -> str | None:
    """Delete a file for good (only after you've said so). None if it worked."""
    path = find_file(location)
    if path is None:
        return "it couldn't be found"
    try:
        os.remove(path)
    except OSError as error:
        return error.strerror or str(error)
    return None
