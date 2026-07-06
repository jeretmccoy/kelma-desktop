# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
"""KelmaDesktop theme — light/dark, both Kelma-branded.

- Dark mode uses the exact KelmaMobile palette (warm near-black + gold).
- Light mode uses a matching warm-cream + gold variant.

Follows the user's chosen theme (no forcing). Green stays the brand/logo; gold is
the interactive accent; semantic card-state colors (new/learning/review) are left
alone. Applied via `!important` webview vars + the native `style_did_init` hook,
and re-rendered on theme switch. All best-effort.
"""

from __future__ import annotations

from aqt import gui_hooks, mw
from aqt.theme import theme_manager

# Exact KelmaMobile dark palette.
DARK = {
    "canvas": "#0f100a",
    "inset": "#0b0c07",
    "surface": "#1b1d16",
    "elevated": "#24271d",
    "border": "#3a3d31",
    "border_subtle": "#2a2c22",
    "fg": "#f4f1e7",
    "fg_subtle": "#adaea1",
    "fg_faint": "#7b7d70",
}
# Matching warm-cream light variant.
LIGHT = {
    "canvas": "#f4f1e7",
    "inset": "#eae4d6",
    "surface": "#fdfbf4",
    "elevated": "#ffffff",
    "border": "#ddd6c4",
    "border_subtle": "#ece6d7",
    "fg": "#26231d",
    "fg_subtle": "#6b655a",
    "fg_faint": "#99937f",
}
GOLD = "#c9ac6b"
GOLD_SOFT = "#dcc48f"
GOLD_BRIGHT = "#ecd49a"
ON_GOLD = "#17150f"


def _pal() -> dict:
    return DARK if theme_manager.night_mode else LIGHT


def _css() -> str:
    p = _pal()
    return f"""
<style id="kelma-theme">
:root {{
  --canvas: {p['canvas']} !important;
  --canvas-inset: {p['inset']} !important;
  --canvas-elevated: {p['surface']} !important;
  --canvas-overlay: {p['elevated']} !important;
  --canvas-code: {p['inset']} !important;
  --canvas-glass: {p['elevated']} !important;
  --fg: {p['fg']} !important;
  --fg-subtle: {p['fg_subtle']} !important;
  --fg-faint: {p['fg_faint']} !important;
  --fg-disabled: {p['fg_faint']} !important;
  --fg-link: {p['fg']} !important;
  --border: {p['border']} !important;
  --border-subtle: {p['border_subtle']} !important;
  --border-strong: {p['border']} !important;
  --border-focus: {GOLD} !important;
  --button-primary-bg: {GOLD} !important;
  --button-primary-gradient-start: {GOLD_BRIGHT} !important;
  --button-primary-gradient-end: {GOLD} !important;
  --button-primary-disabled: {p['border']} !important;
  --highlight-bg: {GOLD} !important;
  --highlight-fg: {ON_GOLD} !important;
}}
a:hover {{ color: {GOLD} !important; }}
</style>
"""


def _qss() -> str:
    p = _pal()
    return f"""
/* KelmaDesktop native accents */
QMenuBar {{ background-color: {p['surface']}; }}
QMenuBar::item:selected {{ background-color: {GOLD}; color: {ON_GOLD}; }}
QMenu {{ background-color: {p['surface']}; color: {p['fg']}; border: 1px solid {p['border']}; }}
QMenu::item:selected {{ background-color: {GOLD}; color: {ON_GOLD}; }}
QPushButton:default {{ background-color: {GOLD}; color: {ON_GOLD}; border: none; }}
QPushButton:default:hover {{ background-color: {GOLD_BRIGHT}; }}
QTabBar::tab:selected {{ color: {GOLD}; }}
QProgressBar::chunk {{ background-color: {GOLD}; }}
"""


def _on_webview(web_content, context) -> None:
    try:
        web_content.head += _css()
        if type(context).__name__ in ("Toolbar", "TopToolbar"):
            web_content.head += (
                "<style>"
                f"body {{ border-bottom: 2px solid {GOLD} !important; }}"
                f".hitem {{ color: {_pal()['fg']} !important; }}"
                f".hitem:hover {{ color: {GOLD} !important; }}"
                "</style>"
            )
    except Exception:  # noqa: BLE001
        pass


def _on_style(buf: str) -> str:
    return buf + _qss()


def _on_theme_change() -> None:
    # Re-render webviews so they pick up the new light/dark palette.
    try:
        mw.reset()
    except Exception:  # noqa: BLE001
        pass


def setup() -> None:
    gui_hooks.style_did_init.append(_on_style)
    gui_hooks.webview_will_set_content.append(_on_webview)
    gui_hooks.theme_did_change.append(_on_theme_change)
    try:
        theme_manager.apply_style()
        mw.reset()
    except Exception:  # noqa: BLE001
        pass
