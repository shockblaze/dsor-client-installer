"""HTTP client for the server's login/register service.

The launcher owns no accounts of its own: registering, logging in, refreshing,
and logging out are all requests to the account service, which is the only
place passwords are checked and session GUIDs are issued. Everything here is
standard library so the frozen executables stay dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_SERVER_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT_SECONDS = 10.0
USER_AGENT = "dso-launcher/1.0"


class AccountServiceError(Exception):
    """A request the service refused, with the message it gave.

    Messages never name the server address. They are shown in the launcher
    and end up in screenshots, and publishing where the game lives invites
    exactly the traffic a small server cannot absorb. ``url`` carries it for
    the few places that deliberately report the configured address.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url


class ServiceUnreachableError(AccountServiceError):
    """The service could not be contacted at all."""


@dataclass(frozen=True)
class Session:
    """A session issued by the service, plus where to point the client."""

    session_id: str
    account_id: int
    username: str
    issued_at: float
    expires_at: float
    expires_in: float
    character_ids: tuple[int, ...]
    host: str
    gateway_port: int
    chat_port: int
    instance_id: int

    @classmethod
    def from_response(cls, document: dict) -> "Session":
        session = document.get("session") or {}
        launch = document.get("launch") or {}
        try:
            return cls(
                session_id=str(session["session_id"]),
                account_id=int(session["account_id"]),
                username=str(session["username"]),
                issued_at=float(session.get("issued_at", 0.0)),
                expires_at=float(session["expires_at"]),
                expires_in=float(session.get("expires_in", 0.0)),
                character_ids=tuple(
                    int(value) for value in session.get("character_ids", ())
                ),
                host=str(launch.get("host", "")),
                gateway_port=int(launch.get("gateway_port", 80)),
                chat_port=int(launch.get("chat_port", 2191)),
                instance_id=int(launch.get("instance_id", 481)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountServiceError(
                f"the service returned a malformed session: {exc}"
            )

    def to_document(self) -> dict:
        return {
            "session_id": self.session_id,
            "account_id": self.account_id,
            "username": self.username,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "character_ids": list(self.character_ids),
            "host": self.host,
            "gateway_port": self.gateway_port,
            "chat_port": self.chat_port,
            "instance_id": self.instance_id,
        }

    @classmethod
    def from_document(cls, document: dict) -> "Session | None":
        try:
            return cls(
                session_id=str(document["session_id"]),
                account_id=int(document["account_id"]),
                username=str(document["username"]),
                issued_at=float(document.get("issued_at", 0.0)),
                expires_at=float(document["expires_at"]),
                expires_in=0.0,
                character_ids=tuple(
                    int(value) for value in document.get("character_ids", ())
                ),
                host=str(document.get("host", "")),
                gateway_port=int(document.get("gateway_port", 80)),
                chat_port=int(document.get("chat_port", 2191)),
                instance_id=int(document.get("instance_id", 481)),
            )
        except (KeyError, TypeError, ValueError):
            return None


def normalize_server_url(url: str) -> str:
    """Accept ``host``, ``host:port``, or a full URL and return a base URL."""

    candidate = str(url or "").strip()
    if not candidate:
        raise AccountServiceError("the account service address is empty")
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    # Trailing slashes are stripped after the scheme test, or a bare "http://"
    # would lose its separator and be re-read as the host name "http".
    parsed = urllib.parse.urlparse(candidate.rstrip("/"))
    if parsed.scheme not in ("http", "https"):
        raise AccountServiceError(
            f"unsupported address scheme {parsed.scheme!r}; use http or https"
        )
    if not parsed.hostname:
        raise AccountServiceError(f"{url!r} has no host")
    port = parsed.port
    if port is None and parsed.scheme == "http":
        port = 8080
    netloc = parsed.hostname
    if ":" in netloc:  # IPv6 literal
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{parsed.scheme}://{netloc}"


def without_address(message: str, url: str) -> str:
    """Strip the server's address out of a message before it is displayed."""

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    cleaned = str(message).replace(url, "the server")
    if host:
        if parsed.port:
            cleaned = cleaned.replace(f"{host}:{parsed.port}", "the server")
        cleaned = cleaned.replace(host, "the server")
    return cleaned


class AccountServiceClient:
    """Thin JSON client for one account service."""

    def __init__(
        self,
        url: str = DEFAULT_SERVER_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        service_token: str = "",
    ) -> None:
        self.url = normalize_server_url(url)
        self.timeout = float(timeout)
        self.service_token = str(service_token or "")

    # -- transport ---------------------------------------------------------

    def _request(
        self, method: str, path: str, payload: dict | None = None
    ) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                **({"Content-Type": "application/json"} if body else {}),
                **(
                    {"X-Service-Token": self.service_token}
                    if self.service_token
                    else {}
                ),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            message = f"HTTP {exc.code}"
            try:
                document = json.loads(raw.decode("utf-8"))
                message = str(document.get("error") or message)
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass
            raise AccountServiceError(
                without_address(message, self.url),
                status=exc.code,
                url=self.url,
            )
        except urllib.error.URLError as exc:
            raise ServiceUnreachableError(
                without_address(
                    f"cannot reach the account service ({exc.reason})",
                    self.url,
                ),
                url=self.url,
            )
        except (TimeoutError, socket.timeout):
            raise ServiceUnreachableError(
                "the account service did not respond in time", url=self.url
            )
        except OSError as exc:
            raise ServiceUnreachableError(
                without_address(
                    f"cannot reach the account service ({exc})", self.url
                ),
                url=self.url,
            )
        if not raw:
            return {}
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccountServiceError(
                f"the service returned a malformed response: {exc}"
            )
        if not isinstance(document, dict):
            raise AccountServiceError(
                "the service returned an unexpected response"
            )
        return document

    # -- endpoints ---------------------------------------------------------

    def status(self) -> dict:
        return self._request("GET", "/api/status")

    def characters(self) -> list[dict[str, Any]]:
        document = self._request("GET", "/api/characters")
        return list(document.get("characters", []))

    def register(self, username: str, password: str) -> dict:
        document = self._request(
            "POST",
            "/api/register",
            {"username": username, "password": password},
        )
        return document.get("account", {})

    def login(self, username: str, password: str) -> Session:
        document = self._request(
            "POST", "/api/login", {"username": username, "password": password}
        )
        return Session.from_response(document)

    def refresh(self, session_id: str) -> Session:
        document = self._request(
            "POST", "/api/refresh", {"session_id": session_id}
        )
        return Session.from_response(document)

    def logout(self, session_id: str) -> bool:
        document = self._request(
            "POST", "/api/logout", {"session_id": session_id}
        )
        return bool(document.get("revoked"))

    def change_password(
        self, username: str, password: str, new_password: str
    ) -> dict:
        document = self._request(
            "POST",
            "/api/password",
            {
                "username": username,
                "password": password,
                "new_password": new_password,
            },
        )
        return document.get("account", {})

    def validate(self, session_id: str) -> dict | None:
        document = self._request(
            "POST", "/api/validate", {"session_id": session_id}
        )
        if not document.get("valid"):
            return None
        return document.get("session", {})
