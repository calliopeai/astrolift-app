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
        assert (
            two_tenants.b.membership.pk not in ids_a
        ), "members query must not surface members from non-caller orgs"

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


# ---------------------------------------------------------------------------
# DataProcess — the bug from #542.
#
# Filed during the #537 audit-sweep and held back because the fix
# requires a data-model migration (DataProcess had no ``organization``
# FK). Tests mirror the Upload shape above: queryset isolation,
# resolver isolation, anonymous deny, no-org deny, superuser bypass,
# and entity-row symmetric coverage via the ``process__organization``
# traversal.
# ---------------------------------------------------------------------------


def _make_data_process(*, name: str, user, organization):
    """Create a DataProcess row in ``organization`` with the given
    creator. ``entity_type`` / ``file_type`` are not under test here,
    they're populated to satisfy the NOT-NULL constraints with stable
    values that show up in error messages."""
    from core.models.process import DataProcess, EntityType, FileType

    return DataProcess.objects.create(
        file_name=name,
        file_type=FileType.CSV,
        entity_type=EntityType.EMPLOYEE,
        created_by=user,
        updated_by=user,
        organization=organization,
    )


def _make_data_process_entity(*, process):
    """Create a child DataProcessEntity. The entity-type's queryset
    filter traverses ``process__organization`` so we don't need to
    pass an organization at the entity row itself."""
    from core.models.process import DataProcessEntity

    return DataProcessEntity.objects.create(
        process=process,
        line_number=1,
        data={"k": "v"},
    )


@pytest.mark.django_db
class TestDataProcessTypeTenantIsolation:
    """The #542 leak: DataProcessType.get_queryset returned the
    unfiltered table via ``permission_filtered_queryset`` (model-wide
    permission only, no row filter)."""

    def test_user_sees_only_their_org_data_processes(self, two_tenants):
        from core.models.process import DataProcess
        from core.schema.types.process import DataProcessType

        proc_a = _make_data_process(
            name="a.csv",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        proc_b = _make_data_process(
            name="b.csv",
            user=two_tenants.b.user,
            organization=two_tenants.b.organization,
        )

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = DataProcessType.get_queryset(DataProcess.objects.all(), _Info(two_tenants.a.user))
        ids = set(qs.values_list("pk", flat=True))
        assert proc_a.pk in ids
        assert proc_b.pk not in ids, "user-A must not see org-B DataProcess rows — this is the #542 leak"

    def test_anonymous_caller_sees_nothing(self, two_tenants):
        from django.contrib.auth.models import AnonymousUser

        from core.models.process import DataProcess
        from core.schema.types.process import DataProcessType

        _make_data_process(
            name="a.csv",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = DataProcessType.get_queryset(DataProcess.objects.all(), _Info(AnonymousUser()))
        assert not qs.exists()

    def test_user_with_no_organization_sees_nothing(self, two_tenants):
        from core.models.process import DataProcess
        from core.schema.types.process import DataProcessType

        _make_data_process(
            name="a.csv",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        stray = User.objects.create_user(
            username="stray-542",
            email="stray-542@example.test",
            password="x",
        )

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = DataProcessType.get_queryset(DataProcess.objects.all(), _Info(stray))
        assert not qs.exists(), "deny-by-default: no-org caller sees zero DataProcess rows"

    def test_superuser_sees_all(self, two_tenants):
        from core.models.process import DataProcess
        from core.schema.types.process import DataProcessType

        proc_a = _make_data_process(
            name="a.csv",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        proc_b = _make_data_process(
            name="b.csv",
            user=two_tenants.b.user,
            organization=two_tenants.b.organization,
        )
        su = User.objects.create_superuser(
            username="root-542",
            email="root-542@example.test",
            password="x",
        )

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = DataProcessType.get_queryset(DataProcess.objects.all(), _Info(su))
        ids = set(qs.values_list("pk", flat=True))
        assert {proc_a.pk, proc_b.pk}.issubset(ids)

    def test_row_with_null_organization_is_denied(self, two_tenants):
        """A DataProcess row whose ``organization`` is NULL (e.g. a
        legacy row the backfill couldn't resolve) is invisible to
        every non-superuser caller. Better than leaking it to the
        first user who happens to query the table."""
        from core.models.process import DataProcess
        from core.schema.types.process import DataProcessType

        orphan = _make_data_process(
            name="orphan.csv",
            user=two_tenants.a.user,
            organization=None,
        )

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = DataProcessType.get_queryset(DataProcess.objects.all(), _Info(two_tenants.a.user))
        assert orphan.pk not in set(qs.values_list("pk", flat=True))


@pytest.mark.django_db
class TestDataProcessEntityTypeTenantIsolation:
    """The entity rows reach the org via ``process__organization``.

    Same isolation requirement as DataProcessType; same fix shape but
    via a one-hop FK traversal."""

    def test_user_sees_only_their_org_entities(self, two_tenants):
        from core.models.process import DataProcessEntity
        from core.schema.types.process import DataProcessEntityType

        proc_a = _make_data_process(
            name="a.csv",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        proc_b = _make_data_process(
            name="b.csv",
            user=two_tenants.b.user,
            organization=two_tenants.b.organization,
        )
        ent_a = _make_data_process_entity(process=proc_a)
        ent_b = _make_data_process_entity(process=proc_b)

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = DataProcessEntityType.get_queryset(
            DataProcessEntity.objects.all(),
            _Info(two_tenants.a.user),
        )
        ids = set(qs.values_list("pk", flat=True))
        assert ent_a.pk in ids
        assert ent_b.pk not in ids, "user-A must not see entities of org-B's DataProcess"

    def test_anonymous_caller_sees_no_entities(self, two_tenants):
        from django.contrib.auth.models import AnonymousUser

        from core.models.process import DataProcessEntity
        from core.schema.types.process import DataProcessEntityType

        proc_a = _make_data_process(
            name="a.csv",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        _make_data_process_entity(process=proc_a)

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = DataProcessEntityType.get_queryset(
            DataProcessEntity.objects.all(),
            _Info(AnonymousUser()),
        )
        assert not qs.exists()


def _make_file_upload(*, name: str, user, organization):
    """Create an ``Upload`` + child ``FileUpload`` row in ``organization``.

    Mirrors ``_make_data_process`` for fixture style — the only thing
    under test is the queryset's tenant-scope behaviour."""
    # public_url has a unique constraint on the Upload table, so each
    # row needs a distinct value even in tests that don't care about
    # the URL itself. Use a UUID-derived path to guarantee uniqueness.
    import uuid

    from core.models.upload import FileUpload, Upload

    upload = Upload.objects.create(
        name=name,
        content_type="text/plain",
        location=Upload.Location.STATIC.value,
        public_url=f"https://test.invalid/{uuid.uuid4()}",
        organization=organization,
        created_by=user,
        updated_by=user,
    )
    file_upload = FileUpload.objects.create(
        upload=upload,
        created_by=user,
        updated_by=user,
    )
    return file_upload


class TestFileUploadTypeTenantIsolation:
    """The #723 leak: ``FileUploadType.get_queryset`` delegated to
    ``permission_filtered_queryset`` which only consults the model-wide
    ``view_fileupload`` permission and never filters by organization.

    The FK path is ``upload__organization`` because ``FileUpload`` is a
    join row hanging off ``Upload`` (which owns the org column)."""

    def test_user_sees_only_their_org_file_uploads(self, two_tenants):
        from core.models.upload import FileUpload
        from core.schema.types.upload import FileUploadType

        fu_a = _make_file_upload(
            name="a.txt",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        fu_b = _make_file_upload(
            name="b.txt",
            user=two_tenants.b.user,
            organization=two_tenants.b.organization,
        )

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = FileUploadType.get_queryset(FileUpload.objects.all(), _Info(two_tenants.a.user))
        ids = set(qs.values_list("pk", flat=True))
        assert fu_a.pk in ids
        assert fu_b.pk not in ids, "user-A must not see org-B FileUpload rows — this is the #723 leak"

    def test_anonymous_caller_sees_nothing(self, two_tenants):
        from django.contrib.auth.models import AnonymousUser

        from core.models.upload import FileUpload
        from core.schema.types.upload import FileUploadType

        _make_file_upload(
            name="a.txt",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = FileUploadType.get_queryset(FileUpload.objects.all(), _Info(AnonymousUser()))
        assert not qs.exists(), "anonymous callers must see no FileUpload rows"

    def test_superuser_bypass(self, two_tenants):
        from django.contrib.auth import get_user_model

        from core.models.upload import FileUpload
        from core.schema.types.upload import FileUploadType

        fu_a = _make_file_upload(
            name="a.txt",
            user=two_tenants.a.user,
            organization=two_tenants.a.organization,
        )
        fu_b = _make_file_upload(
            name="b.txt",
            user=two_tenants.b.user,
            organization=two_tenants.b.organization,
        )

        User = get_user_model()
        superuser = User.objects.create_superuser(
            username="root@astrolift.dev",
            email="root@astrolift.dev",
            password="ignored",
        )

        class _Info:
            def __init__(self, user):
                self.context = _ctx(user)

        qs = FileUploadType.get_queryset(FileUpload.objects.all(), _Info(superuser))
        ids = set(qs.values_list("pk", flat=True))
        assert {fu_a.pk, fu_b.pk}.issubset(
            ids
        ), "superusers retain cross-tenant read for support / audit access"
