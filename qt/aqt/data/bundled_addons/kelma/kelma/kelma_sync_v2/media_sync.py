"""Media sync for KelmaSync v2.

Media files are binary blobs stored by filename on the server. Notes reference
media by filename inside field HTML or [sound:...] tags.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import mimetypes
import re
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


def referenced_media_filenames(col: Collection) -> set[str]:
    """Return media filenames referenced by local note fields."""
    out: set[str] = set()
    for (flds,) in col.db.all("SELECT flds FROM notes"):
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


def sync_media_once(col: Collection, client: V2Client, server_manifest: dict | None = None, progress=None) -> MediaSyncResult:
    """Upload referenced local files and download missing server files.

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
    refs = sorted(referenced_media_filenames(col))
    total = len(refs)
    if progress:
        progress(f"Media: {total} referenced local files; checking uploads…")
    for i, filename in enumerate(refs, 1):
        if progress and (i == 1 or i == total or i % 1000 == 0):
            progress(f"Media upload check {i}/{total} · uploaded {result.uploaded}, skipped {result.skipped}")
        path = _safe_media_path(media_dir, filename)
        if not path.exists() or not path.is_file():
            result.skipped += 1
            continue
        if filename in server_files:
            result.skipped += 1
            continue
        data = path.read_bytes()
        client.put_media(filename, data, mimetypes.guess_type(filename)[0] or "application/octet-stream")
        server_files.add(filename)
        result.uploaded += 1

    if progress:
        progress("Media: scanning local media directory…")
    local_files = {p.name for p in media_dir.iterdir() if p.is_file()}
    server_entries = list(server_manifest.get("media", []) or [])
    total_downloads = len(server_entries)
    for i, entry in enumerate(server_entries, 1):
        if progress and (i == 1 or i == total_downloads or i % 1000 == 0):
            progress(f"Media download check {i}/{total_downloads} · downloaded {result.downloaded}, skipped {result.skipped}")
        filename = entry.get("filename")
        if not filename or filename in local_files:
            result.skipped += 1
            continue
        path = _safe_media_path(media_dir, filename)
        try:
            path.write_bytes(client.get_media(filename))
        except V2Error as err:
            # The server lists the file but can't serve its bytes (e.g. a
            # dev-server restart wiped a non-persistent blob store). Don't abort
            # the whole sync: if we have the file locally, re-upload to heal the
            # server; otherwise skip it.
            if err.status == 404:
                if path.exists() and path.is_file():
                    import mimetypes as _mt
                    client.put_media(filename, path.read_bytes(), _mt.guess_type(filename)[0] or "application/octet-stream")
                    result.uploaded += 1
                else:
                    result.skipped += 1
                continue
            raise
        local_files.add(filename)
        result.downloaded += 1

    if progress:
        progress(f"Media complete: uploaded {result.uploaded}, downloaded {result.downloaded}, skipped {result.skipped}")
    return result


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
