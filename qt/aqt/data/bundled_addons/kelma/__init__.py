"""Kelma Dual Sync — Anki add-on entry point.

Keeps your collection synced to KelmaSync and/or AnkiWeb with per-deck routing,
using a background shadow collection per service. See config.md.
"""

from aqt import gui_hooks

_initialized = False


def _on_profile_open() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True
    try:
        from .kelma import gui

        gui.setup()
    except Exception as err:  # noqa: BLE001 - never break Anki startup
        from aqt.utils import showWarning

        showWarning(f"Kelma Dual Sync failed to initialize:\n{err}")


gui_hooks.profile_did_open.append(_on_profile_open)
