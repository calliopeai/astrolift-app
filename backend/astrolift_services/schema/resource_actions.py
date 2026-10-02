"""Pin reviewed project-resource actions through their database writes (#2207)."""

from dataclasses import replace
from functools import wraps

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from astrolift_graphql import failure
from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ApiToken, Organization, Project, Team
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_services.schema.resource_reads import context_revision, context_row
from astrolift_services.scopes import _credential_scope
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.tenancy import get_current_tenant


def _stale():
    return failure("STALE_TARGET", "Managed resource context changed; review its exact GUID again")


def reviewed_resource_action(field, *, attachment=False):
    """Optional compatibility gate; reviewed clients supply revision on every action.

    Lock reviewed owners and target rows, reload metadata after waiting, and
    recheck current permissions before entering the existing mutation body.
    A request never resolves a resource by name or replaces a deleted GUID.
    """

    def decorate(fn):
        @wraps(fn)
        def wrapped(self, info, input):
            expected = getattr(input, "expected_context_revision", None)
            if expected is None:
                return fn(self, info, input)
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            if not org_id:
                return _stale()
            service_guid = getattr(input, field)
            selected_attachment = None
            if attachment:
                selected_attachment = (
                    ManagedServiceAttachment.objects.filter(
                        guid=service_guid,
                        managed_service__project__organization_id=org_id,
                    )
                    .values("pk", "managed_service_id")
                    .first()
                )
                if selected_attachment is None or input.managed_service_id is None:
                    return _stale()
                service_guid = input.managed_service_id
            initial = (
                ManagedService.objects.filter(
                    guid=service_guid,
                    project__organization_id=org_id,
                    registered_app__isnull=True,
                    organization__isnull=True,
                )
                .values("pk", "project_id", "tenant_cluster_id", "project__team_id")
                .first()
            )
            if initial is None:
                return _stale()
            from astrolift_clusters.models import TenantCluster

            with transaction.atomic():
                # Parent-first ordering is shared by every reviewed action.
                org = Organization.objects.select_for_update(no_key=True).only("pk").filter(pk=org_id).first()
                team = (
                    Team.objects.select_for_update(no_key=True)
                    .only("pk", "organization_id")
                    .filter(pk=initial["project__team_id"], organization_id=org_id)
                    .first()
                )
                project = (
                    Project.objects.select_for_update(no_key=True)
                    .only("pk", "organization_id", "team_id")
                    .filter(
                        pk=initial["project_id"], organization_id=org_id, team_id=initial["project__team_id"]
                    )
                    .first()
                )
                cluster = (
                    TenantCluster.objects.select_for_update(no_key=True)
                    .only("pk", "region")
                    .filter(pk=initial["tenant_cluster_id"])
                    .filter(Q(organization_id=org_id) | Q(organization_id__isnull=True))
                    .first()
                )
                locked = (
                    ManagedService.objects.select_for_update(of=("self",))
                    .filter(pk=initial["pk"], project__organization_id=org_id)
                    .values("project_id", "tenant_cluster_id")
                    .first()
                )
                if (
                    not all((org, team, project, cluster, locked))
                    or locked["project_id"] != initial["project_id"]
                    or locked["tenant_cluster_id"] != initial["tenant_cluster_id"]
                ):
                    return _stale()
                if attachment:
                    current_attachment = (
                        ManagedServiceAttachment.objects.select_for_update()
                        .filter(pk=selected_attachment["pk"], managed_service_id=initial["pk"])
                        .values("pk")
                        .first()
                    )
                    if current_attachment is None:
                        return _stale()
                token = get_current_api_token()
                marker = None
                try:
                    if token is not None:
                        current_token = (
                            ApiToken.objects.select_for_update()
                            .filter(
                                pk=token.pk,
                                user_id=tenant.actor_user_id,
                                organization_id=org_id,
                                is_revoked=False,
                            )
                            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
                            .first()
                        )
                        if current_token is None:
                            raise PermissionDenied(Permission.PROJECT_UPDATE, None, "Authentication required")
                        marker = set_current_api_token(current_token)
                    # Discard admission-time ancestry/policy facts after waiting.
                    from astrolift_services.schema.model_reads import _catalogue_audience

                    try:
                        _catalogue_audience(info)
                    except Exception:
                        raise PermissionDenied(
                            Permission.PROJECT_UPDATE, None, "Authentication required"
                        ) from None
                    attrs = replace(
                        abac.attributes_for(tenant.actor_user_id),
                        cache={},
                        environment=None,
                        region=cluster.region or None,
                        approvals=0,
                        now=timezone.now(),
                    )
                    with abac.request_attributes(attrs):
                        row = context_row(service_guid)
                        if row is None or context_revision(row) != expected:
                            return _stale()
                        with abac.operation_attributes(environment=row.effective_environment_name):
                            scope = _credential_scope(
                                PermissionScope(ScopeKind.PROJECT, project.pk), (Permission.PROJECT_UPDATE,)
                            )
                            check_permission(Permission.PROJECT_UPDATE, scope=scope)
                            return fn(self, info, input)
                finally:
                    if marker is not None:
                        reset_current_api_token(marker)

        return wrapped

    return decorate
