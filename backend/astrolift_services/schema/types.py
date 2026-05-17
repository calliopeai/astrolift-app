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
    status_error: str
    config: JSON
    registered_app_slug: str
    environment_name: str
    created_at: dt.datetime
    updated_at: dt.datetime
    last_action_at: dt.datetime | None = None
    last_action_kind: str = ""


@strawberry.type(name="AstroliftManagedServiceConnectionKey")
class ManagedServiceConnectionKeyType:
    """One entry of the connection envelope projected into the
    workload at runtime.

    The platform synthesizes a fixed key set per kind (see
    ``astrolift_manifest.env_injection._ENVELOPES``); the actual values
    live in the platform secrets backend and are not reachable from this
    API surface.  ``value`` is therefore a placeholder string ('secret-
    ref:<ref>' / 'env-ref:<key>') rather than plaintext — the audit
    trail still captures the disclosure of the key set so the operator
    can demonstrate provenance later.
    """

    key: str
    value: str
    """Always opaque — never plaintext.  The shape is
    ``secret-ref:<connection_secret_ref>`` when the platform has minted
    a secrets-backend pointer for this service, or
    ``placeholder:<note>`` when the workflow loop hasn't populated the
    binding yet."""

    is_secret: bool = True


@strawberry.type(name="AstroliftManagedServiceConnection")
class ManagedServiceConnectionType:
    managed_service_id: GUID
    kind: str
    name: str
    environment_name: str
    connection_secret_ref: str
    """Pointer into the platform secrets backend (e.g.
    ``vault:/acme/app/prod/postgres-primary``); empty until the workflow
    finalizes the binding."""

    keys: list[ManagedServiceConnectionKeyType]
    revealed_at: dt.datetime


@strawberry.type(name="AstroliftManagedServiceObject")
class ManagedServiceObjectType:
    """A single entry in the recent-objects listing for an
    ``object_store`` managed service.  Populated from the
    ``config['recent_objects']`` cache the workflow refreshes on a
    schedule — a live S3/GCS/Azure list call needs the driver SDK to
    expose ``list_recent_objects``, which is filed as a backend gap
    follow-up."""

    key: str
    size_bytes: int = 0
    last_modified: dt.datetime | None = None


@strawberry.type(name="AstroliftManagedServiceObjects")
class ManagedServiceObjectsType:
    managed_service_id: GUID
    kind: str
    name: str
    objects: list[ManagedServiceObjectType]
    truncated: bool = False
    cache_age_seconds: int | None = None
    """How stale the ``recent_objects`` snapshot is; ``None`` when the
    workflow has never populated it (operator sees an info hint in that
    case)."""


@strawberry.type(name="AstroliftManagedServiceQueueDepth")
class ManagedServiceQueueDepthType:
    """Snapshot of the message depth on a `queue` / `topic` kind
    managed service.

    Sourced from the `config['depth_snapshot']` cache the workflow
    refreshes on a schedule — a live cloud round-trip needs the driver
    SDK to expose `queue_depth`, filed as a backend gap follow-up.
    """

    managed_service_id: GUID
    kind: str
    name: str
    depth: int = 0
    in_flight: int = 0
    sampled_at: dt.datetime | None = None
    """When the cached snapshot was last refreshed; ``None`` until the
    workflow loop has populated it."""


@strawberry.type(name="AstroliftManagedServiceTestEmailResult")
class ManagedServiceTestEmailResultType:
    managed_service_id: GUID
    recipient: str
    subject: str
    sent_at: dt.datetime
    transport: str
    """Configured transport kind at the time of send (``aws_ses`` /
    ``sendgrid`` / ``smtp`` / ``postmark``).  Useful for the operator
    to confirm which provider received the call."""


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
        status_error=svc.status_error or "",
        config=svc.config or {},
        registered_app_slug=svc.registered_app.slug,
        environment_name=svc.app_environment.name,
        created_at=svc.created_at,
        updated_at=svc.updated_at,
        last_action_at=svc.last_action_at,
        last_action_kind=svc.last_action_kind or "",
    )
