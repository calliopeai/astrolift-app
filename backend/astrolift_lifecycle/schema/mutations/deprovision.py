"""DeprovisionMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_lifecycle.models import (
    CustomDomain,
)
from astrolift_lifecycle.schema.mutations.types import (
    ArchiveAppRegistryRepoInput,
    DeleteAppDnsRecordInput,
    DeleteAppIdentityRoleInput,
    DeleteAppIngressInput,
    RevokeAppCertificateInput,
    _CapabilityDeprovisionPayload,
)
from astrolift_lifecycle.scopes import custom_domain_app_scope
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_guid
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class DeprovisionMutations:
    # ---- Standalone capability deprovision (#368) ---------------

    @strawberry.field
    @mutation_audit(action="app.dns_record.delete")
    @require_permission(Permission.APP_DELETE, scope=app_scope_by_guid("input.app_id"))
    @tenant_scoped()
    def delete_app_dns_record(
        self,
        info: Info,
        input: DeleteAppDnsRecordInput,
    ) -> MutationResultType[_CapabilityDeprovisionPayload]:
        """Drop one DNS record on the app's bound cluster's
        ``DnsDriver`` (#368).

        Useful for cleaning up a stale CNAME the renderer once
        emitted but the operator has decommissioned, without dropping
        the whole app's DNS scaffolding. The activity splits the
        FQDN into ``(name, parent zone)`` itself and dispatches.
        """
        from astrolift_workflows.activities.capability_deprovision import (
            CapabilityDeprovisionError,
            _deprovision_dns_record_sync,
        )

        # Org-scope the by-guid lookup before the cloud DNS deprovision side
        # effect. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(
            guid=str(input.app_id), organization_id=org_id, deleted_at__isnull=True
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        host = (input.hostname or "").strip()
        if not host:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "hostname is required",
                field="hostname",
            )
        try:
            summary = _deprovision_dns_record_sync(
                registered_app_id=app.pk,
                hostname=host,
                record_type=input.record_type or "CNAME",
            )
        except CapabilityDeprovisionError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        return gql_success(
            _CapabilityDeprovisionPayload(
                app_id=input.app_id,
                cluster_slug=str(summary["cluster_slug"]),
                detail=(f"deleted {summary['type']} record {summary['name']!r} in zone {summary['zone']!r}"),
            ),
        )

    @strawberry.field
    @mutation_audit(action="app.certificate.revoke")
    @require_permission(Permission.APP_DELETE, scope=custom_domain_app_scope("input.custom_domain_id"))
    @tenant_scoped()
    def revoke_app_certificate(
        self,
        info: Info,
        input: RevokeAppCertificateInput,
    ) -> MutationResultType[_CapabilityDeprovisionPayload]:
        """Revoke the auto-issued cert for a ``CustomDomain`` and
        reset cert state so the renderer stops emitting the Ingress
        (#368).

        The ``CustomDomain`` row is preserved — operators may want to
        re-issue or BYO without re-adding the hostname. Clears
        ``certificate_id``, ``byo_certificate_pem``, and flips state
        to NOT_REQUESTED.
        """
        from astrolift_workflows.activities.capability_deprovision import (
            CapabilityDeprovisionError,
            _deprovision_certificate_sync,
        )

        # Org-scope the by-guid lookup before the cloud cert-revoke side
        # effect: CustomDomain reaches the org via registered_app. Fails
        # closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        domain = CustomDomain.objects.filter(
            guid=str(input.custom_domain_id),
            deleted_at__isnull=True,
            registered_app__organization_id=org_id,
        ).first()
        if domain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "custom domain not found")
        try:
            summary = _deprovision_certificate_sync(custom_domain_id=domain.pk)
        except CapabilityDeprovisionError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        cert_id = summary["certificate_id"]
        revoked = summary["revoked"]
        if cert_id and revoked:
            detail = f"revoked certificate {cert_id!r}; cert state reset to not_requested"
        elif cert_id:
            detail = f"could not revoke certificate {cert_id!r}; cert state reset to not_requested"
        else:
            detail = "no auto-issued certificate to revoke; cert state reset to not_requested"
        return gql_success(
            _CapabilityDeprovisionPayload(
                app_id=GUID(str(domain.registered_app.guid)),
                cluster_slug=str(summary["cluster_slug"]),
                detail=detail,
            ),
        )

    @strawberry.field
    @mutation_audit(action="app.identity_role.delete")
    @require_permission(Permission.APP_DELETE, scope=app_scope_by_guid("input.app_id"))
    @tenant_scoped()
    def delete_app_identity_role(
        self,
        info: Info,
        input: DeleteAppIdentityRoleInput,
    ) -> MutationResultType[_CapabilityDeprovisionPayload]:
        """Delete the cloud IAM/identity role bound to the app's
        ServiceAccount via the cluster's WorkloadIdentityDriver
        (#368).

        Subsequent deploys re-provision the role through the
        canonical onboarding path — pulling the role doesn't break
        the app permanently."""
        from astrolift_workflows.activities.capability_deprovision import (
            CapabilityDeprovisionError,
            _deprovision_identity_role_sync,
        )

        # Org-scope the by-guid lookup before the cloud IAM-role delete side
        # effect. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(
            guid=str(input.app_id), organization_id=org_id, deleted_at__isnull=True
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        try:
            summary = _deprovision_identity_role_sync(registered_app_id=app.pk)
        except CapabilityDeprovisionError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        return gql_success(
            _CapabilityDeprovisionPayload(
                app_id=input.app_id,
                cluster_slug=str(summary["cluster_slug"]),
                detail=f"deleted identity role {summary['role']!r}",
            ),
        )

    @strawberry.field
    @mutation_audit(action="app.registry_repo.archive")
    @require_permission(Permission.APP_DELETE, scope=app_scope_by_guid("input.app_id"))
    @tenant_scoped()
    def archive_app_registry_repo(
        self,
        info: Info,
        input: ArchiveAppRegistryRepoInput,
    ) -> MutationResultType[_CapabilityDeprovisionPayload]:
        """Archive the app's image repo on the registry + clear the
        platform's stored URI (#368).

        Default ``archive=True`` matches the registry SDK contract
        (ECR archives images instead of hard-deleting). Clearing the
        URI lets ``provision_registry_repo`` re-create a fresh repo
        on the next provision pass.
        """
        from astrolift_workflows.activities.capability_deprovision import (
            CapabilityDeprovisionError,
            _deprovision_registry_repo_sync,
        )

        # Org-scope the by-guid lookup before the registry-archive side
        # effect. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(
            guid=str(input.app_id), organization_id=org_id, deleted_at__isnull=True
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        try:
            summary = _deprovision_registry_repo_sync(
                registered_app_id=app.pk,
                archive=bool(input.archive),
            )
        except CapabilityDeprovisionError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        verb = "archived" if summary["archived"] else "deleted"
        return gql_success(
            _CapabilityDeprovisionPayload(
                app_id=input.app_id,
                cluster_slug=str(summary["cluster_slug"]),
                detail=f"{verb} registry repo {summary['repo']!r}; registry_repo_uri cleared",
            ),
        )

    @strawberry.field
    @mutation_audit(action="app.ingress.delete")
    @require_permission(Permission.APP_DELETE, scope=app_scope_by_guid("input.app_id"))
    @tenant_scoped()
    def delete_app_ingress(
        self,
        info: Info,
        input: DeleteAppIngressInput,
    ) -> MutationResultType[_CapabilityDeprovisionPayload]:
        """Delete one or every Ingress on the app's namespace (#368).

        ``hostname`` given → soft-delete the matching CustomDomain;
        the renderer drops its Ingress on the next deploy.
        ``hostname`` None → call ``IngressDriver.delete_ingress`` for
        every CustomDomain on the app's bound cluster, soft-deleting
        each row as it goes. Coordinate with #378 (pause/resume) —
        operators typically pause first, drop the ingress, then
        decide whether to resume on a different host.
        """
        from astrolift_workflows.activities.capability_deprovision import (
            CapabilityDeprovisionError,
            _deprovision_ingress_sync,
        )

        # Org-scope the by-guid lookup before the cloud ingress-delete side
        # effect. Fails closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(
            guid=str(input.app_id), organization_id=org_id, deleted_at__isnull=True
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        host = (input.hostname or "").strip() or None
        try:
            summary = _deprovision_ingress_sync(
                registered_app_id=app.pk,
                hostname=host,
            )
        except CapabilityDeprovisionError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        deleted = summary["deleted_hostnames"]
        if host:
            detail = f"soft-deleted custom domain {host!r}; ingress drops on next deploy"
        else:
            detail = (
                f"deleted {len(deleted)} ingress(es) on namespace "
                f"{summary['namespace']!r}: {', '.join(deleted)}"
                if deleted
                else f"no active ingresses on namespace {summary['namespace']!r}"
            )
        return gql_success(
            _CapabilityDeprovisionPayload(
                app_id=input.app_id,
                cluster_slug=str(summary["cluster_slug"]),
                detail=detail,
            ),
        )
