"""Syncing an iPod, using libgpod (gpod.py).

The order of a sync:
  1. Open the iPod's database. libgpod also reads "Play Counts", the iPod's
     record of plays, skips and rating changes since the last sync.
  2. Add those to the library (stats.merge: only ever adds, never resets),
     then rename "Play Counts" so the same plays can never be counted twice.
  3. Work out the plan (plan.py) and compare it with what's on the iPod:
     remove songs that aren't wanted any more (from the iPod only), copy the
     missing ones, and refresh every song's details (names, ratings, play
     counts...) from the library. A wanted song whose file can't be found on
     this computer stays on the iPod.
  4. Rebuild the playlists, set the iPod's name, and save the database
     (libgpod adds the 6G/7G signature).

Nothing here deletes or changes anything in the library except step 2.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from luxtunes.core import artwork as artwork_reader
from luxtunes.core import library
from luxtunes.core.ipod import gpod, models, plan as ipod_plan, settings as ipod_settings, stats
from luxtunes.core.ipod.device import IPod, usb_details
from luxtunes.core.paths import find_file

log = logging.getLogger(__name__)

# iPod classic (all of 6G/6.5G/7G) reports this USB product id; they need hash58.


class SyncCancelled(Exception):
    pass


@dataclass
class SyncReport:
    added: int = 0
    removed: int = 0
    updated: int = 0
    playlists: int = 0
    stats: stats.MergeResult | None = None
    problems: list[str] = field(default_factory=list)
    cancelled: bool = False
    photos: int = 0
    phone_playlists: object = None     # Android: changes.PlaylistResult from the phone


@dataclass
class Removal:
    """A song the sync is about to take off the iPod, and why."""
    name: str
    artist: str
    album: str
    reason: str


def describe_removals(to_remove, on_ipod: dict, library_ids: set[int]) -> list[Removal]:
    removals = []
    for pointer in to_remove:
        t = pointer.contents
        if ctypes.addressof(on_ipod[t.dbid].contents) != ctypes.addressof(t):
            reason = "A second copy of a song that stays on the iPod"
        elif t.dbid not in library_ids:
            reason = "Not in your LuxTunes library"
            kind = (gpod.get_str(t, "filetype") or "") + (gpod.get_str(t, "ipod_path") or "")
            if "protected" in kind.lower() or kind.lower().endswith(".m4p"):
                reason += " (protected file from the iTunes Store)"
        else:
            reason = "Not in your sync settings"
        removals.append(Removal(gpod.get_str(t, "title") or "", gpod.get_str(t, "artist") or "",
                                gpod.get_str(t, "album") or "", reason))
    return removals


# ---- Small conversions ------------------------------------------------------------------

def to_unix(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def dbid_of(track: dict) -> int:
    try:
        return int(track["persistent_id"], 16)
    except (KeyError, TypeError, ValueError):
        return 0


# ---- The iPod's FireWire GUID (needed for the 6G/7G signature) ---------------------

def prepare_device_info(db: gpod.Database, ipod: IPod) -> None:
    """Make sure libgpod knows the FireWire GUID and the iPod's model, so it
    signs the song list on iPods that check one (classics, nano 3rd–5th
    generation). SysInfo is often empty on iPods that were never plugged into
    a Linux machine with libgpod's helper; the GUID is written into SysInfo
    for next time, the model only kept in memory."""
    product, serial = usb_details(ipod.device)
    if not db.sysinfo("FirewireGuid") and serial:
        db.set_sysinfo("FirewireGuid", f"0x{serial}")
        try:
            db.write_sysinfo()
        except gpod.GpodError as error:
            log.warning("couldn't save SysInfo: %s", error)
    if not db.sysinfo("ModelNumStr"):
        stand_in = models.stand_in_model_number(product)
        if stand_in:
            db.set_sysinfo("ModelNumStr", stand_in)


# ---- Copying details from the library to an iPod track -------------------------

STRING_MAP = {
    "title": "name", "album": "album", "artist": "artist", "albumartist": "album_artist",
    "genre": "genre", "composer": "composer", "comment": "comments", "grouping": "grouping",
    "sort_title": "sort_name", "sort_artist": "sort_artist", "sort_album": "sort_album",
    "sort_albumartist": "sort_album_artist", "sort_composer": "sort_composer",
    "filetype": "kind",
}


def fill_track(t: "gpod.Itdb_Track", song: dict) -> None:
    """Copy one library song's details onto its iPod track."""
    for field_name, key in STRING_MAP.items():
        gpod.set_str(t, field_name, song.get(key))
    movie = song.get("media_kind") == "movie"
    tv = song.get("media_kind") == "tvshow"
    t.size = int(song.get("size") or t.size or 0)
    t.tracklen = int(song.get("total_time") or 0)
    t.cd_nr = int(song.get("disc_number") or 0)
    t.cds = int(song.get("disc_count") or 0)
    t.track_nr = int(song.get("track_number") or 0)
    t.tracks = int(song.get("track_count") or 0)
    t.bitrate = int(song.get("bit_rate") or 0)
    t.samplerate = min(int(song.get("sample_rate") or 0), 65535)
    t.year = int(song.get("year") or 0)
    t.BPM = int(song.get("bpm") or 0)
    rating = 0 if song.get("rating_computed") else int(song.get("rating") or 0)
    t.rating = rating
    t.app_rating = rating                    # equal = "not changed on the iPod"
    t.playcount = t.playcount2 = int(song.get("play_count") or 0)
    t.recent_playcount = 0
    t.skipcount = int(song.get("skip_count") or 0)
    t.recent_skipcount = 0
    t.time_added = to_unix(song.get("date_added"))
    t.time_modified = to_unix(song.get("date_modified"))
    t.time_played = to_unix(song.get("play_date"))
    t.last_skipped = to_unix(song.get("skip_date"))
    t.compilation = 1 if song.get("compilation") else 0
    t.checked = 0 if song.get("enabled", 1) else 1          # 0 means checked
    t.starttime = int(song.get("start_time") or 0)
    t.stoptime = int(song.get("stop_time") or 0)
    t.volume = max(-255, min(255, round((song.get("volume_adjustment") or 0) * 2.55)))
    t.remember_playback_position = 1 if song.get("bookmarkable") else 0
    t.skip_when_shuffling = 1 if song.get("skip_when_shuffling") else 0
    t.bookmark_time = int(song.get("bookmark_time") or 0)
    t.gapless_album_flag = 1 if song.get("part_of_gapless_album") else 0
    t.lyrics_flag = 1 if song.get("lyrics") else 0
    if tv:
        t.mediatype = gpod.MEDIATYPE_TVSHOW
        gpod.set_str(t, "tvshow", song.get("show") or song.get("album"))
        gpod.set_str(t, "tvepisode", song.get("episode_id"))
        t.season_nr = int(song.get("season") or 0)
        t.episode_nr = int(song.get("episode_number") or 0)
    else:
        t.mediatype = {"movie": gpod.MEDIATYPE_MOVIE, "podcast": gpod.MEDIATYPE_PODCAST,
                       "audiobook": gpod.MEDIATYPE_AUDIOBOOK}.get(song.get("media_kind"),
                                                                gpod.MEDIATYPE_AUDIO)
    podcast = song.get("media_kind") == "podcast"
    # A podcast's bullet on the iPod: 2 = unplayed, 1 = played (and for everything else).
    t.mark_unplayed = 2 if podcast and not song.get("play_count") else 1
    t.flag4 = 1 if podcast else 0
    t.movie_flag = 1 if movie or tv else 0
    t.dbid = dbid_of(song)
    kind = (song.get("kind") or "").lower()
    t.type2 = 1 if "mpeg" in kind else 0


def _convert_for_ipod(path: str, kbps: int, folder: str, report: SyncReport,
                      song: dict) -> str | None:
    """An AAC copy of the song for the iPod, in `folder`; None (and a problem
    noted) if it can't be made. The library's file is never changed."""
    from luxtunes.core import convert
    ffmpeg = convert.ffmpeg_path()
    if ffmpeg is None:
        if not any("FFmpeg" in p for p in report.problems):
            report.problems.append("Converting songs needs FFmpeg (sudo apt install ffmpeg); "
                                   "those songs weren't copied.")
        return None
    target = os.path.join(folder, f"{song.get('id') or 'song'}.m4a")
    try:
        result = subprocess.run(convert.ipod_audio_command(ffmpeg, path, target, kbps),
                                capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as error:
        report.problems.append(f"{song.get('name')}: couldn't convert ({error})")
        return None
    if result.returncode != 0 or not os.path.exists(target):
        last = (result.stderr.strip().splitlines() or ["FFmpeg failed"])[-1]
        report.problems.append(f"{song.get('name')}: couldn't convert ({last})")
        return None
    return target


def unlisted_song_files(mountpoint: str, listed: list) -> list[str]:
    """Song files in the iPod's music folders (iPod_Control/Music/F00…F99)
    that its database doesn't list. Nothing else on the iPod is looked at.
    With an empty database nothing counts as unlisted (to be safe)."""
    known = {os.path.normcase(os.path.realpath(p)).lower() for p in listed if p}
    if not known:
        return []
    music = Path(mountpoint) / "iPod_Control" / "Music"
    found = []
    try:
        folders = [e for e in os.scandir(music) if e.is_dir(follow_symlinks=False)
                   and len(e.name) == 3 and e.name[0] in "Ff" and e.name[1:].isdigit()]
    except OSError:
        return []
    for folder in folders:
        try:
            entries = list(os.scandir(folder.path))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file(follow_symlinks=False) or entry.name.startswith("."):
                continue
            if os.path.normcase(os.path.realpath(entry.path)).lower() not in known:
                found.append(entry.path)
    return found


def _sync_photos(ipod: IPod, settings, report: SyncReport, progress) -> None:
    from luxtunes.core.ipod import photos
    try:
        if not settings.sync_photos:
            if photos.remove_all(ipod.mountpoint):
                log.info("removed LuxTunes' photos from %s", ipod.mountpoint)
            return
        albums = photos.chosen_albums(settings.photos_folder, settings.photos_mode,
                                      settings.photo_folders)

        def step(done, total):
            if progress is None:
                return True
            return progress("Adding photos…", done, total) is not False

        report.photos, problems = photos.sync(ipod.mountpoint, albums, settings.full_resolution,
                                              step)
        report.problems += problems
    except (gpod.GpodError, OSError) as error:
        report.problems.append(f"Photos: {error}")


def read_ipod_stats(db: gpod.Database, ratings_too: bool) -> list[stats.IPodTrackStats]:
    items = []
    for pointer in db.tracks():
        t = pointer.contents
        rating_at_sync = t.app_rating if ratings_too else t.rating
        items.append(stats.IPodTrackStats(
            dbid=t.dbid, recent_plays=t.recent_playcount, recent_skips=t.recent_skipcount,
            last_played=int(t.time_played or 0), last_skipped=int(t.last_skipped or 0),
            rating=t.rating, rating_at_sync=rating_at_sync))
    return items


PLAY_COUNT_FILES = ("Play Counts", "iTunesStats", "PlayCounts.plist")


def set_aside_play_counts(ipod: IPod) -> list[Path]:
    """Rename the iPod's play-count files once they're in the library, so the
    same plays can't be added twice (even if the sync stops halfway)."""
    moved = []
    for name in PLAY_COUNT_FILES:
        path = ipod.control / "iTunes" / name
        if path.exists():
            target = path.with_name(name + ".luxtunes-imported")
            os.replace(path, target)
            moved.append(target)
    return moved


# ---- The sync ---------------------------------------------------------------------------

def sync(conn, ipod: IPod, record: ipod_settings.DeviceRecord, progress=None,
         confirm=None) -> SyncReport:
    """Sync this iPod. progress(text, done, total) may return False to stop
    copying. confirm(adding, removing, removals) is asked before anything on
    the iPod changes (removals: a Removal for each song to come off) and may
    return False to cancel. Raises gpod.GpodError."""
    report = SyncReport()
    if ipod.unsupported:
        raise gpod.GpodError(ipod.unsupported)
    if not os.access(ipod.mountpoint, os.W_OK):
        raise gpod.GpodError(
            "The iPod can only be read, not written to. iPods formatted for Mac can't be "
            "written to on Linux; restoring it with the Windows format fixes that (it erases "
            "the iPod).")

    def tell(text, done=0, total=0, can_stop=True):
        if progress is not None and progress(text, done, total) is False and can_stop:
            raise SyncCancelled()

    tell("Reading the iPod…")
    with gpod.Database(ipod.mountpoint) as db:
        prepare_device_info(db, ipod)
        settings = record.settings
        all_tracks = db.tracks()
        on_ipod = {}
        for pointer in all_tracks:
            on_ipod.setdefault(pointer.contents.dbid, pointer)

        library_by_dbid = {dbid_of(song): song for song in library.load_tracks(conn)}

        # What should be on the iPod.
        keep = set()
        if settings.manual:
            wanted = {}
        else:
            space = os.statvfs(ipod.mountpoint)
            capacity = space.f_blocks * space.f_frsize
            from luxtunes.core import smart
            smart.refresh_all(conn)          # smart playlists go over as they are right now
            the_plan = ipod_plan.build_plan(conn, settings, free_space=capacity,
                                            video=ipod.supports_video)
            wanted = {dbid_of(t): t for t in the_plan.tracks if dbid_of(t)}
            keep = set(wanted)
            for t in the_plan.missing:
                if dbid_of(t) in on_ipod:     # can't be copied again, so don't take it off
                    keep.add(dbid_of(t))
                    report.problems.append(f"{t.get('name')}: file not found on this computer "
                                           "(the copy on the iPod is kept)")
                else:
                    report.problems.append(f"{t.get('name')}: file not found")
        # Unwanted songs, plus any second copy of a song (same ID twice).
        to_remove = [] if settings.manual else [
            p for p in all_tracks
            if p.contents.dbid not in keep
            or ctypes.addressof(on_ipod[p.contents.dbid].contents) != ctypes.addressof(p.contents)]
        to_add = [t for dbid, t in wanted.items() if dbid not in on_ipod]

        if confirm is not None and not confirm(
                len(to_add), len(to_remove),
                describe_removals(to_remove, on_ipod, set(library_by_dbid))):
            report.cancelled = True
            return report

        # 1. Plays, skips and ratings from the iPod into the library.
        first_sync = record.last_sync is None
        report.stats = stats.merge(conn, read_ipod_stats(db, ratings_too=not first_sync))
        set_aside_play_counts(ipod)

        # 2. Take unwanted songs off the iPod (never out of the library). Only
        # out of its database for now: the files go once the database is saved,
        # so if LuxTunes is stopped before that the iPod stays as it was.
        doomed_files = []
        for i, pointer in enumerate(to_remove):
            tell("Removing songs from the iPod…", i, len(to_remove), can_stop=False)
            path = db.file_of(pointer)
            if path:
                doomed_files.append(path)
            db.remove_track(pointer)
            report.removed += 1

        # 3. Refresh the details of songs that stay.
        kept = {dbid: p for dbid, p in on_ipod.items() if dbid in library_by_dbid
                and (settings.manual or dbid in keep)}
        for dbid, pointer in kept.items():
            fill_track(pointer.contents, library_by_dbid[dbid])
            report.updated += 1

        # 4. Copy new songs (converted on the way when the settings say so).
        added = {}
        scratch = tempfile.TemporaryDirectory(prefix="luxtunes-sync-")
        try:
            for i, song in enumerate(to_add):
                path = find_file(song.get("location"))
                if path is None:
                    report.problems.append(f"{song.get('name')}: file not found")
                    continue
                kbps = ipod_plan.transfer_kbps(song, settings)
                source = path
                if kbps is not None:
                    tell(f"Converting “{song.get('name')}”", i, len(to_add))
                    source = _convert_for_ipod(path, kbps, scratch.name, report, song)
                    if source is None:
                        continue
                tell(f"Copying “{song.get('name')}”", i, len(to_add))
                pointer = db.new_track()
                fill_track(pointer.contents, song)
                pointer.contents.size = os.path.getsize(source)
                if source != path:
                    pointer.contents.bitrate = kbps
                    pointer.contents.type2 = 0
                    gpod.set_str(pointer.contents, "filetype", "AAC audio file")
                try:
                    db.copy_file(pointer, source)
                except gpod.GpodError as error:
                    db.remove_track(pointer)
                    report.problems.append(f"{song.get('name')}: {error}")
                    continue
                finally:
                    if source != path:
                        try:
                            os.remove(source)
                        except OSError:
                            pass
                art = artwork_reader.read_embedded_artwork(path) if ipod.supports_artwork \
                    else None
                if art:
                    db.set_artwork(pointer, art)
                added[dbid_of(song)] = pointer
                report.added += 1
        except SyncCancelled:
            report.cancelled = True        # keep what was copied; still save below
        finally:
            scratch.cleanup()

        # 5. Playlists: recreate the chosen ones from the library.
        present = {**kept, **added}
        for playlist in db.playlists():
            if not (db.is_master(playlist) or db.is_podcasts(playlist)):
                db.remove_playlist(playlist)
        if not settings.manual:
            # The Podcasts menu: the special playlist holds every podcast on the iPod.
            episodes = [pointer for dbid, pointer in present.items()
                        if library_by_dbid.get(dbid, {}).get("media_kind") == "podcast"]
            podcasts = db.podcasts_playlist(create=bool(episodes))
            if podcasts:
                for pointer in db.playlist_tracks(podcasts):
                    db.remove_from_playlist(podcasts, pointer)
                for pointer in episodes:
                    db.add_to_playlist(podcasts, pointer)
            for entry in the_plan.playlists:
                members = [present[d] for d in (dbid_of(t) for t in
                           library.load_tracks_by_ids(conn, entry["track_ids"])) if d in present]
                playlist = db.new_playlist(entry["name"])
                for pointer in members:
                    db.add_to_playlist(playlist, pointer)
                report.playlists += 1

        # 6. Name, then save (libgpod signs the database for 6G/7G).
        if record.pending_name:
            db.set_name(record.pending_name)
        tell("Updating the iPod's database…", can_stop=False)
        try:
            db.write()
        except gpod.GpodError as error:
            if ipod.identity.family.signature == models.HASH72:
                raise gpod.GpodError(
                    f"{error}. This iPod nano needs to have been synced with iTunes once, so "
                    "LuxTunes can reuse its signature.") from error
            raise
        name = db.name()

        # 6b. Now the removed songs' files, and any song file the database
        # doesn't list (left by a sync that was cut off, e.g. LuxTunes killed).
        for path in doomed_files:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as error:
                log.warning("couldn't delete %s from the iPod: %s", path, error)
        listed = [db.file_of(t) for t in db.tracks()]
        orphans = unlisted_song_files(ipod.mountpoint, listed)
        for path in orphans:
            try:
                os.remove(path)
            except OSError as error:
                log.warning("couldn't delete %s from the iPod: %s", path, error)
        if orphans:
            log.info("removed %d song files the iPod's database didn't list", len(orphans))

    # 7. Photos: one way, from the chosen folder (never back from the iPod).
    if not report.cancelled and ipod.supports_photos:
        _sync_photos(ipod, settings, report, progress)

    ipod_settings.mark_synced(conn, record.id, name)
    log.info("synced %s: +%d -%d ~%d, %d playlists, plays %s", name, report.added,
             report.removed, report.updated, report.playlists,
             report.stats.plays if report.stats else 0)
    return report
