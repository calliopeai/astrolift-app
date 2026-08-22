"""`permissionGroupOperation` actually adds and removes group members (#1567).

It raised on every call. The resolver did:

    from core.schema import GroupType, UserType
    UserType.get_object(global_id=..., info=..., raise_not_found=True)

which is wrong twice over. Neither name is exported from ``core.schema``
(they live in ``core.schema.types.permissions`` and
``core.schema.types.user``), and neither type has a ``get_object``
classmethod -- both are plain ``strawberry_django.type`` wrappers. So the
import raised ImportError, and had it succeeded the attribute lookup would
have raised AttributeError.

It shipped in the published schema with zero tests, backing the Assignments
tab of the Permissions console. That is the whole lesson: the mutation was
registered, the screen called it, and nothing here ever executed it.

So these call the resolver and assert on group membership in the database,
not on the shape of the response.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from core.schema.common import GlobalIDUtils
from core.schema.mutations.permissions import GroupOperationInput, PermissionMutations

pytestmark = pytest.mark.django_db


class _Ctx:
    """Minimal Info stand-in: the resolver reads only ``context.user``."""

    def __init__(self, user):
        self.user = user


class _Info:
    def __init__(self, user):
        self.context = _Ctx(user)


@pytest.fixture
def superuser():
    # Superuser so the two check_django_auth_permission calls pass; the
    # permission gate is not what these tests are about.
    return get_user_model().objects.create_superuser(
        username="root-1567", email="root-1567@example.com", password="x"
    )


@pytest.fixture
def member():
    return get_user_model().objects.create_user(
        username="member-1567", email="member-1567@example.com", password="x"
    )


@pytest.fixture
def group():
    return Group.objects.create(name="editors-1567")


def _gid(type_name: str, pk) -> str:
    return GlobalIDUtils.to_global_id(type_name, pk)


def _call(user, *, user_ids, group_id, operation):
    return PermissionMutations().permission_group_operation(
        _Info(user),
        input=GroupOperationInput(user_ids=user_ids, group_id=group_id, operation=operation),
    )


def test_add_puts_the_user_in_the_group(superuser, member, group):
    """The regression. This call used to raise before touching anything."""
    result = _call(
        superuser,
        user_ids=[_gid("UserType", member.pk)],
        group_id=_gid("GroupType", group.pk),
        operation="ADD",
    )

    assert result.ok, result.errors
    assert group.user_set.filter(pk=member.pk).exists()


def test_remove_takes_the_user_out(superuser, member, group):
    group.user_set.add(member)

    result = _call(
        superuser,
        user_ids=[_gid("UserType", member.pk)],
        group_id=_gid("GroupType", group.pk),
        operation="REMOVE",
    )

    assert result.ok, result.errors
    assert not group.user_set.filter(pk=member.pk).exists()


def test_raw_integer_ids_also_resolve(superuser, member, group):
    """`get_pk_flexible` accepts a bare pk as well as a global id, and the
    console has historically sent both."""
    result = _call(
        superuser,
        user_ids=[str(member.pk)],
        group_id=str(group.pk),
        operation="ADD",
    )

    assert result.ok, result.errors
    assert group.user_set.filter(pk=member.pk).exists()


def test_several_users_in_one_call(superuser, group):
    users = [
        get_user_model().objects.create_user(username=f"bulk-{i}", email=f"bulk-{i}@example.com")
        for i in range(3)
    ]

    result = _call(
        superuser,
        user_ids=[_gid("UserType", u.pk) for u in users],
        group_id=_gid("GroupType", group.pk),
        operation="ADD",
    )

    assert result.ok, result.errors
    assert group.user_set.count() == 3


def test_a_missing_group_is_an_envelope_error_not_a_crash(superuser, member):
    """`Group.objects.get(pk=...)` in the serializer's save would raise
    DoesNotExist straight out of the resolver. Mutations return the
    envelope."""
    result = _call(
        superuser,
        user_ids=[_gid("UserType", member.pk)],
        group_id=_gid("GroupType", 987654),
        operation="ADD",
    )

    assert not result.ok
    assert result.errors
    assert result.errors[0].field == "groupId"


def test_a_missing_user_is_an_envelope_error_not_an_integrity_error(superuser, group):
    """`user_set.add(<bogus pk>)` raises IntegrityError, which would surface
    as a 500 and leave the caller unable to tell what was wrong."""
    result = _call(
        superuser,
        user_ids=[_gid("UserType", 987654)],
        group_id=_gid("GroupType", group.pk),
        operation="ADD",
    )

    assert not result.ok
    assert result.errors[0].field == "userIds"
    assert group.user_set.count() == 0


def test_a_global_id_of_the_wrong_type_is_refused(superuser, member, group):
    """A GroupType id where a user is expected must not silently resolve to
    whatever row shares that pk."""
    result = _call(
        superuser,
        user_ids=[_gid("GroupType", group.pk)],
        group_id=_gid("GroupType", group.pk),
        operation="ADD",
    )

    assert not result.ok
    assert result.errors[0].field == "userIds"


def test_an_unknown_operation_is_refused_by_the_serializer(superuser, member, group):
    result = _call(
        superuser,
        user_ids=[_gid("UserType", member.pk)],
        group_id=_gid("GroupType", group.pk),
        operation="OBLITERATE",
    )

    assert not result.ok
    assert result.errors
    # And nothing happened.
    assert group.user_set.count() == 0


def test_add_is_idempotent(superuser, member, group):
    ids = {"user_ids": [_gid("UserType", member.pk)], "group_id": _gid("GroupType", group.pk)}
    assert _call(superuser, operation="ADD", **ids).ok
    assert _call(superuser, operation="ADD", **ids).ok
    assert group.user_set.count() == 1
