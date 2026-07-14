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


def _running_in_kelma_desktop() -> bool:
    """Detect Desktop at runtime without changing the shared profile config."""
    try:
        from aqt import _kelma_bundled
    except (ImportError, AttributeError):
        return False
    return bool(getattr(_kelma_bundled, "IS_KELMA_DESKTOP", False))


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
    # KelmaDesktop also enforces this at runtime so shared Anki settings cannot
    # re-enable native sync inside the Desktop process.
    cfg.setdefault("kelmasync_only", False)
    # KelmaSync v2 experimental REST client config. Kept separate from v1 hkey
    # auth so the existing sync path remains untouched.
    cfg.setdefault("v2_url", consts.DEFAULT_V2_URL)
    current_v2_url = str(cfg.get("v2_url") or "").rstrip("/")
    if not current_v2_url or current_v2_url in consts.LEGACY_V2_URLS:
        # Both hostnames reach the same service/token database. Migrate the URL
        # without discarding the saved token, so existing clients do not need to
        # sign in again.
        cfg["v2_url"] = consts.DEFAULT_V2_URL
    cfg.setdefault("v2_username", "")
    cfg.setdefault("v2_token", "")
    cfg.setdefault("v2_client_id", "")
    cfg.setdefault(
        "v2_client_label",
        "KelmaDesktop" if _running_in_kelma_desktop() else "Anki plugin",
    )
    cfg.setdefault("v2_last_server_time", "")
    cfg.setdefault("v2_allow_large_deletes", False)
    return cfg


def kelmasync_only() -> bool:
    # KelmaDesktop and regular Anki can share one profile/add-ons directory.
    # Force Desktop-only routing in memory without persisting it into the
    # standalone Anki plugin's configuration.
    return _running_in_kelma_desktop() or bool(
        get().get("kelmasync_only", False)
    )


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
    if kelmasync_only() and service != consts.KELMA:
        return False
    cfg = get()
    if service == consts.KELMA:
        # KelmaSync v2 stores a bearer token. The legacy host key may remain on
        # upgraded profiles, but fresh Desktop/Mobile-era logins only have
        # v2_token and must still show badges/account state.
        return bool(cfg.get("v2_token") or cfg.get("kelmasync_hkey"))
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
