"""hash72: the signature the iPod nano 5th generation checks on its song
list. It needs a secret iTunes leaves on the nano (HashInfo), which LuxTunes
recovers from a song list iTunes signed: so the nano must have been synced
with iTunes once.

Ported from ZenPod's hash72.rs (ishiichan-dayo, GPL-3.0-or-later), which
follows libgpod's itdb_hash72.c. AES-128 (FIPS-197) is written out below, so
nothing needs installing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

AES_KEY = bytes((0x61, 0x8C, 0xA1, 0x0D, 0xC7, 0xF5, 0x7F, 0xD3, 0xB4, 0x72, 0x3E, 0x08, 0x15,
                 0x74, 0x63, 0xD7))
HASH72_AT = 0x72
HASH72_LEN = 46
HASH_INFO_LEN = 54


# ---- AES-128 --------------------------------------------------------------------------------

def _sbox() -> tuple[bytes, bytes]:
    box, inverse = bytearray(256), bytearray(256)
    p = q = 1
    while True:                        # p runs over the field, q over its inverses
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1 | q >> 7) & 0xFF) ^ ((q << 2 | q >> 6) & 0xFF) \
            ^ ((q << 3 | q >> 5) & 0xFF) ^ ((q << 4 | q >> 4) & 0xFF) ^ 0x63
        box[p] = x
        inverse[x] = p
        if p == 1:
            break
    box[0] = 0x63
    inverse[0x63] = 0
    return bytes(box), bytes(inverse)


SBOX, INV_SBOX = _sbox()


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _mul(a: int, b: int) -> int:
    out = 0
    while b:
        if b & 1:
            out ^= a
        a = _xtime(a)
        b >>= 1
    return out


def _round_keys(key: bytes) -> list[bytes]:
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        w = list(words[i - 1])
        if i % 4 == 0:
            w = [SBOX[b] for b in w[1:] + w[:1]]
            w[0] ^= rcon
            rcon = _xtime(rcon)
        words.append([a ^ b for a, b in zip(words[i - 4], w)])
    return [bytes(sum(words[r * 4:r * 4 + 4], [])) for r in range(11)]


def _shift(s: list[int], inverse: bool) -> list[int]:
    out = [0] * 16
    for col in range(4):
        for row in range(4):
            source = (col + row) % 4 if not inverse else (col - row) % 4
            out[col * 4 + row] = s[source * 4 + row]
    return out


def _mix(s: list[int], factors) -> list[int]:
    out = []
    for col in range(4):
        c = s[col * 4:col * 4 + 4]
        for row in range(4):
            out.append(_mul(c[0], factors[(0 - row) % 4]) ^ _mul(c[1], factors[(1 - row) % 4])
                       ^ _mul(c[2], factors[(2 - row) % 4]) ^ _mul(c[3], factors[(3 - row) % 4]))
    return out


def aes_encrypt(key: bytes, block: bytes) -> bytes:
    keys = _round_keys(key)
    s = [a ^ b for a, b in zip(block, keys[0])]
    for r in range(1, 11):
        s = _shift([SBOX[b] for b in s], False)
        if r < 10:
            s = _mix(s, (2, 3, 1, 1))
        s = [a ^ b for a, b in zip(s, keys[r])]
    return bytes(s)


def aes_decrypt(key: bytes, block: bytes) -> bytes:
    keys = _round_keys(key)
    s = [a ^ b for a, b in zip(block, keys[10])]
    for r in range(9, -1, -1):
        s = [INV_SBOX[b] for b in _shift(s, True)]
        s = [a ^ b for a, b in zip(s, keys[r])]
        if r > 0:
            s = _mix(s, (14, 11, 13, 9))
    return bytes(s)


# ---- hash72 ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class HashInfo:
    uuid: bytes                        # 20 bytes: the FireWire GUID, then zeros
    rnd: bytes                         # 12
    iv: bytes                          # 16

    @classmethod
    def new(cls, guid: bytes, rnd: bytes, iv: bytes) -> HashInfo:
        return cls((guid + bytes(20))[:20], rnd, iv)

    @classmethod
    def parse(cls, data: bytes, guid: bytes) -> HashInfo | None:
        """The HashInfo file iTunes (or LuxTunes) left in iPod_Control/Device."""
        if len(data) != HASH_INFO_LEN or data[:6] != b"HASHv0":
            return None
        info = cls(bytes(data[6:26]), bytes(data[26:38]), bytes(data[38:54]))
        return info if info.uuid[:8] == guid and not any(info.uuid[8:]) else None

    def to_bytes(self) -> bytes:
        return b"HASHv0" + self.uuid + self.rnd + self.iv

    def sign(self, sha1: bytes) -> bytes:
        plain = sha1 + self.rnd
        previous, out = self.iv, bytearray(b"\x01\x00" + self.rnd)
        for i in (0, 16):
            previous = aes_encrypt(AES_KEY, bytes(a ^ b for a, b in zip(plain[i:i + 16],
                                                                         previous)))
            out += previous
        return bytes(out)

    @classmethod
    def extract(cls, signature: bytes, sha1: bytes, guid: bytes) -> HashInfo | None:
        """Recover the secret from a signature iTunes made."""
        if len(signature) != HASH72_LEN or signature[:2] != b"\x01\x00":
            return None
        rnd = bytes(signature[2:14])
        plain = sha1 + rnd
        c1, c2 = bytes(signature[14:30]), bytes(signature[30:46])
        if bytes(a ^ b for a, b in zip(aes_decrypt(AES_KEY, c2), c1)) != plain[16:]:
            return None
        iv = bytes(a ^ b for a, b in zip(aes_decrypt(AES_KEY, c1), plain[:16]))
        return cls.new(guid, rnd, iv)


def db_sha1(db: bytes) -> bytes:
    h = hashlib.sha1()
    h.update(db[:0x18])
    h.update(bytes(8))
    h.update(db[0x20:0x58])
    h.update(bytes(20))
    h.update(db[0x6C:HASH72_AT])
    h.update(bytes(HASH72_LEN))
    h.update(db[HASH72_AT + HASH72_LEN:])
    return h.digest()


def sign(db: bytes, info: HashInfo, scheme: int = 1) -> bytes:
    if len(db) < HASH72_AT + HASH72_LEN or db[:4] != b"mhbd":
        raise ValueError("not an iTunesDB")
    out = bytearray(db)
    out[0x30:0x32] = scheme.to_bytes(2, "little")
    out[HASH72_AT:HASH72_AT + HASH72_LEN] = info.sign(db_sha1(bytes(out)))
    return bytes(out)


def extract_from_db(db: bytes, guid: bytes) -> HashInfo | None:
    """The secret, from a song list iTunes signed (iTunesCDB as it is on the nano)."""
    if len(db) < HASH72_AT + HASH72_LEN or db[:4] != b"mhbd":
        return None
    return HashInfo.extract(db[HASH72_AT:HASH72_AT + HASH72_LEN], db_sha1(db), guid)
