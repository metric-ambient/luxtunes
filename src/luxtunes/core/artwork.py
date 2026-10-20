"""Read album art embedded in audio files."""

from __future__ import annotations


def read_embedded_artwork(path: str | None) -> bytes | None:
    """Return the first embedded picture in the file (JPEG/PNG bytes), or None.

    Handles MP3 (ID3 "APIC" frames), M4A/AAC ("covr" atoms) and FLAC.
    Never raises: a missing or unreadable file simply has no artwork.
    """
    if not path:
        return None
    try:
        import mutagen

        audio = mutagen.File(path)
        if audio is None:
            return None
        pictures = getattr(audio, "pictures", None)       # FLAC
        if pictures:
            return pictures[0].data
        tags = audio.tags
        if tags is None:
            return None
        if hasattr(tags, "getall"):                         # MP3 / ID3
            frames = tags.getall("APIC")
            if frames:
                # Prefer the front cover (type 3) if there are several.
                front = [f for f in frames if getattr(f, "type", None) == 3]
                return (front or frames)[0].data
        covers = tags.get("covr") if hasattr(tags, "get") else None  # M4A
        if covers:
            return bytes(covers[0])
    except Exception:
        return None
    return None
