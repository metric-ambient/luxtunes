"""hashAB: the signature the iPod nano 6th and 7th generation check on their
song list. Without the right one the nano shows no music.

A Python translation of hashab by Dylan Staley (https://github.com/dstaley/hashab),
who reverse-engineered it from iPod nano 6th generation firmware and released
the C code under the Unlicense. Its lookup tables are in hashab_tables.py,
copied unchanged from that code. The EDON-R function it uses comes, through
hashab, from RHash's edonr.c by Aleksey Kravchenko (permissive licence).
Thank you to both: without their work no iPod nano 6th or 7th generation
could be synced outside iTunes.

Checked against hashab's test signatures (tests/test_hashab.py) and against
the signatures iTunes wrote on a real iPod nano 6th generation.

Pure Python, no Qt, nothing to compile or install.
"""

from __future__ import annotations

import hashlib
import os
import struct

from luxtunes.core.ipod import hashab_tables as T

M = 0xFFFFFFFF
SIGNATURE_SIZE = 57
RANDOM_SIZE = 23


def _rol(value: int, count: int) -> int:
    count &= 31
    value &= M
    return ((value << count) | (value >> ((32 - count) & 31))) & M


def _ror(value: int, count: int) -> int:
    count &= 31
    value &= M
    return ((value >> count) | (value << ((32 - count) & 31))) & M


def _le(data, offset: int) -> int:
    return data[offset] | data[offset + 1] << 8 | data[offset + 2] << 16 | data[offset + 3] << 24


def _put_le(data: bytearray, offset: int, value: int) -> None:
    data[offset: offset + 4] = (value & M).to_bytes(4, "little")


# ---- The block cipher (an obfuscated AES-128 in CBC mode) --------------------------------

class _Profile:
    def __init__(self, stage: int):
        get = lambda name: getattr(T, f"STAGE{stage}_{name}")   # noqa: E731
        self.input_map = get("INPUT_MAP")
        self.final_map = get("FINAL_MAP")
        self.output_map = get("OUTPUT_MAP")
        self.schedule = get("SCHEDULE")
        base = get("ROUND_BASE")
        affine = get("ROUND_AFFINE")
        rows = [affine[i * 9: i * 9 + 9] for i in range(15)]

        def affine_byte(parameters, value):
            result = parameters[0]
            for bit in range(8):
                if value & (1 << bit):
                    result ^= parameters[bit + 1]
            return result

        # profile_word(table, index) for every table and index, worked out once.
        self.words = []
        for table in range(4):
            column = []
            for index in range(256):
                b = base[index]
                word = 0
                for byte in range(4):
                    lane = table * 4 + byte
                    value = b if lane == 0 else affine_byte(rows[lane - 1], b)
                    word |= value << (byte * 8)
                column.append(word)
            self.words.append(column)
        self.inverse_output = [0] * 256
        for index in range(255, -1, -1):          # the first index wins, as in the C
            self.inverse_output[self.output_map[index]] = index

    def _table_round(self, state, round_key) -> list:
        out = [0] * 16
        for word in range(4):
            value = _le(round_key, word * 4)
            for table in range(4):
                value ^= self.words[table][state[(word * 4 + table * 5) & 15]]
            out[word * 4: word * 4 + 4] = value.to_bytes(4, "little")
        return out

    def _final_round(self, state, round_key) -> list:
        out = [0] * 16
        for position in range(16):
            source = ((position & 12) + (position & 3) * 5) & 15
            value = self.final_map[state[source]]
            out[position] = ((value * 0x81 + 0x7A) & 0xFF) ^ round_key[position]
        return out

    def cbc(self, data: bytes, chain) -> bytes:
        if not data or len(data) % 16:
            raise ValueError("the cipher works on whole 16-byte blocks")
        chain = list(chain)
        schedule = self.schedule
        out = bytearray()
        for offset in range(0, len(data), 16):
            state = [self.input_map[data[offset + p]] ^ chain[p] ^ schedule[p] for p in range(16)]
            for round_number in range(1, 10):
                state = self._table_round(state, schedule[round_number * 16: round_number * 16 + 16])
            state = self._final_round(state, schedule[160:176])
            chain = state
            out += bytes(self.output_map[v] for v in state)
        return bytes(out)


_STAGE1 = _Profile(1)
_STAGE2 = _Profile(2)


def _stage1(data: bytes) -> bytes:
    return _STAGE1.cbc(data, T.STAGE1_INITIAL_CHAIN)


def _stage2(data: bytes, iv: bytes) -> bytes:
    return _STAGE2.cbc(data, [_STAGE2.inverse_output[b] for b in iv])


# ---- EDON-R 256 (from RHash's edonr.c, via hashab) ----------------------------------------

def _q256(x, y):
    """One Q256 step: 8 words from x and 8 from y give 8 new words."""
    x0, x1, x2, x3, x4, x5, x6, x7 = x
    y0, y1, y2, y3, y4, y5, y6, y7 = y
    t8 = (x0 + x4) & M
    t9 = (x1 + x7) & M
    t12 = (t8 + t9) & M
    t10 = (x2 + x3) & M
    t11 = (x5 + x6) & M
    t13 = (t10 + t11) & M
    t0 = (0xAAAAAAAA + t12 + x2) & M
    t1 = _rol(t12 + x3, 4)
    t2 = _rol(t12 + x6, 8)
    t3 = _rol(t13 + x7, 13)
    t4 = _rol(x1 + t13, 17)
    t5 = _rol(t8 + t10 + x5, 22)
    t6 = _rol(x0 + t9 + t11, 24)
    t7 = _rol(t13 + x4, 29)
    t16 = t0 ^ t4
    t17 = t1 ^ t7
    t18 = t2 ^ t3
    t19 = t5 ^ t6
    t8 = t3 ^ t19
    t9 = t2 ^ t19
    t10 = t18 ^ t5
    t11 = t16 ^ t1
    t12 = t16 ^ t7
    t13 = t17 ^ t6
    t14 = t18 ^ t4
    t15 = t0 ^ t17
    t16 = (y0 + y1) & M
    t17 = (y2 + y5) & M
    t20 = (t16 + t17) & M
    t18 = (y3 + y4) & M
    t22 = (t16 + t18) & M
    t19 = (y6 + y7) & M
    t21 = (t18 + t19) & M
    t23 = (t17 + t19) & M
    t0 = (0x55555555 + t20 + y7) & M
    t1 = _rol(t22 + y6, 5)
    t2 = _rol(t20 + y3, 9)
    t3 = _rol(y2 + t21, 11)
    t4 = _rol(t22 + y5, 15)
    t5 = _rol(t23 + y4, 20)
    t6 = _rol(y1 + t23, 25)
    t7 = _rol(y0 + t21, 27)
    t16 = t0 ^ t1
    t17 = t2 ^ t5
    t18 = t3 ^ t4
    t19 = t6 ^ t7
    z5 = (t8 + (t18 ^ t6)) & M
    z6 = (t9 + (t17 ^ t7)) & M
    z7 = (t10 + (t4 ^ t19)) & M
    z0 = (t11 + (t16 ^ t5)) & M
    z1 = (t12 + (t2 ^ t19)) & M
    z2 = (t13 + (t16 ^ t3)) & M
    z3 = (t14 + (t0 ^ t18)) & M
    z4 = (t15 + (t1 ^ t17)) & M
    return [z0, z1, z2, z3, z4, z5, z6, z7]


_EDONR_START = [0x40414243, 0x44454647, 0x48494A4B, 0x4C4D4E4F, 0x50515253, 0x54555657,
                0x58595A5B, 0x5C5D5E5F, 0x60616263, 0x64656667, 0x68696A6B, 0x6C6D6E6F,
                0x70717273, 0x74757677, 0x78797A7B, 0x7C7D7E7F]


def _edonr_compress(h: list, block: list) -> None:
    b = block
    p16 = _q256(b[15:7:-1], b[0:8])
    p24 = _q256(p16, b[8:16])
    p16 = _q256(h[8:16], p16)
    p24 = _q256(p16, p24)
    p16 = _q256(p16, h[0:8])
    p24 = _q256(p24, p16)
    p16 = _q256(b[7::-1], p16)
    p24 = _q256(p16, p24)
    for i in range(8):
        h[i] ^= b[8 + i] ^ p16[i]
        h[8 + i] ^= b[i] ^ p24[i]


# ---- The keyed hash ----------------------------------------------------------------------

def _encoded_rol(value: int, count: int) -> int:
    return _rol((value * 0x8FA491DF) & M, count)


def _preprocess_word(word: int) -> int:
    im, cm, bm, bias = 0xA272BE3F, 0x7F8631BF, 0xC83BEBC7, 0x44E57C7E
    byte0 = T.HASH_BYTES_0[word & 0xFF]
    byte1 = T.HASH_BYTES_1[(word >> 8) & 0xFF]
    byte2 = T.HASH_BYTES_2[(word >> 16) & 0xFF]
    byte3 = T.HASH_BYTES_3[word >> 24]
    scaled = ((((byte0 * 0xC9 * 0x79) & 0xFF) << 8) * im) & M
    byte1 = (byte1 * 0xC9) & 0xFF
    high = (~(byte1 * 0x79) | 0xFF) & M
    partial = (byte1 * bm + bias + ((~high) & M) * im + high * bias + scaled) & M
    scaled = (partial * cm * 0x100 * im) & M
    high = (~(byte2 * 0x79) | 0xFF) & M
    partial = (byte2 * bm + bias + ((~high) & M) * im + high * bias + partial + scaled) & M
    high = (~(byte3 * 0x79) | 0xFF) & M
    partial = (high * bias + byte3 * bm + bias + ((~high) & M) * im
               + ((partial * cm * 0x100 * im) & M) + partial) & M
    partial = (((partial * cm * 0x100 * im) & M) + partial) & M
    return (partial * cm) & M


def _byte_index(value: int) -> int:
    return (value * 0xBF) & 0xFF


def _state_to_word(canonical: int) -> int:
    im, index_decode, index_mul = 0xA272BE3F, 0x7F8631BF, 0x5D8D41C1
    state_mul, bm, bias = 0xBCB95B41, 0xC83BEBC7, 0x44E57C7E
    rotate_input, or_mul = 0x1E051C21, 0x00F39C82
    state = (canonical * 0xC2ED397F) & M

    mapped = T.HASH_BYTES_0[_byte_index(state * state_mul)]
    mapped = (mapped * 0xC9) & 0xFF
    high = (~(mapped * 0x79) | 0xFF) & M
    combined = (state * state_mul + high * bias + mapped * bm + bias + ((~high) & M) * im) & M
    rotated = _encoded_rol((combined * rotate_input) & M, 11)
    rotated = (rotated * im) & M

    mapped = T.HASH_BYTES_1[_byte_index(rotated)]
    mapped = (mapped * 0xC9) & 0xFF
    high = (~(mapped * 0x79) | 0xFF) & M
    combined = (((~high) & M) * im + mapped * bm + bias + high * bias) & M
    either = (((combined * or_mul - 1) & M) | ((rotated * or_mul - 1) & M)) & M
    rotated = _encoded_rol(((either * im + rotated + combined - index_mul) & M) * rotate_input & M, 10)

    mapped = T.HASH_BYTES_2[_byte_index(rotated * im)]
    high = (~(mapped * 0x79) | 0xFF) & M
    combined = (mapped * bm + bias + ((~high) & M) * im + high * bias) & M
    rotated = _encoded_rol(((rotated * im - combined) & M) * rotate_input & M, 7)
    return (rotated * im * index_decode) & M


def _hash(work: bytearray) -> bytes:
    """32 bytes from the 64-byte work area, which is changed too (as in the C)."""
    block = [_preprocess_word(_le(work, i * 4)) for i in range(16)]
    state = list(_EDONR_START)
    for round_number in range(4):
        _edonr_compress(state, block)
        if round_number != 3:
            block = [_state_to_word(state[i]) for i in range(16)]
    for i in range(16):
        _put_le(work, i * 4, block[i])
    return b"".join(((state[8 + i] * 0xF46EF4FF) & M).to_bytes(4, "little") for i in range(8))


def _transform_chunk(digest: bytes) -> bytes:
    out = bytearray(32)
    for i in range(8):
        word = _le(digest, i * 4)
        product = (word * 0xC8180AFF) & M
        value = (((word * 0x6B) & 0xFF)
                 | (((product >> 8) * 0x95) & 0xFF) << 8
                 | (((product >> 16) * 0x95) & 0xFF) << 16
                 | (((product >> 24) * 0x95) & 0xFF) << 24)
        _put_le(out, i * 4, value)
    return bytes(out)


def _expand(at20: bytes, at0: bytes) -> bytes:
    work = bytearray(64)
    for i in range(24):
        work[i] = (at20[i] * 0x6F) & 0xFF
    for i in range(20):
        work[24 + i] = (at0[i] * 0x6F) & 0xFF
    _put_le(work, 48, 12)
    out = bytearray()
    for iteration in range(6):
        _put_le(work, 44, iteration)
        out += _transform_chunk(_hash(work))
    return bytes(out[:190])


# ---- Reduce: 190 bytes down to 16 --------------------------------------------------------

def _rol_or_ones(value: int, count: int) -> int:
    return _rol(value, count) if count else M


def _ror_or_ones(value: int, count: int) -> int:
    return _ror(value, count) if count else M


def _choose(mask: int, when_set: int, when_clear: int) -> int:
    return ((mask & when_set) | (~mask & M & when_clear)) & M


def _maj(a: int, b: int, c: int) -> int:
    return ((a & b) | (a & c) | (b & c)) & M


def _fold(word: int) -> int:
    folded = word & 0xFF
    folded = (folded + ((word >> 8) & 0xFF)) & 0xFF
    folded ^= (word >> 16) & 0xFF
    return (folded + ((word >> 24) & 0xFF)) & 0xFF


def _reduce(data: bytes) -> bytes:
    key = list(T.REDUCE_KEY)
    state = list(T.REDUCE_STATE)
    table = T.REDUCE_TABLE
    work = []
    for i in range(128):
        word = 0
        for byte in range(4):
            word |= ((data[(4 * i + byte) % 190] * 0xBD) & 0xFF) << (24 - 8 * byte)
        work.append(word)
    for _ in range(4):
        for j in range(128):
            work[j] = ((work[j] ^ _rol(work[(j - 13) & 127], 3)) + _rol(work[(j + 71) & 127], 5)
                       - _rol(work[(j + 101) & 127], 23)) & M
    N = lambda v: (~v) & M       # noqa: E731  (32-bit "not")

    s7 = ((work[22] // 5) * 0xC3456F96) & M
    s8 = (_maj(state[60], table[63], 0x908D4D25) // 3 + 0xEF9695DD) & M
    key[42] = (key[42] + ((table[work[33] & 63] & 0x3CBA9069) + 0x43440114) // 5) & M
    s9 = _choose(state[26], state[work[87] & 63], N(0x3CBA9069))
    rotation = _rol_or_ones(0x6F72B2DA, (-state[work[55] & 63]) & 31)
    k = key[s7 & 63] // 5
    selected = _choose(state[42], N(0x3047874D), N(0xB0DEA603))
    s10 = rotation ^ 0x908D4D25
    key[63] = (key[63] - _maj(selected, k, s7)) & M
    state[25] = (state[25] + key[s7 & 63]) & M
    state[60] = (_choose(key[21], s8, N(0x1FB7D8D3)) * work[122]) & M
    work[5] = (work[5] + _choose(s7, N(0xD36D6FB6), table[work[120] & 63])) & M

    key[35] = (key[35] + 0xB1A7E76F) & M
    s18 = key[s10 & 63] ^ 0x908D4D25
    state[44] ^= (3 * _choose(0xC3456F96, table[29], table[work[18] & 63])) & M
    work[107] = (work[107] + state[work[44] & 63] + 1) & M
    inverted_rotation = N(_ror_or_ones(0xB0DEA603, work[71] & 31))
    key[54] = (key[54] - _maj(state[29], inverted_rotation, N(0x6F72B2DA))) & M
    work[62] ^= 0xDBD3D289
    key[19] = (key[19] + state[s7 & 63] // 5 - _maj(state[45], table[work[45] & 63], N(0x4CF50FA3))) & M
    key[49] = table[(state[s18 & 63] * state[s18 & 63]) & 63]
    key[21] ^= table[work[27] & 63]
    s19 = (0x908D4D24 - _rol_or_ones(0x98B99BD8, (state[60] * state[41]) & 31)) & M
    state[10] = work[73]
    key[20] ^= _rol_or_ones(N(state[work[98] & 63]), key[60] & 31)
    key[49] = (key[49] + (_choose(state[56], key[41], table[s19 & 63]) >> 1)) & M
    table_word = table[work[10] & 63]
    state[24] ^= _choose(key[39], state[37], (table_word * table_word * table_word) & M)
    s6 = table[work[43] & 63] & N(0x45059986)
    s6 = (s6 | 0x41050082) ^ 0x3CBA9069
    rotated = _ror_or_ones(N(s18), table[work[31] & 63] & 31)
    state[13] ^= ((key[work[50] & 63] & N(0x147F3886)) | N(rotated | 0xEB80C779)) & M

    key[28] = (key[28] - _choose(key[35], _maj(key[58], table[s9 & 63], 0x908D4D25), N(key[29]))) & M
    state[23] = (state[23] + N(_maj(s8, state[work[106] & 63], 0x2725FE3B))) & M
    state[24] ^= (work[118] * work[118]) & M
    key[35] ^= _maj(state[54], (2 * work[108]) & M, (state[41] * state[41] * state[41]) & M)
    work[34] ^= _choose(s18, state[work[39] & 63] ^ state[37], (table[29] + 0x02E5A7E3) & M)
    state[42] ^= _choose(state[40], state[work[35] & 63], (work[78] * work[78]) & M)
    work[49] = (work[49] + 3 * key[work[39] & 63] + 0x62116BE6) & M
    key[48] = s7
    state[62] = (key[s19 & 63] - state[work[93] & 63] - 0xEF9695DD) & M
    key[55] = (key[55] + key[60] // 3 - state[work[112] & 63] // 3) & M
    s21 = (_maj(s6, (table[60] + 0x4D485456) & M, 0x41D44D69) + 0x3CBA9069) & M
    s25 = s9 ^ key[37]
    state[55] = (state[55] + 0x1D0F1B76) & M
    work[77] = (work[77] - table[(state[60] & 127) >> 1]) & M
    state[29] = key[work[68] & 63] // 15
    state[46] ^= table[table[table[37] & 63] & 63]
    s22 = (0x4F2159FC - _maj(state[59], table[29], table[60] // 5)) & M
    s24 = (_choose(0xBD06DD88, _maj(0x59089BE9, table[work[16] & 63], state[60]), table[60])
           + 0x4F2159FC) & M
    work[10] = 0xDE2849DD
    work[8] = (work[8] + _maj(_choose(state[52], 0x908D4D25, 0x5987B420), work[48] ^ s22,
                              0x51127D0F)) & M
    state[20] = (state[20] + table[work[1] & 63]) & M
    work[32] = (work[32] + 1 + _ror_or_ones(0x0C7AD237, (s22 * s22 * s22) & 31)) & M
    state[4] = (state[4] - (_choose(key[35], 0x4F2159FC, 0xC0540978)
                            | N(_ror_or_ones(0x46EF99F4, key[41] & 31)))) & M
    state[60] = N(_rol_or_ones(N(work[62] // 3), (work[22] + key[work[93] & 63]) & 31))
    key[30] = 0x3CBA9069
    key[10] = N((table[work[122] & 63] + 0x972DA190) & M)
    state[46] = (state[46] + _maj(state[8], table[work[35] & 63] | table[41], 0xF3D39067)) & M
    s26 = (0x3CBA9069 - (((table[work[67] & 63] | table[work[120] & 63])
                          - table[work[98] & 63] - 1) & M)) & M
    word = state[work[38] & 63] >> 1
    state[17] ^= (word * word * word) & M
    state[44] = (state[44] + table[62]) & M
    key[26] = (key[26] - table[s6 & 63]) & M
    first = _choose(0x313EC15D, key[s6 & 63], state[60])
    second = (table[work[79] & 63] & 0x7FBBD9FD) | 0x0C201068
    key[35] = (key[35] - _maj(key[34], first, second)) & M
    key[16] ^= _maj(state[43], work[27] >> 1, state[41] | state[37])
    work[33] = 0x1C3E9665
    key[58] = (key[58] - work[118]) & M
    state[21] = (state[21] + key[60]) & M
    state[10] = (state[10] - state[60]) & M

    initial = [(0xBA + s25) & 0xFF, 0x97, 0x97, (0x9B + s24) & 0xFF, (0xBA + s8) & 0xFF, 0x97,
               (0xD3 + s22) & 0xFF, 0x97, (0xBE + s18 + s6) & 0xFF, (0x27 + s7) & 0xFF, 0x4C,
               (0x27 + s19) & 0xFF, (0xBE + s10 + s21) & 0xFF, (0xE3 + s26) & 0xFF, 0x4C,
               (0x27 + work[92]) & 0xFF]
    out = bytearray(16)
    for i in range(16):
        reduced = initial[i]
        for j in range(i, 64, 16):
            reduced ^= (key[j] & 0xFF) ^ (state[j] & 0xFF)
        for j in range(i, 128, 16):
            reduced ^= _fold(work[j])
        out[i] = (0xA5 * reduced) & 0xFF
    return bytes(out)


# ---- The signature --------------------------------------------------------------------------

_PERMUTE_32 = (0x06, 0x18, 0x11, 0x07, 0x13, 0x0D, 0x0E, 0x09, 0x15, 0x1D, 0x02, 0x1F, 0x01, 0x04,
               0x1C, 0x1A, 0x10, 0x14, 0x0B, 0x1E, 0x03, 0x0A, 0x1B, 0x19, 0x05, 0x0F, 0x16, 0x00,
               0x12, 0x17, 0x08, 0x0C)
_PERMUTE_56 = (0x15, 0x1C, 0x06, 0x0C, 0x07, 0x1A, 0x05, 0x13, 0x08, 0x19, 0x03, 0x01, 0x2D, 0x1E,
               0x10, 0x31, 0x1D, 0x14, 0x28, 0x27, 0x35, 0x00, 0x2F, 0x1B, 0x26, 0x0B, 0x0E, 0x02,
               0x23, 0x17, 0x24, 0x22, 0x12, 0x1F, 0x20, 0x04, 0x29, 0x25, 0x21, 0x09, 0x18, 0x0D,
               0x32, 0x0F, 0x11, 0x2E, 0x33, 0x2B, 0x30, 0x2A, 0x36, 0x0A, 0x2C, 0x34, 0x16, 0x37)


def signature(sha1: bytes, device_id: bytes, rnd: bytes | None = None) -> bytes:
    """The 57-byte hashAB signature of `sha1` for the iPod with this device
    id (its FireWire GUID, 8 bytes). `rnd`: 23 random bytes (new ones if
    not given; the signature carries them, so any will do)."""
    if len(sha1) != 20:
        raise ValueError("sha1 must be 20 bytes")
    device_id = (bytes(device_id) + bytes(8))[:8]
    rnd = os.urandom(RANDOM_SIZE) if rnd is None else bytes(rnd)
    if len(rnd) != RANDOM_SIZE:
        raise ValueError("rnd must be 23 bytes")
    stage1_input = bytes((b * 0xED) & 0xFF for b in device_id + sha1 + rnd) + b"\xc1" * 29
    stage1_output = _stage1(stage1_input)
    compression = _reduce(_expand(stage1_output[20:44], stage1_output[0:24]))
    stage2_input = bytes(stage1_output[44 + p] for p in _PERMUTE_32)
    stage2_output = _stage2(stage2_input, compression)
    out = bytearray(b"\x03\x00")
    for source in _PERMUTE_56[:55]:
        out.append(rnd[source] if source < RANDOM_SIZE
                   else (stage2_output[source - RANDOM_SIZE] * 0x2D) & 0xFF)
    return bytes(out)


def random_part(sig: bytes) -> bytes:
    """The 23 random bytes a signature was made with."""
    out = bytearray(RANDOM_SIZE)
    for i, source in enumerate(_PERMUTE_56[:55]):
        if source < RANDOM_SIZE:
            out[source] = sig[i + 2]
    return bytes(out)


def device_id(firewire_guid: str) -> bytes:
    """The FireWire GUID ("000A27…", with or without 0x) as the 8 bytes signed with."""
    text = firewire_guid.strip()
    if text[:2].lower() == "0x":
        text = text[2:]
    return bytes.fromhex(text)[:8]


# ---- Signing the nano's files ---------------------------------------------------------------

DB_ID, HASH58, SCHEME, HASH72, HASH_AB = (0x18, 8), (0x58, 20), (0x30, 2), (0x72, 46), (0xAB, 57)
SCHEME_HASH_AB = 3


def sign_song_list(data: bytes, guid: bytes, rnd: bytes | None = None) -> bytes:
    """iTunesCDB (or iTunesDB) signed with hashAB, as iTunes does: scheme 3,
    no hash58/hash72, and the SHA1 of the whole file (with db_id and the
    signature fields zeroed) signed at 0xAB."""
    if data[:4] != b"mhbd" or len(data) < 0xF4:
        raise ValueError("not an iPod song list")
    out = bytearray(data)
    struct.pack_into("<H", out, SCHEME[0], SCHEME_HASH_AB)
    for offset, size in (HASH58, HASH72, HASH_AB):
        out[offset: offset + size] = bytes(size)
    db_id = bytes(out[DB_ID[0]: DB_ID[0] + DB_ID[1]])
    out[DB_ID[0]: DB_ID[0] + DB_ID[1]] = bytes(DB_ID[1])
    digest = hashlib.sha1(out).digest()
    out[DB_ID[0]: DB_ID[0] + DB_ID[1]] = db_id
    out[HASH_AB[0]: HASH_AB[0] + HASH_AB[1]] = signature(digest, guid, rnd)
    return bytes(out)


def song_list_is_signed(data: bytes, guid: bytes) -> bool:
    stored = bytes(data[HASH_AB[0]: HASH_AB[0] + HASH_AB[1]])
    return sign_song_list(data, guid, random_part(stored)) == bytes(data)


def locations_checksum(locations: bytes, guid: bytes, rnd: bytes | None = None) -> bytes:
    """Locations.itdb.cbk for this Locations.itdb: the signature, then the
    SHA1 of the SHA1s of each whole 1024-byte block, then those SHA1s."""
    sha1s = b"".join(hashlib.sha1(locations[i: i + 1024]).digest()
                     for i in range(0, len(locations) - len(locations) % 1024, 1024))
    final = hashlib.sha1(sha1s).digest()
    return signature(final, guid, rnd) + final + sha1s
