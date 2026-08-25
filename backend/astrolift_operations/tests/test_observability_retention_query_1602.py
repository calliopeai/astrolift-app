"""The read surface the four retention columns never had (#1602 step 7).

An operator could set `metrics_retention_days_default` and had no way to
see what the platform would do with it. This is also the step that finally
gives `billable_window_days` and `warn_threshold_for` production callers --
the last two of the policy module's five public functions.
"""

from __future__ import annotations

import pytest

from astrolift_operations.observability_retention import ALL_STREAMS
from astrolift_operations.schema.queries import OperationsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Retention Q", slug="retention-q-org")


@pytest.fixture
def actor():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create(username="q@test", email="q@test")


@pytest.fixture
def fake_info(actor):
    from types import SimpleNamespace

    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=actor)))


def _query(org, actor, fake_info):
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        return OperationsQuery().astrolift_observability_retention(fake_info)


def test_it_returns_one_row_per_stream(org, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.ORG_READ)

    rows = _query(org, actor, fake_info)

    assert {r.stream for r in rows} == set(ALL_STREAMS)


def test_an_org_override_is_reported_as_an_override(org, actor, fake_info, permission_resolver):
    """`source` is what lets the UI say whether this org has actually set
    anything, rather than presenting an inherited default as a choice."""
    permission_resolver.grant(Permission.ORG_READ)
    org.log_retention_days_default = 45
    org.save()

    rows = {r.stream: r for r in _query(org, actor, fake_info)}

    assert rows["log"].days == 45
    assert rows["log"].source == "org_override"


def test_an_unset_column_is_reported_as_the_platform_default(org, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.ORG_READ)

    rows = {r.stream: r for r in _query(org, actor, fake_info)}

    assert rows["trace"].source in ("platform_default", "org_override")
    assert rows["trace"].days >= 1


def test_it_reports_what_the_sweep_will_enforce_not_what_was_typed(
    org, actor, fake_info, permission_resolver
):
    """Resolved through `effective_for`, not read off the column.

    Reading the column would show an operator the number they typed even
    when it sits above the platform ceiling, and the sweep would then evict
    on a different one -- the surface and the behaviour disagreeing about
    the same policy, which is a subtler version of the bug this issue is
    about.
    """
    from astrolift_operations.observability_retention import effective_for

    permission_resolver.grant(Permission.ORG_READ)
    org.log_retention_days_default = 45
    org.save()

    rows = {r.stream: r for r in _query(org, actor, fake_info)}
    expected = effective_for(stream="log", org_override_days=45)

    assert rows["log"].days == expected.days


def test_the_surface_reads_the_same_column_map_as_the_sweep(org, actor, fake_info, permission_resolver):
    """A surface reading one column while the sweep evicts on another is
    exactly the failure this issue is about, one level up. The map is
    imported rather than restated, and this proves the import is load
    bearing: change a mapping and both move together.
    """
    from astrolift_operations.observability_retention_sweep import STREAM_COLUMNS

    permission_resolver.grant(Permission.ORG_READ)
    org.metrics_retention_days_default = 123
    org.save()

    rows = {r.stream: r for r in _query(org, actor, fake_info)}

    assert STREAM_COLUMNS["metric_raw"] == "metrics_retention_days_default"
    assert rows["metric_raw"].days == 123


def test_the_last_two_policy_functions_now_have_a_caller(org, actor, fake_info, permission_resolver):
    """`billable_window_days` and `warn_threshold_for` were the two of five
    public functions still without a production call site after step 5."""
    permission_resolver.grant(Permission.ORG_READ)

    rows = _query(org, actor, fake_info)

    assert all(r.billable_window_days >= 1 for r in rows)
    assert all(r.warn_threshold_days >= 0 for r in rows)


def test_no_tenant_is_refused_by_the_decorator_before_the_resolver_runs(
    actor, fake_info, permission_resolver
):
    """Written expecting the resolver's own `org_id is None` branch to
    return `[]`, and it never gets there: `@tenant_scoped()` raises
    `TenantRequired` first.

    So that branch is defence in depth rather than the primary guard --
    which is worth knowing, because the repo's convention is that
    `@tenant_scoped()` asserts a context exists and does *not* filter, so
    the in-resolver check still has to be there for anything calling the
    resolver directly. Recording which one actually fires.
    """
    from core.decorators import TenantRequired

    permission_resolver.grant(Permission.ORG_READ)

    with pytest.raises(TenantRequired):
        OperationsQuery().astrolift_observability_retention(fake_info)


def test_it_requires_org_read(org, actor, fake_info, permission_resolver):
    from core.permissions import PermissionDenied

    permission_resolver.grant(Permission.APP_READ)

    with pytest.raises(PermissionDenied):
        _query(org, actor, fake_info)


def test_every_policy_function_now_has_a_production_call_site():
    """The ratchet closing this issue's premise out.

    The module landed with five public functions and none of them called.
    A sixth added later and left uncalled is the same defect, and nothing
    else in the repo would notice.
    """
    import ast
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[2]
    policy = backend / "astrolift_operations" / "observability_retention.py"
    tree = ast.parse(policy.read_text())
    public = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }

    sources: list[str] = []
    for path in backend.rglob("*.py"):
        parts = path.parts
        if "tests" in parts or path.name.startswith("test_") or path == policy:
            continue
        if "providers" in parts or "migrations" in parts:
            continue
        try:
            sources.append(path.read_text())
        except (UnicodeDecodeError, OSError):  # pragma: no cover
            continue
    blob = "\n".join(sources)

    uncalled = sorted(name for name in public if name not in blob)

    assert not uncalled, (
        "these policy functions have no non-test call site, so they are "
        "tested, green and never run:\n  " + "\n  ".join(uncalled)
    )
