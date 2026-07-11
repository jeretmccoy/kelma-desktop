"""Full server-vs-local diff for all resource types.

Fetches the complete server manifest, builds the local manifest, and classifies
every resource by status. For changed items, optionally fetches full records for
field-level comparison.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from anki.collection import Collection

from .client import V2Client
from . import anki_local

Status = Literal["in-sync", "local-only", "server-only", "changed"]


@dataclass
class DiffEntry:
    resource: str  # "notes" | "cards" | "notetypes" | "decks"
    key: str  # guid / card_id / notetype_id / name
    status: Status
    local: dict[str, Any] | None = None
    server: dict[str, Any] | None = None


@dataclass
class FullDiff:
    notes: list[DiffEntry] = field(default_factory=list)
    cards: list[DiffEntry] = field(default_factory=list)
    notetypes: list[DiffEntry] = field(default_factory=list)
    decks: list[DiffEntry] = field(default_factory=list)
    server_time: str = ""

    @property
    def total_changed(self) -> int:
        return sum(
            len([e for e in entries if e.status != "in-sync"])
            for entries in (self.notes, self.cards, self.notetypes, self.decks)
        )


def build_full_diff(col: Collection, client: V2Client) -> FullDiff:
    """Build a full diff between local collection and server state.

    This fetches the server manifest (lightweight checksums only) and compares
    to the local manifest. It does NOT fetch full records — the caller can fetch
    individual records on demand when the user drills into a specific entry.
    """
    server = client.manifest()
    local = anki_local.local_manifest(col)

    diff = FullDiff(server_time=server.get("server_time", ""))
    diff.notes = _diff_keyed(local.get("notes", []), server.get("notes", []), "guid")
    diff.cards = _diff_keyed(local.get("cards", []), server.get("cards", []), "card_id")
    diff.notetypes = _diff_keyed(local.get("notetypes", []), server.get("notetypes", []), "notetype_id")
    diff.decks = _diff_keyed(local.get("decks", []), server.get("decks", []), "name")
    return diff


def _diff_keyed(
    local: list[dict[str, Any]],
    server: list[dict[str, Any]],
    key: str,
) -> list[DiffEntry]:
    lmap = {str(x.get(key, "")): x for x in local if x.get(key, "") != ""}
    smap = {str(x.get(key, "")): x for x in server if x.get(key, "") != ""}
    out: list[DiffEntry] = []
    for k in sorted(set(lmap) | set(smap)):
        l = lmap.get(k)
        s = smap.get(k)
        if l and not s:
            out.append(DiffEntry(resource=key, key=k, status="local-only", local=l))
        elif s and not l:
            out.append(DiffEntry(resource=key, key=k, status="server-only", server=s))
        else:
            assert l is not None and s is not None
            if "checksum" in l and "checksum" in s:
                status: Status = "in-sync" if l["checksum"] == s["checksum"] else "changed"
                # Card checksums are deliberately structural and exclude
                # scheduling. In the source-review UI, equal structure with a
                # different scheduling timestamp must still be visible so the
                # user can choose Anki/AnkiWeb or KelmaSync as canonical.
                if key == "logical_key" and status == "in-sync":
                    local_ts = str(l.get("modified_at") or "")
                    server_ts = str(s.get("client_modified_at") or s.get("modified_at") or "")
                    if local_ts != server_ts:
                        status = "changed"
            elif "modified_at" in l and "modified_at" in s:
                status = "in-sync" if l["modified_at"] == s["modified_at"] else "changed"
            else:
                status = "changed"
            out.append(DiffEntry(resource=key, key=k, status=status, local=l, server=s))
    return out
