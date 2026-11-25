"""Run slow work (disks, USB, programs like lsblk) off the window's thread.

    background.run(work, done, owner)

work() runs on its own thread; done(result) then runs on the window's thread.
If work() raises, done gets the exception instead of a result. A daemon thread
is used rather than a QThread so a call stuck on a sleeping disk or a dead
network share can never stop LuxTunes from quitting. If `owner` is gone by
the time work() finishes, the result is dropped.

The work's thread never touches Qt: it only puts its result in a plain Python
queue, which a timer on the window's thread empties. (It used to emit a Qt
signal from the thread. The device check does this every 2 seconds, and very
rarely, after hours of playing, that crashed LuxTunes: crash.log showed a
"Segmentation fault" in this file's emit. Plain threads and Qt objects are
best kept apart.)
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading

from PySide6.QtCore import QCoreApplication, QTimer

log = logging.getLogger(__name__)

POLL_MS = 40                      # how often finished work is looked for, while any is running

_results: queue.SimpleQueue = queue.SimpleQueue()      # (ticket, result), from the threads
_waiting: dict[int, tuple] = {}                        # ticket -> (done, owner): window's thread only
_tickets = itertools.count(1)
_timer: QTimer | None = None


def _alive(owner) -> bool:
    if owner is None:
        return True
    try:
        import shiboken6
        return shiboken6.isValid(owner)
    except ImportError:               # (always there with PySide6)
        return True


def _deliver() -> None:
    """On the window's thread: hand each finished result to its `done`."""
    while True:
        try:
            ticket, result = _results.get_nowait()
        except queue.Empty:
            break
        done, owner = _waiting.pop(ticket, (None, None))
        if done is None:
            continue
        if not _alive(owner):
            log.debug("background result dropped: its window is gone")
            continue
        try:
            done(result)
        except Exception:             # one bad handler mustn't stop the others
            log.exception("handling a background result failed")
    if not _waiting and _timer is not None:
        _timer.stop()                 # nothing running: no need to keep looking


def _start_timer() -> None:
    global _timer
    if _timer is None:
        app = QCoreApplication.instance()
        _timer = QTimer(app)
        _timer.setInterval(POLL_MS)
        _timer.timeout.connect(_deliver)
        app.aboutToQuit.connect(_timer.stop)
    if not _timer.isActive():
        _timer.start()


def run(work, done, owner) -> None:
    """Call from the window's thread."""
    ticket = next(_tickets)
    _waiting[ticket] = (done, owner)
    _start_timer()

    def thread():
        try:
            result = work()
        except Exception as error:          # handed to `done`, which decides
            result = error
        _results.put((ticket, result))       # plain Python: safe from any thread

    threading.Thread(target=thread, daemon=True, name="luxtunes-background").start()
