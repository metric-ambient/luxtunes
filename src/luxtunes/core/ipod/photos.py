"""Photos on the iPod: copied one way, from a folder on this computer.

The Photos tab picks a folder (e.g. ~/Pictures) and either all of it or some
of its folders. Each folder becomes a photo album on the iPod (photos right
in the chosen folder only go into the Photo Library). libgpod makes the
small versions the iPod shows and writes its Photo Database.

Nothing ever comes back from the iPod: it can't take photos, so the folder
on this computer is the only source. The iPod's photos are rebuilt from it
whenever the chosen photos change. "Include full-resolution photos" also
copies the originals into the iPod's Photos/Full Resolution folder (see them
with disk use on).

Turning photo syncing off removes the photos LuxTunes put there (and leaves
alone photos some other program put on the iPod).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_void_p
from pathlib import Path

from luxtunes.core.ipod import gpod

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".gif", ".tif", ".tiff",
                    ".heic", ".heif", ".webp", ".tga"}
# Read by libgpod directly; the others (phone HEIC photos, WebP) are first
# turned into a temporary JPEG with heif-convert or FFmpeg.
DIRECT = {".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".gif", ".tif", ".tiff"}
FULL_RESOLUTION = Path("Photos") / "Full Resolution"
MARKER_NAME = ".luxtunes-photos"        # next to the Photo Database: what LuxTunes put there


class PhotoDB(Structure):
    _fields_ = [("photos", POINTER(gpod.GList)), ("photoalbums", POINTER(gpod.GList)),
                ("device", c_void_p)]


class PhotoAlbum(Structure):
    _fields_ = [("photodb", c_void_p), ("name", c_void_p), ("members", POINTER(gpod.GList)),
                ("album_type", ctypes.c_uint8)]


_bound = False


def _lib():
    global _bound
    lib = gpod._load()
    if not _bound:
        PDB, PAL, PERR = POINTER(PhotoDB), POINTER(PhotoAlbum), POINTER(POINTER(gpod.GError))
        for name, restype, argtypes in (
                ("itdb_photodb_parse", PDB, [c_char_p, PERR]),
                ("itdb_get_photodb_path", c_void_p, [c_char_p]),
                ("itdb_photodb_create", PDB, [c_char_p]),
                ("itdb_photodb_free", None, [PDB]),
                ("itdb_photodb_write", gpod.gboolean, [PDB, PERR]),
                ("itdb_photodb_add_photo", c_void_p, [PDB, c_char_p, c_int, c_int, PERR]),
                ("itdb_photodb_remove_photo", None, [PDB, PAL, c_void_p]),
                ("itdb_photodb_photoalbum_create", PAL, [PDB, c_char_p, c_int]),
                ("itdb_photodb_photoalbum_remove", None, [PDB, PAL, gpod.gboolean]),
                ("itdb_photodb_photoalbum_add_photo", None, [PDB, PAL, c_void_p, c_int])):
            function = getattr(lib, name)
            function.restype = restype
            function.argtypes = argtypes
        _bound = True
    return lib


# ---- Which photos ---------------------------------------------------------------------------

def _images_in(folder: str, recursive: bool) -> list[str]:
    found = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.casefold())
    except OSError:
        return found
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_file() and Path(entry.name).suffix.lower() in PHOTO_EXTENSIONS:
            found.append(entry.path)
        elif recursive and entry.is_dir(follow_symlinks=False):
            found.extend(_images_in(entry.path, True))
    return found


def chosen_albums(folder: str, mode: str, selected: list[str]) -> list[tuple[str | None, list[str]]]:
    """[(album name or None for "Photo Library only", [photo paths])].
    "all": photos in the folder itself, then one album per folder inside it.
    "selected": only the ticked folders."""
    albums = []
    if mode != "selected":
        loose = _images_in(folder, recursive=False)
        if loose:
            albums.append((None, loose))
    try:
        subfolders = sorted((e.name for e in os.scandir(folder)
                             if e.is_dir() and not e.name.startswith(".")), key=str.casefold)
    except OSError:
        subfolders = []
    wanted = set(selected)
    for name in subfolders:
        if mode == "selected" and name not in wanted:
            continue
        photos = _images_in(os.path.join(folder, name), recursive=True)
        if photos:
            albums.append((name, photos))
    return albums


def as_jpeg(path: str, folder: str) -> str | None:
    """A temporary JPEG copy of a photo libgpod can't read itself (None if no
    installed tool could make one)."""
    target = os.path.join(folder, f"{len(os.listdir(folder))}.jpg")
    commands = []
    if Path(path).suffix.lower() in (".heic", ".heif") and shutil.which("heif-convert"):
        commands.append(["heif-convert", "-q", "92", path, target])
    if shutil.which("ffmpeg"):
        commands.append(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", path,
                         "-frames:v", "1", "-q:v", "2", target])
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and os.path.exists(target) and os.path.getsize(target):
            return target
    return None


def count(albums) -> int:
    return sum(len(paths) for _, paths in albums)


def signature(albums, full_resolution: bool) -> str:
    """Changes whenever the chosen photos (or the full-resolution choice) do."""
    digest = hashlib.sha1(b"full" if full_resolution else b"small")
    for name, paths in albums:
        digest.update(f"\0{name}\0".encode("utf-8", "surrogateescape"))
        for path in paths:
            try:
                st = os.stat(path)
                digest.update(f"{path}\0{st.st_size}\0{st.st_mtime_ns}\0"
                              .encode("utf-8", "surrogateescape"))
            except OSError:
                pass
    return digest.hexdigest()


# ---- Photo orientation (phones and cameras store "turn me" in EXIF) --------------------------

def rotation_of(path: str) -> int:
    """Degrees counterclockwise to turn a JPEG so it shows upright (0, 90, 180, 270)."""
    try:
        with open(path, "rb") as f:
            data = f.read(128 * 1024)
    except OSError:
        return 0
    if data[:2] != b"\xff\xd8":
        return 0
    offset = 2
    while offset + 4 <= len(data) and data[offset] == 0xFF:
        marker = data[offset + 1]
        length = struct.unpack(">H", data[offset + 2:offset + 4])[0]
        if marker == 0xE1 and data[offset + 4:offset + 10] == b"Exif\0\0":
            orientation = _exif_orientation(data[offset + 10:offset + 2 + length])
            return {3: 180, 6: 270, 8: 90}.get(orientation, 0)
        if marker == 0xDA:          # the picture itself starts: no EXIF before it
            break
        offset += 2 + length
    return 0


def _exif_orientation(tiff: bytes) -> int:
    try:
        endian = {b"II": "<", b"MM": ">"}[tiff[:2]]
        first = struct.unpack(endian + "I", tiff[4:8])[0]
        entries = struct.unpack(endian + "H", tiff[first:first + 2])[0]
        for i in range(entries):
            at = first + 2 + i * 12
            tag, _, _ = struct.unpack(endian + "HHI", tiff[at:at + 8])
            if tag == 0x0112:
                return struct.unpack(endian + "H", tiff[at + 8:at + 10])[0]
    except (KeyError, struct.error):
        pass
    return 0


# ---- On the iPod -----------------------------------------------------------------------------

def database_path(mountpoint: str) -> Path | None:
    """Where this iPod keeps its Photo Database (None if it has none yet)."""
    found = gpod.free_c_string(_lib().itdb_get_photodb_path(mountpoint.encode()))
    return Path(found) if found else None


def _marker(mountpoint: str) -> Path:
    database = database_path(mountpoint)
    folder = database.parent if database else Path(mountpoint) / "Photos"
    return folder / MARKER_NAME


def _read_marker(mountpoint: str) -> dict | None:
    try:
        return json.loads(_marker(mountpoint).read_text())
    except (OSError, ValueError):
        return None


def _write_marker(mountpoint: str, data: dict | None) -> None:
    path = _marker(mountpoint)
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _open_db(mountpoint: str):
    lib = _lib()
    error = POINTER(gpod.GError)()
    db = None
    if database_path(mountpoint) is not None:
        db = lib.itdb_photodb_parse(mountpoint.encode(), byref(error))
    if not db:
        db = lib.itdb_photodb_create(mountpoint.encode())
    if not db:
        raise gpod.GpodError("this iPod can't hold photos")
    return db


def _empty(db) -> None:
    """Take every photo and album out of the database (the Photo Library stays)."""
    lib = _lib()
    for photo in gpod.glist(db.contents.photos):
        lib.itdb_photodb_remove_photo(db, None, photo)
    for album in gpod.glist(db.contents.photoalbums):
        album = ctypes.cast(album, POINTER(PhotoAlbum))
        if album.contents.album_type != 1:             # 1 = the Photo Library itself
            lib.itdb_photodb_photoalbum_remove(db, album, 0)


def _write(db) -> None:
    error = POINTER(gpod.GError)()
    if not _lib().itdb_photodb_write(db, byref(error)):
        gpod._raise(error, "couldn't save the iPod's photos")


def remove_all(mountpoint: str) -> bool:
    """Take off the photos LuxTunes put on the iPod. False if there were none."""
    marker = _read_marker(mountpoint)
    if marker is None:
        return False
    lib = _lib()
    db = _open_db(mountpoint)
    try:
        _empty(db)
        _write(db)
    finally:
        lib.itdb_photodb_free(db)
    if marker.get("full_resolution"):
        shutil.rmtree(Path(mountpoint) / FULL_RESOLUTION, ignore_errors=True)
    _write_marker(mountpoint, None)
    return True


def sync(mountpoint: str, albums, full_resolution: bool, progress=None) -> tuple[int, list[str]]:
    """Put these photos on the iPod (replacing what LuxTunes put there before).
    progress(done, total) may return False to stop (nothing is changed then).
    Returns (photos added, problems). Unchanged photos are left as they are."""
    wanted = signature(albums, full_resolution)
    marker = _read_marker(mountpoint) or {}
    if marker.get("signature") == wanted and database_path(mountpoint) is not None:
        return count(albums), []
    lib = _lib()
    problems: list[str] = []
    added = 0
    total = count(albums)
    db = _open_db(mountpoint)
    scratch = tempfile.TemporaryDirectory(prefix="luxtunes-photos-")   # kept until written
    try:
        _empty(db)
        done = 0
        for name, paths in albums:
            album = lib.itdb_photodb_photoalbum_create(db, name.encode("utf-8"), -1) if name else None
            for path in paths:
                if progress is not None and progress(done, total) is False:
                    return added, problems          # stopped: the iPod keeps its old photos
                done += 1
                source = path
                if Path(path).suffix.lower() not in DIRECT:
                    source = as_jpeg(path, scratch.name)
                    if source is None:
                        problems.append(f"{os.path.basename(path)}: LuxTunes can't read this kind "
                                        "of photo here (for HEIC photos: sudo apt install "
                                        "libheif-examples)")
                        continue
                error = POINTER(gpod.GError)()
                photo = lib.itdb_photodb_add_photo(db, source.encode("utf-8", "surrogateescape"),
                                                   -1, rotation_of(source), byref(error))
                if not photo:
                    reason = error.contents.message.decode() if error else "can't be read"
                    problems.append(f"{os.path.basename(path)}: {reason}")
                    continue
                if album:
                    lib.itdb_photodb_photoalbum_add_photo(db, album, photo, -1)
                added += 1
        _write(db)
    finally:
        lib.itdb_photodb_free(db)
        scratch.cleanup()

    full = Path(mountpoint) / FULL_RESOLUTION
    if marker.get("full_resolution"):
        shutil.rmtree(full, ignore_errors=True)        # LuxTunes' own earlier copies only
    if full_resolution:
        for name, paths in albums:
            target = full / (name or "")
            target.mkdir(parents=True, exist_ok=True)
            for path in paths:
                destination = target / os.path.basename(path)
                n = 2
                while destination.exists():           # same name in two folders
                    destination = target / f"{Path(path).stem} {n}{Path(path).suffix}"
                    n += 1
                try:
                    shutil.copyfile(path, destination)
                except OSError as error:
                    problems.append(f"{os.path.basename(path)}: {error.strerror or error}")
    _write_marker(mountpoint, {"signature": wanted, "full_resolution": full_resolution})
    return added, problems
