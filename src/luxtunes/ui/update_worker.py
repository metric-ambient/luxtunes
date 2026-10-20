"""Help → Check for Updates…, in the background so the window stays usable.
Only started after you've said yes to going online (see MainWindow)."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from luxtunes.core import updater


class CheckWorker(QThread):
    found = Signal(object)          # updater.Release
    failed = Signal(str)

    def __init__(self, approved: bool, parent=None):
        super().__init__(parent)
        self.approved = approved

    def run(self):
        try:
            self.found.emit(updater.latest_release(approved=self.approved))
        except (updater.UpdateError, updater.NotApproved) as error:
            self.failed.emit(str(error))
        except Exception as error:      # never let a crash in the thread go unreported
            self.failed.emit(f"{type(error).__name__}: {error}")


class InstallWorker(QThread):
    progress = Signal(int, int)     # bytes downloaded, bytes in all
    installing = Signal()           # downloaded; now pip is installing it
    done = Signal()
    failed = Signal(str)

    def __init__(self, release: updater.Release, approved: bool, parent=None):
        super().__init__(parent)
        self.release = release
        self.approved = approved

    def run(self):
        import tempfile
        from pathlib import Path
        try:
            with tempfile.TemporaryDirectory(prefix="luxtunes-update-") as folder:
                wheel = updater.download(self.release, Path(folder), approved=self.approved,
                                         progress=self.progress.emit)
                self.installing.emit()
                updater.install(wheel)
            self.done.emit()
        except (updater.UpdateError, updater.NotApproved) as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
