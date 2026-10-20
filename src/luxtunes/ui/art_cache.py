"""Album art for the Album List, Grid and Cover Flow views, read once and kept.

Reading embedded art means opening the music file, so each picture is read
the first time it's needed (only albums actually on screen) and then reused.
The cache keeps the most recently used pictures and forgets the oldest.
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from luxtunes.core import artwork
from luxtunes.core.paths import find_file

MAX_ENTRIES = 600


class ArtCache:
    def __init__(self):
        self._items: OrderedDict = OrderedDict()

    def get(self, track: dict | None, size: int) -> QPixmap | None:
        """Square-ish art for the track's file, scaled to fit `size`, or None."""
        if not track:
            return None
        location = track.get("location")
        key = (location, size)
        if key in self._items:
            self._items.move_to_end(key)
            return self._items[key]
        pixmap = None
        path = find_file(location)
        data = artwork.read_embedded_artwork(path) if path else None
        if data:
            loaded = QPixmap()
            if loaded.loadFromData(data):
                pixmap = loaded.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
        self._items[key] = pixmap
        if len(self._items) > MAX_ENTRIES:
            self._items.popitem(last=False)
        return pixmap

    def forget(self, location: str | None):
        """Drop a file's pictures (its artwork was just changed)."""
        for key in [k for k in self._items if k[0] == location]:
            del self._items[key]

    def clear(self):
        self._items.clear()


# One cache shared by all views.
ART_CACHE = ArtCache()
