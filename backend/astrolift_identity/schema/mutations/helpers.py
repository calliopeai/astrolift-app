"""Module constants and helper functions for the mutation package."""

from __future__ import annotations

from astrolift_graphql import (
    GUID,
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_identity.models import (
    Organization,
    Project,
    Team,
)
from astrolift_identity.schema.mutations.types import (
    SoftDeleteByGuidInput,
)
from astrolift_identity.schema.types import (
    MyProfileType,
)
from core.mutations import ErrorCode
from core.tenancy import get_current_tenant

SoftDeleteOrganizationInput = SoftDeleteByGuidInput  # back-compat alias


def _actor():
    tenant = get_current_tenant()
    actor_id = tenant.actor_user_id if tenant else None
    if actor_id is None:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=actor_id).first()


def _resolve_org(guid: GUID, org_id: int | None) -> Organization | None:
    """Resolve an org by guid, but only when it is the caller's own org.

    A guid for any other org — or a missing tenant (``org_id is None``)
    — resolves to None so the caller can't reach across tenants.
    """
    return Organization.objects.filter(guid=str(guid), pk=org_id).first()


def _resolve_team(guid: GUID, org_id: int | None) -> Team | None:
    """Resolve a team by guid, scoped to the caller's org."""
    return Team.objects.filter(guid=str(guid), organization_id=org_id).first()


def _resolve_project(guid: GUID, org_id: int | None) -> Project | None:
    """Resolve a project by guid, scoped to the caller's org.

    ``organization_id`` is denormalized onto Project, so no team join is
    needed to enforce the boundary.
    """
    return Project.objects.filter(guid=str(guid), organization_id=org_id).first()


def _validate_idp_config(input) -> MutationResultType | None:
    """Per-kind config validation. Returns a failure envelope or None."""
    kind = input.kind
    if kind == "local":
        return None
    if kind in {"oidc", "okta", "azure_ad", "auth0", "google", "github"}:
        if not (input.oidc_discovery_url or input.metadata_url):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"{kind} requires oidcDiscoveryUrl",
                field="oidcDiscoveryUrl",
            )
        if not input.client_id:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"{kind} requires clientId",
                field="clientId",
            )
    if kind == "cognito":
        # Cognito needs user_pool_id + region in addition to OIDC fields
        cfg = input.config or {}
        for required in ("user_pool_id", "region"):
            if required not in cfg:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"cognito config requires {required!r}",
                    field=f"config.{required}",
                )
    if kind == "saml":
        if not input.metadata_url:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "saml requires metadataUrl",
                field="metadataUrl",
            )
    return None


def _resolve_scope_pk_in_org(scope_kind: str, scope_guid: str, org_id: int | None) -> int | None:
    """Map a ``(scope_kind, guid)`` pair to its integer PK, but only when
    the referenced row belongs to ``org_id``.

    A scope owned by another org — or an unsupported scope kind (APP is
    not grantable here, matching the prior behaviour) — resolves to None
    so a caller can't grant a role into a foreign tenant. ORG resolves
    only when the guid *is* the caller's own org.
    """
    if scope_kind == "ORG":
        row = Organization.objects.filter(guid=scope_guid, pk=org_id).first()
    elif scope_kind == "TEAM":
        row = Team.objects.filter(guid=scope_guid, organization_id=org_id).first()
    elif scope_kind == "PROJECT":
        row = Project.objects.filter(guid=scope_guid, organization_id=org_id).first()
    else:
        return None
    return row.pk if row else None


def _make_token_secret() -> tuple[str, str, str]:
    """Generate a token; return (plaintext, hash, last4)."""
    import hashlib
    import secrets

    plaintext = "alft_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(plaintext.encode()).hexdigest()
    last4 = plaintext[-4:]
    return plaintext, digest, last4


def _idp_locked_fields(user, session=None) -> list[str]:
    """Fields whose value came from the IdP at last login.

    Phase 1 logic: when the user signed in via local accounts (the
    default ``django.contrib.auth.backends.ModelBackend``), nothing
    is locked — they typed their own credentials, they own the data.
    When they came through OIDC / SAML / Cognito, ``email`` is
    locked because it's the IdP's primary identity claim and
    overwriting locally would just get clobbered on next login.

    The auth backend is read from ``session['_auth_user_backend']``
    (set by Django's ``login()``); ``user.backend`` is only present
    during the login request itself, not on subsequent requests.

    Future: per-IdP claim mapping would move this to
    IdentityProvider.locked_fields so installs that don't get email
    from their IdP can still let users edit it.
    """
    backend = ""
    if session is not None:
        backend = session.get("_auth_user_backend", "") or ""
    if not backend:
        backend = getattr(user, "backend", "") or ""
    if not backend:
        # No backend tag → can't tell, assume local accounts so the
        # user gets edit access. SSO installs will set the backend.
        return []
    if "ModelBackend" in backend:
        return []
    return ["email"]


def _my_profile_payload(user, org, locked: list[str]) -> MyProfileType:
    from astrolift_identity.models import UserPreferences

    try:
        tz = user.preferences.timezone or None
    except UserPreferences.DoesNotExist:
        tz = None

    return MyProfileType(
        user_id=user.pk,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
        email=user.email or "",
        locked_fields=list(locked),
        org_allows_edit=org.allow_user_profile_edit,
        timezone=tz,
    )
