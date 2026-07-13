"""Shared constants for the Kelma Dual Sync add-on."""

KELMA_CLIENT_VERSION = "1.0.115"
UPDATE_MANIFEST_URL = "https://kelma.tech/updates/v1.json"

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
# Canonical KelmaSync v2 REST hostname. The old ankiai.tech alias remains live
# for already-installed clients, but all new/default traffic uses kelma.tech.
DEFAULT_V2_URL = "https://sync2.kelma.tech"
LEGACY_V2_URLS = {"https://sync2.ankiai.tech"}

# Account creation is web-based (branded sign-up, email verification, etc.), not
# done in-client — the login dialog links out to these. KelmaSync accounts are
# created on Kelma Immersion (kelma.tech); AnkiWeb accounts on ankiweb.net.
KELMA_SIGNUP_URL = "https://kelma.tech"
ANKIWEB_SIGNUP_URL = "https://ankiweb.net/account/register"
SIGNUP_URL = {
    KELMA: KELMA_SIGNUP_URL,
    ANKIWEB: ANKIWEB_SIGNUP_URL,
}

# KelmaSync compatibility modes. Reconciliation remains routed and
# change-detected in every mode; "legacy" records that a server advertises
# stock-compatible behavior.
# AnkiWeb is always legacy (real AnkiWeb only speaks the stock protocol).
PATH_AUTO = "auto"
PATH_STANDARD = "standard"
PATH_LEGACY = "legacy"
PATH_MODES = (PATH_AUTO, PATH_STANDARD, PATH_LEGACY)
PATH_LABEL = {
    PATH_AUTO: "Auto (detect from server)",
    PATH_STANDARD: "Standard — incremental (only changed decks)",
    PATH_LEGACY: "Legacy-compatible server",
}
