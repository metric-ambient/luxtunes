"""Command-line entry point: `luxtunes <command>` or `python -m luxtunes <command>`.

Commands:
    luxtunes                       (no command: open the LuxTunes window)
    luxtunes import-itunes "iTunes Music Library.xml" --media-folder "~/Music/LuxTunes/LuxTunes Media"
    luxtunes stats
"""

from __future__ import annotations

import argparse
import os
import sys

from luxtunes.core import db
from luxtunes.importer.itunes_xml import import_library


def _restore_library_path() -> None:
    """In the packaged app, let the programs LuxTunes runs (ffmpeg, cdparanoia, ...)
    use the system's libraries rather than the ones bundled with LuxTunes."""
    if not getattr(sys, "frozen", False):
        return
    original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        os.environ["LD_LIBRARY_PATH"] = original
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


COMMANDS = ("import-itunes", "stats")


def _drop_opened_items(argv: list[str]) -> list[str]:
    """The desktop starts LuxTunes with the plugged-in iPod or CD's address
    (e.g. file:///media/you/IPOD) when it's chosen for "Music player" or
    "Audio CD" in the removable media settings. LuxTunes finds those itself,
    so such an address just means: open the window."""
    if argv and not argv[0].startswith("-") and argv[0] not in COMMANDS:
        return []
    return argv


def main(argv: list[str] | None = None) -> int:
    _restore_library_path()
    argv = _drop_opened_items(sys.argv[1:] if argv is None else list(argv))
    parser = argparse.ArgumentParser(prog="luxtunes")
    parser.add_argument(
        "--db", help=f"library database file (default: {db.default_db_path()})"
    )
    commands = parser.add_subparsers(dest="command")  # no command = open the window

    imp = commands.add_parser("import-itunes", help="import an iTunes library XML file")
    imp.add_argument("xml", help='path to "iTunes Music Library.xml"')
    imp.add_argument(
        "--media-folder",
        required=True,
        help='where your iTunes Media folder now lives, e.g. "~/Music/LuxTunes/LuxTunes Media"',
    )
    imp.add_argument(
        "--itunes-folder",
        help="the old iTunes Media path (default: read from the XML)",
    )

    commands.add_parser("stats", help="show a summary of the library")

    args = parser.parse_args(argv)
    if args.command is None:
        from luxtunes.ui.app import run
        return run(args.db)

    conn = db.connect(args.db)

    if args.command == "import-itunes":
        result = import_library(conn, args.xml, args.media_folder, args.itunes_folder)
        print(f"Imported {result.tracks} tracks and {result.playlists} playlists.")
        if result.missing_files:
            print(f"\n{len(result.missing_files)} files were not found on disk. First few:")
            for path in result.missing_files[:10]:
                print(f"  {path}")
        if result.unknown_keys:
            print(f"\nKept but not yet understood: {', '.join(sorted(result.unknown_keys))}")

    if args.command in ("import-itunes", "stats"):
        print()
        for name, value in db.stats(conn).items():
            print(f"  {name.replace('_', ' '):>16}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
