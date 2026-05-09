"""GraphQL types for app secrets, managed services, secret bundles."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftAppSecret")
class AppSecretType:
    """A secret reference visible to the workload at runtime.

    The ``value`` is never exposed via GraphQL — secrets stay in
    the platform's secrets backend or in the source manifest.
    ``isMasked`` is always True for now (literals from the manifest
    are committed in plaintext to the source repo, but the API
    treats them as opaque on read so the editor surface stays
    write-only).
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


def attachment_to_type(ref) -> AppSecretBundleAttachmentType:
    return AppSecretBundleAttachmentType(
        id=GUID(str(ref.guid)),
        bundle_slug=ref.secret_bundle.slug,
        bundle_name=ref.secret_bundle.name,
        environment_name=ref.app_environment.name,
        prefix=ref.prefix or "",
        registered_app_slug=ref.registered_app.slug,
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
