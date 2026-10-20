"""Finding plugged-in iPods and reading what they say about themselves.

Linux mounts an iPod like a USB stick (e.g. /media/you/IPOD). We spot it
by its iPod_Control folder. Everything here only *reads* the iPod:

  * iPod_Control/Device/SysInfo (and SysInfoExtended, if present): model
    number, serial number, FireWire GUID, software version.
  * iPod_Control/iTunes/iTunesDB: just enough to get the iPod's name (the
    title of its main playlist) and how many songs it holds.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CONTROL = "iPod_Control"

# Model numbers (last four characters of ModelNumStr) of the iPods LuxTunes
# supports, with their name as Apple sold them.
MODELS = {
    "A002": ("iPod 5th generation (video)", "30 GB", "white"),
    "A146": ("iPod 5th generation (video)", "30 GB", "black"),
    "A003": ("iPod 5th generation (video)", "60 GB", "white"),
    "A147": ("iPod 5th generation (video)", "60 GB", "black"),
    "A444": ("iPod 5.5th generation (video)", "30 GB", "white"),
    "A446": ("iPod 5.5th generation (video)", "30 GB", "black"),
    "A448": ("iPod 5.5th generation (video)", "80 GB", "white"),
    "A450": ("iPod 5.5th generation (video)", "80 GB", "black"),
    "B029": ("iPod classic (6th generation)", "80 GB", "silver"),
    "B147": ("iPod classic (6th generation)", "80 GB", "black"),
    "B145": ("iPod classic (6th generation)", "160 GB", "silver"),
    "B150": ("iPod classic (6th generation)", "160 GB", "black"),
    "B562": ("iPod classic (6.5th generation)", "120 GB", "silver"),
    "B565": ("iPod classic (6.5th generation)", "120 GB", "black"),
    "C293": ("iPod classic (7th generation)", "160 GB", "silver"),
    "C297": ("iPod classic (7th generation)", "160 GB", "black"),
}


@dataclass
class IPod:
    mountpoint: str
    device: str = ""                 # e.g. /dev/sdb2 (used to eject)
    filesystem: str = ""             # vfat = "Windows" format, hfsplus = "Mac"
    name: str = ""                   # what you named the iPod
    model_number: str = ""
    model_name: str = ""
    colour: str = ""
    serial: str = ""
    firewire_guid: str = ""
    software_version: str = ""
    usb_product: str = ""            # 1261 = iPod classic, 1209 = iPod 5G/5.5G
    song_count: int | None = None
    sysinfo: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """What LuxTunes remembers this iPod by (its settings are kept under it)."""
        return (self.firewire_guid or self.serial or os.path.basename(self.mountpoint)).upper()

    @property
    def format_name(self) -> str:
        if self.filesystem in ("vfat", "fat", "msdos", "exfat"):
            return "Windows"
        if self.filesystem.startswith("hfs"):
            return "Mac"
        return self.filesystem or "Unknown"

    @property
    def control(self) -> Path:
        return Path(self.mountpoint) / CONTROL

    @property
    def needs_hash(self) -> bool:
        """6G and later check a signature on their database (hash58)."""
        return self.usb_product == "1261" or self.model_number[-4:] in {
            "B029", "B147", "B145", "B150", "B562", "B565", "C293", "C297"}


# ---- Finding iPods ----------------------------------------------------------------

def usb_details(device_node: str) -> tuple[str, str]:
    """(USB product id, USB serial) of the iPod behind e.g. /dev/sdb2, read
    from /sys. An iPod's USB serial number is its FireWire GUID."""
    name = os.path.basename(device_node or "")
    if not name:
        return "", ""
    try:
        path = Path(os.path.realpath(f"/sys/class/block/{name}"))
    except OSError:
        return "", ""
    for folder in [path, *path.parents]:
        vendor = folder / "idVendor"
        if vendor.exists():
            try:
                if vendor.read_text().strip() != "05ac":
                    return "", ""
                product = (folder / "idProduct").read_text().strip()
                serial = (folder / "serial").read_text().strip() if (folder / "serial").exists() else ""
                return product, serial
            except OSError:
                return "", ""
    return "", ""


def _unescape(path: str) -> str:
    """/proc/mounts writes spaces as \\040 etc."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), path)


def mounted_filesystems(mounts_file: str = "/proc/mounts") -> list[tuple[str, str, str]]:
    """(device, mountpoint, filesystem) for everything mounted."""
    try:
        with open(mounts_file, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 3:
            found.append((_unescape(parts[0]), _unescape(parts[1]), parts[2]))
    return found


def is_ipod(mountpoint: str) -> bool:
    return os.path.isdir(os.path.join(mountpoint, CONTROL, "iTunes"))


def find_ipods(mounts_file: str = "/proc/mounts") -> list[IPod]:
    """Every iPod that's plugged in and mounted."""
    ipods = []
    for device, mountpoint, filesystem in mounted_filesystems(mounts_file):
        if mountpoint.startswith(("/proc", "/sys", "/dev")):
            continue
        if is_ipod(mountpoint):
            ipods.append(read_ipod(mountpoint, device, filesystem))
    return ipods


def read_ipod(mountpoint: str, device: str = "", filesystem: str = "") -> IPod:
    ipod = IPod(mountpoint=mountpoint, device=device, filesystem=filesystem)
    info = read_sysinfo(ipod.control / "Device")
    ipod.sysinfo = info
    ipod.usb_product, usb_serial = usb_details(device)
    ipod.model_number = info.get("ModelNumStr", "")
    model = MODELS.get(ipod.model_number[-4:].upper())
    if model:
        ipod.model_name, _, ipod.colour = model
    elif ipod.usb_product == "1261":
        ipod.model_name = "iPod classic"          # exact model unknown until SysInfo has it
    ipod.serial = info.get("pszSerialNumber") or info.get("SerialNumber", "")
    guid = info.get("FirewireGuid") or info.get("FireWireGUID", "")
    if not guid and usb_serial:
        guid = usb_serial            # an iPod's USB serial number is its FireWire GUID
    ipod.firewire_guid = (guid[2:] if guid.lower().startswith("0x") else guid).upper()
    ipod.software_version = _software_version(info)
    name, songs = read_database_summary(ipod.control / "iTunes" / "iTunesDB")
    ipod.name = name or os.path.basename(mountpoint.rstrip("/")) or "iPod"
    ipod.song_count = songs
    return ipod


def read_sysinfo(device_folder: Path) -> dict:
    """SysInfo ("Key: value" lines) plus SysInfoExtended (an XML plist), merged."""
    info: dict = {}
    try:
        for line in (device_folder / "SysInfo").read_text(errors="replace").splitlines():
            key, sep, value = line.partition(":")
            if sep:
                info[key.strip()] = value.strip()
    except OSError:
        pass
    try:
        import plistlib
        extended = plistlib.loads((device_folder / "SysInfoExtended").read_bytes())
        for key, value in extended.items():
            if isinstance(value, (str, int)):
                info.setdefault(key, str(value))
    except Exception:       # missing or not a valid plist: SysInfo is enough
        pass
    return info


def _software_version(info: dict) -> str:
    """'visibleBuildID: 0x02048000 (2.0.4)' -> '2.0.4'."""
    for key in ("visibleBuildID", "VisibleBuildID", "buildID", "BuildID"):
        value = info.get(key, "")
        match = re.search(r"\(([\d.]+)\)", value)
        if match:
            return match.group(1)
        if value.lower().startswith("0x") and len(value) >= 8:
            raw = int(value, 16)
            major, minor, patch = (raw >> 24) & 0xFF, (raw >> 20) & 0xF, (raw >> 16) & 0xF
            if major:
                return f"{major:x}.{minor}.{patch}"
    return ""


# ---- A quick look inside iTunesDB ----------------------------------------------------

def read_database_summary(path: Path) -> tuple[str | None, int | None]:
    """(the iPod's name, number of songs) from iTunesDB, or (None, None).

    Only reads the outline of the file: the database is a tree of chunks
    ("mhbd" > "mhsd" > "mhlt"/"mhlp" > "mhit"/"mhyp" > "mhod"), each starting
    with a 4-letter tag and its sizes, so we can hop between them."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None, None
    if data[:4] != b"mhbd":
        return None, None
    try:
        header_len, = struct.unpack_from("<I", data, 4)
        datasets, = struct.unpack_from("<I", data, 0x14)
        offset = header_len
        name = songs = None
        for _ in range(datasets):
            if data[offset:offset + 4] != b"mhsd":
                break
            mhsd_header, mhsd_total, kind = struct.unpack_from("<III", data, offset + 4)
            child = offset + mhsd_header
            if kind == 1 and data[child:child + 4] == b"mhlt":
                songs, = struct.unpack_from("<I", data, child + 8)
            elif kind in (2, 3) and name is None and data[child:child + 4] == b"mhlp":
                name = _master_playlist_name(data, child)
            offset += mhsd_total
        return name, songs
    except struct.error:
        return None, None


def _master_playlist_name(data: bytes, mhlp: int) -> str | None:
    header_len, count = struct.unpack_from("<II", data, mhlp + 4)
    offset = mhlp + header_len
    for _ in range(count):
        if data[offset:offset + 4] != b"mhyp":
            return None
        header_len, total, mhods = struct.unpack_from("<III", data, offset + 4)
        is_master = data[offset + 0x14] == 1
        if is_master:
            child = offset + header_len
            for _ in range(mhods):
                if data[child:child + 4] != b"mhod":
                    break
                _, mhod_total, kind = struct.unpack_from("<III", data, child + 4)
                if kind == 1:
                    encoding, length = struct.unpack_from("<II", data, child + 0x18)
                    raw = data[child + 0x28:child + 0x28 + length]
                    return raw.decode("utf-8" if encoding == 2 else "utf-16-le", errors="replace")
                child += mhod_total
            return None
        offset += total
    return None


# ---- Mounting iPods automatically -----------------------------------------------------
#
# Some desktops don't mount an iPod until you click it in the file manager.
# LuxTunes looks for an unmounted iPod and asks udisks (the same service the
# file manager uses) to mount it. No password is needed for removable drives.

APPLE_USB_VENDOR = "05ac"
IPOD_FILESYSTEMS = {"vfat", "hfsplus", "exfat"}


def apple_usb_connected(sys_usb: str = "/sys/bus/usb/devices") -> bool:
    """Quick check (no programs run): is any Apple USB device plugged in?"""
    try:
        for entry in os.scandir(sys_usb):
            try:
                with open(os.path.join(entry.path, "idVendor")) as handle:
                    if handle.read().strip() == APPLE_USB_VENDOR:
                        return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def unmounted_ipod_partitions(lsblk_output: str | None = None) -> list[str]:
    """Device paths (e.g. /dev/sdb2) of iPod music partitions that aren't mounted."""
    import json
    if lsblk_output is None:
        if not shutil.which("lsblk"):
            return []
        result = subprocess.run(["lsblk", "-J", "-o", "PATH,FSTYPE,MOUNTPOINT,VENDOR,MODEL,TYPE"],
                                capture_output=True, text=True, timeout=10)
        lsblk_output = result.stdout
    try:
        devices = json.loads(lsblk_output or "{}").get("blockdevices", [])
    except ValueError:
        return []
    found = []
    for disk in devices:
        vendor = (disk.get("vendor") or "").strip().lower()
        model = (disk.get("model") or "").strip().lower()
        if vendor != "apple" or "ipod" not in model:
            continue
        for part in disk.get("children") or []:
            if part.get("fstype") in IPOD_FILESYSTEMS and not part.get("mountpoint"):
                found.append(part["path"])
    return found


def mount(device: str) -> tuple[str | None, str | None]:
    """Mount a partition like the file manager would. Returns (mountpoint, error)."""
    if not shutil.which("udisksctl"):
        return None, "udisksctl isn't installed"
    result = subprocess.run(["udisksctl", "mount", "--no-user-interaction", "-b", device],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout).strip() or "couldn't mount the iPod"
    match = re.search(r" at (.+?)\.?$", result.stdout.strip())
    return (match.group(1) if match else None), None


# ---- Space ------------------------------------------------------------------------

@dataclass
class Space:
    capacity: int          # bytes the iPod can hold
    free: int
    media: int             # songs and videos synced onto it (iPod_Control/Music)
    photos: int            # iPod's photo thumbnails (Photos folder)

    @property
    def other(self) -> int:
        return max(0, self.capacity - self.free - self.media - self.photos)


def _folder_size(folder: Path) -> int:
    total = 0
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def space(ipod: IPod) -> Space:
    usage = shutil.disk_usage(ipod.mountpoint)
    return Space(capacity=usage.total, free=usage.free,
                 media=_folder_size(ipod.control / "Music"),
                 photos=_folder_size(Path(ipod.mountpoint) / "Photos"))


def format_gb(size: int) -> str:
    """Sizes as the device page shows them: '148.79 GB'
    (it divides by 1024³, so a 160 GB iPod shows as 148.79)."""
    return f"{size / 1024 ** 3:.2f} GB" if size >= 1024 ** 3 / 10 else f"{size / 1024 ** 2:.1f} MB"


# ---- Ejecting ----------------------------------------------------------------------

def is_busy_error(message: str | None) -> bool:
    """udisks/umount's way of saying a program still has files open on it."""
    text = (message or "").lower()
    return "busy" in text


def users_of(mountpoint: str, proc: str = "/proc") -> list[tuple[int, str, str]]:
    """The programs (pid, name, a path they use) that keep a mounted drive busy:
    open files, memory-mapped files, or a folder they're in. Only your own
    programs are visible, which are the ones that matter here."""
    root = os.path.realpath(mountpoint).rstrip("/") + "/"

    def inside(path: str) -> bool:
        return (path.rstrip("/") + "/").startswith(root)

    found = []
    try:
        pids = [int(n) for n in os.listdir(proc) if n.isdigit()]
    except OSError:
        return found
    for pid in pids:
        base = os.path.join(proc, str(pid))
        hit = None
        links = [os.path.join(base, n) for n in ("cwd", "root", "exe")]
        try:
            links += [os.path.join(base, "fd", n) for n in os.listdir(os.path.join(base, "fd"))]
        except OSError:
            pass
        for link in links:
            try:
                target = os.readlink(link)
            except OSError:
                continue
            if inside(target):
                hit = target
                break
        if hit is None:
            try:
                with open(os.path.join(base, "maps"), encoding="utf-8", errors="replace") as maps:
                    for line in maps:
                        path = line.rstrip("\n").split(None, 5)[-1] if line.count(" ") >= 5 else ""
                        if path.startswith("/") and inside(path):
                            hit = path
                            break
            except OSError:
                pass
        if hit is not None:
            try:
                with open(os.path.join(base, "comm"), encoding="utf-8", errors="replace") as f:
                    name = f.read().strip()
            except OSError:
                name = "?"
            found.append((pid, name, hit))
    return found


def eject(ipod: IPod) -> str | None:
    """Unmount the iPod and power it off so it shows "OK to disconnect".
    Returns None, or what went wrong."""
    commands = []
    if ipod.device and shutil.which("udisksctl"):
        commands.append(["udisksctl", "unmount", "-b", ipod.device])
    elif shutil.which("gio"):
        commands.append(["gio", "mount", "-e", ipod.mountpoint])
    else:
        commands.append(["umount", ipod.mountpoint])
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return (result.stderr or result.stdout).strip() or "couldn't eject the iPod"
    if ipod.device and shutil.which("udisksctl"):
        # Best effort: the iPod's screen changes from "Do not disconnect".
        subprocess.run(["udisksctl", "power-off", "-b", ipod.device],
                       capture_output=True, text=True, timeout=60)
    return None
