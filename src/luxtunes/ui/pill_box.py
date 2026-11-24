"""A box of pills for fields holding several names: Get Info's Artists,
Featured Artist and Genre. Each name is a pill with a ✕; you type the next
one at the end, with suggestions from the library (ui/genre_completer.py).

- Enter, choosing a suggestion, or typing the separator (";" for names, ","
  or ";" for genres) turns what's typed into a pill. Pasting "A; B" makes two.
- Backspace in the empty space after the pills takes the last one back to
  edit; double-clicking a pill does the same for that one; ✕ removes it.
- ▾ lists everything the library has for the field.
- text() is the field as stored: names joined by "; " (genres by ", "),
  including a name still being typed, so nothing typed is lost.

It has the parts of QLineEdit's API that Get Info uses (setText, text,
textEdited, textChanged, setPlaceholderText), so it drops in for one.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QScrollArea,
                               QSizePolicy, QToolButton, QWidget)

from luxtunes.ui import genre_completer


class FlowLayout(QLayout):
    """Lays widgets out left to right, wrapping onto new rows (Qt's flow layout example)."""

    def __init__(self, parent=None, spacing: int = 4):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing

    def addItem(self, item):
        self._items.append(item)

    def insertWidget(self, index: int, widget: QWidget):
        self.addChildWidget(widget)
        from PySide6.QtWidgets import QWidgetItem
        self._items.insert(index, QWidgetItem(widget))
        self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, move=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, move: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        last = len(self._items) - 1
        for i, item in enumerate(self._items):
            hint = item.sizeHint()
            # The typing space (last) moves to a new row only below its minimum width.
            needed = item.minimumSize().width() if i == last else hint.width()
            if x + needed > area.right() + 1 and row_height:
                x, y, row_height = area.x(), y + row_height + self._spacing, 0
            width = hint.width()
            if i == last:                      # the typing space takes the rest of its row
                width = max(needed, area.right() + 1 - x)
            if move:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, hint.height())))
            x += width + self._spacing
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


class Pill(QFrame):
    """One name: its text and a ✕."""
    remove_clicked = Signal(object)
    edit_requested = Signal(object)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("pill")
        self.value = text
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 1, 2, 1)
        row.setSpacing(2)
        self.label = QLabel(text)
        self.label.setToolTip(f"{text}\nDouble-click to change it")
        close = QToolButton()
        close.setObjectName("pillClose")
        close.setText("✕")
        close.setAutoRaise(True)
        close.setCursor(Qt.CursorShape.ArrowCursor)
        close.setToolTip(f"Remove {text}")
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(lambda: self.remove_clicked.emit(self))
        row.addWidget(self.label)
        row.addWidget(close)

    def mouseDoubleClickEvent(self, event):
        self.edit_requested.emit(self)


STYLE = """
#pillBox { background: palette(base); border: 1px solid palette(mid); border-radius: 3px; }
#pillBox[focused="true"] { border-color: palette(highlight); }
#pill { background: palette(button); border: 1px solid palette(mid); border-radius: 9px; }
#pillClose { border: none; padding: 0 2px; font-size: 9px; color: palette(mid); }
#pillClose:hover { color: palette(text); }
#pillBox QLineEdit { border: none; background: transparent; padding: 0; }
#pillMore { border: none; padding: 0 3px; }
#pillArea { background: transparent; }
"""
# Rows of pills shown before the box scrolls instead of growing further.
MAX_ROWS = 3


class PillBox(QFrame):
    textEdited = Signal(str)        # the user added, removed or typed a name
    textChanged = Signal(str)       # any change, also setText()

    def __init__(self, field: str, values: list[str] | None = None, parent=None):
        """`field`: a key of genre_completer.FIELDS ("artists", "featured_artist", "genre")."""
        super().__init__(parent)
        self.setObjectName("pillBox")
        self.setStyleSheet(STYLE)
        self.field = field
        self._genre = field == "genre"
        self._split = re.compile(r"[;,]") if self._genre else re.compile(r";")
        self._joiner = ", " if self._genre else "; "
        self._values = list(values or [])
        self.pills: list[Pill] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(3, 2, 2, 2)
        outer.setSpacing(2)
        self.area = QWidget()
        self.area.setObjectName("pillArea")
        self.flow = FlowLayout(self.area)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setMinimumWidth(60)
        self.flow.addWidget(self.edit)
        # The pills sit in a scroll area whose height _fit_height sets: the box grows a
        # row at a time (its new height then makes room in Get Info, which only goes
        # by fixed sizes), up to MAX_ROWS, and past that it scrolls.
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setWidget(self.area)
        outer.addWidget(self.scroll, 1)
        self.more = QToolButton()
        self.more.setObjectName("pillMore")
        self.more.setText("▾")
        self.more.setToolTip("Choose from your library")
        self.more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more.clicked.connect(self._show_all)
        outer.addWidget(self.more, 0, Qt.AlignmentFlag.AlignTop)

        self.completer = genre_completer.FieldCompleter(self._values, None, self._genre, self)
        self.edit.setCompleter(self.completer)
        self.completer.activated.connect(lambda _: QTimer.singleShot(0, self._commit_typed))
        # Enter while suggestions show but none is picked: add what's typed (the
        # completer alone would only close the list).
        self._popup = self.completer.popup()
        # Watch keys only now: the filter uses the completer, and adding the box to
        # the layout above already sends it events.
        self._popup.installEventFilter(self)
        self.edit.installEventFilter(self)
        self.edit.textEdited.connect(self._typed)

    # ---- QLineEdit-like API -------------------------------------------------------

    def text(self) -> str:
        names = [p.value for p in self.pills]
        typed = self.edit.text().strip()
        if typed:
            names += [n.strip() for n in self._split.split(typed)]
        return self._join(names)

    def setText(self, text: str | None):
        for pill in self.pills:
            self._drop(pill)
        self.pills = []
        self.edit.clear()
        for name in self._names(text):
            self._add_pill(name)
        self._after_change(edited=False)

    def values(self) -> list[str]:
        return self._names(self.text())

    def setPlaceholderText(self, text: str):
        self._placeholder = text
        self._update_placeholder()

    def setReadOnly(self, read_only: bool):
        self.edit.setReadOnly(read_only)
        self.more.setVisible(not read_only)

    # ---- Pills ----------------------------------------------------------------------

    def _names(self, text: str | None) -> list[str]:
        seen, out = set(), []
        for part in self._split.split(text or ""):
            name = " ".join(part.split())
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                out.append(name)
        return out

    def _join(self, names) -> str:
        return self._joiner.join(self._names(";".join(names)))

    def _add_pill(self, name: str):
        if any(p.value.casefold() == name.casefold() for p in self.pills):
            return
        pill = Pill(name)
        pill.remove_clicked.connect(self._remove)
        pill.edit_requested.connect(self._edit_pill)
        self.flow.insertWidget(len(self.pills), pill)
        # Shown now: Qt would only show it once LuxTunes is next idle, and until then
        # the layout counts it as taking no space, so _fit_height wouldn't see the new row.
        pill.show()
        self.pills.append(pill)

    def _drop(self, pill: Pill):
        """Take a pill out for good. Hidden at once: deleteLater alone would leave it
        on screen until the dialog closes (a dialog runs its own event loop, and
        deletions asked for before it started wait for it to end)."""
        self.flow.removeWidget(pill)
        pill.hide()          # (kept as a child until then: its own ✕ may still be mid-click)
        pill.deleteLater()

    def _remove(self, pill: Pill):
        self.pills.remove(pill)
        self._drop(pill)
        self._after_change(edited=True)
        self.edit.setFocus()

    def _edit_pill(self, pill: Pill):
        """Take a pill back into the typing space (the typed text becomes a pill first)."""
        self._commit_typed(edited=False)
        self.pills.remove(pill)
        self._drop(pill)
        self.edit.setText(pill.value)
        self.edit.setFocus()
        self._after_change(edited=True)

    def _commit_typed(self, edited: bool = True):
        typed = self.edit.text()
        names = [n for n in (" ".join(p.split()) for p in self._split.split(typed)) if n]
        if not names:
            return False
        self.edit.clear()
        for name in names:
            self._add_pill(name)
        self._after_change(edited=edited)
        return True

    def _typed(self, text: str):
        if self._split.search(text):          # "A;" or a pasted "A; B; C": pills
            *done, rest = self._split.split(text)
            for name in done:
                name = " ".join(name.split())
                if name:
                    self._add_pill(name)
            self.edit.setText(rest.lstrip())
            self.completer.popup().hide()
        else:
            self.completer.taken = [p.value for p in self.pills]
            if self.completer.update_for(text):
                self.completer.complete()
            else:
                self.completer.popup().hide()
        self._after_change(edited=True)

    def _show_all(self):
        taken = {p.value.casefold() for p in self.pills}
        values = [v for v in self._values if v.casefold() not in taken]
        if not values:
            return
        self.edit.setFocus()
        self.completer.show_all(values)

    def _fit_height(self):
        """As tall as the pills need, up to MAX_ROWS rows; then a scroll bar."""
        row = max(self.edit.sizeHint().height(), self.fontMetrics().height() + 8)
        most = MAX_ROWS * row + (MAX_ROWS - 1) * self.flow._spacing
        width = self.scroll.viewport().width()
        if width < 60:                     # not laid out yet: guess from the box itself
            width = max(60, self.width() - self.more.sizeHint().width() - 8)
        needed = max(row, self.flow.heightForWidth(width))
        self.scroll.setFixedHeight(min(needed, most))
        if needed > most and self.edit.hasFocus():       # keep the typing space in view
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.edit))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._fit_height()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_height()

    def _after_change(self, edited: bool):
        self._update_placeholder()
        self.area.updateGeometry()
        self._fit_height()
        self.updateGeometry()
        text = self.text()
        self.textChanged.emit(text)
        if edited:
            self.textEdited.emit(text)

    def _update_placeholder(self):
        self.edit.setPlaceholderText("" if self.pills else getattr(self, "_placeholder", ""))

    # ---- Keys and focus -------------------------------------------------------------

    def eventFilter(self, watched, event):
        if getattr(self, "_popup", None) is None:        # still being built
            return False
        if watched is self._popup and event.type() == QEvent.Type.KeyPress \
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not watched.currentIndex().isValid():
            watched.hide()
            self._commit_typed()
            return True
        if watched is self.edit:
            if event.type() == QEvent.Type.KeyPress:
                key = event.key()
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.edit.text().strip():
                    if not self._popup.isVisible():
                        self._commit_typed()
                        return True        # a name was added; the dialog stays open
                if key == Qt.Key.Key_Backspace and not self.edit.text() and self.pills:
                    self._edit_pill(self.pills[-1])
                    return True
            elif event.type() == QEvent.Type.FocusIn:
                self.setProperty("focused", True)
                self.style().polish(self)
            elif event.type() == QEvent.Type.FocusOut:
                self.setProperty("focused", False)
                self.style().polish(self)
                if not self._popup.isVisible():
                    self._commit_typed()
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        self.edit.setFocus()                  # clicking the empty space types there
        super().mousePressEvent(event)
