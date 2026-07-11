"""Apply server tombstones to local Anki state."""
from __future__ import annotations

from dataclasses import dataclass, field

from anki.collection import Collection

from . import anki_apply


@dataclass
class TombstoneSyncResult:
    applied: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def apply_tombstones(col: Collection, manifest: dict) -> TombstoneSyncResult:
    """Apply tombstones from a server manifest locally.

    Notes are applied before cards/decks/notetypes so dependent resources are
    removed in the safest order.
    """
    result = TombstoneSyncResult()
    tombstones = list(manifest.get("tombstones", []) or [])
    order = {"note": 0, "card": 1, "deck": 2, "notetype": 3, "media": 4}
    tombstones.sort(key=lambda t: order.get(t.get("type", ""), 99))
    for t in tombstones:
        typ = t.get("type")
        rid = str(t.get("resource_id", ""))
        try:
            ok = False
            if typ == "note":
                ok = anki_apply.delete_note(col, rid)
            elif typ == "card":
                ok = anki_apply.delete_card(col, int(rid))
            elif typ == "deck":
                ok = anki_apply.delete_deck(col, rid)
            elif typ == "notetype":
                ok = anki_apply.delete_notetype(col, int(rid))
            elif typ == "media":
                # Media local deletion is handled by the media sync phase.
                ok = False
            if ok:
                result.applied += 1
            else:
                result.skipped += 1
        except Exception as err:  # noqa: BLE001
            result.errors.append(f"{typ}:{rid}: {err}")
    return result
