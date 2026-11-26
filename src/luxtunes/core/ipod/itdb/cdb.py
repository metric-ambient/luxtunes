"""iTunesCDB: the song list of the iPod nano 5th–7th generation, an iTunesDB
whose body (after the header) is zlib-compressed; header byte 0xA8 says so.

Ported from ZenPod's cdb.rs (ishiichan-dayo, GPL-3.0-or-later).
"""

from __future__ import annotations

import zlib

from luxtunes.core.ipod.itdb.binary import Corrupt, rd32, wr32

MIN_HEADER = 0xA9
MAX_INFLATED = 1 << 30


def _header_len(data: bytes) -> int:
    if len(data) < 12 or data[0:4] != b"mhbd":
        raise Corrupt("this isn't an iTunesCDB (no mhbd header)")
    hlen = rd32(data, 4)
    if not MIN_HEADER <= hlen <= len(data):
        raise Corrupt("the iTunesCDB header has a bad length")
    return hlen


def is_compressed(data: bytes) -> bool:
    return len(data) >= MIN_HEADER and data[0:4] == b"mhbd" and rd32(data, 4) >= MIN_HEADER \
        and data[0xA8] == 1


def decompress(data: bytes) -> bytes:
    hlen = _header_len(data)
    end = max(hlen, min(rd32(data, 8), len(data)))
    inflater = zlib.decompressobj()
    try:
        body = inflater.decompress(data[hlen:end], MAX_INFLATED)
    except zlib.error as error:
        raise Corrupt(f"the iTunesCDB couldn't be unpacked: {error}") from error
    if inflater.unconsumed_tail:
        raise Corrupt("the iTunesCDB unpacks too big")
    out = bytearray(data[:hlen]) + body
    out[0xA8] = 0
    wr32(out, 8, len(out))
    return bytes(out)


def compress(db: bytes) -> bytes:
    hlen = _header_len(db)
    end = max(hlen, min(rd32(db, 8), len(db)))
    out = bytearray(db[:hlen]) + zlib.compress(db[hlen:end], 1)
    out[0xA8] = 1
    wr32(out, 8, len(out))
    return bytes(out)
