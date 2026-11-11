"""hashAB, the iPod nano 6th/7th generation's signature (core/ipod/hashab.py),
checked against signatures from the hashab project's test data."""

import hashlib
import struct

from luxtunes.core.ipod import hashab

# (SHA1, FireWire GUID, signature) from hashab's test-data.json.
KNOWN = [
    ("915dd7203086aeea302740e97febb7aefa9ca9f1", "f832c65917da6785",
     "030056d7474d4843465449bd444266eb516bf4552726b94169d1424c4f433a8208fc5345e34507c3fe4a8f4e8a5052664515aad30e4b195c57"),
    ("457b7fc697ee76b0a64844fd0ae871f524afca75", "42f10dc6c056bc67",
     "030056a4474d48e14654494a4442aac051622955974688414385484c4f437ca63bd45315c6451f114a4a674e36505203cc1c98f1964b8b8957"),
    ("987589f7a7e2511d86651d9777608ed6cb615e44", "bd1f3238d729c5f0",
     "03005619474d488d4654494c4442c0d05107055509271541d7d3ee4c4f43d2fcabe25352874540d81a4aaf4e74505209703cb79dad4b0bc757"),
]


def test_known_signatures_are_made_exactly():
    for sha1, guid, expected in KNOWN:
        expected = bytes.fromhex(expected)
        rnd = hashab.random_part(expected)
        assert hashab.signature(bytes.fromhex(sha1), bytes.fromhex(guid), rnd) == expected


def test_new_signatures_carry_their_random_part():
    sig = hashab.signature(bytes(20), hashab.device_id("0x000A270012345678"))
    assert len(sig) == 57 and sig[:2] == b"\x03\x00"
    again = hashab.signature(bytes(20), hashab.device_id("000A270012345678"), hashab.random_part(sig))
    assert again == sig


def test_a_song_list_is_signed_as_the_nano_checks_it():
    guid = hashab.device_id("000A270012345678")
    data = bytearray(b"mhbd" + bytes(0xF0) + b"songs" * 50)
    data[0x18:0x20] = b"DATABASE"                       # db_id: kept, but not signed
    data[0x72:0x74] = b"\x01\x02"                       # an old hash72: removed
    signed = hashab.sign_song_list(bytes(data), guid)
    assert struct.unpack_from("<H", signed, 0x30)[0] == hashab.SCHEME_HASH_AB
    assert signed[0x18:0x20] == b"DATABASE" and signed[0x72:0x72 + 46] == bytes(46)
    assert hashab.song_list_is_signed(signed, guid)
    assert not hashab.song_list_is_signed(signed[:-1] + b"X", guid)
    assert not hashab.song_list_is_signed(signed, hashab.device_id("000A270087654321"))


def test_locations_checksum_lists_each_blocks_sha1():
    guid = hashab.device_id("000A270012345678")
    locations = bytes(range(256)) * 9                   # two whole 1024-byte blocks and a bit
    cbk = hashab.locations_checksum(locations, guid)
    sha1s = hashlib.sha1(locations[:1024]).digest() + hashlib.sha1(locations[1024:2048]).digest()
    assert cbk[57:77] == hashlib.sha1(sha1s).digest() and cbk[77:] == sha1s
    assert cbk[:57] == hashab.signature(cbk[57:77], guid, hashab.random_part(cbk[:57]))
