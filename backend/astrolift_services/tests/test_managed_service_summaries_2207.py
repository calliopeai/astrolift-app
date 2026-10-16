"""astroliftManagedServiceSummariesPage and astroliftAppManagedService (#2207).

``astroliftManagedServicesPage`` bounds its response, not its work: every
row prefetches attachments, volume bindings and workload identity grants,
loads config, and resolves editable fields, whatever the client selects.
The summary page is the services table's bounded basic projection; the
exact read is how a row opens its full record without a full-list lookup.

Real Postgres, no DB mocks; resolvers are invoked directly inside a bound
tenant context, as ``test_managed_services_page_1235.py`` does.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from astrolift_services.models import (
    ManagedService,
    ManagedServiceAttachment,
    ManagedServiceVolumeBinding,
    WorkloadIdentityGrant,
)
from astrolift_services.schema.queries import ServicesQuery, _managed_service_summary_rows
from astrolift_services.tests.test_managed_services_page_1235 import _extra_env, _graph, _info, _svc
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

SECRET = "postgres://admin:hunter2@db.internal:5432 AccessDenied arn:aws:iam::123456789012:role/x"


def _summaries(graph, **kwargs):
    with tenant_context(TenantContext(organization_id=graph.org.id)):
        return ServicesQuery().astrolift_managed_service_summaries_page(
            _info(), app_slug=graph.app.slug, **kwargs
        )


def _exact(graph, id, *, app_slug=None):
    with tenant_context(TenantContext(organization_id=graph.org.id)):
        return ServicesQuery().astrolift_app_managed_service(
            _info(), app_slug=app_slug or graph.app.slug, id=id
        )


def _walk(graph, method, *, limit):
    names, cursor = [], None
    with tenant_context(TenantContext(organization_id=graph.org.id)):
        for _ in range(100):
            page = getattr(ServicesQuery(), method)(
                _info(), app_slug=graph.app.slug, limit=limit, after=cursor
            )
            names.extend(item.name for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return names
    raise AssertionError("walk did not terminate")


def test_more_than_200_services_stay_reachable_in_the_legacy_order(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    for n in range(251):
        _svc(a, f"svc-{n:03d}")

    names = _walk(a, "astrolift_managed_service_summaries_page", limit=50)

    assert len(names) == 251
    assert len(set(names)) == 251
    assert names == _walk(a, "astrolift_managed_services_page", limit=50)
    assert _summaries(a, limit=50).total_count == 251


def test_a_page_never_touches_nested_collections_or_the_config_document(permission_resolver):
    """The point of the projection: the work is bounded, not just the
    response. The legacy page issues one prefetch per nested collection
    and loads config and applied config on every row."""
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    for n in range(60):
        _svc(a, f"svc-{n:02d}")

    with CaptureQueriesContext(connection) as ctx:
        page = _summaries(a, limit=50)
    sql = "\n".join(q["sql"] for q in ctx.captured_queries)

    assert len(page.items) == 50
    for model in (ManagedServiceAttachment, ManagedServiceVolumeBinding, WorkloadIdentityGrant):
        assert model._meta.db_table not in sql, f"{model.__name__} was queried"
    assert '"applied_config"' not in sql
    assert '."config",' not in sql and '."config" FROM' not in sql
    # Count + page, plus whatever the visibility gates read: never one per row.
    assert len(ctx.captured_queries) <= 6

    with tenant_context(TenantContext(organization_id=a.org.id)):
        rows = list(_managed_service_summary_rows(app_slug=a.app.slug, environment_name=None)[:1])
    assert {"config", "applied_config"} <= rows[0].get_deferred_fields()


def test_raw_provider_text_never_reaches_the_summary(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    broken = _svc(a, "broken", status=ManagedService.Status.FAILED)
    broken.status_error = SECRET
    broken.save(update_fields=["status_error"])
    queued = _svc(a, "queued", status=ManagedService.Status.FAILED)
    queued.status_error = "could not enqueue managed-service update: dial tcp 10.0.0.4:7233"
    queued.save(update_fields=["status_error"])
    revoked = _svc(a, "revoked", status=ManagedService.Status.FAILED)
    revoked.status_error = "Revocation was observed; retained native source admission is unavailable."
    revoked.save(update_fields=["status_error"])
    _svc(a, "fine")

    by_name = {item.name: item for item in _summaries(a).items}

    assert by_name["fine"].status_failure is None
    assert by_name["broken"].status_failure.code == "provider_error"
    assert by_name["queued"].status_failure.code == "enqueue_failed"
    assert by_name["revoked"].status_failure.code == "source_revoked"
    rendered = repr([vars(item) for item in by_name.values()])
    assert "hunter2" not in rendered and "123456789012" not in rendered and "10.0.0.4" not in rendered


def test_posture_reads_only_the_badged_keys(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    svc = _svc(a, "uploads", kind=ManagedService.Kind.OBJECT_STORE, variant="s3")
    svc.config = {
        "public_access_blocked": True,
        "backup_enabled": "yes",
        "snapshot_policy": "daily",
        "password": "hunter2",
    }
    svc.save(update_fields=["config"])
    _svc(a, "empty")

    by_name = {item.name: item for item in _summaries(a).items}

    assert by_name["uploads"].posture.public_access_blocked is True
    assert by_name["uploads"].posture.backup_enabled is None, "a non-boolean is not a fact"
    assert by_name["uploads"].posture.snapshot_policy == "daily"
    assert by_name["empty"].posture.dkim_verified is None
    assert "hunter2" not in repr(vars(by_name["uploads"]))


def test_summary_carries_environment_and_cluster_lineage(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    staging = _extra_env(a, "staging")
    _svc(a, "staged", env=staging)

    item = _summaries(a, environment_name="staging").items[0]

    assert (item.environment_name, item.cluster_slug, item.registered_app_slug) == (
        "staging",
        a.cluster.slug,
        a.app.slug,
    )


def test_other_orgs_same_slug_app_is_invisible(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a", app_slug="shared-app")
    b = _graph("b", app_slug="shared-app")
    _svc(a, "ours")
    theirs = _svc(b, "theirs")

    page = _summaries(a)

    assert [i.name for i in page.items] == ["ours"]
    assert page.total_count == 1
    assert _exact(a, theirs.guid) is None


def test_exact_read_resolves_only_this_apps_live_row(permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    a = _graph("a")
    other = _graph("other")
    mine = _svc(a, "db")
    gone = _svc(a, "gone")
    gone.soft_delete()
    _svc(a, "gone")  # a same-name replacement must not answer for the deleted GUID
    sibling = _svc(other, "db")

    found = _exact(a, mine.guid)
    assert found is not None and str(found.id) == str(mine.guid)
    assert _exact(a, gone.guid) is None
    assert _exact(a, uuid.uuid4()) is None
    assert _exact(a, "not-a-guid") is None
    with tenant_context(TenantContext(organization_id=a.org.id)):
        assert (
            ServicesQuery().astrolift_app_managed_service(_info(), app_slug=a.app.slug, id=sibling.guid)
            is None
        )
