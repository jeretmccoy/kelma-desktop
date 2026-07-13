"""Kelma Desktop release updater.

Checks a Kelma-controlled manifest using the embedded Kelma bundle version,
selects the current OS/architecture, and delegates checksum-verified downloads
to Anki's existing release downloader. Installation is always user-confirmed.
"""
from __future__ import annotations

import gzip
import json
import platform
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from anki.collection import GithubRelease
from aqt.operations import QueryOp
from aqt.package import download_github_update_and_install
from aqt.qt import QMessageBox
from aqt.utils import showWarning, tooltip

UPDATE_MANIFEST_URL = "https://kelma.tech/updates/v1.json"
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class DesktopUpdate:
    version: str
    filename: str
    url: str
    sha256: str
    size: int
    notes_url: str


def version_tuple(value: str) -> tuple[int, ...]:
    """Compare numeric Kelma versions without a packaging dependency."""
    match = re.match(r"^(\d+(?:\.\d+)*)", value.strip())
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def platform_key() -> str:
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    if sys.platform == "darwin":
        return "macos-arm64" if arm else "macos-x86_64"
    if sys.platform == "win32":
        return "windows-arm64" if arm else "windows-x86_64"
    if sys.platform.startswith("linux"):
        return "linux-arm64" if arm else "linux-x86_64"
    raise RuntimeError(f"Kelma updates are not available for {sys.platform}/{machine}")


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "KelmaDesktop updater",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("update manifest is unexpectedly large")
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise RuntimeError("unsupported Kelma update manifest")
    return data


def fetch_desktop_update() -> DesktopUpdate:
    data = _fetch_json(UPDATE_MANIFEST_URL)
    desktop = data.get("desktop")
    if not isinstance(desktop, dict):
        raise RuntimeError("desktop update metadata is missing")
    platforms = desktop.get("platforms")
    artifact = platforms.get(platform_key()) if isinstance(platforms, dict) else None
    if not isinstance(artifact, dict):
        raise RuntimeError(f"no Kelma update is available for {platform_key()}")
    version = str(desktop.get("version") or "")
    filename = str(artifact.get("filename") or "")
    url = str(artifact.get("url") or "")
    sha256 = str(artifact.get("sha256") or "").lower()
    size = int(artifact.get("size") or 0)
    if not version or not filename or "/" in filename or "\\" in filename:
        raise RuntimeError("invalid desktop update metadata")
    if not url.startswith("https://") or not _SHA256_RE.fullmatch(sha256):
        raise RuntimeError("invalid desktop update URL or checksum")
    return DesktopUpdate(
        version=version,
        filename=filename,
        url=url,
        sha256=sha256,
        size=size,
        notes_url=str(desktop.get("notes_url") or "https://kelma.tech/downloads"),
    )


def _record_check(mw: Any) -> None:
    mw.pm.meta["kelma_update_last_check"] = int(time.time())
    mw.pm.save()


def check_for_kelma_update(mw: Any, *, manual: bool = False) -> None:
    """Check in the background and prompt before downloading/installing."""
    from aqt._kelma_bundled import BUNDLED_VERSION

    def on_success(update: DesktopUpdate) -> None:
        _record_check(mw)
        if version_tuple(update.version) <= version_tuple(BUNDLED_VERSION):
            if manual:
                tooltip(f"Kelma Desktop {BUNDLED_VERSION} is up to date.", parent=mw)
            return

        box = QMessageBox(mw)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Kelma Desktop update")
        box.setText(
            f"Kelma Desktop {update.version} is available.\n\n"
            f"Installed KelmaSync bundle: {BUNDLED_VERSION}\n"
            f"Download size: {update.size / (1024 * 1024):.1f} MB\n\n"
            "The download will be SHA-256 verified. Kelma will ask before "
            "opening the installer and quitting."
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        release = GithubRelease(
            tag_name=update.version,
            filename=update.filename,
            url=update.url,
            checksum=update.sha256,
        )
        download_github_update_and_install(release)

    def on_failure(error: Exception) -> None:
        _record_check(mw)
        if manual:
            showWarning(f"Could not check for Kelma updates:\n\n{error}", parent=mw)
        else:
            print(f"Kelma update check failed: {error}")

    op = QueryOp(
        parent=mw,
        op=lambda _col: fetch_desktop_update(),
        success=on_success,
    ).failure(on_failure).without_collection()
    if manual:
        op = op.with_progress("Checking for Kelma updates…")
    op.run_in_background()


def maybe_check_for_kelma_update(mw: Any) -> None:
    if not mw.pm.check_for_updates():
        return
    last = int(mw.pm.meta.get("kelma_update_last_check") or 0)
    if time.time() - last < _CHECK_INTERVAL_SECONDS:
        return
    check_for_kelma_update(mw, manual=False)
