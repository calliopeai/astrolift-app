"""GraphQL types for app secrets, managed services, secret bundles."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftSecretEditor")
class SecretEditorType:
    """Compact actor reference for the `lastEditedBy` surface (#424).

    Resolved from `Tracking.updated_by`; null when the record predates
    the per-field-attribution work or was set by a system actor.
    Mirrors the shape of `AstroliftUser` for the fields the UI needs
    without forcing a circular schema import.
    """

    id: str
    """Django auth user pk rendered as string (mirrors AstroliftUser)."""

    username: str
    display_name: str


@strawberry.type(name="AstroliftAppSecret")
class AppSecretType:
    """A secret reference visible to the workload at runtime.

    The ``value`` is never exposed via GraphQL except through the
    explicit ``revealAppSecret`` mutation (`secret.read`-gated) for
    literals; bundle / managed-service rows always stay opaque on
    read since the values live in the secrets backend.
    """

    id: str
    """Stable id derived from ``(source, key, environment)`` so the
    UI can key React lists; may collide if two sources surface the
    same key — UI presents both rows + tags by source."""

    key: str
    environment_name: str
    source: str
    """literal | bundle | managed_service"""

    bundle_slug: str
    """Set when source=bundle; empty otherwise."""

    managed_service_kind: str
    """Set when source=managed_service; empty otherwise."""

    is_masked: bool = True
    last_edited_at: dt.datetime | None = None
    last_edited_by: SecretEditorType | None = None


@strawberry.type(name="AstroliftSecretBundle")
class SecretBundleType:
    id: GUID
    slug: str
    name: str
    backend_ref: str
    organization_slug: str
    team_slug: str | None
    created_at: dt.datetime


@strawberry.type(name="AstroliftAppSecretBundleAttachment")
class AppSecretBundleAttachmentType:
    id: GUID
    bundle_slug: str
    bundle_name: str
    environment_name: str
    prefix: str
    registered_app_slug: str
    team_slug: str | None = None
    """Owning team of the underlying bundle; null for org-wide bundles."""

    key_count: int = 0
    """Number of keys the bundle is expected to project at runtime.

    Bundle values themselves live in the platform secrets backend
    (Vault / SecretsManager / GSM / KeyVault) — the platform stores
    only a reference + the manifest-declared envelope. When the
    envelope is unknown the count is 0 and the UI shows '?'.
    """

    merge_order: int = 0
    """Lowest-first merge order across attachments on the same
    (app, environment). App-local literals always win on collision
    regardless of merge order — operators read this column to debug
    precedence when two bundles project the same key."""

    attached_at: dt.datetime | None = None


@strawberry.type(name="AstroliftManagedService")
class ManagedServiceType:
    id: GUID
    name: str
    kind: str
    variant: str
    status: str
    config: JSON
    registered_app_slug: str
    environment_name: str
    created_at: dt.datetime
    updated_at: dt.datetime


@strawberry.type(name="AstroliftRevealedSecret")
class RevealedSecretType:
    """Plaintext payload returned by `revealAppSecret` (#424).

    Returned only for `literal` source secrets — bundle and managed-
    service values live in the platform secrets backend and need a
    separate workflow to fetch (out of scope for this mutation)."""

    secret_id: str
    key: str
    environment_name: str
    value: str
    revealed_at: dt.datetime


def secret_editor_from_user(user) -> SecretEditorType | None:
    """Strawberry-friendly shaped from a Django auth user; returns
    None when no user is on the record (system writes, legacy)."""
    if user is None:
        return None
    username = getattr(user, "username", "") or ""
    try:
        full_name = user.get_full_name()
    except Exception:  # noqa: BLE001 — user model swap-safety
        full_name = ""
    display = full_name or username or getattr(user, "email", "") or ""
    return SecretEditorType(
        id=str(user.pk),
        username=username,
        display_name=display,
    )


def secret_bundle_to_type(b) -> SecretBundleType:
    return SecretBundleType(
        id=GUID(str(b.guid)),
        slug=b.slug,
        name=b.name,
        backend_ref=b.backend_ref or "",
        organization_slug=b.organization.slug,
        team_slug=b.team.slug if b.team_id else None,
        created_at=b.created_at,
    )


def attachment_to_type(
    ref,
    *,
    key_count: int = 0,
    merge_order: int = 0,
) -> AppSecretBundleAttachmentType:
    return AppSecretBundleAttachmentType(
        id=GUID(str(ref.guid)),
        bundle_slug=ref.secret_bundle.slug,
        bundle_name=ref.secret_bundle.name,
        environment_name=ref.app_environment.name,
        prefix=ref.prefix or "",
        registered_app_slug=ref.registered_app.slug,
        team_slug=(ref.secret_bundle.team.slug if ref.secret_bundle.team_id else None),
        key_count=key_count,
        merge_order=merge_order,
        attached_at=ref.created_at,
    )


def managed_service_to_type(svc) -> ManagedServiceType:
    return ManagedServiceType(
        id=GUID(str(svc.guid)),
        name=svc.name,
        kind=svc.kind,
        variant=svc.variant or "",
        status=svc.status,
        config=svc.config or {},
        registered_app_slug=svc.registered_app.slug,
        environment_name=svc.app_environment.name,
        created_at=svc.created_at,
        updated_at=svc.updated_at,
    )
