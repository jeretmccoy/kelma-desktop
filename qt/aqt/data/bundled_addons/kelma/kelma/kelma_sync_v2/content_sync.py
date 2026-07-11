"""Content sync orchestration: notetypes first, then notes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from anki.collection import Collection

from .client import V2Client
from .card_sync import CardSyncConflict, CardSyncResult, sync_cards_once
from .deck_sync import DeckSyncConflict, DeckSyncResult, sync_decks_once
from .media_sync import MediaSyncResult, sync_media_once
from .notetype_sync import NotetypeSyncConflict, NotetypeSyncResult, sync_notetypes_once
from .note_sync import NoteSyncConflict, NoteSyncResult, sync_notes_once
from .tombstone_sync import TombstoneSyncResult, apply_tombstones
from . import anki_local, sync_state


@dataclass
class ContentSyncResult:
    tombstones: TombstoneSyncResult
    local_deletes: dict[str, list[str]] = field(default_factory=dict)
    decks: DeckSyncResult = None  # type: ignore[assignment]
    notetypes: NotetypeSyncResult = None  # type: ignore[assignment]
    notes: NoteSyncResult = None  # type: ignore[assignment]
    cards: CardSyncResult = None  # type: ignore[assignment]
    media: MediaSyncResult = None  # type: ignore[assignment]
    server_time: str = ""


class ContentSyncConflict(RuntimeError):
    def __init__(self, resource: str, conflicts: list[dict]) -> None:
        super().__init__(f"{len(conflicts)} {resource} conflict(s)")
        self.resource = resource
        self.conflicts = conflicts


def _push_local_deletes(col: Collection, client: V2Client, deletes: dict[str, list[str]], progress=None) -> None:
    """Push DELETE for resources that were removed locally since last sync."""
    total = sum(len(v) for v in deletes.values())
    done = 0
    if progress:
        progress(f"Deletes: pushing {total} local tombstone(s)…")
    for guid in deletes.get("notes", []):
        client.delete_note(guid); done += 1
        if progress:
            progress(f"Deletes {done}/{total}: note {guid}")
    for cid in deletes.get("cards", []):
        client.delete_card(int(cid)); done += 1
        if progress:
            progress(f"Deletes {done}/{total}: card {cid}")
    for name in deletes.get("decks", []):
        client.delete_deck(name); done += 1
        if progress:
            progress(f"Deletes {done}/{total}: deck {name}")
    for ntid in deletes.get("notetypes", []):
        try:
            client.delete_notetype(int(ntid)); done += 1
            if progress:
                progress(f"Deletes {done}/{total}: notetype {ntid}")
        except Exception:
            pass


def sync_content_once(
    col: Collection,
    client: V2Client,
    *,
    since: str | None = None,
    deck_name: str | None = None,
    apply_note_pulls: bool = True,
    progress=None,
) -> ContentSyncResult:
    """Run one content sync pass.

    Order:
      1. apply server tombstones locally
      2. detect local deletes (compare to last snapshot) and push DELETEs
      3. sync decks → notetypes → notes → cards → media
      4. save new snapshot
    """
    if progress:
        progress("Phase 1/9: fetching full server manifest for checksum comparison…")
    # IMPORTANT: checksum planning requires a full server manifest. If we pass
    # `since`, unchanged server rows are omitted and look local-only, causing
    # needless re-sends even when checksums match. Incremental sync can only be
    # reintroduced after the local snapshot stores checksums per resource.
    manifest = client.manifest()
    if progress:
        progress(
            f"Server manifest: {len(manifest.get('notes', []))} notes, "
            f"{len(manifest.get('cards', []))} cards, {len(manifest.get('notetypes', []))} notetypes, "
            f"{len(manifest.get('decks', []))} decks, {len(manifest.get('media', []))} media"
        )
        progress("Phase 2/9: applying server tombstones…")
    tombstones = apply_tombstones(col, manifest)
    if progress:
        progress(f"Tombstones complete: applied {tombstones.applied}")

    # Detect locally deleted resources by comparing to the last snapshot.
    if progress:
        progress("Phase 3/9: loading previous sync snapshot…")
    snapshot = sync_state.load_state(col)
    if progress:
        progress("Phase 4/9: building local key snapshot…")
    local_note_manifest = anki_local.note_manifest(col, progress=progress)
    if progress:
        progress(f"Snapshot: {len(local_note_manifest)} local notes")
    local_card_manifest = anki_local.card_manifest(col)
    if progress:
        progress(f"Snapshot: {len(local_card_manifest)} local cards")
    local_notetype_manifest = anki_local.notetype_manifest(col)
    if progress:
        progress(f"Snapshot: {len(local_notetype_manifest)} local notetypes")
    local_deck_manifest = anki_local.deck_manifest(col)
    if progress:
        progress(f"Snapshot: {len(local_deck_manifest)} local decks")
    local_keys = {
        "notes": {x["guid"] for x in local_note_manifest},
        "cards": {str(x["card_id"]) for x in local_card_manifest},
        "notetypes": {str(x["notetype_id"]) for x in local_notetype_manifest},
        "decks": {x["name"] for x in local_deck_manifest},
    }
    if progress:
        progress("Phase 5/9: detecting local deletes…")
    local_deletes = sync_state.compute_local_deletes(snapshot, local_keys)
    if local_deletes:
        _push_local_deletes(col, client, local_deletes, progress=progress)
        if progress:
            progress("Deletes changed server state; refreshing manifest…")
        manifest = client.manifest()
    elif progress:
        progress("Deletes: none")

    result = ContentSyncResult(tombstones=tombstones, local_deletes=local_deletes)

    try:
        if progress:
            progress("Phase 6/9: syncing decks…")
        result.decks = sync_decks_once(col, client, manifest, progress=progress)
    except DeckSyncConflict as e:
        raise ContentSyncConflict("deck", e.conflicts) from e
    try:
        if progress:
            progress("Phase 7/9: syncing notetypes…")
        result.notetypes = sync_notetypes_once(col, client, manifest, apply_pulls=True, progress=progress)
    except NotetypeSyncConflict as e:
        raise ContentSyncConflict("notetype", e.conflicts) from e
    try:
        if progress:
            progress("Phase 8/9: syncing notes…")
        result.notes = sync_notes_once(
            col,
            client,
            since=since,
            apply_pulls=apply_note_pulls,
            deck_name=deck_name,
            server_manifest=manifest,
            progress=progress,
        )
    except NoteSyncConflict as e:
        raise ContentSyncConflict("note", e.conflicts) from e
    if progress:
        progress("Phase 9/9: syncing cards…")
    try:
        result.cards = sync_cards_once(col, client, manifest, progress=progress)
    except CardSyncConflict as e:
        raise ContentSyncConflict("card", e.conflicts) from e
    if progress:
        progress("Final phase: syncing media…")
    result.media = sync_media_once(col, client, manifest, progress=progress)
    result.server_time = result.notes.server_time or manifest.get("server_time", "")

    # Save the new snapshot so next sync can detect deletes.
    if progress:
        progress("Saving sync snapshot…")
    new_state = sync_state.build_state(
        notes=sorted(local_keys["notes"]),
        cards=sorted(local_keys["cards"]),
        notetypes=sorted(local_keys["notetypes"]),
        decks=sorted(local_keys["decks"]),
    )
    sync_state.save_state(col, new_state)

    if progress:
        progress("Sync complete.")
    return result
