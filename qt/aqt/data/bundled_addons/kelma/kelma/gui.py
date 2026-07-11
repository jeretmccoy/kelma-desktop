"""Menu + the unified Settings dialog (accounts login + per-deck routing),
plus integration with the Sync button."""

from __future__ import annotations

import difflib
import sys
from collections import Counter
from concurrent.futures import Future
from datetime import datetime

from aqt import mw
from aqt.qt import (
    QAbstractItemView,
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QCursor,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QInputDialog,
    QLineEdit,
    QMenu,
    QPushButton,
    pyqtSignal,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    Qt,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from aqt.utils import tooltip

from . import auth, branding, capabilities, config, consts, deckbadges, engine, features, inspect, state

_orig_sync = None
_V2_ACTIVE_ACTION: str | None = None


def _v2_active_message() -> str | None:
    if _V2_ACTIVE_ACTION == "sync":
        return "A KelmaSync sync is already running. Please wait for its completion toast, then compare."
    if _V2_ACTIVE_ACTION == "ankiweb":
        return "AnkiWeb sync is already running. Please wait for it to finish."
    if _V2_ACTIVE_ACTION == "compare":
        return "A KelmaSync compare is already running. Please wait for it to finish."
    return None

_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
# table column per service
_COL = {consts.KELMA: 1, consts.ANKIWEB: 2}
_DIFF_LABEL = {
    "in-sync": "✓ in sync",
    "local-newer": "↑ local newer",
    "server-newer": "↓ server newer",
    "server-only": "↓ server only",
    "local-only": "↑ local only",
    "server-extra": "↓ extra server duplicate",
    "local-extra": "↑ extra local duplicate",
    "deck-count": "⚠ deck count mismatch",
    "deck-hash": "⚠ deck hash mismatch",
    "conflict": "⚠ conflict",
    "card-count": " cards differ",
}
_DIFF_PRIORITY = {
    "conflict": 0,
    "local-newer": 1,
    "server-newer": 1,
    "deck-count": 0,
    "deck-hash": 0,
    "local-extra": 2,
    "server-extra": 2,
    "local-only": 2,
    "server-only": 2,
    "in-sync": 3,
}

def _deck_ids_for_names(names: list[str]) -> list[int]:
    wanted = set(names)
    ids: list[int] = []
    for d in mw.col.decks.all_names_and_ids():
        if d.name in wanted:
            ids.append(int(d.id))
    return ids


def _collection_counts_for_decks(deck_names: list[str]) -> tuple[int, int]:
    """Cheap collection size: (distinct notes, cards) for exact selected decks.

    This intentionally avoids scanning note fields/media files so opening the
    sync menu remains instant.
    """
    dids = _deck_ids_for_names(deck_names)
    if not dids:
        return (0, 0)
    marks = ",".join("?" for _ in dids)
    cards = mw.col.db.scalar(f"SELECT COUNT(*) FROM cards WHERE did IN ({marks})", *dids) or 0
    notes = mw.col.db.scalar(f"SELECT COUNT(DISTINCT nid) FROM cards WHERE did IN ({marks})", *dids) or 0
    return (int(notes), int(cards))


def _esc(text: str) -> str:
    """HTML-escape note field content for safe display in QTextBrowser."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )


def _strip_html(text: str) -> str:
    """Strip HTML tags from an Anki note field, leaving plain text.

    Anki fields are HTML (e.g. ``<div dir="rtl">مرحبا</div>``). Escaping that
    buries the text under literal ``&lt;div&gt;`` — so we strip tags first and
    diff/display the plain text. Whitespace is collapsed; ``<br>``/``<p>``
    become newlines before stripping so line breaks survive.
    """
    import re
    # Convert block-level tags to newlines before stripping.
    text = re.sub(r"<(?:br|/p|/div)\s*>", "\n", text, flags=re.IGNORECASE)
    # Remove all remaining tags.
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common HTML entities.
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    # Collapse runs of spaces (but preserve newlines).
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(lines).strip()


def _card_count_in_deck(note: dict | None, deck_id: int) -> int:
    """Number of cards `note` has in `deck_id`, from manifest data.

    Mirrors ``inspect._card_count`` but lives in gui so the diff dialog can
    show per-deck card counts without importing a private helper.
    """
    if not note:
        return 0
    decks = note.get("decks", []) or []
    cpd = note.get("cards_per_deck", []) or []
    total = 0
    for i, did in enumerate(decks):
        if did == deck_id and i < len(cpd):
            total += int(cpd[i])
    return total


def _total_card_count(note: dict | None) -> int:
    """Total cards for `note` across all decks (manifest data)."""
    if not note:
        return 0
    return sum(int(c) for c in (note.get("cards_per_deck", []) or []))


def _render_field_diff(local: str, server: str) -> tuple[str, str]:
    """Render two field values as HTML with inline character-level diff.

    HTML is stripped to plain text first (Anki fields are HTML), so the diff is
    on actual content, not markup. Uses ``difflib.SequenceMatcher`` to highlight
    exact spans that differ. Returns ``(local_html, server_html)``.
    """
    local = _strip_html(local)
    server = _strip_html(server)
    if local == server:
        return _esc(local), _esc(server)

    sm = difflib.SequenceMatcher(None, local, server)
    local_parts: list[str] = []
    server_parts: list[str] = []
    for tag, l1, l2, s1, s2 in sm.get_opcodes():
        l_chunk = local[l1:l2]
        s_chunk = server[s1:s2]
        if tag == "equal":
            local_parts.append(_esc(l_chunk))
            server_parts.append(_esc(s_chunk))
        else:
            # Differing spans: highlight with a bright inline mark so the
            # exact change is visible even within a long field.
            if l_chunk:
                local_parts.append(
                    f"<mark style='background:#8b3a3a; color:#fff; "
                    f"border-radius:2px;'>{_esc(l_chunk)}</mark>"
                )
            if s_chunk:
                server_parts.append(
                    f"<mark style='background:#3a6b8b; color:#fff; "
                    f"border-radius:2px;'>{_esc(s_chunk)}</mark>"
                )
    local_html = "".join(local_parts) or "<i style='color:#555'>(empty)</i>"
    server_html = "".join(server_parts) or "<i style='color:#555'>(empty)</i>"
    return local_html, server_html


class NoteDiffDialog(QDialog):
    """Shows the actual field-by-field diff for one conflicting note.

    Fetches the server note's full fields (via ``/sync/inspect/note/:guid``)
    and the local note's full fields, then displays each field side by side
    using a QTextBrowser (HTML) so Arabic/RTL text renders correctly.
    """

    # Soft highlight for differing fields — not the blinding Qt yellow.
    _DIFF_BG = "#3a2a1a"   # dark amber for dark mode
    _SAME_BG = "transparent"
    _LOCAL_FG = "#e8c46a"   # warm gold for local
    _SERVER_FG = "#6ab4e8"  # cool blue for server

    def __init__(
        self,
        parent,
        guid: str,
        local_note: dict,
        hkey: str,
        endpoint: str,
        diff: dict | None = None,
        deck_diff: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Note diff — {guid[:12]}…")
        self.resize(860, 600)
        self._guid = guid
        self._local_note = local_note
        self._hkey = hkey
        self._endpoint = endpoint
        self._server_note = None
        # Manifest-level entries (carry decks / cards_per_deck / hash) plus the
        # deck IDs under inspection, so we can show card-count and tag/model
        # differences even when the field text is byte-identical.
        self._diff = diff or {}
        self._deck_diff = deck_diff or {}
        self._local_manifest = self._diff.get("local")
        self._server_manifest = self._diff.get("server")
        self._local_did = int((self._deck_diff.get("local") or {}).get("id", 0))
        self._server_did = int((self._deck_diff.get("server") or {}).get("id", 0))
        # Server nid from the manifest entry — unique per server note, so the
        # fetch is unambiguous even when multiple notes share a guid.
        self._server_nid = int((self._server_manifest or {}).get("nid", 0))

        outer = QVBoxLayout(self)
        self.status = QLabel("Fetching server note…")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        outer.addWidget(self.browser)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        # Per-note resolution actions.
        if not self._guid:
            self.gen_guid_btn = buttons.addButton(
                "Generate GUID", QDialogButtonBox.ButtonRole.ActionRole
            )
            self.gen_guid_btn.setToolTip(
                "This note has an empty GUID, which causes sync/inspect ambiguity. "
                "Generate a unique GUID to fix the root cause."
            )
            self.gen_guid_btn.clicked.connect(self._generate_guid)

        self.accept_btn = buttons.addButton(
            "Accept server", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.accept_btn.setEnabled(False)
        self.accept_btn.clicked.connect(self._accept_server)

        self.push_btn = buttons.addButton(
            "Force local → server", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.push_btn.setEnabled(False)
        self.push_btn.clicked.connect(self._push_to_server)

        outer.addWidget(buttons)

        if self._server_manifest:
            # Fetch the server note in a background thread. This is a network
            # request (not collection access), so uses_collection=False lets it
            # run in parallel without taking the collection lock.
            self._future = mw.taskman.run_in_background(
                lambda: inspect.fetch_server_note(hkey, endpoint, self._server_nid, guid),
                self._on_server_note,
                uses_collection=False,
            )
        else:
            # Local-only row: don't fall back to fetching by guid, because
            # duplicate/empty GUIDs can return an unrelated server note.
            self.status.setText("Rendering local-only note…")
            self._populate()

    def _on_server_note(self, future: Future) -> None:
        try:
            self._server_note = future.result()
        except Exception as err:
            self.status.setText(f"Failed to fetch server note: {err}")
            return
        try:
            self._populate()
        except Exception as err:
            import traceback
            self.status.setText(f"Error rendering diff: {err}")
            self.browser.setHtml(
                f"<pre style='color:#f44; white-space:pre-wrap; font-size:12px;'>"
                f"{traceback.format_exc() or err}"
                f"</pre>"
            )

    def _populate(self) -> None:
        local = self._local_note or {}
        server = self._server_note or {}
        local_missing = not bool(local)
        server_missing = not bool(self._server_note)

        local_fields = (local.get("flds") or "").split("\x1f")
        server_fields = (server.get("flds") or "").split("\x1f")
        max_len = max(len(local_fields), len(server_fields))
        local_fields += [""] * (max_len - len(local_fields))
        server_fields += [""] * (max_len - len(server_fields))

        # Empty GUIDs mean the server can't uniquely identify this note —
        # the fetched server note may be a *different* note that also lacks a
        # guid. Warn so the user doesn't trust the diff blindly.
        guid_warning = ""
        if not self._guid:
            guid_warning = (
                "<div style='padding:8px 10px; margin-bottom:8px; "
                "background:#3a2a1a; border:1px solid #8b6a3a; border-radius:4px; "
                "font-size:13px; color:#e8c46a;'>"
                "⚠ This note has an empty GUID. The server may have multiple "
                "notes without a GUID, so the server side shown below might be "
                "a different note. Consider regenerating this note's GUID in Anki."
                "</div>"
            )

        # --- Compute every concrete difference ---------------------------
        n_field_diff = sum(1 for i in range(max_len) if local_fields[i] != server_fields[i])
        local_tags = local.get("tags") or ""
        server_tags = server.get("tags") or ""
        tags_differ = local_tags != server_tags
        local_mid = int(local.get("mid") or 0)
        server_mid = int(server.get("mid") or 0)
        mid_differ = local_mid != server_mid
        local_mod = int(local.get("mod") or 0)
        server_mod = int(server.get("mod") or 0)
        mod_differ = local_mod != server_mod
        local_cards = _card_count_in_deck(self._local_manifest, self._local_did)
        server_cards = _card_count_in_deck(self._server_manifest, self._server_did)
        cards_differ = local_cards != server_cards
        local_total = _total_card_count(self._local_manifest)
        server_total = _total_card_count(self._server_manifest)
        total_differ = local_total != server_total

        # --- "What differs" banner ---------------------------------------
        status = self._diff.get("status", "")
        changes: list[str] = []
        if local_missing:
            if status == "server-extra":
                changes.append("extra duplicate exists on server")
            else:
                changes.append("note exists on server only")
        if server_missing:
            if status == "local-extra":
                changes.append("extra duplicate exists locally")
            else:
                changes.append("note exists locally only")
        if n_field_diff:
            changes.append(f"{n_field_diff} field(s) differ")
        if tags_differ:
            changes.append(f"tags differ (local {len(local_tags.split())}, server {len(server_tags.split())})")
        if cards_differ:
            changes.append(f"cards in this deck differ (local {local_cards}, server {server_cards})")
        if total_differ and not cards_differ:
            changes.append(f"total cards differ (local {local_total}, server {server_total})")
        if mid_differ:
            changes.append(f"note type differs (local {local_mid}, server {server_mid})")
        if mod_differ and not (n_field_diff or tags_differ):
            # mod differs but no field/tag change explains it — call it out so
            # the user isn't staring at two identical field rows.
            changes.append("modified time differs")
        if not changes:
            changes.append("no differences detected — note is identical")
        banner_color = "#e8c46a" if (len(changes) > 1 or changes[0] != "no differences detected — note is identical") else "#7ab37a"
        banner = (
            "<div style='padding:8px 10px; margin-bottom:8px; "
            "background:#252525; border:1px solid #444; border-radius:4px; "
            "font-size:13px; color:#ddd;'>"
            f"<b style='color:{banner_color};'>What differs:</b> "
            f"{', '.join(changes)}"
            "</div>"
        )

        # --- Table rows --------------------------------------------------
        def _row(label: str, lf: str, sf: str, force_diff: bool = False) -> str:
            differs = force_diff or lf != sf
            bg = self._DIFF_BG if differs else self._SAME_BG
            local_html, server_html = _render_field_diff(lf, sf)
            return (
                f"<tr>"
                f"<td style='padding:6px 10px; font-weight:bold; color:#888; "
                f"vertical-align:top; white-space:nowrap;'>{label}</td>"
                f"<td dir='auto' style='padding:6px 10px; background:{bg}; "
                f"color:{self._LOCAL_FG}; vertical-align:top;'>{local_html}</td>"
                f"<td dir='auto' style='padding:6px 10px; background:{bg}; "
                f"color:{self._SERVER_FG}; vertical-align:top;'>{server_html}</td>"
                f"</tr>"
            )

        rows_html: list[str] = []
        # Field rows.
        for i in range(max_len):
            rows_html.append(_row(f"F{i + 1}", local_fields[i], server_fields[i]))
        # Tags row — note hash covers flds only, so a tag-only change would
        # otherwise be invisible.
        rows_html.append(_row("Tags", local_tags, server_tags, force_diff=tags_differ))
        # Cards row — always show per-deck + total counts; highlight when they
        # differ. This is the only signal for the "card-count" status.
        local_cards_txt = f"{local_cards} in deck · {local_total} total"
        server_cards_txt = f"{server_cards} in deck · {server_total} total"
        rows_html.append(_row("Cards", local_cards_txt, server_cards_txt, force_diff=(cards_differ or total_differ)))
        # Note type row.
        rows_html.append(_row("Model", str(local_mid), str(server_mid), force_diff=mid_differ))
        # Modified row.
        rows_html.append(_row("Modified", _format_note_mod(local), _format_note_mod(server), force_diff=mod_differ))

        legend = (
            "<div style='padding:6px 10px; margin-bottom:8px; "
            "background:#1f1f1f; border:1px solid #333; border-radius:4px; "
            "font-size:12px; color:#888;'>"
            f"<span style='color:{self._LOCAL_FG};'>● Local</span> &nbsp;"
            f"<span style='color:{self._SERVER_FG};'>● Server</span> &nbsp;·&nbsp; "
            f"<span style='background:{self._DIFF_BG}; padding:2px 6px; "
            f"border-radius:2px; color:#ddd;'>row differs</span> &nbsp;"
            "<span style='background:#8b3a3a; color:#fff; padding:2px 6px; "
            "border-radius:2px;'>red</span> = removed locally &nbsp;"
            "<span style='background:#3a6b8b; color:#fff; padding:2px 6px; "
            "border-radius:2px;'>blue</span> = added on server"
            "</div>"
        )

        # --- Cards breakdown ------------------------------------------------
        # One row per card-template ord present on either side. A note has at
        # most one card per ord, so ord keys the comparison. Missing on one
        # side = that template's card was deleted / not yet generated there.
        local_card_list = local.get("cards") or []
        server_card_list = server.get("cards") or []
        local_by_ord = {int(c["ord"]): int(c.get("did", 0)) for c in local_card_list}
        server_by_ord = {int(c["ord"]): int(c.get("did", 0)) for c in server_card_list}
        # Resolve deck ids to names where we can: the inspected deck is in
        # deck_diff; its local/server entries carry id + name.
        deck_names: dict[int, str] = {}
        for side in ("local", "server"):
            dk = self._deck_diff.get(side) or {}
            if dk.get("id") is not None and dk.get("name"):
                deck_names[int(dk["id"])] = dk["name"]

        def _deck_label(did: int) -> str:
            name = deck_names.get(did)
            return f"{name} ({did})" if name else f"deck {did}"

        card_rows: list[str] = []
        for ord_ in sorted(set(local_by_ord) | set(server_by_ord)):
            l_did = local_by_ord.get(ord_)
            s_did = server_by_ord.get(ord_)
            l_txt = _deck_label(l_did) if l_did is not None else "—"
            s_txt = _deck_label(s_did) if s_did is not None else "—"
            missing_local = l_did is None
            missing_server = s_did is None
            bg = self._DIFF_BG if (missing_local or missing_server or l_did != s_did) else self._SAME_BG
            l_html = (
                f"<i style='color:#8b3a3a;'>(missing)</i>" if missing_local
                else f"<span style='color:{self._LOCAL_FG};'>{_esc(l_txt)}</span>"
            )
            s_html = (
                f"<i style='color:#3a6b8b;'>(missing)</i>" if missing_server
                else f"<span style='color:{self._SERVER_FG};'>{_esc(s_txt)}</span>"
            )
            card_rows.append(
                f"<tr>"
                f"<td style='padding:6px 10px; font-weight:bold; color:#888; "
                f"vertical-align:top; white-space:nowrap;'>Card {ord_ + 1}</td>"
                f"<td dir='auto' style='padding:6px 10px; background:{bg}; "
                f"vertical-align:top;'>{l_html}</td>"
                f"<td dir='auto' style='padding:6px 10px; background:{bg}; "
                f"vertical-align:top;'>{s_html}</td>"
                f"</tr>"
            )
        l_fg = self._LOCAL_FG
        s_fg = self._SERVER_FG
        cards_table_html = (
            "<div style='margin-top:14px;'><div style='padding:4px 10px; "
            "font-size:12px; color:#888;'>Cards (one row per card template)</div>"
            "<table style='border-collapse:collapse; width:100%;'>"
            "<tr>"
            "<th style='padding:6px 10px; text-align:left; color:#888; "
            "border-bottom:1px solid #333;'>Template</th>"
            f"<th style='padding:6px 10px; text-align:left; color:{l_fg}; "
            "border-bottom:1px solid #333;'>Local</th>"
            f"<th style='padding:6px 10px; text-align:left; color:{s_fg}; "
            "border-bottom:1px solid #333;'>Server</th>"
            "</tr>"
            f"{''.join(card_rows)}"
            "</table></div>"
        )

        rows_joined = "".join(rows_html)
        html = (
            "<html><body style='font-family: -apple-system, sans-serif; "
            "font-size: 14px; background:#1e1e1e; color:#ddd;'>"
            f"{guid_warning}"
            f"{banner}"
            f"{legend}"
            "<table style='border-collapse:collapse; width:100%;'>"
            "<tr>"
            "<th style='padding:6px 10px; text-align:left; color:#888; "
            "border-bottom:1px solid #333;'>Field</th>"
            f"<th style='padding:6px 10px; text-align:left; color:{l_fg}; "
            "border-bottom:1px solid #333;'>Local</th>"
            f"<th style='padding:6px 10px; text-align:left; color:{s_fg}; "
            "border-bottom:1px solid #333;'>Server</th>"
            "</tr>"
            f"{rows_joined}"
            "</table>"
            f"{cards_table_html}"
            "</body></html>"
        )
        self.browser.setHtml(html)

        self.status.setText(
            f"GUID: {self._guid}  ·  status: {self._diff.get('status', '—')}"
        )

        # Enable resolution buttons — always enabled so the user can try;
        # the click handlers explain what's missing.
        if hasattr(self, "accept_btn"):
            self.accept_btn.setEnabled(True)
        if hasattr(self, "push_btn"):
            self.push_btn.setEnabled(True)

    def _generate_guid(self) -> None:
        """Assign a unique GUID to the local note, fixing the root cause of
        empty-GUID ambiguity."""
        local_nid = int((self._local_note or {}).get("nid", 0))
        if not local_nid:
            tooltip("No local note to update.")
            return
        new_guid = inspect.generate_guid(mw.col, local_nid)
        tooltip(f"Generated GUID: {new_guid}")
        self._guid = new_guid
        self.setWindowTitle(f"Note diff — {new_guid[:12]}…")
        self._local_note = inspect.local_note_detail(mw.col, local_nid, new_guid)
        self._populate()

    def _accept_server(self) -> None:
        """Update the local note to match the server's fields, tags, and cards."""
        local = self._local_note or {}
        server = self._server_note
        if not server:
            tooltip("No server note to accept.")
            return
        nid = int(local.get("nid", 0))
        preview = inspect.preview_accept_server(local if local else None, server)
        if not self._confirm_action("Accept server", preview, "local"):
            return
        inspect.accept_server_note(mw.col, nid, server, deck_id=self._local_did)
        # Updating the note bumps its mod, so a full dual_sync now carries the
        # accepted version to BOTH Kelma and AnkiWeb (AnkiWeb only speaks the
        # wire protocol — there's no direct write endpoint).
        tooltip("Local note updated to match server. Syncing to AnkiWeb…")
        self.accept()
        engine.dual_sync()

    def _find_created_note(self, server_note: dict) -> int:
        """Find a note just created by accept_server (by guid)."""
        guid = server_note.get("guid") or ""
        if not guid:
            return 0
        row = mw.col.db.first("SELECT id FROM notes WHERE guid = ?", guid)
        return int(row[0]) if row else 0

    def _push_to_server(self) -> None:
        """Force the local note onto every sync target ("force local → server").

        Two steps, because Kelma and AnkiWeb are different beasts:

        1. Bump the local note's mod so the stock sync protocol treats it as
           changed — this is what carries the note to **AnkiWeb**, which only
           speaks the wire protocol (no direct write endpoint).
        2. Direct ``PUT /sync/notes/:guid`` to the **Kelma** server for an
           immediate, visible fix, then run the full ``dual_sync`` so both
           services converge on the local copy.
        """
        local = self._local_note or {}
        if not local:
            tooltip("No local note to push.")
            return
        nid = int(local.get("nid", 0))
        if not nid:
            tooltip("No local note to push.")
            return
        if not local.get("guid"):
            tooltip("Cannot push a note with an empty GUID — generate one first.")
            return
        preview = inspect.preview_push_local(local, self._server_note)
        if not self._confirm_action("Force local → server", preview, "server"):
            return
        # Step 1: bump local mod so the note is "changed" for BOTH protocols
        # (this is what reaches AnkiWeb).
        inspect.push_local_note(mw.col, nid)
        # Re-read the freshest local note (post-bump) to push directly to Kelma.
        payload = inspect.local_note_detail(mw.col, nid, local.get("guid", ""))
        if not payload:
            tooltip("Local note vanished.")
            return
        hkey = self._hkey
        endpoint = self._endpoint
        self.push_btn.setEnabled(False)
        self.status.setText("Pushing note and syncing to AnkiWeb…")

        def _done(future: Future) -> None:
            try:
                future.result()
            except Exception as err:
                # The direct Kelma PUT failed, but the local mod bump stands —
                # a dual_sync will still carry the note to both services.
                self.status.setText(f"Direct push failed ({err}); syncing anyway…")
            # Close the dialog and run the FULL dual sync so the change lands on
            # both Kelma and AnkiWeb via the wire protocol.
            tooltip("Forced local note to server. Syncing to AnkiWeb…")
            self.accept()
            engine.dual_sync()

        mw.taskman.run_in_background(
            lambda: inspect.write_server_note(hkey, endpoint, payload),
            _done,
            uses_collection=False,
        )

    def _confirm_action(self, action: str, preview: dict, target: str) -> bool:
        """Show a confirmation dialog with what will change. Returns True if confirmed."""
        from aqt.qt import QMessageBox
        parts = []
        if preview["fields"]:
            parts.append(f"  {len(preview['fields'])} field(s) will change")
        if preview.get("tags"):
            parts.append(f"  tags: {preview['tags']['old']!r} → {preview['tags']['new']!r}")
        if preview["cards_added"]:
            parts.append(f"  {len(preview['cards_added'])} card(s) will be added (ord: {preview['cards_added']})")
        if preview["cards_deleted"]:
            parts.append(f"  {len(preview['cards_deleted'])} card(s) will be deleted (ord: {preview['cards_deleted']})")
        if not parts:
            parts.append("  (no changes — sides already match)")
        direction = "local ← server" if target == "local" else "server ← local"
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle(f"Confirm: {action}")
        msg.setText(f"{action}: {direction}\n\nWhat will change:\n" + "\n".join(parts))
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return msg.exec() == QMessageBox.StandardButton.Yes


class ConflictDialog(QDialog):
    """Drills into one deck conflict and explains it note-by-note."""

    def __init__(self, parent, deck_diff: dict, local: dict, server: dict, hkey: str, endpoint: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Kelma — Conflict: {deck_diff['name']}")
        self.resize(850, 580)
        outer = QVBoxLayout(self)
        title = QLabel(deck_diff["name"])
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet("font-weight: bold;")
        outer.addWidget(title)
        hint = QLabel(
            "These are the notes that make the local and server deck hashes differ. "
            "Modified times determine which side newest-wins would take."
        )
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self._note_diffs = inspect.diff_deck_notes(local, server, deck_diff)
        self._deck_diff = deck_diff
        self._local = local
        self._server = server
        self._hkey = hkey
        self._endpoint = endpoint
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search server/local notes, GUIDs, status, card counts…")
        self.search.textChanged.connect(self._populate)
        outer.addWidget(self.search)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Note preview", "Difference", "Cards L/S", "GUID / nid", "Local modified", "Server modified"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self._note_clicked)
        outer.addWidget(self.table)

        controls = QHBoxLayout()
        self.show_matching = QCheckBox("Show matching notes")
        self.show_matching.toggled.connect(self._populate)
        controls.addWidget(self.show_matching)
        controls.addStretch()
        outer.addLayout(controls)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._populate()

    def _card_count_text(self, diff: dict) -> str:
        local_did = int((self._deck_diff.get("local") or {}).get("id", 0))
        server_did = int((self._deck_diff.get("server") or {}).get("id", 0))
        lc = _card_count_in_deck(diff.get("local"), local_did)
        sc = _card_count_in_deck(diff.get("server"), server_did)
        return f"{lc} / {sc}"

    def _row_search_text(self, diff: dict) -> str:
        return " ".join(
            str(part)
            for part in (
                diff.get("preview", ""),
                diff.get("guid", ""),
                diff.get("status", ""),
                _DIFF_LABEL.get(diff.get("status", ""), diff.get("status", "")),
                self._card_count_text(diff),
                (diff.get("local") or {}).get("nid", ""),
                (diff.get("server") or {}).get("nid", ""),
            )
        ).lower()

    def _populate(self, _checked: bool = False) -> None:
        query = self.search.text().strip().lower()
        rows = []
        for diff in self._note_diffs:
            if not query and not self.show_matching.isChecked() and diff["status"] == "in-sync":
                continue
            if query and query not in self._row_search_text(diff):
                continue
            rows.append(diff)
        self.table.setRowCount(len(rows))
        for row, diff in enumerate(rows):
            preview = QTableWidgetItem(diff["preview"])
            preview.setToolTip(f"Note GUID: {diff['guid']}")
            self.table.setItem(row, 0, preview)
            status = QTableWidgetItem(_DIFF_LABEL[diff["status"]])
            local_hash = (diff.get("local") or {}).get("hash", "—")
            server_hash = (diff.get("server") or {}).get("hash", "—")
            status.setToolTip(f"Local: {local_hash}\nServer: {server_hash}")
            self.table.setItem(row, 1, status)
            cards = QTableWidgetItem(self._card_count_text(diff))
            cards.setToolTip("Cards in this deck: local / server")
            self.table.setItem(row, 2, cards)
            local_nid = (diff.get("local") or {}).get("nid", "—")
            server_nid = (diff.get("server") or {}).get("nid", "—")
            guid_text = diff.get("guid") or "(empty GUID)"
            guid_item = QTableWidgetItem(f"{guid_text} · L {local_nid} / S {server_nid}")
            guid_item.setToolTip(
                f"GUID: {diff.get('guid') or '(empty)'}\nLocal nid: {local_nid}\nServer nid: {server_nid}"
            )
            self.table.setItem(row, 3, guid_item)
            self.table.setItem(row, 4, QTableWidgetItem(_format_note_mod(diff.get("local"))))
            self.table.setItem(row, 5, QTableWidgetItem(_format_note_mod(diff.get("server"))))

        counts = Counter(diff["status"] for diff in self._note_diffs)
        changed = len(self._note_diffs) - counts["in-sync"]
        parts = [
            f"{counts[label]} {label.replace('-', ' ')}"
            for label in (
                "conflict", "card-count", "deck-count", "deck-hash",
                "local-newer", "server-newer", "local-extra", "server-extra",
                "local-only", "server-only",
            )
            if counts[label]
        ]
        detail = ", ".join(parts) if parts else "no note-level differences"
        self.summary.setText(
            f"{changed} differing note(s): {detail}. "
            f"Showing {len(rows)} of {len(self._note_diffs)} notes. "
            "Search filters all visible server/local rows. Double-click a row to see fields and cards."
        )

    def _note_clicked(self, row: int, _col: int) -> None:
        """Open a field-by-field diff dialog for the double-clicked note."""
        query = self.search.text().strip().lower()
        rows = [
            diff for diff in self._note_diffs
            if (query or self.show_matching.isChecked() or diff["status"] != "in-sync")
            and (not query or query in self._row_search_text(diff))
        ]
        if row >= len(rows):
            return
        diff = rows[row]
        if diff["status"] in ("deck-count", "deck-hash"):
            tooltip(diff["preview"])
            return
        guid = diff["guid"]
        # Local nid from the manifest entry — unique per local note. For
        # server-only rows, do not fall back to guid: duplicate/empty GUIDs can
        # fetch an unrelated local note and hide the one-sided difference.
        local_manifest_note = diff.get("local")
        if local_manifest_note:
            local_nid = int(local_manifest_note.get("nid", 0))
            # Fetch the local note's full fields on the main thread (Qt-safe).
            local_note = inspect.local_note_detail(mw.col, local_nid, guid)
        else:
            local_note = None
        dlg = NoteDiffDialog(
            self, guid, local_note, self._hkey, self._endpoint,
            diff=diff, deck_diff=self._deck_diff,
        )
        dlg.exec()


class ServerSearchDialog(QDialog):
    """Search/browse notes that exist in the server manifest.

    This is intentionally available from the top-level Compare dialog, even
    when no deck conflict is selected. It gives users a direct inventory of
    what the server says exists, with card counts and GUID/nid identity.
    """

    def __init__(self, parent, local: dict, server: dict, hkey: str, endpoint: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kelma — Search server notes")
        self.resize(950, 620)
        self._local = local
        self._server = server
        self._hkey = hkey
        self._endpoint = endpoint
        self._rows = self._build_rows()

        outer = QVBoxLayout(self)
        hint = QLabel(
            "Search the server manifest directly. Double-click a row to fetch "
            "the server note fields and card-template list."
        )
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search preview, GUID, nid, deck ids, card counts…")
        self.search.textChanged.connect(self._populate)
        outer.addWidget(self.search)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Server preview", "GUID", "Server nid", "Cards", "Deck ids"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self._row_clicked)
        outer.addWidget(self.table)

        self.summary = QLabel()
        outer.addWidget(self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._populate()

    def _build_rows(self) -> list[dict]:
        # Index local notes by guid so we can pair a server note with its local
        # counterpart for the drill-in (avoids always-empty local side).
        self._local_by_guid: dict[str, list[dict]] = {}
        for note in self._local.get("notes", []) or []:
            g = note.get("guid") or ""
            self._local_by_guid.setdefault(g, []).append(note)

        rows = []
        for note in self._server.get("notes", []) or []:
            total = _total_card_count(note)
            rows.append(
                {
                    "note": note,
                    "preview": note.get("preview") or "(no preview)",
                    "guid": note.get("guid") or "",
                    "nid": note.get("nid", ""),
                    "cards": total,
                    "decks": note.get("decks", []) or [],
                }
            )
        rows.sort(key=lambda r: (str(r["preview"]).lower(), str(r["guid"]), int(r["nid"] or 0)))
        return rows

    def _row_text(self, row: dict) -> str:
        return " ".join(
            str(x) for x in (
                row["preview"], row["guid"], row["nid"], row["cards"], row["decks"]
            )
        ).lower()

    def _populate(self, _text: str = "") -> None:
        query = self.search.text().strip().lower()
        rows = [r for r in self._rows if not query or query in self._row_text(r)]
        self._visible_rows = rows
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(row["preview"]))
            guid_item = QTableWidgetItem(row["guid"] or "(empty GUID)")
            guid_item.setToolTip(row["guid"] or "(empty GUID)")
            self.table.setItem(i, 1, guid_item)
            self.table.setItem(i, 2, QTableWidgetItem(str(row["nid"])))
            self.table.setItem(i, 3, QTableWidgetItem(str(row["cards"])))
            self.table.setItem(i, 4, QTableWidgetItem(", ".join(str(d) for d in row["decks"])))
        server_cards = sum(int(r["cards"] or 0) for r in self._rows)
        self.summary.setText(
            f"Showing {len(rows)} of {len(self._rows)} server notes · "
            f"{server_cards} server cards total"
        )

    def _row_clicked(self, row: int, _col: int) -> None:
        if row >= len(getattr(self, "_visible_rows", [])):
            return
        server_note = self._visible_rows[row]["note"]
        guid = server_note.get("guid") or ""
        # Try to find the matching local note from the local manifest. Prefer
        # an exact hash+mod match; fall back to the first note with the same
        # guid. This avoids the local side always being empty.
        local_note = None
        local_manifest_note = None
        candidates = self._local_by_guid.get(guid, [])
        if candidates:
            local_manifest_note = next(
                (n for n in candidates
                 if n.get("hash") == server_note.get("hash")
                 and n.get("mod") == server_note.get("mod")),
                candidates[0],
            )
            local_nid = int(local_manifest_note.get("nid", 0))
            local_note = inspect.local_note_detail(mw.col, local_nid, guid)
        dlg = NoteDiffDialog(
            self,
            guid,
            local_note,
            self._hkey,
            self._endpoint,
            diff={
                "guid": guid,
                "status": "server-only" if not local_manifest_note else "in-sync",
                "server": server_note,
                "local": local_manifest_note,
            },
            deck_diff={},
        )
        dlg.exec()


def _format_note_mod(note) -> str:
    if not note or not note.get("mod"):
        return "—"
    try:
        return datetime.fromtimestamp(note["mod"]).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, TypeError, ValueError):
        return str(note["mod"])


class CompareDialog(QDialog):
    """Shows the server's state vs the local master, deck-by-deck, so the user
    can see what will change before committing to a sync. See
    ``docs/REDESIGN.md``."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kelma — Compare with server")
        if branding.logo_enabled():
            self.setWindowIcon(branding.star_icon())
        self.resize(560, 520)
        outer = QVBoxLayout(self)
        outer.addWidget(_brand_header("Compare"))
        outer.addWidget(QLabel(
            "This shows what differs between this collection and the KelmaSync "
            "server, before you sync. Syncing applies newest-wins per note."
        ))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Deck", "Status", "Cards (L/S)"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellClicked.connect(self._row_clicked)
        outer.addWidget(self.table)

        filters = QHBoxLayout()
        self.show_matching = QCheckBox("Show decks already in sync")
        self.show_matching.toggled.connect(self._populate)
        self.show_matching.setEnabled(False)
        filters.addWidget(self.show_matching)
        filters.addStretch()
        filters.addWidget(QLabel("One-sided decks:"))
        self.one_sided = QComboBox()
        self.one_sided.addItem("Hide one-sided", "none")
        self.one_sided.addItem("Show local + server only", "both")
        self.one_sided.addItem("Show local only", "local")
        self.one_sided.addItem("Show server only", "server")
        self.one_sided.currentIndexChanged.connect(self._populate)
        filters.addWidget(self.one_sided)
        outer.addLayout(filters)

        self.status_label = QLabel("Loading…")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.search_server_btn = buttons.addButton(
            "Search server…", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.search_server_btn.setEnabled(False)
        self.search_server_btn.clicked.connect(self._search_server)
        self.sync_btn = buttons.addButton(
            "Sync now", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.sync_btn.setEnabled(False)
        buttons.accepted.connect(self._on_sync)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        # Defer the fetch so the dialog paints first.
        from aqt.qt import QTimer
        QTimer.singleShot(0, self._load)

    def _load(self) -> None:
        self.status_label.setText("Fetching server state…")
        # addonManager/Qt access must stay on the main thread. Capture plain
        # strings here, then do only the blocking HTTP request in the worker.
        cfg = config.get()
        hkey = cfg["kelmasync_hkey"]
        endpoint = cfg["kelmasync_url"] or consts.DEFAULT_KELMA_URL
        if not hkey:
            self.status_label.setText(
                "Not logged in to KelmaSync. Open Settings & deck routing first."
            )
            return
        self._hkey = hkey
        self._endpoint = endpoint
        mw.taskman.run_in_background(
            lambda: inspect.fetch_server_manifest(hkey, endpoint),
            self._server_loaded,
        )

    def _server_loaded(self, future: "Future[object]") -> None:
        try:
            server = future.result()
        except Exception as err:
            self.status_label.setText(
                f"Could not fetch server state: {err}"
            )
            return
        self.status_label.setText("Reading local collection…")
        local = inspect.build_local_manifest(mw.col, consts.KELMA)
        self._local_manifest = local
        self._server_manifest = server
        self._diffs = inspect.diff_manifests(local, server)
        n_changed = sum(1 for d in self._diffs if d["status"] != "in-sync")
        n_matching = len(self._diffs) - n_changed
        self.show_matching.setEnabled(n_matching > 0)
        self.sync_btn.setEnabled(n_changed > 0)
        self.search_server_btn.setEnabled(True)
        self._populate()

    def _populate(self, _checked: bool = False) -> None:
        one_sided = self.one_sided.currentData()
        diffs = []
        for diff in getattr(self, "_diffs", []):
            status = diff["status"]
            if status == "in-sync" and not self.show_matching.isChecked():
                continue
            if status == "local-only" and one_sided not in ("local", "both"):
                continue
            if status == "server-only" and one_sided not in ("server", "both"):
                continue
            diffs.append(diff)
        diffs.sort(key=lambda d: (_DIFF_PRIORITY[d["status"]], d["name"].lower()))
        self._visible_diffs = diffs
        self.table.setRowCount(len(diffs))
        for row, d in enumerate(diffs):
            name = QTableWidgetItem(d["name"])
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, name)
            status = QTableWidgetItem(_DIFF_LABEL.get(d["status"], d["status"]))
            status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, status)
            l = d.get("local")
            s = d.get("server")
            lc = str(l["cards"]) if l else "0"
            sc = str(s["cards"]) if s else "0"
            counts = QTableWidgetItem(f"{lc} / {sc}")
            counts.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, counts)
        if hasattr(self, "_diffs"):
            n_changed = sum(1 for d in self._diffs if d["status"] != "in-sync")
            n_matching = len(self._diffs) - n_changed
            n_conflicts = sum(1 for d in self._diffs if d["status"] == "conflict")
            self.status_label.setText(
                f"Showing {len(diffs)} of {len(self._diffs)} decks: "
                f"{n_conflicts} conflict(s), {n_changed} differ, "
                f"{n_matching} in sync. Click a conflict row for note details."
            )

    def _row_clicked(self, row: int, _column: int) -> None:
        if row >= len(getattr(self, "_visible_diffs", [])):
            return
        deck_diff = self._visible_diffs[row]
        if deck_diff["status"] != "conflict":
            return
        ConflictDialog(
            self,
            deck_diff,
            self._local_manifest,
            self._server_manifest,
            self._hkey,
            self._endpoint,
        ).exec()

    def _search_server(self) -> None:
        if not getattr(self, "_server_manifest", None):
            tooltip("Server manifest is not loaded yet.")
            return
        ServerSearchDialog(
            self,
            getattr(self, "_local_manifest", {}),
            self._server_manifest,
            self._hkey,
            self._endpoint,
        ).exec()

    def _on_sync(self) -> None:
        self.accept()
        engine.dual_sync(only=consts.KELMA)


class SettingsDialog(QDialog):
    """One window: log in to each service, and pick which decks sync where."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kelma — Settings & deck routing")
        if branding.logo_enabled():
            self.setWindowIcon(branding.star_icon())
        self.resize(660, 620)
        # Shift-click range toggling state. _suppress guards programmatic edits
        # from re-entering the itemChanged handler.
        self._suppress = False
        self._anchor = {col: None for col in _COL.values()}
        outer = QVBoxLayout(self)
        outer.addWidget(_brand_header("Kelma"))

        # --- Accounts ---------------------------------------------------------
        accounts = QGroupBox("Accounts")
        grid = QGridLayout(accounts)
        self._status = {}
        for row, service in enumerate(config.ui_services()):
            grid.addWidget(QLabel(f"<b>{consts.SERVICE_LABEL[service]}</b>"), row, 0)
            status = QLabel()
            self._status[service] = status
            grid.addWidget(status, row, 1)
            btn = QPushButton("Log in / change…")
            btn.clicked.connect(lambda _=False, s=service: self._login(s))
            grid.addWidget(btn, row, 2)
        grid.setColumnStretch(1, 1)
        outer.addWidget(accounts)

        # --- Options ----------------------------------------------------------
        cfg = config.get()
        opts = QHBoxLayout()
        self.enabled_cb = QCheckBox("Enable Kelma sync")
        self.enabled_cb.setChecked(cfg["enabled"])
        self.media_cb = QCheckBox("Sync media")
        self.media_cb.setChecked(cfg["sync_media"])
        self.block_cb = QCheckBox("Block Anki's own sync")
        self.block_cb.setChecked(cfg.get("block_native_sync", True))
        self.block_cb.setToolTip(
            "Stop Anki from syncing your main collection on its own "
            "(auto-sync, the Sync button, the Y shortcut). Only Kelma's shadow "
            "collections sync — prevents conflicting double-syncs."
        )
        opts.addWidget(self.enabled_cb)
        opts.addWidget(self.media_cb)
        opts.addWidget(self.block_cb)
        opts.addStretch()
        outer.addLayout(opts)

        # KelmaSync sync path (AnkiWeb is always legacy/stock).
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("KelmaSync mode:"))
        self.path_combo = QComboBox()
        for mode in consts.PATH_MODES:
            self.path_combo.addItem(consts.PATH_LABEL[mode], mode)
        current = cfg.get("kelmasync_path", consts.PATH_AUTO)
        idx = self.path_combo.findData(current)
        self.path_combo.setCurrentIndex(idx if idx >= 0 else 0)
        path_row.addWidget(self.path_combo)
        path_row.addStretch()
        outer.addLayout(path_row)

        # --- Deck routing -----------------------------------------------------
        outer.addWidget(
            QLabel(
                "Tick where each deck should sync (a deck with neither ticked is "
                "not synced; new decks default to KelmaSync). <b>Shift-click</b> a box "
                "to set every deck in the range. The cloud columns show pending "
                "changes since that cloud last synced: <b>+n</b> added, "
                "<b>~n</b> changed, <b>✓</b> in sync."
            )
        )
        self.summary = QLabel()
        self.summary.setStyleSheet("color: palette(mid);")
        outer.addWidget(self.summary)

        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter decks…")
        self.filter.textChanged.connect(self._apply_filter)
        bar.addWidget(self.filter)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_pending)
        bar.addWidget(refresh)
        bulk_buttons = [
            ("All KelmaSync", consts.KELMA, True),
            ("Clear KelmaSync", consts.KELMA, False),
        ]
        if consts.ANKIWEB in config.ui_services():
            bulk_buttons += [
                ("All AnkiWeb", consts.ANKIWEB, True),
                ("Clear AnkiWeb", consts.ANKIWEB, False),
            ]
        for label, service, value in bulk_buttons:
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, s=service, v=value: self._bulk(s, v))
            bar.addWidget(b)
        outer.addLayout(bar)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Deck", "KelmaSync", "AnkiWeb"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        if consts.ANKIWEB not in config.ui_services():
            self.table.setColumnHidden(_COL[consts.ANKIWEB], True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        outer.addWidget(self.table)
        self._compute_status()
        self._populate()
        self.summary.setText(self._summary_text())
        # Connect after the initial populate so it only fires on user edits.
        self.table.itemChanged.connect(self._on_item_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._refresh_status()

    # -- accounts -------------------------------------------------------------
    def _login(self, service: str) -> None:
        auth.login(service, on_success=self._refresh_status)

    def _refresh_status(self) -> None:
        cfg = config.get()
        # Only the services with a status widget — ui_services() drops AnkiWeb in
        # KelmaSync-only mode, so iterating consts.SERVICES would KeyError there.
        for service in config.ui_services():
            user = cfg["kelmasync_user"] if service == consts.KELMA else cfg["ankiweb_user"]
            if config.has_credentials(service):
                self._status[service].setText(f"✓ Logged in as <b>{user or '?'}</b>")
            else:
                self._status[service].setText("<i>not logged in</i>")

    # -- deck status ----------------------------------------------------------
    def _compute_status(self) -> None:
        names = [d.name for d in mw.col.decks.all_names_and_ids()]
        self._deck_names = names
        self._pending = {
            s: state.pending_for_service(mw.col, names, s) for s in consts.SERVICES
        }
        self._deletions = state.pending_deletions(mw.col)
        self._service_counts = {
            s: _collection_counts_for_decks(config.decks_for_service(s, names))
            for s in config.ui_services()
        }
        self.table.setHorizontalHeaderLabels(["Deck", "KelmaSync", "AnkiWeb"])

    def _status_text(self, service: str, name: str) -> str:
        added, changed = self._pending.get(service, {}).get(name, (0, 0))
        if not added and not changed:
            return "✓"
        parts = []
        if added:
            parts.append(f"+{added}")
        if changed:
            parts.append(f"~{changed}")
        return " ".join(parts)

    def _summary_text(self) -> str:
        parts = []
        for s in consts.SERVICES:
            if not config.has_credentials(s):
                continue
            pend = self._pending.get(s, {})
            ta = sum(a for a, _ in pend.values())
            tc = sum(c for _, c in pend.values())
            ndirty = sum(1 for a, c in pend.values() if a or c)
            meta = state.last_sync(s)
            when = _ago(meta["at"]) if meta and meta.get("at") else "never"
            routed = config.decks_for_service(s, getattr(self, "_deck_names", []))
            notes, cards = getattr(self, "_service_counts", {}).get(s, (0, 0))
            parts.append(
                f"<b>{consts.SERVICE_LABEL[s]}</b>: {len(routed)} deck(s), "
                f"{notes} notes / {cards} cards; {ndirty} deck(s) pending "
                f"(+{ta} ~{tc}), synced {when}"
            )
        if self._deletions:
            parts.append(f"{self._deletions} deletion(s) pending")
        return " &nbsp;·&nbsp; ".join(parts) if parts else "No accounts logged in."

    def _refresh_pending(self) -> None:
        self._compute_status()
        self._suppress = True
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text()
            for service, col in _COL.items():
                item = self.table.item(row, col)
                checked = item.checkState() == Qt.CheckState.Checked
                item.setText(self._status_text(service, name) if checked else "")
        self._suppress = False
        self.summary.setText(self._summary_text())

    # -- deck table -----------------------------------------------------------
    def _populate(self) -> None:
        names = sorted(
            (d.name for d in mw.col.decks.all_names_and_ids()), key=str.lower
        )
        self.table.setRowCount(len(names))
        for row, name in enumerate(names):
            name_item = QTableWidgetItem(name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, name_item)
            services = config.services_for_deck(name)
            for service, col in _COL.items():
                item = QTableWidgetItem()
                item.setFlags(_CHECKABLE)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                checked = service in services
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                item.setText(self._status_text(service, name) if checked else "")
                self.table.setItem(row, col, item)

    def _apply_filter(self, text: str) -> None:
        needle = text.lower()
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().lower()
            self.table.setRowHidden(row, needle not in name)

    def _bulk(self, service: str, value: bool) -> None:
        col = _COL[service]
        check = Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        self._suppress = True
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                item = self.table.item(row, col)
                item.setCheckState(check)
                name = self.table.item(row, 0).text()
                item.setText(self._status_text(service, name) if value else "")
        self._suppress = False
        self._anchor[col] = None

    def _on_item_changed(self, item) -> None:
        """Shift-click a checkbox to set every (visible) deck between it and the
        last box you changed in that column to the same state."""
        if self._suppress:
            return
        col = item.column()
        if col not in _COL.values():
            return
        row = item.row()
        check = item.checkState()
        anchor = self._anchor.get(col)
        shift = QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
        service = next(s for s, c in _COL.items() if c == col)
        checked = check == Qt.CheckState.Checked
        if shift and anchor is not None and anchor != row:
            lo, hi = sorted((anchor, row))
            self._suppress = True
            for r in range(lo, hi + 1):
                if self.table.isRowHidden(r):
                    continue
                cell = self.table.item(r, col)
                if cell is not None:
                    cell.setCheckState(check)
                    name = self.table.item(r, 0).text()
                    cell.setText(self._status_text(service, name) if checked else "")
            self._suppress = False
        name = self.table.item(row, 0).text()
        item.setText(self._status_text(service, name) if checked else "")
        self._anchor[col] = row

    def _save(self) -> None:
        routing: dict[str, list[str]] = {}
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text()
            services = [
                service
                for service, col in _COL.items()
                if self.table.item(row, col).checkState() == Qt.CheckState.Checked
            ]
            routing[name] = services
        cfg = config.get()
        cfg["deck_routing"] = routing
        cfg["enabled"] = self.enabled_cb.isChecked()
        cfg["sync_media"] = self.media_cb.isChecked()
        cfg["block_native_sync"] = self.block_cb.isChecked()
        cfg["kelmasync_path"] = self.path_combo.currentData()
        config.save(cfg)
        capabilities.clear_cache()  # re-probe under the new setting/URL
        tooltip("Kelma settings saved.", parent=mw)
        self.accept()


# -----------------------------------------------------------------------------
# Sync button integration — clicking Sync opens a split menu: KelmaSync /
# AnkiWeb / both, with a details header.
# -----------------------------------------------------------------------------
def _ago(ts: int) -> str:
    import time

    secs = max(0, int(time.time()) - int(ts))
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h ago"
    return f"{hrs // 24}d ago"


def _service_line_html(service: str, deck_names: list[str], cfg: dict) -> str:
    """The status text for one account row: gold label (the clickable cue), the
    rest muted, and a trailing chevron. The enclosing row widget handles the
    click and hover, so no anchor is needed here."""
    label = consts.SERVICE_LABEL[service]
    if not config.has_credentials(service):
        body = "<i>not logged in</i> — sign in or create an account"
    else:
        service_decks = config.decks_for_service(service, deck_names)
        decks = len(service_decks)
        notes, cards = _collection_counts_for_decks(service_decks)
        user = cfg["kelmasync_user"] if service == consts.KELMA else cfg["ankiweb_user"]
        extra = ""
        if service == consts.KELMA:
            extra = f" · {cfg.get('kelmasync_path', consts.PATH_AUTO)}"
        meta = state.last_sync(service)
        when = _ago(meta["at"]) if meta and meta.get("at") else "never"
        body = f"{user or '?'} · {decks} decks · {notes} notes/{cards} cards{extra} · synced {when}"
    accent = branding.accent()
    return (
        f'<span style="color:{accent}; font-weight:bold;">{label}</span>'
        f'<span style="color:#c9c9c9;">: {body}</span>'
        f' <span style="color:{accent};">›</span>'
    )


class _AccountRow(QLabel):
    """A clickable account status line in the sync popup, with a menu-item-style
    hover highlight and a pointing-hand cursor."""

    def __init__(self, html: str, on_click) -> None:
        super().__init__(html)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QLabel { padding: 6px 14px; }"
            f"QLabel:hover {{ background-color: {branding.accent_rgba(0.16)}; }}"
        )

    def mousePressEvent(self, ev) -> None:  # noqa: N802 - Qt override
        self._on_click()


def _brand_header(text: str) -> QWidget:
    """A menu/dialog header row: the Kelma star logo (if enabled) + a title,
    tinted with the Kelma accent when the theme toggle is on."""
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(14, 0, 14, 6)
    row.setSpacing(8)
    if branding.logo_enabled():
        icon = QLabel()
        icon.setPixmap(branding.star_pixmap(18))
        row.addWidget(icon)
    if branding.theme_enabled():
        title = QLabel(f'<b style="color:{branding.accent()};">{text}</b>')
    else:
        title = QLabel(f"<b>{text}</b>")
    row.addWidget(title)
    row.addStretch(1)
    return w


def _sync_menu() -> None:
    deck_names = [d.name for d in mw.col.decks.all_names_and_ids()]
    menu = QMenu(mw)

    pending = {"login": None}

    def _on_account_click(service: str) -> None:
        # Defer: opening the auth modal from inside the menu's own modal loop
        # would nest event loops and freeze the UI. Record the pick, close the
        # menu, and act once exec() returns (same pattern as the actions below).
        pending["login"] = service
        menu.close()

    cfg = config.get()
    container = QWidget()
    box = QVBoxLayout(container)
    box.setContentsMargins(0, 8, 0, 4)
    box.setSpacing(0)
    box.addWidget(_brand_header("Kelma sync"))
    for service in config.ui_services():
        row = _AccountRow(
            _service_line_html(service, deck_names, cfg),
            lambda s=service: _on_account_click(s),
        )
        box.addWidget(row)

    wa = QWidgetAction(menu)
    wa.setDefaultWidget(container)
    menu.addAction(wa)
    menu.addSeparator()

    aw = consts.ANKIWEB in config.ui_services()
    both = a_w = None
    if aw:
        both = menu.addAction("Sync KelmaSync + AnkiWeb")
        a_k = menu.addAction("Sync KelmaSync only")
        a_w = menu.addAction("Sync AnkiWeb only")
    else:
        a_k = menu.addAction("Sync now")
    menu.addSeparator()
    a_compare = menu.addAction("Compare with server…")
    a_set = menu.addAction("Settings && deck routing…")

    # Dispatch AFTER the menu's modal loop closes — starting a progress dialog
    # from inside a triggered handler nests modal loops and freezes the UI.
    chosen = menu.exec(QCursor.pos())

    # A clicked account link wins (menu.close() makes exec() return None). On a
    # successful login/register, reopen the menu so the fresh status shows.
    if pending["login"] in consts.SERVICES:
        auth.login(pending["login"], on_success=_sync_menu)
        return
    if chosen is None:
        return  # menu dismissed
    if both is not None and chosen is both:
        engine.dual_sync()
    elif chosen is a_k:
        engine.dual_sync(only=consts.KELMA)
    elif a_w is not None and chosen is a_w:
        engine.dual_sync(only=consts.ANKIWEB)
    elif chosen is a_compare:
        CompareDialog(mw).exec()
    elif chosen is a_set:
        SettingsDialog(mw).exec()


def _wrapped_sync() -> None:
    cfg = config.get()
    if not cfg["enabled"] or not cfg["wrap_sync_button"]:
        if _orig_sync:
            return _orig_sync()
        return
    _v2_sync_menu()


def _install_sync_hook() -> None:
    global _orig_sync
    if _orig_sync is not None:
        return
    _orig_sync = mw.on_sync_button_clicked
    mw.on_sync_button_clicked = _wrapped_sync
    mw.onSync = _wrapped_sync  # legacy alias used by some callers


_orig_collection_sync = None


def _install_native_sync_guard() -> None:
    """Block the master collection from syncing to AnkiWeb on its own.

    `_sync_collection_and_media` is the single chokepoint for Anki's native sync —
    auto-sync on open/close, the Y shortcut, and the (unwrapped) Sync button all
    route through it. While Kelma is enabled and "block native sync" is on, we
    make it a no-op (still calling the continuation so open/close proceeds), so
    only the Kelma shadows ever write to AnkiWeb — no two-writers conflict.
    """
    global _orig_collection_sync
    if _orig_collection_sync is not None:
        return
    _orig_collection_sync = mw._sync_collection_and_media

    def guarded(after_sync):
        cfg = config.get()
        if cfg.get("enabled", True) and cfg.get("block_native_sync", True):
            after_sync()  # skip the native sync; let the flow continue
        else:
            _orig_collection_sync(after_sync)

    mw._sync_collection_and_media = guarded


# -----------------------------------------------------------------------------
# KelmaSync v2 experimental note-only sync
# -----------------------------------------------------------------------------
def _ensure_v2_vendor() -> None:
    """Expose the vendored .kelma_sync_v2 package as top-level kelma_sync_v2.

    We intentionally do NOT add the addon directory to sys.path, because this
    addon has files like inspect.py that can shadow Python stdlib modules.
    """
    if "kelma_sync_v2" in sys.modules:
        return
    from . import kelma_sync_v2 as vendored
    sys.modules["kelma_sync_v2"] = vendored


def _v2_kelma_deck_names() -> list[str]:
    names = [d.name for d in mw.col.decks.all_names_and_ids()]
    return config.decks_for_service(consts.KELMA, names)


def _v2_client_or_login():
    """Return a V2Client, prompting for credentials if no token is saved."""
    try:
        _ensure_v2_vendor()
        from kelma_sync_v2.client import V2Client
    except Exception as err:  # noqa: BLE001
        tooltip(f"KelmaSync client package is not installed: {err}")
        return None

    cfg = config.get()
    endpoint = cfg.get("v2_url") or "http://localhost:8081"
    token = cfg.get("v2_token") or ""
    client = V2Client(endpoint, token=token, timeout=8)
    if token:
        return client

    username, ok = QInputDialog.getText(mw, "KelmaSync login", "Username:", text=cfg.get("v2_username", ""))
    if not ok or not username:
        return None
    password, ok = QInputDialog.getText(mw, "KelmaSync login", "Password:", QLineEdit.EchoMode.Password)
    if not ok or not password:
        return None
    label, ok = QInputDialog.getText(mw, "KelmaSync login", "Client label:", text=cfg.get("v2_client_label", "Anki plugin"))
    if not ok or not label:
        return None

    try:
        auth_out = client.login(username, password, label)
    except Exception as err:  # noqa: BLE001
        tooltip(f"KelmaSync v2 login failed: {err}")
        return None
    cfg["v2_url"] = endpoint
    cfg["v2_username"] = username
    cfg["v2_token"] = auth_out.token
    cfg["v2_client_id"] = auth_out.client_id
    cfg["v2_client_label"] = label
    config.save(cfg)
    tooltip("KelmaSync login saved.")
    return client


class V2NoteConflictDialog(QDialog):
    """Simple v2 note conflict resolver.

    The client has the last say: accept server, or force local to server.
    """

    def __init__(self, parent, client, conflicts: list[dict]) -> None:
        super().__init__(parent)
        self._client = client
        self._conflicts = conflicts
        self.setWindowTitle("KelmaSync v2 note conflicts")
        self.resize(900, 420)

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("These notes changed on both this device and the server. Choose a resolution."))

        self.table = QTableWidget(len(conflicts), 4)
        self.table.setHorizontalHeaderLabels(["GUID", "Server", "Client", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        outer.addWidget(self.table)

        for row, c in enumerate(conflicts):
            guid = c.get("guid", "")
            server = c.get("server") or {}
            client_payload = c.get("client") or {}
            self.table.setItem(row, 0, QTableWidgetItem(guid))
            self.table.setItem(row, 1, QTableWidgetItem(_v2_preview(server)))
            self.table.setItem(row, 2, QTableWidgetItem(_v2_preview(client_payload)))
            btns = QWidget()
            hb = QHBoxLayout(btns)
            hb.setContentsMargins(0, 0, 0, 0)
            accept = QPushButton("Accept server")
            force = QPushButton("Force local")
            accept.clicked.connect(lambda _=False, g=guid: self._accept_server(g))
            force.clicked.connect(lambda _=False, g=guid: self._force_local(g))
            hb.addWidget(accept)
            hb.addWidget(force)
            self.table.setCellWidget(row, 3, btns)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _accept_server(self, guid: str) -> None:
        try:
            from kelma_sync_v2.note_sync import accept_server_note
            accept_server_note(mw.col, self._client, guid)
        except Exception as err:  # noqa: BLE001
            tooltip(f"Accept server failed: {err}")
            return
        tooltip(f"Accepted server note {guid[:12]}")
        self.accept()

    def _force_local(self, guid: str) -> None:
        try:
            from kelma_sync_v2.note_sync import force_local_note
            force_local_note(mw.col, self._client, guid)
        except Exception as err:  # noqa: BLE001
            tooltip(f"Force local failed: {err}")
            return
        tooltip(f"Forced local note {guid[:12]} to server")
        self.accept()


class V2FullDiffDialog(QDialog):
    """Full server-vs-local compare with per-item and batch resolution."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("KelmaSync compare")
        self.resize(1000, 640)
        self._client = None

        layout = QVBoxLayout(self)
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Resource:"))
        self.resource_combo = QComboBox()
        self.resource_combo.addItems(["Notes", "Notetypes", "Decks", "Cards"])
        self.resource_combo.currentIndexChanged.connect(self._populate)
        top_row.addWidget(self.resource_combo)
        top_row.addStretch()

        self.btn_accept_all = QPushButton("Accept all server")
        self.btn_accept_all.clicked.connect(self._accept_all)
        top_row.addWidget(self.btn_accept_all)
        self.btn_force_all = QPushButton("Force all local")
        self.btn_force_all.clicked.connect(self._force_all)
        top_row.addWidget(self.btn_force_all)
        layout.addLayout(top_row)

        self.status_label = QLabel("Loading…")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Key", "Status", "Local", "Server", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._diff = None
        self._load()

    def _on_progress(self, text: str) -> None:
        self.status_label.setText(text)
        QApplication.processEvents()

    def _load(self) -> None:
        # Hard guard: a sync task owns Anki's collection queue. If Compare starts
        # while sync is running, do NOT queue behind it and look hung.
        global _V2_ACTIVE_ACTION
        p = self._on_progress
        blocked = _v2_active_message()
        if blocked:
            p(f"⚠ {blocked}")
            self.btn_accept_all.setEnabled(False)
            self.btn_force_all.setEnabled(False)
            return
        _V2_ACTIVE_ACTION = "compare"
        try:
            try:
                p("Connecting…")
                client = _v2_client_or_login()
            except Exception as err:  # noqa: BLE001
                p(f"⚠ Login error: {err}")
                return
            if client is None:
                p("Not logged in. Open the sync menu → Settings to log in.")
                return
            self._client = client
            deck_names = _v2_kelma_deck_names()
            if not deck_names:
                p("No decks are picked for KelmaSync. Open Settings → deck routing and tick KelmaSync for at least one deck.")
                return
            p(f"Scope: {len(deck_names)} KelmaSync deck(s)")

            import time as _t
            try:
                p("Contacting server…")
                t0 = _t.time()
                server = client.manifest()
                from kelma_sync_v2.content_sync import _scope_server_manifest_to_decks
                server = _scope_server_manifest_to_decks(client, server, deck_names, progress=p)
            except Exception as err:  # noqa: BLE001
                p(f"⚠ Server fetch failed: {err}")
                tooltip(f"KelmaSync compare: server fetch failed: {err}")
                return
            p(
                f"Server: {len(server.get('notes', []))} notes, "
                f"{len(server.get('cards', []))} cards, "
                f"{len(server.get('notetypes', []))} notetypes, "
                f"{len(server.get('decks', []))} decks ({_t.time()-t0:.1f}s)"
            )

            try:
                from kelma_sync_v2 import anki_local
                local = anki_local.local_manifest(mw.col, deck_names=deck_names, progress=p)
            except Exception as err:  # noqa: BLE001
                p(f"⚠ Reading local collection failed: {err}")
                tooltip(f"KelmaSync compare: local read failed: {err}")
                return

            try:
                from kelma_sync_v2.full_diff import _diff_keyed
                p("Comparing notes…")
                notes = _diff_keyed(local.get("notes", []), server.get("notes", []), "guid")
                p("Comparing cards…")
                cards = _diff_keyed(local.get("cards", []), server.get("cards", []), "card_id")
                p("Comparing notetypes…")
                notetypes = _diff_keyed(local.get("notetypes", []), server.get("notetypes", []), "notetype_id")
                p("Comparing decks…")
                decks = _diff_keyed(local.get("decks", []), server.get("decks", []), "name")
                self._diff = type("FullDiff", (), {
                    "notes": notes, "cards": cards, "notetypes": notetypes, "decks": decks,
                    "server_time": server.get("server_time", ""),
                })()
            except Exception as err:  # noqa: BLE001
                p(f"⚠ Compare failed: {err}")
                return

            try:
                p("Building table…")
                self._populate()
            except Exception as err:  # noqa: BLE001
                p(f"⚠ Failed to render: {err}")
        finally:
            if _V2_ACTIVE_ACTION == "compare":
                _V2_ACTIVE_ACTION = None

    def _resource_key(self) -> str:
        return {0: "notes", 1: "notetypes", 2: "decks", 3: "cards"}.get(
            self.resource_combo.currentIndex(), "notes"
        )

    def _current_entries(self) -> list:
        if self._diff is None:
            return []
        key = self._resource_key()
        return getattr(self._diff, key, [])

    def _changed_entries(self) -> list:
        return [e for e in self._current_entries() if e.status != "in-sync"]

    def _populate(self) -> None:
        if self._diff is None:
            return
        entries = self._current_entries()
        changed = self._changed_entries()
        total = len(entries)
        # Only render changed rows (text-only). Rendering thousands of per-row
        # button widgets freezes Qt; instead select rows and use the top
        # Accept/Force buttons.
        self._rows = changed
        self.status_label.setText(
            f"{total} total · {len(changed)} changed  —  select rows, then Accept/Force (or use all)"
        )
        # Cap rendered rows so a huge diff (e.g. thousands of local-only cards)
        # doesn't freeze Qt. The batch actions still operate on all changed.
        cap = 2000
        shown = changed[:cap]
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(shown))
            for i, entry in enumerate(shown):
                self.table.setItem(i, 0, QTableWidgetItem(entry.key[:60]))
                self.table.setItem(i, 1, QTableWidgetItem(entry.status))
                self.table.setItem(i, 2, QTableWidgetItem(_diff_local_preview(entry)))
                self.table.setItem(i, 3, QTableWidgetItem(_diff_server_preview(entry)))
        finally:
            self.table.setUpdatesEnabled(True)
        if len(changed) > cap:
            self.status_label.setText(
                f"{total} total · {len(changed)} changed (showing first {cap}) — "
                f"use Accept/Force all to resolve everything"
            )

    def _selected_entries(self) -> list:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        return [self._rows[r] for r in sorted(rows) if 0 <= r < len(self._rows)]

    def _resolve_entries(self, entries: list, mode: str) -> None:
        """Resolve a batch of entries in the background, reloading once at end."""
        if not entries:
            tooltip("No changed rows selected.")
            return
        client = self._client
        self.status_label.setText(f"Resolving {len(entries)} item(s)…")
        self.btn_accept_all.setEnabled(False)
        self.btn_force_all.setEnabled(False)

        def work():
            from kelma_sync_v2 import anki_apply, anki_local
            done = 0
            for entry in entries:
                if mode == "accept":
                    if entry.resource == "guid":
                        anki_apply.apply_note(mw.col, client.get_note(entry.key))
                    elif entry.resource == "notetype_id":
                        anki_apply.apply_notetype(mw.col, client.get_notetype(int(entry.key)))
                    elif entry.resource == "name":
                        anki_apply.apply_deck(mw.col, client.get_deck(entry.key))
                    elif entry.resource == "card_id":
                        anki_apply.apply_card(mw.col, client.get_card(int(entry.key)))
                else:  # force
                    if entry.resource == "guid":
                        rec = anki_local.note_record(mw.col, entry.key)
                        if rec:
                            client.put_note(entry.key, notetype_id=rec["notetype_id"],
                                fields=rec["fields"], tags=rec["tags"],
                                client_modified_at=rec["client_modified_at"],
                                base_checksum="", force=True)
                    elif entry.resource == "notetype_id":
                        rec = anki_local.notetype_record(mw.col, int(entry.key))
                        if rec:
                            client.put_notetype(int(entry.key), name=rec["name"],
                                definition=rec["definition"],
                                client_modified_at=rec["client_modified_at"],
                                base_checksum="", force=True)
                    elif entry.resource == "name":
                        rec = anki_local.deck_record(mw.col, entry.key)
                        if rec:
                            client.put_deck(entry.key, config=rec["config"],
                                client_modified_at=rec["client_modified_at"],
                                base_checksum="", force=True)
                    elif entry.resource == "card_id":
                        rec = anki_local.card_record(mw.col, int(entry.key))
                        if rec:
                            client.put_card(int(entry.key),
                                note_guid=rec["note_guid"], deck_name=rec["deck_name"],
                                ord=rec["ord"], scheduling=rec["scheduling"],
                                client_modified_at=rec["client_modified_at"])
                done += 1
            return done

        def done_cb(future: Future) -> None:
            self.btn_accept_all.setEnabled(True)
            self.btn_force_all.setEnabled(True)
            try:
                n = future.result()
            except Exception as err:  # noqa: BLE001
                self.status_label.setText(f"Resolve failed: {err}")
                return
            tooltip(f"Resolved {n} item(s).")
            self._load()  # reload once

        mw.taskman.run_in_background(work, done_cb, uses_collection=True)

    def _accept_all(self) -> None:
        sel = self._selected_entries()
        self._resolve_entries(sel if sel else self._changed_entries(), "accept")

    def _force_all(self) -> None:
        sel = self._selected_entries()
        self._resolve_entries(sel if sel else self._changed_entries(), "force")


def _diff_local_preview(entry) -> str:
    l = entry.local
    if l is None:
        return "(missing)"
    return _entry_preview(l)


def _diff_server_preview(entry) -> str:
    s = entry.server
    if s is None:
        return "(missing)"
    return _entry_preview(s)


def _entry_preview(record: dict) -> str:
    """Short preview of a manifest entry's content."""
    if "guid" in record or "checksum" in record and "modified_at" in record and len(record) <= 4:
        # Manifest entry: show timestamp + checksum suffix
        ts = record.get("modified_at", "")
        cs = record.get("checksum", "")[:8]
        return f"{ts} · {cs}" if cs else str(ts)
    return str(record)[:120]


class V2SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("KelmaSync settings")
        cfg = config.get()
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        layout.addLayout(grid)

        self.endpoint = QLineEdit(cfg.get("v2_url", "http://localhost:8081"))
        self.username = QLineEdit(cfg.get("v2_username", ""))
        self.label = QLineEdit(cfg.get("v2_client_label", "Anki plugin"))
        self.last = QLabel(cfg.get("v2_last_server_time", "") or "(none)")
        self.token = QLabel("saved" if cfg.get("v2_token") else "not logged in")

        for row, (name, widget) in enumerate([
            ("Endpoint", self.endpoint),
            ("Username", self.username),
            ("Client label", self.label),
            ("Token", self.token),
            ("Last server time", self.last),
        ]):
            grid.addWidget(QLabel(name), row, 0)
            grid.addWidget(widget, row, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        cfg = config.get()
        cfg["v2_url"] = self.endpoint.text().strip() or "http://localhost:8081"
        cfg["v2_username"] = self.username.text().strip()
        cfg["v2_client_label"] = self.label.text().strip() or "Anki plugin"
        config.save(cfg)
        tooltip("KelmaSync v2 settings saved.")
        self.accept()


class V2CompareDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("KelmaSync compare notes")
        self.resize(900, 520)
        layout = QVBoxLayout(self)
        self.status = QLabel("Loading…")
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["GUID", "Status", "Local", "Server"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load()

    def _load(self) -> None:
        client = _v2_client_or_login()
        if client is None:
            self.status.setText("Not logged in.")
            return

        def work():
            from kelma_sync_v2 import anki_local
            local = {x["guid"]: x for x in anki_local.note_manifest(mw.col)}
            server_manifest = client.manifest()
            server = {x["guid"]: x for x in server_manifest.get("notes", [])}
            rows = []
            for guid in sorted(set(local) | set(server)):
                l = local.get(guid)
                s = server.get(guid)
                if l and s and l.get("checksum") == s.get("checksum"):
                    status = "in-sync"
                elif l and not s:
                    status = "local-only"
                elif s and not l:
                    status = "server-only"
                else:
                    status = "changed"
                rows.append((guid, status, l, s))
            return rows

        def done(future: Future) -> None:
            try:
                rows = future.result()
            except Exception as err:  # noqa: BLE001
                self.status.setText(f"Compare failed: {err}")
                return
            self.table.setRowCount(len(rows))
            changed = 0
            for i, (guid, status, l, s) in enumerate(rows):
                if status != "in-sync":
                    changed += 1
                self.table.setItem(i, 0, QTableWidgetItem(guid))
                self.table.setItem(i, 1, QTableWidgetItem(status))
                self.table.setItem(i, 2, QTableWidgetItem(str((l or {}).get("modified_at", ""))))
                self.table.setItem(i, 3, QTableWidgetItem(str((s or {}).get("modified_at", ""))))
            self.status.setText(f"{len(rows)} notes · {changed} changed")

        mw.taskman.run_in_background(work, done, uses_collection=True)


def _v2_run_ankiweb_sync(progress=None, done=None) -> None:
    """Run native AnkiWeb sync with a completion callback when auth exists.

    If the user is not logged into AnkiWeb, fall back to Anki's original sync
    button behavior so it can show the login UI.
    """
    global _V2_ACTIVE_ACTION
    blocked = _v2_active_message()
    if blocked and _V2_ACTIVE_ACTION != "sync":
        tooltip(f"KelmaSync: {blocked}")
        return
    if progress:
        progress("AnkiWeb: starting native sync…")
    if not getattr(mw, "pm", None) or not mw.pm.sync_auth():
        if _V2_ACTIVE_ACTION in ("sync", "ankiweb"):
            _V2_ACTIVE_ACTION = None
        if progress:
            progress("AnkiWeb: login required; opening native Anki sync/login…")
        if done:
            done(False, "AnkiWeb login required; native sync/login opened.")
        if _orig_sync:
            _orig_sync()
        return

    _V2_ACTIVE_ACTION = "ankiweb"

    def after_sync() -> None:
        global _V2_ACTIVE_ACTION
        if progress:
            progress("AnkiWeb: native sync finished.")
        if _V2_ACTIVE_ACTION == "ankiweb":
            _V2_ACTIVE_ACTION = None
        if done:
            done(True, "AnkiWeb sync finished.")

    try:
        mw._sync_collection_and_media(after_sync)
    except Exception as err:  # noqa: BLE001
        if _V2_ACTIVE_ACTION == "ankiweb":
            _V2_ACTIVE_ACTION = None
        if progress:
            progress(f"AnkiWeb: failed to start native sync: {err}")
        if done:
            done(False, f"AnkiWeb sync failed to start: {err}")


def _v2_forget_login() -> None:
    cfg = config.get()
    cfg["v2_token"] = ""
    cfg["v2_client_id"] = ""
    cfg["v2_last_server_time"] = ""
    config.save(cfg)
    tooltip("KelmaSync v2 login/checkpoint cleared.")


class V2SyncProgressDialog(QDialog):
    _line = pyqtSignal(str)
    _done = pyqtSignal(str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("KelmaSync progress")
        self.resize(720, 420)
        self._started = datetime.now()
        layout = QVBoxLayout(self)
        self.status = QLabel("Starting sync…")
        layout.addWidget(self.status)
        self.log = QTextBrowser()
        layout.addWidget(self.log)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_btn.setEnabled(False)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._line.connect(self._append_line)
        self._done.connect(self._finish)

    def progress(self, text: str) -> None:
        self._line.emit(text)

    def complete(self, text: str, ok: bool = True) -> None:
        self._done.emit(text, ok)

    def _append_line(self, text: str) -> None:
        elapsed = (datetime.now() - self._started).total_seconds()
        line = f"{elapsed:6.1f}s  {text}"
        self.status.setText(text)
        self.log.append(line)
        QApplication.processEvents()

    def _finish(self, text: str, ok: bool) -> None:
        prefix = "✅" if ok else "⚠"
        self._append_line(f"{prefix} {text}")
        self.close_btn.setEnabled(True)


def _v2_preview(record: dict) -> str:
    fields = record.get("fields") or []
    if isinstance(fields, list) and fields:
        return " | ".join(str(x) for x in fields[:2])[:160]
    if "client_modified_at" in record:
        return str(record.get("client_modified_at"))
    return str(record)[:160]


def _v2_sync_menu() -> None:
    """V2 popup shown from the Anki Sync button.

    This restores the old interaction pattern (click Sync → Kelma menu appears)
    while keeping all actions on the v2 path.
    """
    cfg = config.get()
    menu = QMenu(mw)
    menu.setStyleSheet("QMenu::item { padding-left: 18px; padding-right: 18px; padding-top: 5px; padding-bottom: 5px; } QMenu::item:selected { background-color: rgba(255,255,255,0.12); }")
    if branding.logo_enabled():
        menu.setIcon(branding.star_icon())

    container = QWidget()
    box = QVBoxLayout(container)
    box.setContentsMargins(0, 8, 0, 4)
    box.setSpacing(4)
    box.addWidget(_brand_header("KelmaSync"))
    container.setMinimumWidth(360)
    status = "logged in" if cfg.get("v2_token") else "not logged in"
    endpoint = cfg.get("v2_url") or "http://localhost:8081"
    user = cfg.get("v2_username") or "(no username saved)"
    all_decks = [d.name for d in mw.col.decks.all_names_and_ids()]
    kelma_decks = config.decks_for_service(consts.KELMA, all_decks)
    kelma_notes, kelma_cards = _collection_counts_for_decks(kelma_decks)
    scope_line = f"KelmaSync: ✓ {len(kelma_decks)} deck(s), {kelma_notes} notes/{kelma_cards} cards"
    if not config.kelmasync_only():
        ankiweb_decks = config.decks_for_service(consts.ANKIWEB, all_decks)
        aw_notes, aw_cards = _collection_counts_for_decks(ankiweb_decks)
        scope_line += f" · AnkiWeb: ✓ {len(ankiweb_decks)} deck(s), {aw_notes} notes/{aw_cards} cards"
    active = _v2_active_message()
    active_html = f"<br><span style='color:#d98'>⚠ {active}</span>" if active else ""
    status_label = QLabel(
        f"<b>{status}</b> · {user}<br>"
        f"<span style='color:#888'>{endpoint}</span><br>"
        f"{scope_line}"
        f"{active_html}"
    )
    status_wrap = QWidget()
    status_layout = QHBoxLayout(status_wrap)
    status_layout.setContentsMargins(18, 0, 18, 0)
    status_layout.addWidget(status_label)
    box.addWidget(status_wrap)
    wa = QWidgetAction(menu)
    wa.setDefaultWidget(container)
    menu.addAction(wa)
    menu.addSeparator()

    menu.setMinimumWidth(380)
    if config.kelmasync_only():
        act_dual = None
        act_kelma = menu.addAction("Sync KelmaSync")
        act_ankiweb = None
    else:
        act_dual = menu.addAction("Sync KelmaSync + AnkiWeb")
        act_kelma = menu.addAction("Sync KelmaSync only")
        act_ankiweb = menu.addAction("Sync AnkiWeb only")
    act_compare = menu.addAction("Compare everything…")
    if _V2_ACTIVE_ACTION:
        if act_dual is not None:
            act_dual.setEnabled(False)
        act_kelma.setEnabled(False)
        if act_ankiweb is not None:
            act_ankiweb.setEnabled(False)
        act_compare.setEnabled(False)
    menu.addSeparator()
    act_settings = menu.addAction("Settings && deck routing…")
    act_v2_settings = menu.addAction("KelmaSync account/server…")
    act_forget = menu.addAction("Forget login")

    chosen = menu.exec(QCursor.pos())
    if chosen is None:
        return
    if act_dual is not None and chosen is act_dual:
        _v2_test_sync_notes(also_ankiweb=True)
    elif chosen is act_kelma:
        _v2_test_sync_notes(also_ankiweb=False)
    elif act_ankiweb is not None and chosen is act_ankiweb:
        _v2_run_ankiweb_sync()
    elif chosen is act_compare:
        V2FullDiffDialog(mw).exec()
    elif chosen is act_settings:
        SettingsDialog(mw).exec()
    elif chosen is act_v2_settings:
        V2SettingsDialog(mw).exec()
    elif chosen is act_forget:
        _v2_forget_login()


def _v2_test_sync_notes(*, also_ankiweb: bool = False) -> None:
    """Run Kelma v2 sync, optionally followed by native AnkiWeb sync."""
    global _V2_ACTIVE_ACTION
    blocked = _v2_active_message()
    if blocked:
        tooltip(f"KelmaSync: {blocked}")
        return
    deck_names = _v2_kelma_deck_names()
    if not deck_names:
        tooltip("KelmaSync: no decks are picked for KelmaSync. Open Settings → deck routing.")
        return
    client = _v2_client_or_login()
    if client is None:
        return
    try:
        from kelma_sync_v2.content_sync import ContentSyncConflict, sync_content_once
    except Exception as err:  # noqa: BLE001
        tooltip(f"KelmaSync v2 package import failed: {err}")
        return

    cfg = config.get()
    since = cfg.get("v2_last_server_time") or None
    _V2_ACTIVE_ACTION = "sync"
    dlg = V2SyncProgressDialog(mw)
    dlg.show()
    if also_ankiweb:
        dlg.progress(f"Dual sync queued: {len(deck_names)} KelmaSync deck(s) first, then AnkiWeb.")
        tooltip("KelmaSync + AnkiWeb: syncing… progress window opened.")
    else:
        dlg.progress(f"KelmaSync queued for {len(deck_names)} picked deck(s). Waiting for Anki collection worker…")
        tooltip("KelmaSync: syncing… progress window opened.")

    def _work():
        dlg.progress("Worker started.")
        return sync_content_once(mw.col, client, since=since, deck_names=deck_names, progress=dlg.progress)

    def _done(future: Future) -> None:
        global _V2_ACTIVE_ACTION
        try:
            result = future.result()
        except ContentSyncConflict as conflict:
            msg = f"{len(conflict.conflicts)} {conflict.resource} conflict(s). No checkpoint saved. Opening resolver…"
            if _V2_ACTIVE_ACTION == "sync":
                _V2_ACTIVE_ACTION = None
            dlg.complete(msg, ok=False)
            tooltip(f"KelmaSync: {msg}")
            # Open the full checksum diff resolver for notes/cards/decks/notetypes.
            # It lets the user explicitly Accept server or Force local.
            V2FullDiffDialog(mw).exec()
            return
        except Exception as err:  # noqa: BLE001
            if _V2_ACTIVE_ACTION == "sync":
                _V2_ACTIVE_ACTION = None
            dlg.complete(f"KelmaSync failed: {err}", ok=False)
            tooltip(f"KelmaSync v2 sync failed: {err}")
            return
        cfg2 = config.get()
        cfg2["v2_last_server_time"] = result.server_time
        config.save(cfg2)
        msg = (
            f"tombstones {result.tombstones.applied}, "
            f"decks {result.decks.pushed}/{result.decks.pulled}, "
            f"notetypes {result.notetypes.pushed}/{result.notetypes.pulled}, "
            f"notes {result.notes.pushed}/{result.notes.pulled}, "
            f"cards {result.cards.pushed}/{result.cards.pulled}, "
            f"media {result.media.uploaded}/{result.media.downloaded}."
        )
        if not also_ankiweb:
            if _V2_ACTIVE_ACTION == "sync":
                _V2_ACTIVE_ACTION = None
            dlg.complete(msg, ok=True)
            tooltip(f"KelmaSync complete: {msg}")
            return

        dlg.progress(f"KelmaSync complete: {msg}")
        dlg.progress("Starting AnkiWeb sync…")

        def ankiweb_done(ok: bool, text: str) -> None:
            if ok:
                dlg.complete(f"Dual sync complete. KelmaSync: {msg} AnkiWeb: {text}", ok=True)
                tooltip("KelmaSync + AnkiWeb complete.")
            else:
                dlg.complete(f"KelmaSync complete, but {text}", ok=False)
                tooltip(f"KelmaSync complete; AnkiWeb issue: {text}")

        _v2_run_ankiweb_sync(progress=dlg.progress, done=ankiweb_done)

    try:
        mw.taskman.run_in_background(_work, _done, uses_collection=True)
    except Exception as err:  # noqa: BLE001
        if _V2_ACTIVE_ACTION == "sync":
            _V2_ACTIVE_ACTION = None
        dlg.complete(f"Could not start sync: {err}", ok=False)
        tooltip(f"KelmaSync: could not start sync: {err}")


# -----------------------------------------------------------------------------
# Menu
# -----------------------------------------------------------------------------
def _build_menu() -> None:
    """Build the v2-only Kelma menu.

    The old v1 dual-sync/inspect UI is intentionally hidden here so this plugin
    surface is unambiguously testing the new v2 REST protocol.
    """
    menu = QMenu("&Kelma", mw)
    menu.setStyleSheet("QMenu::item { padding-left: 18px; padding-right: 18px; padding-top: 5px; padding-bottom: 5px; } QMenu::item:selected { background-color: rgba(255,255,255,0.12); }")
    if branding.logo_enabled():
        menu.setIcon(branding.star_icon())
    mw.form.menuTools.addMenu(menu)

    if config.kelmasync_only():
        act_sync = QAction("Sync KelmaSync", mw)
        act_sync.triggered.connect(lambda: _v2_test_sync_notes(also_ankiweb=False))
        menu.addAction(act_sync)
    else:
        act_sync = QAction("Sync KelmaSync + AnkiWeb", mw)
        act_sync.triggered.connect(lambda: _v2_test_sync_notes(also_ankiweb=True))
        menu.addAction(act_sync)

        act_kelma = QAction("Sync KelmaSync only", mw)
        act_kelma.triggered.connect(lambda: _v2_test_sync_notes(also_ankiweb=False))
        menu.addAction(act_kelma)

        act_ankiweb = QAction("Sync AnkiWeb only", mw)
        act_ankiweb.triggered.connect(lambda: _v2_run_ankiweb_sync())
        menu.addAction(act_ankiweb)

    act_compare = QAction("Compare everything…", mw)
    act_compare.triggered.connect(lambda: V2FullDiffDialog(mw).exec())
    menu.addAction(act_compare)

    menu.addSeparator()

    act_settings = QAction("Settings && deck routing…", mw)
    act_settings.triggered.connect(lambda: SettingsDialog(mw).exec())
    menu.addAction(act_settings)

    act_v2_settings = QAction("KelmaSync account/server…", mw)
    act_v2_settings.triggered.connect(lambda: V2SettingsDialog(mw).exec())
    menu.addAction(act_v2_settings)

    act_logout = QAction("Forget login", mw)
    act_logout.triggered.connect(_v2_forget_login)
    menu.addAction(act_logout)

    menu.addSeparator()
    _build_display_menu(menu)


def _open_storage() -> None:
    from .storageview import StorageDialog

    StorageDialog(mw).exec()


def _open_diagnostics() -> None:
    from .diagnostics import DiagnosticsDialog

    DiagnosticsDialog(mw).exec()


def _build_display_menu(parent: QMenu) -> None:
    """A checkable submenu, auto-populated from the features registry, for
    toggling visual modifications (deck badges, and future ones)."""
    sub = QMenu("Display modifications", mw)
    parent.addMenu(sub)
    for feat in features.FEATURES:
        act = QAction(feat.label, mw)
        act.setCheckable(True)
        act.setChecked(features.enabled(feat.key))
        act.setToolTip(feat.description)
        act.toggled.connect(
            lambda checked, k=feat.key: _toggle_feature(k, checked)
        )
        sub.addAction(act)


def _toggle_feature(key: str, checked: bool) -> None:
    features.set_enabled(key, checked)
    # Re-render the main screen so the change shows immediately.
    try:
        mw.reset()
    except Exception:  # noqa: BLE001
        pass


def setup() -> None:
    """Entry point, called once after the profile/collection is ready."""
    _build_menu()
    _install_sync_hook()
    # Do not install the old native-sync guard: v2 dual sync intentionally runs
    # KelmaSync first, then AnkiWeb's native sync.
