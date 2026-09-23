"""Legacy surfaces whose Django-permission authorization could not be kept (#1864).

The first two answered "allowed" to people who should never have been let
in, so an equivalence with the old outcome would have preserved the hole.

* ``profile(input)`` gated writes with Django field permissions and let
  any field without a permission record through. ``user { id, username,
  is_active }`` has none, so any logged-in user could rename or deactivate
  any other user, a superuser included. It is platform-operator only now;
  the web app edits its own profile through ``updateMyProfile``.
* ``/app/core/core/*`` is the Django admin's group-and-permission tooling.
  It sat behind ``login_required`` alone, so any logged-in user could list
  every user on the install, put themselves in any Django group and add
  any permission to a group. It now requires what the admin requires.
* The generic ``delete(gid)`` mutation hard-deletes whatever row the id
  names. Only seven models ever got past its Django permission check; the
  operator may still delete those and nothing else.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission as DjangoPermission
from django.test import Client

from core.schema.common import GlobalIDUtils

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


class _Request:
    def __init__(self, user):
        self.user = user
        self.session = {}
        self.headers = {}
        self.META = {}


_PROFILE = "mutation($i: JSON!) { profile(input: $i) { ok errors { field messages } } }"


def _edit_profile(actor, victim, **user_fields):
    from config.schema import schema
    from core.schema.context import StrawberryContext

    payload = {"user": {"id": GlobalIDUtils.to_global_id("UserType", victim.pk), **user_fields}}
    return schema.execute_sync(
        _PROFILE, variable_values={"i": payload}, context_value=StrawberryContext(_Request(actor))
    )


def test_a_plain_user_can_no_longer_rename_or_deactivate_someone_else():
    attacker = User.objects.create_user(username="attacker-1864", email="attacker@t.test", password="x")
    victim = User.objects.create_superuser(username="victim-1864", email="victim@t.test", password="x")

    # Twice: the first call only creates the draft profile, the second one
    # is the call that used to land.
    for _ in range(2):
        result = _edit_profile(attacker, victim, username="renamed-1864", is_active=False)
        assert result.errors, "the mutation must refuse a non-operator"

    victim.refresh_from_db()
    assert victim.username == "victim-1864"
    assert victim.is_active is True


def test_a_plain_user_cannot_edit_their_own_profile_through_it_either():
    """Self-edits went through the same field allowlist-by-absence (the
    ``switch_group`` field is writable there and gates impersonation), and
    ``updateMyProfile`` is the self-service path."""
    user = User.objects.create_user(username="self-1864", email="self@t.test", password="x")
    for _ in range(2):
        assert _edit_profile(user, user, username="self-renamed-1864").errors
    user.refresh_from_db()
    assert user.username == "self-1864"


def test_the_platform_operator_still_edits_any_profile():
    operator = User.objects.create_superuser(username="operator-1864", email="operator@t.test", password="x")
    someone = User.objects.create_user(username="someone-1864", email="someone@t.test", password="x")
    for _ in range(2):
        result = _edit_profile(operator, someone, username="someone-renamed-1864")
        assert result.errors is None, result.errors
        assert result.data["profile"]["ok"] is True
    someone.refresh_from_db()
    assert someone.username == "someone-renamed-1864"


# ---------------------------------------------------------------------------
# /app/core/core/* tooling
# ---------------------------------------------------------------------------


def _client(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def target_group():
    return Group.objects.create(name="admins-1864")


def _is_admin_login_redirect(response):
    return response.status_code == 302 and "/admin/login/" in response["Location"]


def test_a_plain_user_cannot_join_a_django_group(target_group):
    user = User.objects.create_user(username="joiner-1864", email="joiner@t.test", password="x")
    response = _client(user).post(
        "/app/core/core/toggle-group-membership/",
        {"user_id": user.pk, "group_id": target_group.pk, "add": "true"},
    )
    assert _is_admin_login_redirect(response)
    assert not user.groups.filter(pk=target_group.pk).exists()


def test_a_plain_user_cannot_add_a_permission_to_a_group(target_group):
    user = User.objects.create_user(username="granter-1864", email="granter@t.test", password="x")
    permission = DjangoPermission.objects.get(content_type__app_label="auth", codename="change_user")
    response = _client(user).post(
        "/app/core/core/toggle-permission-in-group/",
        {"group_id": target_group.pk, "permission_id": permission.pk, "add": "true"},
    )
    assert _is_admin_login_redirect(response)
    assert not target_group.permissions.exists()


def test_a_plain_user_cannot_list_the_installs_users():
    user = User.objects.create_user(username="lister-1864", email="lister@t.test", password="x")
    User.objects.create_user(username="elsewhere-1864", email="elsewhere@t.test", password="x")
    response = _client(user).get("/app/core/core/search-users/", {"q": "elsewhere"})
    assert _is_admin_login_redirect(response)
    assert b"elsewhere-1864" not in response.content


def test_staff_needs_the_admins_own_permission(target_group):
    staff = User.objects.create_user(username="staff-1864", email="staff@t.test", password="x", is_staff=True)
    client = _client(staff)
    post = {"user_id": staff.pk, "group_id": target_group.pk, "add": "true"}

    assert client.post("/app/core/core/toggle-group-membership/", post).status_code == 403
    assert not staff.groups.filter(pk=target_group.pk).exists()

    staff.user_permissions.add(
        DjangoPermission.objects.get(content_type__app_label="auth", codename="change_user")
    )
    staff = User.objects.get(pk=staff.pk)
    assert _client(staff).post("/app/core/core/toggle-group-membership/", post).status_code == 200
    assert staff.groups.filter(pk=target_group.pk).exists()


def test_the_superuser_keeps_the_tooling(target_group):
    operator = User.objects.create_superuser(username="tool-op-1864", email="tool-op@t.test", password="x")
    client = _client(operator)
    listed = client.get("/app/core/core/search-users/", {"q": "tool-op"})
    assert listed.status_code == 200 and b"tool-op-1864" in listed.content
    joined = client.post(
        "/app/core/core/toggle-group-membership/",
        {"user_id": operator.pk, "group_id": target_group.pk, "add": "true"},
    )
    assert joined.status_code == 200
    assert operator.groups.filter(pk=target_group.pk).exists()
    assert client.get("/app/core/core/user-permissions-tree-view/").status_code == 200


def test_every_tooling_route_refuses_whoever_the_admin_would():
    """Every route in ``core.urls``, so a new one cannot land open."""
    from django.urls import reverse

    from core import urls as core_urls

    paths = [reverse(pattern.name) for pattern in core_urls.urlpatterns]
    assert paths
    plain = User.objects.create_user(username="route-plain-1864", email="route-plain@t.test", password="x")
    staff = User.objects.create_user(
        username="route-staff-1864", email="route-staff@t.test", password="x", is_staff=True
    )
    anonymous, as_plain, as_staff = Client(), _client(plain), _client(staff)
    for path in paths:
        assert _is_admin_login_redirect(anonymous.get(path)), path
        assert _is_admin_login_redirect(as_plain.get(path)), path
        assert as_staff.get(path).status_code == 403, path


# ---------------------------------------------------------------------------
# Generic delete(gid)
# ---------------------------------------------------------------------------

_DELETE = "mutation($g: ID!) { delete(gid: $g) }"


def _generic_delete(actor, gid):
    from config.schema import schema
    from core.schema.context import StrawberryContext

    return schema.execute_sync(
        _DELETE, variable_values={"g": gid}, context_value=StrawberryContext(_Request(actor))
    )


def test_the_generic_delete_still_removes_a_legacy_row_for_the_operator_only():
    from core.models import Address

    plain = User.objects.create_user(username="del-plain-1864", email="del-plain@t.test", password="x")
    operator = User.objects.create_superuser(username="del-op-1864", email="del-op@t.test", password="x")
    address = Address.objects.create(city="Springfield")
    gid = GlobalIDUtils.to_global_id("AddressType", address.pk)

    assert _generic_delete(plain, gid).errors
    assert Address.objects.filter(pk=address.pk).exists()

    result = _generic_delete(operator, gid)
    assert result.errors is None, result.errors
    assert result.data == {"delete": True}
    assert not Address.objects.filter(pk=address.pk).exists()


def test_the_generic_delete_refuses_the_operator_a_model_it_never_deleted():
    """``SiteLabel`` had no permission in the retired catalogue, so this
    raised for everyone before. Operator-only on its own would let the
    operator hard-delete it through the API."""
    from core.models.internationalization import SiteLabel

    operator = User.objects.create_superuser(username="del-op2-1864", email="del-op2@t.test", password="x")
    label = SiteLabel.objects.create(key="kept-1864")

    result = _generic_delete(operator, GlobalIDUtils.to_global_id("SiteLabelType", label.pk))

    assert result.errors
    assert SiteLabel.objects.filter(pk=label.pk).exists()
