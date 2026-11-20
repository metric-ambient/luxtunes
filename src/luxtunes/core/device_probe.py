"""Everything the 2-second device check needs to ask the system: CD drives,
mounted drives (is one an iPod?), iPods and phones waiting to be opened.

These questions can be slow (a sleeping disk, a dead network share, lsblk or
gio taking a while), so the window asks them on another thread and then
acts on the answers (main_window._scan_devices). No Qt and no library
database in here: only reading the system.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from luxtunes.core import cd
from luxtunes.core.android import device as android_device
from luxtunes.core.ipod import device as ipod_device


@dataclass
class CdDrive:
    present: bool              # an audio disc is in, ready
    readable: bool             # LuxTunes may open the drive
    toc: object = None         # a new disc's table of contents (cd.Toc)
    error: Exception | None = None


@dataclass
class Snapshot:
    cds: dict = field(default_factory=dict)           # "/dev/sr0" -> CdDrive
    apple_usb: bool = False
    ipod_partitions: list = field(default_factory=list)   # plugged in, not mounted
    phones_waiting: list = field(default_factory=list)    # mtp:// addresses not opened
    phone_names: set = field(default_factory=set)
    phones: list | None = None                         # read again only when needed
    mounted: dict = field(default_factory=dict)        # mountpoint -> (device, filesystem)
    ipod_mounts: set = field(default_factory=set)
    new_ipods: dict = field(default_factory=dict)      # mountpoint -> IPod (None: unreadable)


def cds(known_discs: set[str]) -> dict:
    """Each drive's state; a disc not seen before has its table of contents read."""
    found = {}
    for device in cd.drives():
        present = cd.audio_disc_present(device)
        drive = CdDrive(present, present or os.access(device, os.R_OK))
        if present and f"cd:{device}" not in known_discs:
            try:
                drive.toc = cd.read_toc(device)
            except cd.CdError as error:
                drive.error = error
        found[device] = drive
    return found


def ipod_candidates() -> tuple[bool, list]:
    """(an Apple USB device is plugged in, its partitions that aren't mounted)."""
    apple = ipod_device.apple_usb_connected()
    return apple, (ipod_device.unmounted_ipod_partitions() if apple else [])


def phones(known_names: set[str], waiting: bool) -> tuple[list, set, list | None]:
    """(phones not opened yet, the open phones' names, the phones: only read
    when the names changed or one is still waiting for its storage)."""
    unopened = android_device.unmounted_phones()
    names = android_device.mounted_names()
    found = android_device.find_phones() if names != known_names or waiting else None
    return unopened, names, found


def ipods(seen_as_ipods: set[str], known_mountpoints: set[str]) -> tuple[dict, set, dict]:
    """(mounted drives, those that are iPods, the iPods not known yet, read)."""
    mounted = {mountpoint: (dev, fs) for dev, mountpoint, fs in ipod_device.mounted_filesystems()
               if not mountpoint.startswith(("/proc", "/sys", "/dev"))}
    ipod_mounts = {m for m in mounted if m in seen_as_ipods or ipod_device.is_ipod(m)}
    new = {}
    for mountpoint in ipod_mounts - known_mountpoints:
        dev, fs = mounted[mountpoint]
        try:
            new[mountpoint] = ipod_device.read_ipod(mountpoint, dev, fs)
        except OSError:
            new[mountpoint] = None
    return mounted, ipod_mounts, new


def everything(known_discs: set[str], seen_as_ipods: set[str], known_mountpoints: set[str],
               phone_names: set[str], phones_waiting: bool) -> Snapshot:
    snap = Snapshot()
    snap.cds = cds(known_discs)
    snap.apple_usb, snap.ipod_partitions = ipod_candidates()
    snap.phones_waiting, snap.phone_names, snap.phones = phones(phone_names, phones_waiting)
    snap.mounted, snap.ipod_mounts, snap.new_ipods = ipods(seen_as_ipods, known_mountpoints)
    return snap
