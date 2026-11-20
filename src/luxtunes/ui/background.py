"""Run slow work (disks, USB, programs like lsblk) off the window's thread.

    background.run(work, done, owner)

work() runs on its own thread; done(result) then runs on the window's thread.
If work() raises, done gets the exception instead of a result. A daemon thread
is used rather than a QThread so a call stuck on a sleeping disk or a dead
network share can never stop LuxTunes from quitting. If `owner` is gone by
the time work() finishes, the result is dropped.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


class _Relay(QObject):
    finished = Signal(object)


def run(work, done, owner: QObject) -> None:
    relay = _Relay(owner)           # lives on the window's thread, so `done` runs there

    def deliver(result):
        try:
            done(result)
        finally:
            relay.deleteLater()

    relay.finished.connect(deliver)

    def thread():
        try:
            result = work()
        except Exception as error:          # handed to `done`, which decides
            result = error
        try:
            relay.finished.emit(result)
        except RuntimeError:                # the owner (and relay) were closed meanwhile
            log.debug("background result dropped: its window is gone")

    threading.Thread(target=thread, daemon=True).start()
