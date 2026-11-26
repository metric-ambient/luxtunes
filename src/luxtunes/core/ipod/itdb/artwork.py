"""ArtworkDB (iPod_Control/Artwork/ArtworkDB) and its picture files
(F1055_1.ithmb...): the album artwork an iPod shows.

Each song with artwork points (mhit 0x160, mhii_link) at an image (mhii) in
ArtworkDB. An image lists one thumbnail per size the iPod uses, each a
stretch of raw RGB565 pixels in that size's .ithmb file. Songs with the same
picture share the pixels: each gets its own image record, pointing at the
same place in the .ithmb files.

Ported from ZenPod's artwork.rs (ishiichan-dayo, GPL-3.0-or-later), which
follows libgpod's db-artwork-*.c. Pictures are decoded and scaled with Qt's
QImage (Pillow when Qt isn't there, as in tests) and packed with numpy.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import numpy as np

from luxtunes.core.ipod.itdb.binary import Corrupt, rd16, rd32, rd64, wr16, wr32, wr64

log = logging.getLogger(__name__)

ITHMB_MAX = 256 * 1000 * 1000                  # a new .ithmb file after this many bytes
FIRST_IMAGE_ID = 0x64
MAX_THUMB = 2048


@dataclass(frozen=True)
class ArtFormat:
    id: int
    width: int
    height: int
    row_align: int = 0                          # rows padded to a multiple of this many bytes

    @property
    def row_bytes(self) -> int:
        b = self.width * 2
        if self.row_align > 1:
            return -(-b // self.row_align) * self.row_align
        return b

    @property
    def frame_size(self) -> int:
        return self.row_bytes * self.height


F = ArtFormat
# The cover sizes of each iPod family (format id, width, height), as libgpod lists them.
FAMILY_FORMATS = {
    "classic": (F(1061, 55, 55, 4), F(1055, 128, 128), F(1068, 128, 128), F(1060, 320, 320)),
    "nano_4": (F(1055, 128, 128), F(1068, 128, 128)),
    "video": (F(1028, 100, 100), F(1029, 200, 200)),
    "photo": (F(1017, 56, 56), F(1016, 140, 140)),
    "nano": (F(1031, 42, 42), F(1027, 100, 100)),
    "nano_5": (F(1056, 128, 128), F(1078, 80, 80), F(1073, 240, 240), F(1074, 50, 50)),
    "nano_6": (F(1073, 240, 240), F(1085, 88, 88), F(1089, 58, 58), F(1074, 50, 50)),
    "nano_7": (F(1010, 240, 240), F(1013, 50, 50), F(1015, 58, 58), F(1016, 57, 57, 4)),
}
# Other cover sizes iTunes writes (nano 4th generation), so they're kept when found.
OTHER_FORMATS = (F(1071, 240, 240), F(1084, 240, 240))
_FAMILY_KEYS = {
    "classic": "classic", "classic_1": "classic", "classic_2": "classic", "classic_3": "classic",
    "nano_3": "classic", "nano_4": "nano_4", "video": "video", "video_1": "video",
    "video_2": "video", "photo": "photo", "nano_1": "nano", "nano_2": "nano",
    "nano_5": "nano_5", "nano_6": "nano_6", "nano_7": "nano_7",
}
_ALL = {f.id: f for formats in (*FAMILY_FORMATS.values(), OTHER_FORMATS) for f in formats}


def formats_for(family_key: str) -> tuple[ArtFormat, ...]:
    """The cover sizes this iPod family shows (none for those without a screen
    for covers). An unknown iPod gets the classic's."""
    if family_key in ("unknown", ""):
        return FAMILY_FORMATS["classic"]
    key = _FAMILY_KEYS.get(family_key)
    return FAMILY_FORMATS[key] if key else ()


@dataclass
class Thumb:
    format_id: int
    file: str                                   # ":F1055_1.ithmb"
    offset: int
    size: int
    vpad: int = 0
    hpad: int = 0
    height: int = 0
    width: int = 0


@dataclass
class Image:
    hdr: bytearray
    id: int
    song_id: int
    orig_size: int
    thumbs: list[Thumb] = field(default_factory=list)
    extra: list[bytes] = field(default_factory=list)


def _new_mhii_header() -> bytearray:
    hdr = bytearray(0x98)
    hdr[0:4] = b"mhii"
    wr32(hdr, 4, 0x98)
    return hdr


class ArtworkDB:
    def __init__(self, hdr: bytearray | None = None):
        if hdr is None:
            hdr = bytearray(0x84)
            hdr[0:4] = b"mhfd"
            wr32(hdr, 4, 0x84)
            wr32(hdr, 0x10, 2)
            hdr[0x30] = 2
        self.hdr = hdr
        self.images: list[Image] = []
        self.albums: list[bytes] = []           # photo albums: kept as they were (mhba)
        self.files: list[tuple[int, int]] = []  # (format id, bytes per picture)

    def next_id(self) -> int:
        return max([FIRST_IMAGE_ID] + [i.id + 1 for i in self.images])

    def formats(self, device: tuple[ArtFormat, ...]) -> list[ArtFormat]:
        """The sizes to write: those already on the iPod (so pictures iTunes made
        stay usable), then any of the family's own it lacks."""
        out: list[ArtFormat] = []
        for format_id, size in self.files:
            if any(f.id == format_id for f in out):
                continue
            found = next((f for f in device if f.id == format_id), None)
            if found is None:
                known = _ALL.get(format_id)
                found = known if known and known.frame_size == size else None
            if found is None:
                found = self._format_from_thumbs(format_id, size)
            if found is None:
                log.warning("ArtworkDB: size %s (%d bytes) isn't known, so it's left alone",
                            format_id, size)
            else:
                out.append(found)
        for f in device:
            if not any(o.id == f.id for o in out):
                out.append(f)
        return out

    def _format_from_thumbs(self, format_id: int, size: int) -> ArtFormat | None:
        for image in self.images:
            for t in image.thumbs:
                w, h = t.width, t.height
                if t.format_id != format_id or not (1 <= w <= MAX_THUMB and 1 <= h <= MAX_THUMB):
                    continue
                if w * h * 2 == size:
                    return ArtFormat(format_id, w, h)
                row = size // h
                if size % h == 0 and w * 2 <= row < w * 2 + 16:
                    return ArtFormat(format_id, w, h, row)
        return None

    @classmethod
    def parse(cls, data: bytes) -> ArtworkDB:
        data = memoryview(bytes(data))
        if len(data) < 0x20 or bytes(data[0:4]) != b"mhfd":
            raise Corrupt("ArtworkDB: no mhfd header")
        hlen = rd32(data, 4)
        db = cls(bytearray(data[:min(hlen, len(data))]))
        if len(db.hdr) < 0x84:
            db.hdr.extend(bytes(0x84 - len(db.hdr)))
        pos = hlen
        for _ in range(rd32(data, 0x14)):
            section = _sub(data, pos, b"mhsd")
            if section is None:
                raise Corrupt("ArtworkDB: a section is damaged")
            body = section[rd32(section, 4):]
            kind = rd16(section, 0x0C)
            if kind == 1:
                db.images = _parse_mhli(body)
            elif kind == 2:
                db.albums = _parse_mhla(body)
            elif kind == 3:
                db.files = _parse_mhlf(body)
            pos += len(section)
        return db

    def serialize(self) -> bytes:
        mhli = bytearray(_list_header(b"mhli", len(self.images)))
        for image in self.images:
            mhli += _write_mhii(image)
        mhla = bytearray(_list_header(b"mhla", len(self.albums)))
        for album in self.albums:
            mhla += album
        mhlf = bytearray(_list_header(b"mhlf", len(self.files)))
        for format_id, size in self.files:
            h = bytearray(0x7C)
            h[0:4] = b"mhif"
            wr32(h, 4, 0x7C)
            wr32(h, 8, 0x7C)
            wr32(h, 0x10, format_id)
            wr32(h, 0x14, size)
            mhlf += h
        out = bytearray(self.hdr)
        wr32(out, 4, len(out))
        wr32(out, 0x10, 2)
        wr32(out, 0x14, 3)
        wr32(out, 0x1C, max(self.next_id() - 1, FIRST_IMAGE_ID - 1))
        for i, body in enumerate((mhli, mhla, mhlf)):
            h = bytearray(0x60)
            h[0:4] = b"mhsd"
            wr32(h, 4, 0x60)
            wr32(h, 8, 0x60 + len(body))
            wr16(h, 0x0C, i + 1)
            out += h + body
        wr32(out, 8, len(out))
        return bytes(out)


def _sub(data, pos: int, tag: bytes):
    if pos + 12 > len(data) or bytes(data[pos:pos + 4]) != tag:
        return None
    total = rd32(data, pos + 8)
    if total < 12 or pos + total > len(data):
        return None
    return data[pos:pos + total]


def _list_header(tag: bytes, count: int) -> bytes:
    h = bytearray(0x5C)
    h[0:4] = tag
    wr32(h, 4, 0x5C)
    wr32(h, 8, count)
    return bytes(h)


def _parse_mhli(body) -> list[Image]:
    if len(body) < 12 or bytes(body[0:4]) != b"mhli":
        raise Corrupt("ArtworkDB: the image list is damaged")
    pos = rd32(body, 4)
    out = []
    for _ in range(rd32(body, 8)):
        record = _sub(body, pos, b"mhii")
        if record is None:
            raise Corrupt("ArtworkDB: an image record is damaged")
        hlen = rd32(record, 4)
        hdr = bytearray(record[:hlen])
        if len(hdr) < 0x98:
            hdr.extend(bytes(0x98 - len(hdr)))
        image = Image(hdr, rd32(record, 0x10), rd64(record, 0x14), rd32(record, 0x30))
        at = hlen
        for _ in range(rd32(record, 0x0C)):
            od = _sub(record, at, b"mhod")
            if od is None:
                raise Corrupt("ArtworkDB: an image record is damaged")
            thumb = _parse_mhni(od[rd32(od, 4):]) if rd16(od, 0x0C) == 2 else None
            if thumb is not None:
                image.thumbs.append(thumb)
            else:
                image.extra.append(bytes(od))
            at += len(od)
        pos += len(record)
        out.append(image)
    return out


def _parse_mhni(b) -> Thumb | None:
    ni = _sub(b, 0, b"mhni")
    if ni is None:
        return None
    od = _sub(ni, rd32(ni, 4), b"mhod")
    if od is None:
        return None
    name = _mhod_string(od)
    if name is None:
        return None
    return Thumb(rd32(ni, 0x10), name, rd32(ni, 0x14), rd32(ni, 0x18), rd16(ni, 0x1C),
                 rd16(ni, 0x1E), rd16(ni, 0x20), rd16(ni, 0x22))


def _mhod_string(od) -> str | None:
    length = rd32(od, 0x18)
    raw = bytes(od[0x24:0x24 + length])
    if len(raw) < length:
        return None
    if od[0x1C] == 2:
        return raw[:len(raw) // 2 * 2].decode("utf-16-le", "replace")
    return raw.decode("utf-8", "replace")


def _parse_mhla(body) -> list[bytes]:
    if len(body) < 12 or bytes(body[0:4]) != b"mhla":
        return []
    pos = rd32(body, 4)
    out = []
    for _ in range(rd32(body, 8)):
        record = _sub(body, pos, b"mhba")
        if record is None:
            break
        out.append(bytes(record))
        pos += len(record)
    return out


def _parse_mhlf(body) -> list[tuple[int, int]]:
    if len(body) < 12 or bytes(body[0:4]) != b"mhlf":
        return []
    pos = rd32(body, 4)
    out = []
    for _ in range(rd32(body, 8)):
        record = _sub(body, pos, b"mhif")
        if record is None:
            break
        out.append((rd32(record, 0x10), rd32(record, 0x14)))
        pos += len(record)
    return out


def _string_mhod(kind: int, text: str, encoding: int) -> bytes:
    data = text.encode("utf-16-le") if encoding == 2 else text.encode("utf-8")
    pad = (4 - (0x24 + len(data)) % 4) % 4
    od = bytearray(0x24)
    od[0:4] = b"mhod"
    wr32(od, 4, 0x18)
    wr32(od, 8, 0x24 + len(data) + pad)
    wr16(od, 0x0C, kind)
    od[0x0F] = pad
    wr32(od, 0x18, len(data))
    od[0x1C] = encoding
    return bytes(od) + data + bytes(pad)


def _write_mhii(image: Image) -> bytes:
    out = bytearray(image.hdr)
    wr32(out, 4, len(out))
    wr32(out, 0x0C, len(image.thumbs) + len(image.extra))
    wr32(out, 0x10, image.id)
    wr64(out, 0x14, image.song_id)
    wr32(out, 0x30, image.orig_size)
    for t in image.thumbs:
        name = _string_mhod(3, t.file, 2)
        ni = bytearray(0x4C)
        ni[0:4] = b"mhni"
        wr32(ni, 4, 0x4C)
        wr32(ni, 8, 0x4C + len(name))
        wr32(ni, 0x0C, 1)
        wr32(ni, 0x10, t.format_id)
        wr32(ni, 0x14, t.offset)
        wr32(ni, 0x18, t.size)
        wr16(ni, 0x1C, t.vpad)
        wr16(ni, 0x1E, t.hpad)
        wr16(ni, 0x20, t.height)
        wr16(ni, 0x22, t.width)
        ni += name
        od = bytearray(0x18)
        od[0:4] = b"mhod"
        wr32(od, 4, 0x18)
        wr32(od, 8, 0x18 + len(ni))
        wr16(od, 0x0C, 2)
        out += od + ni
    for extra in image.extra:
        out += extra
    wr32(out, 8, len(out))
    return bytes(out)


# ---- Pictures -----------------------------------------------------------------------------

def decode(data: bytes):
    """A picture (JPEG, PNG...) as an RGB array (height, width, 3), or None."""
    try:
        from PySide6.QtGui import QImage
    except ImportError:
        QImage = None
    if QImage is not None:
        image = QImage.fromData(data)
        if image.isNull():
            return None
        image = image.convertToFormat(QImage.Format.Format_RGB888)
        w, h, stride = image.width(), image.height(), image.bytesPerLine()
        raw = np.frombuffer(bytes(image.constBits()), dtype=np.uint8)
        return raw.reshape(h, stride)[:, :w * 3].reshape(h, w, 3).copy()
    try:
        import io

        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(data)) as picture:
            return np.asarray(picture.convert("RGB")).copy()
    except Exception:
        return None


def _scale(rgb, width: int, height: int):
    """rgb resized to width × height, smoothly (area averaging when shrinking)."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
    except ImportError:
        QImage = None
    h, w = rgb.shape[:2]
    if QImage is not None:
        source = np.ascontiguousarray(rgb)
        image = QImage(source.data, w, h, w * 3, QImage.Format.Format_RGB888)
        scaled = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        scaled = scaled.convertToFormat(QImage.Format.Format_RGB888)
        raw = np.frombuffer(bytes(scaled.constBits()), dtype=np.uint8)
        return raw.reshape(height, scaled.bytesPerLine())[:, :width * 3] \
            .reshape(height, width, 3).copy()
    from PIL import Image as PILImage
    return np.asarray(PILImage.fromarray(rgb).resize((width, height),
                                                     PILImage.Resampling.LANCZOS))


def pack(rgb, fmt: ArtFormat) -> bytes:
    """The picture cut to the format's shape (from the middle), scaled to fit and
    packed as RGB565, little-endian, rows padded as the format wants."""
    h, w = rgb.shape[:2]
    ratio = fmt.width / fmt.height
    if w / h > ratio:
        cw, ch = max(1, round(h * ratio)), h
    else:
        cw, ch = w, max(1, round(w / ratio))
    x, y = (w - cw) // 2, (h - ch) // 2
    picture = _scale(rgb[y:y + ch, x:x + cw], fmt.width, fmt.height).astype(np.uint16)
    value = ((picture[..., 0] >> 3) << 11) | ((picture[..., 1] >> 2) << 5) | (picture[..., 2] >> 3)
    rows = value.astype("<u2").tobytes()
    if fmt.row_bytes == fmt.width * 2:
        return rows
    pad = bytes(fmt.row_bytes - fmt.width * 2)
    line = fmt.width * 2
    return b"".join(rows[i * line:(i + 1) * line] + pad for i in range(fmt.height))


def unpack(data: bytes, fmt: ArtFormat):
    """RGB565 pixels back to an RGB array (for checking what was written)."""
    rows = np.frombuffer(data, dtype="<u2").reshape(fmt.height, fmt.row_bytes // 2)
    v = rows[:, :fmt.width].astype(np.uint32)
    r, g, b = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)],
                    axis=-1).astype(np.uint8)


class IthmbFiles:
    """Adds pictures to the end of each size's .ithmb file, starting a new one
    after 256 MB, as iTunes does. Nothing already in them is changed."""

    def __init__(self, folder: str):
        self.folder = folder
        self.written: list[str] = []            # files added to, to undo should saving fail

    def _name(self, format_id: int) -> tuple[str, int]:
        index = 1
        while os.path.exists(os.path.join(self.folder, f"F{format_id}_{index + 1}.ithmb")):
            index += 1
        path = os.path.join(self.folder, f"F{format_id}_{index}.ithmb")
        size = os.path.getsize(path) if os.path.exists(path) else 0
        return path, index if size + 1 <= ITHMB_MAX else index + 1

    def append(self, format_id: int, pixels: bytes) -> tuple[str, int]:
        os.makedirs(self.folder, exist_ok=True)
        path, index = self._name(format_id)
        if os.path.exists(path) and os.path.getsize(path) + len(pixels) > ITHMB_MAX:
            index += 1
        path = os.path.join(self.folder, f"F{format_id}_{index}.ithmb")
        with open(path, "ab") as f:
            offset = f.tell()
            f.write(pixels)
        if path not in self.written:
            self.written.append(path)
        return f":F{format_id}_{index}.ithmb", offset

    def flush(self) -> None:
        for path in self.written:
            try:
                fd = os.open(path, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as error:
                log.warning("couldn't flush %s: %s", path, error)


def thumb_path(folder: str, name: str) -> str | None:
    """Where a thumbnail's file is (":F1055_1.ithmb" -> folder/F1055_1.ithmb),
    or None if the name points outside the Artwork folder."""
    parts = [p for p in name.replace("\\", ":").replace("/", ":").split(":") if p]
    if not parts or any(p in (".", "..") for p in parts):
        return None
    return os.path.join(folder, *parts)


def read_thumb(folder: str, thumb: Thumb) -> bytes | None:
    path = thumb_path(folder, thumb.file)
    if path is None or thumb.size > MAX_THUMB * MAX_THUMB * 2:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(thumb.offset)
            data = f.read(thumb.size)
    except OSError:
        return None
    return data if len(data) == thumb.size else None


def build_image(ithmb: IthmbFiles, data: bytes, formats, image_id: int,
                song_id: int) -> Image | None:
    """A new image for this picture, its thumbnails added to the .ithmb files.
    None if the picture can't be read."""
    rgb = decode(data)
    if rgb is None or rgb.size == 0:
        return None
    if max(rgb.shape[:2]) > 1000:              # big covers: shrink once, then each size
        h, w = rgb.shape[:2]
        scale = 1000 / max(h, w)
        rgb = _scale(rgb, max(1, round(w * scale)), max(1, round(h * scale)))
    thumbs = []
    for fmt in formats:
        pixels = pack(rgb, fmt)
        name, offset = ithmb.append(fmt.id, pixels)
        thumbs.append(Thumb(fmt.id, name, offset, len(pixels), 0, 0, fmt.height, fmt.width))
    return Image(_new_mhii_header(), image_id, song_id, len(data), thumbs)
