"""Mutations for app secrets, secret bundles, managed services."""

from __future__ import annotations

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import (
    delete_app_env_key,
    parse_dotenv,
    read_app_env,
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
    ManagedServiceConnectionKeyType,
    ManagedServiceConnectionType,
    ManagedServiceTestEmailResultType,
    ManagedServiceType,
    RevealedSecretType,
    SecretBundleType,
    attachment_to_type,
    managed_service_to_type,
    secret_bundle_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

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
class RevealAppSecretInput:
    """Persistent reveal-on-demand for one secret row (#424).

    `app_slug` scopes the lookup to one app (the caller's current page);
    `secret_id` is the stable id minted by `_list_app_secrets` —
    ``{source}:{env}:{key}`` — so the UI can ask for "this row I'm
    looking at" without re-deriving the source/env tuple.

    Reveal is intentionally an explicit per-row mutation rather than
    a query: every successful call audit-logs the actor + IP so the
    org's audit trail captures the disclosure.
    """

    app_slug: str
    secret_id: str


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


@strawberry.input
class RevealManagedServiceConnectionInput:
    """Audit-logged reveal of the connection envelope key set for one
    bound managed service (#401).

    Mirrors `RevealAppSecretInput` (#424): the call is explicit and
    every successful reveal lands in the audit log via the
    @mutation_audit decorator + a sibling emit_audit row carrying the
    client IP.

    The returned envelope NEVER contains plaintext values — the actual
    credentials live in the platform secrets backend and aren't reachable
    from this API surface. The reveal exposes the envelope key set
    (variable names) + the `connection_secret_ref` pointer so an
    operator can confirm which env vars the workload sees and where to
    find the values in the secrets backend.
    """

    managed_service_id: GUID


@strawberry.input
class SendManagedServiceTestEmailInput:
    """Operator-fired 'send test email' against a bound `email` kind
    managed service (#401).

    `subject` / `body` are optional — sensible defaults are used so the
    common case is a one-field interaction (recipient).  The send rides
    the existing `astrolift_operations.email_infra.send()` plumbing —
    the configured transport (SES / SendGrid / Postmark / SMTP) receives
    the payload; suppression list checks fire normally; bypasses
    UNSUBSCRIBE since the operator triggered it deliberately."""

    managed_service_id: GUID
    recipient: str
    subject: str | None = None
    body: str | None = None


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


def _actor_user(info):
    """Return the authenticated user from the resolver context or None.

    Anonymous / unauthenticated contexts (CLI bypass, system actor)
    return None so the caller can no-op the attribution write."""
    request = getattr(info.context, "request", None)
    user = getattr(request, "user", None) if request else None
    if user is None:
        user = getattr(info.context, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


def _client_ip(info) -> str:
    """Best-effort caller-IP for the audit row. Honours
    X-Forwarded-For first (left-most hop) then REMOTE_ADDR."""
    request = getattr(info.context, "request", None)
    if request is None:
        return ""
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "") if hasattr(request, "META") else ""
    if fwd:
        return fwd.split(",")[0].strip()
    if hasattr(request, "META"):
        return request.META.get("REMOTE_ADDR", "") or ""
    return ""


def _stage_manifest(app, new_text: str, *, actor=None) -> str:
    """Validate the new TOML parses, then write to the staging
    buffer + clear it when it matches the source-of-truth.

    ``actor`` is the user attributing this write so the secrets row's
    ``lastEditedBy`` surface (#424) shows who staged the change. Pass
    None for system writes and the existing updated_by value stands."""
    try:
        parse_raw(new_text)
    except ManifestError as exc:
        raise ManifestError(str(exc)) from exc
    if new_text == (app.manifest_raw or ""):
        app.manifest_raw_staged = ""
    else:
        app.manifest_raw_staged = new_text
    update_fields = [
        "manifest_raw_staged",
        "updated_at",
        "version",
    ]
    if actor is not None:
        app.updated_by = actor
        update_fields.append("updated_by")
    app.save(update_fields=update_fields)
    return app.manifest_raw_staged


# ---------------------------------------------------------------------


@strawberry.type
class ServicesMutation:
    @strawberry.field
    @mutation_audit(action="app.secret.set")
    @requires_elevation(action_label="app.secret.set")
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
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
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
    @requires_elevation(action_label="app.secret.delete")
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
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
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
    @requires_elevation(action_label="app.secret.bulk_import")
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
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
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
    @mutation_audit(
        action="app.secret.reveal",
        extras=lambda result: (
            {
                "secret_id": result.data.secret_id,
                "key": result.data.key,
                "environment_name": result.data.environment_name,
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @requires_elevation(action_label="app.secret.reveal")
    @require_permission(Permission.APP_READ, Permission.SECRET_READ)
    @tenant_scoped()
    def reveal_app_secret(
        self,
        info: Info,
        input: RevealAppSecretInput,
    ) -> MutationResultType[RevealedSecretType]:
        """Return plaintext for one secret row (#424).

        Requires both `app.read` (the caller can see the app) and
        `secret.read` (the caller can disclose the value). Plaintext
        is returned **only** for `literal`-source secrets — bundle and
        managed-service values live in the platform secrets backend
        and aren't reachable from the API surface.

        Every successful reveal lands in the audit log via the
        wrapping `@mutation_audit` decorator (actor + IP + tenant +
        target secret id). Failures audit-log too with the failure
        code so the trail captures attempted disclosures.
        """
        # Stable id format: ``{source}:{env}:{key}``. ``env`` is a
        # POSIX-shape env name (no colons); ``key`` is too for
        # literal secrets. Split on the first two colons so a key
        # that contains a colon (managed-service style ``svc.k``)
        # survives reassembly even though we won't reveal those.
        secret_id = input.secret_id or ""
        parts = secret_id.split(":", 2)
        if len(parts) != 3 or not all(parts):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "secretId must look like 'literal:<env>:<KEY>'",
                field="secretId",
            )
        source, env_name, key = parts
        if source != "literal":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"reveal is only supported for literal-source secrets; "
                    f"{source!r} values live in the platform secrets backend "
                    f"and aren't reachable from the API"
                ),
                field="secretId",
            )
        app = RegisteredApp.objects.filter(slug=input.app_slug).first()
        if app is None or app.deleted_at is not None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")
        env_exists = AppEnvironment.objects.filter(
            registered_app=app,
            name=env_name,
            deleted_at__isnull=True,
        ).exists()
        if not env_exists:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {env_name!r} not found",
                field="secretId",
            )
        raw_text = app.manifest_raw_staged or app.manifest_raw or ""
        literals = read_app_env(raw_text)
        if key not in literals:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"no literal secret {key!r} in environment {env_name!r}",
                field="secretId",
            )
        # @mutation_audit (above) already records actor + tenant +
        # secret_id via its extras hook. Emit a sibling audit entry
        # carrying the client IP so the trail captures the disclosure
        # source — done as a separate emit_audit call so the IP isn't
        # surfaced in the GraphQL response payload (which would leak
        # the caller's IP back to a layered proxy).
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="app.secret.reveal.disclosure",
                decision="ALLOW",
                target_kind="app_secret",
                target_id=secret_id,
                duration_ms=0,
                permissions=(
                    Permission.APP_READ.value,
                    Permission.SECRET_READ.value,
                ),
                extra={
                    "app_slug": input.app_slug,
                    "environment_name": env_name,
                    "key": key,
                    "client_ip": ip,
                },
            )
        )
        return gql_success(
            RevealedSecretType(
                secret_id=secret_id,
                key=key,
                environment_name=env_name,
                value=literals[key],
                revealed_at=timezone.now(),
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.bundle.attach")
    @requires_elevation(action_label="app.secret.bundle.attach")
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
        actor = _actor_user(info)
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
                update_fields = [
                    "prefix",
                    "updated_at",
                    "version",
                ]
                if actor is not None:
                    existing.updated_by = actor
                    update_fields.append("updated_by")
                existing.save(update_fields=update_fields)
            # #441: prefix change doesn't alter the key set itself, but
            # surface the cached count back to the operator so the UI
            # shows a real number rather than '?' on first render.
            return gql_success(
                attachment_to_type(
                    existing,
                    key_count=len(bundle.last_known_keys or []),
                )
            )
        ref = AppSecretBundleRef.objects.create(
            registered_app=app,
            app_environment=env,
            secret_bundle=bundle,
            prefix=input.prefix or "",
            created_by=actor,
            updated_by=actor,
        )
        # #441: eagerly enumerate so the UI can show the count on the
        # first read after attach.  Errors are swallowed inside the
        # helper -- attach must not fail because the secrets backend
        # was momentarily unreachable.
        from astrolift_services.bundle_keys import force_refresh_bundle_known_keys

        force_refresh_bundle_known_keys(bundle)
        return gql_success(
            attachment_to_type(
                ref,
                key_count=len(bundle.last_known_keys or []),
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.bundle.detach")
    @requires_elevation(action_label="app.secret.bundle.detach")
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

    # ---- Per-service quick actions (#401) -----------------------------
    #
    # The Settings landing surfaces a managed-services summary card with
    # a per-row dropdown of kind-specific actions:
    #   - postgres / redis / mysql  → revealManagedServiceConnection
    #   - object_store              → listManagedServiceObjects (query)
    #   - email                     → sendManagedServiceTestEmail
    #   - queue / topic             → managedServiceQueueDepth (query)
    #
    # Reveal mirrors #424's pattern: explicit mutation, decorated with
    # @mutation_audit + a sibling emit_audit row for the client IP.  No
    # plaintext leaves the platform — the envelope's `value` field is
    # always a `secret-ref:` or `placeholder:` shim.

    @strawberry.field
    @mutation_audit(
        action="managed_service.connection.reveal",
        extras=lambda result: (
            {
                "managed_service_id": str(result.data.managed_service_id),
                "kind": result.data.kind,
                "environment_name": result.data.environment_name,
                "key_count": len(result.data.keys),
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @requires_elevation(action_label="managed_service.connection.reveal")
    @require_permission(Permission.APP_READ, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def reveal_managed_service_connection(
        self,
        info: Info,
        input: RevealManagedServiceConnectionInput,
    ) -> MutationResultType[ManagedServiceConnectionType]:
        """Disclose the connection envelope key set for one managed
        service (#401).

        Returns the stable env-var key set the workload sees at runtime
        for the service's kind, paired with the platform's
        `connection_secret_ref` pointer.  Plaintext values are NEVER
        returned — they live in the platform secrets backend (Vault /
        SecretsManager / GSM / KeyVault) and aren't reachable from this
        API surface.  Each `value` field is the opaque
        ``secret-ref:<ref>`` or ``placeholder:<note>`` shim that points
        the operator at where to fetch the value via the platform's
        secrets-backend client.

        Permission gate stacks `app.read` (caller can see the app) with
        `managed_service.update` (caller can disclose the pointer) so
        the surface matches the rest of the per-service action set.
        """
        # Lazy import to avoid circulars; the env_injection module is
        # the source of truth for the envelope key set per kind.
        from astrolift_manifest.env_injection import envelope_keys_for

        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(guid=str(input.managed_service_id), deleted_at__isnull=True)
            .first()
        )
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
                field="managedServiceId",
            )

        envelope = envelope_keys_for(svc.kind)
        if not envelope:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"kind {svc.kind!r} has no connection envelope to reveal; "
                    "reveal is only meaningful for kinds the platform injects "
                    "env vars for (postgres, redis, mysql, queue, topic, "
                    "object_store, email, etc.)"
                ),
                field="managedServiceId",
            )

        # Shape each key as ``secret-ref:<ref>`` when the workflow has
        # populated `connection_secret_ref`, else ``placeholder:pending``
        # so the UI can render a clear "not yet provisioned" hint
        # without us inventing a fake value.
        ref = svc.connection_secret_ref or ""
        if ref:
            value_for = lambda k: f"secret-ref:{ref}#{k}"  # noqa: E731
        else:
            value_for = lambda k: "placeholder:pending"  # noqa: E731

        keys = [
            ManagedServiceConnectionKeyType(
                key=k,
                value=value_for(k),
                # The handful of non-secret envelope keys are stable
                # config (region, prefix) — surface them as is_secret=
                # False so the UI doesn't mask them.
                is_secret=not _is_envelope_key_public(k),
            )
            for k in envelope
        ]

        # Sibling audit row carrying the client IP so the trail captures
        # the disclosure source (#424 pattern).  Done as a separate
        # emit_audit call so the IP isn't surfaced in the GraphQL
        # response (which would leak the caller's IP to a layered
        # proxy).
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.connection.reveal.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_READ.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "kind": svc.kind,
                    "name": svc.name,
                    "app_slug": svc.registered_app.slug,
                    "environment_name": svc.app_environment.name,
                    "key_count": len(keys),
                    "connection_secret_ref": ref,
                    "client_ip": ip,
                },
            )
        )

        # Stamp the cached "last operator action" surface so the
        # summary card can render "revealed N seconds ago" without
        # re-walking the audit log.
        now = timezone.now()
        svc.last_action_at = now
        svc.last_action_kind = "connection.reveal"
        svc.save(update_fields=["last_action_at", "last_action_kind", "updated_at", "version"])

        return gql_success(
            ManagedServiceConnectionType(
                managed_service_id=input.managed_service_id,
                kind=svc.kind,
                name=svc.name,
                environment_name=svc.app_environment.name,
                connection_secret_ref=ref,
                keys=keys,
                revealed_at=now,
            )
        )

    @strawberry.field
    @mutation_audit(
        action="managed_service.test_email.send",
        extras=lambda result: (
            {
                "managed_service_id": str(result.data.managed_service_id),
                "recipient": result.data.recipient,
                "transport": result.data.transport,
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @require_permission(Permission.APP_UPDATE, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def send_managed_service_test_email(
        self,
        info: Info,
        input: SendManagedServiceTestEmailInput,
    ) -> MutationResultType[ManagedServiceTestEmailResultType]:
        """Operator-fired test send through a bound `email` kind managed
        service (#401).

        Rides the existing `astrolift_operations.email_infra` plumbing —
        the configured transport (SES / SendGrid / Postmark / SMTP)
        receives the rendered payload.  Suppression list checks fire
        normally so a hard-bounced address won't be retried; bypasses
        UNSUBSCRIBE since the operator triggered it deliberately to
        verify deliverability.
        """
        # Lazy imports — keep mutation module light when email infra
        # isn't reached.
        from astrolift_operations.email_infra import (
            Email,
            EmailError,
            EmailKind,
            configured_transport,
            is_configured,
        )
        from astrolift_operations.email_infra import (
            send as email_send,
        )

        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(guid=str(input.managed_service_id), deleted_at__isnull=True)
            .first()
        )
        if svc is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "managed service not found",
                field="managedServiceId",
            )
        if svc.kind != ManagedService.Kind.EMAIL:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"managed service is {svc.kind!r}, not 'email'; "
                    "test-email is only supported for email kinds (SES, "
                    "SendGrid, Postmark, SMTP variants)"
                ),
                field="managedServiceId",
            )

        recipient = (input.recipient or "").strip()
        if not recipient:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "recipient is required",
                field="recipient",
            )

        # Resolve a sane from_address from the binding config; falls
        # back to a synthesized address scoped to the app so the email's
        # provenance is obvious in the recipient's mailbox.
        config = svc.config or {}
        from_address = (
            config.get("email_from")
            or config.get("EMAIL_FROM")
            or f"noreply@{svc.registered_app.slug}.astrolift.local"
        )
        subject = (input.subject or "").strip() or (f"[Astrolift] Test email from {svc.name or svc.kind}")
        body = (input.body or "").strip() or (
            f"This is a deliverability test fired from the {svc.registered_app.slug} "
            f"settings page against managed service {svc.name or svc.kind} "
            f"({svc.app_environment.name}).  If you received this, the email "
            "binding is working."
        )

        if not is_configured():
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    "no email transport configured on this install; admin "
                    "must set EMAIL_BACKEND before test sends will land"
                ),
                field="managedServiceId",
            )

        try:
            email = Email(
                to_address=recipient,
                subject=subject,
                html_body=f"<p>{body}</p>",
                plain_body=body,
                from_address=from_address,
                kind=EmailKind.MANAGED_SERVICE_TEST,
            )
        except EmailError as exc:
            field = "recipient" if "to_address" in str(exc) else "managedServiceId"
            return gql_failure(
                ErrorCode.VALIDATION.value,
                str(exc),
                field=field,
            )

        try:
            email_send(email, suppression_lookup=lambda _addr: None)
        except EmailError as exc:
            return gql_failure(
                ErrorCode.INTERNAL.value,
                str(exc),
                field="managedServiceId",
            )

        transport = configured_transport().value

        # Cache the operator action for the summary card.
        now = timezone.now()
        svc.last_action_at = now
        svc.last_action_kind = "test_email.send"
        svc.save(update_fields=["last_action_at", "last_action_kind", "updated_at", "version"])

        # Sibling audit row carrying client IP + recipient so the trail
        # captures the disclosure source (operators triggering a test
        # send to an unfamiliar address should be obvious in audit).
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="managed_service.test_email.send.disclosure",
                decision="ALLOW",
                target_kind="managed_service",
                target_id=str(svc.guid),
                duration_ms=0,
                permissions=(
                    Permission.APP_UPDATE.value,
                    Permission.MANAGED_SERVICE_UPDATE.value,
                ),
                extra={
                    "kind": svc.kind,
                    "name": svc.name,
                    "app_slug": svc.registered_app.slug,
                    "environment_name": svc.app_environment.name,
                    "recipient": recipient,
                    "from_address": from_address,
                    "transport": transport,
                    "client_ip": ip,
                },
            )
        )

        return gql_success(
            ManagedServiceTestEmailResultType(
                managed_service_id=input.managed_service_id,
                recipient=recipient,
                subject=subject,
                sent_at=now,
                transport=transport,
            )
        )


# Envelope keys that are stable configuration (region, prefix, name)
# rather than secrets.  Used by the reveal mutation to mark non-secret
# rows so the UI doesn't mask them.
_PUBLIC_ENVELOPE_KEY_SUFFIXES: tuple[str, ...] = (
    "_REGION",
    "_PREFIX",
    "_NAME",
    "_HOST",
    "_PORT",
    "_DB",
    "_BUCKET",
    "_DOMAIN",
    "_FROM",
    "_PROVIDER",
    "_SSL_MODE",
    "_TLS",
    "_TOPIC",
    "_INDEX",
    "_INDEX_PREFIX",
    "_TABLE_NAME",
    "_VOLUME",
    "_PARTITION_KEY",
    "_SORT_KEY",
    "_DISTRIBUTION_ID",
    "_DOMAIN_NAME",
    "_INVALIDATION_ROLE",
    "_ENDPOINT",
    "_NAMESPACE",
    "_ORG",
    "_ARN_OR_ID",
    "_MASTER_SECRET_REF",
    "_BOOTSTRAP_SERVERS",
    "_SECURITY_PROTOCOL",
    "_SASL_MECHANISM",
    "_SASL_USERNAME",
    "_TOPIC_PREFIX",
)


def _is_envelope_key_public(key: str) -> bool:
    """An envelope key is 'public' (non-secret) if it carries
    configuration shape rather than a credential.  The reveal mutation
    marks public keys with ``is_secret=False`` so the UI doesn't mask
    them; secret-shaped keys (PASSWORD, API_KEY, URL with embedded
    creds, etc.) stay masked."""
    upper = key.upper()
    # URLs typically embed creds (postgres://user:pass@host); treat as
    # secret unless explicitly suffixed to a region/endpoint marker.
    if upper.endswith("_URL"):
        return False
    if upper.endswith(("_PASSWORD", "_API_KEY", "_USER")):
        return False
    for suffix in _PUBLIC_ENVELOPE_KEY_SUFFIXES:
        if upper.endswith(suffix):
            return True
    return False
