"""
Identity mutations.

Each mutation:

1. Returns ``MutationResultType`` — never raises.
2. Is wrapped with ``@require_permission(...)`` (resolver-entry check).
3. Is wrapped with ``@mutation_audit(...)`` so the audit row carries
   actor + target + decision automatically.

The two example mutations below cover the common shapes — `create`
returns the new entity inside ``data``; `softDelete` returns a
``deleted: bool`` payload.
"""

from __future__ import annotations

import strawberry

from astrolift_graphql import (
    GUID,
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.models import Organization
from astrolift_identity.schema.types import OrganizationType, organization_to_type
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.input
class CreateOrganizationInput:
    name: str
    slug: str
    website: str | None = None


@strawberry.input
class SoftDeleteOrganizationInput:
    id: GUID


@strawberry.type
class _SoftDeletePayload:
    id: GUID
    deleted: bool


@strawberry.type
class IdentityMutation:
    @strawberry.field
    @require_permission(Permission.ORG_UPDATE)
    @mutation_audit(action="org.create")
    def create_organization(
        self, info, input: CreateOrganizationInput
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
    @require_permission(Permission.ORG_DELETE)
    @mutation_audit(action="org.delete")
    def soft_delete_organization(
        self, info, input: SoftDeleteOrganizationInput
    ) -> MutationResultType[_SoftDeletePayload]:
        try:
            org = Organization.objects.get(guid=str(input.id))
        except Organization.DoesNotExist:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        tenant = get_current_tenant()
        actor_id = tenant.actor_user_id if tenant else None
        if actor_id is not None:
            from django.contrib.auth import get_user_model

            org.soft_delete(by=get_user_model().objects.filter(pk=actor_id).first())
        else:
            org.soft_delete()

        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
