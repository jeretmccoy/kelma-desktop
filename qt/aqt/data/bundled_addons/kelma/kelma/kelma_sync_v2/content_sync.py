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


def _chunks(xs: list, n: int = 1000):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def _scope_server_manifest_to_decks(client: V2Client, manifest: dict[str, Any], deck_names: list[str], progress=None) -> dict[str, Any]:
    """Filter server manifest to the deck picker scope.

    Server note manifest entries don't include deck membership, so derive scope
    from full server cards (card -> deck_name + note_guid), then keep only notes
    referenced by scoped cards.
    """
    allowed = set(deck_names)

    def _in_scope(deck: str) -> bool:
        # Match the deck itself OR any of its subdecks ("Parent::Child"),
        # consistent with local deck scoping which is prefix-based.
        if deck in allowed:
            return True
        return any(deck.startswith(name + "::") for name in allowed)

    card_ids = [int(c["card_id"]) for c in manifest.get("cards", []) if c.get("card_id")]
    scoped_card_ids: set[int] = set()
    scoped_note_guids: set[str] = set()
    if card_ids:
        if progress:
            progress(f"Server scope: checking {len(card_ids)} card deck assignments…")
        done = 0
        for chunk in _chunks(card_ids):
            pulled = client.batch_pull(cards=chunk).get("cards", [])
            for c in pulled:
                deck = str(c.get("deck_name", ""))
                if _in_scope(deck):
                    scoped_card_ids.add(int(c.get("card_id")))
                    guid = c.get("note_guid")
                    if guid:
                        scoped_note_guids.add(str(guid))
            done += len(chunk)
            if progress:
                progress(f"Server scope: {done}/{len(card_ids)} cards checked · {len(scoped_card_ids)} in Kelma decks")
    scoped = dict(manifest)
    scoped["cards"] = [c for c in manifest.get("cards", []) if int(c.get("card_id", 0)) in scoped_card_ids]
    scoped["notes"] = [n for n in manifest.get("notes", []) if str(n.get("guid", "")) in scoped_note_guids]
    scoped["decks"] = [d for d in manifest.get("decks", []) if _in_scope(str(d.get("name", "")))]
    # Keep notetypes broad enough for pulled scoped notes; local notetypes are
    # independently restricted to selected local notes.
    return scoped


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
    deck_names: list[str] | None = None,
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
    if deck_name and not deck_names:
        deck_names = [deck_name]
    if deck_names is not None:
        if progress:
            progress(f"Scoping server manifest to {len(deck_names)} Kelma deck(s)…")
        manifest = _scope_server_manifest_to_decks(client, manifest, deck_names, progress=progress)
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
    # Repair local duplicate generated cards before building manifests. This
    # prevents invalid duplicate cards/blank-GUID duplicate notes from being
    # counted or pushed forever.
    anki_local.repair_duplicate_cards(col, deck_names=deck_names, progress=progress)
    if progress:
        progress("Phase 4/9: building local key snapshot…")
    local_note_manifest = anki_local.note_manifest(col, deck_names=deck_names, progress=progress)
    if progress:
        progress(f"Snapshot: {len(local_note_manifest)} local notes")
    local_card_manifest = anki_local.card_manifest(col, deck_names=deck_names)
    if progress:
        progress(f"Snapshot: {len(local_card_manifest)} local cards")
    used_notetype_ids = {int(n["notetype_id"]) for n in local_note_manifest}
    local_notetype_manifest = anki_local.notetype_manifest(col, notetype_ids=used_notetype_ids if deck_names is not None else None)
    if progress:
        progress(f"Snapshot: {len(local_notetype_manifest)} local notetypes")
    local_deck_manifest = anki_local.deck_manifest(col, deck_names=deck_names)
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
        result.decks = sync_decks_once(col, client, manifest, progress=progress, deck_names=deck_names)
    except DeckSyncConflict as e:
        raise ContentSyncConflict("deck", e.conflicts) from e
    try:
        if progress:
            progress("Phase 7/9: syncing notetypes…")
        result.notetypes = sync_notetypes_once(col, client, manifest, apply_pulls=True, progress=progress, notetype_ids=used_notetype_ids if deck_names is not None else None)
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
            deck_names=deck_names,
            server_manifest=manifest,
            progress=progress,
        )
    except NoteSyncConflict as e:
        raise ContentSyncConflict("note", e.conflicts) from e
    if progress:
        progress("Phase 9/9: syncing cards…")
    try:
        result.cards = sync_cards_once(col, client, manifest, progress=progress, deck_names=deck_names)
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
