"""An iPod's database, opened and saved by LuxTunes' own engine.

It answers to the same calls as gpod.Database (libgpod), so the sync code
works with either: tracks(), new_track(), copy_file(), set_artwork(),
write()... A "pointer" here is the Track itself (its .contents is itself).

Saving is careful:
  - the new song list is read back before it replaces the old one, and
    again from the iPod afterwards; should that fail, the old one goes back;
  - each file is written beside the old one, flushed to the iPod, then
    swapped in (one rename), so a pulled cable leaves the old file, not half
    a new one;
  - ArtworkDB is saved first, as libgpod does, so the song list never
    points at pictures that aren't there;
  - new pictures go on the end of the .ithmb files: the ones there aren't
    touched.

Covers the iPods whose song list is a plain iTunesDB (the 1st–5.5th
generation, mini, photo, classic and nano 1st–4th generation; hash58 for the
classic and nano 3rd/4th) and the nano 5th–7th generation, whose song list
is compressed (iTunesCDB) and also kept in SQLite (sqlitedb.py), signed with
hash72 (5th) or hashAB (6th/7th). The shuffles' iTunesSD isn't written yet.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
from pathlib import Path

from luxtunes.core.ipod.errors import IPodDatabaseError
from luxtunes.core.ipod.itdb import artwork, hash58
from luxtunes.core.ipod.itdb.binary import Corrupt
from luxtunes.core.ipod.itdb.itunesdb import (  # noqa: F401  (MEDIATYPE_*: same names as gpod)
    MEDIATYPE_AUDIO,
    MEDIATYPE_AUDIOBOOK,
    MEDIATYPE_MOVIE,
    MEDIATYPE_MUSICVIDEO,
    MEDIATYPE_PODCAST,
    MEDIATYPE_TVSHOW,
    ITunesDB,
    Playlist,
    Track,
    merge_play_counts,
    merge_play_counts_plist,
    parse_otg,
)

log = logging.getLogger(__name__)

GpodError = IPodDatabaseError               # what sync.py catches, whichever engine
ART_INDEX = "LuxTunes artwork.json"         # picture fingerprint -> image id, to share pixels
TEMP_SUFFIX = ".luxtunes-new"
SQLITE_FAMILIES = ("nano_5", "nano_6", "nano_7")
NAME_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def get_str(track: Track, field: str) -> str | None:
    return getattr(track, field)


def set_str(track: Track, field: str, value) -> None:
    setattr(track, field, value)


def supports(family_key: str, signature: str) -> bool:
    """This engine can write this iPod's song list."""
    from luxtunes.core.ipod import models
    if family_key in SQLITE_FAMILIES:
        return True
    if family_key.startswith("shuffle"):          # (their iTunesSD isn't written yet)
        return False
    return signature in (models.NO_SIGNATURE, models.HASH58)


def write_atomic(path: Path, data: bytes) -> None:
    """Replace the file in one step: written beside it, flushed, then renamed over."""
    temporary = path.with_name(path.name + TEMP_SUFFIX)
    with open(temporary, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)
    try:                                    # the rename itself, onto the disk
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:                         # (not possible on Windows: fine)
        pass


def find_on_ipod(root: Path, ipod_path: str | None) -> str | None:
    """":iPod_Control:Music:F01:ABCD.mp3" -> its file on this computer, matching
    letter case as the iPod's disk has it. None if it points outside the iPod."""
    if not ipod_path:
        return None
    parts = [p for p in ipod_path.replace("/", ":").replace("\\", ":").split(":") if p]
    if not parts or any(p in (".", "..") for p in parts):
        return None
    path = root.joinpath(*parts)
    if path.exists():
        return str(path)
    here = root
    for part in parts:                      # a disk that minds letter case
        candidate = here / part
        if not candidate.exists():
            try:
                match = next((e for e in os.listdir(here) if e.lower() == part.lower()), None)
            except OSError:
                match = None
            candidate = here / (match or part)
        here = candidate
    return str(here)


class Database:
    """An iPod's song list and covers. Use as a context manager, like gpod.Database."""

    def __init__(self, mountpoint: str, family_key: str = "unknown", firewire_guid: str = "",
                 signature: str = "none", music_folders: int | None = None,
                 read_only: bool = False):
        """read_only: only to read the songs (e.g. to import them), so any iPod
        will do, the nano 5th–7th generation included."""
        from luxtunes.core.ipod import models
        self.read_only = read_only
        self.sqlite = family_key in SQLITE_FAMILIES
        self.root = Path(mountpoint)
        self.mountpoint = mountpoint
        self.family_key = family_key
        self.signature = signature
        self.guid = hash58.parse_guid(firewire_guid) if firewire_guid else None
        if self.sqlite:                             # (their own signatures say which)
            self.signature = {"nano_5": models.HASH72}.get(family_key, models.HASH_AB)
        self._raw_song_list = b""
        self.music_folders = music_folders or (50 if family_key.startswith(("classic", "video"))
                                               or family_key == "unknown" else 20)
        self.itunes = self.root / "iPod_Control" / "iTunes"
        self.art_folder = self.root / "iPod_Control" / "Artwork"
        self._otg_files: list[Path] = []
        self.db = self._read_song_list()
        self._read_since_last_sync()
        self.art: artwork.ArtworkDB | None = None
        self.art_formats: list[artwork.ArtFormat] = []
        self.art_changed = False
        self._ithmb = artwork.IthmbFiles(str(self.art_folder))
        self._art_index: dict[str, int] = {}
        self._images: dict[int, artwork.Image] = {}
        if self.signature != models.NO_SIGNATURE and self.guid is None and not read_only:
            raise IPodDatabaseError("The iPod's FireWire ID couldn't be read, so its song list "
                                    "can't be signed.")
        self.hash_info = None
        if self.signature == models.HASH72 and not read_only:
            self.hash_info = self._hash_info()
        self._read_artwork()

    # ---- Opening ------------------------------------------------------------------------

    def _read_song_list(self) -> ITunesDB:
        from luxtunes.core.ipod.itdb import cdb
        data = b""
        for name in ("iTunesCDB", "iTunesDB"):          # the newer nanos' first
            path = self.itunes / name
            try:
                data = path.read_bytes() if path.exists() else b""
            except OSError as error:
                raise IPodDatabaseError(f"The iPod's song list couldn't be read: {error}") \
                    from error
            if data:
                break
        self._raw_song_list = data
        if not data:
            log.info("no song list on the iPod yet: starting an empty one")
            return ITunesDB.new_empty(self._ipod_name())
        try:
            if cdb.is_compressed(data):
                data = cdb.decompress(data)
            return ITunesDB.parse(data)
        except Corrupt as error:
            raise IPodDatabaseError(f"The iPod's song list couldn't be read ({error}). "
                                    "Restoring the iPod with iTunes or Finder fixes "
                                    "that (it erases the iPod).") from error

    def _ipod_name(self) -> str:
        return self.root.name or "iPod"

    def _hash_info(self):
        """The nano 5th generation's secret for hash72: from its HashInfo file,
        or recovered from the song list iTunes last signed (then kept in HashInfo)."""
        from luxtunes.core.ipod.itdb import hash72
        path = self.root / "iPod_Control" / "Device" / "HashInfo"
        try:
            info = hash72.HashInfo.parse(path.read_bytes(), self.guid) if path.exists() else None
        except OSError:
            info = None
        if info is None and self._raw_song_list:
            info = hash72.extract_from_db(self._raw_song_list, self.guid)
            if info is not None:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(info.to_bytes())
                except OSError as error:
                    log.warning("couldn't save HashInfo: %s", error)
        if info is None:
            raise IPodDatabaseError("This iPod nano needs to have been synced with iTunes once, "
                                    "so LuxTunes can reuse its signature.")
        return info

    def _read_since_last_sync(self) -> None:
        """Play Counts and On-The-Go playlists, which the iPod wrote (as libgpod does)."""
        for name, merge in (("Play Counts", merge_play_counts),
                            ("PlayCounts.plist", merge_play_counts_plist)):
            path = self.itunes / name
            try:
                data = path.read_bytes() if path.exists() else None
            except OSError as error:
                log.warning("couldn't read %s: %s", name, error)
                data = None
            if data:
                log.info("%s: %d songs played or rated on the iPod", name, merge(self.db, data))
        for i in range(100):
            path = self.itunes / ("OTGPlaylistInfo" if i == 0 else f"OTGPlaylistInfo_{i}")
            if not path.exists():
                if i > 0:
                    break
                continue
            self._otg_files.append(path)
            try:
                items = parse_otg(self.db, path.read_bytes())
            except OSError:
                continue
            if items:
                playlist = Playlist(f"On-The-Go {len(self._otg_files)}")
                playlist.items = items
                self.db.playlists.append(playlist)

    def _read_artwork(self) -> None:
        wanted = artwork.formats_for(self.family_key)
        if not wanted:
            return
        path = self.art_folder / "ArtworkDB"
        try:
            data = path.read_bytes() if path.exists() else b""
        except OSError as error:
            log.warning("couldn't read ArtworkDB, so covers are left as they are: %s", error)
            return
        if data:
            try:
                art = artwork.ArtworkDB.parse(data)
            except Corrupt as error:            # never overwrite what can't be read
                log.warning("%s; covers are left as they are", error)
                return
        else:
            art = artwork.ArtworkDB()
        self.art = art
        self.art_formats = art.formats(wanted)
        self._images = {image.id: image for image in art.images}
        try:
            index = json.loads((self.art_folder / ART_INDEX).read_text("utf-8"))
            self._art_index = {k: int(v) for k, v in index.items()
                               if isinstance(v, int) and int(v) in self._images}
        except (OSError, ValueError, AttributeError):
            self._art_index = {}

    # ---- Context ------------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        pass

    # ---- Songs and playlists (as gpod.Database) ------------------------------------------

    def tracks(self) -> list[Track]:
        return list(self.db.tracks)

    def playlists(self) -> list[Playlist]:
        return list(self.db.playlists)

    def master(self) -> Playlist:
        return self.db.master

    def name(self) -> str:
        return self.db.master.name

    def set_name(self, name: str) -> None:
        self.db.master.name = name

    def new_track(self) -> Track:
        track = Track(dbid=self.db.new_dbid())
        self.db.tracks.append(track)
        self.db.master.items.append(track)
        return track

    def _music_folders(self) -> list[Path]:
        music = self.root / "iPod_Control" / "Music"
        try:
            found = sorted(e.path for e in os.scandir(music) if e.is_dir()
                           and len(e.name) == 3 and e.name[0] in "Ff" and e.name[1:].isdigit())
        except OSError:
            found = []
        if not found:
            for i in range(self.music_folders):
                (music / f"F{i:02d}").mkdir(parents=True, exist_ok=True)
            found = [str(music / f"F{i:02d}") for i in range(self.music_folders)]
        return [Path(p) for p in found]

    def copy_file(self, track: Track, path: str) -> None:
        """Copy the song's file onto the iPod (a new name in one of its music
        folders) and point the track at it."""
        extension = os.path.splitext(path)[1].lower() or ".mp3"
        try:
            folders = self._music_folders()
            for _ in range(1000):
                folder = secrets.choice(folders)
                name = "".join(secrets.choice(NAME_LETTERS) for _ in range(4)) + extension
                target = folder / name
                if not target.exists():
                    break
            else:
                raise IPodDatabaseError("no free file name on the iPod")
            try:
                with open(path, "rb") as source, open(target, "xb") as out:
                    shutil.copyfileobj(source, out, 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        except OSError as error:
            raise IPodDatabaseError(f"couldn't copy the song to the iPod: {error}") from error
        relative = target.relative_to(self.root)
        track.ipod_path = ":" + ":".join(relative.parts)
        track.filetype_marker = int.from_bytes(
            extension[1:5].upper().ljust(4).encode("ascii", "replace"), "big")
        if not track.size:
            track.size = target.stat().st_size

    def file_of(self, track: Track) -> str | None:
        return find_on_ipod(self.root, track.ipod_path)

    def remove_track(self, track: Track) -> None:
        self.db.tracks = [t for t in self.db.tracks if t is not track]
        for playlist in self.db.playlists:
            playlist.items = [t for t in playlist.items if t is not track]

    def remove_playlist(self, playlist: Playlist) -> None:
        if playlist.is_master:
            return
        self.db.playlists = [p for p in self.db.playlists if p is not playlist]

    def new_playlist(self, name: str) -> Playlist:
        playlist = Playlist(name)
        self.db.playlists.append(playlist)
        return playlist

    def add_to_playlist(self, playlist: Playlist, track: Track) -> None:
        playlist.items.append(track)

    def is_master(self, playlist: Playlist) -> bool:
        return playlist.is_master

    def is_podcasts(self, playlist: Playlist) -> bool:
        return playlist.is_podcasts

    def podcasts_playlist(self, create: bool = True) -> Playlist | None:
        found = next((p for p in self.db.playlists if p.is_podcasts), None)
        if found is None and create:
            found = Playlist("Podcasts")
            found.set_podcasts()
            self.db.playlists.insert(1, found)
        return found

    def playlist_tracks(self, playlist: Playlist) -> list[Track]:
        return list(playlist.items)

    def remove_from_playlist(self, playlist: Playlist, track: Track) -> None:
        playlist.items = [t for t in playlist.items if t is not track]

    # ---- Covers -------------------------------------------------------------------------

    def _image_of(self, track: Track) -> artwork.Image | None:
        if track.has_artwork != 1 or not track.artwork_count:
            return None
        image = self._images.get(track.mhii_link) if track.mhii_link else None
        if image is None and self.art is not None:      # iPods written before mhii_link
            image = next((i for i in self.art.images if i.song_id == track.dbid), None)
        return image

    def has_artwork(self, track: Track) -> bool:
        image = self._image_of(track)
        return image is not None and bool(image.thumbs)

    def artwork_formats(self, track: Track) -> set[int]:
        image = self._image_of(track)
        return {t.format_id for t in image.thumbs} if image else set()

    def set_artwork(self, track: Track, data: bytes) -> bool:
        """Give the song this cover. A picture already on the iPod (the rest of
        the album) is shared: its pixels aren't written again."""
        if self.art is None or not data:
            return False
        key = hashlib.sha1(data).hexdigest()
        wanted = {f.id for f in self.art_formats}
        image_id = self.art.next_id()
        same = self._images.get(self._art_index.get(key, -1))
        if same is not None and wanted <= {t.format_id for t in same.thumbs}:
            image = artwork.Image(bytearray(same.hdr), image_id, track.dbid, same.orig_size,
                                  [artwork.Thumb(**vars(t)) for t in same.thumbs])
        else:
            try:
                image = artwork.build_image(self._ithmb, data, self.art_formats, image_id,
                                            track.dbid)
            except OSError as error:
                log.warning("couldn't add a cover: %s", error)
                return False
            if image is None:
                return False
            self._art_index[key] = image_id
        self.art.images.append(image)
        self._images[image_id] = image
        track.set_artwork(image_id, len(data))
        self.art_changed = True
        return True

    def _drop_unused_images(self) -> None:
        linked = {t.mhii_link for t in self.db.tracks if t.has_artwork == 1 and t.mhii_link}
        older = {t.dbid for t in self.db.tracks if t.has_artwork == 1 and not t.mhii_link}
        kept = [i for i in self.art.images if i.id in linked or (i.song_id in older and i.song_id)]
        if len(kept) == len(self.art.images):
            return
        # A shared picture stays shareable while any song still shows it.
        where = {(i.thumbs[0].file, i.thumbs[0].offset): i.id for i in kept if i.thumbs}
        kept_ids = {i.id for i in kept}
        index = {}
        for key, image_id in self._art_index.items():
            image = self._images.get(image_id)
            if image_id in kept_ids:
                index[key] = image_id
            elif image is not None and image.thumbs:
                other = where.get((image.thumbs[0].file, image.thumbs[0].offset))
                if other is not None:
                    index[key] = other
        self.art.images = kept
        self._images = {i.id: i for i in kept}
        self._art_index = index
        self.art_changed = True

    # ---- Saving -------------------------------------------------------------------------

    def _nano(self):
        """The nano 6th/7th generation's details (hash_ab_nanos.py), or None."""
        if self.family_key in ("nano_6", "nano_7"):
            from luxtunes.core.ipod import hash_ab_nanos
            return hash_ab_nanos.NANOS[self.family_key]
        return None

    def song_list_bytes(self) -> bytes:
        """The song list as it's saved: signed, and for the newer nanos compressed."""
        from luxtunes.core.ipod import models
        data = self.db.serialize()
        if self.sqlite:
            out = bytearray(data)
            out[0x0C:0x10] = (2).to_bytes(4, "little")
            kind = 2 if self.signature == models.HASH72 else 4
            out[0x70:0x72] = kind.to_bytes(2, "little")
            out[0x32:0x46] = bytes(0x46 - 0x32)
            nano = self._nano()
            if nano is not None:
                out[0x10:0x14] = nano.song_list_version.to_bytes(4, "little")
            from luxtunes.core.ipod.itdb import cdb
            data = cdb.compress(bytes(out))
        if self.signature == models.HASH58:
            data = hash58.sign(data, self.guid)
        elif self.signature == models.HASH72:
            from luxtunes.core.ipod.itdb import hash72
            data = hash58.sign(hash72.sign(data, self.hash_info, 1), self.guid)
        elif self.signature == models.HASH_AB:
            from luxtunes.core.ipod import hashab
            data = hashab.sign_song_list(data, self.guid)
        return data

    def _sign_locations(self, sha1: bytes) -> bytes:
        from luxtunes.core.ipod import hashab, models
        if self.signature == models.HASH72:
            return self.hash_info.sign(sha1)
        return hashab.signature(sha1, self.guid)

    def _post_process(self) -> list[str]:
        """The nano 5th generation's own SQL, from its SysInfoExtended (if it has one)."""
        from luxtunes.core.ipod.itdb import sqlitedb
        path = self.root / "iPod_Control" / "Device" / "SysInfoExtended"
        try:
            return sqlitedb.post_process_commands(path.read_bytes()) if path.exists() else []
        except OSError:
            return []

    def write(self) -> None:
        if self.read_only:
            raise IPodDatabaseError("opened only to read")
        try:
            self._write()
        except OSError as error:
            raise IPodDatabaseError(f"couldn't save the iPod's database: {error}") from error

    def _write(self) -> None:
        self.itunes.mkdir(parents=True, exist_ok=True)
        if self.art is not None:
            self._drop_unused_images()
        if self.art is not None and self.art_changed:
            for fmt in self.art_formats:
                for i, (format_id, _) in enumerate(self.art.files):
                    if format_id == fmt.id:
                        self.art.files[i] = (fmt.id, fmt.frame_size)
                        break
                else:
                    self.art.files.append((fmt.id, fmt.frame_size))
            self._ithmb.flush()
            self.art_folder.mkdir(parents=True, exist_ok=True)
            art_bytes = self.art.serialize()
            artwork.ArtworkDB.parse(art_bytes)          # reads back before it's saved
            write_atomic(self.art_folder / "ArtworkDB", art_bytes)
            try:
                (self.art_folder / ART_INDEX).write_text(json.dumps(self._art_index), "utf-8")
            except OSError as error:
                log.warning("couldn't save %s: %s", ART_INDEX, error)
            self.art_changed = False

        data = self.song_list_bytes()
        from luxtunes.core.ipod.itdb import cdb
        check = ITunesDB.parse(cdb.decompress(data) if self.sqlite else data)
        if len(check.tracks) != len(self.db.tracks) or \
                len(check.playlists) != len(self.db.playlists):
            raise IPodDatabaseError("the new song list didn't read back the same, so the old "
                                    "one was kept")
        if self.sqlite:                                 # what these nanos read their songs from
            from luxtunes.core.ipod.itdb import sqlitedb
            sqlitedb.write(self.itunes, self.db, self._sign_locations, self._nano(),
                           [] if self._nano() else self._post_process())
        path = self.itunes / ("iTunesCDB" if self.sqlite else "iTunesDB")
        before = path.read_bytes() if path.exists() else None
        write_atomic(path, data)
        try:
            ok = path.read_bytes() == data
        except OSError:
            ok = False
        if not ok:
            if before is not None:
                write_atomic(path, before)
            raise IPodDatabaseError("the iPod didn't keep the new song list (it read back "
                                    "differently), so the old one was put back")
        if self.sqlite:
            plain = self.itunes / "iTunesDB"
            if self._nano() is not None:
                # The 6th/7th generation read the compressed list (and SQLite).
                # iTunes leaves no plain one on the 6th, but keeps a full one
                # on the 7th, so a full one is left alone; only an empty one
                # (as libgpod leaves) goes.
                if plain.exists() and plain.stat().st_size == 0:
                    plain.unlink()
            else:
                write_atomic(plain, b"")                # the 5th generation: an empty one
        for otg in self._otg_files:                     # now part of the song list
            try:
                otg.unlink()
            except OSError:
                pass
        self._otg_files = []
