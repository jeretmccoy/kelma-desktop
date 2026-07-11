"""Explicitly publish the selected local client state to KelmaSync."""
from __future__ import annotations

from typing import Any

from anki.collection import Collection

from . import anki_local
from .client import V2Client
from .media_sync import sync_media_once

_BATCH = 500


def push_client_state(
    col: Collection,
    client: V2Client,
    *,
    deck_names: list[str],
    progress=None,
) -> dict[str, int]:
    """Force the scoped local collection state to KelmaSync.

    This operation never pulls. It is intentionally separate from source
    selection so the user decides the local canonical state before publishing.
    """
    manifest = anki_local.local_manifest(col, deck_names=deck_names, progress=progress)
    totals = {"notetypes": 0, "decks": 0, "notes": 0, "cards": 0, "media": 0, "deleted": 0}

    # Remove scoped server resources absent from the chosen client state. This
    # makes "Use Anki / AnkiWeb" canonical for server-only items as well as
    # changed items, instead of merely upserting what exists locally.
    from .content_sync import _scope_server_manifest_to_decks
    server = _scope_server_manifest_to_decks(client, client.manifest(), deck_names, progress=progress)
    local_cards = {str(x.get("logical_key")) for x in manifest["cards"]}
    local_notes = {str(x.get("guid")) for x in manifest["notes"]}
    for card in server.get("cards", []):
        key = str(card.get("logical_key") or f"{card.get('note_guid', '')}:{int(card.get('ord', 0) or 0)}")
        if key not in local_cards and card.get("card_id"):
            client.delete_card(int(card["card_id"]))
            totals["deleted"] += 1
    for note in server.get("notes", []):
        guid = str(note.get("guid") or "")
        if guid and guid not in local_notes:
            client.delete_note(guid)
            totals["deleted"] += 1

    resources: list[tuple[str, list[dict[str, Any]], Any]] = [
        ("notetypes", manifest["notetypes"], lambda item: anki_local.notetype_record(col, int(item["notetype_id"]))),
        ("decks", manifest["decks"], lambda item: anki_local.deck_record(col, str(item["name"]))),
        ("notes", manifest["notes"], lambda item: anki_local.note_record(col, str(item["guid"]))),
        ("cards", manifest["cards"], lambda item: anki_local.card_record(col, int(item["card_id"]))),
    ]
    for kind, entries, record_for in resources:
        if progress:
            progress(f"Publishing {len(entries)} {kind} to KelmaSync…")
        for start in range(0, len(entries), _BATCH):
            records = []
            for item in entries[start:start + _BATCH]:
                record = record_for(item)
                if not record:
                    continue
                if kind == "notes":
                    record = {k: record[k] for k in ("guid", "notetype_id", "fields", "tags", "client_modified_at")}
                    record["base_checksum"] = ""
                elif kind == "cards":
                    record = {k: record[k] for k in ("card_id", "note_guid", "deck_name", "ord", "scheduling", "client_modified_at")}
                elif kind == "notetypes":
                    record = {k: record[k] for k in ("notetype_id", "name", "definition", "client_modified_at")}
                    record["base_checksum"] = ""
                elif kind == "decks":
                    record = {k: record[k] for k in ("name", "config", "client_modified_at")}
                    record["base_checksum"] = ""
                records.append(record)
            payload = {"notes": [], "cards": [], "notetypes": [], "decks": []}
            payload[kind] = records
            response = client.batch_push(payload, force=True)
            totals[kind] += int((response.get("accepted") or {}).get(kind, 0))
            if progress:
                progress(f"{kind}: {min(start + _BATCH, len(entries))}/{len(entries)} published")

    server_manifest = client.manifest()
    media = sync_media_once(
        col,
        client,
        server_manifest,
        progress=progress,
        deck_names=deck_names,
    )
    totals["media"] = media.uploaded
    return totals


def mark_client_state_for_ankiweb(col: Collection, deck_names: list[str]) -> tuple[int, int]:
    """Mark scoped notes/cards pending so native AnkiWeb publishes local state."""
    dids = anki_local._deck_ids_for_names(col, deck_names)
    if not dids:
        return 0, 0
    marks = ",".join("?" for _ in dids)
    valid_note = "nid IN (SELECT id FROM notes WHERE guid != '')"
    note_count = int(col.db.scalar(
        f"SELECT count(DISTINCT nid) FROM cards WHERE did IN ({marks}) AND {valid_note}", *dids
    ) or 0)
    card_count = int(col.db.scalar(
        f"SELECT count(*) FROM cards WHERE did IN ({marks}) AND {valid_note}", *dids
    ) or 0)
    col.db.execute(
        f"UPDATE cards SET usn=-1 WHERE did IN ({marks}) AND {valid_note}", *dids
    )
    col.db.execute(
        f"UPDATE notes SET usn=-1 WHERE guid != '' AND id IN "
        f"(SELECT DISTINCT nid FROM cards WHERE did IN ({marks}))",
        *dids,
    )
    return note_count, card_count
