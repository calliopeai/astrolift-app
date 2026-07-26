"""SecretBundleMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
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
    DetachSecretBundleInput,
    RotateSecretBundleInput,
    _AttachmentRemovedPayload,
)
from astrolift_services.schema.types import (
    AppSecretBundleAttachmentType,
    SecretBundleType,
    attachment_to_type,
    secret_bundle_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission


@strawberry.type
class SecretBundleMutations:
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
    @require_permission(Permission.APP_UPDATE)
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
