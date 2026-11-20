"""Dialogs reopen on the tab you last used, until LuxTunes closes."""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget

_last_tab: dict[str, str] = {}      # dialog -> tab title, for this session only


def remember_tab(tabs: QTabWidget, dialog: str, default: str) -> None:
    """Open on the tab last used in this kind of dialog (this session), or
    `default`; and remember whichever tab the user picks."""
    wanted = _last_tab.get(dialog, default)
    for title in (wanted, default):
        for i in range(tabs.count()):
            if tabs.tabText(i) == title and tabs.isTabVisible(i):
                tabs.setCurrentIndex(i)
                break
        else:
            continue
        break

    def picked(index):
        if index >= 0:
            _last_tab[dialog] = tabs.tabText(index)
    tabs.currentChanged.connect(picked)
