"""Whether a source connection can be used now (#2601).

Providers > Source showed a GitHub App connection as active from its stored
``is_active`` flag, so an App with no installation on its account read
active while every repository read through it failed. A GitHub App
installation's status now comes from GitHub: ``github_app.installation_token``
mints a token, first finding a missing installation id by account (#2609),
and orphans the row when GitHub answers 404. Other kinds answer from their
stored flags.

The check is bounded. It runs only when a status field is selected, only for
a caller holding scm.read in the connection's organization, at most
``CHECKS_PER_RESPONSE`` times per GraphQL response, and none starts once
``CHECK_BUDGET_SECONDS`` have passed since the first; those rows read
UNVERIFIED until a later load. A verdict is reused for ``CACHE_SECONDS`` while
the row's version is unchanged.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
import time
from typing import Any

from django.core.cache import cache

from astrolift_scm.scopes import scm_org_scope
from core.permissions import Permission, PermissionDenied, check_permission
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)

CHECKS_PER_RESPONSE = 10
CHECK_BUDGET_SECONDS = 5.0
CACHE_SECONDS = 60

_APP_KIND = "github_app_install"
_INSTALL_STEP = "Providers → Source → Connect to GitHub → Install Astrolift on a GitHub org"
_RELINK_STEP = "Install on GitHub in this row's actions"


class Status(enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    INSTALLATION_MISSING = "installation_missing"
    UNVERIFIED = "unverified"


@dataclasses.dataclass(frozen=True)
class Verdict:
    status: Status
    detail: str = ""


NOT_CHECKED = Verdict(
    Status.UNVERIFIED,
    f"Not checked on this load: one load checks at most {CHECKS_PER_RESPONSE} GitHub Apps, "
    f"for up to {CHECK_BUDGET_SECONDS:g} seconds. Reload to check it.",
)
NOT_PERMITTED = Verdict(
    Status.UNVERIFIED,
    "Not checked: checking a connection against GitHub needs scm.read in its organization.",
)


def _on(login: str) -> str:
    return f" on {login}" if login else ""


def can_relink(row: Any) -> bool:
    """Whether the row's own Install on GitHub action applies: a registered App
    on github.com with an account, whose install flow can store the
    installation back on this row (auth1.scm_app_manifest). The Source page
    offers the action by the same rule."""
    from astrolift_scm.providers import github_app

    return (
        row.kind == _APP_KIND
        and bool(row.oauth_client_id and row.account_login)
        and github_app._api_base(row) == github_app.GITHUB_API_DEFAULT
    )


def _install_hint(row: Any) -> str:
    login = row.account_login or ""
    if can_relink(row):
        return f"Use {_RELINK_STEP}, and pick {login} on GitHub."
    return f"Install it under {_INSTALL_STEP}" + (f", with {login} as the org." if login else ".")


def _gone(row: Any) -> Verdict:
    return Verdict(
        Status.INSTALLATION_MISSING,
        f"GitHub no longer has this App's installation{_on(row.account_login or '')}. {_install_hint(row)}",
    )


def stored_verdict(row: Any) -> Verdict | None:
    """What the stored flags settle without asking GitHub; None when a GitHub
    App installation has to be checked."""
    login = row.account_login or ""
    if not row.is_active:
        if row.kind == _APP_KIND and not row.installation_id:
            return Verdict(
                Status.INSTALLATION_MISSING,
                f"The install{_on(login)} never finished, so there is no installation. {_install_hint(row)}",
            )
        return Verdict(Status.INACTIVE, "Inactive in Astrolift.")
    if row.is_orphaned:
        if row.kind == _APP_KIND:
            return _gone(row)
        return Verdict(Status.INACTIVE, f"Orphaned: {row.orphaned_reason or 'no reason recorded'}.")
    if row.kind != _APP_KIND:
        return Verdict(Status.ACTIVE)
    return None


def check_installation(row: Any) -> Verdict:
    """Mint an installation token for a GitHub App connection and read GitHub's answer."""
    from astrolift_scm.providers.github_app import GithubAppError, installation_token

    login = row.account_login or ""
    try:
        installation_token(row)
    except GithubAppError as exc:
        if exc.code == "INCOMPLETE_CONFIG" and not (row.app_client_id or row.oauth_client_id):
            return Verdict(
                Status.INACTIVE,
                "No App ID or Client ID is recorded for this connection, so it cannot sign in to GitHub.",
            )
        if exc.code == "INCOMPLETE_CONFIG":
            return Verdict(
                Status.INSTALLATION_MISSING,
                f"GitHub has no installation of this App{_on(login)}. {_install_hint(row)}",
            )
        if exc.http_status == 404:
            # installation_token has orphaned the row: the recorded installation is gone.
            return _gone(row)
        if exc.http_status in (401, 403):
            return Verdict(
                Status.INACTIVE,
                f"GitHub refused this App's credentials (HTTP {exc.http_status}). Check that the App "
                "exists and is not suspended, and that its private key is current.",
            )
        if exc.http_status is not None:
            return Verdict(
                Status.UNVERIFIED, f"GitHub answered HTTP {exc.http_status} to the installation check."
            )
        return Verdict(Status.UNVERIFIED, f"{exc.message}.")
    except Exception as exc:  # noqa: BLE001 - an unreadable key or a raw socket error: unknown, not broken
        log.warning("installation check failed for connection %s", row.guid, exc_info=True)
        return Verdict(Status.UNVERIFIED, f"The installation check failed ({type(exc).__name__}).")
    return Verdict(Status.ACTIVE)


def _cache_key(row: Any) -> str:
    return f"astrolift-scm-connection-status-v1:{row.guid}:{row.version}"


def _caller_may_check(organization_id: int) -> bool:
    """The check signs in to GitHub with the organization's App key, so only a
    caller holding scm.read in that organization, as its tenant, runs it."""
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id != organization_id:
        return False
    try:
        check_permission(Permission.SCM_READ, scope=scm_org_scope(Permission.SCM_READ)({}))
    except PermissionDenied:
        return False
    return True


class ResponseStatus:
    """The connection statuses one GraphQL response needs, each settled once."""

    def __init__(self) -> None:
        self._verdicts: dict[int, Verdict] = {}
        self._allowed: dict[int, bool] = {}
        self._checks = 0
        self._first_check_at: float | None = None

    def verdict(self, row: Any) -> Verdict:
        if row.pk not in self._verdicts:
            self._verdicts[row.pk] = self._settle(row)
        return self._verdicts[row.pk]

    def _settle(self, row: Any) -> Verdict:
        stored = stored_verdict(row)
        if stored is not None:
            return stored
        if row.organization_id not in self._allowed:
            self._allowed[row.organization_id] = _caller_may_check(row.organization_id)
        if not self._allowed[row.organization_id]:
            return NOT_PERMITTED
        cached = cache.get(_cache_key(row))
        if cached is not None:
            return Verdict(Status(cached[0]), cached[1])
        now = time.monotonic()
        if self._first_check_at is None:
            self._first_check_at = now
        if self._checks >= CHECKS_PER_RESPONSE or now - self._first_check_at >= CHECK_BUDGET_SECONDS:
            return NOT_CHECKED
        self._checks += 1
        verdict = check_installation(row)
        # Keyed after the check, which may have saved the row (a found
        # installation id, an orphan flag) and so bumped its version.
        cache.set(_cache_key(row), (verdict.status.value, verdict.detail), CACHE_SECONDS)
        return verdict


def response_status(info: Any) -> ResponseStatus:
    """The response's shared ``ResponseStatus``; a private one without a context."""
    context = getattr(info, "context", None) if info is not None else None
    found = getattr(context, "_astrolift_connection_status", None)
    if isinstance(found, ResponseStatus):
        return found
    created = ResponseStatus()
    if context is not None:
        try:
            context._astrolift_connection_status = created
        except (AttributeError, TypeError):
            pass
    return created
