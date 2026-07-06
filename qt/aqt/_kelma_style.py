# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
"""KelmaDesktop theme — the warm-dark + gold look of the Kelma mobile app.

Two layers:
  * webviews (deck list, reviewer, top toolbar, dialogs) — override Anki's base
    CSS variables with `!important` so they win over the generated ones;
  * native Qt chrome — append a small QSS via the `style_did_init` hook so it's
    part of Anki's own stylesheet (not clobbered on re-apply).

Green stays the brand/logo; gold is the interactive accent; the semantic
card-state colors (new/learning/review) are left alone. All best-effort.
"""

from __future__ import annotations

from aqt import gui_hooks, mw
from aqt.theme import theme_manager

# KelmaMobile palette, shifted to a warm charcoal (no green/olive cast — the
# darks keep R >= G >= B so they read neutral-warm and pair with the gold).
CANVAS = "#100f0d"
CANVAS_INSET = "#0a0908"
SURFACE = "#1c1a17"
ELEVATED = "#262219"
BORDER = "#3d382e"
BORDER_SUBTLE = "#2b2721"
FG = "#f4f1e7"
FG_SUBTLE = "#b0ada3"
FG_FAINT = "#7d7a70"
GOLD = "#c9ac6b"
GOLD_SOFT = "#dcc48f"
GOLD_BRIGHT = "#ecd49a"
ON_GOLD = "#17150f"

_CSS = f"""
<style id="kelma-theme">
:root {{
  --canvas: {CANVAS} !important;
  --canvas-inset: {CANVAS_INSET} !important;
  --canvas-elevated: {SURFACE} !important;
  --canvas-overlay: {ELEVATED} !important;
  --canvas-code: {CANVAS_INSET} !important;
  --canvas-glass: {ELEVATED} !important;
  --fg: {FG} !important;
  --fg-subtle: {FG_SUBTLE} !important;
  --fg-faint: {FG_FAINT} !important;
  --fg-disabled: {FG_FAINT} !important;
  --fg-link: {FG} !important;
  --border: {BORDER} !important;
  --border-subtle: {BORDER_SUBTLE} !important;
  --border-strong: {BORDER} !important;
  --border-focus: {GOLD} !important;
  --button-primary-bg: {GOLD} !important;
  --button-primary-gradient-start: {GOLD_BRIGHT} !important;
  --button-primary-gradient-end: {GOLD} !important;
  --button-primary-disabled: {BORDER} !important;
  --highlight-bg: {GOLD} !important;
  --highlight-fg: {ON_GOLD} !important;
}}
a:hover {{ color: {GOLD} !important; }}
</style>
"""

_QSS = f"""
/* KelmaDesktop native accents */
QMenuBar {{ background-color: {SURFACE}; }}
QMenuBar::item:selected {{ background-color: {GOLD}; color: {ON_GOLD}; }}
QMenu {{ background-color: {SURFACE}; color: {FG}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background-color: {GOLD}; color: {ON_GOLD}; }}
QPushButton:default {{ background-color: {GOLD}; color: {ON_GOLD}; border: none; }}
QPushButton:default:hover {{ background-color: {GOLD_BRIGHT}; }}
QTabBar::tab:selected {{ color: {GOLD}; }}
QProgressBar::chunk {{ background-color: {GOLD}; }}
"""


def _on_webview(web_content, context) -> None:
    try:
        web_content.head += _CSS
        if type(context).__name__ in ("Toolbar", "TopToolbar"):
            web_content.head += (
                "<style>"
                f"body {{ border-bottom: 2px solid {GOLD} !important; }}"
                # Nav items are warm-white; gold on hover / active only.
                f".hitem {{ color: {FG} !important; }}"
                f".hitem:hover {{ color: {GOLD} !important; }}"
                "</style>"
            )
    except Exception:  # noqa: BLE001
        pass


def _on_style(buf: str) -> str:
    return buf + _QSS


def setup() -> None:
    # Pin a consistent dark base (KelmaMobile is dark-only), then re-apply.
    try:
        theme_manager._determine_night_mode = lambda: True  # type: ignore[method-assign]
        theme_manager.set_night_mode(True)
    except Exception:  # noqa: BLE001
        pass
    gui_hooks.style_did_init.append(_on_style)
    gui_hooks.webview_will_set_content.append(_on_webview)
    try:
        theme_manager.apply_style()  # re-render with our hooks in place
        mw.reset()
    except Exception:  # noqa: BLE001
        pass
