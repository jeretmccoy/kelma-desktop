"""Note-only v2 sync orchestration for the Anki plugin.

This is the first usable sync path. It intentionally ignores cards/notetypes/
decks/media until notes are proven end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_BATCH_SIZE = 3000

from anki.collection import Collection

from .client import V2Client, V2Conflict
from . import anki_apply, anki_local


@dataclass
class NoteSyncResult:
    pushed: int = 0
    pulled: int = 0
    skipped: int = 0
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    server_time: str = ""


class NoteSyncConflict(RuntimeError):
    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        super().__init__(f"{len(conflicts)} note conflict(s)")
        self.conflicts = conflicts


def sync_notes_once(
    col: Collection,
    client: V2Client,
    *,
    since: str | None = None,
    apply_pulls: bool = True,
    deck_name: str | None = None,
    deck_names: list[str] | None = None,
    server_manifest: dict[str, Any] | None = None,
    progress=None,
) -> NoteSyncResult:
    """Run one note-only sync pass.

    Behavior:
    - local-only notes are pushed
    - server-only notes are pulled/applied when `apply_pulls` is true
    - checksum mismatches attempt a normal push with server checksum as base;
      if server changed concurrently, the server returns 409 and we collect it
    - if conflicts exist, raises `NoteSyncConflict` after processing safe items

    The caller should persist `result.server_time` only if no exception is
    raised and the overall sync is considered successful.
    """
    if progress:
        progress("Notes: building local note manifest…")
    if deck_name and not deck_names:
        deck_names = [deck_name]
    local_manifest = {x["guid"]: x for x in anki_local.note_manifest(col, deck_names=deck_names, progress=progress)}
    if server_manifest is None:
        if progress:
            progress("Notes: fetching full server manifest for checksum comparison…")
        # Full manifest required: an incremental manifest omits unchanged server
        # notes, making them appear local-only and causing pointless re-pushes.
        server_manifest = client.manifest()
    server_notes = {x["guid"]: x for x in server_manifest.get("notes", [])}
    result = NoteSyncResult(server_time=server_manifest.get("server_time", ""))

    if deck_name and not apply_pulls:
        # Test-deck mode: only consider local notes in that deck. Server-only
        # notes cannot be safely attributed to the test deck until card/deck
        # sync is implemented.
        all_guids = sorted(set(local_manifest))
    else:
        all_guids = sorted(set(local_manifest) | set(server_notes))
    total = len(all_guids)
    if progress:
        progress(f"Notes: planning {total} notes by checksum…")
    local_only: list[str] = []
    server_only: list[str] = []
    for idx, guid in enumerate(all_guids, 1):
        if progress and (idx == 1 or idx == total or idx % _BATCH_SIZE == 0):
            progress(f"Notes plan {idx}/{total} · to push {len(local_only)}, to pull {len(server_only)}, in sync {result.skipped}, conflicts {len(result.conflicts)}")
        local = local_manifest.get(guid)
        server = server_notes.get(guid)
        if local and server and local.get("checksum") == server.get("checksum"):
            result.skipped += 1
            continue
        if local and not server:
            local_only.append(guid)
            continue
        if server and not local:
            if apply_pulls:
                server_only.append(guid)
            else:
                result.skipped += 1
            continue
        if local and server:
            # Both sides exist and content checksums differ. Do not silently
            # choose local; surface this as an explicit merge conflict.
            result.conflicts.append({"guid": guid, "server": server, "client": local})

    # Batch-pull server-only notes instead of one HTTP request per note.
    if server_only:
        if progress:
            progress(f"Notes: pulling {len(server_only)} server-only notes in {_BATCH_SIZE}-item batches…")
        for start in range(0, len(server_only), _BATCH_SIZE):
            chunk = server_only[start:start + _BATCH_SIZE]
            resp = client.batch_pull(notes=chunk)
            for record in resp.get("notes", []):
                try:
                    anki_apply.apply_note(col, record)
                    result.pulled += 1
                except Exception:
                    result.skipped += 1
            if progress:
                progress(f"Notes: pulled {min(start + _BATCH_SIZE, len(server_only))}/{len(server_only)}…")

    if local_only:
        if progress:
            progress(f"Notes: pushing {len(local_only)} new notes in {_BATCH_SIZE}-item batches…")
        total_batches = (len(local_only) + _BATCH_SIZE - 1) // _BATCH_SIZE
        for batch_idx, start in enumerate(range(0, len(local_only), _BATCH_SIZE), 1):
            chunk = local_only[start:start + _BATCH_SIZE]
            if progress:
                progress(f"Notes: sending batch {batch_idx}/{total_batches} ({len(chunk)} notes)…")
            payload_notes = []
            for guid in chunk:
                rec = anki_local.note_record(col, guid)
                if rec:
                    payload_notes.append({
                        "guid": guid,
                        "notetype_id": rec["notetype_id"],
                        "fields": rec["fields"],
                        "tags": rec["tags"],
                        "client_modified_at": rec["client_modified_at"],
                        "base_checksum": "",
                    })
            resp = client.batch_push({"notes": payload_notes, "cards": [], "notetypes": [], "decks": []})
            result.pushed += int((resp.get("accepted") or {}).get("notes", 0))
            conflicts = (resp.get("conflicts") or {}).get("notes", []) or []
            result.conflicts.extend(conflicts)
            if progress:
                progress(f"Notes batch {batch_idx}/{total_batches} complete · {min(start + _BATCH_SIZE, len(local_only))}/{len(local_only)} sent · pushed {result.pushed}, conflicts {len(result.conflicts)}")

    if result.conflicts:
        if progress:
            progress(f"Notes: {len(result.conflicts)} conflict(s)")
        raise NoteSyncConflict(result.conflicts)
    if progress:
        progress(f"Notes complete: pushed {result.pushed}, pulled {result.pulled}, skipped {result.skipped}")
    return result


def force_local_note(col: Collection, client: V2Client, guid: str) -> dict[str, Any]:
    """Resolution action: local note wins; overwrite server."""
    return _push_note(col, client, guid, base_checksum="", force=True)


def accept_server_note(col: Collection, client: V2Client, guid: str) -> int:
    """Resolution action: server note wins; overwrite local."""
    return anki_apply.apply_server_note(col, client, guid)


def _push_note(
    col: Collection,
    client: V2Client,
    guid: str,
    *,
    base_checksum: str,
    force: bool = False,
) -> dict[str, Any]:
    rec = anki_local.note_record(col, guid)
    if rec is None:
        raise ValueError(f"local note not found: {guid}")
    return client.put_note(
        guid,
        notetype_id=rec["notetype_id"],
        fields=rec["fields"],
        tags=rec["tags"],
        client_modified_at=rec["client_modified_at"],
        base_checksum=base_checksum,
        force=force,
    )
