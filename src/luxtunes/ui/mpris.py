"""Media keys and the desktop's sound menu, through MPRIS.

The desktop (on Linux Mint: Cinnamon) catches the keyboard's Play/Pause,
Next and Previous keys itself, even while another window is in front, and
passes them on to music players over D-Bus using the MPRIS interface. This
module makes LuxTunes one of those players: it registers
org.mpris.MediaPlayer2.luxtunes on the session bus and answers PlayPause,
Next, Previous and so on. The sound menu in the panel shows what's playing.

Nothing here goes online; D-Bus is the desktop's local message bus. If there
is no bus (e.g. a test run without a desktop), LuxTunes runs without it.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import ClassInfo, Property, QObject, Slot
from PySide6.QtDBus import (
    QDBusAbstractAdaptor,
    QDBusConnection,
    QDBusMessage,
    QDBusObjectPath,
)

log = logging.getLogger(__name__)

PATH = "/org/mpris/MediaPlayer2"
SERVICE = "org.mpris.MediaPlayer2.luxtunes"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"


@ClassInfo({"D-Bus Interface": ROOT_INTERFACE})
class _Root(QDBusAbstractAdaptor):
    def __init__(self, host: "Mpris"):
        super().__init__(host)
        self.host = host

    @Slot()
    def Raise(self):
        self.host.window_raise()

    @Slot()
    def Quit(self):
        self.host.window_quit()

    def _true(self):
        return True

    def _false(self):
        return False

    def _identity(self):
        return "LuxTunes"

    def _desktop_entry(self):
        return "luxtunes"

    def _no_list(self):
        return []

    CanQuit = Property(bool, _true)
    CanRaise = Property(bool, _true)
    HasTrackList = Property(bool, _false)
    Identity = Property(str, _identity)
    DesktopEntry = Property(str, _desktop_entry)
    SupportedUriSchemes = Property("QStringList", _no_list)
    SupportedMimeTypes = Property("QStringList", _no_list)


@ClassInfo({"D-Bus Interface": PLAYER_INTERFACE})
class _Player(QDBusAbstractAdaptor):
    def __init__(self, host: "Mpris"):
        super().__init__(host)
        self.host = host

    @Slot()
    def PlayPause(self):
        self.host.request("play_pause", "PlayPause")

    @Slot()
    def Play(self):
        if self.host.status() != "Playing":
            self.host.request("play_pause", "Play")

    @Slot()
    def Pause(self):
        if self.host.status() == "Playing":
            self.host.request("play_pause", "Pause")

    @Slot()
    def Stop(self):
        self.host.request("stop", "Stop")

    @Slot()
    def Next(self):
        self.host.request("next", "Next")

    @Slot()
    def Previous(self):
        self.host.request("previous", "Previous")

    def _status(self):
        return self.host.status()

    def _metadata(self):
        return self.host.metadata()

    def _has_song(self):
        return self.host.status() != "Stopped"

    def _true(self):
        return True

    def _false(self):
        return False

    def _rate(self):
        return 1.0

    def _volume(self):
        return self.host.controls["volume"]() / 100.0

    PlaybackStatus = Property(str, _status)
    Metadata = Property("QVariantMap", _metadata)
    Rate = Property(float, _rate)
    MinimumRate = Property(float, _rate)
    MaximumRate = Property(float, _rate)
    Volume = Property(float, _volume)
    CanGoNext = Property(bool, _has_song)
    CanGoPrevious = Property(bool, _has_song)
    CanPlay = Property(bool, _true)
    CanPause = Property(bool, _true)
    CanSeek = Property(bool, _false)
    CanControl = Property(bool, _true)


class Mpris(QObject):
    """Puts LuxTunes on the session bus as an MPRIS player.

    `controls` holds what the window does for each request: play_pause, stop,
    next, previous, raise, quit, plus current() -> the song dict or None,
    playing() -> bool and volume() -> 0..100."""

    def __init__(self, controls: dict, parent=None, service: str = SERVICE):
        super().__init__(parent)
        self.controls = controls
        self.service = None
        self._root = _Root(self)              # adaptors must exist before registering
        self._player = _Player(self)
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            log.info("no D-Bus session bus: media keys won't reach LuxTunes")
            return
        if not bus.registerObject(PATH, self, QDBusConnection.RegisterOption.ExportAdaptors):
            log.info("couldn't register %s on D-Bus", PATH)
            return
        # A second LuxTunes (another library) gets a name of its own.
        for name in (service, f"{service}.instance{os.getpid()}"):
            if bus.registerService(name):
                self.service = name
                log.info("media keys: registered %s", name)
                others = self.other_players()
                if others:
                    log.info("other players the desktop may send media keys to: %s",
                             ", ".join(others))
                break
        if self.service is None:
            bus.unregisterObject(PATH)
            log.info("couldn't take an MPRIS name on D-Bus")

    @property
    def active(self) -> bool:
        return self.service is not None

    def request(self, action: str, method: str):
        log.info("media key from the desktop (MPRIS %s)", method)
        self.controls[action]()

    def other_players(self) -> list[str]:
        """Other music players on the bus. The desktop may send the media keys
        to one of them instead (Cinnamon uses the player that started first)."""
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return []
        try:
            names = bus.interface().registeredServiceNames().value()
        except Exception:
            return []
        return sorted(n for n in names if n.startswith("org.mpris.MediaPlayer2.")
                      and n != self.service)

    def close(self):
        if self.service is not None:
            bus = QDBusConnection.sessionBus()
            bus.unregisterService(self.service)
            bus.unregisterObject(PATH)
            self.service = None

    # ---- What the desktop reads ---------------------------------------------------------

    def status(self) -> str:
        if self.controls["current"]() is None:
            return "Stopped"
        return "Playing" if self.controls["playing"]() else "Paused"

    def metadata(self) -> dict:
        track = self.controls["current"]()
        if track is None:
            return {"mpris:trackid": QDBusObjectPath("/org/mpris/MediaPlayer2/TrackList/NoTrack")}
        data = {
            "mpris:trackid": QDBusObjectPath(f"/org/luxtunes/track/{int(track.get('id') or 0)}"),
            "xesam:title": track.get("name") or "",
            "xesam:album": track.get("album") or "",
        }
        if track.get("artist"):
            data["xesam:artist"] = [track["artist"]]
        if track.get("album_artist"):
            data["xesam:albumArtist"] = [track["album_artist"]]
        length = int(track.get("total_time") or 0) * 1000         # microseconds
        if 0 < length < 2 ** 31:
            data["mpris:length"] = length
        return data

    def changed(self):
        """Tell the desktop the song or play/pause changed (call after each change)."""
        if self.service is None:
            return
        message = QDBusMessage.createSignal(PATH, "org.freedesktop.DBus.Properties",
                                            "PropertiesChanged")
        has_song = self.status() != "Stopped"
        message.setArguments([PLAYER_INTERFACE,
                              {"PlaybackStatus": self.status(), "Metadata": self.metadata(),
                               "CanGoNext": has_song, "CanGoPrevious": has_song},
                              ["Position"]])
        QDBusConnection.sessionBus().send(message)

    def window_raise(self):
        self.controls["raise"]()

    def window_quit(self):
        self.controls["quit"]()
