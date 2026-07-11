"""KelmaSync v2 local collection helpers.

This module converts an Anki collection into the resource shapes expected by the
v2 REST API. It does not decide conflict policy; it only builds local records
and lightweight manifests.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from anki.collection import Collection

from .checksum_rs import note_checksum, note_checksums_batch, notetype_checksum, deck_checksum, card_checksum


def iso_from_anki_mod(mod_seconds: int) -> str:
    """Convert Anki's integer seconds timestamp to RFC3339/ISO string."""
    return datetime.fromtimestamp(int(mod_seconds or 0), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def checksum(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def note_record(col: Collection, guid: str) -> dict[str, Any] | None:
    row = col.db.first("SELECT id, guid, mid, mod, flds, tags FROM notes WHERE guid = ?", guid)
    if not row:
        return None
    _nid, guid, mid, mod, flds, tags = row
    fields = str(flds or "").split("\x1f")
    tag_list = [t for t in str(tags or "").split() if t]
    return {
        "guid": guid,
        "notetype_id": int(mid),
        "fields": fields,
        "tags": tag_list,
        "checksum": note_checksum(fields, tag_list),
        "client_modified_at": iso_from_anki_mod(int(mod or 0)),
    }


def note_manifest(col: Collection, deck_name: str | None = None, progress=None) -> list[dict[str, Any]]:
    """Return note manifest, optionally restricted to notes with cards in deck_name."""
    if progress:
        progress("Reading local notes…")
    out: list[dict[str, Any]] = []
    if deck_name:
        dids = _deck_ids_for_name(col, deck_name)
        if not dids:
            return []
        marks = ",".join("?" for _ in dids)
        rows = col.db.all(
            f"""
            SELECT DISTINCT n.guid, n.mid, n.mod, n.flds, n.tags
            FROM notes n JOIN cards c ON c.nid = n.id
            WHERE c.did IN ({marks})
            """,
            *dids,
        )
    else:
        rows = col.db.all("SELECT guid, mid, mod, flds, tags FROM notes")
    parsed = []
    for guid, mid, mod, flds, tags in rows:
        if not guid:
            # Empty GUIDs are ambiguous and cannot be v2 identities. The UI
            # should offer a generate-GUID action before v2 sync.
            continue
        fields = str(flds or "").split("\x1f")
        tag_list = [t for t in str(tags or "").split() if t]
        parsed.append((guid, mid, mod, fields, tag_list))
    if progress:
        progress(f"Checksumming {len(parsed)} notes…")
    checksums = note_checksums_batch([(p[3], p[4]) for p in parsed])
    for (guid, mid, mod, fields, tag_list), cs in zip(parsed, checksums):
        out.append({
            "guid": guid,
            "checksum": cs,
            "modified_at": iso_from_anki_mod(int(mod or 0)),
            "notetype_id": int(mid),
        })
    return out


def card_record(col: Collection, card_id: int) -> dict[str, Any] | None:
    row = col.db.first(
        """
        SELECT c.id, c.nid, c.did, c.ord, c.mod, c.type, c.queue, c.due,
               c.ivl, c.factor, c.reps, c.lapses, c.left, c.odue, c.odid,
               c.flags, c.data, n.guid
        FROM cards c JOIN notes n ON n.id = c.nid
        WHERE c.id = ?
        """,
        card_id,
    )
    if not row:
        return None
    (
        cid, _nid, did, ord_, mod, typ, queue, due, ivl, factor, reps, lapses,
        left, odue, odid, flags, data, guid,
    ) = row
    deck = col.decks.get(int(did))
    deck_name = deck.get("name", str(did)) if deck else str(did)
    scheduling = {
        "type": int(typ or 0),
        "queue": int(queue or 0),
        "due": int(due or 0),
        "ivl": int(ivl or 0),
        "factor": int(factor or 0),
        "reps": int(reps or 0),
        "lapses": int(lapses or 0),
        "left": int(left or 0),
        "odue": int(odue or 0),
        "odid": int(odid or 0),
        "flags": int(flags or 0),
        "data": data or "",
    }
    return {
        "card_id": int(cid),
        "note_guid": guid or "",
        "deck_name": deck_name,
        "ord": int(ord_ or 0),
        "scheduling": scheduling,
        "checksum": card_checksum(guid or "", deck_name, int(ord_ or 0), scheduling),
        "client_modified_at": iso_from_anki_mod(int(mod or 0)),
    }


def card_manifest(col: Collection) -> list[dict[str, Any]]:
    out = []
    rows = col.db.all(
        """
        SELECT c.id, c.did, c.ord, c.mod, c.type, c.queue, c.due,
               c.ivl, c.factor, c.reps, c.lapses, c.left, c.odue, c.odid,
               c.flags, c.data, n.guid
        FROM cards c JOIN notes n ON n.id = c.nid
        """
    )
    deck_names: dict[int, str] = {}
    for cid, did, ord_, mod, typ, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data, guid in rows:
        did_int = int(did)
        if did_int not in deck_names:
            deck = col.decks.get(did_int)
            deck_names[did_int] = deck.get("name", str(did_int)) if deck else str(did_int)
        deck_name = deck_names[did_int]
        scheduling = {
            "type": int(typ or 0), "queue": int(queue or 0), "due": int(due or 0),
            "ivl": int(ivl or 0), "factor": int(factor or 0), "reps": int(reps or 0),
            "lapses": int(lapses or 0), "left": int(left or 0), "odue": int(odue or 0),
            "odid": int(odid or 0), "flags": int(flags or 0), "data": data or "",
        }
        out.append({
            "card_id": int(cid),
            "checksum": card_checksum(guid or "", deck_name, int(ord_ or 0), scheduling),
            "modified_at": iso_from_anki_mod(int(mod or 0)),
        })
    return out


def deck_record(col: Collection, name: str) -> dict[str, Any] | None:
    deck = next((d for d in col.decks.all() if d.get("name") == name), None)
    if not deck:
        return None
    # Store the deck config as Anki exposes it. This includes local ids, but v2
    # uses the deck name as the identity and treats config as opaque JSON.
    cfg = dict(deck)
    cfg.pop("name", None)
    return {
        "name": name,
        "config": cfg,
        "checksum": deck_checksum(cfg),
        "client_modified_at": iso_from_anki_mod(int(deck.get("mod", 0) or 0)),
    }


def deck_manifest(col: Collection) -> list[dict[str, Any]]:
    out = []
    for deck in col.decks.all():
        name = deck.get("name", "")
        cfg = dict(deck)
        cfg.pop("name", None)
        out.append({
            "name": name,
            "checksum": deck_checksum(cfg),
            "modified_at": iso_from_anki_mod(int(deck.get("mod", 0) or 0)),
        })
    return out


def notetype_record(col: Collection, notetype_id: int) -> dict[str, Any] | None:
    nt = col.models.get(notetype_id)
    if not nt:
        return None
    definition = dict(nt)
    name = definition.get("name", str(notetype_id))
    return {
        "notetype_id": int(notetype_id),
        "name": name,
        "definition": definition,
        "checksum": notetype_checksum(name, definition),
        "client_modified_at": iso_from_anki_mod(int(definition.get("mod", 0) or 0)),
    }


def notetype_manifest(col: Collection) -> list[dict[str, Any]]:
    out = []
    for nt in col.models.all():
        ntid = int(nt.get("id", 0))
        name = nt.get("name", str(ntid))
        definition = dict(nt)
        out.append({
            "notetype_id": ntid,
            "checksum": notetype_checksum(name, definition),
            "modified_at": iso_from_anki_mod(int(nt.get("mod", 0) or 0)),
        })
    return out


def local_manifest(col: Collection, deck_name: str | None = None, progress=None) -> dict[str, Any]:
    notes = note_manifest(col, deck_name=deck_name, progress=progress)
    if progress:
        progress("Reading local cards…")
    cards = card_manifest(col)
    if progress:
        progress(f"Read {len(cards)} cards · reading notetypes…")
    notetypes = notetype_manifest(col)
    if progress:
        progress(f"Read {len(notetypes)} notetypes · reading decks…")
    decks = deck_manifest(col)
    if progress:
        progress(f"Read {len(decks)} decks")
    return {
        "notes": notes,
        "cards": cards,
        "notetypes": notetypes,
        "decks": decks,
    }


def _deck_ids_for_name(col: Collection, name: str) -> list[int]:
    ids: list[int] = []
    for deck in col.decks.all():
        if deck.get("name") == name or str(deck.get("name", "")).startswith(name + "::"):
            try:
                ids.append(int(deck.get("id")))
            except Exception:
                pass
    return ids
