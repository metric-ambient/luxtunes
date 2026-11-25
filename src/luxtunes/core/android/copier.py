"""Copying files to a phone in a way that can always be stopped.

A phone is reached through the desktop's MTP layer (gvfs), which keeps a
file being written and only sends it to the phone when it's closed. For a big
file (an audiobook, a movie) that last step can take minutes, and if the
phone stops answering it never ends: a copy done by LuxTunes itself would be
stuck there, so Stop couldn't work and the only way out was pulling the cable.

So the copying is done by a small helper program (a second Python process),
one file at a time, while LuxTunes watches:
- Stop ends the helper at once, mid-file or while the phone is still taking
  the file; the half-copied file is removed and the sync stops cleanly.
- If the phone stops taking data (no progress for STALL seconds), or takes far
  longer than it should to finish a file, the helper is ended and the copy is
  reported as a PhoneStalled problem instead of waiting forever.
- Progress (bytes written, then "finishing on the phone") is reported, so a
  long copy shows how far it got.

The helper stays running between files (starting one per song would add up
over thousands of songs) and is started again after it's been ended.

Run from source, the helper is Python running HELPER. In the packaged app
(the AppImage), sys.executable is LuxTunes itself, not Python, so the helper
is LuxTunes started with HELPER_FLAG, which runs HELPER and nothing else (see
__main__.py). (0.9.3 started `LuxTunes -I -c …` there, which just opened and
closed another LuxTunes: every copy failed with "the copying helper ended
unexpectedly".)
"""

from __future__ import annotations

import collections
import json
import logging
import queue
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)

STALL = 120            # seconds without any progress before giving up on a file
FINISH_RATE = 2_000_000  # bytes/second the phone can at least take when finishing a file
POLL = 0.2

# The helper: reads one JSON request per line ({"source", "target"}), copies,
# and answers with JSON lines: {"wrote": bytes}, {"closing": true}, then
# {"done": true} or {"error": "...", "errno": n}. Standard library only.
HELPER = r"""
import json, os, sys
CHUNK = 1024 * 1024
def say(**message):
    sys.stdout.write(json.dumps(message) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    request = json.loads(line)
    try:
        wrote = 0
        with open(request["source"], "rb") as src:
            dst = open(request["target"], "wb")
            try:
                while True:
                    block = src.read(CHUNK)
                    if not block:
                        break
                    dst.write(block)
                    wrote += len(block)
                    say(wrote=wrote)
                say(closing=True)
            finally:
                dst.close()
        say(done=True)
    except OSError as error:
        say(error=error.strerror or str(error), errno=error.errno or 0)
"""


HELPER_FLAG = "--luxtunes-copy-helper"


def helper_command() -> list[str]:
    """How to start the helper: Python, or the packaged LuxTunes in helper mode."""
    if getattr(sys, "frozen", False):
        return [sys.executable, HELPER_FLAG]
    return [sys.executable, "-I", "-c", HELPER]


def run_helper() -> int:
    """The helper itself, in the packaged app (LuxTunes started with HELPER_FLAG)."""
    exec(compile(HELPER, "<luxtunes copy helper>", "exec"), {"__name__": "__luxtunes_helper__"})
    return 0


class PhoneStalled(OSError):
    """The phone stopped taking a file (or never finished it)."""


class CopyStopped(Exception):
    """Stop was pressed during the copy."""


class Copier:
    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._lines: queue.Queue | None = None
        self._errors: collections.deque = collections.deque(maxlen=20)   # its last error lines

    def _start(self):
        self._process = subprocess.Popen(
            helper_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
        lines: queue.Queue = queue.Queue()
        out, err = self._process.stdout, self._process.stderr
        errors = self._errors = collections.deque(maxlen=20)

        def read():
            for line in out:
                lines.put(line)
            lines.put(None)                     # the helper ended
        threading.Thread(target=read, daemon=True, name="copier-output").start()

        def read_errors():
            for line in err:
                if line.strip():
                    errors.append(line.rstrip())
        threading.Thread(target=read_errors, daemon=True, name="copier-errors").start()
        self._lines = lines

    def _why_it_ended(self, process) -> str:
        """The helper's exit code and its last error line, for the log and the message."""
        code = None
        if process is not None:
            try:
                code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        time.sleep(0.05)                        # let its last error lines arrive
        last = self._errors[-1] if self._errors else ""
        if self._errors:
            log.error("the copying helper's last output:\n%s", "\n".join(self._errors))
        return ", ".join(part for part in (f"exit code {code}" if code is not None else "",
                                            last[:200]) if part)

    def _end(self):
        """End the helper now (it may be stuck in the phone's MTP layer)."""
        process, self._process, self._lines = self._process, None, None
        if process is None:
            return
        try:
            process.kill()
        except OSError:
            pass
        # Don't wait long: a process stuck in the kernel may take a while to go.
        threading.Thread(target=process.wait, daemon=True).start()

    def close(self):
        process, self._process = self._process, None
        if process is not None:
            try:
                process.stdin.close()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()

    def copy(self, source: str, target: str, size: int, stop=None, progress=None) -> None:
        """Copy `source` to `target` (a path on the phone). stop: a
        threading.Event; progress(wrote, size, finishing). Raises CopyStopped,
        PhoneStalled or OSError; the caller removes a half-copied file."""
        if self._process is None or self._process.poll() is not None:
            self._start()
        started = last = time.monotonic()
        closing_since = None
        wrote = 0
        try:
            self._process.stdin.write(json.dumps({"source": source, "target": target}) + "\n")
            self._process.stdin.flush()
        except OSError as error:
            self._end()
            raise OSError(f"couldn't start copying ({error})") from error
        while True:
            if stop is not None and stop.is_set():
                log.info("copy of %s stopped after %d of %d bytes%s", target, wrote, size,
                         " (the phone was still taking it)" if closing_since else "")
                self._end()
                raise CopyStopped()
            try:
                line = self._lines.get(timeout=POLL)
            except queue.Empty:
                line = ""
            now = time.monotonic()
            if line is None:
                process = self._process
                why = self._why_it_ended(process)
                self._end()
                raise OSError("the copying helper ended unexpectedly" + (f" ({why})" if why else ""))
            if line:
                message = json.loads(line)
                if "wrote" in message:
                    wrote, last = message["wrote"], now
                    if progress is not None:
                        progress(wrote, size, False)
                elif message.get("closing"):
                    closing_since = last = now
                    log.debug("%s: all %d bytes written, the phone is taking the file "
                              "(%.1f s so far)", target, wrote, now - started)
                    if progress is not None:
                        progress(wrote, size, True)
                elif message.get("done"):
                    seconds = now - started
                    log.info("copied %s: %.1f MB in %.1f s (%.1f MB/s)%s", target, size / 1e6,
                             seconds, size / 1e6 / seconds if seconds else 0,
                             f", {now - closing_since:.1f} s of it finishing on the phone"
                             if closing_since and now - closing_since >= 1 else "")
                    return
                elif "error" in message:
                    log.warning("copy of %s failed after %d bytes: %s", target, wrote,
                                message["error"])
                    raise OSError(message.get("errno") or 0, message["error"])
                continue
            # No news: is the phone still taking the file?
            if closing_since is not None:
                allowed = STALL + size / FINISH_RATE
                if now - closing_since > allowed:
                    log.error("%s: the phone hasn't finished taking the file after %.0f s "
                              "(%d bytes); giving up on it", target, now - closing_since, size)
                    self._end()
                    raise PhoneStalled(f"the phone stopped responding while finishing it "
                                       f"({size / 1e6:,.0f} MB, {now - closing_since:.0f} s)")
            elif now - last > STALL:
                log.error("%s: no progress for %.0f s after %d of %d bytes; giving up on it",
                          target, now - last, wrote, size)
                self._end()
                raise PhoneStalled(f"the phone stopped taking data after "
                                   f"{wrote / 1e6:,.0f} of {size / 1e6:,.0f} MB")
