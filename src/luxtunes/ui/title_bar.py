"""The window's title bar in the style's light or dark, where the system lets
an app ask for it. The title bar is drawn by the desktop, not by LuxTunes, so
its exact color can't be chosen; only its light or dark version:

  - Linux (X11, e.g. Cinnamon on Linux Mint): each window is given the
    _GTK_THEME_VARIANT property ("dark" or "light"), which the window manager
    reads to draw that window's title bar from the dark or light version of
    your desktop theme, as it does for GTK apps;
  - Windows and Wayland desktops: Qt's color scheme, which Qt passes on.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

log = logging.getLogger(__name__)

_state = {"dark": False, "filter": None}
_x11 = {}


def _xlib():
    """libX11 and a display connection of our own, once (None if not on X11)."""
    if "lib" not in _x11:
        _x11["lib"] = _x11["display"] = None
        try:
            if QGuiApplication.platformName() != "xcb":
                return None
            lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
            lib.XOpenDisplay.restype = ctypes.c_void_p
            lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            lib.XInternAtom.restype = ctypes.c_ulong
            lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            lib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                            ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_char_p, ctypes.c_int]
            lib.XFlush.argtypes = [ctypes.c_void_p]
            display = lib.XOpenDisplay(None)
            if display:
                _x11["lib"], _x11["display"] = lib, display
        except (OSError, AttributeError) as error:
            log.debug("no X11 title bar variant: %s", error)
    return _x11["lib"]


def _apply(widget) -> None:
    lib = _xlib()
    if lib is None:
        return
    try:
        display = _x11["display"]
        value = b"dark" if _state["dark"] else b"light"
        lib.XChangeProperty(display, int(widget.winId()),
                            lib.XInternAtom(display, b"_GTK_THEME_VARIANT", 0),
                            lib.XInternAtom(display, b"UTF8_STRING", 0),
                            8, 0, value, len(value))        # 8-bit, PropModeReplace
        lib.XFlush(display)
    except Exception as error:                              # a nicety: never stop the app
        log.debug("couldn't set the title bar variant: %s", error)


def set_dark(app, dark: bool) -> None:
    _state["dark"] = dark
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
    except AttributeError:                                  # Qt before 6.8
        pass
    if _state["filter"] is None:            # each new window (dialogs too) as it opens
        _state["filter"] = True
        app.focusWindowChanged.connect(lambda window: window is not None and _apply(window))
    for widget in app.topLevelWidgets():
        if widget.isWindow() and widget.isVisible():
            _apply(widget)
