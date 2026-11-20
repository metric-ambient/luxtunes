"""Album art for the Album List, Grid and Cover Flow views, read once and kept.

Reading embedded art means opening the music file and decoding a picture that
is often 1000+ pixels wide, which is slow when a Grid of 1,000+ albums
scrolls past. So:

- Views ask with `notify=<widget>`: a picture that isn't ready yet is read on
  a background thread (the newest requests first, so the tiles on screen win
  when you scroll fast) and the widget repaints when it arrives.
- Each picture is kept on disk as a small thumbnail in ~/.cache/luxtunes/art,
  named after the file's path, size and modification time, so the next start
  doesn't have to open the music files again. A changed file (new artwork)
  gets a new thumbnail. Old thumbnails are trimmed when the folder grows big.
- The most recently used pictures stay in memory, up to MEMORY_BUDGET.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QSize, Qt, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

from luxtunes.core import artwork
from luxtunes.core.paths import cache_home, find_file, make_private

log = logging.getLogger(__name__)

MEMORY_BUDGET = 160 * 1024 * 1024     # decoded pictures kept in memory (bytes)
WORKERS = 2                           # background readers
MAX_QUEUE = 300                       # older requests are dropped (asked again if still on screen)
DISK_LIMIT = 300 * 1024 * 1024        # thumbnails on disk: trimmed back to DISK_KEEP past this
DISK_KEEP = 200 * 1024 * 1024


def cache_folder() -> Path:
    return cache_home() / "luxtunes" / "art"


def _thumbnail_name(path: str, size: int) -> str | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    text = f"{path}\0{st.st_size}\0{st.st_mtime_ns}\0{size}"
    return hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()


def _decode(data: bytes, size: int) -> QImage | None:
    """The picture scaled to fit size x size. Big JPEGs are decoded straight
    at a smaller size, which is much faster than decoding them whole."""
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    original = reader.size()
    if original.isValid() and (original.width() > size or original.height() > size):
        reader.setScaledSize(original.scaled(QSize(size, size), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return None
    if image.width() != size and image.height() != size:   # small art is scaled up, as before
        image = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


def load_image(location: str | None, size: int) -> QImage | None:
    """A track's art at `size`, from the thumbnail cache or its file. Safe to
    call from any thread (it uses QImage, not QPixmap). Never raises."""
    try:
        path = find_file(location)
        if not path:
            return None
        name = _thumbnail_name(path, size)
        if name is None:
            return None
        folder = cache_folder() / name[:2]
        for suffix in (".jpg", ".png"):
            thumb = folder / (name + suffix)
            if thumb.exists():
                image = QImage(str(thumb))
                if not image.isNull():
                    return image
        if (folder / (name + ".none")).exists():
            return None                                  # known: no artwork
        data = artwork.read_embedded_artwork(path)
        image = _decode(data, size) if data else None
        _store(folder, name, image)
        return image
    except Exception:                                    # a picture is never worth a crash
        log.debug("couldn't load artwork for a track", exc_info=True)
        return None


def _store(folder: Path, name: str, image: QImage | None) -> None:
    try:
        new = not folder.parent.exists()
        folder.mkdir(parents=True, exist_ok=True)
        if new:
            make_private(folder.parent)
        if image is None:
            (folder / (name + ".none")).touch()
            return
        alpha = image.hasAlphaChannel()
        final = folder / (name + (".png" if alpha else ".jpg"))
        temporary = final.with_name(final.name + ".part")
        if image.save(str(temporary), "PNG" if alpha else "JPG", -1 if alpha else 92):
            os.replace(temporary, final)
    except OSError:
        pass                                             # no cache today; still works


def trim_disk_cache(limit: int = DISK_LIMIT, keep: int = DISK_KEEP) -> None:
    """Delete the oldest thumbnails once the cache folder is over `limit`."""
    root = cache_folder()
    try:
        files = [(entry.stat().st_mtime, entry.stat().st_size, entry)
                 for sub in root.iterdir() if sub.is_dir() for entry in sub.iterdir()]
    except OSError:
        return
    total = sum(size for _, size, _ in files)
    if total <= limit:
        return
    for _, size, entry in sorted(files, key=lambda f: f[0]):
        try:
            entry.unlink()
        except OSError:
            continue
        total -= size
        if total <= keep:
            break


class _Relay(QObject):
    loaded = Signal(object, object)       # key, QImage or None


class ArtCache:
    def __init__(self):
        self._items: OrderedDict = OrderedDict()    # (location, size) -> QPixmap or None
        self._bytes = 0
        self._lock = threading.Condition()
        self._queue: list[tuple] = []               # newest last; workers take from the end
        self._waiting: dict[tuple, set] = {}        # key -> widgets to repaint
        self._threads: list[threading.Thread] = []
        self._relay: _Relay | None = None

    # ---- asking -------------------------------------------------------------------

    def get(self, track: dict | None, size: int, notify=None) -> QPixmap | None:
        """The track's art scaled to fit `size`, or None (no art, or - with
        `notify` - not read yet: `notify.update()` is called when it is)."""
        if not track:
            return None
        key = (track.get("location"), size)
        if key in self._items:
            self._items.move_to_end(key)
            return self._items[key]
        if notify is None:
            self._keep(key, load_image(key[0], size))
            return self._items[key]
        self._request(key, notify)
        return None

    def loading(self, track: dict | None, size: int) -> bool:
        """True while the track's art is being read (draw a blank, not "no art")."""
        return bool(track) and (track.get("location"), size) in self._waiting

    def forget(self, location: str | None):
        """Drop a file's pictures (its artwork was just changed)."""
        for key in [k for k in self._items if k[0] == location]:
            self._drop(key)

    def clear(self):
        self._items.clear()
        self._bytes = 0

    # ---- inside -------------------------------------------------------------------

    def _keep(self, key, image: QImage | None):
        pixmap = QPixmap.fromImage(image) if image is not None else None
        if key in self._items:
            self._drop(key)
        self._items[key] = pixmap
        self._bytes += self._cost(pixmap)
        while self._bytes > MEMORY_BUDGET and len(self._items) > 1:
            self._drop(next(iter(self._items)))

    def _drop(self, key):
        self._bytes -= self._cost(self._items.pop(key))

    @staticmethod
    def _cost(pixmap) -> int:
        return 64 if pixmap is None else pixmap.width() * pixmap.height() * 4

    def _request(self, key, widget):
        if self._relay is None:                   # made on the window's thread
            self._relay = _Relay()
            self._relay.loaded.connect(self._arrived)
        with self._lock:
            watchers = self._waiting.get(key)
            if watchers is not None:
                watchers.add(widget)
                if key in self._queue:            # still wanted: move it to the front
                    self._queue.remove(key)
                    self._queue.append(key)
                return
            self._waiting[key] = {widget}
            self._queue.append(key)
            if len(self._queue) > MAX_QUEUE:      # scrolled past long ago
                dropped = self._queue.pop(0)
                self._waiting.pop(dropped, None)
            if not self._threads:
                for _ in range(WORKERS):
                    thread = threading.Thread(target=self._work, daemon=True, name="art")
                    thread.start()
                    self._threads.append(thread)
                threading.Thread(target=trim_disk_cache, daemon=True, name="art-trim").start()
            self._lock.notify()

    def _work(self):
        while True:
            with self._lock:
                while not self._queue:
                    self._lock.wait()
                key = self._queue.pop()           # the newest request first
            image = load_image(*key)
            try:
                self._relay.loaded.emit(key, image)
            except RuntimeError:                  # quitting
                return

    def _arrived(self, key, image):
        with self._lock:
            widgets = self._waiting.pop(key, set())
        self._keep(key, image)
        for widget in widgets:
            try:
                widget.update()
            except RuntimeError:                  # that view was closed meanwhile
                pass


# One cache shared by all views.
ART_CACHE = ArtCache()
