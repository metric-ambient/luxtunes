"""Colour themes: View → Appearance…, as in the LuxTunes app for Android.

Three styles (Dark, Material, Light), each with three colours you choose:
background, text and accent. Every other colour (bars, the sidebar, row
stripes, buttons, dimmed text) is mixed from those, the same way the app
mixes its own (its LuxPalette), so any choice stays readable. Material has
an accent-coloured toolbar, as the app has an accent-coloured tab bar.
With Auto Accent, the accent comes from the playing song's album art
(accent_from_image, the app's AccentPicker), when the art has a colour that
stands out on the background.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from string import Template

import numpy as np
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

STYLES = ("dark", "material", "light")
STYLE_NAMES = {"dark": "Dark", "material": "Material", "light": "Light"}
LUX_BLUE = "#0099CC"
# (background, text, accent): the app's defaults. Light's background is the
# soft white LuxTunes has always used for row stripes.
DEFAULTS = {"dark": ("#000000", "#FFFFFF", LUX_BLUE),
            "material": ("#303030", "#FFFFFF", LUX_BLUE),
            "light": ("#F1F5FA", "#000000", LUX_BLUE)}
# The suggestions in the colour picker, as in the app.
ACCENT_SUGGESTIONS = ("#0099CC", "#1E88E5", "#00ACC1", "#26A69A", "#43A047",
                      "#FDD835", "#FB8C00", "#E53935", "#D81B60", "#8E24AA")
BACKGROUND_SUGGESTIONS = ("#000000", "#121212", "#1E1F22", "#303030", "#1B2631",
                          "#FFFFFF", "#F1F5FA", "#F5F5F5", "#ECEFF1", "#FFF8E1")
TEXT_SUGGESTIONS = ("#FFFFFF", "#EEEEEE", "#BDBDBD", "#E4E4E4", "#E3F2FD",
                    "#000000", "#212121", "#424242", "#37474F", "#3E2723")


@dataclass(frozen=True)
class Look:
    style: str = "material"
    colors: tuple = ()               # ((background, text, accent) per style, in STYLES order)
    auto_accent: bool = True

    def of(self, style: str) -> tuple[str, str, str]:
        i = STYLES.index(style)
        return self.colors[i] if len(self.colors) == len(STYLES) else DEFAULTS[style]

    @property
    def current(self) -> tuple[str, str, str]:
        return self.of(self.style)

    def with_colors(self, style: str, colors: tuple[str, str, str]) -> "Look":
        all_ = [self.of(s) for s in STYLES]
        all_[STYLES.index(style)] = tuple(colors)
        return replace(self, colors=tuple(all_))


def load_look(settings) -> Look:
    """The chosen look, from LuxTunes' settings. Material to start with. Before
    View → Appearance there was only View → Dark Mode: if that was set, it
    becomes the Dark or Light style."""
    style = settings.value("appearance/style", None)
    if style not in STYLES:
        old = settings.value("dark_mode", None)
        style = "material" if old is None else \
            "dark" if str(old).lower() in ("true", "1") else "light"
    colors = []
    for s in STYLES:
        default = DEFAULTS[s]
        colors.append(tuple(_valid(settings.value(f"appearance/{s}/{slot}", None), d)
                            for slot, d in zip(("background", "text", "accent"), default)))
    return Look(style, tuple(colors), settings.value("appearance/auto_accent", True, type=bool))


def save_look(settings, look: Look) -> None:
    settings.setValue("appearance/style", look.style)
    settings.setValue("appearance/auto_accent", look.auto_accent)
    for s in STYLES:
        for slot, value in zip(("background", "text", "accent"), look.of(s)):
            settings.setValue(f"appearance/{s}/{slot}", value)


def _valid(value, default: str) -> str:
    return value.upper() if isinstance(value, str) and QColor.isValidColorName(value) \
        and value.startswith("#") and len(value) == 7 else default


# ---- Mixing colours (as the app's LuxPalette) -----------------------------------------------

def mix(a: str, b: str, t: float) -> str:
    """From a (t = 0) towards b (t = 1)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t),
                  round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def luminance(color: str) -> float:
    """Relative luminance (0 black … 1 white), as contrast ratios use."""
    c = QColor(color)

    def ch(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def on_color(color: str) -> str:
    """Text and icons drawn on top of `color` (the accent): black or white."""
    return "#000000" if luminance(color) > 0.4 else "#FFFFFF"


def build_colors(style: str, background: str, text: str, accent: str) -> dict:
    """Every colour the stylesheet uses, from a style's three."""
    light = luminance(background) > 0.5
    bar = mix(background, text, 0.075 if light else 0.12)          # chrome: the window, headers
    panel = mix(background, text, 0.12 if light else 0.17)         # the display, menus
    raised = mix(background, text, 0.15 if light else 0.25)        # buttons that aren't the accent
    border = mix(background, text, 0.2 if light else 0.24)
    on_accent = on_color(accent)
    material = style == "material"
    # Dark: the background colour is the frame (toolbar, headers) and the lists
    # sit on the slightly lifted shade. Light and Material keep the app's way:
    # the background under the lists, the frame a shade off it.
    frame, content = (background, bar) if style == "dark" else (bar, background)
    toolbar = frame                      # (Material shows the accent on the chosen tab instead)
    toolbar_text = text
    # The chosen library tab and view: an accent bar along the bottom (Dark,
    # Light), or the whole box in the accent (Material).
    tab_checked = (f"background: {accent}; color: {on_accent}; border-radius: 6px;" if material
                   else f"background: transparent; color: {text}; border-radius: 0px; "
                        f"border-bottom: 3px solid {accent};")
    return {
        "light": light,
        "window": frame,
        "text": text,
        "text_dim": mix(text, content, 0.42 if light else 0.31),
        "text_disabled": mix(content, text, 0.38),
        "indicator_border": mix(text, content, 0.45),
        "base": content,
        "alt_base": mix(content, text, 0.035 if light else 0.05),
        "list_bg": mix(content, text, 0.03 if light else 0.06),
        "list_alt": mix(content, text, 0.065 if light else 0.1),
        "list_border": border,
        "now_playing": mix(content, text, 0.1),
        "now_playing_text": accent,
        "highlight": accent,
        "highlight_text": on_accent,
        "toolbar_top": toolbar,
        "toolbar_bottom": toolbar,
        "toolbar_border": mix(toolbar, text, 0.2) if not material else mix(accent, "#000000", 0.25),
        "toolbar_text": toolbar_text,
        "tab_checked": tab_checked,
        "tab_on_text": on_accent if material else text,
        "media_bar": frame,
        "button_top": raised,
        "button_bottom": raised,
        "button_pressed": mix(raised, text, 0.15),
        "button_checked": mix(raised, text, 0.25),
        "button_border": mix(background, text, 0.32),
        "play_bg": accent,
        "play_fg": on_accent,
        "lcd_top": panel,
        "lcd_bottom": panel,
        "lcd_border": border,
        "lcd_text": text,
        "sidebar": mix(background, text, 0.04 if light else 0.07),
        "sidebar_sel_top": accent,
        "sidebar_sel_bottom": accent,
        "sidebar_sel_inactive": mix(background, text, 0.18),
        "header_top": frame,
        "header_bottom": frame,
        "border": border,
        "bottom_bar": mix(background, text, 0.11 if light else 0.18),
        "track": mix(frame, text, 0.3),
        "panel_bg": panel,
        "warning": "#B3261E" if light else "#FFD54F",
        "art_header": frame,
        "art_bg": content,
        "overlay": "rgba({}, {}, {}, 225)".format(*QColor(background).getRgb()[:3]),
        "overlay_border": "rgba({}, {}, {}, 50)".format(*QColor(text).getRgb()[:3]),
        "grid_bg": content,
    }


current: dict = build_colors("material", *DEFAULTS["material"])     # the colours in use


# ---- Auto Accent (the app's AccentPicker) ----------------------------------------------------

MIN_CONTRAST = 3.0          # against the background: the usual minimum for icons and lines
_MIN_SHARE = 0.003          # specks smaller than this part of the picture don't count
_COLORFUL = 0.15            # chroma that counts as a colour rather than a grey


def accent_from_image(image, background: str) -> str | None:
    """An accent from album art (a QImage) that stands out on `background`:
    the cover's most prominent colour if it's colourful and readable, else its
    strongest readable splash of colour, else None (only greys: the style's
    own accent is used). Never a grey, so the accent can't look "off"."""
    if image is None or image.isNull():
        return None
    from PySide6.QtGui import QImage
    small = image.scaled(96, 96).convertToFormat(QImage.Format.Format_RGB888)
    w, h = small.width(), small.height()
    raw = np.frombuffer(bytes(small.constBits()), dtype=np.uint8)
    pixels = raw.reshape(h, small.bytesPerLine())[:, :w * 3].reshape(-1, 3).astype(np.int64)
    if not len(pixels):
        return None
    bins = ((pixels[:, 0] >> 5) << 6) | ((pixels[:, 1] >> 5) << 3) | (pixels[:, 2] >> 5)
    count = np.bincount(bins, minlength=512)
    total = count.sum()
    swatches = []
    for i in np.nonzero(count)[0]:
        n = count[i]
        rgb = pixels[bins == i].sum(axis=0) // n
        chroma = (rgb.max() - rgb.min()) / 255
        bright = rgb.max() / 255
        share = n / total
        score = share ** 0.5 * (0.12 + chroma) * (0.3 if bright < 0.2 else 1.0)
        swatches.append((QColor(*map(int, rgb)).name(), share, chroma, score))
    colorful = [s for s in swatches if s[2] >= _COLORFUL and s[1] >= _MIN_SHARE]
    if not colorful:
        return None
    prominent = max(colorful, key=lambda s: s[3])
    if contrast(prominent[0], background) >= MIN_CONTRAST:
        return prominent[0].upper()
    readable = [s for s in colorful if contrast(s[0], background) >= MIN_CONTRAST]
    if readable:
        return max(readable, key=lambda s: s[1] ** 0.5 * s[2])[0].upper()
    return None


STYLESHEET = Template("""
QWidget#toolbar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $toolbar_top, stop:1 $toolbar_bottom);
    border-bottom: 1px solid $toolbar_border;
}
QToolButton#transport {
    background: transparent;
    border: none;
    border-radius: 16px;
    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
}
QToolButton#transport:hover { background: $button_top; }
QToolButton#libraryTab, QToolButton#viewTab {
    background: transparent; border: none; border-radius: 6px; color: $text_dim;
    font-size: 11px; padding: 2px 0px 3px 0px;
}
QToolButton#viewTab { font-size: 10px; }
QToolButton#libraryTab:hover:!checked, QToolButton#viewTab:hover:!checked { background: $button_top; }
QToolButton#libraryTab:checked, QToolButton#viewTab:checked { $tab_checked font-weight: bold; }
QWidget#mediaBar { background: $media_bar; border-top: 1px solid $toolbar_border; }
QWidget#titleBar { background: $toolbar_top; }
QLabel#titleText { color: $text_dim; }
QMenuBar#titleMenus { background: transparent; border: none; color: $text; }
QMenuBar#titleMenus::item { background: transparent; padding: 4px 8px; border-radius: 4px; }
QMenuBar#titleMenus::item:selected { background: $button_top; }
QToolButton#windowButton, QToolButton#closeButton { background: transparent; border: none; }
QToolButton#windowButton:hover { background: $button_top; }
QToolButton#closeButton:hover { background: #C42B1C; }
QMainWindow { background: $toolbar_top; }
QWidget#mediaBar QLabel { color: $text_dim; }
QLabel#libraryStats { font-size: 11px; }
QLabel#seekTime { font-size: 13px; }
QLabel#artThumb { background: $panel_bg; color: $text_dim; }
QToolButton#sleepToggle {
    background: transparent; border: none; border-radius: 6px; color: $text_dim;
    font-size: 11px; padding: 2px 0px;
}
QToolButton#sleepToggle:checked { color: $highlight; }
QToolButton#sleepToggle:hover { background: $button_top; }
QWidget#barActions, QWidget#barGroup { background: transparent; }
QWidget#barActions QPushButton {
    color: $text; border: 1px solid $button_border; border-radius: 8px; padding: 2px 12px;
    background: $button_top; min-height: 20px;
}
QWidget#barActions QPushButton:pressed { background: $button_pressed; }
QWidget#barActions QPushButton:disabled { color: $text_disabled; }
QWidget#barActions QToolButton#dupButton { padding: 2px 12px; min-height: 20px; }
QFrame#sleepPopup {
    background: $media_bar; border: 1px solid $toolbar_border; border-radius: 8px;
}
QLabel#sleepPopupTitle { color: $text_dim; font-size: 11px; font-weight: bold; }
QToolButton#barToggle {
    background: transparent; border: none; border-radius: 6px;
    min-width: 32px; min-height: 32px; icon-size: 22px;
}
QToolButton#barToggle:hover { background: $button_top; }
QToolButton#sidebarHeaderButton { border: none; background: transparent; border-radius: 4px; }
QToolButton#sidebarHeaderButton:hover { background: $sidebar_sel_inactive; }
QSlider#seekSlider::groove:horizontal, QSlider#volumeSlider::groove:horizontal {
    height: 4px; background: $track; border-radius: 2px;
}
QSlider#seekSlider::sub-page:horizontal, QSlider#volumeSlider::sub-page:horizontal {
    background: $text; border-radius: 2px;
}
QSlider#seekSlider::sub-page:horizontal:hover, QSlider#volumeSlider::sub-page:horizontal:hover {
    background: $highlight;
}
QSlider#seekSlider::handle:horizontal, QSlider#volumeSlider::handle:horizontal {
    width: 12px; margin: -4px 0; background: $text; border-radius: 6px;
}
QToolButton#transport:pressed { background: $button_pressed; }
QWidget#toolbar > QLabel { color: $toolbar_text; }
QWidget#toolbar > QSlider::groove:horizontal {
    height: 4px; background: $toolbar_border; border-radius: 2px;
}
QWidget#toolbar > QSlider::sub-page:horizontal { background: $toolbar_text; border-radius: 2px; }
QWidget#toolbar > QSlider::handle:horizontal {
    width: 12px; margin: -4px 0; background: $toolbar_text; border-radius: 6px;
}
QToolButton#sleepButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $button_top, stop:1 $button_bottom);
    border: 1px solid $button_border;
    border-radius: 11px;
    padding: 2px 8px;
    color: $text;
}
QToolButton#sleepButton:hover { border-color: $highlight; }
QToolButton#playButton {
    background: $play_bg;
    border: none;
    border-radius: 20px;
    min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px;
}
QToolButton#playButton:pressed { background: $button_pressed; }
QToolButton#viewButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $button_top, stop:1 $button_bottom);
    border: 1px solid $button_border;
    border-left: none;
    min-width: 28px; min-height: 20px;
    padding: 0px 2px;
}
QToolButton#viewButton[segment="first"] {
    border-left: 1px solid $button_border;
    border-top-left-radius: 4px; border-bottom-left-radius: 4px;
}
QToolButton#viewButton[segment="last"] {
    border-top-right-radius: 4px; border-bottom-right-radius: 4px;
}
QToolButton#viewButton:checked { background: $button_checked; }

QWidget#gridPage, QListView#grid { background: $grid_bg; border: none; }
QWidget#gridTabs { background: $grid_bg; border-bottom: 1px solid $grid_line; }
QToolButton#gridTab {
    color: $grid_tab_text; background: transparent; border: none;
    font-weight: bold; padding: 2px 10px; border-radius: 9px;
}
QToolButton#gridTab:checked { background: $grid_tab_checked; border: 1px solid $grid_tab_border; }
QToolButton#gridTab:hover:!checked { background: $grid_tab_hover; }

QWidget#devicePage { background: $base; }
QWidget#deviceTabs {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $header_top, stop:1 $header_bottom);
    border-bottom: 1px solid $border;
}
QToolButton#deviceTab {
    color: $text; font-weight: bold; border: none; padding: 1px 10px; border-radius: 9px;
    background: transparent;
}
QToolButton#deviceTab:checked { color: $highlight_text; background: $highlight; }
QFrame#deviceBox { background: $alt_base; border: 1px solid $border; }
/* The device page's lists: a frame and their own background, so each list
   stands out from the page (hard to tell apart on dark otherwise). */
QListWidget#deviceList {
    background: $list_bg; alternate-background-color: $list_alt; border: 1px solid $list_border;
    border-radius: 4px;
}
/* Checkboxes and radio buttons everywhere (dialogs, the iPod's pages, list
   checkboxes): a clear outline in both themes, filled blue when ticked. */
QCheckBox::indicator, QGroupBox::indicator, QAbstractItemView::indicator {
    width: 13px; height: 13px; border: 1px solid $indicator_border; border-radius: 3px;
    background: $base;
}
QRadioButton::indicator {
    width: 13px; height: 13px; border: 1px solid $indicator_border; border-radius: 7px;
    background: $base;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover, QGroupBox::indicator:hover,
QAbstractItemView::indicator:hover { border-color: $highlight; }
QCheckBox::indicator:checked, QGroupBox::indicator:checked, QAbstractItemView::indicator:checked {
    background: $highlight; border-color: $highlight; image: url("$check_icon");
}
QCheckBox::indicator:indeterminate, QAbstractItemView::indicator:indeterminate {
    border-color: $highlight;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $highlight, stop:0.4 $highlight,
                                stop:0.41 #ffffff, stop:0.59 #ffffff, stop:0.6 $highlight,
                                stop:1 $highlight);
}
/* The song list's checkboxes stay quiet: a plain box with a tick in the text colour. */
QTableView::indicator:checked {
    background: $base; border-color: $indicator_border; image: url("$list_check_icon");
}
QRadioButton::indicator:checked {
    border-color: $highlight;
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #ffffff,
                                stop:0.38 #ffffff, stop:0.48 $highlight, stop:1 $highlight);
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled, QGroupBox::indicator:disabled,
QAbstractItemView::indicator:disabled { border-color: $text_disabled; background: $window; }
QCheckBox::indicator:checked:disabled, QGroupBox::indicator:checked:disabled,
QAbstractItemView::indicator:checked:disabled { background: $text_disabled; }
QRadioButton::indicator:checked:disabled {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 $base,
                                stop:0.38 $base, stop:0.48 $text_disabled, stop:1 $text_disabled);
}
QLabel#deviceHeading, QLabel#deviceCount { color: $text_dim; }
QLabel#deviceNote { color: $text_dim; }
QWidget#capacityPanel {
    background: $bottom_bar;
    border-top: 1px solid $border;
}
QLabel#capacityWarning { color: $warning; font-weight: bold; }
QWidget#cdBar {
    background: $bottom_bar;
    border-top: 1px solid $border;
}
QLabel#cdStatus { color: $text; font-weight: bold; }
QPushButton#cdButton {
    color: $text; border: 1px solid $button_border; border-radius: 4px; padding: 3px 14px;
    background: $button_top;
}
QPushButton#cdButton:pressed { background: $button_pressed; }
QPushButton#cdButton:disabled { color: $text_disabled; }
QTableWidget#cdTracks {
    border: none;
    gridline-color: transparent;
    selection-background-color: $highlight;
    selection-color: $highlight_text;
    outline: 0;
}

QWidget#dupBar {
    background: $bottom_bar;
    border-top: 1px solid $border;
}
QLabel#dupLabel { color: $text; font-weight: bold; }
QToolButton#dupButton {
    color: $text; border: 1px solid $button_border; border-radius: 8px; padding: 1px 12px;
    background: $button_top;
}
QToolButton#dupButton:pressed { background: $button_pressed; }
QToolButton#dupButton:disabled { color: $text_disabled; }
QToolButton#dupButton:checked {          /* Radio's Discover while it's on */
    color: $highlight_text; border-color: $highlight; background: $highlight;
}

QWidget#drillBar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $header_top, stop:1 $header_bottom);
    border-bottom: 1px solid $border;
}
QToolButton#drillBack {
    color: $text; font-weight: bold; border: none; padding: 1px 8px;
    border-right: 1px solid $border;
}
QToolButton#drillBack:hover { background: $sidebar_sel_inactive; }
QLabel#drillTitle { color: $text; font-weight: bold; }

QWidget#coverFlow { background: black; }
QScrollBar#coverFlowScroll:horizontal {
    background: #111; border: 1px solid #555; border-radius: 7px; height: 14px;
}
QScrollBar#coverFlowScroll::handle:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5a5a5a, stop:1 #333);
    border: 1px solid #777; border-radius: 6px; min-width: 40px;
}
QScrollBar#coverFlowScroll::add-line, QScrollBar#coverFlowScroll::sub-line { width: 0px; }
QSplitter#songsSplit::handle { background: #2a2a2a; height: 5px; }

QFrame#lcd {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $lcd_top, stop:1 $lcd_bottom);
    border: 1px solid $lcd_border;
    border-radius: 4px;
}
QFrame#lcd QLabel { color: $lcd_text; background: transparent; }
QLabel#lcdIdle { font-size: 20px; font-weight: bold; }
QLabel#lcdTitle { font-weight: bold; }
QToolButton#lcdButton {
    color: $lcd_text; background: transparent; border: 1px solid $lcd_border; border-radius: 10px;
    font-weight: bold; padding: 0;
}
QToolButton#lcdButton:hover { background: rgba(127, 127, 127, 60); }
QProgressBar#lcdProgress {
    border: 1px solid $lcd_border; border-radius: 4px; background: transparent;
}
QProgressBar#lcdProgress::chunk { background: $lcd_text; border-radius: 3px; }
QSlider#lcdSlider::groove:horizontal {
    height: 6px; border: 1px solid $lcd_border; background: transparent; border-radius: 3px;
}
QSlider#lcdSlider::sub-page:horizontal { background: $lcd_text; border-radius: 3px; }
QSlider#lcdSlider::handle:horizontal {
    width: 8px; margin: -3px 0; background: $lcd_text; border-radius: 4px;
}

QLineEdit#search {
    border: 1px solid $button_border;
    border-radius: 17px;
    padding: 2px 10px;
    background: $base;
    color: $text;
    font-size: 14px;
}

QTreeWidget#sidebar {
    background: $sidebar;
    border: none;
    color: $text;
    outline: 0;
}
QTreeWidget#sidebar { show-decoration-selected: 0; }
QTreeWidget#sidebar::item { height: 20px; border-radius: 4px; }
QTreeWidget#sidebar::branch, QTreeWidget#sidebar::branch:selected { background: transparent; }
QTreeWidget#sidebar::item:selected:active {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $sidebar_sel_top, stop:1 $sidebar_sel_bottom);
    color: $highlight_text;
}
QTreeWidget#sidebar::item:selected:!active {
    background: $sidebar_sel_inactive;
    color: $text;
}

QTableView#tracks {
    border: none;
    gridline-color: transparent;
    selection-background-color: $highlight;
    selection-color: $highlight_text;
    outline: 0;
}
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $header_top, stop:1 $header_bottom);
    color: $text;
    border: none;
    border-right: 1px solid $border;
    border-bottom: 1px solid $border;
    padding: 1px 4px;
    font-weight: bold;
}

QWidget#leftPanel { background: $sidebar; border-right: 1px solid $border; }
QLabel#artHeader {
    background: $art_header;
    color: $text;
    border-top: 1px solid $border;
    font-weight: bold;
    padding: 1px;
}
QLabel#artImage { background: $art_bg; color: $text_dim; }
QWidget#sidebarButtons { background: $art_header; border-top: 1px solid $border; }
QToolButton#sidebarButton {
    border: none; color: $text;
    font-size: 17px;
    min-width: 30px; min-height: 26px;
    padding: 1px 6px;
}
QToolButton#sidebarButton:hover { background: $sidebar_sel_inactive; border-radius: 4px; }
QToolButton#sidebarButton:checked {
    background: $highlight; color: $highlight_text; border-radius: 4px;
}

QWidget#artWindow { background: $art_bg; }
QLabel#artWindowImage { background: $art_bg; color: $text_dim; }
QFrame#artControls {
    background: $overlay;
    border: 1px solid $overlay_border;
    border-radius: 9px;
}
QFrame#artControls QLabel { color: $text; background: transparent; }
QToolButton#overlayButton { border: none; padding: 4px; color: $text; }
QToolButton#overlayButton:checked { background: $highlight; color: $highlight_text; border-radius: 4px; }
QTextBrowser#artLyrics {
    background: rgba(0, 0, 0, 175);
    color: #f2f2f2;
    border: none;
    padding: 28px 36px 110px 36px;
    font-size: 15pt;
}
QToolButton#overlayButton:hover { background: $overlay_border; border-radius: 4px; }

QStatusBar { background: $toolbar_bottom; color: $text; border-top: 1px solid $border; }
QSplitter::handle { background: $border; }
""")


def text_color(dark: bool | None = None) -> QColor:
    """The theme's text colour, read straight from the theme (the widgets'
    palettes only catch up after Qt processes the theme change)."""
    return QColor(current["text"])


def section_color(dark: bool | None = None) -> QColor:
    return QColor(current["text_dim"])


def toolbar_icon_color() -> QColor:
    """Icons drawn straight on the toolbar (on the accent in Material)."""
    return QColor(current["toolbar_text"])


# Preferences → General → Grid View: the album wall dark or light. On the
# matching kind of style it takes that style's colours; otherwise these.
GRID_DARK = {"grid_bg": "#262729", "grid_line": "#222", "grid_tab_text": "#e6e6e6",
             "grid_tab_checked": "#1d1d1d", "grid_tab_border": "#555", "grid_tab_hover": "#484848",
             "grid_title": "#f2f2f2", "grid_subtitle": "#a8a8a8", "grid_empty_art": "#4a4a4a"}
GRID_LIGHT = {"grid_bg": "#f2f2f2", "grid_line": "#c8c8c8", "grid_tab_text": "#333333",
              "grid_tab_checked": "#d6d6d6", "grid_tab_border": "#b0b0b0", "grid_tab_hover": "#e2e2e2",
              "grid_title": "#1e1e1e", "grid_subtitle": "#6b6b6b", "grid_empty_art": "#d0d0d0"}
grid_colors = dict(GRID_DARK)       # read by the Grid view when it paints


def _grid_from(colors: dict) -> dict:
    """The Grid in the style's own colours."""
    background, text = colors["base"], colors["text"]
    return {"grid_bg": background, "grid_line": colors["border"], "grid_tab_text": text,
            "grid_tab_checked": colors["button_checked"], "grid_tab_border": colors["border"],
            "grid_tab_hover": colors["button_top"], "grid_title": text,
            "grid_subtitle": colors["text_dim"], "grid_empty_art": mix(background, text, 0.2)}


def now_playing_color() -> QColor:
    """The playing song's text in the song lists: the accent, as in the app."""
    return QColor(current["now_playing_text"])


def apply_theme(app: QApplication, look: Look, grid_dark: bool = True,
                accent: str | None = None) -> dict:
    """Use this look. `accent`: Auto Accent's colour from the album art (None:
    the style's own accent). Returns the colours in use."""
    background, text, own_accent = look.current
    colors = build_colors(look.style, background, text, accent or own_accent)
    dark_style = not colors["light"]
    colors.update(_grid_from(colors) if grid_dark == dark_style else
                  (GRID_DARK if grid_dark else GRID_LIGHT))
    current.clear()
    current.update(colors)
    grid_colors.clear()
    grid_colors.update(colors)
    app.setStyle("Fusion")

    palette = QPalette()
    role = QPalette.ColorRole
    palette.setColor(role.Window, QColor(colors["window"]))
    palette.setColor(role.WindowText, QColor(colors["text"]))
    palette.setColor(role.Base, QColor(colors["base"]))
    palette.setColor(role.AlternateBase, QColor(colors["alt_base"]))
    palette.setColor(role.Text, QColor(colors["text"]))
    palette.setColor(role.Button, QColor(colors["button_bottom"]))
    palette.setColor(role.ButtonText, QColor(colors["text"]))
    palette.setColor(role.Highlight, QColor(colors["highlight"]))
    palette.setColor(role.HighlightedText, QColor(colors["highlight_text"]))
    palette.setColor(role.ToolTipBase, QColor(colors["base"]))
    palette.setColor(role.ToolTipText, QColor(colors["text"]))
    palette.setColor(role.PlaceholderText, QColor(colors["text_dim"]))
    palette.setColor(role.Link, QColor(colors["highlight"]))
    palette.setColor(role.Mid, QColor(colors["border"]))
    # setColor(role, ...) sets every state, disabled included; give things
    # that can't be used right now their own, lighter text.
    disabled = QColor(colors["text_disabled"])
    for r in (role.WindowText, role.Text, role.ButtonText, role.HighlightedText):
        palette.setColor(QPalette.ColorGroup.Disabled, r, disabled)
    # Keep the selection in the accent even when the song list doesn't have focus.
    palette.setColor(QPalette.ColorGroup.Inactive, role.Highlight, QColor(colors["highlight"]))
    palette.setColor(
        QPalette.ColorGroup.Inactive, role.HighlightedText, QColor(colors["highlight_text"])
    )
    app.setPalette(palette)
    resources = Path(__file__).resolve().parent.parent / "resources"
    list_check = resources / ("check-dark.svg" if colors["light"] else "check-light.svg")
    app.setStyleSheet(STYLESHEET.substitute(colors, check_icon=(resources / "check.svg").as_posix(),
                                            list_check_icon=list_check.as_posix()))
    return colors


# ---- Drawn icons -----------------------------------------------------------------------------

def transport_icon(kind: str, color: str, size: int = 32):
    """Play, pause, stop, previous and next, drawn in `color` (the standard
    icons are dark only, which can't sit on any background)."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QIcon, QPainter, QPixmap, QPolygonF
    icons = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        u = size / 32

        def triangle(x, y, w, h, left=False):
            points = [QPointF(x + w, y), QPointF(x, y + h / 2), QPointF(x + w, y + h)] if left \
                else [QPointF(x, y), QPointF(x + w, y + h / 2), QPointF(x, y + h)]
            p.drawPolygon(QPolygonF(points))
        if kind == "play":
            triangle(11 * u, 8 * u, 13 * u, 16 * u)
        elif kind == "pause":
            p.drawRoundedRect(QRectF(9 * u, 8 * u, 5 * u, 16 * u), u, u)
            p.drawRoundedRect(QRectF(18 * u, 8 * u, 5 * u, 16 * u), u, u)
        elif kind == "stop":
            p.drawRoundedRect(QRectF(9 * u, 9 * u, 14 * u, 14 * u), 1.5 * u, 1.5 * u)
        elif kind == "previous":
            p.drawRect(QRectF(7 * u, 10 * u, 2.5 * u, 12 * u))
            triangle(9.5 * u, 10 * u, 8 * u, 12 * u, left=True)
            triangle(17 * u, 10 * u, 8 * u, 12 * u, left=True)
        elif kind == "next":
            triangle(7 * u, 10 * u, 8 * u, 12 * u)
            triangle(14.5 * u, 10 * u, 8 * u, 12 * u)
            p.drawRect(QRectF(22.5 * u, 10 * u, 2.5 * u, 12 * u))
        p.end()
        icons.addPixmap(pixmap)
    return icons
