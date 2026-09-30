from __future__ import annotations

from typing import Optional

import logging

import strawberry
from graphql import GraphQLError
from strawberry.types import Info

from core.schema.common import GlobalIDUtils, MutationResult, unpack_nested_errors

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@strawberry.input
class OrganizationInput:
    id: Optional[strawberry.ID] = None
    website: Optional[str] = None


@strawberry.input
class UpsertOrganizationInput:
    id: Optional[strawberry.ID] = None
    website: Optional[str] = None


@strawberry.input
class OrganizationMemberStatusInput:
    user_id: strawberry.ID
    is_active: bool
    organization_id: Optional[strawberry.ID] = None


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@strawberry.type
class OrganizationMutationResult(MutationResult):
    id: Optional[strawberry.ID] = None


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def _require_caller_in_org(info: Info, org_pk) -> None:
    """Reject the mutation unless the caller is an active member of org_pk.

    #537 (tenant-isolation sweep): the organization-side mutations
    previously accepted any logged-in caller — including users with no
    relation to the target org — and could be used to edit websites,
    upsert org rows, or toggle other-tenants' membership statuses.
    Superusers retain cross-tenant write privileges.
    """
    from organization.models import OrganizationMember

    user = info.context.user
    if not getattr(user, "is_authenticated", False):
        raise GraphQLError("Authentication required")
    from core.schema.legacy_access import is_operator_with_credential, require_account_access

    require_account_access(info)
    if is_operator_with_credential(user):
        return
    is_member = OrganizationMember.objects.filter(
        member=user,
        organization_id=org_pk,
        is_active=True,
        deleted_at__isnull=True,
    ).exists()
    if not is_member:
        raise GraphQLError("Caller is not a member of the target organization")


@strawberry.type
class Mutation:
    @strawberry.mutation
    def organization(self, info: Info, input: OrganizationInput) -> OrganizationMutationResult:
        from core.permissions import require_platform_operator

        require_platform_operator(info.context.user)
        raise GraphQLError(
            "Legacy organization writes are unavailable; use the Astrolift identity mutations."
        )

    @strawberry.mutation
    def upsert_organization(self, info: Info, input: UpsertOrganizationInput) -> OrganizationMutationResult:
        from core.permissions import require_platform_operator

        require_platform_operator(info.context.user)
        raise GraphQLError(
            "Legacy organization writes are unavailable; use the Astrolift identity mutations."
        )

    @strawberry.mutation
    def organization_member_status(
        self, info: Info, input: OrganizationMemberStatusInput
    ) -> MutationResult:
        """Activate or deactivate an organization member."""
        from core.permissions import require_platform_operator
        from organization.models import OrganizationMember
        from organization.serializers.organization_member import OrganizationMemberSerializer

        user = info.context.user
        if not getattr(user, 'is_authenticated', False):
            raise GraphQLError('Authentication required')
        # #1979: the serializer flips the global ``User.is_active``, which
        # ends the person's access in every Astrolift org, and nothing but
        # co-membership of this legacy org gated it. No org owns that
        # switch, so only the platform operator throws it, as #1864 made
        # ``profile``'s ``is_active`` path.
        require_platform_operator(user)

        # Resolve user_id from global ID
        user_pk = GlobalIDUtils.get_pk_flexible(input.user_id)
        if user_pk is None:
            raise GraphQLError(f'Invalid user ID: {input.user_id}')

        # Default organization_id to the requester's organization
        org_id = input.organization_id
        if org_id is None:
            org_id = str(user.profile.organization().id)
        else:
            org_id = GlobalIDUtils.get_pk_flexible(org_id) or org_id

        # #537: caller must be a member of the target org to flip another
        # member's status. Without this gate any authed user could flip
        # is_active on any membership row in any tenant.
        _require_caller_in_org(info, org_id)

        # Look up the membership
        instance = OrganizationMember.objects.filter(
            member_id=user_pk,
            organization_id=org_id,
        ).first()

        if instance is None:
            global_id = GlobalIDUtils.to_global_id('OrganizationType', org_id)
            raise GraphQLError(f'{input.user_id} is not a member of organization {global_id}')

        data = {
            'is_active': input.is_active,
            'user_id': user_pk,
            'organization_id': org_id,
            'updated_by_id': user.id,
        }

        serializer = OrganizationMemberSerializer(instance=instance, data=data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return MutationResult.success()

        errors = unpack_nested_errors(serializer.errors)
        return MutationResult(ok=False, errors=errors)
