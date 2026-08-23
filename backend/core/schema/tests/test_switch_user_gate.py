"""``switchUser`` authorization gate (#1593).

Real Postgres only - no DB mocks (workspace rule).

The mutation writes another user's pk into the session. It carried no
``@require_permission``, no superuser check, and no class decorator; the only
schema extensions are the optimizer and an audit extension. The single thing
preventing any authenticated user from taking over any other account,
superusers included, was that ``_get_user_object`` imported
``core.schema.user`` - a module that does not exist - and raised ImportError
on every call.

So repairing that import (#1590) and gating the mutation had to land in one
commit: the import was load-bearing.

The gate is ``Profile.switch_group``, which the data model already declared
("Users in the same group are allowed to switch between them") and nothing
read.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from graphql import GraphQLError

from core.models import Profile, UserSwitchGroup
from core.schema.common import GlobalIDUtils
from core.schema.mutations.user import UserMutations, _may_switch_to

pytestmark = pytest.mark.django_db


def _user(username: str, *, group: UserSwitchGroup | None = None, superuser: bool = False) -> User:
    user = User.objects.create_user(username=username, is_superuser=superuser)
    profile, _ = Profile.objects.get_or_create(user=user)
    if group is not None:
        profile.switch_group = group
        profile.save(update_fields=["switch_group"])
    # create_user's signal already populated User._state.fields_cache["profile"]
    # with the group-less row, so re-fetch rather than hand back a stale cache.
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# the rule
# ---------------------------------------------------------------------------


def test_a_stranger_cannot_be_impersonated():
    """The finding. Before #1593 this was allowed by nothing but an ImportError."""
    caller = _user("caller")
    victim = _user("victim")

    assert _may_switch_to(caller, victim) is False


def test_a_stranger_cannot_impersonate_a_superuser():
    caller = _user("caller")
    root = _user("root", superuser=True)

    assert _may_switch_to(caller, root) is False


def test_members_of_one_switch_group_may_switch_between_them():
    group = UserSwitchGroup.objects.create()
    a = _user("a", group=group)
    b = _user("b", group=group)

    assert _may_switch_to(a, b) is True
    assert _may_switch_to(b, a) is True


def test_members_of_different_switch_groups_may_not():
    a = _user("a", group=UserSwitchGroup.objects.create())
    b = _user("b", group=UserSwitchGroup.objects.create())

    assert _may_switch_to(a, b) is False


def test_two_ungrouped_users_are_not_a_group():
    """The null-equals-null trap. Both columns are None and must not match:
    every user starts ungrouped, so treating that as a group would leave the
    mutation exactly as open as it was."""
    a = _user("a")
    b = _user("b")

    assert a.profile.switch_group_id is None
    assert b.profile.switch_group_id is None
    assert _may_switch_to(a, b) is False


def test_an_ungrouped_user_cannot_reach_a_grouped_one():
    caller = _user("caller")
    member = _user("member", group=UserSwitchGroup.objects.create())

    assert _may_switch_to(caller, member) is False


def test_a_superuser_may_switch_to_anyone():
    """Operator support work is what impersonation is for."""
    root = _user("root", superuser=True)
    victim = _user("victim")

    assert _may_switch_to(root, victim) is True


def test_switching_to_yourself_is_always_allowed():
    caller = _user("caller")

    assert _may_switch_to(caller, caller) is True


# ---------------------------------------------------------------------------
# the mutation enforces it
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, user, request):
        self.user = user
        self.request = request


class _Info:
    def __init__(self, user, request):
        self.context = _Ctx(user, request)


def test_the_mutation_refuses_an_ungated_target(rf):
    caller = _user("caller")
    victim = _user("victim")
    request = rf.post("/gql/")
    request.session = {}
    info = _Info(caller, request)

    gid = GlobalIDUtils.to_global_id("UserType", victim.pk)
    with pytest.raises(GraphQLError, match="not found"):
        UserMutations().switch_user(info, gid)

    # The session is untouched: no partial switch on the refused path.
    assert request.session == {}


def test_the_refusal_is_indistinguishable_from_a_missing_user(rf):
    """Confirming an id exists is its own disclosure, so both paths say the
    same thing."""
    caller = _user("caller")
    victim = _user("victim")
    request = rf.post("/gql/")
    request.session = {}
    info = _Info(caller, request)

    real = GlobalIDUtils.to_global_id("UserType", victim.pk)
    absent = GlobalIDUtils.to_global_id("UserType", victim.pk + 10_000)

    with pytest.raises(GraphQLError) as refused:
        UserMutations().switch_user(info, real)
    with pytest.raises(GraphQLError) as missing:
        UserMutations().switch_user(info, absent)

    assert str(refused.value).replace(real, "") == str(missing.value).replace(absent, "")


def test_the_helper_resolves_now_that_the_import_is_repaired():
    """Regression for #1590: this raised ImportError on every call."""
    from core.schema.mutations.user import _get_user_object

    victim = _user("victim")
    gid = GlobalIDUtils.to_global_id("UserType", victim.pk)

    assert _get_user_object(None, gid).pk == victim.pk
