"""Which iPod is plugged in, and what it can do.

The model comes from the iPod's model number (SysInfo's ModelNumStr, looked
up in libgpod's table of every model it knows) or, failing that, from its USB
product ID, which tells the family but not the exact model. Each family says
whether the iPod plays video, shows photos and album artwork, and whether
LuxTunes can write to it at all: some later iPods check a signature on their
song list, and the newest nanos use one nobody outside Apple can make.

No Qt in here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

# Signatures the iPod checks on its song list (libgpod makes the first two).
NO_SIGNATURE, HASH58, HASH72, HASH_AB = "none", "hash58", "hash72", "hashAB"


@dataclass(frozen=True)
class Family:
    name: str
    video: bool = False
    photos: bool = False
    artwork: bool = False
    signature: str = NO_SIGNATURE
    unsupported: str = ""          # why LuxTunes can't sync it ("" = it can)
    note: str = ""                 # shown on the device page


TOUCH = ("iPod touch, iPhone and iPad keep their music in a different way that "
         "LuxTunes doesn't support.")
HASH_AB_REASON = ("This iPod checks a signature on its song list that only Apple's "
                  "software can make, so LuxTunes can't add songs to it.")

FAMILIES = {
    "first": Family("iPod (1st generation)"),
    "second": Family("iPod (2nd generation)"),
    "third": Family("iPod (3rd generation)"),
    "fourth": Family("iPod (4th generation)"),
    "photo": Family("iPod photo", photos=True, artwork=True),
    "mini_1": Family("iPod mini (1st generation)"),
    "mini_2": Family("iPod mini (2nd generation)"),
    "mini": Family("iPod mini"),
    "shuffle_1": Family("iPod shuffle (1st generation)", note="Not tested with LuxTunes yet."),
    "shuffle_2": Family("iPod shuffle (2nd generation)", note="Not tested with LuxTunes yet."),
    "shuffle_3": Family("iPod shuffle (3rd generation)", note="Not tested with LuxTunes yet."),
    "shuffle_4": Family("iPod shuffle (4th generation)", note="Not tested with LuxTunes yet."),
    "nano_1": Family("iPod nano (1st generation)", photos=True, artwork=True),
    "nano_2": Family("iPod nano (2nd generation)", photos=True, artwork=True),
    "nano_3": Family("iPod nano (3rd generation)", video=True, photos=True, artwork=True,
                     signature=HASH58),
    "nano_4": Family("iPod nano (4th generation)", video=True, photos=True, artwork=True,
                     signature=HASH58),
    "nano_5": Family("iPod nano (5th generation)", video=True, photos=True, artwork=True,
                     signature=HASH72,
                     note="Needs to have been synced with iTunes once (LuxTunes reuses that "
                          "signature). Not tested with LuxTunes yet."),
    "nano_6": Family("iPod nano (6th generation)", photos=True, artwork=True,
                     signature=HASH_AB, unsupported=HASH_AB_REASON),
    "nano_7": Family("iPod nano (7th generation)", video=True, photos=True, artwork=True,
                     signature=HASH_AB, unsupported=HASH_AB_REASON),
    "video_1": Family("iPod (5th generation)", video=True, photos=True, artwork=True),
    "video_2": Family("iPod (5.5th generation)", video=True, photos=True, artwork=True),
    "video": Family("iPod (5th generation)", video=True, photos=True, artwork=True),
    "classic_1": Family("iPod classic (6th generation)", video=True, photos=True,
                        artwork=True, signature=HASH58),
    "classic_2": Family("iPod classic (6.5th generation)", video=True, photos=True,
                        artwork=True, signature=HASH58),
    "classic_3": Family("iPod classic (7th generation)", video=True, photos=True,
                        artwork=True, signature=HASH58),
    "classic": Family("iPod classic", video=True, photos=True, artwork=True,
                      signature=HASH58),
    "touch": Family("iPod touch", video=True, photos=True, artwork=True, unsupported=TOUCH),
    "iphone": Family("iPhone", video=True, photos=True, artwork=True, unsupported=TOUCH),
    "ipad": Family("iPad", video=True, photos=True, artwork=True, unsupported=TOUCH),
    "mobile": Family("iTunes phone", unsupported="This phone isn't supported."),
    # Not known: assume it can do everything (as LuxTunes always did).
    "unknown": Family("iPod", video=True, photos=True, artwork=True),
}

# libgpod's generation numbers (Itdb_IpodGeneration) -> family.
GENERATIONS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "photo", 6: "mobile",
    7: "mini_1", 8: "mini_2", 9: "shuffle_1", 10: "shuffle_2", 11: "shuffle_3",
    12: "nano_1", 13: "nano_2", 14: "nano_3", 15: "nano_4", 16: "video_1", 17: "video_2",
    18: "classic_1", 19: "classic_2", 20: "touch", 21: "iphone", 22: "shuffle_4",
    23: "touch", 24: "iphone", 25: "iphone", 26: "classic_3", 27: "nano_5", 28: "touch",
    29: "ipad", 30: "iphone", 31: "touch", 32: "nano_6",
}

# Apple's USB product IDs (as /sys shows them) -> family. Some IDs cover a
# whole family (all classics are 1261), so those give the family's name only.
USB_PRODUCTS = {
    "1201": "third", "1203": "fourth", "1204": "photo", "1205": "mini", "1209": "video",
    "120a": "nano_1", "1260": "nano_2", "1261": "classic", "1262": "nano_3",
    "1263": "nano_4", "1265": "nano_5", "1266": "nano_6", "1267": "nano_7",
    "1300": "shuffle_1", "1301": "shuffle_2", "1302": "shuffle_3", "1303": "shuffle_4",
}


# The video iPods and classics, for when libgpod isn't there to ask:
# model number -> (family, capacity GB, colour).
BUILT_IN = {
    "A002": ("video_1", 30, "white"), "A146": ("video_1", 30, "black"),
    "A003": ("video_1", 60, "white"), "A147": ("video_1", 60, "black"),
    "A444": ("video_2", 30, "white"), "A446": ("video_2", 30, "black"),
    "A448": ("video_2", 80, "white"), "A450": ("video_2", 80, "black"),
    "B029": ("classic_1", 80, "silver"), "B147": ("classic_1", 80, "black"),
    "B145": ("classic_1", 160, "silver"), "B150": ("classic_1", 160, "black"),
    "B562": ("classic_2", 120, "silver"), "B565": ("classic_2", 120, "black"),
    "C293": ("classic_3", 160, "silver"), "C297": ("classic_3", 160, "black"),
}


# The sizes each family was sold in (GB), to name an iPod's size from its
# disk when it doesn't say which model it is.
SIZES = {
    "first": (5, 10), "second": (10, 20), "third": (10, 15, 20, 30, 40), "fourth": (20, 40),
    "photo": (20, 30, 40, 60), "mini": (4, 6), "mini_1": (4,), "mini_2": (4, 6),
    "shuffle_1": (0.5, 1), "shuffle_2": (1, 2), "shuffle_3": (2, 4), "shuffle_4": (2,),
    "nano_1": (1, 2, 4), "nano_2": (2, 4, 8), "nano_3": (4, 8), "nano_4": (4, 8, 16),
    "nano_5": (8, 16), "nano_6": (8, 16), "nano_7": (16,),
    "video": (30, 60, 80), "video_1": (30, 60), "video_2": (30, 80),
    "classic": (80, 120, 160), "classic_1": (80, 160), "classic_2": (120,),
    "classic_3": (160,),
}


def size_from_disk(family_key: str, disk_bytes: int) -> float | None:
    """The size this iPod was sold as, from its disk's size: the family's size
    nearest to it (a "2 GB" nano has about 1.9 GB that can be used)."""
    sizes = SIZES.get(family_key)
    if not sizes or not disk_bytes or disk_bytes <= 0:
        return None
    return min(sizes, key=lambda gb: abs(gb * 1e9 - disk_bytes))


@dataclass(frozen=True)
class Identity:
    family_key: str
    family: Family
    capacity_gb: float | None = None       # as sold (None: unknown)
    colour: str = ""                       # "black", "silver", "pink"...

    @property
    def name(self) -> str:
        return self.family.name

    @property
    def capacity_label(self) -> str:
        if not self.capacity_gb or self.capacity_gb <= 0:
            return ""
        if self.capacity_gb < 1:
            return f"{round(self.capacity_gb * 1024)} MB"
        return f"{self.capacity_gb:g} GB"


@lru_cache(maxsize=1)
def model_table() -> dict:
    """Model number (last four characters) -> (capacity GB, model name, generation),
    from libgpod. Empty if libgpod isn't installed."""
    try:
        from luxtunes.core.ipod import gpod
        rows = gpod.ipod_models()
    except Exception as error:            # no libgpod: the USB product ID still helps
        log.info("no iPod model table from libgpod: %s", error)
        return {}
    return {number.upper()[-4:]: (capacity, name, generation)
            for number, capacity, name, generation in rows}


def _colour(model_name: str) -> str:
    """ "Nano (Black)" -> "black"; "Video U2" -> "black" (the U2 editions are black)."""
    if "U2" in model_name:
        return "black"
    if "(" in model_name and model_name.endswith(")"):
        return model_name[model_name.index("(") + 1:-1].strip().lower()
    return ""


def identify(model_number: str = "", usb_product: str = "") -> Identity:
    """What this iPod is, from its model number and/or USB product ID."""
    number = (model_number or "").upper()[-4:]
    known = model_table().get(number) if number else None
    if known is None and number in BUILT_IN:
        key, capacity, colour = BUILT_IN[number]
        return Identity(key, FAMILIES[key], capacity, colour)
    if known:
        capacity, name, generation = known
        key = GENERATIONS.get(generation, "unknown")
        return Identity(key, FAMILIES[key], capacity, _colour(name))
    key = USB_PRODUCTS.get((usb_product or "").lower(), "unknown")
    return Identity(key, FAMILIES[key])


def stand_in_model_number(usb_product: str) -> str | None:
    """A model number of the iPod's family, for an iPod whose SysInfo doesn't
    say which model it is: libgpod needs one to know it must sign the song
    list (classics, nano 3rd–5th generation). None if not needed or unknown."""
    key = USB_PRODUCTS.get((usb_product or "").lower())
    if key is None or FAMILIES[key].signature not in (HASH58, HASH72):
        return None
    wanted = {"classic": "classic_1"}.get(key, key)
    for number, (_, _, generation) in sorted(model_table().items()):
        if GENERATIONS.get(generation) == wanted:
            return "x" + number
    return None
