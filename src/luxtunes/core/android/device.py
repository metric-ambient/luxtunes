"""Finding Android phones plugged in over USB.

A phone set to "File transfer" in its USB notification appears in the file
manager. The desktop (gvfs) also makes it available as an ordinary folder,
/run/user/<your user id>/gvfs/mtp:host=<Maker>_<Model>_<serial>, with one
folder per storage inside ("Internal shared storage", an SD card...). LuxTunes
syncs through that folder, so it needs no extra software and no developer
settings on the phone. No Qt in here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PREFIX = "mtp:host="


def gvfs_folder() -> Path:
    return Path(f"/run/user/{os.getuid()}/gvfs")


@dataclass
class Phone:
    folder: str                       # the gvfs folder for the whole phone
    maker: str = ""
    model: str = ""
    serial: str = ""
    storages: dict = field(default_factory=dict)   # name -> folder ("Internal shared storage")

    @property
    def key(self) -> str:
        """What LuxTunes remembers this phone by (its settings are kept under it)."""
        return f"android:{self.serial or os.path.basename(self.folder)}"

    @property
    def name(self) -> str:
        return self.model or self.maker or "Android device"

    @property
    def model_name(self) -> str:
        return " ".join(part for part in (self.maker, self.model) if part) or "Android device"

    @property
    def uri(self) -> str:
        return "mtp://" + os.path.basename(self.folder)[len(PREFIX):] + "/"

    def storage(self, wanted: str = "") -> str | None:
        """The folder of the chosen storage (or the phone's own storage)."""
        if wanted in self.storages:
            return self.storages[wanted]
        internal = [n for n in self.storages if "internal" in n.lower() or "phone" in n.lower()]
        names = internal or sorted(self.storages)
        return self.storages[names[0]] if names else None


def parse_host(name: str) -> tuple[str, str, str]:
    """"mtp:host=Google_Pixel_7_28141FDH2000XY" -> ("Google", "Pixel 7", "28141FDH2000XY")."""
    host = name[len(PREFIX):] if name.startswith(PREFIX) else name
    parts = host.split("_")
    if len(parts) < 2:
        return host, "", ""
    serial = parts[-1]
    maker = parts[0]
    model = " ".join(parts[1:-1])
    # Some phones repeat the maker in the model ("SAMSUNG_SAMSUNG_Android_...").
    if model.upper().startswith(maker.upper() + " "):
        model = model[len(maker) + 1:]
    return maker.title() if maker.isupper() else maker, model, serial


def find_phones(folder: Path | None = None) -> list[Phone]:
    """Every Android device the desktop shows right now (unlocked, in File
    transfer mode). A phone whose storage can't be seen yet has no storages."""
    folder = folder or gvfs_folder()
    phones = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name)
    except OSError:
        return phones
    for entry in entries:
        if not entry.name.startswith(PREFIX):
            continue
        maker, model, serial = parse_host(entry.name)
        phone = Phone(entry.path, maker, model, serial)
        try:
            for storage in os.scandir(entry.path):
                if storage.is_dir():
                    phone.storages[storage.name] = storage.path
        except OSError:
            pass
        phones.append(phone)
    return phones


def mounted_names(folder: Path | None = None) -> set[str]:
    """The phones' folder names only (cheap: no question to the phones)."""
    try:
        return {n for n in os.listdir(folder or gvfs_folder()) if n.startswith(PREFIX)}
    except OSError:
        return set()


@dataclass
class Space:
    capacity: int
    free: int
    media: int             # what LuxTunes put on it
    photos: int = 0

    @property
    def other(self) -> int:
        return max(0, self.capacity - self.free - self.media)


def space(storage: str | None, media_bytes: int = 0) -> Space:
    if not storage:
        return Space(0, 0, 0)
    try:
        usage = shutil.disk_usage(storage)
    except OSError:
        return Space(0, 0, media_bytes)
    return Space(usage.total, usage.free, media_bytes)


def eject(phone: Phone) -> str | None:
    """Let go of the phone (it can then be unplugged). Returns an error or None."""
    if not shutil.which("gio"):
        return "the gio program is missing"
    result = subprocess.run(["gio", "mount", "-u", phone.uri], capture_output=True, text=True,
                            timeout=60)
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip() or "couldn't let go of the phone"
    return None


def mtp_usb_connected(usb: str = "/sys/bus/usb/devices") -> bool:
    """Is anything plugged in that offers file transfer (MTP)? Cheap: reads
    the USB device list only, so it can be asked every few seconds."""
    try:
        entries = os.listdir(usb)
    except OSError:
        return False
    for name in entries:
        if ":" not in name:                      # interfaces look like 1-2:1.0
            continue
        folder = os.path.join(usb, name)
        try:
            with open(os.path.join(folder, "interface")) as f:
                if "MTP" in f.read():
                    return True
        except OSError:
            pass
        try:
            with open(os.path.join(folder, "bInterfaceClass")) as f:
                if f.read().strip() == "06":     # still image / file transfer
                    return True
        except OSError:
            pass
    return False


def unmounted_phones() -> list[str]:
    """mtp:// addresses of phones the desktop knows about but hasn't opened
    yet (as when it isn't set to open devices by itself)."""
    if not mtp_usb_connected() or not shutil.which("gio"):
        return []
    try:
        result = subprocess.run(["gio", "mount", "-li"], capture_output=True, text=True,
                                timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    mounted = {name[len(PREFIX):] for name in mounted_names()}
    found = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("activation_root=mtp://"):
            uri = line.split("=", 1)[1]
            host = uri[len("mtp://"):].strip("/")
            if host and host not in mounted and uri not in found:
                found.append(uri)
    return found


def mount(uri: str) -> str | None:
    """Open a phone (like clicking it in the file manager). Returns an error or None."""
    try:
        result = subprocess.run(["gio", "mount", uri], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip() or "couldn't open the phone"
    return None
