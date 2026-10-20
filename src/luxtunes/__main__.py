"""Command-line entry point: `luxtunes <command>` or `python -m luxtunes <command>`.

Commands:
    luxtunes                       (no command: open the LuxTunes window)
    luxtunes import-itunes "iTunes Music Library.xml" --media-folder "~/Music/LuxTunes/LuxTunes Media"
    luxtunes stats
"""

from __future__ import annotations

import argparse
import sys

from luxtunes.core import db
from luxtunes.importer.itunes_xml import import_library


def main(argv: list[str] | None = None) -> int:
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
