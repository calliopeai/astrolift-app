"""Microsoft Entra ID as the cluster's edge identity provider (#2134).

Acts on one tenant, the install's own: the tenant central auth signs in
through must be the tenant the cluster runs in. Users and groups go through
Microsoft Graph with the control plane's ambient Azure identity, which needs
the Graph application permissions User.ReadWrite.All, Group.ReadWrite.All and
User.Invite.All in that tenant.

Graph differs from Cognito in two places, and both are refused or mapped
plainly rather than imitated:

- A user created without a password is invited (B2B), so Entra sends the
  invitation; there is no provider-generated temporary password.
- Entra has no provider-sent password reset; an operator sets a temporary
  password instead.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

from _sdk.cloud_credentials import CredentialedConfig
from _sdk.identity_users import IdentityUser, IdentityUsersError

_GRAPH = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_TENANT = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
# https://login.microsoftonline.com/<tenant>/v2.0[/.well-known/openid-configuration]
# https://sts.windows.net/<tenant>/
_ENTRA_ISSUER = re.compile(rf"^https://(?:login\.microsoftonline\.com|sts\.windows\.net)/{_TENANT}(?:/|$)")
_PAGE = 60


def entra_tenant_from_issuer(url: str) -> str | None:
    """The tenant id when ``url`` is an Entra ID issuer or discovery URL."""
    match = _ENTRA_ISSUER.match((url or "").strip())
    return match.group(1).lower() if match else None


@dataclass(frozen=True)
class EntraUsersConfig(CredentialedConfig):
    tenant_id: str


def _created(value: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _odata(value: str) -> str:
    return value.replace("'", "''")


class EntraIdentityUsersDriver:
    provider = "Microsoft Entra ID"

    def __init__(
        self,
        config: EntraUsersConfig,
        *,
        credential: Any = None,
        opener: Any = None,
        timeout: float = 20,
    ) -> None:
        self._config = config
        self._credential = credential
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
        return self._config.tenant_id

    def verify_user(self, *, username: str, expected_user_id: str) -> None:
        if not expected_user_id:
            raise IdentityUsersError("The reviewed provider user identity is unknown.")
        raw = self._request("GET", f"/users/{self._key(username)}?$select=id,userPrincipalName")
        if raw.get("id") != expected_user_id or str(raw.get("userPrincipalName") or "").lower() != username.lower():
            raise IdentityUsersError("The reviewed provider user has changed or its identity is unknown.")

    # ---- plumbing ------------------------------------------------------

    def _token(self) -> str:
        if self._credential is None:
            from azure.identity import DefaultAzureCredential

            self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        return self._credential.get_token(_GRAPH_SCOPE, tenant_id=self._config.tenant_id).token

    @staticmethod
    def _key(username: str) -> str:
        return quote(username, safe="@")

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, secret: str = ""
    ) -> dict[str, Any]:
        if method != "GET" and self._before_write is not None:
            self._before_write()
        request = Request(
            f"{_GRAPH}{path}",
            data=json.dumps(body, separators=(",", ":")).encode() if body is not None else None,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
                "ConsistencyLevel": "eventual",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:2000]
            try:
                error = json.loads(detail).get("error") or {}
                code, message = str(error.get("code") or exc.code), str(error.get("message") or "")
            except (ValueError, AttributeError):
                code, message = str(exc.code), detail
            # Never echo a password back, even inside a provider message.
            if secret:
                message = message.replace(secret, "***")
            raise IdentityUsersError(f"{code}: {message}") from None
        except URLError as exc:
            raise IdentityUsersError(f"Microsoft Graph unreachable: {exc.reason}") from None
        if not payload:
            return {}
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            raise IdentityUsersError("Microsoft Graph returned a non-object body.")
        return parsed

    def _user_id(self, username: str) -> str:
        user_id = str(self._request("GET", f"/users/{self._key(username)}?$select=id").get("id") or "")
        if not user_id:
            raise IdentityUsersError(f"No Entra ID user {username!r}.")
        return user_id

    def _group_id(self, name: str) -> str:
        query = urlencode({"$filter": f"displayName eq '{_odata(name)}'", "$select": "id"})
        rows = self._request("GET", f"/groups?{query}").get("value") or []
        if len(rows) != 1:
            raise IdentityUsersError(
                f"No Entra ID group named {name!r}." if not rows else f"More than one Entra ID group is named {name!r}."
            )
        return str(rows[0]["id"])

    def _groups_of(self, user_id: str) -> tuple[str, ...]:
        query = urlencode({"$select": "displayName", "$top": _PAGE})
        rows = self._request("GET", f"/users/{user_id}/memberOf/microsoft.graph.group?{query}").get("value") or []
        return tuple(sorted(str(g.get("displayName") or "") for g in rows if g.get("displayName")))

    @staticmethod
    def _user(raw: dict[str, Any], groups: tuple[str, ...] = ()) -> IdentityUser:
        return IdentityUser(
            username=str(raw.get("userPrincipalName") or ""),
            email=str(raw.get("mail") or raw.get("userPrincipalName") or ""),
            enabled=bool(raw.get("accountEnabled", True)),
            status=str(raw.get("externalUserState") or raw.get("userType") or "Member"),
            created_at=_created(raw.get("createdDateTime")),
            groups=groups,
            provider_user_id=str(raw.get("id") or "") or None,
        )

    # ---- the contract ---------------------------------------------------

    def list_users(self, *, search: str = "", limit: int = 60) -> list[IdentityUser]:
        params = {
            "$top": max(1, min(limit, _PAGE)),
            "$select": "id,userPrincipalName,mail,accountEnabled,userType,externalUserState,createdDateTime",
        }
        term = search.strip()
        if term:
            params["$filter"] = f"startswith(mail,'{_odata(term)}') or startswith(userPrincipalName,'{_odata(term)}')"
            params["$count"] = "true"
        rows = self._request("GET", f"/users?{urlencode(params)}").get("value") or []
        return [self._user(u, self._groups_of(str(u["id"]))) for u in rows]

    def list_groups(self) -> list[str]:
        query = urlencode({"$filter": "securityEnabled eq true", "$select": "displayName", "$top": _PAGE})
        rows = self._request("GET", f"/groups?{query}").get("value") or []
        return sorted(str(g.get("displayName") or "") for g in rows if g.get("displayName"))

    def create_user(
        self,
        *,
        email: str,
        password: str | None,
        permanent: bool,
        groups: tuple[str, ...] = (),
    ) -> IdentityUser:
        if password:
            created = self._request(
                "POST",
                "/users",
                {
                    "accountEnabled": True,
                    "displayName": email,
                    "mailNickname": re.sub(r"[^A-Za-z0-9]", "", email.split("@", 1)[0])[:64] or "user",
                    "userPrincipalName": email,
                    "passwordProfile": {"password": password, "forceChangePasswordNextSignIn": not permanent},
                },
                secret=password,
            )
        else:
            # Entra has no generated temporary password; the invitation is the
            # provider-sent path, as a guest of the tenant.
            invited = self._request(
                "POST",
                "/invitations",
                {
                    "invitedUserEmailAddress": email,
                    "inviteRedirectUrl": "https://myapps.microsoft.com",
                    "sendInvitationMessage": True,
                },
            )
            created = {
                **(invited.get("invitedUser") or {}),
                "mail": email,
                "externalUserState": "PendingAcceptance",
                "accountEnabled": True,
            }
            created.setdefault("userPrincipalName", email)
        user_id = str(created.get("id") or "")
        username = str(created.get("userPrincipalName") or email)
        for group in groups:
            if self._before_write is not None:
                self.verify_user(username=username, expected_user_id=user_id)
            self.add_to_group(username=username, group=group)
        return self._user(created, tuple(sorted(groups)))

    def set_password(self, *, username: str, password: str, permanent: bool) -> None:
        self._request(
            "PATCH",
            f"/users/{self._key(username)}",
            {"passwordProfile": {"password": password, "forceChangePasswordNextSignIn": not permanent}},
            secret=password,
        )

    def reset_password(self, *, username: str) -> None:
        raise IdentityUsersError(
            "Microsoft Entra ID has no provider-sent password reset; set a temporary password instead."
        )

    def set_enabled(self, *, username: str, enabled: bool) -> None:
        self._request("PATCH", f"/users/{self._key(username)}", {"accountEnabled": enabled})

    def delete_user(self, *, username: str) -> None:
        self._request("DELETE", f"/users/{self._key(username)}")

    def create_group(self, *, name: str, description: str = "") -> None:
        body: dict[str, Any] = {
            "displayName": name,
            "mailEnabled": False,
            "securityEnabled": True,
            "mailNickname": re.sub(r"[^A-Za-z0-9]", "", name)[:64] or "group",
        }
        if description:
            body["description"] = description
        self._request("POST", "/groups", body)

    def add_to_group(self, *, username: str, group: str) -> None:
        user_id, group_id = self._user_id(username), self._group_id(group)
        self._request(
            "POST",
            f"/groups/{group_id}/members/$ref",
            {"@odata.id": f"{_GRAPH}/directoryObjects/{user_id}"},
        )

    def remove_from_group(self, *, username: str, group: str) -> None:
        user_id, group_id = self._user_id(username), self._group_id(group)
        self._request("DELETE", f"/groups/{group_id}/members/{user_id}/$ref")
