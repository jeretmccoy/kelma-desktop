"""Per-deck sync badges + per-cloud size totals on the main deck list.

Hooks `deck_browser_will_render_content` and rewrites the deck-tree HTML to:
  - place a small coloured badge to the LEFT of each deck name — green = Kelma,
    blue = AnkiWeb, no letters — showing that deck's pending state (`✓` in sync,
    `+n` added / `~n` changed), with a hover tooltip naming the cloud + state;
  - show each cloud's total card count (its "size") above the deck list, coloured
    to match.
"""

from __future__ import annotations

import os
import re
import threading
import time

from anki.media import media_paths_from_col_path
from aqt import mw, gui_hooks

from . import config, consts, features, paths, state

_installed = False

# Per-cloud on-disk size: computed in the background (walking media is slow) and
# cached, so it never blocks the deck-list render.
_SIZE_TTL = 300
_size_cache: dict[str, tuple[float, int]] = {}
_size_running: set[str] = set()

# Deck-list cloud filter: "all" | "kelma" | "ankiweb" | "both".
_filter_mode = "all"

# Kelma = green, AnkiWeb = blue.
_COLOR = {consts.KELMA: "#16a34a", consts.ANKIWEB: "#2563eb"}

# Matches a deck-name link in the deck-browser tree, capturing the deck id.
_LINK_RE = re.compile(r"""(<a class="deck[^>]*pycmd\('open:(\d+)'\)">[^<]*</a>)""")
# Matches a deck row's opening <tr>, capturing the deck id (for filter hiding).
_ROW_RE = re.compile(r"(<tr class='deck[^']*' id='(\d+)'[^>]*>)")

_STYLE = (
    "<style>"
    ".kelma-badges:not(:empty){margin-right:8px;white-space:nowrap;}"
    ".kelma-badge{font-size:11px;border:1px solid;border-radius:4px;"
    "padding:0 4px;margin-right:3px;vertical-align:middle;}"
    ".kelma-badge.synced{opacity:.55;}"
    ".kelma-sizes{padding:4px 10px 2px;font-size:12px;opacity:.9;}"
    ".kelma-filter{padding:0 10px 8px;font-size:12px;opacity:.9;}"
    ".kelma-filter a{margin-right:12px;text-decoration:none;color:inherit;}"
    ".kelma-filter a:hover{text-decoration:underline;}"
    "</style>"
)


def _deck_clouds(name: str) -> tuple[bool, bool]:
    """(on_kelma, on_ankiweb) for a deck — routed to it and logged in."""
    services = config.services_for_deck(name)
    k = consts.KELMA in services and config.has_credentials(consts.KELMA)
    w = consts.ANKIWEB in services and config.has_credentials(consts.ANKIWEB)
    return k, w


def _matches_filter(name: str) -> bool:
    if _filter_mode == "all":
        return True
    k, w = _deck_clouds(name)
    if _filter_mode == "kelma":
        return k
    if _filter_mode == "ankiweb":
        return w
    if _filter_mode == "both":
        return k or w
    return True


def _filter_bar() -> str:
    opts = [
        ("all", "All", None),
        ("kelma", "KelmaSync", _COLOR[consts.KELMA]),
        ("ankiweb", "AnkiWeb", _COLOR[consts.ANKIWEB]),
        ("both", "Both", None),
    ]
    links = []
    for mode, label, color in opts:
        style = "font-weight:bold;" if mode == _filter_mode else ""
        if color:
            style += f"color:{color};"
        links.append(
            f"<a href=# onclick=\"pycmd('kelma_filter:{mode}');return false\" "
            f'style="{style}">{label}</a>'
        )
    return '<div class="kelma-filter">Show: ' + "".join(links) + "</div>"


def _badges(did: int, name: str, pending: dict) -> str:
    services = config.services_for_deck(name)
    k, w = _deck_clouds(name)
    spans = []
    for s in consts.SERVICES:
        if s not in services or not config.has_credentials(s):
            continue
        added, changed = pending[s].get(did, (0, 0))
        cloud = consts.SERVICE_LABEL[s]
        if added or changed:
            text = " ".join(
                p for p in (f"+{added}" if added else "", f"~{changed}" if changed else "") if p
            )
            detail = ", ".join(
                p for p in (f"{added} added" if added else "", f"{changed} changed" if changed else "") if p
            )
            title = f"{cloud}: {detail}"
            cls = "kelma-badge"
        else:
            text = "✓"
            title = f"{cloud}: in sync"
            cls = "kelma-badge synced"
        color = _COLOR[s]
        spans.append(
            f'<span class="{cls}" style="color:{color};border-color:{color}" '
            f'title="{title}">{text}</span>'
        )
    # Always emit the container (with cloud flags) so the deck-list filter can act
    # on every row; CSS gives it zero margin when empty.
    return (
        f'<span class="kelma-badges" data-k="{int(k)}" data-w="{int(w)}">'
        f'{"".join(spans)}</span>'
    )


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _compute_size(shadow_path: str) -> int:
    total = 0
    try:
        total += os.path.getsize(shadow_path)
    except OSError:
        pass
    media_folder, media_db = media_paths_from_col_path(shadow_path)
    if os.path.isdir(media_folder):
        total += _dir_size(media_folder)
    try:
        total += os.path.getsize(media_db)
    except OSError:
        pass
    return total


def _refresh_deck_list() -> None:
    try:
        if mw.state == "deckBrowser":
            mw.deckBrowser.refresh()
    except Exception:  # noqa: BLE001
        pass


def _ensure_size(service: str) -> None:
    entry = _size_cache.get(service)
    if entry and time.time() - entry[0] < _SIZE_TTL:
        return
    if service in _size_running:
        return
    _size_running.add(service)
    shadow_path = paths.shadow_path(service)  # resolve on the main thread

    def run() -> None:
        try:
            size = _compute_size(shadow_path)
            _size_cache[service] = (time.time(), size)
            mw.taskman.run_on_main(_refresh_deck_list)
        finally:
            _size_running.discard(service)

    threading.Thread(target=run, daemon=True).start()


def invalidate_sizes() -> None:
    """Force the per-cloud size to recompute on next render (e.g. after a delete)."""
    _size_cache.clear()


def _size_str(service: str) -> str:
    _ensure_size(service)
    entry = _size_cache.get(service)
    if entry is None:
        return "…"
    return f"{entry[1] / (1024 ** 3):.2f} GB"


def _sizes_html(totals: dict) -> str:
    parts = []
    for s in consts.SERVICES:
        if not config.has_credentials(s):
            continue
        parts.append(
            f'<span style="color:{_COLOR[s]}">{consts.SERVICE_LABEL[s]}: '
            f"{totals[s]:,} cards · {_size_str(s)}</span>"
        )
    return (
        '<div class="kelma-sizes">' + " &nbsp;·&nbsp; ".join(parts) + "</div>"
        if parts
        else ""
    )


def _on_render(deck_browser, content) -> None:
    try:
        if not features.enabled("deck_badges"):
            return
        if not any(config.has_credentials(s) for s in consts.SERVICES):
            return
        pending = {s: state.pending_by_did(mw.col, s) for s in consts.SERVICES}
        names = {d.id: d.name for d in mw.col.decks.all_names_and_ids()}

        # Per-cloud "size" = total cards across the decks routed to that cloud.
        did_counts = {
            did: cnt
            for did, cnt in mw.col.db.all(
                "select did, count(*) from cards group by did"
            )
        }
        totals = {s: 0 for s in consts.SERVICES}
        for did, name in names.items():
            cnt = did_counts.get(did, 0)
            for s in config.services_for_deck(name):
                if config.has_credentials(s):
                    totals[s] += cnt

        def repl(m: "re.Match") -> str:
            name = names.get(int(m.group(2)))
            if name is None:
                return m.group(1)
            return _badges(int(m.group(2)), name, pending) + m.group(1)

        def hide_repl(m: "re.Match") -> str:
            name = names.get(int(m.group(2)))
            if name is None or _matches_filter(name):
                return m.group(1)
            return m.group(1)[:-1] + ' style="display:none">'

        tree = _LINK_RE.sub(repl, content.tree)
        tree = _ROW_RE.sub(hide_repl, tree)
        content.tree = _STYLE + _sizes_html(totals) + _filter_bar() + tree
    except Exception:  # noqa: BLE001 - never break the deck list over a badge
        pass


def _on_js_message(handled, message, context):
    """Handle the deck-list filter buttons' pycmd, then re-render."""
    if isinstance(message, str) and message.startswith("kelma_filter:"):
        global _filter_mode
        _filter_mode = message.split(":", 1)[1] or "all"
        try:
            if mw.state == "deckBrowser":
                mw.deckBrowser.refresh()
        except Exception:  # noqa: BLE001
            pass
        return (True, None)
    return handled


def setup() -> None:
    global _installed
    if _installed:
        return
    gui_hooks.deck_browser_will_render_content.append(_on_render)
    gui_hooks.webview_did_receive_js_message.append(_on_js_message)
    _installed = True
