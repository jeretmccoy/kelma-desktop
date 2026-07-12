"""KelmaSync v2 HTTP client.

This module is intentionally standalone: it only knows how to talk to the v2
REST API. It does not touch Anki collections or Qt. Higher-level sync code will
build manifests from the local collection and call this client.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class V2Error(RuntimeError):
    """Base error raised by the v2 client."""

    def __init__(self, status: int, message: str, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class V2Conflict(V2Error):
    """Raised for HTTP 409 conflict responses."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(409, "conflict", payload)
        self.server = payload.get("server")
        self.client = payload.get("client")


@dataclass(frozen=True)
class V2Auth:
    token: str
    client_id: str


class V2Client:
    def __init__(self, endpoint: str, token: str = "", timeout: int = 30) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ------------------------------------------------------------------ auth --

    def register(self, username: str, password: str) -> dict[str, Any]:
        return self._json("POST", "/v2/auth/register", {
            "username": username,
            "password": password,
        }, auth=False)

    def login(self, username: str, password: str, client_label: str) -> V2Auth:
        data = self._json("POST", "/v2/auth/login", {
            "username": username,
            "password": password,
            "client_label": client_label,
        }, auth=False)
        auth = V2Auth(token=data["token"], client_id=data["client_id"])
        self.token = auth.token
        return auth

    def logout(self) -> None:
        self._json("POST", "/v2/auth/logout", None)
        self.token = ""

    # -------------------------------------------------------------- manifest --

    def manifest(self, since: Optional[str] = None) -> dict[str, Any]:
        path = "/v2/sync/manifest"
        if since:
            path += "?" + urllib.parse.urlencode({"since": since})
        return self._json("GET", path, None)

    # ---------------------------------------------------------------- notes --

    def get_note(self, guid: str) -> dict[str, Any]:
        return self._json("GET", f"/v2/notes/{_quote(guid)}", None)

    def put_note(
        self,
        guid: str,
        *,
        notetype_id: int,
        fields: list[str],
        tags: list[str],
        client_modified_at: str,
        base_checksum: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        return self._json("PUT", f"/v2/notes/{_quote(guid)}", {
            "notetype_id": notetype_id,
            "fields": fields,
            "tags": tags,
            "client_modified_at": client_modified_at,
            "base_checksum": base_checksum,
        }, force=force)

    def delete_note(self, guid: str) -> None:
        self._request("DELETE", f"/v2/notes/{_quote(guid)}", None)

    # ---------------------------------------------------------------- cards --

    def get_card(self, card_id: int) -> dict[str, Any]:
        return self._json("GET", f"/v2/cards/{card_id}", None)

    def put_card(
        self,
        card_id: int,
        *,
        note_guid: str,
        deck_name: str,
        ord: int,
        scheduling: dict[str, Any],
        client_modified_at: str,
    ) -> dict[str, Any]:
        return self._json("PUT", f"/v2/cards/{card_id}", {
            "note_guid": note_guid,
            "deck_name": deck_name,
            "ord": ord,
            "scheduling": scheduling,
            "client_modified_at": client_modified_at,
        })

    def delete_card(self, card_id: int) -> None:
        self._request("DELETE", f"/v2/cards/{card_id}", None)

    # ------------------------------------------------------------- notetypes --

    def get_notetype(self, notetype_id: int) -> dict[str, Any]:
        return self._json("GET", f"/v2/notetypes/{notetype_id}", None)

    def put_notetype(
        self,
        notetype_id: int,
        *,
        name: str,
        definition: dict[str, Any],
        client_modified_at: str,
        base_checksum: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        return self._json("PUT", f"/v2/notetypes/{notetype_id}", {
            "name": name,
            "definition": definition,
            "client_modified_at": client_modified_at,
            "base_checksum": base_checksum,
        }, force=force)

    def delete_notetype(self, notetype_id: int) -> None:
        self._request("DELETE", f"/v2/notetypes/{notetype_id}", None)

    # ---------------------------------------------------------------- decks --

    def get_deck(self, name: str) -> dict[str, Any]:
        return self._json("GET", f"/v2/decks/{_quote(name)}", None)

    def put_deck(
        self,
        name: str,
        *,
        config: dict[str, Any],
        client_modified_at: str,
        base_checksum: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        return self._json("PUT", f"/v2/decks/{_quote(name)}", {
            "config": config,
            "client_modified_at": client_modified_at,
            "base_checksum": base_checksum,
        }, force=force)

    def delete_deck(self, name: str) -> None:
        self._request("DELETE", f"/v2/decks/{_quote(name)}", None)

    # ---------------------------------------------------------------- media --

    def has_media(self, filename: str) -> bool:
        try:
            self._request("HEAD", f"/v2/media/{_quote(filename)}", None)
            return True
        except V2Error as err:
            if err.status == 404:
                return False
            raise

    def get_media(self, filename: str) -> bytes:
        return self._request("GET", f"/v2/media/{_quote(filename)}", None)

    def put_media(self, filename: str, data: bytes, content_type: str = "application/octet-stream") -> dict[str, Any]:
        return self._json("PUT", f"/v2/media/{_quote(filename)}", data, content_type=content_type)

    def delete_media(self, filename: str) -> None:
        self._request("DELETE", f"/v2/media/{_quote(filename)}", None)

    # ---------------------------------------------------------------- batch --

    def batch_pull(
        self,
        *,
        notes: list[str] | None = None,
        cards: list[int] | None = None,
        notetypes: list[int] | None = None,
        decks: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._json("POST", "/v2/batch/pull", {
            "notes": notes or [],
            "cards": cards or [],
            "notetypes": notetypes or [],
            "decks": decks or [],
        })

    def batch_push(self, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        return self._json("POST", "/v2/batch/push", payload, force=force)

    def batch_delete(
        self,
        *,
        notes: list[str] | None = None,
        cards: list[int] | None = None,
        notetypes: list[int] | None = None,
        decks: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._json("POST", "/v2/batch/delete", {
            "notes": notes or [],
            "cards": cards or [],
            "notetypes": notetypes or [],
            "decks": decks or [],
        })

    # -------------------------------------------------------------- internals --

    def _json(
        self,
        method: str,
        path: str,
        body: Any,
        *,
        auth: bool = True,
        force: bool = False,
        content_type: str = "application/json",
    ) -> Any:
        raw = self._request(method, path, body, auth=auth, force=force, content_type=content_type)
        if raw == b"" or raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as err:
            raise V2Error(0, f"invalid JSON response: {err}") from err

    def _request(
        self,
        method: str,
        path: str,
        body: Any,
        *,
        auth: bool = True,
        force: bool = False,
        content_type: str = "application/json",
    ) -> bytes:
        url = self.endpoint + path
        data: bytes | None
        if body is None:
            data = None
        elif isinstance(body, bytes):
            data = body
        else:
            data = json.dumps(body).encode("utf-8")

        headers = {"User-Agent": "KelmaSync-v2 Python client"}
        if data is not None:
            headers["Content-Type"] = content_type
        if auth:
            if not self.token:
                raise V2Error(401, "missing token")
            headers["Authorization"] = f"Bearer {self.token}"
        if force:
            headers["Force-Override"] = "true"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            payload: Any = None
            raw = err.read()
            if raw:
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    payload = raw.decode("utf-8", errors="replace")
            if err.code == 409 and isinstance(payload, dict):
                raise V2Conflict(payload) from err
            message = payload.get("message") if isinstance(payload, dict) else str(payload or err)
            raise V2Error(err.code, message, payload) from err
        except urllib.error.URLError as err:
            raise V2Error(0, f"could not reach {self.endpoint}: {err.reason}") from err


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")
