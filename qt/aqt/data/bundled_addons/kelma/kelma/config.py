"""Add-on configuration access + per-deck routing resolution.

Routing model: `deck_routing` maps a deck name to the list of services it syncs
to, e.g. `{"Spanish": ["kelma", "ankiweb"], "Immersion": ["kelma"]}`. There is
no global mode — every deck is routed individually (decks with no entry use
`consts.DEFAULT_SERVICES`). Subdecks inherit the nearest configured ancestor.
"""

from __future__ import annotations

from typing import Any

from aqt import mw

from . import consts

# The add-on's package/dir name (e.g. "kelma" or a numeric AnkiWeb id).
ADDON = __name__.split(".")[0]


def get() -> dict[str, Any]:
    cfg = mw.addonManager.getConfig(ADDON) or {}
    cfg.setdefault("enabled", True)
    cfg.setdefault("kelmasync_url", consts.DEFAULT_KELMA_URL)
    cfg.setdefault("kelmasync_hkey", "")
    cfg.setdefault("kelmasync_user", "")
    cfg.setdefault("kelmasync_path", consts.PATH_AUTO)
    cfg.setdefault("ankiweb_hkey", "")
    cfg.setdefault("ankiweb_user", "")
    cfg.setdefault("sync_media", True)
    cfg.setdefault("wrap_sync_button", True)
    cfg.setdefault("block_native_sync", True)
    cfg.setdefault("backup_before_sync", True)
    cfg.setdefault("deck_routing", {})
    cfg.setdefault("features", {})
    # KelmaSync-only mode: hide every AnkiWeb surface (account, sync options,
    # routing column). Off by default — the standalone plugin is dual-sync; the
    # KelmaDesktop bundle turns this on so the app never references AnkiWeb.
    cfg.setdefault("kelmasync_only", False)
    # KelmaSync v2 experimental REST client config. Kept separate from v1 hkey
    # auth so the existing sync path remains untouched.
    cfg.setdefault("v2_url", "https://sync2.ankiai.tech")
    cfg.setdefault("v2_username", "")
    cfg.setdefault("v2_token", "")
    cfg.setdefault("v2_client_id", "")
    cfg.setdefault("v2_client_label", "Anki plugin")
    cfg.setdefault("v2_last_server_time", "")
    return cfg


def kelmasync_only() -> bool:
    return bool(get().get("kelmasync_only", False))


def ui_services() -> tuple[str, ...]:
    """Services the UI should expose — KelmaSync only in KelmaSync-only mode."""
    return (consts.KELMA,) if kelmasync_only() else consts.SERVICES


def save(cfg: dict[str, Any]) -> None:
    mw.addonManager.writeConfig(ADDON, cfg)


def set_value(key: str, value: Any) -> None:
    cfg = get()
    cfg[key] = value
    save(cfg)


def has_credentials(service: str) -> bool:
    cfg = get()
    if service == consts.KELMA:
        return bool(cfg["kelmasync_hkey"])
    return bool(cfg["ankiweb_hkey"])


def _normalize(services: Any) -> tuple[str, ...]:
    if not isinstance(services, (list, tuple)):
        return ()
    return tuple(s for s in consts.SERVICES if s in services)


def services_for_deck(deck_name: str) -> tuple[str, ...]:
    """Services a deck syncs to: explicit entry, else nearest ancestor, else default."""
    routing: dict[str, Any] = get()["deck_routing"]
    if deck_name in routing:
        return _normalize(routing[deck_name])
    parts = deck_name.split("::")
    for i in range(len(parts) - 1, 0, -1):
        ancestor = "::".join(parts[:i])
        if ancestor in routing:
            return _normalize(routing[ancestor])
    return consts.DEFAULT_SERVICES


def decks_for_service(service: str, all_deck_names: list[str]) -> list[str]:
    return [n for n in all_deck_names if service in services_for_deck(n)]


def active_services(all_deck_names: list[str]) -> tuple[str, ...]:
    """Services to actually sync: enabled, have credentials, and at least one
    deck routes to them."""
    if not get()["enabled"]:
        return ()
    out = []
    for s in consts.SERVICES:
        if has_credentials(s) and decks_for_service(s, all_deck_names):
            out.append(s)
    return tuple(out)
