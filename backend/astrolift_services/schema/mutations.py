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
    SecretBundleType,
    attachment_to_type,
    managed_service_to_type,
    secret_bundle_to_type,
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


@strawberry.input
class RotateSecretBundleInput:
    """Fire the ``RotateSecretBundleWorkflow`` for one bundle. The
    workflow re-fetches values from the SecretsBackend, applies them
    across every cluster the bundle is referenced on, then bounces
    every Deployment that envFroms the bundle so pods pick up the
    new values immediately (#365)."""

    id: GUID


# Managed services CRUD (#281) ---------------------------------------


@strawberry.input
class ProvisionManagedServiceInput:
    app_slug: str
    environment_name: str
    kind: str
    """Catalog kind: postgres | redis | object_store | queue | ..."""

    name: str | None = None
    variant: str | None = None
    config: strawberry.scalars.JSON | None = None


@strawberry.input
class UpdateManagedServiceInput:
    id: GUID
    config: strawberry.scalars.JSON | None = None
    name: str | None = None


@strawberry.input
class DeprovisionManagedServiceInput:
    """Two-axis safety surface for the managed-service deprovision (#320).

    ``deleteData`` False (default): the driver takes the safest deletion
    path — RDS final-snapshot, S3 retains contents, queues drain. The
    artifact survives for later restore.

    ``deleteData`` True: irreversibly delete persistent state.

    ``forceDestroy`` False (default): respect cloud-side deletion-
    protection flags; refuse with an error message when a guard trips.

    ``forceDestroy`` True: bypass guards (suspend versioning, ignore
    deletion-protection, --atomic cleanup). Terraform-style semantic.

    The UI surfaces both as separate explicit checkboxes so destructive
    paths can't be triggered accidentally.
    """

    id: GUID
    delete_data: bool = False
    force_destroy: bool = False


@strawberry.type
class _ManagedServiceDeletedPayload:
    id: GUID
    deleted: bool


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


_ENV_NAME_HINT = "must start with a letter or underscore and use only [A-Z0-9_] (POSIX env-var rules)"


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
    app.save(
        update_fields=[
            "manifest_raw_staged",
            "updated_at",
            "version",
        ]
    )
    return app.manifest_raw_staged


# ---------------------------------------------------------------------


@strawberry.type
class ServicesMutation:
    @strawberry.field
    @mutation_audit(action="app.secret.set")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_app_secret(
        self,
        info: Info,
        input: SetAppSecretInput,
    ) -> MutationResultType[_AppSecretWritePayload]:
        validation_msg = _validate_env_key(input.key)
        if validation_msg:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                validation_msg,
                field="key",
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
        return gql_success(
            _AppSecretWritePayload(
                app_slug=app.slug,
                key=input.key,
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.delete")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def delete_app_secret(
        self,
        info: Info,
        input: DeleteAppSecretInput,
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
        return gql_success(
            _AppSecretWritePayload(
                app_slug=app.slug,
                key=input.key,
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.bulk_import")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def bulk_import_app_secrets(
        self,
        info: Info,
        input: BulkImportAppSecretsInput,
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
                    ErrorCode.VALIDATION.value,
                    msg,
                    field="dotenvText",
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
        return gql_success(
            _BulkImportPayload(
                app_slug=app.slug,
                keys_set=sorted(kvs.keys()),
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.bundle.attach")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def attach_secret_bundle(
        self,
        info: Info,
        input: AttachSecretBundleInput,
    ) -> MutationResultType[AppSecretBundleAttachmentType]:
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        env = AppEnvironment.objects.filter(
            registered_app=app,
            name=input.environment_name,
            deleted_at__isnull=True,
        ).first()
        if env is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {input.environment_name!r} not found",
                field="environmentName",
            )
        bundle = SecretBundle.objects.filter(slug=input.bundle_slug, deleted_at__isnull=True).first()
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
                existing.save(
                    update_fields=[
                        "prefix",
                        "updated_at",
                        "version",
                    ]
                )
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
        self,
        info: Info,
        input: DetachSecretBundleInput,
    ) -> MutationResultType[_AttachmentRemovedPayload]:
        ref = AppSecretBundleRef.objects.filter(guid=str(input.attachment_id)).first()
        if ref is None or ref.deleted_at is not None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "attachment not found",
            )
        ref.soft_delete()
        return gql_success(
            _AttachmentRemovedPayload(
                attachment_id=input.attachment_id,
                deleted=True,
            )
        )

    @strawberry.field
    @mutation_audit(action="secret_bundle.rotate")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def rotate_secret_bundle(
        self,
        info: Info,
        input: RotateSecretBundleInput,
    ) -> MutationResultType[SecretBundleType]:
        """Fire ``RotateSecretBundleWorkflow`` for one SecretBundle (#365).

        Re-applies the bundle's materialized k8s Secret on every
        cluster the bundle is referenced on (operator-fired path bounces
        consumers; the hourly scheduled path doesn't). Idempotent — the
        workflow id is bound to the bundle so re-firing joins the
        existing run rather than spawning a parallel one.
        """
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            RotateSecretBundleInput as RotateInput,
        )

        bundle = SecretBundle.objects.filter(
            guid=str(input.id),
            deleted_at__isnull=True,
        ).first()
        if bundle is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "secret bundle not found",
            )
        request = getattr(info.context, "request", None)
        user = getattr(request, "user", None) if request else None
        actor = (
            Actor(
                kind="user",
                user_id=user.pk,
                display=getattr(user, "username", "") or "",
            )
            if user is not None and getattr(user, "is_authenticated", False)
            else Actor(kind="system", display="rotate-secret-bundle")
        )
        start_workflow(
            "RotateSecretBundleWorkflow",
            args=[
                RotateInput(
                    secret_bundle_id=bundle.pk,
                    actor=actor,
                    bounce_workloads=True,
                ),
            ],
            workflow_id=f"RotateSecretBundleWorkflow-{bundle.guid}",
        )
        return gql_success(secret_bundle_to_type(bundle))

    # ---- Managed services CRUD (#281) ----------------------------

    @strawberry.field
    @mutation_audit(action="managed_service.provision")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def provision_managed_service(
        self,
        info: Info,
        input: ProvisionManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        """Provision a managed-service binding.

        DB-side write only — the actual workflow that drives the
        provider plugin's provision() lives in
        ``astrolift_workflows`` and reads from this row. The
        mutation creates the row in PENDING state; the workflow
        loop transitions it through PROVISIONING → ACTIVE."""
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        env = AppEnvironment.objects.filter(
            registered_app=app,
            name=input.environment_name,
            deleted_at__isnull=True,
        ).first()
        if env is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {input.environment_name!r} not found",
                field="environmentName",
            )
        valid_kinds = {k for k, _ in ManagedService.Kind.choices}
        if input.kind not in valid_kinds:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"kind must be one of {sorted(valid_kinds)}",
                field="kind",
            )
        name = (input.name or input.kind).strip()
        if ManagedService.objects.filter(
            registered_app=app,
            kind=input.kind,
            name=name,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"managed service ({input.kind}, {name!r}) already exists for this app",
                field="name",
            )
        svc = ManagedService.objects.create(
            registered_app=app,
            app_environment=env,
            kind=input.kind,
            name=name,
            variant=input.variant or "",
            config=dict(input.config or {}),
            status=ManagedService.Status.PENDING,
        )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.update")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_managed_service(
        self,
        info: Info,
        input: UpdateManagedServiceInput,
    ) -> MutationResultType[ManagedServiceType]:
        svc = ManagedService.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
            )
        if input.name is not None:
            svc.name = input.name.strip()
        if input.config is not None:
            svc.config = dict(input.config)
        # Re-applying config kicks the workflow back to UPDATING;
        # the workflow loop will roll it forward to ACTIVE.
        if svc.status == ManagedService.Status.ACTIVE:
            svc.status = ManagedService.Status.UPDATING
        svc.save(
            update_fields=[
                "name",
                "config",
                "status",
                "updated_at",
                "version",
            ]
        )
        return gql_success(managed_service_to_type(svc))

    @strawberry.field
    @mutation_audit(action="managed_service.deprovision")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def deprovision_managed_service(
        self,
        info: Info,
        input: DeprovisionManagedServiceInput,
    ) -> MutationResultType[_ManagedServiceDeletedPayload]:
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import (
            Actor,
        )
        from astrolift_workflows.inputs import (
            DeprovisionManagedServiceInput as DeprovisionInput,
        )

        svc = ManagedService.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
            )

        # Flip to DEPROVISIONING so the UI shows the in-flight state
        # immediately. The workflow re-asserts on entry; the platform
        # row is only soft-deleted by the workflow's finalize activity
        # AFTER the driver confirms the backend resource is gone.
        svc.status = ManagedService.Status.DEPROVISIONING
        svc.save(
            update_fields=[
                "status",
                "updated_at",
                "version",
            ]
        )

        request = info.context.request  # type: ignore[attr-defined]
        user = getattr(request, "user", None)
        actor = Actor(
            kind="user",
            user_id=getattr(user, "pk", None) if user is not None else None,
            display=str(getattr(user, "email", "") or getattr(user, "username", "")),
        )
        start_workflow(
            "DeprovisionManagedServiceWorkflow",
            args=[
                DeprovisionInput(
                    managed_service_id=svc.pk,
                    actor=actor,
                    delete_data=bool(input.delete_data),
                    force_destroy=bool(input.force_destroy),
                ),
            ],
            workflow_id=f"DeprovisionManagedServiceWorkflow-{svc.guid}",
        )
        return gql_success(
            _ManagedServiceDeletedPayload(
                id=input.id,
                deleted=False,  # workflow finalizes the soft-delete
            )
        )
