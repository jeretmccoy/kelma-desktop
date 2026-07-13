"""Media sync for KelmaSync v2.

Media files are binary blobs stored by filename on the server. Notes reference
media by filename inside field HTML or [sound:...] tags.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from itertools import islice
import mimetypes
import re
import time
from typing import Iterable

from anki.collection import Collection

from .client import V2Client, V2Error

_IMG_RE = re.compile(r'''(?i)<img\b[^>]*\bsrc=["']([^"']+)["']''')
_SOUND_RE = re.compile(r'''\[sound:([^\]]+)\]''')


@dataclass
class MediaSyncResult:
    uploaded: int = 0
    downloaded: int = 0
    skipped: int = 0


def referenced_media_filenames(
    col: Collection,
    deck_names: list[str] | None = None,
) -> set[str]:
    """Return files referenced by notes in the selected deck scope."""
    if deck_names:
        from .anki_local import _deck_ids_for_names

        dids = _deck_ids_for_names(col, deck_names)
        if not dids:
            return set()
        marks = ",".join("?" for _ in dids)
        rows = col.db.all(
            f"""
            SELECT DISTINCT n.flds
            FROM notes n JOIN cards c ON c.nid = n.id
            WHERE c.did IN ({marks})
            """,
            *dids,
        )
    else:
        rows = col.db.all("SELECT flds FROM notes")

    out: set[str] = set()
    for (flds,) in rows:
        text = str(flds or "")
        for m in _IMG_RE.finditer(text):
            name = _clean_media_name(m.group(1))
            if name:
                out.add(name)
        for m in _SOUND_RE.finditer(text):
            name = _clean_media_name(m.group(1))
            if name:
                out.add(name)
    return out


def sync_media_once(
    col: Collection,
    client: V2Client,
    server_manifest: dict | None = None,
    progress=None,
    deck_names: list[str] | None = None,
) -> MediaSyncResult:
    """Sync files referenced by notes in the selected deck scope.

    Fast path: use the manifest's media list instead of one HEAD request per
    local media reference. This removes the biggest sync-time multiplier.
    """
    result = MediaSyncResult()
    media_dir = Path(col.media.dir())
    media_dir.mkdir(parents=True, exist_ok=True)

    if server_manifest is None:
        if progress:
            progress("Media: fetching server media manifest…")
        server_manifest = client.manifest()
    server_files = {str(e.get("filename")) for e in (server_manifest.get("media", []) or []) if e.get("filename")}

    if progress:
        progress("Media: scanning note fields for referenced files…")
    refs = sorted(referenced_media_filenames(col, deck_names=deck_names))
    ref_set = set(refs)
    total = len(refs)
    if progress:
        progress(f"Media: {total} referenced local files; planning uploads…")

    uploads: list[tuple[str, Path, int]] = []
    for filename in refs:
        path = _safe_media_path(media_dir, filename)
        if not path.exists() or not path.is_file() or filename in server_files:
            result.skipped += 1
            continue
        uploads.append((filename, path, path.stat().st_size))

    upload_total = len(uploads)
    upload_bytes_total = sum(size for _, _, size in uploads)
    uploaded_bytes = 0
    if progress:
        progress(
            f"Media: uploading {upload_total} files "
            f"({_format_mib(upload_bytes_total)}) with 50 connections…"
        )

    def upload_one(item: tuple[str, Path, int]) -> tuple[str, int]:
        filename, path, size = item
        data = path.read_bytes()
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                client.put_media(filename, data, content_type)
                return filename, size
            except Exception as err:  # noqa: BLE001
                last_error = err
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        assert last_error is not None
        raise last_error

    # Keep only 50 futures in flight. Do not enqueue the entire collection:
    # previously, one early request error stopped progress reporting while the
    # executor silently drained thousands of already-queued uploads.
    upload_iter = iter(uploads)
    with ThreadPoolExecutor(max_workers=50, thread_name_prefix="kelma-media") as pool:
        pending = {pool.submit(upload_one, item) for item in islice(upload_iter, 50)}
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                filename, size = future.result()
                server_files.add(filename)
                result.uploaded += 1
                uploaded_bytes += size
                if progress and (
                    result.uploaded == 1
                    or result.uploaded == upload_total
                    or result.uploaded % 100 == 0
                ):
                    progress(
                        f"Media upload {result.uploaded}/{upload_total} · "
                        f"{_format_mib(uploaded_bytes)} / {_format_mib(upload_bytes_total)}"
                    )
                try:
                    item = next(upload_iter)
                except StopIteration:
                    continue
                pending.add(pool.submit(upload_one, item))

    if progress:
        progress("Media: checking server files against local media…")
    # Media is user-global on the server, but deck routing is local. Only pull
    # blobs referenced by notes in this Kelma deck scope; otherwise a dual-sync
    # Anki client would download media belonging to AnkiWeb-only decks.
    server_entries = [
        entry for entry in (server_manifest.get("media", []) or [])
        if entry.get("filename") in ref_set
    ]
    total_downloads = len(server_entries)
    if progress:
        progress(
            f"Media: checking/downloading {total_downloads} server files with 50 connections…"
        )

    def download_one(entry: dict) -> str:
        filename = entry.get("filename")
        if not filename:
            return "skipped"
        path = _safe_media_path(media_dir, str(filename))
        # Ask the filesystem rather than comparing directory-entry strings.
        # Default macOS volumes are case-insensitive, so case aliases identify
        # the same local file even when Python strings differ.
        if path.exists() and path.is_file():
            return "skipped"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                data = client.get_media(str(filename))
                # Another case-alias worker may have completed while this request
                # was in flight.
                if path.exists() and path.is_file():
                    return "skipped"
                import threading

                temp = media_dir / (
                    f".kelma-download-{abs(hash(str(filename)))}-"
                    f"{threading.get_ident()}.tmp"
                )
                try:
                    temp.write_bytes(data)
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)
                return "downloaded"
            except V2Error as err:
                if err.status == 404:
                    return "skipped"
                last_error = err
            except Exception as err:  # noqa: BLE001
                last_error = err
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
        assert last_error is not None
        raise last_error

    download_iter = iter(server_entries)
    checked = 0
    with ThreadPoolExecutor(max_workers=50, thread_name_prefix="kelma-media") as pool:
        pending = {
            pool.submit(download_one, entry)
            for entry in islice(download_iter, 50)
        }
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                status = future.result()
                checked += 1
                if status == "downloaded":
                    result.downloaded += 1
                else:
                    result.skipped += 1
                if progress and (
                    checked == 1 or checked == total_downloads or checked % 1000 == 0
                ):
                    progress(
                        f"Media download check {checked}/{total_downloads} · "
                        f"downloaded {result.downloaded}, skipped {result.skipped}"
                    )
                try:
                    entry = next(download_iter)
                except StopIteration:
                    continue
                pending.add(pool.submit(download_one, entry))

    if progress:
        progress(f"Media complete: uploaded {result.uploaded}, downloaded {result.downloaded}, skipped {result.skipped}")
    return result


def _format_mib(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MiB"


def _clean_media_name(name: str) -> str:
    # HTML may percent-encode spaces. Keep this deliberately conservative.
    name = name.strip().replace("%20", " ")
    if not name or name.startswith(("http://", "https://", "data:")):
        return ""
    # Anki media filenames should be relative file names, not paths.
    if "/" in name or "\\" in name or name in {".", ".."}:
        return ""
    return name


def _safe_media_path(media_dir: Path, filename: str) -> Path:
    clean = _clean_media_name(filename)
    if not clean:
        raise ValueError(f"unsafe media filename: {filename!r}")
    path = (media_dir / clean).resolve()
    root = media_dir.resolve()
    if root not in path.parents and path != root:
        raise ValueError(f"media path escapes directory: {filename!r}")
    return path
