"""Google Cloud Identity Platform as the cluster's edge identity provider (#2135).

Acts on one project, the install's own: central auth must sign in through
``https://securetoken.google.com/<project>`` for the project the cluster runs
in. Users go through the Identity Toolkit admin API with the control plane's
Application Default Credentials (``roles/identitytoolkit.admin`` on that
project).

Identity Platform has users but no groups, so group reads are empty and group
writes are refused with that reason (Leo, 2026-10-11).
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

import datetime as dt

from _sdk.cloud_credentials import CredentialedConfig
from _sdk.identity_users import IdentityUser, IdentityUsersError

_API = "https://identitytoolkit.googleapis.com/v1"
# https://securetoken.google.com/<project>[/.well-known/openid-configuration]
_ISSUER = re.compile(r"^https://securetoken\.google\.com/([a-z][a-z0-9-]{4,28}[a-z0-9])(?:/|$)")
_PAGE = 60
_NO_GROUPS = "Google Cloud Identity Platform has no groups; manage access with roles in Astrolift instead."


def identity_platform_project_from_issuer(url: str) -> str | None:
    """The project id when ``url`` is an Identity Platform issuer or discovery URL."""
    match = _ISSUER.match((url or "").strip())
    return match.group(1) if match else None


@dataclass(frozen=True)
class IdentityPlatformUsersConfig(CredentialedConfig):
    project_id: str


def _created(value: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(int(value) / 1000, tz=dt.UTC) if value else None
    except (TypeError, ValueError):
        return None


class IdentityPlatformUsersDriver:
    provider = "Google Cloud Identity Platform"

    def __init__(
        self,
        config: IdentityPlatformUsersConfig,
        *,
        token: Callable[[], str] | None = None,
        opener: Any = None,
        timeout: float = 20,
    ) -> None:
        self._config = config
        self._token_source = token
        self._opener = opener or build_opener()
        self._timeout = timeout
        self._before_write: Callable[[], None] | None = None

    @contextmanager
    def guard_writes(self, before_write: Callable[[], None]) -> Iterator[None]:
        previous = self._before_write
        self._before_write = before_write
        try:
            yield
        finally:
            self._before_write = previous

    @property
    def pool_id(self) -> str:
        return self._config.project_id

    def verify_user(self, *, username: str, expected_user_id: str) -> None:
        if not expected_user_id:
            raise IdentityUsersError("The reviewed provider user identity is unknown.")
        users = self._call("accounts:lookup", {"localId": [expected_user_id]}, write=False).get("users") or []
        if len(users) != 1 or str(users[0].get("email") or "").lower() != username.lower():
            raise IdentityUsersError("The reviewed provider user has changed or its identity is unknown.")

    # ---- plumbing ------------------------------------------------------

    def _token(self) -> str:
        if self._token_source is None:
            import google.auth
            from google.auth.transport.requests import Request as GoogleRequest

            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])

            def token() -> str:
                if not credentials.valid:
                    credentials.refresh(GoogleRequest())
                return str(credentials.token)

            self._token_source = token
        return self._token_source()

    def _call(self, method: str, body: dict[str, Any], *, write: bool = True, secret: str = "") -> dict[str, Any]:
        if write and self._before_write is not None:
            self._before_write()
        request = Request(
            f"{_API}/projects/{self._config.project_id}/{method}",
            data=json.dumps(body, separators=(",", ":")).encode(),
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:2000]
            try:
                error = json.loads(detail).get("error") or {}
                message = str(error.get("message") or exc.code)
            except (ValueError, AttributeError):
                message = detail or str(exc.code)
            # Never echo a password back, even inside a provider message.
            if secret:
                message = message.replace(secret, "***")
            raise IdentityUsersError(f"{exc.code}: {message}") from None
        except URLError as exc:
            raise IdentityUsersError(f"Identity Toolkit unreachable: {exc.reason}") from None
        parsed = json.loads(payload) if payload else {}
        if not isinstance(parsed, dict):
            raise IdentityUsersError("Identity Toolkit returned a non-object body.")
        return parsed

    def _local_id(self, username: str) -> str:
        users = self._call("accounts:lookup", {"email": [username]}, write=False).get("users") or []
        if len(users) != 1 or not users[0].get("localId"):
            raise IdentityUsersError(f"No Identity Platform user {username!r}.")
        return str(users[0]["localId"])

    @staticmethod
    def _user(raw: dict[str, Any]) -> IdentityUser:
        return IdentityUser(
            username=str(raw.get("email") or ""),
            email=str(raw.get("email") or ""),
            enabled=not bool(raw.get("disabled")),
            status="VERIFIED" if raw.get("emailVerified") else "UNVERIFIED",
            created_at=_created(raw.get("createdAt")),
            groups=(),
            provider_user_id=str(raw.get("localId") or "") or None,
        )

    # ---- the contract ---------------------------------------------------

    def list_users(self, *, search: str = "", limit: int = 60) -> list[IdentityUser]:
        cap = max(1, min(limit, _PAGE))
        term = search.strip().lower()
        if term and "@" in term and " " not in term:
            # An exact email is answered by the server; a prefix is not supported.
            rows = self._call("accounts:lookup", {"email": [term]}, write=False).get("users") or []
        else:
            body: dict[str, Any] = {"returnUserInfo": True, "limit": str(500 if term else cap)}
            rows = self._call("accounts:query", body, write=False).get("userInfo") or []
            if term:
                rows = [u for u in rows if str(u.get("email") or "").lower().startswith(term)]
        return [self._user(u) for u in rows[:cap]]

    def list_groups(self) -> list[str]:
        return []

    def create_user(
        self,
        *,
        email: str,
        password: str | None,
        permanent: bool,
        groups: tuple[str, ...] = (),
    ) -> IdentityUser:
        if groups:
            raise IdentityUsersError(_NO_GROUPS)
        body: dict[str, Any] = {"email": email, "emailVerified": bool(password)}
        if password:
            body["password"] = password
        created = self._call("accounts", body, secret=password or "")
        if not password:
            # No generated password: Identity Platform sends a set-password email.
            if self._before_write is not None:
                self.verify_user(username=email, expected_user_id=str(created.get("localId") or ""))
            self._call("accounts:sendOobCode", {"requestType": "PASSWORD_RESET", "email": email})
        return self._user({**created, "email": email, "emailVerified": bool(password)})

    def set_password(self, *, username: str, password: str, permanent: bool) -> None:
        # Identity Platform cannot force a change at next sign-in; ``permanent``
        # has no separate meaning here.
        self._call("accounts:update", {"localId": self._local_id(username), "password": password}, secret=password)

    def reset_password(self, *, username: str) -> None:
        self._local_id(username)
        self._call("accounts:sendOobCode", {"requestType": "PASSWORD_RESET", "email": username})

    def set_enabled(self, *, username: str, enabled: bool) -> None:
        self._call("accounts:update", {"localId": self._local_id(username), "disableUser": not enabled})

    def delete_user(self, *, username: str) -> None:
        self._call("accounts:delete", {"localId": self._local_id(username)})

    def create_group(self, *, name: str, description: str = "") -> None:
        raise IdentityUsersError(_NO_GROUPS)

    def add_to_group(self, *, username: str, group: str) -> None:
        raise IdentityUsersError(_NO_GROUPS)

    def remove_from_group(self, *, username: str, group: str) -> None:
        raise IdentityUsersError(_NO_GROUPS)
