"""Shared constants for the Kelma Dual Sync add-on."""

# Services
KELMA = "kelma"
ANKIWEB = "ankiweb"
SERVICES = (KELMA, ANKIWEB)

SERVICE_LABEL = {
    KELMA: "KelmaSync",
    ANKIWEB: "AnkiWeb",
}

# Routing is purely per-deck: each deck syncs to a set of services. A deck with
# no explicit routing (e.g. a newly created one) falls back to DEFAULT_SERVICES.
# KelmaSync-only by default, so nothing is pushed to AnkiWeb until you opt a deck
# in from the Settings dialog.
DEFAULT_SERVICES = (KELMA,)

# Shadow collection filenames (kept next to the master collection).
SHADOW_FILENAME = {
    KELMA: "kelma_shadow_kelmasync.anki2",
    ANKIWEB: "kelma_shadow_ankiweb.anki2",
}

DEFAULT_KELMA_URL = "https://sync.kelma.tech/"

# Account creation is web-based (branded sign-up, email verification, etc.), not
# done in-client — the login dialog links out to these. KelmaSync accounts are
# created on Kelma Immersion (kelma.tech); AnkiWeb accounts on ankiweb.net.
KELMA_SIGNUP_URL = "https://kelma.tech"
ANKIWEB_SIGNUP_URL = "https://ankiweb.net/account/register"
SIGNUP_URL = {
    KELMA: KELMA_SIGNUP_URL,
    ANKIWEB: ANKIWEB_SIGNUP_URL,
}

# KelmaSync sync paths.
#   standard  – incremental: fingerprint the collection and only move the decks
#               that changed since the last sync (fast).
#   legacy    – move every routed deck every time; maximal compatibility with
#               AnkiMobile and stock Anki sync servers (slower).
#   auto      – probe the server and pick one (manual override always wins).
# AnkiWeb is always legacy (real AnkiWeb only speaks the stock protocol).
PATH_AUTO = "auto"
PATH_STANDARD = "standard"
PATH_LEGACY = "legacy"
PATH_MODES = (PATH_AUTO, PATH_STANDARD, PATH_LEGACY)
PATH_LABEL = {
    PATH_AUTO: "Auto (detect from server)",
    PATH_STANDARD: "Standard — incremental (only changed decks)",
    PATH_LEGACY: "Legacy — full every time (AnkiMobile-safe)",
}
