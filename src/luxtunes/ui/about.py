"""Help → About LuxTunes: version, credits, licenses and system details.

Three tabs, like most desktop apps:
  About        the icon, version, a one-line description and the copyright.
  Licenses     LuxTunes' own licence (GPL v3, with "View License…") and the
               open-source parts it's built on.
  System Info  versions and file locations, with "Copy" for bug reports.
"""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import subprocess
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, qVersion
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from luxtunes import __version__
from luxtunes.core import organize

RESOURCES = Path(__file__).resolve().parent.parent / "resources"
# Shown under the version in the About tab.
COPYRIGHT = "Copyright © 2026 metric-ambient. Written entirely by Claude Opus 5.5."
DESCRIPTION = "A native Linux music manager for your library, your CDs and your iPod Classic."

# (name, what LuxTunes uses it for, licence, link)
THIRD_PARTY = [
    ("Qt 6", "windows, drawing and audio playback", "GNU LGPL v3",
     "https://www.qt.io/licensing/open-source-lgpl-obligations"),
    ("PySide6 (Qt for Python)", "using Qt from Python", "GNU LGPL v3",
     "https://doc.qt.io/qtforpython-6/licenses.html"),
    ("Mutagen", "reading and writing tags in music files", "GNU GPL v2 or later",
     "https://github.com/quodlibet/mutagen"),
    ("FFmpeg", "playing video, Create MP3 Version and encoding imported CDs (used as a separate "
     "program)",
     "GNU LGPL v2.1 or later; GPL v2 or later for some builds", "https://ffmpeg.org/legal.html"),
    ("LAME", "the MP3 encoder inside FFmpeg", "GNU LGPL v2 or later", "https://lame.sourceforge.io/"),
    ("cdparanoia", "reading audio CDs (used as a separate program)", "GNU GPL v2 or later",
     "https://www.xiph.org/paranoia/"),
    ("libgpod", "reading and writing iPod databases", "GNU LGPL v2 or later",
     "https://sourceforge.net/projects/gtkpod/"),
    ("MusicBrainz", "CD track names (an online database)", "Data: CC0 (public domain)",
     "https://musicbrainz.org/doc/About/Data_License"),
    ("Cover Art Archive", "CD covers (an online archive)", "Each cover belongs to its owner",
     "https://coverartarchive.org/"),
    ("SQLite", "the library database", "Public domain", "https://www.sqlite.org/copyright.html"),
    ("Python", "the language LuxTunes is written in", "Python Software Foundation License",
     "https://docs.python.org/3/license.html"),
]


def app_icon() -> QIcon:
    """LuxTunes' icon (a penguin wearing headphones), in every size we ship."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        path = RESOURCES / f"luxtunes-{size}.png"
        if path.exists():
            icon.addFile(str(path))
    return icon


def _version_of(module_name: str) -> str:
    try:
        module = __import__(module_name)
    except ImportError:
        return "not installed"
    return getattr(module, "version_string", None) or getattr(module, "__version__", "unknown")


def _ffmpeg_version() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        return "not installed (needed for Create MP3 Version)"
    try:
        first = subprocess.run([path, "-version"], capture_output=True, text=True,
                               timeout=3).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return path
    return first.replace("ffmpeg version ", "").split(" Copyright")[0]


def _system_name() -> str:
    """e.g. "Linux Mint 22.1"."""
    try:
        return platform.freedesktop_os_release().get("PRETTY_NAME") or platform.platform()
    except OSError:
        return platform.platform()


def system_info(conn: sqlite3.Connection | None) -> list[tuple[str, str]]:
    import PySide6
    database = ""
    if conn is not None:
        row = conn.execute("PRAGMA database_list").fetchone()
        database = row[2] if row else ""
    return [
        ("LuxTunes", __version__),
        ("This copy", _which_copy()),
        ("Python", platform.python_version()),
        ("Qt", qVersion()),
        ("PySide6", getattr(PySide6, "__version__", "unknown")),
        ("Mutagen", _version_of("mutagen")),
        ("SQLite", sqlite3.sqlite_version),
        ("FFmpeg", _ffmpeg_version()),
        ("cdparanoia", "installed" if shutil.which("cdparanoia") else
         "not installed (sudo apt install cdparanoia)"),
        ("System", _system_name()),
        ("Desktop", os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
                    + f" ({QApplication.platformName()})"),
        ("Library database", database),
        ("LuxTunes Media folder", str(organize.media_folder(conn)) if conn is not None else ""),
        ("Settings file", QSettings().fileName()),
    ]


def _which_copy() -> str:
    from luxtunes.core import updater
    kind = updater.kind()
    if kind == "appimage":
        return f"AppImage at {updater.running_appimage()} (updates from Help → Check for Updates…)"
    if kind == "installed":
        return f"installed in {updater.app_dir()} (updates from Help → Check for Updates…)"
    return f"running from its source folder ({Path(__file__).resolve().parents[3]}; update with git pull)"


class AboutDialog(QDialog):
    def __init__(self, conn: sqlite3.Connection | None = None, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("About LuxTunes")
        self.resize(520, 460)
        tabs = QTabWidget()
        tabs.addTab(self._about_tab(), "About")
        tabs.addTab(self._licenses_tab(), "Licenses")
        tabs.addTab(self._system_tab(), "System Info")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)

    def _about_tab(self) -> QWidget:
        page = QWidget()
        icon = QLabel()
        icon.setPixmap(QPixmap(str(RESOURCES / "luxtunes-128.png")))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = QLabel("LuxTunes")
        font = name.font()
        font.setPointSizeF(font.pointSizeF() * 1.8)
        font.setBold(True)
        name.setFont(font)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel(
            f"<p>Version {__version__}</p>"
            f"<p>{DESCRIPTION}</p>"
            f"<p>{COPYRIGHT}</p>"
            "<p>LuxTunes is free software: you can redistribute it and/or modify it under the "
            "terms of the GNU General Public License, version 3 or (at your option) any later "
            "version. It comes with <b>absolutely no warranty</b>.</p>"
            "<p style='color: gray'>iPod is a trademark of Apple Inc. LuxTunes is an "
            "independent project and is not affiliated with or endorsed by Apple.</p>")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        text.setOpenExternalLinks(True)
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        layout.addWidget(icon)
        layout.addWidget(name)
        layout.addWidget(text)
        layout.addStretch(1)
        return page

    def _licenses_tab(self) -> QWidget:
        page = QWidget()
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        rows = "".join(
            f"<p><b>{name}</b> — {use}<br>{licence} · <a href='{url}'>{url}</a></p>"
            for name, use, licence, url in THIRD_PARTY)
        browser.setHtml(
            "<h3>LuxTunes</h3>"
            "<p>GNU General Public License v3.0 or later. The icon was made for LuxTunes "
            "and is under the same licence.</p>"
            "<h3>Built with</h3>" + rows)
        view = QPushButton("View License…")
        view.clicked.connect(self._show_license)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(view)
        layout = QVBoxLayout(page)
        layout.addWidget(browser, 1)
        layout.addLayout(row)
        return page

    def _show_license(self):
        path = RESOURCES / "LICENSE"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = "The licence text is at https://www.gnu.org/licenses/gpl-3.0.txt"
        dialog = QDialog(self)
        dialog.setWindowTitle("GNU General Public License v3")
        dialog.resize(640, 560)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(view, 1)
        layout.addWidget(buttons)
        dialog.exec()

    def _system_tab(self) -> QWidget:
        page = QWidget()
        self.info = system_info(self.conn)
        rows = "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in self.info)
        browser = QTextBrowser()
        browser.setHtml(f"<table cellspacing='4'>{rows}</table>")
        copy = QPushButton("Copy")
        copy.setToolTip("Copy these details, e.g. to paste into a bug report")
        copy.clicked.connect(self.copy_info)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(copy)
        layout = QVBoxLayout(page)
        layout.addWidget(browser, 1)
        layout.addLayout(row)
        return page

    def info_text(self) -> str:
        return "\n".join(f"{k}: {v}" for k, v in self.info)

    def copy_info(self):
        QApplication.clipboard().setText(self.info_text())
