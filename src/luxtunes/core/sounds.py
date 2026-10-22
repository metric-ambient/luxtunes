"""The "done" sound (when a CD finishes importing or a sync is complete).

Plays the notification sound chosen in the desktop's sound settings (Linux
Mint: System Settings → Sounds), or a standard one, with the first sound
player that works (PipeWire's pw-play, PulseAudio's paplay, ...). Whether it
plays at all is LuxTunes' own choice (Preferences → General), so the
desktop's "notification sounds" switch, which is about desktop
notifications, isn't consulted. Never waits for the sound to finish.

Preferences → General → Test Sound runs the same steps and says what
happened, for when no sound is heard. No Qt in here.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

FALLBACK_SOUNDS = (
    "/usr/share/mint-artwork/sounds/notification.oga",
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "/usr/share/sounds/freedesktop/stereo/message.oga",
)

# (program, arguments before the file name)
PLAYERS = (
    ("pw-play", []),
    ("paplay", []),
    ("canberra-gtk-play", ["-f"]),
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
)


def system_env() -> dict:
    """The environment for running the system's own programs. Inside the
    AppImage, the bundle's libraries are first on the library path, which can
    break programs that aren't LuxTunes' (the original path is kept aside)."""
    env = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def _gsetting(schema: str, key: str) -> str | None:
    tool = shutil.which("gsettings")
    if not tool:
        return None
    try:
        result = subprocess.run([tool, "get", schema, key], capture_output=True, text=True,
                                timeout=5, env=system_env())
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().strip("'")


def candidate_sounds() -> list[str]:
    """Sound files to try, the desktop's chosen one first."""
    chosen = _gsetting("org.cinnamon.sounds", "notification-file")
    found = []
    for path in ([chosen] if chosen else []) + list(FALLBACK_SOUNDS):
        if path and Path(path).is_file() and path not in found:
            found.append(path)
    return found


def desktop_sound() -> str | None:
    sounds = candidate_sounds()
    return sounds[0] if sounds else None


def play_now(timeout: float = 10) -> tuple[bool, list[str]]:
    """Try each sound with each installed player until one plays. Waits.
    Returns (played, what happened, step by step)."""
    steps = []
    sounds = candidate_sounds()
    if not sounds:
        return False, ["No sound file was found (looked for the desktop's notification "
                       "sound and the standard ones)."]
    players = [(shutil.which(p), p, args) for p, args in PLAYERS]
    players = [(path, name, args) for path, name, args in players if path]
    if not players:
        return False, ["No sound player is installed (pw-play, paplay, canberra-gtk-play "
                       "or ffplay)."]
    for sound in sounds:
        for path, name, args in players:
            try:
                done = subprocess.run([path, *args, sound], capture_output=True, text=True,
                                      timeout=timeout, env=system_env())
            except subprocess.TimeoutExpired:
                steps.append(f"{name} {sound}: still playing after {timeout:g} s, stopped.")
                continue
            except OSError as error:
                steps.append(f"{name} {sound}: couldn't start ({error}).")
                continue
            if done.returncode == 0:
                steps.append(f"Played {sound} with {name}.")
                return True, steps
            reason = (done.stderr or "").strip().splitlines()
            steps.append(f"{name} {sound}: failed ({reason[-1] if reason else done.returncode}).")
    return False, steps


def play_done_sound() -> bool:
    """Play the sound in the background. True if there's something to try."""
    if not candidate_sounds():
        log.info("done sound: no sound file found")
        return False

    def run():
        played, steps = play_now()
        for step in steps:
            (log.info if played else log.warning)("done sound: %s", step)

    threading.Thread(target=run, daemon=True).start()
    return True
