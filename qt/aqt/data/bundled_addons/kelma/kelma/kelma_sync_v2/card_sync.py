from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from anki.collection import Collection

from . import anki_apply, anki_local
from .client import V2Client
from .conflict_policy import newest_side

_BATCH_SIZE = 3000


@dataclass
class CardSyncResult:
    pushed: int = 0
    pulled: int = 0
    skipped: int = 0
    conflicts: list[dict[str, Any]] = field(default_factory=list)


class CardSyncConflict(RuntimeError):
    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        super().__init__(f"{len(conflicts)} card conflict(s)")
        self.conflicts = conflicts


def _logical_key(entry: dict) -> str:
    return entry.get("logical_key") or f"{entry.get('note_guid', '')}:{int(entry.get('ord', 0) or 0)}"


def _looks_like_interrupted_server_pull(local: dict, server: dict) -> bool:
    """Detect a pristine Default card generated while an upstream pull aborted.

    Applying a new server note makes Anki generate a card in deck id 1 with a
    current timestamp. If a later note conflict aborts before card sync, the
    next pass must not treat that generated timestamp as an intentional move
    that outranks the existing server card.
    """
    if int(local.get("deck_id", 0) or 0) != 1:
        return False
    if str(server.get("deck_name", "")).casefold() == str(
        local.get("deck_name", "")
    ).casefold():
        return False
    scheduling = local.get("scheduling") or {}
    if any(
        int(scheduling.get(field, 0) or 0) != 0
        for field in ("type", "queue", "ivl", "reps", "lapses")
    ):
        return False
    note_id = int(local.get("note_id", 0) or 0)
    card_id = int(local.get("card_id", 0) or 0)
    try:
        modified = int(
            datetime.fromisoformat(
                str(local.get("modified_at", "")).replace("Z", "+00:00")
            ).timestamp()
        )
    except (TypeError, ValueError):
        return False
    return (
        note_id > 0
        and card_id > 0
        and abs(note_id // 1000 - modified) <= 1
        and abs(card_id // 1000 - modified) <= 1
    )


def sync_cards_once(
    col: Collection,
    client: V2Client,
    server_manifest: dict | None = None,
    progress=None,
    deck_names: list[str] | None = None,
    prefer_server: bool = False,
    newest_wins: bool = False,
    server_authoritative_keys: set[str] | None = None,
) -> CardSyncResult:
    if progress:
        progress("Cards: building local card manifest…")
    local = {_logical_key(x): x for x in anki_local.card_manifest(col, deck_names=deck_names)}
    if server_manifest is None:
        server_manifest = client.manifest()
    server = {_logical_key(x): x for x in server_manifest.get("cards", [])}
    server_authoritative_keys = server_authoritative_keys or set()
    utc_now = server_manifest.get("server_time")
    result = CardSyncResult()
    keys = sorted(set(local) | set(server))
    total = len(keys)
    if progress:
        progress(f"Cards: planning {total} cards by logical identity…")
    local_only: list[int] = []
    server_pull_ids: list[int] = []  # card_ids to batch-pull
    for idx, key in enumerate(keys, 1):
        if progress and (idx == 1 or idx == total or idx % _BATCH_SIZE == 0):
            progress(f"Cards plan {idx}/{total} · to push {len(local_only)}, to pull {len(server_pull_ids)}, in sync {result.skipped}, conflicts {len(result.conflicts)}")
        l = local.get(key)
        s = server.get(key)
        if prefer_server:
            # Pulling notes into a fresh collection generates local cards in the
            # stock Default deck before card records are restored. They are not
            # competing edits: apply the server card to set its real deck and
            # scheduling, and ignore generated cards absent from the server.
            if s:
                server_pull_ids.append(int(s["card_id"]))
            else:
                result.skipped += 1
            continue
        if key in server_authoritative_keys:
            # This card did not exist locally before upstream notes/notetypes
            # were pulled. Anki just generated a pristine card with the current
            # wall-clock mod time; that is not a local edit and must never
            # overwrite the upstream card's deck or scheduling.
            if s:
                server_pull_ids.append(int(s["card_id"]))
            else:
                result.skipped += 1
            continue
        if l and s:
            if l.get("checksum") != s.get("checksum"):
                if newest_wins and _looks_like_interrupted_server_pull(l, s):
                    server_pull_ids.append(int(s["card_id"]))
                    continue
                # KelmaSync-only clients have two canonical sources, so a
                # structural move with a clear timestamp direction can use the same newest-wins
                # rule as scheduling. Ties/unknowns remain explicit conflicts.
                winner = (
                    newest_side(l, s, utc_now=utc_now) if newest_wins else None
                )
                if winner == "server":
                    server_pull_ids.append(int(s["card_id"]))
                elif winner == "local":
                    local_only.append(int(l["card_id"]))
                else:
                    result.conflicts.append({"card_id": int(l["card_id"]), "server": s, "client": l})
                continue
            # Same structure: scheduling is newest-wins by UTC source time.
            winner = newest_side(l, s, utc_now=utc_now)
            if winner == "local":
                local_only.append(int(l["card_id"]))  # push local scheduling
            elif winner == "server":
                server_pull_ids.append(int(s["card_id"]))
            else:
                result.skipped += 1
            continue
        if l and not s:
            local_only.append(int(l["card_id"]))
            continue
        if s:
            # Server-only card: batch-pull and apply if the card exists locally.
            server_pull_ids.append(int(s["card_id"]))

    # Batch-pull server cards instead of one HTTP request per card.
    if server_pull_ids:
        if progress:
            progress(f"Cards: pulling {len(server_pull_ids)} server cards in {_BATCH_SIZE}-item batches…")
        for start in range(0, len(server_pull_ids), _BATCH_SIZE):
            chunk = server_pull_ids[start:start + _BATCH_SIZE]
            resp = client.batch_pull(cards=chunk)
            for record in resp.get("cards", []):
                try:
                    anki_apply.apply_card(col, record)
                    result.pulled += 1
                except Exception:
                    result.skipped += 1
            if progress:
                progress(f"Cards: pulled {min(start + _BATCH_SIZE, len(server_pull_ids))}/{len(server_pull_ids)}…")
    if local_only:
        if progress:
            progress(f"Cards: pushing {len(local_only)} new cards in {_BATCH_SIZE}-item batches…")
        total_batches = (len(local_only) + _BATCH_SIZE - 1) // _BATCH_SIZE
        for batch_idx, start in enumerate(range(0, len(local_only), _BATCH_SIZE), 1):
            chunk = local_only[start:start + _BATCH_SIZE]
            if progress:
                progress(f"Cards: sending batch {batch_idx}/{total_batches} ({len(chunk)} cards)…")
            payload_cards = []
            for cid in chunk:
                rec = anki_local.card_record(col, cid)
                if rec:
                    payload_cards.append({
                        "card_id": cid,
                        "note_guid": rec["note_guid"],
                        "deck_name": rec["deck_name"],
                        "ord": rec["ord"],
                        "scheduling": rec["scheduling"],
                        "client_modified_at": rec["client_modified_at"],
                    })
            resp = client.batch_push({"notes": [], "cards": payload_cards, "notetypes": [], "decks": []})
            result.pushed += int((resp.get("accepted") or {}).get("cards", 0))
            if progress:
                progress(f"Cards batch {batch_idx}/{total_batches} complete · {min(start + _BATCH_SIZE, len(local_only))}/{len(local_only)} sent · pushed {result.pushed}")

    if result.conflicts:
        if progress:
            progress(f"Cards: {len(result.conflicts)} conflict(s)")
        raise CardSyncConflict(result.conflicts)
    if progress:
        progress(f"Cards complete: pushed {result.pushed}, pulled {result.pulled}, skipped {result.skipped}")
    return result
