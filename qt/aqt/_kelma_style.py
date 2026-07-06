# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
"""KelmaDesktop theme — the warm-dark + gold look of the Kelma mobile app.

Overrides Anki's base theme variables across every webview (canvas, text,
borders, links, primary buttons, selection) and the native Qt palette, and pins
a consistent dark base (the mobile app is dark-only). The green star stays the
brand/logo; gold is the interactive accent. Semantic card-state colors
(new/learning/review) are left untouched. Best-effort and guarded.
"""

from __future__ import annotations

from aqt import gui_hooks, mw
from aqt.qt import QColor, QPalette
from aqt.theme import theme_manager

# KelmaMobile palette.
CANVAS = "#0f100a"
CANVAS_INSET = "#0b0c07"
SURFACE = "#1b1d16"
ELEVATED = "#24271d"
BORDER = "#3a3d31"
BORDER_SUBTLE = "#2d3024"
FG = "#f4f1e7"
FG_SUBTLE = "#adaea1"
FG_FAINT = "#7b7d70"
GOLD = "#c9ac6b"
GOLD_SOFT = "#dcc48f"
GOLD_BRIGHT = "#ecd49a"
ON_GOLD = "#17180f"

_CSS = f"""
<style id="kelma-theme">
:root {{
  --canvas: {CANVAS};
  --canvas-inset: {CANVAS_INSET};
  --canvas-elevated: {SURFACE};
  --canvas-overlay: {ELEVATED};
  --canvas-code: {CANVAS_INSET};
  --canvas-glass: {ELEVATED};
  --fg: {FG};
  --fg-subtle: {FG_SUBTLE};
  --fg-faint: {FG_FAINT};
  --fg-disabled: {FG_FAINT};
  --fg-link: {GOLD_SOFT};
  --border: {BORDER};
  --border-subtle: {BORDER_SUBTLE};
  --border-strong: {BORDER};
  --border-focus: {GOLD};
  --button-primary-bg: {GOLD};
  --button-primary-gradient-start: {GOLD_BRIGHT};
  --button-primary-gradient-end: {GOLD};
  --button-primary-disabled: {BORDER};
  --highlight-bg: {GOLD};
  --highlight-fg: {ON_GOLD};
}}
a {{ color: {GOLD_SOFT}; }}
</style>
"""


def _on_webview(web_content, context) -> None:
    try:
        web_content.head += _CSS
        if type(context).__name__ in ("Toolbar", "TopToolbar"):
            web_content.head += (
                f"<style>body {{ border-bottom: 2px solid {GOLD} !important; }}</style>"
            )
    except Exception:  # noqa: BLE001
        pass


def _apply_palette() -> None:
    try:
        pal = mw.app.palette()
        c = QColor
        for role, col in (
            (QPalette.ColorRole.Window, CANVAS),
            (QPalette.ColorRole.Base, SURFACE),
            (QPalette.ColorRole.AlternateBase, ELEVATED),
            (QPalette.ColorRole.Button, SURFACE),
            (QPalette.ColorRole.ToolTipBase, ELEVATED),
            (QPalette.ColorRole.Text, FG),
            (QPalette.ColorRole.WindowText, FG),
            (QPalette.ColorRole.ButtonText, FG),
            (QPalette.ColorRole.ToolTipText, FG),
            (QPalette.ColorRole.Highlight, GOLD),
            (QPalette.ColorRole.HighlightedText, ON_GOLD),
            (QPalette.ColorRole.Link, GOLD_SOFT),
        ):
            pal.setColor(role, c(col))
        mw.app.setPalette(pal)
    except Exception:  # noqa: BLE001
        pass


def setup() -> None:
    # Pin a consistent dark base (KelmaMobile is dark-only), then layer the
    # warm-dark + gold palette on top.
    try:
        theme_manager._determine_night_mode = lambda: True  # type: ignore[method-assign]
        theme_manager.set_night_mode(True)
        theme_manager.apply_style()
    except Exception:  # noqa: BLE001
        pass
    gui_hooks.webview_will_set_content.append(_on_webview)
    gui_hooks.theme_did_change.append(_apply_palette)
    _apply_palette()
