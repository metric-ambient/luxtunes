"""New Smart Playlist… / Edit Smart Playlist:

    [x] Match [all ▾] of the following rules:
        [Field ▾] [operator ▾] [value]            [-] [+] […]
        [Match any ▾ of the following rules:]      [-] [+] […]   ← a nested group ("…")
            [Field ▾] [operator ▾] [value]        [-] [+] […]
    [ ] Limit to [25] [items ▾] selected by [random ▾]
    [ ] Match only checked items
    [x] Live updating
    [?]                                            [OK] [Cancel]

The value box changes with the field and operator: text, a number, stars,
a time (m:ss), a size in MB, a date, "in the last N weeks", "in the range
… to …", a media kind, or another playlist.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, time as dtime

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from luxtunes.core import smart
from luxtunes.core.smart import Group, Rule

FIELD_ORDER = sorted(smart.FIELDS.values(), key=lambda f: f.label.casefold())
STARS = ["", "★", "★★", "★★★", "★★★★", "★★★★★"]


def _day_start(qdate: QDate) -> int:
    return int(datetime.combine(date(qdate.year(), qdate.month(), qdate.day()), dtime.min).timestamp())


def _day_end(qdate: QDate) -> int:
    return _day_start(qdate) + 86399


def _qdate(seconds) -> QDate:
    when = datetime.fromtimestamp(seconds) if seconds else datetime.now()
    return QDate(when.year, when.month, when.day)


def _parse_time(text: str) -> int:
    """"3:25" (or "1:02:03", or "205") -> milliseconds."""
    seconds = 0
    for part in (text or "0").strip().split(":"):
        seconds = seconds * 60 + (int(part) if part.strip().isdigit() else 0)
    return seconds * 1000


def _format_time(ms) -> str:
    seconds = int(ms or 0) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def _small_button(text: str, tip: str) -> QToolButton:
    button = QToolButton()
    button.setText(text)
    button.setToolTip(tip)
    button.setFixedWidth(26)
    return button


class ValueEditor(QWidget):
    """One value box for a field kind (two, with "to", for a range)."""

    def __init__(self, kind: str, op: int, rule: Rule, playlists: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.kind, self.op = kind, op
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.first = self.second = self.units = None
        if kind == smart.BOOL:
            row.addStretch(1)
            return
        if kind == smart.DATE and op in (smart.IN_LAST, smart.NOT_IN_LAST):
            self.first = QSpinBox()
            self.first.setRange(1, 9999)
            self.first.setValue(int(rule.amount or 2))
            self.units = QComboBox()
            for seconds, label in smart.DATE_UNITS:
                self.units.addItem(label, seconds)
            index = self.units.findData(rule.units)
            if index < 0:                            # an unusual unit (from an imported playlist): keep it
                self.units.addItem(f"× {rule.units} seconds", rule.units)
                index = self.units.count() - 1
            self.units.setCurrentIndex(index)
            row.addWidget(self.first)
            row.addWidget(self.units, 1)
            return
        self.first = self._box(rule.value, playlists)
        row.addWidget(self.first, 1)
        if op == smart.RANGE:
            row.addWidget(QLabel("to"))
            self.second = self._box(rule.value2 if rule.value2 is not None else rule.value, playlists)
            row.addWidget(self.second, 1)

    def _box(self, value, playlists):
        kind = self.kind
        if kind == smart.TEXT:
            box = QLineEdit(value or "")
        elif kind == smart.NUMBER:
            box = QSpinBox()
            box.setRange(0, 99_999_999)
            box.setValue(int(value or 0))
        elif kind == smart.SIZE:                      # shown in MB, kept in bytes
            box = QSpinBox()
            box.setRange(0, 9_999_999)
            box.setSuffix(" MB")
            box.setValue(int(value or 0) // (1024 * 1024))
        elif kind == smart.TIME:
            box = QLineEdit(_format_time(value))
            box.setPlaceholderText("m:ss")
        elif kind == smart.RATING:
            box = QComboBox()
            for stars, label in enumerate(STARS):
                box.addItem(label or "(none)", stars * 20)
            box.setCurrentIndex(max(0, min(5, round(int(value or 0) / 20))))
        elif kind == smart.DATE:
            box = QDateEdit(_qdate(value))
            box.setCalendarPopup(True)
            box.setDisplayFormat("yyyy-MM-dd")
        elif kind == smart.MEDIA:
            box = QComboBox()
            for mask, label in smart.MEDIA_KINDS:
                box.addItem(label, mask)
            box.setCurrentIndex(max(0, box.findData(int(value or 1))))
        else:                                        # PLAYLIST
            box = QComboBox()
            for persistent_id, name in playlists:
                box.addItem(name, persistent_id)
            index = box.findData((value or "").upper())
            if index < 0 and value:
                box.addItem("(a playlist that's gone)", value)
                index = box.count() - 1
            box.setCurrentIndex(max(0, index))
        return box

    def _read(self, box, end_of_day=False):
        kind = self.kind
        if kind == smart.TEXT:
            return box.text()
        if kind == smart.NUMBER:
            return box.value()
        if kind == smart.SIZE:
            return box.value() * 1024 * 1024
        if kind == smart.TIME:
            return _parse_time(box.text())
        if kind == smart.DATE:
            return _day_end(box.date()) if end_of_day else _day_start(box.date())
        return box.currentData()

    def fill(self, rule: Rule):
        """Put the edited value(s) into the rule."""
        if self.kind == smart.BOOL:
            rule.value = 1
            return
        if self.units is not None:
            rule.amount, rule.units = self.first.value(), self.units.currentData()
            return
        if self.kind == smart.DATE:
            # Stored as: "is after" = end of that day, "is" = the whole day.
            if self.op == smart.GREATER:
                rule.value = rule.value2 = self._read(self.first, end_of_day=True)
            elif self.op == smart.RANGE:
                rule.value, rule.value2 = self._read(self.first), self._read(self.second, True)
            elif self.op in (smart.IS, smart.IS_NOT):
                rule.value, rule.value2 = self._read(self.first), self._read(self.first, True)
            else:
                rule.value = rule.value2 = self._read(self.first)
            return
        rule.value = self._read(self.first)
        rule.value2 = self._read(self.second) if self.second is not None else None


class RuleRow(QWidget):
    """[Field] [operator] [value] [-] [+] […] for one rule."""

    def __init__(self, rule: Rule, group: "GroupBox", playlists, parent=None):
        super().__init__(parent)
        self.rule = copy.deepcopy(rule)
        self.group = group
        self.playlists = playlists
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.field = QComboBox()
        self.op = QComboBox()
        self.value_slot = QHBoxLayout()
        self.value_slot.setContentsMargins(0, 0, 0, 0)
        self.editor: ValueEditor | None = None
        row.addWidget(self.field, 2)
        row.addWidget(self.op, 2)
        row.addLayout(self.value_slot, 3)
        self.minus, self.plus, self.more = (_small_button("-", "Remove this rule"),
                                            _small_button("+", "Add a rule"),
                                            _small_button("…", "Add a group of rules (match any or all of them)"))
        for button in (self.minus, self.plus, self.more):
            row.addWidget(button)
        self.minus.clicked.connect(lambda: group.remove(self))
        self.plus.clicked.connect(lambda: group.add_rule_after(self))
        self.more.clicked.connect(lambda: group.add_group_after(self))

        if not self.rule.known:                     # kept exactly as it was imported
            self.field.addItem("(a rule LuxTunes doesn't know)")
            self.field.setEnabled(False)
            self.op.setEnabled(False)
            note = QLabel("kept as it is")
            note.setEnabled(False)
            self.value_slot.addWidget(note)
            return
        for f in FIELD_ORDER:
            self.field.addItem(f.label, f.id)
        self.field.setCurrentIndex(self.field.findData(self.rule.field))
        self._fill_ops()
        self.field.currentIndexChanged.connect(self._field_changed)
        self.op.currentIndexChanged.connect(self._op_changed)

    @property
    def kind(self) -> str:
        return smart.FIELDS[self.rule.field].kind

    def _fill_ops(self):
        self.op.blockSignals(True)
        self.op.clear()
        for code, label in smart.OPERATORS[self.kind]:
            self.op.addItem(label, code)
        index = self.op.findData(self.rule.op)
        if index < 0 and self.kind == smart.MEDIA:     # older playlists: "is"/"is not" as numbers
            index = 1 if self.rule.op & 0x02000000 else 0
        self.op.setCurrentIndex(max(0, index))
        self.rule.op = smart.OPERATORS[self.kind][max(0, index)][0]
        self.op.blockSignals(False)
        self._make_editor()

    def _make_editor(self):
        if self.editor is not None:
            self.editor.setParent(None)
            self.editor.deleteLater()
        self.editor = ValueEditor(self.kind, self.rule.op, self.rule, self.playlists)
        self.value_slot.addWidget(self.editor)

    def _field_changed(self):
        old_kind = smart.FIELDS[self.rule.field].kind if self.rule.field in smart.FIELDS else None
        self.rule.field = self.field.currentData()
        if self.kind != old_kind:                   # a different sort of value: start fresh
            self.rule.value = self.rule.value2 = None
            self.rule.op = smart.OPERATORS[self.kind][0][0]
            if self.kind == smart.DATE:
                self.rule.op, self.rule.amount, self.rule.units = smart.IN_LAST, 2, smart.WEEK
        self._fill_ops()

    def _op_changed(self):
        self.rule.op = self.op.currentData()
        self._make_editor()

    def to_rule(self) -> Rule:
        if not self.rule.known:
            return self.rule
        self.rule.field = self.field.currentData()
        self.rule.op = self.op.currentData()
        self.editor.fill(self.rule)
        return self.rule


class GroupBox(QFrame):
    """A list of rules (and nested groups). A nested one has its own
    "Match any/all of the following rules" line with - + … buttons."""

    def __init__(self, group: Group, playlists, parent_group: "GroupBox | None" = None, parent=None):
        super().__init__(parent)
        self.parent_group = parent_group
        self.playlists = playlists
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        self.match = QComboBox()
        self.match.addItem("all", False)
        self.match.addItem("any", True)
        self.match.setCurrentIndex(1 if group.match_any else 0)
        if parent_group is not None:
            header = QHBoxLayout()
            header.addWidget(QLabel("Match"))
            header.addWidget(self.match)
            header.addWidget(QLabel("of the following rules:"))
            header.addStretch(1)
            self.minus, self.plus, self.more = (_small_button("-", "Remove this group"),
                                                _small_button("+", "Add a rule"),
                                                _small_button("…", "Add a group of rules"))
            for button in (self.minus, self.plus, self.more):
                header.addWidget(button)
            self.minus.clicked.connect(lambda: parent_group.remove(self))
            self.plus.clicked.connect(lambda: parent_group.add_rule_after(self))
            self.more.clicked.connect(lambda: parent_group.add_group_after(self))
            layout.addLayout(header)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        layout.addLayout(self.rows)
        self.items: list[QWidget] = []
        for rule in group.rules or [Rule(0x04, smart.S_CONTAINS, "")]:
            self._insert(len(self.items), rule)
        self._update_minus()

    def _insert(self, index: int, rule):
        if isinstance(rule, Group):
            widget = GroupBox(rule, self.playlists, self)
            widget.setContentsMargins(18, 0, 0, 0)       # indented under its parent
        else:
            widget = RuleRow(rule, self, self.playlists)
        self.items.insert(index, widget)
        self.rows.insertWidget(index, widget)
        return widget

    def add_rule_after(self, widget):
        """+: a new rule just below, about the same field."""
        like = widget.to_rule() if isinstance(widget, RuleRow) else None
        if like is not None and like.known:
            kind = smart.FIELDS[like.field].kind
            new = Rule(like.field, smart.OPERATORS[kind][0][0])
            if kind == smart.DATE:
                new.op, new.amount, new.units = smart.IN_LAST, 2, smart.WEEK
        else:
            new = Rule(0x04, smart.S_CONTAINS, "")
        self._insert(self.items.index(widget) + 1, new)
        self._update_minus()

    def add_group_after(self, widget):
        """…: a nested group of rules just below."""
        self._insert(self.items.index(widget) + 1,
                     Group(True, [Rule(0x04, smart.S_CONTAINS, "")]))
        self._update_minus()

    def remove(self, widget):
        if len(self.items) <= 1 and self.parent_group is None:
            return                                   # the playlist keeps at least one rule
        self.items.remove(widget)
        widget.setParent(None)
        widget.deleteLater()
        if not self.items and self.parent_group is not None:
            self.parent_group.remove(self)           # an empty group goes too
            return
        self._update_minus()

    def _update_minus(self):
        if self.parent_group is None:
            alone = len(self.items) == 1
            first = self.items[0]
            if isinstance(first, RuleRow):
                first.minus.setEnabled(not alone)
            for item in self.items[1:]:
                if isinstance(item, RuleRow):
                    item.minus.setEnabled(True)

    def to_group(self) -> Group:
        rules = []
        for item in self.items:
            rules.append(item.to_group() if isinstance(item, GroupBox) else item.to_rule())
        return Group(bool(self.match.currentData()), rules)


class SmartPlaylistDialog(QDialog):
    def __init__(self, playlist: smart.SmartPlaylist, title: str,
                 playlists: list[tuple[str, str]], parent=None):
        """`playlists`: (persistent id, name) of the ones a "Playlist is" rule can name."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(680)
        info = playlist.info

        self.use_rules = QCheckBox("Match")
        self.use_rules.setChecked(info.use_rules)
        self.rules = GroupBox(playlist.rules, playlists)
        top = QHBoxLayout()
        top.addWidget(self.use_rules)
        top.addWidget(self.rules.match)
        top.addWidget(QLabel("of the following rules:"))
        top.addStretch(1)
        self.use_rules.toggled.connect(self.rules.setEnabled)
        self.rules.setEnabled(info.use_rules)

        self.limit = QCheckBox("Limit to")
        self.limit.setChecked(info.limit)
        self.limit_value = QSpinBox()
        self.limit_value.setRange(1, 999_999)
        self.limit_value.setValue(max(1, info.limit_value))
        self.limit_type = QComboBox()
        for code, label in smart.LIMIT_TYPES:
            self.limit_type.addItem(label, code)
        self.limit_type.setCurrentIndex(max(0, self.limit_type.findData(info.limit_type)))
        self.limit_sort = QComboBox()
        for code, reverse, label in smart.LIMIT_SORTS:
            self.limit_sort.addItem(label)
        index = next((i for i, (code, reverse, _) in enumerate(smart.LIMIT_SORTS)
                      if code == info.limit_sort and reverse == info.reverse), 0)
        self.limit_sort.setCurrentIndex(index)
        limit_row = QHBoxLayout()
        for widget in (self.limit, self.limit_value, self.limit_type, QLabel("selected by"),
                       self.limit_sort):
            limit_row.addWidget(widget)
        limit_row.addStretch(1)
        for widget in (self.limit_value, self.limit_type, self.limit_sort):
            widget.setEnabled(info.limit)
            self.limit.toggled.connect(widget.setEnabled)

        self.checked_only = QCheckBox("Match only checked items")
        self.checked_only.setChecked(info.checked_only)
        self.live = QCheckBox("Live updating")
        self.live.setChecked(info.live)

        help_button = QPushButton("?")
        help_button.setFixedWidth(28)
        help_button.clicked.connect(self._help)
        ok, cancel = QPushButton("OK"), QPushButton("Cancel")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(help_button)
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.rules)
        layout.addLayout(limit_row)
        layout.addWidget(self.checked_only)
        layout.addWidget(self.live)
        layout.addLayout(buttons)

    def _help(self):
        QMessageBox.information(
            self, "Smart Playlists",
            "A smart playlist fills itself with the songs that match its rules.\n\n"
            "• Match all: a song must match every rule. Match any: one is enough.\n"
            "• + adds a rule, - removes one, … adds a group of rules with its own all/any "
            "(e.g. Year is in the range 1990 to 1999, and Media Kind is Music or Music Video).\n"
            "• Limit to keeps only some of the matching songs, picked by the \"selected by\" order.\n"
            "• Live updating keeps the playlist up to date as your library changes; without it, "
            "the songs only change when you edit the rules.")

    def playlist(self) -> smart.SmartPlaylist:
        code, reverse, _ = smart.LIMIT_SORTS[max(0, self.limit_sort.currentIndex())]
        info = smart.Info(live=self.live.isChecked(), use_rules=self.use_rules.isChecked(),
                          limit=self.limit.isChecked(), limit_type=self.limit_type.currentData(),
                          limit_sort=code, limit_value=self.limit_value.value(),
                          checked_only=self.checked_only.isChecked(), reverse=reverse)
        return smart.SmartPlaylist(info, self.rules.to_group())
