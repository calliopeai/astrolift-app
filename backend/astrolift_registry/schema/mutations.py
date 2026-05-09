"""Mutations for the registry app: register/update/soft-delete app."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Project
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import RegisteredAppType, app_to_type
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.input
class RegisterAppInput:
    project_id: GUID
    name: str
    slug: str
    description: str | None = None
    source_kind: str = "github"
    source_repo: str
    source_url: str | None = None
    manifest_path: str | None = None
    default_branch: str | None = None
    deploy_branch: str | None = None
    trigger_mode: str | None = None


@strawberry.input
class UpdateAppInput:
    id: GUID
    name: str | None = None
    description: str | None = None
    source_url: str | None = None
    manifest_path: str | None = None
    default_branch: str | None = None
    deploy_branch: str | None = None
    trigger_mode: str | None = None
    preview_enabled: bool | None = None
    is_active: bool | None = None


@strawberry.input
class SetAppSubdomainInput:
    id: GUID
    subdomain: str


@strawberry.input
class SoftDeleteAppInput:
    id: GUID


@strawberry.type
class _SoftDeletePayload:
    id: GUID
    deleted: bool


def _actor():
    tenant = get_current_tenant()
    actor_id = tenant.actor_user_id if tenant else None
    if actor_id is None:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=actor_id).first()


@strawberry.type
class RegistryMutation:
    @strawberry.field
    @mutation_audit(action="app.create")
    @require_permission(Permission.APP_CREATE)
    @tenant_scoped()
    def register_app(self, info: Info, input: RegisterAppInput) -> MutationResultType[RegisteredAppType]:
        project = (
            Project.objects.select_related("organization", "team").filter(guid=str(input.project_id)).first()
        )
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found", field="projectId")

        if RegisteredApp.objects.filter(organization=project.organization, slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"app with slug {input.slug!r} already exists in this organization",
                field="slug",
            )

        if input.source_repo and input.manifest_path:
            if RegisteredApp.objects.filter(
                source_repo=input.source_repo,
                manifest_path=input.manifest_path,
            ).exists():
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    "this repo + manifest path is already registered",
                    field="sourceRepo",
                )

        app = RegisteredApp.objects.create(
            organization=project.organization,
            team=project.team,
            project=project,
            name=input.name.strip(),
            slug=input.slug,
            description=input.description or "",
            source_kind=input.source_kind or "github",
            source_repo=input.source_repo or "",
            source_url=input.source_url or "",
            manifest_path=input.manifest_path or "astrolift.toml",
            default_branch=input.default_branch or "main",
            deploy_branch=input.deploy_branch or input.default_branch or "main",
            trigger_mode=input.trigger_mode or "auto_on_push",
            k8s_namespace=f"{project.organization.slug}-{input.slug}",
            subdomain=input.slug,
        )
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.update")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_app(self, info: Info, input: UpdateAppInput) -> MutationResultType[RegisteredAppType]:
        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        for field in (
            "name",
            "description",
            "source_url",
            "manifest_path",
            "default_branch",
            "deploy_branch",
            "trigger_mode",
            "preview_enabled",
            "is_active",
        ):
            new_value = getattr(input, field)
            if new_value is not None:
                setattr(app, field, new_value)
        app.save()
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.set_subdomain")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_app_subdomain(
        self, info: Info, input: SetAppSubdomainInput
    ) -> MutationResultType[RegisteredAppType]:
        """Edit a registered app's subdomain without redeploy.

        The platform's hostname computation re-derives from
        ``app.subdomain`` on the next render — for live ingress
        traffic this needs an Ingress patch (handled by the
        SyncAppDomainWorkflow, separately tracked). This mutation is
        the source-of-truth update + collision check.

        Validation:
        - DNS label rules (lowercase letters, digits, hyphens)
        - Reserved name check (api / admin / etc — see hostname.py)
        - Within-org collision check (no two active apps in the same
          org may claim the same subdomain)

        Per spec 13 §6.
        """
        from astrolift_manifest.hostname import validate_subdomain_label

        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        try:
            new_subdomain = validate_subdomain_label(input.subdomain)
        except ValueError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value, str(exc), field="subdomain"
            )

        # Within-org collision: another active app already owning
        # this subdomain is a footgun (DNS would race for the same
        # label). Refuse with CONFLICT.
        clash = (
            RegisteredApp.objects.filter(
                organization_id=app.organization_id,
                subdomain=new_subdomain,
                deleted_at__isnull=True,
            )
            .exclude(pk=app.pk)
            .first()
        )
        if clash is not None:
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"another app in this org already uses {new_subdomain!r}",
                field="subdomain",
            )

        if app.subdomain != new_subdomain:
            app.subdomain = new_subdomain
            app.save(update_fields=["subdomain", "updated_at", "version"])
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.delete")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def soft_delete_app(
        self, info: Info, input: SoftDeleteAppInput
    ) -> MutationResultType[_SoftDeletePayload]:
        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        app.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
