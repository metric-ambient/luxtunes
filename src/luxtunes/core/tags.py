"""Reading and writing the tags inside music files (ID3 tags and their equivalents).

LuxTunes' library is the source of truth; this module copies edits into the
files so other players (and iPods) see them too. Rules:

  * Only the fields you changed are written. Anything else already in the
    file is left exactly as it was.
  * MP3s get ID3 v2.3 tags, the version every iPod reads.
  * Lyrics and artwork are only touched when you edit them.

Supported: MP3 (ID3), M4A/AAC/ALAC (MP4 atoms), FLAC and Ogg (Vorbis
comments), WAV/AIFF (ID3 chunk).
"""

from __future__ import annotations

import os

# Library fields that live in file tags (the rest — plays, ratings, dates —
# live only in the library).
TAG_FIELDS = (
    "name", "artist", "album_artist", "album", "composer", "genre", "grouping",
    "comments", "year", "track_number", "track_count", "disc_number", "disc_count",
    "bpm", "compilation", "sort_name", "sort_artist", "sort_album_artist",
    "sort_album", "sort_composer",
)
UNCHANGED = object()   # "leave lyrics / artwork as they are"


class TagError(Exception):
    """The file couldn't be read or written (missing, read-only, unsupported...)."""


# ---- Field names in each tag format ------------------------------------------------

ID3_TEXT = {
    "name": "TIT2", "artist": "TPE1", "album_artist": "TPE2", "album": "TALB",
    "composer": "TCOM", "genre": "TCON", "grouping": "TIT1",
    "sort_name": "TSOT", "sort_artist": "TSOP", "sort_album_artist": "TSO2",
    "sort_album": "TSOA", "sort_composer": "TSOC",
}
MP4_TEXT = {
    "name": "\xa9nam", "artist": "\xa9ART", "album_artist": "aART", "album": "\xa9alb",
    "composer": "\xa9wrt", "genre": "\xa9gen", "grouping": "\xa9grp", "comments": "\xa9cmt",
    "sort_name": "sonm", "sort_artist": "soar", "sort_album_artist": "soaa",
    "sort_album": "soal", "sort_composer": "soco",
}
VORBIS_TEXT = {
    "name": "TITLE", "artist": "ARTIST", "album_artist": "ALBUMARTIST", "album": "ALBUM",
    "composer": "COMPOSER", "genre": "GENRE", "grouping": "GROUPING", "comments": "COMMENT",
    "sort_name": "TITLESORT", "sort_artist": "ARTISTSORT", "sort_album_artist": "ALBUMARTISTSORT",
    "sort_album": "ALBUMSORT", "sort_composer": "COMPOSERSORT", "year": "DATE",
    "track_number": "TRACKNUMBER", "track_count": "TRACKTOTAL",
    "disc_number": "DISCNUMBER", "disc_count": "DISCTOTAL", "bpm": "BPM",
}


def _image_mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def _pair(number, count) -> str | None:
    """Track/disc numbers as written in ID3: '3/12', '3' or nothing."""
    if not number and not count:
        return None
    return f"{number or 0}/{count}" if count else str(number)


def _open(path: str):
    if not path or not os.path.exists(path):
        raise TagError("file not found")
    import mutagen
    try:
        audio = mutagen.File(path)
    except Exception as error:   # mutagen raises many kinds of errors
        raise TagError(f"can't read the file: {error}") from error
    if audio is None:
        raise TagError("not a supported audio file")
    return audio


# ---- Reading ---------------------------------------------------------------------------

def read_details(path: str) -> dict:
    """What Get Info shows from the file itself: format details, lyrics and artwork.

    Returns {"format", "channels", "tag_version", "encoder", "lyrics", "artworks"},
    where artworks is a list of image bytes (front cover first).
    """
    audio = _open(path)
    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4

    details = {"format": "", "channels": "", "tag_version": "", "encoder": "",
               "lyrics": "", "artworks": []}
    info = audio.info
    channels = getattr(info, "channels", 0)
    details["channels"] = {1: "Mono", 2: "Stereo"}.get(channels, f"{channels} channels" if channels else "")

    tags = audio.tags
    if isinstance(audio, MP3):
        version = {1: "1", 2: "2", 2.5: "2.5"}.get(info.version, str(info.version))
        details["format"] = f"MPEG-{version}, Layer {info.layer}"
        modes = {0: "Stereo", 1: "Joint Stereo", 2: "Dual Channel", 3: "Mono"}
        details["channels"] = modes.get(getattr(info, "mode", None), details["channels"])
        details["encoder"] = getattr(info, "encoder_info", "") or ""
    elif isinstance(audio, MP4):
        details["format"] = getattr(info, "codec_description", "") or "MPEG-4 audio"
        if tags and "\xa9too" in tags:
            details["encoder"] = str(tags["\xa9too"][0])
        if tags:
            lyrics = tags.get("\xa9lyr")
            details["lyrics"] = str(lyrics[0]) if lyrics else ""
            details["artworks"] = [bytes(c) for c in tags.get("covr", [])]
        return details
    else:
        details["format"] = type(audio).__name__.replace("OggVorbis", "Ogg Vorbis")

    if isinstance(tags, ID3):
        details["tag_version"] = f"v{tags.version[0]}.{tags.version[1]}"
        encoder = tags.get("TSSE")
        if encoder and encoder.text:
            details["encoder"] = str(encoder.text[0])
        lyrics = tags.getall("USLT")
        details["lyrics"] = lyrics[0].text if lyrics else ""
        pictures = tags.getall("APIC")
        pictures.sort(key=lambda p: 0 if p.type == 3 else 1)    # front cover first
        details["artworks"] = [p.data for p in pictures]
    elif tags is not None:      # FLAC / Ogg: Vorbis comments
        details["tag_version"] = "Vorbis comments"
        for key in ("LYRICS", "UNSYNCEDLYRICS"):
            if tags.get(key):
                details["lyrics"] = tags[key][0]
                break
        details["artworks"] = [p.data for p in getattr(audio, "pictures", [])]
    return details


# ---- Writing ---------------------------------------------------------------------------

def write_tags(path: str, fields: dict, lyrics=UNCHANGED, artworks=UNCHANGED) -> None:
    """Write `fields` (library keys -> values; None/"" deletes that tag) into the file.

    Track and disc numbers are written as pairs, so pass both halves together
    (e.g. track_number and track_count). Raises TagError on failure; the file
    is left untouched in that case.
    """
    unknown = set(fields) - set(TAG_FIELDS)
    if unknown:
        raise ValueError(f"not tag fields: {sorted(unknown)}")
    audio = _open(path)
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4

    try:
        if isinstance(audio, MP3):
            _write_id3_file(path, fields, lyrics, artworks)
        elif isinstance(audio, MP4):
            _write_mp4(audio, fields, lyrics, artworks)
        elif hasattr(audio, "tags") and type(audio).__name__ in ("WAVE", "AIFF"):
            if audio.tags is None:
                audio.add_tags()
            _fill_id3(audio.tags, fields, lyrics, artworks)
            _convert_to_v23(audio.tags)
            audio.save(v2_version=3)
        elif isinstance(audio, FLAC) or type(audio).__name__.startswith("Ogg"):
            _write_vorbis(audio, fields, lyrics, artworks)
        else:
            raise TagError(f"writing tags to {type(audio).__name__} files isn't supported yet")
    except TagError:
        raise
    except PermissionError as error:
        raise TagError("the file is read-only") from error
    except Exception as error:
        raise TagError(f"couldn't write the tags: {error}") from error


def _write_id3_file(path, fields, lyrics, artworks):
    from mutagen.id3 import ID3, ID3NoHeaderError
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    _fill_id3(tags, fields, lyrics, artworks)
    _convert_to_v23(tags)
    tags.save(path, v2_version=3)       # iPod-compatible


# Sort frames that are officially v2.4-only, but are commonly written into v2.3
# tags anyway (and iPods read). mutagen's v2.3 conversion would delete them.
V23_SORT_FRAMES = ("TSOA", "TSOP", "TSOT")


def _convert_to_v23(tags):
    """Turn v2.4 frames into their v2.3 forms (e.g. year TDRC -> TYER) so iPods
    and older players read them, keeping the sort frames."""
    keep = [frame for frame_id in V23_SORT_FRAMES for frame in tags.getall(frame_id)]
    tags.update_to_v23()
    for frame in keep:
        tags.add(frame)


def _fill_id3(tags, fields, lyrics, artworks):
    from mutagen.id3 import APIC, COMM, TBPM, TCMP, TDRC, TPOS, TRCK, USLT, Frames

    def put(frame_id, frame):
        tags.delall(frame_id)
        if frame is not None:
            tags.add(frame)

    for key, frame_id in ID3_TEXT.items():
        if key in fields:
            value = fields[key]
            put(frame_id, Frames[frame_id](encoding=3, text=[str(value)]) if value not in (None, "") else None)
    if "year" in fields:
        tags.delall("TYER")
        put("TDRC", TDRC(encoding=3, text=[str(fields["year"])]) if fields["year"] else None)
    if "track_number" in fields or "track_count" in fields:
        text = _pair(fields.get("track_number"), fields.get("track_count"))
        put("TRCK", TRCK(encoding=3, text=[text]) if text else None)
    if "disc_number" in fields or "disc_count" in fields:
        text = _pair(fields.get("disc_number"), fields.get("disc_count"))
        put("TPOS", TPOS(encoding=3, text=[text]) if text else None)
    if "bpm" in fields:
        put("TBPM", TBPM(encoding=3, text=[str(fields["bpm"])]) if fields["bpm"] else None)
    if "compilation" in fields:
        put("TCMP", TCMP(encoding=3, text=["1"]) if fields["compilation"] else None)
    if "comments" in fields:
        # Replace only the plain comment; other comment frames hold player data (iTunNORM,
        # iTunSMPB, ...) in COMM frames with a description, and those stay.
        for key in [k for k, f in tags.items() if k.startswith("COMM") and not f.desc]:
            del tags[key]
        if fields["comments"]:
            tags.add(COMM(encoding=3, lang="eng", desc="", text=[fields["comments"]]))
    if lyrics is not UNCHANGED:
        tags.delall("USLT")
        if lyrics:
            tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
    if artworks is not UNCHANGED:
        tags.delall("APIC")
        for i, data in enumerate(artworks):
            # Each picture needs its own description to be kept separately.
            tags.add(APIC(encoding=3, mime=_image_mime(data), type=3 if i == 0 else 0,
                          desc="" if i == 0 else f"Artwork {i + 1}", data=data))


def _write_mp4(audio, fields, lyrics, artworks):
    from mutagen.mp4 import MP4Cover
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    def put(atom, value):
        if value in (None, "", [], False):
            tags.pop(atom, None)
        else:
            tags[atom] = value

    for key, atom in MP4_TEXT.items():
        if key in fields:
            value = fields[key]
            put(atom, [str(value)] if value not in (None, "") else None)
            if key == "genre":
                tags.pop("gnre", None)      # old numeric genre would override the text one
    if "year" in fields:
        put("\xa9day", [str(fields["year"])] if fields["year"] else None)
    if "track_number" in fields or "track_count" in fields:
        n, m = fields.get("track_number") or 0, fields.get("track_count") or 0
        put("trkn", [(n, m)] if n or m else None)
    if "disc_number" in fields or "disc_count" in fields:
        n, m = fields.get("disc_number") or 0, fields.get("disc_count") or 0
        put("disk", [(n, m)] if n or m else None)
    if "bpm" in fields:
        put("tmpo", [int(fields["bpm"])] if fields["bpm"] else None)
    if "compilation" in fields:
        put("cpil", True if fields["compilation"] else None)
    if lyrics is not UNCHANGED:
        put("\xa9lyr", [lyrics] if lyrics else None)
    if artworks is not UNCHANGED:
        covers = [MP4Cover(d, imageformat=MP4Cover.FORMAT_PNG if _image_mime(d) == "image/png"
                           else MP4Cover.FORMAT_JPEG) for d in artworks]
        put("covr", covers or None)
    audio.save()


def _write_vorbis(audio, fields, lyrics, artworks):
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    def remove(name):   # Vorbis comments have no pop(name, default)
        if name in tags:
            del tags[name]

    for key, name in VORBIS_TEXT.items():
        if key in fields:
            value = fields[key]
            if value in (None, "", 0):
                remove(name)
            else:
                tags[name] = [str(value)]
    if "compilation" in fields:
        if fields["compilation"]:
            tags["COMPILATION"] = ["1"]
        else:
            remove("COMPILATION")
    if lyrics is not UNCHANGED:
        remove("UNSYNCEDLYRICS")
        if lyrics:
            tags["LYRICS"] = [lyrics]
        else:
            remove("LYRICS")
    if artworks is not UNCHANGED and hasattr(audio, "clear_pictures"):
        from mutagen.flac import Picture
        audio.clear_pictures()
        for i, data in enumerate(artworks):
            picture = Picture()
            picture.data = data
            picture.mime = _image_mime(data)
            picture.type = 3 if i == 0 else 0
            audio.add_picture(picture)
    audio.save()


# ---- Reading everything (adding new files to the library) --------------------------

KINDS = {
    "MP3": "MPEG audio file", "FLAC": "FLAC audio file", "WAVE": "WAV audio file",
    "AIFF": "AIFF audio file", "OggVorbis": "Ogg Vorbis audio file", "OggOpus": "Opus audio file",
}


def _first(value):
    """The first item of a tag value, as text ('' if missing)."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(getattr(value, "text", [value])[0] if hasattr(value, "text") else value).strip()


def _split_pair(text: str) -> tuple[int | None, int | None]:
    """'3/12' -> (3, 12); '3' -> (3, None); '' -> (None, None)."""
    def number(part):
        part = part.strip()
        return int(part) if part.isdigit() and int(part) > 0 else None
    first, _, second = str(text).partition("/")
    return number(first), number(second)


def _year(text: str) -> int | None:
    text = str(text).strip()[:4]
    return int(text) if text.isdigit() else None


# The media type atom ("stik") in MP4 files: what the iTunes Store marked it as.
MP4_MEDIA_TYPES = {2: "audiobook", 21: "podcast"}


def media_kind_hint(path: str) -> str | None:
    """"podcast" or "audiobook" if the file says so (an .m4b, an MP4 media type,
    or a podcast flag in MP4 or ID3 tags); otherwise None (plain music)."""
    from luxtunes.core.library import AUDIOBOOK_EXTENSIONS
    if (path or "").lower().endswith(AUDIOBOOK_EXTENSIONS):
        return "audiobook"
    try:
        audio = _open(path)
    except TagError:
        return None
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
    tags = audio.tags
    if isinstance(audio, MP4) and tags:
        stik = tags.get("stik")
        kind = MP4_MEDIA_TYPES.get(int(stik[0])) if stik else None
        if kind:
            return kind
        if tags.get("pcst"):
            return "podcast"
    elif isinstance(tags, ID3) and tags.getall("PCST"):
        return "podcast"
    return None


def read_fields(path: str) -> dict:
    """Everything LuxTunes stores about a new file: its tags (as library
    fields), lyrics, artwork count and the audio details. Raises TagError."""
    audio = _open(path)
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4

    info = audio.info
    fields: dict = {
        "total_time": int(round(getattr(info, "length", 0) * 1000)) or None,
        "bit_rate": int(getattr(info, "bitrate", 0) or 0) // 1000 or None,
        "sample_rate": getattr(info, "sample_rate", None),
        "size": os.path.getsize(path),
        "kind": KINDS.get(type(audio).__name__, f"{type(audio).__name__} audio file"),
    }
    tags = audio.tags
    lyrics, artworks = "", 0

    if isinstance(audio, MP4):
        codec = (getattr(info, "codec", "") or "").lower()
        fields["kind"] = "Apple Lossless audio file" if codec == "alac" else "AAC audio file"
        if path.lower().endswith((".m4v", ".mp4", ".mov")):
            fields["kind"] = "MPEG-4 video file"
        tags = tags or {}
        for key, atom in MP4_TEXT.items():
            if atom in tags:
                fields[key] = _first(tags[atom])
        fields["year"] = _year(_first(tags.get("\xa9day")))
        for key, atom in (("track", "trkn"), ("disc", "disk")):
            if tags.get(atom):
                number, count = tags[atom][0]
                fields[f"{key}_number"], fields[f"{key}_count"] = number or None, count or None
        if tags.get("tmpo"):
            fields["bpm"] = int(tags["tmpo"][0]) or None
        fields["compilation"] = int(bool(tags.get("cpil")))
        lyrics = _first(tags.get("\xa9lyr"))
        artworks = len(tags.get("covr", []))
    elif isinstance(tags, ID3):
        for key, frame_id in ID3_TEXT.items():
            if frame_id in tags:
                fields[key] = _first(tags[frame_id])
        year = tags.get("TDRC") or tags.get("TYER")
        fields["year"] = _year(_first(year))
        if "TRCK" in tags:
            fields["track_number"], fields["track_count"] = _split_pair(_first(tags["TRCK"]))
        if "TPOS" in tags:
            fields["disc_number"], fields["disc_count"] = _split_pair(_first(tags["TPOS"]))
        if "TBPM" in tags:
            bpm = _first(tags["TBPM"]).split(".")[0]
            fields["bpm"] = int(bpm) if bpm.isdigit() else None
        fields["compilation"] = int(_first(tags.get("TCMP")) == "1")
        comments = [c for c in tags.getall("COMM") if not c.desc]
        if comments:
            fields["comments"] = _first(comments[0])
        uslt = tags.getall("USLT")
        lyrics = uslt[0].text if uslt else ""
        artworks = len(tags.getall("APIC"))
    elif tags is not None:      # Vorbis comments
        for key, name in VORBIS_TEXT.items():
            if tags.get(name):
                fields[key] = _first(tags[name])
        fields["year"] = _year(fields.get("year") or "")
        for key in ("track", "disc"):
            number, count = _split_pair(fields.pop(f"{key}_number", "") or "")
            total = _split_pair(fields.pop(f"{key}_count", "") or "")[0]
            fields[f"{key}_number"], fields[f"{key}_count"] = number, count or total
        bpm = str(fields.pop("bpm", "") or "").split(".")[0]
        fields["bpm"] = int(bpm) if bpm.isdigit() else None
        fields["compilation"] = int(_first(tags.get("COMPILATION")) == "1")
        lyrics = _first(tags.get("LYRICS") or tags.get("UNSYNCEDLYRICS"))
        artworks = len(getattr(audio, "pictures", []))

    for key in ("track_number", "track_count", "disc_number", "disc_count", "bpm"):
        if fields.get(key) in (0, ""):
            fields[key] = None
    for key in list(fields):
        if fields[key] == "":
            fields[key] = None
    fields["lyrics"] = lyrics or None
    fields["artwork_count"] = artworks
    return fields
