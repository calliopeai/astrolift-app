"""The factory gate's GitHub App (#2589).

The Calliope AI factory's quarantine gate trusts an author only when the
membership API reports an active organization owner, and the Actions job token
cannot be granted the organization Members permission. Astrolift therefore
holds a GitHub App with that one permission, read-only, registered on one
GitHub organization through the existing manifest flow
(``auth1.scm_app_manifest`` with ``purpose=factory_gate``), and mints its
installation token for the API tokens listed in FACTORY_GATE_TOKEN_CALLERS.

The App is a ``SourceConnection`` of its own kind, in the columns the source
App uses: the App id in ``oauth_client_id``, the Client ID in
``app_client_id``, the GitHub organization in ``account_login``, the
installation id, and the private key encrypted at rest in
``secret_ciphertext``. Nothing here returns the key, and no audit event
carries a token.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import logging
import uuid
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from django.conf import settings

from astrolift_scm.models import SourceConnection
from core.mutations import AuditEntry, emit_audit
from core.secrets import encrypt_at_rest

log = logging.getLogger(__name__)

PURPOSE = "factory_gate"
KIND = SourceConnection.Kind.GITHUB_FACTORY_GATE_APP.value
# The App's whole permission set: the manifest asks for exactly this, and
# every token is minted with exactly this.
PERMISSIONS: Mapping[str, str] = MappingProxyType({"members": "read"})

REGISTER = "scm.factory_gate.register"
INSTALL = "scm.factory_gate.install"
ROTATE = "scm.factory_gate.rotate"
TOKEN_MINT = "scm.factory_gate.token_mint"


class State(enum.StrEnum):
    NOT_REGISTERED = "NOT_REGISTERED"
    REGISTERED = "REGISTERED"
    INSTALLED = "INSTALLED"
    MISSING_KEY = "MISSING_KEY"


def current_app(organization_id: int | None) -> SourceConnection | None:
    """The org's factory gate App: its newest live row that finished the
    manifest exchange. A pending row from an abandoned registration has no App
    id and does not count."""
    if organization_id is None:
        return None
    return (
        SourceConnection.objects.filter(organization_id=organization_id, kind=KIND, deleted_at__isnull=True)
        .exclude(oauth_client_id="")
        .order_by("-created_at", "-pk")
        .first()
    )


def state_of(app: SourceConnection | None) -> State:
    if app is None:
        return State.NOT_REGISTERED
    if not bytes(app.secret_ciphertext or b""):
        return State.MISSING_KEY
    if not app.installation_id:
        return State.REGISTERED
    return State.INSTALLED


def audit(
    action: str,
    *,
    actor_user_id: int | None,
    organization_id: int | None,
    app_id: str,
    decision: str = "ALLOW",
    error_code: str | None = None,
    error_message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    emit_audit(
        AuditEntry(
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            action=action,
            decision=decision,
            target_kind="github_app",
            target_id=app_id or None,
            duration_ms=0,
            permissions=(),
            error_code=error_code,
            error_message=error_message,
            extra={"purpose": PURPOSE, "app_id": app_id, **(extra or {})},
        )
    )


def _signed_jwt(app: SourceConnection, pem: bytes | None = None) -> str:
    from astrolift_scm.providers import github_app as gh_app

    issuer = app.app_client_id or app.oauth_client_id
    return gh_app._mint_jwt(issuer, pem if pem is not None else gh_app._decrypt_pem(app))


def record_installation(
    app: SourceConnection, installation_id: str, *, actor_user_id: int | None
) -> str | None:
    """Attach ``installation_id`` once GitHub, asked with the App's own JWT,
    confirms it is this App's installation on the App's organization. The id
    arrives in the browser's setup redirect, so it is checked, not trusted.
    Returns an error code for that redirect, or None."""
    from astrolift_scm.providers import github_app as gh_app

    try:
        jwt_token = _signed_jwt(app)
    except Exception:  # noqa: BLE001 -- an unusable key is reported, never raised
        return "factory_gate_key_unusable"
    installation, err = gh_app.fetch_installation(gh_app._api_base(app), jwt_token, installation_id)
    if err is not None:
        log.warning("factory gate installation %s not confirmed: %s", installation_id, err.message)
        return "factory_gate_install_unconfirmed"
    account = installation.get("account") or {}
    installed_on = account.get("login") or ""
    if (
        str(installation.get("app_id") or "") != app.oauth_client_id
        or installed_on.lower() != app.account_login.lower()
        or installation.get("target_type") != "Organization"
    ):
        audit(
            INSTALL,
            actor_user_id=actor_user_id,
            organization_id=app.organization_id,
            app_id=app.oauth_client_id,
            decision="DENY",
            error_code="WRONG_INSTALLATION",
            error_message=f"installation {installation_id} is not this App's, on {app.account_login}",
            extra={"installation_id": installation_id, "installed_on": installed_on},
        )
        return "factory_gate_install_wrong_account"
    app.installation_id = installation_id[:64]
    app.is_active = True
    app.is_orphaned = False
    app.orphaned_at = None
    app.orphaned_reason = ""
    app.save()
    audit(
        INSTALL,
        actor_user_id=actor_user_id,
        organization_id=app.organization_id,
        app_id=app.oauth_client_id,
        extra={"installation_id": app.installation_id, "owner_login": app.account_login},
    )
    return None


def rotate_key(app: SourceConnection, pem: str) -> tuple[str, str] | None:
    """Store ``pem`` in place of the App's key once it proves to sign for this
    App: GitHub answers GET /app, with a JWT made from it, as this App. GitHub
    has no API that issues App keys, so the operator generates one in the
    App's settings and pastes it. Returns (error code, message), or None once
    the key is stored."""
    from astrolift_scm.providers import github_app as gh_app

    pem_bytes = pem.strip().encode("utf-8")
    try:
        jwt_token = _signed_jwt(app, pem_bytes)
    except Exception:  # noqa: BLE001 -- any load or sign failure is a paste error
        return "VALIDATION", "privateKeyPem is not a valid RSA private key"
    meta, err = gh_app.fetch_app_metadata(gh_app._api_base(app), jwt_token)
    if err is not None:
        return "PRECONDITION", f"GitHub did not accept this key for App {app.oauth_client_id}: {err.message}"
    if str(meta.get("id") or "") != app.oauth_client_id:
        return "PRECONDITION", f"this key signs for App {meta.get('id')}, not App {app.oauth_client_id}"
    encrypted = encrypt_at_rest(pem_bytes)
    app.secret_backend_kind = encrypted.backend_kind
    app.secret_ciphertext = encrypted.backend_ref
    app.save(update_fields=["secret_backend_kind", "secret_ciphertext", "updated_at", "version"])
    return None


@dataclasses.dataclass(frozen=True, slots=True)
class MintedToken:
    token: str
    expires_at: dt.datetime
    permissions: dict[str, str]


class MintRefused(Exception):
    def __init__(self, code: str, message: str, *, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# A request body that is not JSON, or not an object: refused like any other
# request for something but Members read.
MALFORMED = object()

_NOT_READY = {
    State.NOT_REGISTERED: ("NOT_REGISTERED", "no factory gate App is registered for this organization"),
    State.REGISTERED: ("NOT_INSTALLED", "the factory gate App is registered but not installed"),
    State.MISSING_KEY: ("MISSING_KEY", "the factory gate App has no stored key; rotate it"),
}


def allowed_callers() -> frozenset[str]:
    callers = set()
    for raw in getattr(settings, "FACTORY_GATE_TOKEN_CALLERS", ()) or ():
        try:
            callers.add(str(uuid.UUID(str(raw).strip())))
        except ValueError:
            log.warning("FACTORY_GATE_TOKEN_CALLERS holds an entry that is not an API token GUID; ignored")
    return frozenset(callers)


def mint_token(api_token, requested: object = None) -> MintedToken:
    """A short-lived installation token for the factory gate App of the API
    token's own organization, carrying organization Members read and nothing
    else. Refused unless the API token is listed in FACTORY_GATE_TOKEN_CALLERS
    and asks for Members read or for nothing. Every attempt is audited with
    the caller's token id; the token itself never is."""
    from astrolift_scm.providers import github_app as gh_app

    caller = str(api_token.guid)
    organization_id = api_token.organization_id
    app = current_app(organization_id)
    app_id = app.oauth_client_id if app is not None else ""

    def refused(code: str, message: str, status: int, decision: str = "DENY", **extra: Any) -> MintRefused:
        audit(
            TOKEN_MINT,
            actor_user_id=api_token.user_id,
            organization_id=organization_id,
            app_id=app_id,
            decision=decision,
            error_code=code,
            error_message=message,
            extra={"api_token_id": caller, **extra},
        )
        return MintRefused(code, message, status=status)

    if caller not in allowed_callers():
        raise refused("CALLER_NOT_ALLOWED", "this API token may not mint the factory gate token", 403)
    wanted = dict(PERMISSIONS)
    if requested is not None and (not isinstance(requested, Mapping) or dict(requested) != wanted):
        shown = sorted(str(name) for name in requested) if isinstance(requested, Mapping) else []
        raise refused(
            "PERMISSION_NOT_ALLOWED",
            "the factory gate token carries organization Members read and nothing else",
            403,
            requested=shown,
        )
    state = state_of(app)
    if state is not State.INSTALLED:
        code, message = _NOT_READY[state]
        raise refused(code, message, 409, "ALLOW")
    assert app is not None  # narrowed by the state check
    try:
        jwt_token = _signed_jwt(app)
    except Exception:  # noqa: BLE001 -- an unusable key is reported, never raised
        raise refused(
            "KEY_UNUSABLE", "the stored key cannot sign for the App; rotate it", 409, "ALLOW"
        ) from None
    try:
        minted = gh_app.request_installation_token(
            gh_app._api_base(app), jwt_token, app.installation_id, permissions=wanted
        )
    except gh_app.GithubAppError as exc:
        raise refused("GITHUB_ERROR", f"GitHub refused the token: {exc.message}", 502, "ALLOW") from None
    if minted.permissions != wanted:
        raise refused(
            "SCOPE_MISMATCH",
            f"GitHub minted a token with {sorted(minted.permissions.items())}; only members: read is allowed",
            502,
            granted=minted.permissions,
        )
    expires_at = dt.datetime.fromtimestamp(minted.expires_at, tz=dt.UTC)
    audit(
        TOKEN_MINT,
        actor_user_id=api_token.user_id,
        organization_id=organization_id,
        app_id=app_id,
        extra={
            "api_token_id": caller,
            "installation_id": app.installation_id,
            "permissions": wanted,
            "expires_at": expires_at.isoformat(),
        },
    )
    return MintedToken(token=minted.token, expires_at=expires_at, permissions=wanted)
