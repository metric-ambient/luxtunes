"""Audio CDs: finding them, reading their track list, looking up the names,
and ripping tracks into the library (Import CD).

- The drive is asked directly (Linux CD-ROM ioctls on /dev/sr0...) whether a
  disc is in and for its table of contents (TOC); nothing to install.
- Names come from MusicBrainz (the free, open music database) using the
  disc's MusicBrainz Disc ID, computed from the TOC; the cover from the
  Cover Art Archive. Both need the internet; without it the tracks are
  "Track 01", "Track 02"...
- Ripping uses cdparanoia (`sudo apt install cdparanoia`), whose audio is
  piped straight into FFmpeg to encode (see encoders.py), so each track is
  read and encoded in one pass. The file is written into the LuxTunes Media
  folder where "Keep organized" would put it, tagged, and added to the
  library.

No Qt in here: the window runs these in background threads.
"""

from __future__ import annotations

import base64
import fcntl
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from luxtunes import __version__
from luxtunes.core import add_files, encoders, organize
from luxtunes.core.tags import UNCHANGED, write_tags

# ---- linux/cdrom.h -----------------------------------------------------------------
CDROMREADTOCHDR = 0x5305
CDROMREADTOCENTRY = 0x5306
CDROMEJECT = 0x5309
CDROM_DRIVE_STATUS = 0x5326
CDROM_DISC_STATUS = 0x5327
CDROM_LOCKDOOR = 0x5329
CDSL_CURRENT = 0x7FFFFFFF
CDS_DISC_OK = 4
CDS_AUDIO, CDS_MIXED = 100, 105
CDROM_LBA = 0x01
CDROM_LEADOUT = 0xAA
CDROM_DATA_TRACK = 0x04
TOC_ENTRY = struct.Struct("BBBxiBxxx")     # struct cdrom_tocentry (12 bytes)

SECTOR_BYTES = 2352                        # one CD sector of audio: 1/75 s of 16-bit stereo
SECTORS_PER_SECOND = 75
LEAD_IN = 150                              # 2 s before track 1; disc IDs count it
DATA_GAP = 11400                           # between the audio and a CD-Extra data session
BYTES_PER_SECOND = SECTOR_BYTES * SECTORS_PER_SECOND

VARIOUS_ARTISTS = "89ad4ac3-39f7-470e-963a-56509c546377"     # MusicBrainz's "Various Artists"
USER_AGENT = f"LuxTunes/{__version__} (Linux music player)"
MUSICBRAINZ = "https://musicbrainz.org/ws/2/discid/"
COVER_ART = "https://coverartarchive.org/"

KEY_ASK_LOOKUP = "cd_ask_lookup"
KEY_INSERT_ACTION = "cd_insert_action"
INSERT_ACTIONS = [("nothing", "Do Nothing"), ("show", "Show CD"), ("ask", "Ask to Import CD"),
                  ("import", "Import CD"), ("import_eject", "Import CD and Eject")]


class CdError(Exception):
    """The drive couldn't be read, ripping failed, ..."""


class ImportStopped(Exception):
    """You pressed stop; the half-written file was removed."""


# ---- Settings ----------------------------------------------------------------------

def insert_action(conn: sqlite3.Connection) -> str:
    """Preferences → General → "When you insert a CD" (default: ask)."""
    row = conn.execute("SELECT value FROM library_info WHERE key = ?", (KEY_INSERT_ACTION,)).fetchone()
    return row[0] if row and row[0] in dict(INSERT_ACTIONS) else "ask"


def set_insert_action(conn: sqlite3.Connection, action: str) -> None:
    with conn:
        conn.execute("INSERT INTO library_info (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (KEY_INSERT_ACTION, action))


def ask_lookup(conn: sqlite3.Connection) -> bool:
    """Ask to look up a newly found CD's names online? (Preferences → General;
    "Don't ask again" in the question turns it off.) Default: yes."""
    row = conn.execute("SELECT value FROM library_info WHERE key = ?", (KEY_ASK_LOOKUP,)).fetchone()
    return not (row and row[0] == "0")


def set_ask_lookup(conn: sqlite3.Connection, ask: bool) -> None:
    with conn:
        conn.execute("INSERT INTO library_info (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (KEY_ASK_LOOKUP, "1" if ask else "0"))


def cdparanoia_path() -> str | None:
    return shutil.which("cdparanoia")


# ---- The disc ------------------------------------------------------------------------

@dataclass
class Toc:
    first: int                       # first audio track number (normally 1)
    last: int                        # last audio track number
    offsets: list[int]               # start sector of each audio track, counting the 2 s lead-in
    leadout: int                     # where the audio ends (same counting)

    def sectors(self, index: int) -> int:
        end = self.offsets[index + 1] if index + 1 < len(self.offsets) else self.leadout
        return end - self.offsets[index]

    def disc_id(self) -> str:
        """The MusicBrainz Disc ID (28 characters)."""
        text = f"{self.first:02X}{self.last:02X}{self.leadout:08X}"
        numbered = {self.first + i: offset for i, offset in enumerate(self.offsets)}
        text += "".join(f"{numbered.get(n, 0):08X}" for n in range(1, 100))
        digest = base64.b64encode(hashlib.sha1(text.encode("ascii")).digest()).decode("ascii")
        return digest.translate(str.maketrans("+/=", "._-"))

    def toc_param(self) -> str:
        """MusicBrainz's toc= value, for finding a disc whose ID isn't known yet."""
        return " ".join(str(n) for n in [self.first, self.last, self.leadout, *self.offsets])


@dataclass
class CdTrack:
    number: int                      # the track number on the disc
    sectors: int
    name: str
    artist: str = ""
    checked: bool = True

    @property
    def total_time(self) -> int:     # milliseconds
        return self.sectors * 1000 // SECTORS_PER_SECOND


@dataclass
class Disc:
    device: str
    toc: Toc
    tracks: list[CdTrack]
    album: str = "Audio CD"
    artist: str = ""
    album_artist: str = ""
    genre: str = ""
    year: int | None = None
    disc_number: int | None = None
    disc_count: int | None = None
    compilation: bool = False
    release_id: str = ""
    release_group_id: str = ""
    artwork: bytes | None = None
    looked_up: bool = False          # MusicBrainz knew it
    choices: int = 0                 # how many releases matched

    @property
    def key(self) -> str:
        return f"cd:{self.device}"

    @property
    def name(self) -> str:
        return self.album or "Audio CD"

    def fields(self, track: CdTrack) -> dict:
        """The tags the imported file gets."""
        album = self.album if self.looked_up or self.album != "Audio CD" else None
        return {"name": track.name, "artist": track.artist or self.artist or None,
                "album_artist": self.album_artist or None, "album": album or None,
                "genre": self.genre or None, "year": self.year,
                "track_number": track.number, "track_count": len(self.tracks),
                "disc_number": self.disc_number, "disc_count": self.disc_count,
                "compilation": 1 if self.compilation else 0}


def drives() -> list[str]:
    """CD/DVD drives on this computer (/dev/sr0, /dev/sr1, ...)."""
    return sorted(glob.glob("/dev/sr[0-9]*"))


def _open(device: str) -> int:
    return os.open(device, os.O_RDONLY | os.O_NONBLOCK)


def audio_disc_present(device: str) -> bool:
    """A disc with audio tracks is in the drive (closed and ready)."""
    try:
        fd = _open(device)
    except OSError:
        return False
    try:
        if fcntl.ioctl(fd, CDROM_DRIVE_STATUS, CDSL_CURRENT) != CDS_DISC_OK:
            return False
        return fcntl.ioctl(fd, CDROM_DISC_STATUS, 0) in (CDS_AUDIO, CDS_MIXED)
    except OSError:
        return False
    finally:
        os.close(fd)


def read_toc(device: str) -> Toc:
    """The disc's table of contents. Data tracks are left out (a CD-Extra's
    data session at the end moves the audio's end back, as MusicBrainz expects)."""
    try:
        fd = _open(device)
    except OSError as error:
        raise CdError(f"Can't open {device}: {error.strerror}") from error
    try:
        header = bytearray(2)
        fcntl.ioctl(fd, CDROMREADTOCHDR, header)
        first, last = header
        entries = []
        for number in [*range(first, last + 1), CDROM_LEADOUT]:
            buf = bytearray(TOC_ENTRY.pack(number, 0, CDROM_LBA, 0, 0))
            fcntl.ioctl(fd, CDROMREADTOCENTRY, buf)
            _, adr_ctrl, _, lba, _ = TOC_ENTRY.unpack(buf)
            entries.append((number, lba, bool((adr_ctrl >> 4) & CDROM_DATA_TRACK)))
    except OSError as error:
        raise CdError(f"Can't read the CD's track list: {error.strerror}") from error
    finally:
        os.close(fd)
    return toc_from_entries(entries)


def toc_from_entries(entries: list[tuple[int, int, bool]]) -> Toc:
    """(track number, start LBA, is data) for each track, then the lead-out."""
    *tracks, (_, leadout_lba, _) = entries
    audio = [(n, lba) for n, lba, data in tracks if not data]
    if not audio:
        raise CdError("This disc has no audio tracks.")
    leadout = leadout_lba
    later_data = [lba for n, lba, data in tracks if data and n > audio[-1][0]]
    if later_data:                         # CD-Extra: the audio ends before the data session
        leadout = later_data[0] - DATA_GAP
    return Toc(audio[0][0], audio[-1][0], [lba + LEAD_IN for _, lba in audio], leadout + LEAD_IN)


def blank_disc(device: str, toc: Toc) -> Disc:
    tracks = [CdTrack(toc.first + i, toc.sectors(i), f"Track {toc.first + i:02d}")
              for i in range(len(toc.offsets))]
    return Disc(device, toc, tracks)


def drive_status(device: str) -> int | None:
    """The drive's state (CDS_DISC_OK = closed with a disc in it), or None."""
    try:
        fd = _open(device)
    except OSError:
        return None
    try:
        return fcntl.ioctl(fd, CDROM_DRIVE_STATUS, CDSL_CURRENT)
    except OSError:
        return None
    finally:
        os.close(fd)


def _still_in(device: str, wait: float = 3.0) -> bool:
    """After an eject: True if the disc is still (or again) in the closed drive."""
    deadline = time.monotonic() + wait
    while drive_status(device) == CDS_DISC_OK:
        if time.monotonic() >= deadline:
            return True
        time.sleep(0.2)
    return False


def _eject_ioctl(device: str) -> None:
    fd = _open(device)
    try:
        try:
            fcntl.ioctl(fd, CDROM_LOCKDOOR, 0)
        except OSError:
            pass
        fcntl.ioctl(fd, CDROMEJECT, 0)
    finally:
        os.close(fd)


def eject(device: str) -> str | None:
    """Open the tray (and make sure it stays open). Returns an error message, or None.

    The desktop's own way comes first: when it shows the CD (an audio CD
    "mounted" by the file manager), it has to let go of it, or it reads the
    drive again straight away, which closes the tray and brings the CD back."""
    problems = []
    if shutil.which("gio"):
        uri = f"cdda://{os.path.basename(device)}/"
        try:
            result = subprocess.run(["gio", "mount", "-e", uri], capture_output=True, text=True,
                                    timeout=30)
            if result.returncode == 0 and not _still_in(device):
                return None
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        _eject_ioctl(device)
        if not _still_in(device):
            return None
        problems.append("the drive closed again")
    except OSError as error:
        problems.append(error.strerror or str(error))
    if shutil.which("eject"):
        try:
            result = subprocess.run(["eject", device], capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and not _still_in(device):
                return None
            problems.append(result.stderr.strip() or "the drive closed again")
        except (OSError, subprocess.SubprocessError) as error:
            problems.append(str(error))
    return ("Another program may be holding the CD and pulling it back in. Try Eject in the "
            "file manager, or the button on the drive. (" + "; ".join(p for p in problems if p)
            + ")")


# ---- Looking up names --------------------------------------------------------------

def _get(url: str, timeout: float = 15) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _credit(credits: list | None) -> str:
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in credits or []).strip()


def _top_genre(*groups) -> str:
    for genres in groups:
        if genres:
            best = max(genres, key=lambda g: g.get("count", 0))
            return best.get("name", "").title()
    return ""


def apply_release(disc: Disc, data: dict) -> bool:
    """Fill `disc` from a MusicBrainz /discid answer. True if a release fitted."""
    releases = data.get("releases") or []
    disc_id = disc.toc.disc_id()
    count = len(disc.tracks)
    best = None
    for release in releases:
        for medium in release.get("media") or []:
            ids = {d.get("id") for d in medium.get("discs") or []}
            tracks = medium.get("tracks") or []
            if disc_id in ids and len(tracks) == count:
                best = (release, medium)
                break
            if best is None and len(tracks) == count:
                best = (release, medium)           # a near match (found by the TOC)
        if best and disc_id in {d.get("id") for d in best[1].get("discs") or []}:
            break
    if best is None:
        return False
    release, medium = best
    disc.choices = len(releases)
    disc.release_id = release.get("id", "")
    group = release.get("release-group") or {}
    disc.release_group_id = group.get("id", "")
    disc.album = release.get("title") or disc.album
    album_artist = _credit(release.get("artist-credit"))
    disc.artist = album_artist
    credits = release.get("artist-credit") or []
    disc.compilation = any((c.get("artist") or {}).get("id") == VARIOUS_ARTISTS for c in credits)
    date = release.get("date") or group.get("first-release-date") or ""
    disc.year = int(date[:4]) if date[:4].isdigit() else None
    media = release.get("media") or []
    disc.disc_count = len(media) or None
    disc.disc_number = medium.get("position") or None
    recording_genres = [g for t in medium.get("tracks") or []
                        for g in (t.get("recording") or {}).get("genres") or []]
    disc.genre = _top_genre(release.get("genres"), group.get("genres"), recording_genres)
    for track, info in zip(disc.tracks, medium.get("tracks") or []):
        recording = info.get("recording") or {}
        track.name = info.get("title") or recording.get("title") or track.name
        track.artist = (_credit(info.get("artist-credit")) or _credit(recording.get("artist-credit"))
                        or album_artist)
    if disc.compilation or any(t.artist != album_artist for t in disc.tracks):
        disc.album_artist = album_artist
    disc.looked_up = True
    return True


class NotApproved(Exception):
    """Something needed the internet before you said it could."""


def lookup(disc: Disc, *, approved: bool, artwork: bool = True) -> Disc:
    """Fill in names from MusicBrainz (and the cover from the Cover Art
    Archive). Only with approved=True: the window asks you first, every time.
    Raises CdError if the internet or the database can't be reached; an
    unknown disc just keeps "Track 01"..."""
    if approved is not True:
        raise NotApproved("Looking up CD track names online needs your OK first.")
    query = urllib.parse.urlencode({"toc": disc.toc.toc_param(), "cdstubs": "no",
                                    "inc": "recordings artist-credits genres release-groups",
                                    "fmt": "json"})
    url = MUSICBRAINZ + disc.toc.disc_id() + "?" + query
    try:
        data = json.loads(_get(url))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return disc                       # not in the database
        raise CdError(f"MusicBrainz answered {error.code}.") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise CdError(f"Couldn't reach MusicBrainz ({getattr(error, 'reason', error)}).") from error
    if apply_release(disc, data) and artwork:
        disc.artwork = cover_art(disc.release_id, disc.release_group_id, approved=True)
    return disc


def cover_art(release_id: str, release_group_id: str = "", *, approved: bool) -> bytes | None:
    """The front cover (500 px), or None. Only with approved=True (it's online)."""
    if approved is not True:
        raise NotApproved("Downloading album artwork needs your OK first.")
    for kind, mbid in (("release", release_id), ("release-group", release_group_id)):
        if not mbid:
            continue
        try:
            data = _get(f"{COVER_ART}{kind}/{mbid}/front-500", timeout=20)
        except (urllib.error.URLError, OSError):
            continue
        if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
            return data
    return None


# ---- Ripping ---------------------------------------------------------------------------

@dataclass
class Progress:
    fraction: float                  # of this track
    remaining: float                 # seconds left for this track
    speed: float                     # times real time ("4.6x")


def rip_command(device: str, number: int, error_correction: bool) -> list[str]:
    """cdparanoia, writing the track's raw audio to stdout. Without error
    correction only its quick overlap check runs (much faster)."""
    cmd = [cdparanoia_path() or "cdparanoia", "--force-cdrom-device", device,
           "--output-raw", "--quiet"]           # raw 16-bit samples
    if not error_correction:
        cmd.append("--disable-extra-paranoia")     # -Y: overlap checking only
    return cmd + [str(number), "-"]


def raw_input_args(byte_order: str = "little") -> list[str]:
    fmt = "s16le" if byte_order == "little" else "s16be"
    return ["-f", fmt, "-ar", "44100", "-ac", "2", "-i", "pipe:0"]


CHECK_BYTES = BYTES_PER_SECOND * 2          # look at 2 s of real (not silent) audio
MAX_CHECK_BYTES = BYTES_PER_SECOND * 60     # but don't wait more than a minute of silence


def _roughness(samples) -> int:
    """How jumpy the waveform is: music changes smoothly from one sample to
    the next; the same bytes read in the wrong order look like loud noise."""
    return sum(abs(samples[i + 2] - samples[i]) for i in range(0, len(samples) - 2, 2))


def guess_byte_order(data: bytes) -> str | None:
    """"little" or "big": which way round these 16-bit samples make sense.
    cdparanoia has to guess each drive's byte order, and a wrong guess turns
    the whole track into static. None while there's only silence to go on."""
    import array
    usable = len(data) - len(data) % 4
    if usable < 4:
        return None
    native = array.array("h", data[:usable])
    if not any(native):
        return None
    swapped = array.array("h", native)
    swapped.byteswap()
    native_rough, swapped_rough = _roughness(native), _roughness(swapped)
    native_order = sys.byteorder
    other = "big" if native_order == "little" else "little"
    return native_order if native_rough <= swapped_rough else other


def _error_text(stream) -> str:
    stream.seek(0)
    lines = [l.strip() for l in stream.read().decode("utf-8", "replace").splitlines() if l.strip()]
    return lines[-1] if lines else ""


def rip_and_encode(device: str, track: CdTrack, settings: encoders.ImportSettings, target: str,
                   progress=None, stop: threading.Event | None = None) -> None:
    """Read one track and encode it into `target` as it's read."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise CdError("Importing needs FFmpeg: sudo apt install ffmpeg")
    if not cdparanoia_path():
        raise CdError("Importing CDs needs cdparanoia: sudo apt install cdparanoia")
    total = track.sectors * SECTOR_BYTES
    with tempfile.TemporaryFile() as rip_err, tempfile.TemporaryFile() as enc_err:
        rip = subprocess.Popen(rip_command(device, track.number, settings.error_correction),
                               stdout=subprocess.PIPE, stderr=rip_err)
        enc = None
        held = bytearray()           # the start of the track, until its byte order is known
        heard = 0                    # bytes of non-silent audio held so far

        def start_encoder(order):
            return subprocess.Popen(
                encoders.ffmpeg_command(ffmpeg, raw_input_args(order), target, settings),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=enc_err)

        done, started, last_report = 0, time.monotonic(), 0.0
        try:
            while True:
                if stop is not None and stop.is_set():
                    raise ImportStopped()
                chunk = rip.stdout.read1(SECTOR_BYTES * 64)
                if not chunk:
                    break
                done += len(chunk)
                if enc is None:
                    held += chunk
                    if any(chunk):
                        heard += len(chunk)
                    if heard >= CHECK_BYTES or len(held) >= MAX_CHECK_BYTES:
                        enc = start_encoder(guess_byte_order(bytes(held)) or "little")
                        enc.stdin.write(held)
                        held = bytearray()
                else:
                    enc.stdin.write(chunk)
                now = time.monotonic()
                if progress is not None and now - last_report > 0.2:
                    last_report = now
                    elapsed = max(now - started, 1e-6)
                    rate = done / elapsed
                    progress(Progress(min(1.0, done / total), max(0.0, (total - done) / rate),
                                      rate / BYTES_PER_SECOND))
            if enc is None:          # a short track: decide with what there is
                enc = start_encoder(guess_byte_order(bytes(held)) or "little")
                enc.stdin.write(held)
            enc.stdin.close()
            enc.wait()
            rip.wait()
        except BaseException:
            for process in (rip, enc):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()
            raise
        finally:
            if enc is not None and enc.stdin and not enc.stdin.closed:
                try:
                    enc.stdin.close()
                except OSError:
                    pass
        if rip.returncode != 0 or done < total * 0.98:
            message = _error_text(rip_err) or "the drive stopped reading"
            raise CdError(f"Couldn't read track {track.number}: {message}")
        if enc.returncode != 0:
            raise CdError(f"Couldn't encode track {track.number}: {_error_text(enc_err) or 'FFmpeg failed'}")
        if progress is not None:
            progress(Progress(1.0, 0.0, (done / max(time.monotonic() - started, 1e-6)) / BYTES_PER_SECOND))


def import_track(conn: sqlite3.Connection, disc: Disc, track: CdTrack,
                 settings: encoders.ImportSettings, progress=None,
                 stop: threading.Event | None = None) -> int:
    """Rip, encode, tag and add one track. Returns the new song's id. The
    file goes straight to where "Keep LuxTunes Media folder organized" puts
    it (Music/Artist/Album/01 Name.mp3)."""
    root = organize.media_folder(conn)
    fields = disc.fields(track)
    extension = encoders.extension(settings)
    target = organize.target_path(root, {**fields, "media_kind": "music"}, "new" + extension)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.stem}.importing{extension}")
    try:
        rip_and_encode(disc.device, track, settings, str(partial), progress, stop)
        os.replace(partial, target)
    except BaseException:
        try:
            os.remove(partial)
        except OSError:
            pass
        _remove_empty_folders(target.parent, root)
        raise
    tags = {k: v for k, v in fields.items() if v not in (None, "")}
    write_tags(str(target), tags, artworks=[disc.artwork] if disc.artwork else UNCHANGED)
    return add_files.add_file(conn, str(target), copy=False, root=root)


def _remove_empty_folders(folder: Path, root: Path) -> None:
    """Folders made for a track that was stopped (only empty ones, never the root)."""
    try:
        while folder != root and organize.is_inside(folder, root) and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent
    except OSError:
        pass
