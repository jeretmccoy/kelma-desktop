# Kelma Dual Sync — configuration

This add-on keeps your collection synced to **KelmaSync** and/or **AnkiWeb** with
**per-deck routing**. You study in your normal collection; the add-on maintains
two background *shadow* collections (one per service), syncs each two-way to its
server, and reconciles shared decks between them.

Everything is managed from **Tools → Kelma → Settings & deck routing** (log in to
each service and tick which decks sync where). The raw values:

- **enabled**: master switch. When `false`, the add-on stays out of the way and
  Anki's normal AnkiWeb sync is used.
- **kelmasync_url**: your KelmaSync server base URL.
- **kelmasync_hkey / kelmasync_user**: filled in after you log in (Tools → Kelma →
  Settings). The host key is a token, treat it like a password.
- **kelmasync_path**: which KelmaSync sync path to use — `"auto"` (probe the
  server), `"standard"` (per-deck reconcile, live progress), or `"legacy"` (bulk
  transfer, AnkiMobile/stock-server compatible). AnkiWeb is always legacy.
- **ankiweb_hkey / ankiweb_user**: filled in after AnkiWeb login.
- **sync_media**: also sync media for each service.
- **wrap_sync_button**: when `true`, the normal Sync button runs Kelma dual sync.
- **backup_before_sync**: create a backup before each dual sync (recommended).
- **deck_routing**: map of `deck name → list of services`, e.g.
  `{"Spanish": ["kelma", "ankiweb"], "Immersion": ["kelma"]}`. Decks not listed
  default to KelmaSync only. Edit via Tools → Kelma → Settings & deck routing.

## Known limitations (v1)

- **Deck deletions** are not auto-propagated across the two collections (note and
  card deletions are). An emptied deck may linger on the other side until removed
  manually — this is deliberate, to avoid destructive surprises.
- Reconciliation is by deck via Anki's package import (`If Newer`), so a card
  moved *between* a routed and non-routed deck may need a manual sync to settle.
