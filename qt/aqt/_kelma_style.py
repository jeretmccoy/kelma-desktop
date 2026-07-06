# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
"""KelmaDesktop theme: retint the app's brand accent to Kelma green.

Applies across every webview (links, primary buttons, selection) and to native
Qt widgets (selection highlight, links). The semantic card-state colors
(new/learning/review) are deliberately left alone. Best-effort and guarded.
"""

from __future__ import annotations

from aqt import gui_hooks, mw
from aqt.qt import QColor, QPalette

GREEN = "#4cb867"
GREEN_LIGHT = "#5cbf77"
GREEN_DARK = "#3fa25f"

_CSS = f"""
<style id="kelma-theme">
:root {{
  --fg-link: {GREEN_LIGHT};
  --button-primary-bg: {GREEN};
  --button-primary-gradient-start: {GREEN_LIGHT};
  --button-primary-gradient-end: {GREEN_DARK};
  --highlight-bg: {GREEN};
  --highlight-fg: #ffffff;
}}
a {{ color: {GREEN_LIGHT}; }}
</style>
"""


def _on_webview(web_content, context) -> None:
    try:
        web_content.head += _CSS
        # A Kelma-green accent under the top toolbar.
        if type(context).__name__ in ("Toolbar", "TopToolbar"):
            web_content.head += (
                f"<style>body {{ border-bottom: 2px solid {GREEN} !important; }}</style>"
            )
    except Exception:  # noqa: BLE001
        pass


def _apply_palette() -> None:
    try:
        pal = mw.app.palette()
        pal.setColor(QPalette.ColorRole.Highlight, QColor(GREEN))
        pal.setColor(QPalette.ColorRole.Link, QColor(GREEN_LIGHT))
        mw.app.setPalette(pal)
    except Exception:  # noqa: BLE001
        pass


def setup() -> None:
    gui_hooks.webview_will_set_content.append(_on_webview)
    gui_hooks.theme_did_change.append(_apply_palette)
    _apply_palette()
