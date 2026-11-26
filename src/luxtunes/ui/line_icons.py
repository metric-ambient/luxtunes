"""The line icons of the top and bottom bars (library tabs, views, shuffle,
repeat, sleep, volume…), drawn in the theme's colours from small SVGs, so
they suit any background. A two-state icon is dim while off, and in the
accent (with a dot under it, so on/off isn't told by colour alone) while on.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

STROKE = ('fill="none" stroke="{c}" stroke-width="{w}" stroke-linecap="round" '
          'stroke-linejoin="round"')

SHAPES = {
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "audiobooks": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14z"/>'
                  '<path d="M6.5 17A2.5 2.5 0 0 0 4 19.5 2.5 2.5 0 0 0 6.5 22H20v-5"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "album": '<rect x="3" y="3.5" width="7" height="7" rx="1"/>'
             '<path d="M13 4.5h8M13 9.5h8M3 14.5h18M3 19.5h18"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>'
            '<rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "carousel": '<rect x="8" y="5" width="8" height="12" rx="1"/><path d="M5 7l-3 1v8l3 1M19 7l3 1v8l-3 1"/>',
    # Shuffle and repeat: the LuxTunes app's own line icons (LuxIcons.kt), drawn
    # 2 wide while off and 2.4 while on, as there.
    "shuffle": '<path d="M3 7h3.5c4 0 6 10 10 10H21M3 17h3.5c1.6 0 2.8-1.6 3.9-3.6'
               'M14 7h7M18 4l3 3-3 3M18 14l3 3-3 3"/>',
    "repeat": '<path d="M17 3l3 3-3 3M20 6H8a4 4 0 0 0-4 4v1M7 21l-3-3 3-3M4 18h12a4 4 0 0 0 4-4v-1"/>',
    "repeat_one": '<path d="M17 3l3 3-3 3M20 6H8a4 4 0 0 0-4 4v1M7 21l-3-3 3-3M4 18h12a4 4 0 0 0 4-4v-1'
                  'M11.5 10.5l1.5-1v5"/>',
    "sleep": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "volume": '<path d="M11 5L6 9H2v6h4l5 4zM15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "up": '<path d="M6 15l6-6 6 6"/>',
    # Filled, as Material icons (what the LuxTunes app uses for playback).
    "skip_previous": '<path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/>',
    "skip_next": '<path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/>',
    "play": '<path d="M8 5v14l11-7z"/>',
    "pause": '<path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>',
    "stop": '<path d="M6 6h12v12H6z"/>',
    "bedtime": '<path d="M12.34 2.02C6.59 1.82 2 6.42 2 12c0 5.52 4.48 10 10 10 3.71 0 6.93-2.02 '
               '8.66-5.02-7.51-.25-12.09-8.43-8.32-14.96z"/>',
    # The app's Material icons: tabs (Movie, Tv, Mic), playlists and Up Next
    # (QueueMusic) and smart playlists (Bolt).
    "movie": '<path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 '
             '1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z"/>',
    "tv_filled": '<path d="M21 3H3c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h5v2h8v-2h5c1.1 0 1.99-.9 '
                 '1.99-2L23 5c0-1.1-.9-2-2-2zm0 14H3V5h18v12z"/>',
    "mic": '<path d="M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 '
           '1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 '
           '6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"/>',
    "queue_music": '<path d="M15 6H3v2h12V6zm0 4H3v2h12v-2zM3 16h8v-2H3v2zM17 6v8.18c-.31-.11-.65'
                   '-.18-1-.18-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3V8h3V6h-5z"/>',
    "bolt": '<path d="M11 21h-1l1-7H7.5c-.58 0-.57-.32-.38-.66.19-.34.05-.08.07-.12C8.48 10.94 '
            '10.42 7.54 13 3h1l-1 7h3.5c.49 0 .56.33.47.51l-.07.15C12.96 17.55 11 21 11 21z"/>',
    "down": '<path d="M6 9l6 6 6-6"/>',
    "left": '<path d="M15 6l-6 6 6 6"/>',
    "right": '<path d="M9 6l6 6-6 6"/>',
}


FILLED = {"skip_previous", "skip_next", "play", "pause", "stop", "bedtime",
          "movie", "tv_filled", "mic", "queue_music", "bolt"}
DOT_ROOM = 8                 # below a toggle's glyph: where its "on" dot goes


def _render(name: str, color: str, size: int, dot: str | None = None,
            room: bool = False, width: float = 2) -> QPixmap:
    """The icon at size × size (twice that, for sharp screens). room: the
    pixmap is DOT_ROOM taller, the glyph the same size either way, and the
    dot (if any) drawn in that room: on and off are the same size."""
    scale = 2
    height = size + (DOT_ROOM if room else 0)
    pixmap = QPixmap(size * scale, height * scale)
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    glyph = size
    style = (f'fill="{QColor(color).name()}" stroke="none"' if name in FILLED
             else STROKE.format(c=QColor(color).name(), w=width))
    try:
        from PySide6.QtSvg import QSvgRenderer
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
               + style + '>' + SHAPES[name] + '</svg>')
        QSvgRenderer(QByteArray(svg.encode())).render(
            painter, QRectF((size - glyph) / 2, 0, glyph, glyph))
    except ImportError:                     # no QtSvg: a plain mark rather than nothing
        painter.setPen(QColor(color))
        painter.drawText(QRectF(0, 0, size, glyph), Qt.AlignmentFlag.AlignCenter, "•")
    if dot:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(dot))
        painter.drawEllipse(QRectF(size / 2 - 2.5, size + DOT_ROOM / 2 - 1.5, 5, 5))
    painter.end()
    return pixmap


def icon(name: str, color: str, size: int = 20) -> QIcon:
    return QIcon(_render(name, color, size))


def toggle_icon(name: str, off_color: str, on_color: str, size: int = 22,
                dot_when_on: bool = True, on_width: float = 2) -> QIcon:
    """Dim while off; the on colour plus a dot under it while on (a checkable
    button's state), its lines on_width wide. Both the same size: size wide,
    size + DOT_ROOM tall."""
    result = QIcon()
    result.addPixmap(_render(name, off_color, size, room=dot_when_on),
                     QIcon.Mode.Normal, QIcon.State.Off)
    result.addPixmap(_render(name, on_color, size, on_color if dot_when_on else None,
                             room=dot_when_on, width=on_width), QIcon.Mode.Normal, QIcon.State.On)
    return result


def tab_icon(name: str, off_color: str, on_color: str, size: int = 20) -> QIcon:
    """A tab's icon: dim while not chosen, the chosen colour while it is (no dot:
    a chosen tab has its own accent bar or box)."""
    return toggle_icon(name, off_color, on_color, size, dot_when_on=False)


ICON_SIZE = QSize(20, 20)
