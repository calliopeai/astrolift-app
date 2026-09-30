"""Live managed-resource owners and credential ceilings inside the active org."""

from __future__ import annotations

from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.scope_args import read_guid
from core.tenancy import get_current_tenant


def _org_id():
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def services_org_scope(_args=None):
    return PermissionScope(ScopeKind.ORG, _org_id() or 0)


def live_projects(qs):
    return qs.filter(
        organization_id=_org_id(),
        organization__deleted_at__isnull=True,
        team__organization_id=_org_id(),
        team__deleted_at__isnull=True,
    )


def _credential_scope(scope, permissions=()):
    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.scopes import app_scope_by_guid

    token = get_current_api_token()
    if token is None:
        return scope
    permission = permissions[0] if permissions else Permission.APP_READ
    if token.organization_id != _org_id():
        raise PermissionDenied(permission, scope, "credential belongs to another organization")
    if token.team_id is None:
        return scope
    if scope.kind == ScopeKind.APP:
        guid = (
            RegisteredApp.objects.filter(pk=scope.id, organization_id=_org_id())
            .values_list("guid", flat=True)
            .first()
        )
        for action in permissions or (Permission.APP_READ,):
            app_scope_by_guid(permission=action)({"app_id": str(guid)})
        return scope
    if scope.kind == ScopeKind.PROJECT:
        allowed = live_projects(Project.objects.filter(pk=scope.id)).filter(team_id=token.team_id).exists()
    elif scope.kind == ScopeKind.TEAM:
        allowed = Team.objects.filter(
            pk=scope.id,
            pk__in=[token.team_id],
            organization_id=_org_id(),
            organization__deleted_at__isnull=True,
        ).exists()
    else:
        allowed = False
    if not allowed:
        raise PermissionDenied(permission, scope, "resource is outside the credential's team")
    return scope


def services_project_scope_by_guid(field="project_id", *, permissions=()):
    def scope(args):
        from astrolift_identity.models import Project

        guid = read_guid(args, field)
        project_id = (
            live_projects(Project.objects.filter(guid=guid)).values_list("pk", flat=True).first()
            if guid
            else None
        )
        resolved = PermissionScope(ScopeKind.PROJECT, project_id) if project_id else services_org_scope()
        return _credential_scope(resolved, permissions)

    return scope


def live_managed_services(qs):
    from django.db.models import F, Q

    from astrolift_identity.models import Project
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.scopes import live_app_owners

    apps = live_app_owners(
        RegisteredApp.objects.filter(organization_id=_org_id(), organization__deleted_at__isnull=True)
    )
    projects = live_projects(Project.objects.all())
    return qs.filter(
        Q(
            registered_app__in=apps,
            app_environment__deleted_at__isnull=True,
            app_environment__registered_app_id=F("registered_app_id"),
            app_environment__tenant_cluster__deleted_at__isnull=True,
        )
        | Q(project__in=projects, tenant_cluster__deleted_at__isnull=True),
        Q(tenant_cluster__isnull=True)
        | Q(tenant_cluster__organization_id=_org_id())
        | Q(tenant_cluster__organization_id__isnull=True),
        Q(app_environment__isnull=True)
        | Q(app_environment__tenant_cluster__organization_id=_org_id())
        | Q(app_environment__tenant_cluster__organization_id__isnull=True),
    )


def assert_provider_cluster(cluster, *, permission=Permission.APP_READ):
    if cluster is None or cluster.deleted_at is not None or cluster.organization_id not in (None, _org_id()):
        raise PermissionDenied(
            permission,
            services_org_scope(),
            "managed resource provider target is not live in this organization",
        )


def managed_service_scope_by_guid(field="managed_service_id", *, permissions=()):
    def scope(args):
        from astrolift_services.models import ManagedService

        guid = read_guid(args, field)
        if guid:
            from django.db.models import Q

            target = (
                ManagedService.objects.filter(
                    Q(registered_app__organization_id=_org_id()) | Q(project__organization_id=_org_id()),
                    guid=guid,
                )
                .select_related("app_environment__tenant_cluster", "tenant_cluster")
                .first()
            )
            if target is not None:
                assert_provider_cluster(
                    target.effective_cluster,
                    permission=permissions[0] if permissions else Permission.APP_READ,
                )
        row = (
            live_managed_services(ManagedService.objects.filter(guid=guid))
            .values_list("registered_app_id", "project_id")
            .first()
            if guid
            else None
        )
        resolved = services_org_scope()
        if row:
            app_id, project_id = row
            resolved = (
                PermissionScope(ScopeKind.APP, app_id)
                if app_id
                else PermissionScope(ScopeKind.PROJECT, project_id)
            )
        return _credential_scope(resolved, permissions)

    return scope


def _app_scope_via(model_label: str, field: str, app_path="registered_app", *, permissions=()):
    def scope(args):
        from django.apps import apps

        from astrolift_registry.models import RegisteredApp
        from astrolift_registry.scopes import live_app_owners

        guid = read_guid(args, field)
        app_id = None
        if guid:
            owners = live_app_owners(
                RegisteredApp.objects.filter(organization_id=_org_id(), organization__deleted_at__isnull=True)
            )
            app_id = (
                apps.get_model(model_label)
                .objects.filter(guid=guid, **{f"{app_path}__in": owners})
                .values_list(f"{app_path}_id", flat=True)
                .first()
            )
        resolved = PermissionScope(ScopeKind.APP, app_id) if app_id else services_org_scope()
        return _credential_scope(resolved, permissions)

    return scope


def live_secret_bundles(qs):
    from django.db.models import F, Q

    from astrolift_identity.models import Project, Team

    teams = Team.objects.filter(organization_id=_org_id(), organization__deleted_at__isnull=True)
    return qs.filter(
        organization_id=_org_id(),
        organization__deleted_at__isnull=True,
    ).filter(
        Q(team_id__isnull=True) | Q(team__in=teams),
        Q(project_id__isnull=True) | Q(project__in=live_projects(Project.objects.all())),
        Q(project_id__isnull=True) | Q(team_id__isnull=True) | Q(team_id=F("project__team_id")),
    )


def secret_bundle_project_scope(field="bundle_id", *, permissions=()):
    """Project bundles use PROJECT; legacy team/org bundles retain their own scope."""

    def scope(args):
        from astrolift_services.models import SecretBundle

        guid = read_guid(args, field)
        row = (
            live_secret_bundles(SecretBundle.objects.filter(guid=guid))
            .values_list("project_id", "team_id")
            .first()
            if guid
            else None
        )
        resolved = services_org_scope()
        if row:
            project_id, team_id = row
            if project_id:
                resolved = PermissionScope(ScopeKind.PROJECT, project_id)
            elif team_id:
                resolved = PermissionScope(ScopeKind.TEAM, team_id)
        return _credential_scope(resolved, permissions)

    return scope


def bundle_attachment_app_scope(field="input.attachment_id", *, permissions=()):
    return _app_scope_via("astrolift_services.AppSecretBundleRef", field, permissions=permissions)


def secret_change_proposal_app_scope(field="input.proposal_id", *, permissions=()):
    return _app_scope_via("astrolift_services.SecretChangeProposal", field, permissions=permissions)


def managed_service_attachment_scope(field="input.attachment_id", *, permissions=()):
    def scope(args):
        from astrolift_services.models import ManagedServiceAttachment

        guid = read_guid(args, field)
        service_guid = (
            ManagedServiceAttachment.objects.filter(guid=guid)
            .values_list("managed_service__guid", flat=True)
            .first()
            if guid
            else None
        )
        return managed_service_scope_by_guid("service_id", permissions=permissions)(
            {"service_id": str(service_guid)}
        )

    return scope


def services_app_scope_by_slug(field="app_slug", *, permissions=()):
    from astrolift_registry.scopes import app_scope_by_slug

    def scope(args):
        resolved = None
        for action in permissions or (Permission.APP_READ,):
            resolved = app_scope_by_slug(field, permission=action)(args)
        return resolved

    return scope
