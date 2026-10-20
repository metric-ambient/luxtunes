"""The desktop's notification sound (e.g. when a CD finishes importing).

Uses the sound chosen in the desktop's sound settings (Linux Mint:
System Settings → Sounds), or the standard "complete" sound, played with
whichever player is installed. Never waits for it to finish. No Qt in here.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

FALLBACK_SOUNDS = (
    "/usr/share/mint-artwork/sounds/notification.oga",
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "/usr/share/sounds/freedesktop/stereo/message.oga",
)

# (program, arguments before the file name)
PLAYERS = (
    ("canberra-gtk-play", ["-f"]),
    ("paplay", []),
    ("pw-play", []),
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
)


def _gsetting(schema: str, key: str) -> str | None:
    tool = shutil.which("gsettings")
    if not tool:
        return None
    try:
        result = subprocess.run([tool, "get", schema, key], capture_output=True, text=True,
                                timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().strip("'")


def desktop_sound() -> str | None:
    """The notification sound file to play, or None if the desktop has
    notification sounds turned off or none can be found."""
    if _gsetting("org.cinnamon.sounds", "notification-enabled") == "false":
        return None
    chosen = _gsetting("org.cinnamon.sounds", "notification-file")
    for path in ([chosen] if chosen else []) + list(FALLBACK_SOUNDS):
        if path and Path(path).is_file():
            return path
    return None


def play_done_sound() -> bool:
    """Play the notification sound in the background. True if it started."""
    sound = desktop_sound()
    if sound is None:
        return False
    for program, arguments in PLAYERS:
        path = shutil.which(program)
        if path:
            try:
                process = subprocess.Popen([path, *arguments, sound], stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                continue
            # Collect it when it ends, without waiting here.
            threading.Thread(target=process.wait, daemon=True).start()
            return True
    return False
