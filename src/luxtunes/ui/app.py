"""Starts the LuxTunes window."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from luxtunes.core import db


def run(db_path: str | None = None) -> int:
    # Short status lines in the terminal (e.g. "repeat set to one"), handy for
    # reporting problems. Only a few events are logged, so it stays quiet.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    app = QApplication(sys.argv)
    app.setApplicationName("LuxTunes")
    app.setOrganizationName("LuxTunes")
    app.setDesktopFileName("luxtunes")
    from luxtunes.ui.about import app_icon
    app.setWindowIcon(app_icon())           # the penguin, in the taskbar and title bar

    # Imported here so `luxtunes import-itunes` works without loading Qt.
    from luxtunes.ui.main_window import MainWindow

    conn = db.connect(db_path)
    window = MainWindow(conn)
    window.show()
    code = app.exec()
    conn.close()
    return code
