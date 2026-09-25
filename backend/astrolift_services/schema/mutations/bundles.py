"""SecretBundleMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from strawberry.types import Info

from astrolift_clusters.models import TenantCluster
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Project
from astrolift_identity.scopes import project_scope_by_guid
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from astrolift_services.models import (
    AppSecretBundleRef,
    SecretBundle,
    SecretChangeProposal,
)
from astrolift_services.schema.mutations.helpers import (
    _actor_user,
    _caller_org_id,
    _maybe_create_proposal_for_write,
)
from astrolift_services.schema.mutations.types import (
    AttachSecretBundleInput,
    CreateProjectSecretBundleInput,
    DetachSecretBundleInput,
    ProjectSecretBundleKeyInput,
    RotateSecretBundleInput,
    UpdateProjectSecretBundleInput,
    _AttachmentRemovedPayload,
)
from astrolift_services.schema.types import (
    AppSecretBundleAttachmentType,
    SecretBundleRevealType,
    SecretBundleType,
    attachment_to_type,
    secret_bundle_to_type,
)
from astrolift_services.scopes import bundle_attachment_app_scope, secret_bundle_project_scope
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission


def _project_bundle_for_caller(bundle_id):
    return (
        SecretBundle.objects.select_related("organization", "project", "tenant_cluster")
        .filter(
            guid=str(bundle_id),
            project__organization_id=_caller_org_id(),
            deleted_at__isnull=True,
        )
        .first()
    )


def _project_bundle_backend(bundle):
    from astrolift_dispatch.agent_secrets import secret_backend_capabilities
    from core.app_deploy import AppDeployError, driver_for_capability

    try:
        backend = driver_for_capability(bundle.tenant_cluster, "secrets")
    except AppDeployError:
        return (
            None,
            None,
            gql_failure(
                ErrorCode.PRECONDITION.value,
                "the project cluster has no secrets backend",
            ),
        )
    return backend, secret_backend_capabilities(backend), None


def _project_bundle_backend_ref(project, slug: str) -> str:
    """Canonical tenant-safe location for a project-owned bundle."""

    return f"project-bundles/{project.organization.guid}/{project.guid}/{slug}"


@strawberry.type
class SecretBundleMutations:
    @strawberry.field
    @mutation_audit(action="project.secret.bundle.create")
    @requires_elevation(action_label="project.secret.bundle.create")
    @require_permission(
        Permission.PROJECT_UPDATE, Permission.SECRET_WRITE, scope=project_scope_by_guid("input.project_id")
    )
    @tenant_scoped()
    def create_project_secret_bundle(
        self,
        info: Info,
        input: CreateProjectSecretBundleInput,
    ) -> MutationResultType[SecretBundleType]:
        project = (
            Project.objects.select_related("organization")
            .filter(
                guid=str(input.project_id),
                organization_id=_caller_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found", field="projectId")
        cluster = TenantCluster.objects.filter(
            Q(organization_id=project.organization_id) | Q(organization_id__isnull=True),
            guid=str(input.cluster_id),
            deleted_at__isnull=True,
            is_active=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        ).first()
        if cluster is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "cluster not found", field="clusterId")
        name = (input.name or "").strip()
        normalized_slug = slugify(input.slug or name)[:200]
        if not name or not normalized_slug:
            return gql_failure(ErrorCode.VALIDATION.value, "name and slug are required")
        from astrolift_services.models.secret_bundle import reserved_bundle_slug_error

        reserved = reserved_bundle_slug_error(normalized_slug)
        if reserved:
            return gql_failure(ErrorCode.VALIDATION.value, reserved, field="slug")
        backend_ref = _project_bundle_backend_ref(project, normalized_slug)
        requested_backend_ref = (input.backend_ref or "").strip()
        if requested_backend_ref and requested_backend_ref != backend_ref:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "project bundle backendRef is platform managed",
                field="backendRef",
            )
        try:
            bundle = SecretBundle.objects.create(
                organization=project.organization,
                project=project,
                tenant_cluster=cluster,
                team=None,
                name=name[:200],
                slug=normalized_slug,
                backend_ref=backend_ref,
            )
        except IntegrityError:
            return gql_failure(ErrorCode.CONFLICT.value, "a project bundle with this slug already exists")
        return gql_success(secret_bundle_to_type(bundle))

    @strawberry.field
    @mutation_audit(action="project.secret.bundle.update")
    @requires_elevation(action_label="project.secret.bundle.update")
    @require_permission(
        Permission.PROJECT_UPDATE, Permission.SECRET_WRITE, scope=secret_bundle_project_scope("input.id")
    )
    @tenant_scoped()
    def update_project_secret_bundle(
        self,
        info: Info,
        input: UpdateProjectSecretBundleInput,
    ) -> MutationResultType[SecretBundleType]:
        bundle = _project_bundle_for_caller(input.id)
        if bundle is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project secret bundle not found")
        name = (input.name or "").strip()
        backend_ref = (input.backend_ref or "").strip()
        if not name or not backend_ref:
            return gql_failure(ErrorCode.VALIDATION.value, "name and backendRef are required")
        if len(name) > 200 or len(backend_ref) > 512:
            return gql_failure(ErrorCode.VALIDATION.value, "name or backendRef is too long")
        if backend_ref != bundle.backend_ref:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "project bundle backendRef is platform managed",
                field="backendRef",
            )
        bundle.name = name
        bundle.backend_ref = backend_ref
        bundle.save(update_fields=["name", "backend_ref", "updated_at", "version"])
        return gql_success(secret_bundle_to_type(bundle))

    @strawberry.field
    @mutation_audit(action="project.secret.bundle.delete")
    @requires_elevation(action_label="project.secret.bundle.delete")
    @require_permission(
        Permission.PROJECT_UPDATE, Permission.SECRET_WRITE, scope=secret_bundle_project_scope("bundle_id")
    )
    @tenant_scoped()
    def delete_project_secret_bundle(
        self,
        info: Info,
        bundle_id: GUID,
    ) -> MutationResultType[SecretBundleType]:
        bundle = _project_bundle_for_caller(bundle_id)
        if bundle is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project secret bundle not found")
        if (
            bundle.agent_refs.filter(deleted_at__isnull=True).exists()
            or bundle.app_refs.filter(deleted_at__isnull=True).exists()
        ):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "detach this bundle from every agent and app before deleting it",
            )
        backend, _caps, err = _project_bundle_backend(bundle)
        if err is not None:
            return err
        try:
            if backend.get(bundle.backend_ref) is not None:
                backend.delete(bundle.backend_ref)
        except Exception:  # noqa: BLE001
            return gql_failure(ErrorCode.INTERNAL.value, "secret bundle delete failed")
        payload = secret_bundle_to_type(bundle)
        bundle.soft_delete(by=_actor_user(info))
        return gql_success(payload)

    @strawberry.field
    @mutation_audit(action="project.secret.bundle.key.set")
    @requires_elevation(action_label="project.secret.bundle.key.set")
    @require_permission(
        Permission.PROJECT_UPDATE,
        Permission.SECRET_WRITE,
        scope=secret_bundle_project_scope("input.bundle_id"),
    )
    @tenant_scoped()
    def set_project_bundle_secret_value(
        self,
        info: Info,
        input: ProjectSecretBundleKeyInput,
    ) -> MutationResultType[SecretBundleType]:
        from astrolift_dispatch.agent_secrets import valid_agent_env_var

        bundle = _project_bundle_for_caller(input.bundle_id)
        if bundle is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project secret bundle not found")
        key = (input.key or "").strip()
        if not valid_agent_env_var(key) or len(key) > 255:
            return gql_failure(ErrorCode.VALIDATION.value, "key must be env-var safe", field="key")
        if input.value is None or input.value == "":
            return gql_failure(ErrorCode.VALIDATION.value, "value must not be empty", field="value")
        backend, _caps, err = _project_bundle_backend(bundle)
        if err is not None:
            return err
        try:
            with transaction.atomic():
                locked = SecretBundle.objects.select_for_update().get(pk=bundle.pk)
                payload = dict(backend.get(locked.backend_ref) or {})
                payload[key] = input.value
                backend.upsert(locked.backend_ref, payload)
                locked.last_known_keys = sorted(payload)
                locked.last_key_enum_at = timezone.now()
                locked.save(update_fields=["last_known_keys", "last_key_enum_at", "updated_at", "version"])
                bundle = locked
        except Exception:  # noqa: BLE001
            return gql_failure(ErrorCode.INTERNAL.value, "secret bundle write failed")
        return gql_success(secret_bundle_to_type(bundle))

    @strawberry.field
    @mutation_audit(action="project.secret.bundle.key.delete")
    @requires_elevation(action_label="project.secret.bundle.key.delete")
    @require_permission(
        Permission.PROJECT_UPDATE,
        Permission.SECRET_WRITE,
        scope=secret_bundle_project_scope("input.bundle_id"),
    )
    @tenant_scoped()
    def delete_project_bundle_secret_value(
        self,
        info: Info,
        input: ProjectSecretBundleKeyInput,
    ) -> MutationResultType[SecretBundleType]:
        bundle = _project_bundle_for_caller(input.bundle_id)
        if bundle is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project secret bundle not found")
        backend, _caps, err = _project_bundle_backend(bundle)
        if err is not None:
            return err
        try:
            with transaction.atomic():
                locked = SecretBundle.objects.select_for_update().get(pk=bundle.pk)
                payload = dict(backend.get(locked.backend_ref) or {})
                payload.pop(input.key, None)
                backend.upsert(locked.backend_ref, payload)
                locked.last_known_keys = sorted(payload)
                locked.last_key_enum_at = timezone.now()
                locked.save(update_fields=["last_known_keys", "last_key_enum_at", "updated_at", "version"])
                bundle = locked
        except Exception:  # noqa: BLE001
            return gql_failure(ErrorCode.INTERNAL.value, "secret bundle key delete failed")
        return gql_success(secret_bundle_to_type(bundle))

    @strawberry.field
    @mutation_audit(action="project.secret.bundle.key.reveal")
    @requires_elevation(action_label="project.secret.bundle.key.reveal")
    @require_permission(
        Permission.PROJECT_READ, Permission.SECRET_READ, scope=secret_bundle_project_scope("input.bundle_id")
    )
    @tenant_scoped()
    def reveal_project_bundle_secret_value(
        self,
        info: Info,
        input: ProjectSecretBundleKeyInput,
    ) -> MutationResultType[SecretBundleRevealType]:
        bundle = _project_bundle_for_caller(input.bundle_id)
        if bundle is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project secret bundle not found")
        backend, capabilities, err = _project_bundle_backend(bundle)
        if err is not None:
            return err
        if not capabilities["can_reveal"]:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                capabilities["read_limitation"] or "provider does not support reveal",
            )
        try:
            payload = backend.get(bundle.backend_ref) or {}
        except Exception:  # noqa: BLE001
            return gql_failure(ErrorCode.INTERNAL.value, "secret bundle read failed")
        if input.key not in payload:
            return gql_failure(ErrorCode.NOT_FOUND.value, "secret bundle key not found")
        return gql_success(
            SecretBundleRevealType(
                key=input.key,
                value=str(payload[input.key]),
                provider=capabilities["provider"],
                revealed_at=timezone.now(),
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.bundle.attach")
    @requires_elevation(action_label="app.secret.bundle.attach")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug"))
    @tenant_scoped()
    def attach_secret_bundle(
        self,
        info: Info,
        input: AttachSecretBundleInput,
    ) -> MutationResultType[AppSecretBundleAttachmentType]:
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=_caller_org_id()).first()
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
        bundle = SecretBundle.objects.filter(
            slug=input.bundle_slug,
            organization_id=_caller_org_id(),
            deleted_at__isnull=True,
        ).first()
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
        if bundle.project_id is not None and bundle.project_id != app.project_id:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "project bundles can only be attached inside their owning project",
            )
        if bundle.project_id is not None and bundle.tenant_cluster_id != env.tenant_cluster_id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "project bundles can only attach to environments on their secrets cluster",
            )
        # #488: gate on secret-approval policy.
        if app.requires_secret_approval:
            proposal = _maybe_create_proposal_for_write(
                app=app,
                op=SecretChangeProposal.Op.ATTACH_BUNDLE.value,
                payload={
                    "bundle_slug": input.bundle_slug,
                    "prefix": input.prefix or "",
                },
                environment_name=env.name,
                info=info,
            )
            # Surface the proposal id by reusing the attachment payload
            # shape: id is the proposal guid so the FE can route on it.
            return gql_success(
                AppSecretBundleAttachmentType(
                    id=GUID(str(proposal.guid)),
                    bundle_slug=bundle.slug,
                    bundle_name=bundle.name,
                    environment_name=env.name,
                    prefix=input.prefix or "",
                    registered_app_slug=app.slug,
                    team_slug=(bundle.team.slug if bundle.team_id else None),
                    key_count=len(bundle.last_known_keys or []),
                    merge_order=0,
                    attached_at=None,
                )
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
    @require_permission(Permission.APP_UPDATE, scope=bundle_attachment_app_scope("input.attachment_id"))
    @tenant_scoped()
    def detach_secret_bundle(
        self,
        info: Info,
        input: DetachSecretBundleInput,
    ) -> MutationResultType[_AttachmentRemovedPayload]:
        ref = (
            AppSecretBundleRef.objects.select_related(
                "registered_app",
                "app_environment",
            )
            .filter(
                guid=str(input.attachment_id),
                registered_app__organization_id=_caller_org_id(),
            )
            .first()
        )
        if ref is None or ref.deleted_at is not None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "attachment not found",
            )
        # #488: gate on secret-approval policy.
        app = ref.registered_app
        if app.requires_secret_approval:
            proposal = _maybe_create_proposal_for_write(
                app=app,
                op=SecretChangeProposal.Op.DETACH_BUNDLE.value,
                payload={"attachment_id": str(ref.guid)},
                environment_name=ref.app_environment.name,
                info=info,
            )
            return gql_success(
                _AttachmentRemovedPayload(
                    attachment_id=input.attachment_id,
                    deleted=False,
                    pending_proposal_id=GUID(str(proposal.guid)),
                )
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
    @require_permission(Permission.APP_UPDATE, scope=secret_bundle_project_scope("input.id"))
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
            organization_id=_caller_org_id(),
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
