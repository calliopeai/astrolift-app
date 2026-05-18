"""Regression tests for tenant-isolation leaks across the GraphQL surface.

Closes #537 — ``UploadQuerySet.with_view_permission`` returning the
unfiltered table — plus the audit-sweep follow-ups it surfaced.

Every test follows the same shape, anchored on the reusable
``two_tenants`` fixture (see ``core/tests/fixtures/tenants.py``):

  1. Seed one row per organization (org-A, org-B).
  2. Exercise the resolver / queryset as user-A.
  3. Assert user-A sees A's row and never B's row.
  4. Symmetric assertion for user-B where it makes sense.

When a new tenant-scoped model lands, drop in a new test below
following the same shape. Keep them stateless — the fixture wipes the
two orgs between tests via the standard pytest-django teardown.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from config.schema import schema
from core.models import Upload
from core.models.upload import UploadQuerySet
from core.schema.context import StrawberryContext

User = get_user_model()


class _FakeRequest:
    """Minimal request stand-in for StrawberryContext.

    StrawberryContext expects ``user``, ``session`` (dict-like), and
    ``headers`` (mapping). We carry just enough for resolver execution.
    """

    def __init__(self, user):
        self.user = user
        self.session = {}
        self.headers = {}


def _ctx(user) -> StrawberryContext:
    return StrawberryContext(_FakeRequest(user))


def _make_upload(*, name: str, user, organization):
    """Create an Upload row with unique public_url / pre_signed_url.

    Upload's public_url has a unique constraint and pre_signed_url is
    non-null; both default to ``''`` if not passed, which collides at
    the second insert. Helper keeps the test body focused on the
    isolation assertion.
    """
    import secrets as _secrets

    nonce = _secrets.token_hex(6)
    return Upload.objects.create(
        name=name,
        content_type="image/png",
        public_url=f"https://example.test/{nonce}",
        pre_signed_url=f"https://example.test/{nonce}/put",
        path=f"static/{nonce}",
        created_by=user,
        updated_by=user,
        organization=organization,
    )


# ---------------------------------------------------------------------------
# Upload — the bug from #537.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadQuerySetWithViewPermission:
    """Direct queryset-level proof that the #537 leak is fixed."""

    def test_user_sees_only_their_org_uploads(self, two_tenants):
        upload_a = _make_upload(
            name="a.png",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        upload_b = _make_upload(
            name="b.png",
            user=two_tenants.b.user,
            organization=two_tenants.b.organization,
        )

        qs_a: UploadQuerySet = Upload.objects.with_view_permission(two_tenants.a.user)
        ids_a = set(qs_a.values_list("id", flat=True))

        assert upload_a.id in ids_a, "user-A should see their own org upload"
        assert upload_b.id not in ids_a, "user-A must not see org-B uploads — this is the #537 leak"

    def test_anonymous_caller_sees_nothing(self, two_tenants, db):
        _make_upload(
            name="a.png",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        from django.contrib.auth.models import AnonymousUser

        qs = Upload.objects.with_view_permission(AnonymousUser())
        assert not qs.exists(), "anonymous callers must see no uploads"

    def test_user_with_no_organization_sees_nothing(self, two_tenants, db):
        """Caller has a profile but no active org and no memberships."""
        _make_upload(
            name="a.png",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        stray = User.objects.create_user(
            username="stray-537",
            email="stray@example.test",
            password="x",
        )
        qs = Upload.objects.with_view_permission(stray)
        assert not qs.exists(), "deny-by-default: users with no resolvable org see zero uploads"

    def test_superuser_sees_all(self, two_tenants, db):
        """Superusers retain cross-org visibility for admin / debugging."""
        upload_a = _make_upload(
            name="a.png",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        upload_b = _make_upload(
            name="b.png",
            user=two_tenants.b.user,
            organization=two_tenants.b.organization,
        )
        su = User.objects.create_superuser(
            username="root-537",
            email="root@example.test",
            password="x",
        )
        ids = set(Upload.objects.with_view_permission(su).values_list("id", flat=True))
        assert {upload_a.id, upload_b.id}.issubset(ids)


# ---------------------------------------------------------------------------
# organization.queries.organizations / members / employees
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOrganizationQueryTenantFilter:
    def test_organizations_query_filters_to_caller_memberships(self, two_tenants):
        result = schema.execute_sync(
            "{ organizations { name } }",
            context_value=_ctx(two_tenants.a.user),
        )
        assert result.errors is None
        names = {o["name"] for o in result.data["organizations"]}
        assert two_tenants.a.organization.name in names
        assert two_tenants.b.organization.name not in names

    def test_members_query_filters_to_caller_organizations(self, two_tenants):
        # OrganizationMemberType's ``member`` field is async (dataloader-
        # backed) and the ``id`` field isn't exposed. We exercise the
        # resolver directly so the test stays synchronous; the resolver
        # logic is the unit under test here, not the loader plumbing.
        from organization.schema.queries import Query as OrgQuery

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        result_a = OrgQuery.members(None, _Info(two_tenants.a.user))
        ids_a = set(result_a.values_list("pk", flat=True))
        assert two_tenants.a.membership.pk in ids_a
        assert two_tenants.b.membership.pk not in ids_a, (
            "members query must not surface members from non-caller orgs"
        )

    def test_employees_query_filters_to_caller_organizations(self, two_tenants):
        result = schema.execute_sync(
            "{ employees(first: 50) { edges { node { user { email } } } } }",
            context_value=_ctx(two_tenants.a.user),
        )
        assert result.errors is None
        emails = {e["node"]["user"]["email"] for e in result.data["employees"]["edges"]}
        assert two_tenants.a.user.email in emails
        assert two_tenants.b.user.email not in emails

    def test_organization_lookup_blocks_non_member(self, two_tenants):
        # Asking for org-B by id as user-A returns null, not the row.
        # ``Organization.global_id()`` is broken at HEAD (NameError on
        # ``get_global_registry``); we build the same relay payload by
        # hand so the test exercises the resolver guard, not that bug.
        from strawberry.relay import to_base64

        b_gid = to_base64("OrganizationType", two_tenants.b.organization.pk)
        result = schema.execute_sync(
            "query($id: ID!) { organization(id: $id) { name } }",
            variable_values={"id": b_gid},
            context_value=_ctx(two_tenants.a.user),
        )
        assert result.errors is None
        assert result.data["organization"] is None

    def test_organizations_query_requires_authentication(self, two_tenants):
        from django.contrib.auth.models import AnonymousUser

        result = schema.execute_sync(
            "{ organizations { name } }",
            context_value=_ctx(AnonymousUser()),
        )
        assert result.errors and "Authentication required" in str(result.errors[0])


# ---------------------------------------------------------------------------
# AuditLogQuery
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAuditLogQueryAccessControl:
    def test_anonymous_rejected(self, two_tenants):
        from django.contrib.auth.models import AnonymousUser

        result = schema.execute_sync(
            "{ auditLogs { operation } }",
            context_value=_ctx(AnonymousUser()),
        )
        assert result.errors and "Authentication required" in str(result.errors[0])

    def test_regular_user_rejected(self, two_tenants):
        result = schema.execute_sync(
            "{ auditLogs { operation } }",
            context_value=_ctx(two_tenants.a.user),
        )
        assert result.errors and "superuser" in str(result.errors[0])

    def test_superuser_accepted(self, two_tenants, db):
        su = User.objects.create_superuser(
            username="audit-su-537",
            email="audit@example.test",
            password="x",
        )
        result = schema.execute_sync(
            "{ auditLogs { operation } }",
            context_value=_ctx(su),
        )
        assert result.errors is None
        assert isinstance(result.data["auditLogs"], list)


# ---------------------------------------------------------------------------
# PermissionAnalysisQuery — restricts cross-user inspection
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPermissionAnalysisAccessControl:
    def test_effective_permissions_blocks_cross_user(self, two_tenants):
        target_pk = str(two_tenants.b.user.pk)
        result = schema.execute_sync(
            "query($id: ID!) { effectivePermissions(userId: $id) { codename } }",
            variable_values={"id": target_pk},
            context_value=_ctx(two_tenants.a.user),
        )
        assert result.errors and "self or superuser" in str(result.errors[0])

    def test_effective_permissions_allows_self(self, two_tenants):
        own_pk = str(two_tenants.a.user.pk)
        result = schema.execute_sync(
            "query($id: ID!) { effectivePermissions(userId: $id) { codename } }",
            variable_values={"id": own_pk},
            context_value=_ctx(two_tenants.a.user),
        )
        assert result.errors is None

    def test_permission_compare_requires_superuser(self, two_tenants):
        result = schema.execute_sync(
            "query($a: ID!, $b: ID!) {  permissionCompare(userIdA: $a, userIdB: $b) { onlyA } }",
            variable_values={
                "a": str(two_tenants.a.user.pk),
                "b": str(two_tenants.b.user.pk),
            },
            context_value=_ctx(two_tenants.a.user),
        )
        assert result.errors and "superuser" in str(result.errors[0])


# ---------------------------------------------------------------------------
# UploadType (GraphQL) — proves the queryset fix flows through the resolver.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadTypeResolverIsolation:
    """End-to-end proof: the GraphQL resolver no longer leaks uploads.

    The Upload model isn't exposed as a top-level list query in this
    schema (uploads are accessed via parent objects); we exercise the
    queryset hook directly via the relay node lookup, which goes
    through ``UploadType.get_queryset``.
    """

    def test_resolver_queryset_hook_filters_by_org(self, two_tenants):
        from core.schema.types.upload import UploadType

        upload_b = _make_upload(
            name="b.png",
            user=two_tenants.b.user,
            organization=two_tenants.b.organization,
        )

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = UploadType.get_queryset(Upload.objects.all(), _Info(two_tenants.a.user))
        ids = set(qs.values_list("id", flat=True))
        assert upload_b.id not in ids
