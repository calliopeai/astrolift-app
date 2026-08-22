"""OrganizationMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import (
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.models import (
    Organization,
)
from astrolift_identity.schema.mutations.helpers import (
    _actor,
    _resolve_org,
)
from astrolift_identity.schema.mutations.types import (
    CreateOrganizationInput,
    SoftDeleteByGuidInput,
    UpdateOrganizationInput,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    OrganizationType,
    organization_to_type,
)
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.resource_tags import ResourceTagError, validate_resource_tags
from core.tenancy import get_current_tenant


@strawberry.type
class OrganizationMutations:
    @strawberry.field
    @mutation_audit(action="org.create")
    @require_permission(Permission.ORG_UPDATE)
    def create_organization(
        self, info: Info, input: CreateOrganizationInput
    ) -> MutationResultType[OrganizationType]:
        if Organization.objects.filter(slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"organization with slug {input.slug!r} already exists",
                field="slug",
            )
        org = Organization.objects.create(
            name=input.name,
            slug=input.slug,
            website=input.website or "",
        )
        return gql_success(organization_to_type(org))

    @strawberry.field
    @mutation_audit(action="org.update")
    @require_permission(Permission.ORG_UPDATE)
    def update_organization(
        self, info: Info, input: UpdateOrganizationInput
    ) -> MutationResultType[OrganizationType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        org = _resolve_org(input.id, org_id)
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        if input.name is not None:
            org.name = input.name
        if input.website is not None:
            org.website = input.website
        if input.audit_log_retention_days is not None:
            days = int(input.audit_log_retention_days)
            # Sane bound (spec ceiling ~7 years). The DB column is a
            # PositiveIntegerField, which still admits 0 and absurdly
            # large values; both the /administration/organization and
            # /administration/audit surfaces write this field, so guard
            # it here rather than trusting client-side min/max alone.
            if days < 1 or days > 2557:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "auditLogRetentionDays must be between 1 and 2557 (about 7 years)",
                    field="auditLogRetentionDays",
                )
            org.audit_log_retention_days = days
        if input.allow_user_profile_edit is not None:
            org.allow_user_profile_edit = input.allow_user_profile_edit
        if input.default_resource_tags is not None:
            # Refused here rather than at provision time. A tag that only
            # AWS accepts would otherwise provision fine for weeks and then
            # fail the first GCP resource, with the resource half made and
            # nothing pointing at the tag as the cause.
            try:
                org.default_resource_tags = validate_resource_tags(input.default_resource_tags)
            except ResourceTagError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    str(exc),
                    field="defaultResourceTags",
                )
        if input.managed_service_isolation_policy is not None:
            from astrolift_drivers.isolation import IsolationError, parse_policy
            from astrolift_services.models import ManagedService

            raw = input.managed_service_isolation_policy
            if not isinstance(raw, dict):
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "managedServiceIsolationPolicy must be an object mapping kind to mode",
                    field="managedServiceIsolationPolicy",
                )
            known_kinds = {kind for kind, _label in ManagedService.Kind.choices}
            unknown = sorted(str(kind) for kind in raw if str(kind) not in known_kinds)
            if unknown:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"managedServiceIsolationPolicy names unknown kinds {unknown}",
                    field="managedServiceIsolationPolicy",
                )
            try:
                policy = parse_policy(raw)
            except IsolationError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    str(exc),
                    field="managedServiceIsolationPolicy",
                )
            org.managed_service_isolation_policy = {kind: mode.value for kind, mode in policy.items()}
        org.save()
        return gql_success(organization_to_type(org))

    @strawberry.field
    @mutation_audit(action="org.delete")
    @require_permission(Permission.ORG_DELETE)
    def soft_delete_organization(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        org = _resolve_org(input.id, org_id)
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")
        org.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
