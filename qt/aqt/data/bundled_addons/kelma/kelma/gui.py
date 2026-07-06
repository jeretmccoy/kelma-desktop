"""Menu + the unified Settings dialog (accounts login + per-deck routing),
plus integration with the Sync button."""

from __future__ import annotations

from aqt import mw
from aqt.qt import (
    QAbstractItemView,
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QCursor,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    Qt,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from aqt.utils import tooltip

from . import auth, branding, capabilities, config, consts, deckbadges, engine, features, state

_orig_sync = None

_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
# table column per service
_COL = {consts.KELMA: 1, consts.ANKIWEB: 2}


class SettingsDialog(QDialog):
    """One window: log in to each service, and pick which decks sync where."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kelma — Settings & deck routing")
        if branding.logo_enabled():
            self.setWindowIcon(branding.star_icon())
        self.resize(660, 620)
        # Shift-click range toggling state. _suppress guards programmatic edits
        # from re-entering the itemChanged handler.
        self._suppress = False
        self._anchor = {col: None for col in _COL.values()}
        outer = QVBoxLayout(self)
        outer.addWidget(_brand_header("Kelma"))

        # --- Accounts ---------------------------------------------------------
        accounts = QGroupBox("Accounts")
        grid = QGridLayout(accounts)
        self._status = {}
        for row, service in enumerate(config.ui_services()):
            grid.addWidget(QLabel(f"<b>{consts.SERVICE_LABEL[service]}</b>"), row, 0)
            status = QLabel()
            self._status[service] = status
            grid.addWidget(status, row, 1)
            btn = QPushButton("Log in / change…")
            btn.clicked.connect(lambda _=False, s=service: self._login(s))
            grid.addWidget(btn, row, 2)
        grid.setColumnStretch(1, 1)
        outer.addWidget(accounts)

        # --- Options ----------------------------------------------------------
        cfg = config.get()
        opts = QHBoxLayout()
        self.enabled_cb = QCheckBox("Enable Kelma sync")
        self.enabled_cb.setChecked(cfg["enabled"])
        self.media_cb = QCheckBox("Sync media")
        self.media_cb.setChecked(cfg["sync_media"])
        self.block_cb = QCheckBox("Block Anki's own sync")
        self.block_cb.setChecked(cfg.get("block_native_sync", True))
        self.block_cb.setToolTip(
            "Stop Anki from syncing your main collection on its own "
            "(auto-sync, the Sync button, the Y shortcut). Only Kelma's shadow "
            "collections sync — prevents conflicting double-syncs."
        )
        opts.addWidget(self.enabled_cb)
        opts.addWidget(self.media_cb)
        opts.addWidget(self.block_cb)
        opts.addStretch()
        outer.addLayout(opts)

        # KelmaSync sync path (AnkiWeb is always legacy/stock).
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("KelmaSync mode:"))
        self.path_combo = QComboBox()
        for mode in consts.PATH_MODES:
            self.path_combo.addItem(consts.PATH_LABEL[mode], mode)
        current = cfg.get("kelmasync_path", consts.PATH_AUTO)
        idx = self.path_combo.findData(current)
        self.path_combo.setCurrentIndex(idx if idx >= 0 else 0)
        path_row.addWidget(self.path_combo)
        path_row.addStretch()
        outer.addLayout(path_row)

        # --- Deck routing -----------------------------------------------------
        outer.addWidget(
            QLabel(
                "Tick where each deck should sync (a deck with neither ticked is "
                "not synced; new decks default to KelmaSync). <b>Shift-click</b> a box "
                "to set every deck in the range. The cloud columns show pending "
                "changes since that cloud last synced: <b>+n</b> added, "
                "<b>~n</b> changed, <b>✓</b> in sync."
            )
        )
        self.summary = QLabel()
        self.summary.setStyleSheet("color: palette(mid);")
        outer.addWidget(self.summary)

        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter decks…")
        self.filter.textChanged.connect(self._apply_filter)
        bar.addWidget(self.filter)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_pending)
        bar.addWidget(refresh)
        bulk_buttons = [
            ("All KelmaSync", consts.KELMA, True),
            ("Clear KelmaSync", consts.KELMA, False),
        ]
        if consts.ANKIWEB in config.ui_services():
            bulk_buttons += [
                ("All AnkiWeb", consts.ANKIWEB, True),
                ("Clear AnkiWeb", consts.ANKIWEB, False),
            ]
        for label, service, value in bulk_buttons:
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, s=service, v=value: self._bulk(s, v))
            bar.addWidget(b)
        outer.addLayout(bar)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Deck", "KelmaSync", "AnkiWeb"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        if consts.ANKIWEB not in config.ui_services():
            self.table.setColumnHidden(_COL[consts.ANKIWEB], True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        outer.addWidget(self.table)
        self._compute_status()
        self._populate()
        self.summary.setText(self._summary_text())
        # Connect after the initial populate so it only fires on user edits.
        self.table.itemChanged.connect(self._on_item_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._refresh_status()

    # -- accounts -------------------------------------------------------------
    def _login(self, service: str) -> None:
        auth.login(service, on_success=self._refresh_status)

    def _refresh_status(self) -> None:
        cfg = config.get()
        for service in consts.SERVICES:
            user = cfg["kelmasync_user"] if service == consts.KELMA else cfg["ankiweb_user"]
            if config.has_credentials(service):
                self._status[service].setText(f"✓ Logged in as <b>{user or '?'}</b>")
            else:
                self._status[service].setText("<i>not logged in</i>")

    # -- deck status ----------------------------------------------------------
    def _compute_status(self) -> None:
        names = [d.name for d in mw.col.decks.all_names_and_ids()]
        self._pending = {
            s: state.pending_for_service(mw.col, names, s) for s in consts.SERVICES
        }
        self._deletions = state.pending_deletions(mw.col)

    def _status_text(self, service: str, name: str) -> str:
        added, changed = self._pending.get(service, {}).get(name, (0, 0))
        if not added and not changed:
            return "✓"
        parts = []
        if added:
            parts.append(f"+{added}")
        if changed:
            parts.append(f"~{changed}")
        return " ".join(parts)

    def _summary_text(self) -> str:
        parts = []
        for s in consts.SERVICES:
            if not config.has_credentials(s):
                continue
            pend = self._pending.get(s, {})
            ta = sum(a for a, _ in pend.values())
            tc = sum(c for _, c in pend.values())
            ndirty = sum(1 for a, c in pend.values() if a or c)
            meta = state.last_sync(s)
            when = _ago(meta["at"]) if meta and meta.get("at") else "never"
            parts.append(
                f"<b>{consts.SERVICE_LABEL[s]}</b>: {ndirty} deck(s) pending "
                f"(+{ta} ~{tc}), synced {when}"
            )
        if self._deletions:
            parts.append(f"{self._deletions} deletion(s) pending")
        return " &nbsp;·&nbsp; ".join(parts) if parts else "No accounts logged in."

    def _refresh_pending(self) -> None:
        self._compute_status()
        self._suppress = True
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text()
            for service, col in _COL.items():
                item = self.table.item(row, col)
                checked = item.checkState() == Qt.CheckState.Checked
                item.setText(self._status_text(service, name) if checked else "")
        self._suppress = False
        self.summary.setText(self._summary_text())

    # -- deck table -----------------------------------------------------------
    def _populate(self) -> None:
        names = sorted(
            (d.name for d in mw.col.decks.all_names_and_ids()), key=str.lower
        )
        self.table.setRowCount(len(names))
        for row, name in enumerate(names):
            name_item = QTableWidgetItem(name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, name_item)
            services = config.services_for_deck(name)
            for service, col in _COL.items():
                item = QTableWidgetItem()
                item.setFlags(_CHECKABLE)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                checked = service in services
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                item.setText(self._status_text(service, name) if checked else "")
                self.table.setItem(row, col, item)

    def _apply_filter(self, text: str) -> None:
        needle = text.lower()
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().lower()
            self.table.setRowHidden(row, needle not in name)

    def _bulk(self, service: str, value: bool) -> None:
        col = _COL[service]
        check = Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        self._suppress = True
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                self.table.item(row, col).setCheckState(check)
        self._suppress = False
        self._anchor[col] = None

    def _on_item_changed(self, item) -> None:
        """Shift-click a checkbox to set every (visible) deck between it and the
        last box you changed in that column to the same state."""
        if self._suppress:
            return
        col = item.column()
        if col not in _COL.values():
            return
        row = item.row()
        check = item.checkState()
        anchor = self._anchor.get(col)
        shift = QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
        if shift and anchor is not None and anchor != row:
            lo, hi = sorted((anchor, row))
            self._suppress = True
            for r in range(lo, hi + 1):
                if self.table.isRowHidden(r):
                    continue
                cell = self.table.item(r, col)
                if cell is not None:
                    cell.setCheckState(check)
            self._suppress = False
        self._anchor[col] = row

    def _save(self) -> None:
        routing: dict[str, list[str]] = {}
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text()
            services = [
                service
                for service, col in _COL.items()
                if self.table.item(row, col).checkState() == Qt.CheckState.Checked
            ]
            routing[name] = services
        cfg = config.get()
        cfg["deck_routing"] = routing
        cfg["enabled"] = self.enabled_cb.isChecked()
        cfg["sync_media"] = self.media_cb.isChecked()
        cfg["block_native_sync"] = self.block_cb.isChecked()
        cfg["kelmasync_path"] = self.path_combo.currentData()
        config.save(cfg)
        capabilities.clear_cache()  # re-probe under the new setting/URL
        tooltip("Kelma settings saved.", parent=mw)
        self.accept()


# -----------------------------------------------------------------------------
# Sync button integration — clicking Sync opens a split menu: KelmaSync /
# AnkiWeb / both, with a details header.
# -----------------------------------------------------------------------------
def _ago(ts: int) -> str:
    import time

    secs = max(0, int(time.time()) - int(ts))
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h ago"
    return f"{hrs // 24}d ago"


def _service_line_html(service: str, deck_names: list[str], cfg: dict) -> str:
    """The status text for one account row: gold label (the clickable cue), the
    rest muted, and a trailing chevron. The enclosing row widget handles the
    click and hover, so no anchor is needed here."""
    label = consts.SERVICE_LABEL[service]
    if not config.has_credentials(service):
        body = "<i>not logged in</i> — sign in or create an account"
    else:
        decks = len(config.decks_for_service(service, deck_names))
        user = cfg["kelmasync_user"] if service == consts.KELMA else cfg["ankiweb_user"]
        extra = ""
        if service == consts.KELMA:
            extra = f" · {cfg.get('kelmasync_path', consts.PATH_AUTO)}"
        meta = state.last_sync(service)
        when = _ago(meta["at"]) if meta and meta.get("at") else "never"
        body = f"{user or '?'} · {decks} decks{extra} · synced {when}"
    accent = branding.accent()
    return (
        f'<span style="color:{accent}; font-weight:bold;">{label}</span>'
        f'<span style="color:#c9c9c9;">: {body}</span>'
        f' <span style="color:{accent};">›</span>'
    )


class _AccountRow(QLabel):
    """A clickable account status line in the sync popup, with a menu-item-style
    hover highlight and a pointing-hand cursor."""

    def __init__(self, html: str, on_click) -> None:
        super().__init__(html)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QLabel { padding: 6px 14px; }"
            f"QLabel:hover {{ background-color: {branding.accent_rgba(0.16)}; }}"
        )

    def mousePressEvent(self, ev) -> None:  # noqa: N802 - Qt override
        self._on_click()


def _brand_header(text: str) -> QWidget:
    """A menu/dialog header row: the Kelma star logo (if enabled) + a title,
    tinted with the Kelma accent when the theme toggle is on."""
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(14, 0, 14, 6)
    row.setSpacing(8)
    if branding.logo_enabled():
        icon = QLabel()
        icon.setPixmap(branding.star_pixmap(18))
        row.addWidget(icon)
    if branding.theme_enabled():
        title = QLabel(f'<b style="color:{branding.accent()};">{text}</b>')
    else:
        title = QLabel(f"<b>{text}</b>")
    row.addWidget(title)
    row.addStretch(1)
    return w


def _sync_menu() -> None:
    deck_names = [d.name for d in mw.col.decks.all_names_and_ids()]
    menu = QMenu(mw)

    pending = {"login": None}

    def _on_account_click(service: str) -> None:
        # Defer: opening the auth modal from inside the menu's own modal loop
        # would nest event loops and freeze the UI. Record the pick, close the
        # menu, and act once exec() returns (same pattern as the actions below).
        pending["login"] = service
        menu.close()

    cfg = config.get()
    container = QWidget()
    box = QVBoxLayout(container)
    box.setContentsMargins(0, 8, 0, 4)
    box.setSpacing(0)
    box.addWidget(_brand_header("Kelma sync"))
    for service in config.ui_services():
        row = _AccountRow(
            _service_line_html(service, deck_names, cfg),
            lambda s=service: _on_account_click(s),
        )
        box.addWidget(row)

    wa = QWidgetAction(menu)
    wa.setDefaultWidget(container)
    menu.addAction(wa)
    menu.addSeparator()

    aw = consts.ANKIWEB in config.ui_services()
    both = a_w = None
    if aw:
        both = menu.addAction("Sync KelmaSync + AnkiWeb")
        a_k = menu.addAction("Sync KelmaSync only")
        a_w = menu.addAction("Sync AnkiWeb only")
    else:
        a_k = menu.addAction("Sync now")
    menu.addSeparator()
    a_set = menu.addAction("Settings && deck routing…")

    # Dispatch AFTER the menu's modal loop closes — starting a progress dialog
    # from inside a triggered handler nests modal loops and freezes the UI.
    chosen = menu.exec(QCursor.pos())

    # A clicked account link wins (menu.close() makes exec() return None). On a
    # successful login/register, reopen the menu so the fresh status shows.
    if pending["login"] in consts.SERVICES:
        auth.login(pending["login"], on_success=_sync_menu)
        return
    if chosen is None:
        return  # menu dismissed
    if both is not None and chosen is both:
        engine.dual_sync()
    elif chosen is a_k:
        engine.dual_sync(only=consts.KELMA)
    elif a_w is not None and chosen is a_w:
        engine.dual_sync(only=consts.ANKIWEB)
    elif chosen is a_set:
        SettingsDialog(mw).exec()


def _wrapped_sync() -> None:
    cfg = config.get()
    if not cfg["enabled"] or not cfg["wrap_sync_button"]:
        if _orig_sync:
            return _orig_sync()
        return
    _sync_menu()


def _install_sync_hook() -> None:
    global _orig_sync
    if _orig_sync is not None:
        return
    _orig_sync = mw.on_sync_button_clicked
    mw.on_sync_button_clicked = _wrapped_sync
    mw.onSync = _wrapped_sync  # legacy alias used by some callers


_orig_collection_sync = None


def _install_native_sync_guard() -> None:
    """Block the master collection from syncing to AnkiWeb on its own.

    `_sync_collection_and_media` is the single chokepoint for Anki's native sync —
    auto-sync on open/close, the Y shortcut, and the (unwrapped) Sync button all
    route through it. While Kelma is enabled and "block native sync" is on, we
    make it a no-op (still calling the continuation so open/close proceeds), so
    only the Kelma shadows ever write to AnkiWeb — no two-writers conflict.
    """
    global _orig_collection_sync
    if _orig_collection_sync is not None:
        return
    _orig_collection_sync = mw._sync_collection_and_media

    def guarded(after_sync):
        cfg = config.get()
        if cfg.get("enabled", True) and cfg.get("block_native_sync", True):
            after_sync()  # skip the native sync; let the flow continue
        else:
            _orig_collection_sync(after_sync)

    mw._sync_collection_and_media = guarded


# -----------------------------------------------------------------------------
# Menu
# -----------------------------------------------------------------------------
def _build_menu() -> None:
    menu = QMenu("&Kelma", mw)
    if branding.logo_enabled():
        menu.setIcon(branding.star_icon())
    mw.form.menuTools.addMenu(menu)

    aw = consts.ANKIWEB in config.ui_services()
    if aw:
        act_sync = QAction("Sync now (KelmaSync + AnkiWeb)", mw)
        act_sync.triggered.connect(lambda: engine.dual_sync())
        menu.addAction(act_sync)

    act_kelma = QAction("Sync now" if not aw else "Sync KelmaSync only", mw)
    act_kelma.triggered.connect(lambda: engine.dual_sync(only=consts.KELMA))
    menu.addAction(act_kelma)

    if aw:
        act_ankiweb = QAction("Sync AnkiWeb only", mw)
        act_ankiweb.triggered.connect(lambda: engine.dual_sync(only=consts.ANKIWEB))
        menu.addAction(act_ankiweb)

    menu.addSeparator()

    act_storage = QAction("Storage breakdown…", mw)
    act_storage.triggered.connect(_open_storage)
    menu.addAction(act_storage)

    act_diag = QAction("Sync diagnostics…", mw)
    act_diag.triggered.connect(_open_diagnostics)
    menu.addAction(act_diag)

    _build_display_menu(menu)

    act_settings = QAction("Settings && deck routing…", mw)
    act_settings.triggered.connect(lambda: SettingsDialog(mw).exec())
    menu.addAction(act_settings)


def _open_storage() -> None:
    from .storageview import StorageDialog

    StorageDialog(mw).exec()


def _open_diagnostics() -> None:
    from .diagnostics import DiagnosticsDialog

    DiagnosticsDialog(mw).exec()


def _build_display_menu(parent: QMenu) -> None:
    """A checkable submenu, auto-populated from the features registry, for
    toggling visual modifications (deck badges, and future ones)."""
    sub = QMenu("Display modifications", mw)
    parent.addMenu(sub)
    for feat in features.FEATURES:
        act = QAction(feat.label, mw)
        act.setCheckable(True)
        act.setChecked(features.enabled(feat.key))
        act.setToolTip(feat.description)
        act.toggled.connect(
            lambda checked, k=feat.key: _toggle_feature(k, checked)
        )
        sub.addAction(act)


def _toggle_feature(key: str, checked: bool) -> None:
    features.set_enabled(key, checked)
    # Re-render the main screen so the change shows immediately.
    try:
        mw.reset()
    except Exception:  # noqa: BLE001
        pass


def setup() -> None:
    """Entry point, called once after the profile/collection is ready."""
    _build_menu()
    _install_sync_hook()
    _install_native_sync_guard()
    deckbadges.setup()
