from __future__ import annotations

import logging
from typing import Optional

import strawberry
from django.core.exceptions import ValidationError
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
    if not getattr(user, 'is_authenticated', False):
        raise GraphQLError('Authentication required')
    if getattr(user, 'is_superuser', False):
        return
    is_member = OrganizationMember.objects.filter(
        member=user,
        organization_id=org_pk,
        is_active=True,
        deleted_at__isnull=True,
    ).exists()
    if not is_member:
        raise GraphQLError('Caller is not a member of the target organization')


@strawberry.type
class Mutation:

    @strawberry.mutation
    def organization(self, info: Info, input: OrganizationInput) -> OrganizationMutationResult:
        """Create or update an Organization via form."""
        from organization.models import Organization
        from organization.schema.mutations.organization import OrganizationForm

        user = info.context.user
        if not getattr(user, 'is_authenticated', False):
            raise GraphQLError('Authentication required')

        instance = None
        form_data = {}

        if input.id:
            pk = GlobalIDUtils.get_pk_flexible(input.id)
            instance = Organization.objects.filter(pk=pk).first()
            if not instance:
                raise GraphQLError(f'Organization {input.id} not found')
            # #537: editing an existing org requires membership in it.
            _require_caller_in_org(info, instance.pk)
            form_data['created_by'] = instance.created_by_id
        else:
            form_data['created_by'] = user.pk

        if input.website is not None:
            form_data['website'] = input.website

        form_data['last_modified_by'] = user.pk

        form = OrganizationForm(data=form_data, instance=instance)
        setattr(form, 'info', info)

        if form.is_valid():
            obj = form.save()
            obj = form.post_save(form_data)
            global_id = GlobalIDUtils.to_global_id('OrganizationType', obj.pk)
            return OrganizationMutationResult(ok=True, errors=[], id=global_id)

        return OrganizationMutationResult.from_form_errors(form.errors)

    @strawberry.mutation
    def upsert_organization(self, info: Info, input: UpsertOrganizationInput) -> OrganizationMutationResult:
        """Upsert an Organization using UtilityForm.apply_forms."""
        from core.schema.mutations.common import UtilityForm

        user = info.context.user
        if not getattr(user, 'is_authenticated', False):
            raise GraphQLError('Authentication required')
        # #537: editing an existing org requires membership in it. Creates
        # (no id supplied) are still allowed for any authed user, matching
        # the pre-existing create flow.
        if input.id is not None:
            target_pk = GlobalIDUtils.get_pk_flexible(input.id)
            if target_pk is not None:
                _require_caller_in_org(info, target_pk)

        # Build a dict matching the Graphene InputObjectType shape
        input_data = {}
        if input.id is not None:
            input_data['id'] = input.id
        if input.website is not None:
            input_data['website'] = input.website

        try:
            organization = UtilityForm.apply_forms(None, info, input_data)
        except ValidationError as exc:
            flat_errors = []
            if hasattr(exc, 'message_dict'):
                for field, msgs in exc.message_dict.items():
                    from core.schema.common import ValidationError as VE
                    flat_errors.append(VE(field=field, messages=[str(m) for m in msgs]))
            else:
                from core.schema.common import ValidationError as VE
                flat_errors.append(VE(field='__all__', messages=[str(exc)]))
            return OrganizationMutationResult(ok=False, errors=flat_errors)

        global_id = GlobalIDUtils.to_global_id('OrganizationType', organization.pk)
        return OrganizationMutationResult(ok=True, errors=[], id=global_id)

    @strawberry.mutation
    def organization_member_status(
        self, info: Info, input: OrganizationMemberStatusInput
    ) -> MutationResult:
        """Activate or deactivate an organization member."""
        from organization.models import OrganizationMember
        from organization.serializers.organization_member import OrganizationMemberSerializer

        user = info.context.user
        if not getattr(user, 'is_authenticated', False):
            raise GraphQLError('Authentication required')

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
