"""astroliftDeploymentsPage — cursor pagination over deploy history (#1235).

``astroliftDeployments`` caps at 200 rows and offers no way to reach the
201st: for an app that deploys on every merge, its own history becomes
unreachable from the UI within weeks. That is the operator-visible bug
behind #1230, and these tests are what stop it coming back.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.queries import LifecycleQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _deploy(app, env, tag, **kwargs):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=kwargs.pop("status", Deployment.Status.RUNNING.value),
        image_tag=tag,
        **kwargs,
    )


def _walk(query, org, *, limit, **kwargs):
    """Page through the whole stream, returning every image tag in order."""
    tags: list[str] = []
    cursor: str | None = None
    with tenant_context(TenantContext(organization_id=org.id)):
        for _ in range(50):  # bounded so a non-terminating walk fails loudly
            page = query.astrolift_deployments_page(_info(), limit=limit, after=cursor, **kwargs)
            tags.extend(item.image_tag for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return tags
    raise AssertionError("walk did not terminate")


def test_page_reaches_past_the_old_two_hundred_row_cap(app, env, org, permission_resolver):
    """The regression that motivated the epic: with the list field, row
    201 did not exist as far as the UI was concerned."""
    permission_resolver.grant(Permission.APP_READ)
    for n in range(205):
        _deploy(app, env, f"v{n:03d}")

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        capped = query.astrolift_deployments(_info(), limit=1000)
    assert len(capped) == 200, "precondition: the list field still caps"

    tags = _walk(query, org, limit=50)
    assert len(tags) == 205
    assert len(set(tags)) == 205, "a row was served twice"


def test_walk_is_newest_first_and_loses_nothing(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    for n in range(7):
        _deploy(app, env, f"v{n}")

    expected = list(
        Deployment.objects.filter(registered_app=app)
        .order_by("-created_at", "-guid")
        .values_list("image_tag", flat=True)
    )
    assert _walk(LifecycleQuery(), org, limit=3) == expected


def test_total_count_is_the_whole_result_set(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    for n in range(12):
        _deploy(app, env, f"v{n}")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_deployments_page(_info(), limit=5)
    assert len(page.items) == 5
    assert page.total_count == 12


def test_search_matches_commit_branch_and_tag(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "v1", branch="main", commit_message="fix the widget")
    _deploy(app, env, "v2", branch="feature/pagination", commit_message="add keyset walk")
    _deploy(app, env, "hotfix-tag", branch="main", commit_message="unrelated")

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        by_branch = query.astrolift_deployments_page(_info(), search="pagination")
        by_message = query.astrolift_deployments_page(_info(), search="widget")
        by_tag = query.astrolift_deployments_page(_info(), search="hotfix")

    assert [i.image_tag for i in by_branch.items] == ["v2"]
    assert [i.image_tag for i in by_message.items] == ["v1"]
    assert [i.image_tag for i in by_tag.items] == ["hotfix-tag"]
    assert by_branch.total_count == 1


def test_search_narrows_total_count_not_just_the_page(app, env, org, permission_resolver):
    """A count that ignored the search would render "3 results" over a
    one-row table."""
    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "keep-me", branch="main")
    _deploy(app, env, "other-1", branch="topic")
    _deploy(app, env, "other-2", branch="topic")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_deployments_page(_info(), search="keep-me")
    assert page.total_count == 1


def test_status_filter_applies(app, env, org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "ok", status=Deployment.Status.RUNNING.value)
    _deploy(app, env, "bad", status=Deployment.Status.FAILED.value)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_deployments_page(_info(), status=Deployment.Status.FAILED.value)
    assert [i.image_tag for i in page.items] == ["bad"]


def test_page_hides_deregistered_apps_like_the_list_field_does(
    app, env, org, project, team, cluster, permission_resolver
):
    """#1103's filter has to survive the conversion — both fields share
    one queryset builder so it cannot drift, and this pins that."""
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp

    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "live")

    dead_app = RegisteredApp.objects.create(
        organization=org, project=project, team=team, name="Dead", slug="dead-app"
    )
    dead_env = AppEnvironment.objects.create(registered_app=dead_app, tenant_cluster=cluster, name="prod")
    _deploy(dead_app, dead_env, "gone")
    dead_app.deleted_at = dead_app.created_at
    dead_app.save(update_fields=["deleted_at"])

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_deployments_page(_info(), limit=50)
    assert [i.image_tag for i in page.items] == ["live"]


def test_other_orgs_deployments_are_invisible(app, env, org, permission_resolver, provider_plugin):
    """Deployment reaches the org through registered_app and its manager
    is not tenant-aware, so the scope is the resolver's job (#1183)."""
    from astrolift_clusters.models import TenantCluster
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp

    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "ours")

    other_org = Organization.objects.create(name="Other", slug="other-org")
    other_team = Team.objects.create(organization=other_org, name="T", slug="t")
    other_project = Project.objects.create(organization=other_org, team=other_team, name="P", slug="p")
    other_cluster = TenantCluster.objects.create(
        organization=other_org,
        name="c",
        slug="c",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://other.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    other_app = RegisteredApp.objects.create(
        organization=other_org, project=other_project, team=other_team, name="X", slug="x-app"
    )
    other_env = AppEnvironment.objects.create(
        registered_app=other_app, tenant_cluster=other_cluster, name="prod"
    )
    _deploy(other_app, other_env, "theirs")

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_deployments_page(_info(), limit=50)
    assert [i.image_tag for i in page.items] == ["ours"]
    assert page.total_count == 1, "the count leaked the other org's row"


def test_no_tenant_context_is_refused_outright(app, env, permission_resolver):
    """Fails closed rather than returning every org's deploys (#1183).

    ``@tenant_scoped`` rejects first; the resolver's own ``org_id is
    None`` branch is defence in depth behind it. Both have to hold —
    this pins the outer one, and ``_deployments_qs`` filtering on
    ``organization_id=None`` matches no rows if it is ever reached.
    """
    from core.decorators import TenantRequired

    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "v1")

    with tenant_context(TenantContext(organization_id=None)):
        with pytest.raises(TenantRequired):
            LifecycleQuery().astrolift_deployments_page(_info(), limit=50)


def test_statuses_filter_expresses_a_tab_group(app, env, org, permission_resolver):
    """The /deployments tabs are status *groups* — "active" is in-flight
    plus running, minus the approval queue. Without a plural filter the
    surface has to fetch everything and split it client-side, which is
    the capped-then-filtered pattern #1230 exists to remove."""
    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "live", status=Deployment.Status.RUNNING.value)
    _deploy(app, env, "moving", status=Deployment.Status.DEPLOYING.value)
    _deploy(app, env, "queued", status=Deployment.Status.PENDING_APPROVAL.value)
    _deploy(app, env, "dead", status=Deployment.Status.FAILED.value)

    active = [
        Deployment.Status.PENDING.value,
        Deployment.Status.DEPLOYING.value,
        Deployment.Status.REDEPLOYING.value,
        Deployment.Status.RUNNING.value,
    ]
    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_deployments_page(_info(), statuses=active)

    assert sorted(i.image_tag for i in page.items) == ["live", "moving"]
    assert page.total_count == 2, "the count must narrow with the group, not just the page"


def test_is_preview_splits_pull_request_deployments(app, env, org, permission_resolver):
    """`isPreview` is `prNumber > 0` — the split the previews tab makes."""
    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "from-pr", pr_number=42)
    _deploy(app, env, "from-main", pr_number=0)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id)):
        previews = query.astrolift_deployments_page(_info(), is_preview=True)
        mainline = query.astrolift_deployments_page(_info(), is_preview=False)
        both = query.astrolift_deployments_page(_info())

    assert [i.image_tag for i in previews.items] == ["from-pr"]
    assert [i.image_tag for i in mainline.items] == ["from-main"]
    assert both.total_count == 2, "omitting the filter must not split anything"


def test_group_filters_compose(app, env, org, permission_resolver):
    """The active tab needs both at once: running, but not a preview."""
    permission_resolver.grant(Permission.APP_READ)
    _deploy(app, env, "live-main", status=Deployment.Status.RUNNING.value, pr_number=0)
    _deploy(app, env, "live-preview", status=Deployment.Status.RUNNING.value, pr_number=7)
    _deploy(app, env, "dead-main", status=Deployment.Status.FAILED.value, pr_number=0)

    with tenant_context(TenantContext(organization_id=org.id)):
        page = LifecycleQuery().astrolift_deployments_page(
            _info(), statuses=[Deployment.Status.RUNNING.value], is_preview=False
        )

    assert [i.image_tag for i in page.items] == ["live-main"]
    assert page.total_count == 1
