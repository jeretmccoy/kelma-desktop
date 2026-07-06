# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
"""KelmaDesktop theme — a modern, warm, gold-accented look in both light and dark.

Design language (shared with KelmaMobile):
  * Warm charcoal (dark) / warm cream (light) canvas — never neutral gray.
  * Gold is the single interactive accent; semantic card-state colors
    (new / learning / review) are left untouched.
  * Generous rounding, soft shadows, refined system typography, thin gold
    scrollbars, and a signature gold hairline under the toolbar.

The palette is applied by overriding Anki's CSS custom properties (with
``!important`` so they beat the generated theme vars) plus a native ``QSS``
layer via ``style_did_init``. Structural polish is scoped per screen (deck
list, overview, toolbar) by webview *context* so it never leaks into card
content, the editor, or the browser. Everything is best-effort — a failure
must never break rendering.
"""

from __future__ import annotations

import json

from aqt import gui_hooks, mw
from aqt.theme import theme_manager

# Exact KelmaMobile dark palette (warm charcoal).
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

# Modern system font stack — SF on macOS, Segoe on Windows, Roboto on Linux.
FONT_STACK = (
    '-apple-system, "SF Pro Display", "SF Pro Text", system-ui, '
    '"Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
)


def _pal() -> dict:
    return DARK if theme_manager.night_mode else LIGHT


def _accent() -> tuple[str, str, str, str]:
    """(accent, accent-bright, on-accent, gold-tint-rgba) for the current mode.

    Dark mode uses a slightly brighter gold so it reads against charcoal, and a
    stronger tint for hovers; light mode keeps the softer gold.
    """
    if theme_manager.night_mode:
        return GOLD_SOFT, GOLD_BRIGHT, ON_GOLD, "rgba(236, 212, 154, 0.12)"
    return GOLD, GOLD_BRIGHT, ON_GOLD, "rgba(201, 172, 107, 0.16)"


def _vars_css() -> str:
    """Palette + accent CSS-variable overrides — safe for every webview."""
    p = _pal()
    accent, bright, on_accent, _tint = _accent()
    return f"""
:root {{
  --canvas: {p['canvas']} !important;
  --canvas-inset: {p['inset']} !important;
  --canvas-elevated: {p['surface']} !important;
  --canvas-overlay: {p['elevated']} !important;
  --canvas-code: {p['inset']} !important;
  --canvas-glass: {p['surface']} !important;
  --fg: {p['fg']} !important;
  --fg-subtle: {p['fg_subtle']} !important;
  --fg-faint: {p['fg_faint']} !important;
  --fg-disabled: {p['fg_faint']} !important;
  --fg-link: {accent} !important;
  --border: {p['border']} !important;
  --border-subtle: {p['border_subtle']} !important;
  --border-strong: {p['border']} !important;
  --border-focus: {accent} !important;
  --button-primary-bg: {accent} !important;
  --button-primary-gradient-start: {bright} !important;
  --button-primary-gradient-end: {accent} !important;
  --button-primary-disabled: {p['border']} !important;
  --highlight-bg: {accent} !important;
  --highlight-fg: {on_accent} !important;
}}
"""


def _scrollbar_css() -> str:
    p = _pal()
    accent, _bright, _on, _tint = _accent()
    return f"""
::-webkit-scrollbar {{ width: 11px; height: 11px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{
  background: {p['border']};
  border-radius: 999px;
  border: 3px solid transparent;
  background-clip: content-box;
}}
::-webkit-scrollbar-thumb:hover {{ background: {accent}; background-clip: content-box; }}
"""


def _base_css() -> str:
    """Var overrides + scrollbars + link accent, injected into every webview."""
    accent, _bright, _on, _tint = _accent()
    return (
        '<style id="kelma-theme">'
        + _vars_css()
        + _scrollbar_css()
        + f"a:hover {{ color: {accent} !important; }}"
        + "</style>"
    )


def _deckbrowser_css() -> str:
    """Modern deck list: elevated rounded card, airy rows, refined counts."""
    p = _pal()
    accent, bright, _on, tint = _accent()
    shadow = (
        "0 1px 2px rgba(0,0,0,0.35), 0 10px 30px rgba(0,0,0,0.30)"
        if theme_manager.night_mode
        else "0 1px 2px rgba(60,50,20,0.05), 0 12px 34px rgba(120,100,40,0.11)"
    )
    return f"""
<style id="kelma-deckbrowser">
body {{ font-family: {FONT_STACK}; margin: 2.4em 1em 1em 1em; }}
.fancy table {{
  border: 1px solid {p['border_subtle']} !important;
  border-radius: 20px !important;
  box-shadow: {shadow} !important;
  background: {p['surface']} !important;
  padding: 1.1rem 1rem !important;
  border-collapse: separate !important;
  /* Vertical gap so adjacent row highlights never touch — each stays a
     separate rounded pill. */
  border-spacing: 0 5px !important;
}}
.fancy table:hover {{ box-shadow: {shadow} !important; }}
th {{
  font-size: 0.7rem;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: {p['fg_faint']} !important;
  font-weight: 700;
  padding: 4px 14px 12px 14px !important;
}}
th.count {{ padding-right: 16px !important; }}
/* Airy rows — noticeably more breathing room between decks. */
tr.deck td {{
  padding: 12px 14px !important;
  transition: background 0.14s ease;
}}
tr.deck td.decktd {{ padding-left: 18px !important; }}
tr.deck td[align=end] {{ padding-right: 18px !important; }}
a.deck {{ font-weight: 600; font-size: 1.04em; letter-spacing: 0.01em; }}
a.deck:hover {{ color: {accent} !important; text-decoration: none; }}
/* Counts: tabular figures, aligned, a touch heavier. */
.new-count, .learn-count, .review-count, .zero-count {{
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  font-size: 1.02em;
}}
/* Rounded pill highlight on the current / hovered deck. */
.current td, tr:hover:not(.top-level-drag-row) td {{
  background: {tint} !important;
}}
.current td:first-child, tr:hover:not(.top-level-drag-row) td:first-child {{
  border-top-left-radius: 12px; border-bottom-left-radius: 12px;
}}
.current td:last-child, tr:hover:not(.top-level-drag-row) td:last-child {{
  border-top-right-radius: 12px; border-bottom-right-radius: 12px;
}}
.gears {{ transition: opacity 0.14s ease, filter 0.14s ease; }}
.gears:hover {{ filter: drop-shadow(0 0 4px {accent}); }}
</style>
"""


def _overview_css() -> str:
    """Modern Overview: prominent, readable, gradient Study Now button."""
    p = _pal()
    accent, bright, on_accent, _tint = _accent()
    grad = f"linear-gradient(135deg, {bright}, {accent})"
    glow = (
        f"0 8px 22px rgba(201, 172, 107, 0.30)"
        if theme_manager.night_mode
        else f"0 8px 22px rgba(201, 172, 107, 0.38)"
    )
    return f"""
<style id="kelma-overview">
body {{ font-family: {FONT_STACK}; }}
.descfont {{ line-height: 1.6; }}
h3 {{ letter-spacing: 0.01em; }}
/* Study Now — dark text on gold (was low-contrast white), pill + lift. */
#study, button#study.but {{
  color: {on_accent} !important;
  background: {grad} !important;
  border: none !important;
  border-radius: 999px !important;
  padding: 13px 44px !important;
  margin-top: 0.4em;
  font-size: 1.06rem !important;
  font-weight: 700 !important;
  letter-spacing: 0.015em;
  box-shadow: {glow} !important;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}}
#study:hover, button#study.but:hover {{
  color: {on_accent} !important;
  background: {grad} !important;
  transform: translateY(-2px);
  box-shadow: 0 12px 30px rgba(201, 172, 107, 0.48) !important;
}}
#study:active, button#study.but:active {{ transform: translateY(0); }}
</style>
"""


def _toolbar_css() -> str:
    """Pill nav with gold hovers + a signature gold hairline under the bar."""
    p = _pal()
    accent, bright, on_accent, tint = _accent()
    return f"""
<style id="kelma-toolbar">
body {{
  font-family: {FONT_STACK};
  border-bottom: 1px solid {accent}55 !important;
}}
.fancy .toolbar {{
  background: {p['surface']} !important;
  border: 1px solid {p['border_subtle']} !important;
  border-radius: 999px !important;
  padding: 4px 6px !important;
}}
.hitem {{
  color: {p['fg']} !important;
  border-radius: 999px !important;
  padding: 6px 15px !important;
  letter-spacing: 0.02em;
  transition: background 0.14s ease, color 0.14s ease;
}}
.hitem:hover {{
  text-decoration: none !important;
  background: {tint} !important;
  color: {accent} !important;
}}
</style>
"""


# Semantic rating colors for the answer buttons (kept close to Anki's), shown
# as a coloured accent bar so the four choices stay instantly recognizable.
EASE_COLOR = {
    "1": "#e0555f",  # Again  — red
    "2": "#e0913f",  # Hard   — amber
    "3": "#5bbf83",  # Good   — green
    "4": "#5a9fe0",  # Easy   — blue
}


def _reviewer_bottom_css() -> str:
    """Modern study controls: pill answer buttons with a per-rating accent bar,
    and a prominent gold Show Answer button."""
    p = _pal()
    accent, bright, on_accent, tint = _accent()
    grad = f"linear-gradient(135deg, {bright}, {accent})"
    ease_rules = "".join(
        f"""
#middle button[data-ease="{e}"] {{ border-bottom: 3px solid {c} !important; }}
#middle button[data-ease="{e}"] .nobold {{ color: {c} !important; opacity: 0.9; }}
#middle button[data-ease="{e}"]:hover {{ border-color: {c} !important; }}
"""
        for e, c in EASE_COLOR.items()
    )
    return f"""
<style id="kelma-reviewer-bottom">
body {{ font-family: {FONT_STACK}; }}
#middle button {{
  border: 1px solid {p['border_subtle']} !important;
  border-radius: 12px !important;
  background: {p['elevated']} !important;
  color: {p['fg']} !important;
  padding: 9px 20px !important;
  min-width: 82px !important;
  margin: 8px 7px !important;
  font-weight: 600;
  transition: transform 0.12s ease, box-shadow 0.12s ease, border-color 0.12s ease;
}}
#middle button:hover {{
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.18);
}}
{ease_rules}
/* Show Answer — the primary action, a gold pill. */
#ansbut {{
  background: {grad} !important;
  color: {on_accent} !important;
  border: none !important;
  border-radius: 999px !important;
  padding: 11px 40px !important;
  font-weight: 700 !important;
  letter-spacing: 0.01em;
  box-shadow: 0 6px 18px rgba(201, 172, 107, 0.34) !important;
}}
#ansbut:hover {{ transform: translateY(-2px); }}
.nobold, .stattxt {{ color: {p['fg_faint']} !important; }}
</style>
"""


def _reviewer_css() -> str:
    """Subtle polish on the study card frame — never touches user card CSS."""
    accent, bright, _on, _tint = _accent()
    return f"""
<style id="kelma-reviewer">
/* Q/A divider as a soft gold gradient hairline instead of a hard rule. */
hr#answer {{
  border: none !important;
  height: 2px !important;
  background: linear-gradient(90deg, transparent, {accent}, transparent) !important;
  opacity: 0.75;
  margin: 1.1em auto !important;
  max-width: 620px;
}}
</style>
"""


def _stats_css() -> str:
    """Graph cards: rounded, elevated, subtly bordered — a modern dashboard."""
    p = _pal()
    accent, _bright, _on, _tint = _accent()
    shadow = (
        "0 1px 2px rgba(0,0,0,0.30), 0 8px 22px rgba(0,0,0,0.22)"
        if theme_manager.night_mode
        else "0 1px 2px rgba(60,50,20,0.05), 0 10px 26px rgba(120,100,40,0.09)"
    )
    return f"""
.graph {{
  background: {p['surface']} !important;
  border: 1px solid {p['border_subtle']} !important;
  border-radius: 16px !important;
  box-shadow: {shadow} !important;
  padding: 1.1em 1.2em !important;
  margin: 0.7em auto !important;
}}
.graph h1, .graph .title {{ letter-spacing: 0.01em; }}
.range-box, .range-box-inner {{ border-radius: 12px !important; }}
"""


def _qss() -> str:
    """Native Qt chrome: menus, buttons, tabs, scrollbars, progress bars."""
    p = _pal()
    accent, bright, on_accent, _tint = _accent()
    return f"""
/* --- KelmaDesktop native chrome --- */
QMenuBar {{ background-color: {p['surface']}; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; }}
QMenuBar::item:selected {{ background-color: {accent}; color: {on_accent}; }}
QMenu {{
  background-color: {p['surface']};
  color: {p['fg']};
  border: 1px solid {p['border']};
  border-radius: 10px;
  padding: 6px;
}}
QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background-color: {accent}; color: {on_accent}; }}
QMenu::separator {{ height: 1px; background: {p['border_subtle']}; margin: 5px 8px; }}
QPushButton {{
  border: 1px solid {p['border']};
  border-radius: 8px;
  padding: 5px 14px;
  background-color: {p['elevated']};
  color: {p['fg']};
}}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:default {{
  background-color: {accent};
  color: {on_accent};
  border: none;
  font-weight: 600;
}}
QPushButton:default:hover {{ background-color: {bright}; }}
QTabBar::tab {{ padding: 6px 14px; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom: 2px solid {accent}; }}
QProgressBar {{ border-radius: 6px; }}
QProgressBar::chunk {{ background-color: {accent}; border-radius: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
  background: {p['border']};
  border-radius: 6px;
  min-height: 28px;
  min-width: 28px;
}}
QScrollBar::handle:hover {{ background: {accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
  border: 1px solid {p['border']};
  border-radius: 8px;
  padding: 4px 8px;
  selection-background-color: {accent};
  selection-color: {on_accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {accent}; }}

/* --- Browser: card table + sidebar tree --- */
QTableView, QTreeView {{
  border: none;
  background-color: {p['canvas']};
  alternate-background-color: {p['surface']};
  selection-background-color: {accent};
  selection-color: {on_accent};
  outline: 0;
}}
QTableView::item, QTreeView::item {{ padding: 3px 4px; }}
QTableView::item:selected, QTreeView::item:selected {{
  background-color: {accent}; color: {on_accent};
}}
QTreeView::item:hover, QTableView::item:hover {{ background-color: {_tint}; }}
QHeaderView::section {{
  background-color: {p['surface']};
  color: {p['fg_faint']};
  border: none;
  border-bottom: 1px solid {p['border']};
  padding: 6px 8px;
  font-weight: 600;
}}
"""


def _on_webview(web_content, context) -> None:
    try:
        web_content.head += _base_css()
        name = type(context).__name__
        if name == "DeckBrowser":
            web_content.head += _deckbrowser_css()
        elif name == "Overview":
            web_content.head += _overview_css()
        elif name in ("Toolbar", "TopToolbar"):
            web_content.head += _toolbar_css()
        elif name == "ReviewerBottomBar":
            web_content.head += _reviewer_bottom_css()
        elif name == "Reviewer":
            web_content.head += _reviewer_css()
    except Exception:  # noqa: BLE001
        pass


def _on_page_style(webview) -> None:
    """Theme load_url pages (stats graphs, deck options, import …) that don't go
    through webview_will_set_content — inject the palette + graph-card polish."""
    try:
        css = _vars_css() + _scrollbar_css() + _stats_css()
        payload = json.dumps(css)
        webview.eval(
            "(function(){var s=document.createElement('style');"
            "s.id='kelma-page';s.textContent=" + payload + ";"
            "document.head.appendChild(s);})();"
        )
    except Exception:  # noqa: BLE001
        pass


def _on_style(buf: str) -> str:
    try:
        return buf + _qss()
    except Exception:  # noqa: BLE001
        return buf


def _on_theme_change() -> None:
    # Re-render webviews so they pick up the new light/dark palette.
    try:
        mw.reset()
    except Exception:  # noqa: BLE001
        pass


def setup() -> None:
    gui_hooks.style_did_init.append(_on_style)
    gui_hooks.webview_will_set_content.append(_on_webview)
    gui_hooks.webview_did_inject_style_into_page.append(_on_page_style)
    gui_hooks.theme_did_change.append(_on_theme_change)
    try:
        theme_manager.apply_style()
        mw.reset()
    except Exception:  # noqa: BLE001
        pass
