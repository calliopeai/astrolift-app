"""Permission mutations migrated from Graphene to Strawberry."""
from __future__ import annotations

import strawberry
from strawberry.types import Info

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from config.permissions import AbstractPermissions
from core.serializers.permissions import UserGroupSerializer
from core.schema.common import GlobalIDUtils, MutationResult, ValidationError
from core.systems import Action


@strawberry.input
class GroupOperationInput:
    user_ids: list[strawberry.ID]
    group_id: strawberry.ID
    operation: str


@strawberry.type
class PermissionMutations:

    @strawberry.mutation(description="Add or remove users from a permission group.")
    def permission_group_operation(self, info: Info, input: GroupOperationInput) -> MutationResult:
        user = info.context.user

        # Permission checks (mirrors RestrictedSerializerMutation.has_model_permissions)
        AbstractPermissions.check_django_auth_permission(user, 'user', Action.CHANGE, True)
        AbstractPermissions.check_django_auth_permission(user, 'group', Action.CHANGE, True)

        # Global IDs -> PKs.
        #
        # This was `from core.schema import GroupType, UserType` followed by
        # `Type.get_object(...)`, which was wrong twice over and raised on
        # every single call: neither name is exported from `core.schema`
        # (they live in `core.schema.types.permissions` and
        # `core.schema.types.user`), and neither type has a `get_object`
        # classmethod to call -- both are plain `strawberry_django.type`
        # wrappers. `GlobalIDUtils` was already imported in this module and
        # unused; it is what the other mutations resolve ids with.
        user_pks = []
        bad_user_ids = []
        for user_id in input.user_ids:
            pk = GlobalIDUtils.get_pk_flexible(user_id, expected_type='UserType')
            if pk is None:
                bad_user_ids.append(str(user_id))
            else:
                user_pks.append(pk)
        group_pk = GlobalIDUtils.get_pk_flexible(input.group_id, expected_type='GroupType')

        errors = []
        if bad_user_ids:
            errors.append(ValidationError(
                field='userIds',
                messages=[f'not a user id: {", ".join(bad_user_ids)}'],
            ))
        if group_pk is None:
            errors.append(ValidationError(
                field='groupId',
                messages=[f'not a group id: {input.group_id}'],
            ))
        if errors:
            return MutationResult(ok=False, errors=errors)

        # Existence is checked here rather than left to the serializer, whose
        # fields are plain CharFields, because the save path is
        # `Group.objects.get(pk=...)` + `user_set.add(*pks)`: a missing group
        # raises DoesNotExist and a missing user raises IntegrityError, both
        # of which would escape this resolver as a 500. Mutations return the
        # envelope, they do not raise.
        if not Group.objects.filter(pk=group_pk).exists():
            return MutationResult(ok=False, errors=[ValidationError(
                field='groupId', messages=[f'group {input.group_id} not found'],
            )])
        found = set(
            str(pk) for pk in
            get_user_model().objects.filter(pk__in=user_pks).values_list('pk', flat=True)
        )
        missing = [pk for pk in user_pks if str(pk) not in found]
        if missing:
            return MutationResult(ok=False, errors=[ValidationError(
                field='userIds',
                messages=[f'user(s) not found: {", ".join(str(m) for m in missing)}'],
            )])

        data = {
            'user_ids': user_pks,
            'group_id': group_pk,
            'operation': input.operation,
        }

        serializer = UserGroupSerializer(data=data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return MutationResult.success()
        else:
            return MutationResult.from_serializer_errors(serializer.errors)
