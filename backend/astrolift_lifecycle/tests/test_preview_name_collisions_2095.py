"""Preview names and idempotency remain safe on real concurrent PostgreSQL requests."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection

from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
from astrolift_lifecycle.schema.mutations import CreatePreviewEnvironmentInput, LifecycleMutation
from astrolift_lifecycle.tests.test_environment_namespace_allocation_1922 import _pull_request
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ready(app, cluster, permission_resolver):
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster"])
    permission_resolver.grant(Permission.APP_DEPLOY)
    return app


def manual(app, info, branch="pr-3", name=""):
    with tenant_context(TenantContext(organization_id=app.organization_id)):
        return LifecycleMutation().create_preview_environment(
            info, input=CreatePreviewEnvironmentInput(app_slug=app.slug, branch=branch, environment_name=name)
        )


@pytest.mark.parametrize("pr_first", [True, False])
def test_both_creation_orders_allocate_distinct_names(ready, fake_info, temporal_recorder, pr_first):
    if pr_first:
        pr = _pull_request(ready, 3)
    result = manual(ready, fake_info)
    assert result.ok, result.errors
    if not pr_first:
        pr = _pull_request(ready, 3)
    branch = PreviewEnvironment.objects.get(registered_app=ready, is_manual=True)
    assert branch.app_environment.name != pr.app_environment.name
    assert branch.namespace != pr.namespace
    first = pr if pr_first else branch
    assert first.app_environment.name == "preview-pr-3"
    again = manual(ready, fake_info)
    assert again.ok
    assert _pull_request(ready, 3).pk == pr.pk
    assert PreviewEnvironment.objects.filter(registered_app=ready).count() == 2


@pytest.mark.parametrize("name", ["preview-pr-3", "x" * 129])
def test_explicit_collision_or_oversized_name_is_structured_validation(
    ready, fake_info, temporal_recorder, name
):
    _pull_request(ready, 3)
    result = manual(ready, fake_info, name=name)
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "environmentName"
    assert AppEnvironment.objects.filter(registered_app=ready).count() == 1


@pytest.mark.parametrize("kinds", [("pr", "manual"), ("pr", "pr"), ("manual", "manual")])
def test_concurrent_requests_serialize_name_allocation_and_idempotency(
    ready, fake_info, temporal_recorder, kinds
):
    barrier = Barrier(2)

    def run(kind):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            if kind == "pr":
                return _pull_request(ready, 3).pk
            result = manual(ready, fake_info)
            assert result.ok, result.errors
            return PreviewEnvironment.objects.get(registered_app=ready, is_manual=True).pk
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(run, kinds))
    expected = len(set(kinds))
    assert len(set(ids)) == expected
    rows = list(PreviewEnvironment.objects.filter(registered_app=ready).select_related("app_environment"))
    assert len(rows) == expected
    assert len({p.app_environment.name for p in rows}) == expected
    assert len({p.namespace for p in rows}) == expected
    assert (
        len(temporal_recorder.starts) == kinds.count("manual")
        if expected == 2
        else len(temporal_recorder.starts) <= 1
    )
