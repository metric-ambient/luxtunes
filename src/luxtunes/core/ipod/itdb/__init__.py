"""LuxTunes' own iPod engine: reads and writes an iPod's song list, album
artwork and signatures without libgpod.

iPod database formats ported from ZenPod by ishiichan-dayo
(https://github.com/ishiichan-dayo/ZenPod, GPL-3.0-or-later), which builds on
libgpod. Thank you to both.

  binary.py     reading and writing little-endian numbers in records
  itunesdb.py   iTunesDB: songs, playlists, Play Counts, On-The-Go
  hash58.py     the signature iPod classics and nano 3rd/4th generation check
  artwork.py    ArtworkDB and the .ithmb picture files
  database.py   an iPod's database, opened and saved safely (what sync.py uses)

Pure Python (numpy and Qt's QImage for pictures), so it runs wherever
LuxTunes runs, Windows included.
"""
