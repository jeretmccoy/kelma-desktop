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


class DeletionSafetyError(RuntimeError):
    def __init__(self, deletes: dict[str, list[str]]) -> None:
        self.deletes = deletes
        total = sum(len(values) for values in deletes.values())
        breakdown = ", ".join(f"{len(values)} {kind}" for kind, values in deletes.items())
        super().__init__(
            f"Refusing to delete {total} KelmaSync resources ({breakdown}) without explicit approval"
        )


def _chunks(xs: list, n: int = 3000):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def _scope_server_manifest_to_decks(client: V2Client, manifest: dict[str, Any], deck_names: list[str] | None, progress=None) -> dict[str, Any]:
    """Filter server manifest to the deck picker scope.

    Server note manifest entries don't include deck membership, so derive scope
    from full server cards (card -> deck_name + note_guid), then keep only notes
    referenced by scoped cards. ``deck_names=None`` means unscoped (all decks).
    """
    if deck_names is None:
        # Unscoped: build logical keys from manifest data without pulling cards.
        for m in manifest.get("cards", []):
            if "logical_key" not in m:
                guid = str(m.get("note_guid", ""))
                ord_ = int(m.get("ord", 0) or 0)
                m["logical_key"] = f"{guid}:{ord_}"
        return manifest

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
            by_id = {int(c.get("card_id")): c for c in pulled if c.get("card_id")}
            for c in pulled:
                deck = str(c.get("deck_name", ""))
                if _in_scope(deck):
                    scoped_card_ids.add(int(c.get("card_id")))
                    guid = c.get("note_guid")
                    if guid:
                        scoped_note_guids.add(str(guid))
            # Enrich manifest entries with stable logical card identity for
            # cross-collection comparison. card_id is a local creation timestamp
            # and differs across clients; (note_guid, ord) is the real card key.
            for m in manifest.get("cards", []):
                cid = int(m.get("card_id", 0) or 0)
                c = by_id.get(cid)
                if c:
                    m["note_guid"] = c.get("note_guid") or ""
                    m["ord"] = int(c.get("ord") or 0)
                    m["deck_name"] = c.get("deck_name") or ""
                    m["scheduling"] = dict(c.get("scheduling") or {})
                    m["logical_key"] = f"{m['note_guid']}:{m['ord']}"
            done += len(chunk)
            if progress:
                progress(f"Server scope: {done}/{len(card_ids)} cards checked · {len(scoped_card_ids)} in Kelma decks")
    scoped = dict(manifest)
    scoped["cards"] = [c for c in manifest.get("cards", []) if int(c.get("card_id", 0)) in scoped_card_ids]
    scoped["notes"] = [n for n in manifest.get("notes", []) if str(n.get("guid", "")) in scoped_note_guids]
    scoped["decks"] = [d for d in manifest.get("decks", []) if _in_scope(str(d.get("name", "")))]
    # Server note manifests don't carry notetype_id, so notetype scoping is
    # handled by the caller (which has local notetype IDs from scoped notes).
    return scoped


def _tombstones_for_snapshot(manifest: dict[str, Any], snapshot: dict[str, Any]) -> list[dict]:
    """Only apply tombstones for resources this exact scoped client knew."""
    known = {
        "note": set(snapshot.get("notes", [])),
        "card": set(snapshot.get("cards", [])),
        "notetype": set(snapshot.get("notetypes", [])),
        "deck": set(snapshot.get("decks", [])),
    }
    return [
        tombstone
        for tombstone in manifest.get("tombstones", [])
        if tombstone.get("type") in known
        and str(tombstone.get("resource_id", "")) in known[tombstone["type"]]
    ]


def _push_local_deletes(col: Collection, client: V2Client, deletes: dict[str, list[str]], progress=None) -> None:
    """Push scoped local deletions in transactional 3,000-item batches."""
    total = sum(len(v) for v in deletes.values())
    done = 0
    if progress:
        progress(f"Deletes: pushing {total} approved local tombstone(s) in batches…")
    resources = (
        ("notes", deletes.get("notes", [])),
        ("cards", [int(value) for value in deletes.get("cards", [])]),
        ("notetypes", [int(value) for value in deletes.get("notetypes", [])]),
        ("decks", deletes.get("decks", [])),
    )
    for resource, values in resources:
        for chunk in _chunks(list(values), 3000):
            payload = {"notes": [], "cards": [], "notetypes": [], "decks": []}
            payload[resource] = chunk
            client.batch_delete(**payload)
            done += len(chunk)
            if progress:
                progress(f"Deletes: {done}/{total} complete ({resource} batch of {len(chunk)})")


def _limit_deletes_to_scoped_server(
    deletes: dict[str, list[str]], manifest: dict[str, Any]
) -> dict[str, list[str]]:
    """Never delete an identifier outside the currently scoped server view."""
    server_keys = {
        "notes": {str(item.get("guid", "")) for item in manifest.get("notes", [])},
        "cards": {str(item.get("card_id", "")) for item in manifest.get("cards", [])},
        "notetypes": {str(item.get("notetype_id", "")) for item in manifest.get("notetypes", [])},
        "decks": {str(item.get("name", "")) for item in manifest.get("decks", [])},
    }
    return {
        resource: sorted(set(values) & server_keys[resource])
        for resource, values in deletes.items()
        if resource in server_keys and set(values) & server_keys[resource]
    }


def sync_content_once(
    col: Collection,
    client: V2Client,
    *,
    since: str | None = None,
    deck_name: str | None = None,
    deck_names: list[str] | None = None,
    apply_note_pulls: bool = True,
    allow_large_deletes: bool = False,
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
    # Tombstones and local delete detection are valid only against the exact
    # routing scope that produced the previous snapshot. A route change resets
    # the baseline instead of deleting data in either direction.
    snapshot = sync_state.load_state(col)
    current_scope = sorted(set(deck_names or ([deck_name] if deck_name else [])))
    previous_scope = snapshot.get("scope")
    scope_matches = snapshot.get("version") == 2 and previous_scope == current_scope
    if scope_matches:
        manifest = dict(manifest)
        manifest["tombstones"] = _tombstones_for_snapshot(manifest, snapshot)
    else:
        manifest = dict(manifest)
        manifest["tombstones"] = []
        if progress and any(snapshot.get(key) for key in ("notes", "cards", "notetypes", "decks")):
            progress("Deletes: routing scope changed (or old snapshot found); resetting deletion baseline without deleting data")

    if progress:
        progress(
            f"Server manifest: {len(manifest.get('notes', []))} notes, "
            f"{len(manifest.get('cards', []))} cards, {len(manifest.get('notetypes', []))} notetypes, "
            f"{len(manifest.get('decks', []))} decks, {len(manifest.get('media', []))} media"
        )
        progress("Phase 2/9: applying scoped server tombstones…")
    tombstones = apply_tombstones(col, manifest)
    if progress:
        progress(f"Tombstones complete: applied {tombstones.applied}")
        progress("Phase 3/9: previous scoped snapshot loaded")

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
    local_deletes = (
        sync_state.compute_local_deletes(snapshot, local_keys) if scope_matches else {}
    )
    local_deletes = _limit_deletes_to_scoped_server(local_deletes, manifest)
    delete_total = sum(len(values) for values in local_deletes.values())
    content_deletes = len(local_deletes.get("notes", [])) + len(local_deletes.get("cards", []))
    known_content = len(snapshot.get("notes", [])) + len(snapshot.get("cards", []))
    structural_deletes = bool(local_deletes.get("decks") or local_deletes.get("notetypes"))
    disproportionate = content_deletes > max(10, int(known_content * 0.10))
    needs_approval = delete_total > 100 or structural_deletes or disproportionate
    if needs_approval and not allow_large_deletes:
        raise DeletionSafetyError(local_deletes)
    if local_deletes:
        _push_local_deletes(col, client, local_deletes, progress=progress)
        if progress:
            progress("Deletes changed server state; refreshing manifest…")
        manifest = client.manifest()
        if deck_names is not None:
            manifest = _scope_server_manifest_to_decks(client, manifest, deck_names, progress=progress)
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
    result.media = sync_media_once(
        col,
        client,
        manifest,
        progress=progress,
        deck_names=deck_names or ([deck_name] if deck_name else None),
    )
    result.server_time = result.notes.server_time or manifest.get("server_time", "")

    # Save the new snapshot so next sync can detect deletes.
    if progress:
        progress("Saving sync snapshot…")
    new_state = sync_state.build_state(
        notes=sorted(local_keys["notes"]),
        cards=sorted(local_keys["cards"]),
        notetypes=sorted(local_keys["notetypes"]),
        decks=sorted(local_keys["decks"]),
        scope=current_scope,
    )
    sync_state.save_state(col, new_state)

    if progress:
        progress("Sync complete.")
    return result
