"""`kinds` narrows the workload page server-side (#1242).

/functions, /tasks and the /jobs schedule list are each a single-kind view of a
fleet-wide field that had no kind argument. Filtering on the client left the
rows on screen correct and the page boundaries wrong: the count described the
whole org, and a page whose rows happened to contain none of the wanted kind
rendered empty with Next still enabled.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.queries import _workloads_qs
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _app(org, slug):
    """RegisteredApp requires a team and a project; both are NOT NULL."""
    team = Team.objects.create(organization=org, name=f"{slug} team", slug=f"{slug}-team")
    project = Project.objects.create(organization=org, team=team, name=slug.title(), slug=slug)
    return RegisteredApp.objects.create(
        organization=org, project=project, team=team, name=slug.title(), slug=slug
    )


@pytest.fixture
def app_with_workloads():
    org = Organization.objects.create(name="Acme", slug="acme")
    app = _app(org, "api")
    for slug, kind in (
        ("web", "deployment"),
        ("resize", "function"),
        ("thumbnail", "function"),
        ("import", "task"),
        ("nightly", "cronjob"),
    ):
        Workload.objects.create(registered_app=app, name=slug, slug=slug, kind=kind)
    return org, app


def _slugs(org, **kwargs):
    with tenant_context(TenantContext(organization_id=org.id)):
        return sorted(w.slug for w in _workloads_qs(app_slug=None, **kwargs))


def test_without_kinds_every_workload_is_returned(app_with_workloads):
    org, _ = app_with_workloads

    assert _slugs(org) == ["import", "nightly", "resize", "thumbnail", "web"]


def test_a_single_kind_narrows_to_it(app_with_workloads):
    org, _ = app_with_workloads

    assert _slugs(org, kinds=["function"]) == ["resize", "thumbnail"]


def test_several_kinds_union(app_with_workloads):
    """The /jobs surface wants cronjob and task together."""
    org, _ = app_with_workloads

    assert _slugs(org, kinds=["task", "cronjob"]) == ["import", "nightly"]


def test_an_empty_list_is_treated_as_no_filter(app_with_workloads):
    """`kinds: []` from a client that built its variables carelessly should not
    silently return nothing, which reads as "this install has no functions"."""
    org, _ = app_with_workloads

    assert _slugs(org, kinds=[]) == ["import", "nightly", "resize", "thumbnail", "web"]


def test_an_unknown_kind_returns_nothing_rather_than_everything(app_with_workloads):
    """Failing open here would show every workload on a single-kind page."""
    org, _ = app_with_workloads

    assert _slugs(org, kinds=["nosuchkind"]) == []


def test_kinds_is_not_what_search_does(app_with_workloads):
    """`search` ORs icontains across name, slug, kind and the app slug, so it
    matches a service merely named like the kind. That is the reason this
    argument exists rather than reusing search."""
    org, app = app_with_workloads
    Workload.objects.create(
        registered_app=app, name="function-gateway", slug="function-gateway", kind="deployment"
    )

    assert "function-gateway" in _slugs(org, search="function")
    assert "function-gateway" not in _slugs(org, kinds=["function"])


def test_kinds_still_respects_the_tenant_boundary(app_with_workloads):
    """The filter must narrow within the org, never widen across it (#1183)."""
    _, _ = app_with_workloads
    other = Organization.objects.create(name="Other", slug="other")
    other_app = _app(other, "x")
    Workload.objects.create(registered_app=other_app, name="theirs", slug="theirs", kind="function")

    assert _slugs(other, kinds=["function"]) == ["theirs"]


def test_no_tenant_context_matches_nothing_even_with_kinds():
    """Deny-by-default survives the new argument."""
    assert list(_workloads_qs(app_slug=None, kinds=["function"])) == []
