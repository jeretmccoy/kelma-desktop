"""Shared timestamp policy for unambiguous newest-wins reconciliation."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

Winner = Literal["local", "server"]


def modified_timestamp(item: dict[str, Any] | None) -> float:
    """Return an item's source modification time, or 0 when it is unknown.

    Server records prefer ``client_modified_at`` because ``modified_at`` is the
    time the server accepted the write. Older manifests do not expose the
    client timestamp, so the server timestamp remains a compatibility fallback.
    Local manifests only contain ``modified_at``.
    """
    if not item:
        return 0.0
    for key in ("client_modified_at", "modified_at"):
        raw = item.get(key)
        if raw in (None, "") or isinstance(raw, bool):
            continue
        try:
            if isinstance(raw, (int, float)):
                value = float(raw)
            else:
                value = datetime.fromisoformat(
                    str(raw).replace("Z", "+00:00")
                ).timestamp()
        except (TypeError, ValueError, OverflowError):
            continue
        if value > 0:
            return value
    return 0.0


def newest_side(
    local: dict[str, Any] | None, server: dict[str, Any] | None
) -> Winner | None:
    """Return the uniquely newer side, retaining a conflict for ties/unknowns."""
    local_time = modified_timestamp(local)
    server_time = modified_timestamp(server)
    if local_time <= 0 or server_time <= 0 or local_time == server_time:
        return None
    return "local" if local_time > server_time else "server"
