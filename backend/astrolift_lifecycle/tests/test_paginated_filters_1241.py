"""Server-side status filter for the previews page (#1241).

The Previews tab shipped four status pills, with the stated rationale that
"drowning in torn-down rows is the #1 reported friction". They were a
client-side predicate over one fetched list, so migrating to server pagination
had to drop them: filtering the page in hand reproduces exactly the bug #1230
exists to kill.

`search` matches `status`, so typing a status is a substitute for three of the
four pills. The default view is "everything except torn_down", a negation with
no expression at all, which is what this argument restores.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import PreviewEnvironment
from astrolift_lifecycle.schema.queries import _preview_environments_qs
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def previews(org, app, env):
    for branch, status in (
        ("feat-a", "running"),
        ("feat-b", "failed"),
        ("feat-c", "torn_down"),
        ("feat-d", "torn_down"),
    ):
        PreviewEnvironment.objects.create(
            registered_app=app, app_environment=env, branch=branch, status=status
        )
    return org, app, env


def _branches(org, **kwargs):
    with tenant_context(TenantContext(organization_id=org.id)):
        return sorted(p.branch for p in _preview_environments_qs(**kwargs))


def test_without_statuses_every_preview_is_returned(previews):
    org, _, _ = previews

    assert _branches(org, app_slug=None) == ["feat-a", "feat-b", "feat-c", "feat-d"]


def test_the_default_view_is_expressible_as_a_positive_list(previews):
    """ "Everything except torn_down" is a negation with no expression of its
    own, which is why the pills had to be dropped rather than ported.
    Enumerating the wanted statuses is how it comes back, and it is why the
    argument is a list rather than a single status."""
    org, _, _ = previews

    assert _branches(org, app_slug=None, statuses=["running", "failed"]) == ["feat-a", "feat-b"]


def test_a_single_status_pill(previews):
    org, _, _ = previews

    assert _branches(org, app_slug=None, statuses=["failed"]) == ["feat-b"]


def test_an_empty_list_is_treated_as_no_filter(previews):
    """A client that built its variables carelessly should not be told the app
    has no previews."""
    org, _, _ = previews

    assert len(_branches(org, app_slug=None, statuses=[])) == 4


def test_an_unknown_status_returns_nothing_rather_than_everything(previews):
    """Failing open would show torn-down rows on a pill that excludes them."""
    org, _, _ = previews

    assert _branches(org, app_slug=None, statuses=["nosuchstatus"]) == []


def test_statuses_composes_with_the_app_scope(previews):
    org, app, env = previews

    assert _branches(org, app_slug=app.slug, statuses=["running"]) == ["feat-a"]
    assert _branches(org, app_slug="not-a-real-app", statuses=["running"]) == []


def test_statuses_narrows_within_the_tenant_and_never_across_it(previews):
    """A filter must not become a way out of the org scope (#1183)."""
    org, _, _ = previews
    with tenant_context(TenantContext(organization_id=None)):
        assert list(_preview_environments_qs(app_slug=None, statuses=["running"])) == []
