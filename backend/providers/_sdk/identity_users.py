"""The users of a cluster's edge identity provider (#2131).

Central auth signs every app on a cluster in through one identity provider,
and an operator needs to add a login without the cloud console: a demo user,
a contractor, a client's staff. This is the one shape every cloud implements
for that, the same way the other capability drivers do: Cognito on AWS first,
then Entra ID, Google Cloud Identity Platform, and Dex or Keycloak.

Passwords go in and never come back out: nothing here returns one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable
    from contextlib import AbstractContextManager


class IdentityUsersError(RuntimeError):
    """The provider refused, with its message fit to show an operator."""


@dataclass(frozen=True)
class IdentityUser:
    username: str
    email: str
    enabled: bool
    status: str
    """The provider's account state, e.g. CONFIRMED, FORCE_CHANGE_PASSWORD."""
    created_at: dt.datetime | None = None
    groups: tuple[str, ...] = field(default_factory=tuple)
    provider_user_id: str | None = None
    """Immutable provider subject, absent when the provider cannot prove it."""


class IdentityUsersDriver(Protocol):
    """One identity provider's users and groups."""

    provider: str
    """What the UI calls it, e.g. "Amazon Cognito"."""

    def list_users(self, *, search: str = "", limit: int = 60) -> list[IdentityUser]: ...

    def list_groups(self) -> list[str]: ...

    def create_user(
        self,
        *,
        email: str,
        password: str | None,
        permanent: bool,
        groups: tuple[str, ...] = (),
    ) -> IdentityUser:
        """Create a login. ``password`` None asks the provider to generate a
        temporary one and send the invitation itself."""
        ...

    def set_password(self, *, username: str, password: str, permanent: bool) -> None: ...

    def reset_password(self, *, username: str) -> None:
        """Have the provider send the user a reset, no password involved."""
        ...

    def set_enabled(self, *, username: str, enabled: bool) -> None: ...

    def delete_user(self, *, username: str) -> None: ...

    def create_group(self, *, name: str, description: str = "") -> None: ...

    def add_to_group(self, *, username: str, group: str) -> None: ...

    def remove_from_group(self, *, username: str, group: str) -> None: ...


class ReviewedIdentityUsersDriver(IdentityUsersDriver, Protocol):
    """Optional additive extension; existing drivers keep current-target semantics.

    Verification is an observation before a write, never external provider CAS.
    """

    @property
    def pool_id(self) -> str: ...

    def verify_user(self, *, username: str, expected_user_id: str) -> None: ...

    def guard_writes(self, before_write: Callable[[], None]) -> AbstractContextManager[None]: ...
