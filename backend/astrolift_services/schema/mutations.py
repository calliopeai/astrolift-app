"""Mutations for app secrets, secret bundles, managed services."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import (
    delete_app_env_key,
    parse_dotenv,
    set_app_env_keys,
)
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    AppSecretBundleRef,
    ManagedService,
    SecretBundle,
)
from astrolift_services.schema.types import (
    AppSecretBundleAttachmentType,
    ManagedServiceType,
    attachment_to_type,
    managed_service_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission


# ---------------------------------------------------------------------
# Inputs


@strawberry.input
class SetAppSecretInput:
    """Sets an app-level [env] literal in the staged manifest buffer.

    The frontend's secrets editor surfaces a 'set' UX; on save the
    mutation writes to ``manifest_raw_staged`` (same buffer #277
    set up). The user follows up with ``pushManifestToRepo`` to
    open a PR with the changes."""

    app_slug: str
    key: str
    value: str


@strawberry.input
class DeleteAppSecretInput:
    app_slug: str
    key: str


@strawberry.input
class BulkImportAppSecretsInput:
    app_slug: str
    dotenv_text: str
    """Multi-line ``KEY=value`` text (.env shape)."""


@strawberry.input
class AttachSecretBundleInput:
    app_slug: str
    environment_name: str
    bundle_slug: str
    prefix: str | None = None


@strawberry.input
class DetachSecretBundleInput:
    attachment_id: GUID


@strawberry.type
class _AppSecretWritePayload:
    app_slug: str
    key: str
    raw_manifest_staged: str


@strawberry.type
class _BulkImportPayload:
    app_slug: str
    keys_set: list[str]
    raw_manifest_staged: str


@strawberry.type
class _AttachmentRemovedPayload:
    attachment_id: GUID
    deleted: bool


# ---------------------------------------------------------------------
# Helpers


_ENV_NAME_HINT = (
    "must start with a letter or underscore and use only "
    "[A-Z0-9_] (POSIX env-var rules)"
)


def _validate_env_key(key: str) -> str | None:
    if not key:
        return "key cannot be empty"
    if not (key[0].isalpha() or key[0] == "_"):
        return f"key {key!r} {_ENV_NAME_HINT}"
    if not all(c.isalnum() or c == "_" for c in key):
        return f"key {key!r} {_ENV_NAME_HINT}"
    return None


def _stage_manifest(app, new_text: str) -> str:
    """Validate the new TOML parses, then write to the staging
    buffer + clear it when it matches the source-of-truth."""
    try:
        parse_raw(new_text)
    except ManifestError as exc:
        raise ManifestError(str(exc)) from exc
    if new_text == (app.manifest_raw or ""):
        app.manifest_raw_staged = ""
    else:
        app.manifest_raw_staged = new_text
    app.save(update_fields=[
        "manifest_raw_staged", "updated_at", "version",
    ])
    return app.manifest_raw_staged


# ---------------------------------------------------------------------


@strawberry.type
class ServicesMutation:
    @strawberry.field
    @mutation_audit(action="app.secret.set")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_app_secret(
        self, info: Info, input: SetAppSecretInput,
    ) -> MutationResultType[_AppSecretWritePayload]:
        validation_msg = _validate_env_key(input.key)
        if validation_msg:
            return gql_failure(
                ErrorCode.VALIDATION.value, validation_msg, field="key",
            )
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        source = app.manifest_raw_staged or app.manifest_raw or ""
        new_text = set_app_env_keys(source, {input.key: input.value})
        try:
            staged = _stage_manifest(app, new_text)
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after edit: {exc}",
                field="rawManifest",
            )
        return gql_success(_AppSecretWritePayload(
            app_slug=app.slug,
            key=input.key,
            raw_manifest_staged=staged,
        ))

    @strawberry.field
    @mutation_audit(action="app.secret.delete")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def delete_app_secret(
        self, info: Info, input: DeleteAppSecretInput,
    ) -> MutationResultType[_AppSecretWritePayload]:
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        source = app.manifest_raw_staged or app.manifest_raw or ""
        new_text, removed = delete_app_env_key(source, input.key)
        if not removed:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"key {input.key!r} not present in [env]",
                field="key",
            )
        try:
            staged = _stage_manifest(app, new_text)
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after delete: {exc}",
                field="rawManifest",
            )
        return gql_success(_AppSecretWritePayload(
            app_slug=app.slug,
            key=input.key,
            raw_manifest_staged=staged,
        ))

    @strawberry.field
    @mutation_audit(action="app.secret.bulk_import")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def bulk_import_app_secrets(
        self, info: Info, input: BulkImportAppSecretsInput,
    ) -> MutationResultType[_BulkImportPayload]:
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        kvs = parse_dotenv(input.dotenv_text or "")
        if not kvs:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "no valid KEY=value lines parsed from input",
                field="dotenvText",
            )
        # Validate all keys before staging so a single bad name
        # doesn't write a half-applied result.
        for key in kvs:
            msg = _validate_env_key(key)
            if msg:
                return gql_failure(
                    ErrorCode.VALIDATION.value, msg, field="dotenvText",
                )
        source = app.manifest_raw_staged or app.manifest_raw or ""
        new_text = set_app_env_keys(source, kvs)
        try:
            staged = _stage_manifest(app, new_text)
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after bulk import: {exc}",
                field="rawManifest",
            )
        return gql_success(_BulkImportPayload(
            app_slug=app.slug,
            keys_set=sorted(kvs.keys()),
            raw_manifest_staged=staged,
        ))

    @strawberry.field
    @mutation_audit(action="app.secret.bundle.attach")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def attach_secret_bundle(
        self, info: Info, input: AttachSecretBundleInput,
    ) -> MutationResultType[AppSecretBundleAttachmentType]:
        app = (
            RegisteredApp.objects
            .filter(slug=input.app_slug)
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        env = (
            AppEnvironment.objects
            .filter(
                registered_app=app,
                name=input.environment_name,
                deleted_at__isnull=True,
            )
            .first()
        )
        if env is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {input.environment_name!r} not found",
                field="environmentName",
            )
        bundle = (
            SecretBundle.objects
            .filter(slug=input.bundle_slug, deleted_at__isnull=True)
            .first()
        )
        if bundle is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"bundle {input.bundle_slug!r} not found",
                field="bundleSlug",
            )
        # Cross-org scoping: bundles belong to the calling tenant
        # via the @tenant_scoped() decorator, but bundle ↔ app
        # require same-org explicitly here so a typo doesn't
        # accidentally cross.
        if bundle.organization_id != app.organization_id:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "bundle and app belong to different organizations",
            )
        existing = AppSecretBundleRef.objects.filter(
            registered_app=app,
            app_environment=env,
            secret_bundle=bundle,
            deleted_at__isnull=True,
        ).first()
        if existing is not None:
            # Idempotent — same attachment with same prefix is a
            # no-op; differing prefix updates.
            new_prefix = input.prefix or ""
            if existing.prefix != new_prefix:
                existing.prefix = new_prefix
                existing.save(update_fields=[
                    "prefix", "updated_at", "version",
                ])
            return gql_success(attachment_to_type(existing))
        ref = AppSecretBundleRef.objects.create(
            registered_app=app,
            app_environment=env,
            secret_bundle=bundle,
            prefix=input.prefix or "",
        )
        return gql_success(attachment_to_type(ref))

    @strawberry.field
    @mutation_audit(action="app.secret.bundle.detach")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def detach_secret_bundle(
        self, info: Info, input: DetachSecretBundleInput,
    ) -> MutationResultType[_AttachmentRemovedPayload]:
        ref = (
            AppSecretBundleRef.objects
            .filter(guid=str(input.attachment_id))
            .first()
        )
        if ref is None or ref.deleted_at is not None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "attachment not found",
            )
        ref.soft_delete()
        return gql_success(_AttachmentRemovedPayload(
            attachment_id=input.attachment_id, deleted=True,
        ))
