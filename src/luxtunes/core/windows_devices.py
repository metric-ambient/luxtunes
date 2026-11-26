"""Drives and USB devices on Windows, for finding iPods: which drive letters
there are (and their file system), which Apple USB devices are plugged in
(their product id and serial number, which on an iPod is its FireWire GUID),
and ejecting a drive. Uses Windows' own libraries through ctypes; nothing to
install. Only imported on Windows."""

from __future__ import annotations

import ctypes
import logging
import re
import string
import subprocess
from ctypes import wintypes

log = logging.getLogger(__name__)

DRIVE_REMOVABLE, DRIVE_FIXED = 2, 3
DIGCF_PRESENT, DIGCF_ALLCLASSES = 0x02, 0x04
APPLE = "05AC"


def drives() -> list[tuple[str, str, str]]:
    """(device, mountpoint, file system) for each drive letter in use: ("E:", "E:\\\\", "vfat")."""
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    found = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        if kernel32.GetDriveTypeW(root) not in (DRIVE_REMOVABLE, DRIVE_FIXED):
            continue
        name = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        old_mode = kernel32.SetErrorMode(1)        # no "insert a disk" box for empty readers
        try:
            ok = kernel32.GetVolumeInformationW(root, name, 261, None, None, None, fs, 261)
        finally:
            kernel32.SetErrorMode(old_mode)
        if not ok:
            continue
        kind = {"FAT32": "vfat", "FAT": "vfat", "EXFAT": "exfat", "NTFS": "ntfs"}.get(
            fs.value.upper(), fs.value.lower())
        found.append((f"{letter}:", root, kind))
    return found


class _DevInfoData(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", ctypes.c_byte * 16),
                ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


def present_usb_devices() -> list[str]:
    """Instance ids of the USB devices plugged in now ("USB\\\\VID_05AC&PID_1261\\\\000A27…")."""
    setupapi = ctypes.windll.setupapi
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND,
                                              wintypes.DWORD]
    setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.POINTER(_DevInfoData)]
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [ctypes.c_void_p,
                                                     ctypes.POINTER(_DevInfoData),
                                                     wintypes.LPWSTR, wintypes.DWORD,
                                                     ctypes.POINTER(wintypes.DWORD)]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    handle = setupapi.SetupDiGetClassDevsW(None, "USB", None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return []
    found = []
    try:
        index = 0
        while True:
            data = _DevInfoData()
            data.cbSize = ctypes.sizeof(_DevInfoData)
            if not setupapi.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(data)):
                break
            buffer = ctypes.create_unicode_buffer(512)
            if setupapi.SetupDiGetDeviceInstanceIdW(handle, ctypes.byref(data), buffer, 512, None):
                found.append(buffer.value)
            index += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(handle)
    return found


def apple_devices(instance_ids: list[str] | None = None) -> list[tuple[str, str]]:
    """(USB product id, serial) of each Apple device plugged in, e.g. ("1261", "000A27…")."""
    ids = present_usb_devices() if instance_ids is None else instance_ids
    found = []
    for instance in ids:
        match = re.match(r"USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})\\([^\\&]+)$", instance.upper())
        if match and match.group(1) == APPLE:
            found.append((match.group(2).lower(), match.group(3)))
    return found


def ipod_usb_details() -> tuple[str, str]:
    """The plugged-in iPod's (USB product id, serial), if there's one Apple device
    that looks like an iPod (several: the first; LuxTunes reads SysInfo too)."""
    try:
        devices = apple_devices()
    except OSError as error:
        log.debug("couldn't list USB devices: %s", error)
        return "", ""
    for product, serial in devices:
        if len(serial) == 16 and all(c in string.hexdigits for c in serial):
            return product, serial
    return devices[0] if devices else ("", "")


def eject(mountpoint: str) -> str | None:
    """Eject the drive as Explorer's Eject does. None, or what went wrong."""
    letter = mountpoint.rstrip("\\/")[:2]
    script = ("$d=(New-Object -ComObject Shell.Application).Namespace(17).ParseName('"
              + letter + "'); if ($d) { $d.InvokeVerb('Eject') } else { exit 1 }")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                 script], capture_output=True, text=True, timeout=60,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip() or "couldn't eject the iPod"
    return None
