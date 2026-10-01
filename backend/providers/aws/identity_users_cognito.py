"""Amazon Cognito as the cluster's edge identity provider (#2131).

Acts on one user pool, the install's own, named by the cluster's central auth
config. The control plane's role is granted the admin user and group actions
on that pool only.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

from _sdk.cloud_credentials import CredentialedConfig
from _sdk.identity_users import IdentityUser, IdentityUsersError
from aws.session import aws_client

# https://cognito-idp.<region>.amazonaws.com/<region>_<id>[/.well-known/openid-configuration]
_COGNITO_ISSUER = re.compile(r"^https://cognito-idp\.([a-z0-9-]+)\.amazonaws\.com/([a-z0-9-]+_[A-Za-z0-9]+)")


def cognito_pool_from_issuer(url: str) -> tuple[str, str] | None:
    """``(region, pool_id)`` when ``url`` is a Cognito issuer or discovery URL."""
    match = _COGNITO_ISSUER.match((url or "").strip())
    if not match:
        return None
    return match.group(1), match.group(2)


@dataclass(frozen=True)
class CognitoUsersConfig(CredentialedConfig):
    region: str
    pool_id: str


def _attr(user: dict[str, Any], name: str) -> str:
    for a in user.get("Attributes") or user.get("UserAttributes") or []:
        if a.get("Name") == name:
            return str(a.get("Value") or "")
    return ""


class CognitoIdentityUsersDriver:
    provider = "Amazon Cognito"

    def __init__(self, config: CognitoUsersConfig, client: Any = None) -> None:
        self._config = config
        self._client = client
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
        return self._config.pool_id

    def verify_user(self, *, username: str, expected_user_id: str) -> None:
        if not expected_user_id:
            raise IdentityUsersError("The reviewed provider user identity is unknown.")
        raw = self._call("admin_get_user", Username=username)
        actual = _attr(raw, "sub")
        if not expected_user_id or not actual or actual != expected_user_id or raw.get("Username") != username:
            raise IdentityUsersError("The reviewed provider user has changed or its identity is unknown.")

    # ---- plumbing ------------------------------------------------------

    def _idp(self) -> Any:
        if self._client is None:
            self._client = aws_client("cognito-idp", region=self._config.region, credential=self._config.credential)
        return self._client

    def _call(self, method: str, **kwargs: Any) -> Any:
        if self._before_write is not None and method not in {
            "list_users",
            "list_groups",
            "admin_get_user",
            "admin_list_groups_for_user",
        }:
            self._before_write()
        try:
            return getattr(self._idp(), method)(UserPoolId=self._config.pool_id, **kwargs)
        except Exception as exc:  # botocore ClientError and friends
            response = getattr(exc, "response", None) or {}
            error = response.get("Error") or {}
            message = error.get("Message") or str(exc)
            # Never echo a password back, even inside a provider message.
            if "Password" in kwargs or "TemporaryPassword" in kwargs:
                for key in ("Password", "TemporaryPassword"):
                    if kwargs.get(key):
                        message = message.replace(str(kwargs[key]), "***")
            raise IdentityUsersError(f"{error.get('Code') or type(exc).__name__}: {message}") from exc

    def _user(self, raw: dict[str, Any], groups: tuple[str, ...] = ()) -> IdentityUser:
        return IdentityUser(
            username=str(raw.get("Username") or ""),
            email=_attr(raw, "email"),
            enabled=bool(raw.get("Enabled", True)),
            status=str(raw.get("UserStatus") or ""),
            created_at=raw.get("UserCreateDate"),
            groups=groups,
            provider_user_id=_attr(raw, "sub") or None,
        )

    def _groups_of(self, username: str) -> tuple[str, ...]:
        resp = self._call("admin_list_groups_for_user", Username=username, Limit=60)
        return tuple(sorted(g["GroupName"] for g in resp.get("Groups") or []))

    # ---- the contract ---------------------------------------------------

    def list_users(self, *, search: str = "", limit: int = 60) -> list[IdentityUser]:
        kwargs: dict[str, Any] = {"Limit": max(1, min(limit, 60))}
        term = search.strip().replace('"', "")
        if term:
            kwargs["Filter"] = f'email ^= "{term}"'
        resp = self._call("list_users", **kwargs)
        return [self._user(u, self._groups_of(u["Username"])) for u in resp.get("Users") or []]

    def list_groups(self) -> list[str]:
        resp = self._call("list_groups", Limit=60)
        return sorted(g["GroupName"] for g in resp.get("Groups") or [])

    def create_user(
        self,
        *,
        email: str,
        password: str | None,
        permanent: bool,
        groups: tuple[str, ...] = (),
    ) -> IdentityUser:
        kwargs: dict[str, Any] = {
            "Username": email,
            "UserAttributes": [
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            "DesiredDeliveryMediums": ["EMAIL"],
        }
        if password:
            # The operator hands the password over themselves, so Cognito sends
            # no invitation carrying it.
            kwargs["TemporaryPassword"] = password
            kwargs["MessageAction"] = "SUPPRESS"
        created = self._call("admin_create_user", **kwargs).get("User") or {}
        username = str(created.get("Username") or email)
        subject = _attr(created, "sub")
        if password and permanent:
            self.verify_user(username=username, expected_user_id=subject)
            self.set_password(username=username, password=password, permanent=True)
        for group in groups:
            self.verify_user(username=username, expected_user_id=subject)
            self.add_to_group(username=username, group=group)
        return self._user(created, tuple(sorted(groups)))

    def set_password(self, *, username: str, password: str, permanent: bool) -> None:
        self._call("admin_set_user_password", Username=username, Password=password, Permanent=permanent)

    def reset_password(self, *, username: str) -> None:
        self._call("admin_reset_user_password", Username=username)

    def set_enabled(self, *, username: str, enabled: bool) -> None:
        self._call("admin_enable_user" if enabled else "admin_disable_user", Username=username)

    def delete_user(self, *, username: str) -> None:
        self._call("admin_delete_user", Username=username)

    def create_group(self, *, name: str, description: str = "") -> None:
        self._call("create_group", GroupName=name, Description=description)

    def add_to_group(self, *, username: str, group: str) -> None:
        self._call("admin_add_user_to_group", Username=username, GroupName=group)

    def remove_from_group(self, *, username: str, group: str) -> None:
        self._call("admin_remove_user_from_group", Username=username, GroupName=group)
