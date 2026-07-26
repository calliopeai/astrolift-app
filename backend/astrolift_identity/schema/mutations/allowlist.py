"""AllowlistMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.db.models import Q
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
    OrganizationAllowlistedDomain,
    Role,
)
from astrolift_identity.schema.mutations.helpers import (
    _actor,
)
from astrolift_identity.schema.mutations.types import (
    AddOrganizationAllowlistDomainInput,
    RemoveOrganizationAllowlistDomainInput,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    OrganizationAllowlistedDomainType,
    organization_allowlisted_domain_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class AllowlistMutations:
    # ---- Domain allowlist --------------------------------------------

    @strawberry.field
    @mutation_audit(action="organization_allowlist_domain.add")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def add_organization_allowlist_domain(
        self, info: Info, input: AddOrganizationAllowlistDomainInput
    ) -> MutationResultType[OrganizationAllowlistedDomainType]:
        """Allowlist an email domain so SSO users from it auto-join
        this org on first sign-in. Optionally grants ``default_role``
        and/or routes the auto-join through admin review.
        """
        import re

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        domain = (input.domain or "").strip().lower()
        if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "domain must look like 'example.com' — letters, digits, dots, hyphens",
                field="domain",
            )

        role = None
        if input.default_role_slug:
            # Only the caller org's custom roles or a system/null-org role
            # may be the auto-join default — a foreign org's custom role
            # slug reads as not-found.
            role = (
                Role.objects.filter(slug=input.default_role_slug)
                .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
                .first()
            )
            if role is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "role not found",
                    field="defaultRoleSlug",
                )

        if OrganizationAllowlistedDomain.objects.filter(
            organization_id=org_id,
            domain=domain,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"domain {domain!r} is already on the allowlist",
                field="domain",
            )

        rule = OrganizationAllowlistedDomain.objects.create(
            organization_id=org_id,
            domain=domain,
            default_role=role,
            requires_review=bool(input.requires_review),
        )
        return gql_success(organization_allowlisted_domain_to_type(rule))

    @strawberry.field
    @mutation_audit(action="organization_allowlist_domain.remove")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def remove_organization_allowlist_domain(
        self, info: Info, input: RemoveOrganizationAllowlistDomainInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        rule = OrganizationAllowlistedDomain.objects.filter(
            guid=str(input.id),
            organization_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if rule is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "allowlist entry not found")
        rule.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
