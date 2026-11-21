"""Suggestions while typing in Get Info (one song or several) and in the
song list's cells: Artist, Album Artist, Featured Artist, Album, Composer,
Grouping and Genre offer what your library already has (type "Eme" and
"Emery" is offered). Genre also offers the genre catalogue's names
(core/genres.py), better-known genres first.

Featured Artist and Genre can hold several names, separated by commas (or
semicolons): only the name being typed, after the last separator, is
completed, and names already in the box aren't offered again.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QComboBox, QCompleter, QLineEdit, QStyledItemDelegate

from luxtunes.core import genres

SEPARATOR = re.compile(r"[;,]\s*")
MOST = 12
# Fields that get suggestions, and whether they hold several names.
FIELDS = {"artist": False, "album_artist": False, "featured_artist": True, "album": False,
          "composer": False, "grouping": False, "genre": True}


def _typed_part(text: str, several: bool) -> tuple[str, str]:
    """("Metalcore, ", "Po") for "Metalcore, Po": what's before the name being typed, and it."""
    found = list(SEPARATOR.finditer(text)) if several else []
    if not found:
        return "", text
    end = found[-1].end()
    return text[:end], text[end:]


def _matches(values: list[str], typed: str) -> list[str]:
    """Values starting with what's typed, then values with a later word
    starting with it ("beat" finds "The Beatles"); ignoring case and accents."""
    wanted = genres.key(typed)
    if not wanted:
        return []
    starts, words = [], []
    for value in values:
        if genres.key(value).startswith(wanted):
            starts.append(value)
        elif any(genres.key(w).startswith(wanted) for w in re.split(r"[\s\-/(]+", value)[1:]):
            words.append(value)
    return starts + words


class FieldCompleter(QCompleter):
    def __init__(self, values: list[str], several: bool = False, catalogue: bool = False,
                 parent=None):
        super().__init__(parent)
        self._values = values
        self._several = several
        self._catalogue = catalogue          # Genre: also the genre catalogue's names
        self._prefix = ""
        self._model = QStringListModel(self)
        self.setModel(self._model)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.setMaxVisibleItems(MOST)

    def update_for(self, text: str) -> bool:
        """Fill the list for what's typed; True if there's anything to show."""
        self._prefix, part = _typed_part(text, self._several)
        if not genres.key(part):
            self._model.setStringList([])
            return False
        seen = {genres.key(p) for p in SEPARATOR.split(self._prefix) if p.strip()}
        candidates = _matches(self._values, part)
        if self._catalogue:
            try:
                candidates += genres.catalogue().suggest(part, MOST)
            except (OSError, ValueError):      # no catalogue file: the library's genres still work
                pass
        found = []
        for name in candidates:
            k = genres.key(name)
            if k not in seen:
                seen.add(k)
                found.append(name)
            if len(found) == MOST:
                break
        if len(found) == 1 and found[0] == part.strip():   # exactly what's typed: nothing to offer
            found = []
        self._model.setStringList(found)
        return bool(found)

    def splitPath(self, path: str) -> list[str]:
        return [_typed_part(path, self._several)[1]]

    def pathFromIndex(self, index) -> str:
        return self._prefix + (index.data() or "")


def attach(widget: QLineEdit | QComboBox, field: str, values: list[str]) -> FieldCompleter:
    """Give a box for `field` (a key of FIELDS) suggestions as you type."""
    edit = widget.lineEdit() if isinstance(widget, QComboBox) else widget
    completer = FieldCompleter(values, FIELDS.get(field, False), field == "genre", widget)
    widget.setCompleter(completer)

    def typed(text):
        if completer.update_for(text):
            completer.complete()
        else:
            completer.popup().hide()
    edit.textEdited.connect(typed)
    return completer


class FieldDelegate(QStyledItemDelegate):
    """A song-list cell (Artist, Album, Genre...): editing it suggests too."""

    def __init__(self, field: str, values, parent=None):
        super().__init__(parent)
        self._field = field
        self._values = values               # a function: the library's values now

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setFrame(False)
        attach(editor, self._field, self._values())
        return editor
