"""Little-endian numbers in an iPod database record. Reading past the end
gives 0 (as in ZenPod), so a short record from an old iTunes reads as zeros."""

from __future__ import annotations

import struct

MAC_EPOCH_OFFSET = 2_082_844_800          # seconds from 1904-01-01 to 1970-01-01


def rd8(b, o: int) -> int:
    return b[o] if o < len(b) else 0


def rd16(b, o: int) -> int:
    return int.from_bytes(b[o:o + 2], "little") if o + 2 <= len(b) else 0


def rd32(b, o: int) -> int:
    return int.from_bytes(b[o:o + 4], "little") if o + 4 <= len(b) else 0


def rd64(b, o: int) -> int:
    return int.from_bytes(b[o:o + 8], "little") if o + 8 <= len(b) else 0


def wr8(b: bytearray, o: int, v: int) -> None:
    b[o] = v & 0xFF


def wr16(b: bytearray, o: int, v: int) -> None:
    b[o:o + 2] = (v & 0xFFFF).to_bytes(2, "little")


def wr32(b: bytearray, o: int, v: int) -> None:
    b[o:o + 4] = (v & 0xFFFFFFFF).to_bytes(4, "little")


def wr64(b: bytearray, o: int, v: int) -> None:
    b[o:o + 8] = (v & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")


def rdf(b, o: int) -> float:
    return struct.unpack_from("<f", b, o)[0] if o + 4 <= len(b) else 0.0


def wrf(b: bytearray, o: int, v: float) -> None:
    struct.pack_into("<f", b, o, float(v))


def unix_to_mac(t: int) -> int:
    return 0 if not t or t <= 0 else (int(t) + MAC_EPOCH_OFFSET) & 0xFFFFFFFF


def mac_to_unix(t: int) -> int:
    return 0 if not t else int(t) - MAC_EPOCH_OFFSET


class Corrupt(ValueError):
    """The file isn't an iPod database we can read (cut short or damaged)."""


def chunk(data, pos: int, tag: bytes):
    """The record with this tag at pos (header and children), checked to fit."""
    if pos + 12 > len(data):
        raise Corrupt(f"{tag.decode()} is cut short")
    if bytes(data[pos:pos + 4]) != tag:
        got = bytes(data[pos:pos + 4])
        raise Corrupt(f"expected {tag.decode()} but found {got!r} at {pos}")
    total = rd32(data, pos + 8)
    if total < 12 or pos + total > len(data):
        raise Corrupt(f"{tag.decode()} has a bad length")
    return data[pos:pos + total]
