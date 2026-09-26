"""Which namespace a new environment records, and who may not take it (#1922).

An environment that shares a cluster with another environment of its app
renders into a namespace of its own, so the two never write the same
objects. Only environments created from now on get one: every environment
that exists keeps a blank ``k8s_namespace`` and the app namespace. Because
the names are built by joining slugs, they collide the same way app
namespaces do (#1912), with apps and with each other.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.db import IntegrityError, transaction

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.namespaces import (
    adopt_preview_namespace,
    environment_namespace_taken,
    namespace_for_new_environment,
    namespace_refusal,
)
from astrolift_registry.schema.mutations.helpers import _bootstrap_app_environments

pytestmark = pytest.mark.django_db

_TWO_ENVIRONMENTS = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"
is_public = true

  [[workloads.containers]]
  name = "web"
  is_primary = true
  port = 8080

[[managed_services]]
kind = "redis"
name = "cache"
environment = "staging"

[[managed_services]]
kind = "redis"
name = "cache"
environment = "production"
"""


@pytest.fixture
def placed(org, app, cluster):
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster"])
    return SimpleNamespace(org=org, app=app, cluster=cluster)


def _second_cluster(org):
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="Other", slug="other-1922", capabilities_manifest={}, config_schema={})]
    )
    return TenantCluster.objects.create(
        organization=org,
        name="other",
        slug="other-1922",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://other.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def _pull_request(app, number, branch="feature"):
    from astrolift_scm.github_pr_dispatch import PrEventContext
    from astrolift_scm.webhook_views import _ensure_preview_environment

    preview, _ = _ensure_preview_environment(
        app,
        PrEventContext(
            raw_action="opened",
            repo_full_name="acme/hello-app",
            pr_number=number,
            head_sha="abc",
            head_branch=branch,
            is_merge=False,
            is_bot_author=False,
        ),
    )
    return preview


def test_the_first_environment_of_an_app_on_a_cluster_keeps_the_app_namespace(placed):
    assert namespace_for_new_environment(placed.app, name="production", cluster=placed.cluster) == ""


def test_a_second_environment_on_the_same_cluster_gets_its_own(placed, env):
    assert env.k8s_namespace == ""

    assert (
        namespace_for_new_environment(placed.app, name="staging", cluster=placed.cluster)
        == "acme-test-hello-app-staging"
    )


def test_an_environment_on_another_cluster_keeps_the_app_namespace(placed, env):
    other = _second_cluster(placed.org)

    assert namespace_for_new_environment(placed.app, name="staging", cluster=other) == ""


def test_bootstrap_creates_production_first_and_gives_the_other_environment_its_own(placed):
    """The manifest lists staging's service first. Production still takes
    the app namespace and hostname: whichever came first in the file is not
    a reason to move production's URL."""
    zone = ManagedDomain.objects.create(
        organization=placed.org,
        zone="apps.example.net",
        dns_driver="route53",
        default_for=ManagedDomain.DefaultFor.TENANT_APPS,
    )
    placed.app.manifest_raw = _TWO_ENVIRONMENTS
    placed.app.save(update_fields=["manifest_raw"])

    _bootstrap_app_environments(placed.app, [])

    production = AppEnvironment.objects.get(registered_app=placed.app, name="production")
    staging = AppEnvironment.objects.get(registered_app=placed.app, name="staging")
    assert production.pk < staging.pk
    assert (production.k8s_namespace, production.url) == ("", "https://hello-app.apps.example.net")
    assert (staging.k8s_namespace, staging.url) == (
        "acme-test-hello-app-staging",
        "https://hello-app-staging.apps.example.net",
    )
    assert production.managed_domain == staging.managed_domain == zone


def test_a_pr_preview_records_its_own_namespace(placed, env):
    preview = _pull_request(placed.app, 7)

    assert preview.namespace == "acme-test-hello-app-pr-7"
    assert preview.app_environment.k8s_namespace == preview.namespace


def test_a_manual_preview_records_its_own_namespace(
    placed, env, actor, fake_info, permission_resolver, temporal_recorder
):
    from core.permissions import Permission

    permission_resolver.grant(Permission.APP_DEPLOY)
    preview = _manual_preview(placed, actor, fake_info, branch="feature/login")

    assert preview.namespace == "acme-test-hello-app-feature-login"
    assert preview.app_environment.k8s_namespace == preview.namespace


def _manual_preview(placed, actor, fake_info, *, branch, environment_name=""):
    from astrolift_lifecycle.schema.mutations import (
        CreatePreviewEnvironmentInput,
        LifecycleMutation,
    )
    from core.tenancy import TenantContext, tenant_context

    with tenant_context(TenantContext(organization_id=placed.org.id, actor_user_id=actor.id)):
        result = LifecycleMutation().create_preview_environment(
            fake_info,
            input=CreatePreviewEnvironmentInput(
                app_slug=placed.app.slug, branch=branch, environment_name=environment_name
            ),
        )
    assert result.ok, result.errors
    return PreviewEnvironment.objects.get(registered_app=placed.app, is_manual=True, branch=branch)


def test_a_branch_named_like_a_pr_does_not_share_that_prs_namespace(
    placed, env, actor, fake_info, permission_resolver, temporal_recorder
):
    """``<org>-<app>-<branch>`` for branch ``pr-3`` is PR #3's
    ``<org>-<app>-pr-3``; both previews deploying there would be the
    collision all over again, and tearing one down would delete the
    other's namespace."""
    from core.permissions import Permission

    permission_resolver.grant(Permission.APP_DEPLOY)
    first = _pull_request(placed.app, 3)
    second = _manual_preview(placed, actor, fake_info, branch="pr-3", environment_name="preview-branch-pr-3")

    assert first.namespace == "acme-test-hello-app-pr-3"
    assert second.namespace != first.namespace
    assert second.namespace.startswith("acme-test-hello-app-pr-3-")
    assert second.app_environment.k8s_namespace == second.namespace


def test_an_environment_namespace_is_not_one_another_app_holds(placed, env, project, team):
    """App ``hello-app-staging`` of the same org already has namespace
    ``acme-test-hello-app-staging``: the join is not injective."""
    RegisteredApp.objects.create(
        organization=placed.org,
        project=project,
        team=team,
        name="Hello staging",
        slug="hello-app-staging",
        k8s_namespace="acme-test-hello-app-staging",
    )

    namespace = namespace_for_new_environment(placed.app, name="staging", cluster=placed.cluster)

    assert namespace != "acme-test-hello-app-staging"
    assert namespace.startswith("acme-test-hello-app-staging-")
    assert not environment_namespace_taken(namespace)


def test_an_app_that_never_recorded_its_namespace_still_holds_it(placed, env, project, team):
    """Apps registered before registration recorded ``k8s_namespace``
    compute theirs; the column alone would miss them."""
    RegisteredApp.objects.create(
        organization=placed.org,
        project=project,
        team=team,
        name="Hello staging",
        slug="hello-app-staging",
        k8s_namespace="",
    )

    assert environment_namespace_taken("acme-test-hello-app-staging")


def test_platform_namespaces_are_never_given_to_an_environment(placed):
    assert environment_namespace_taken("kube-system")
    assert environment_namespace_taken("astrolift-agents-acme")


def test_a_new_app_may_not_take_a_namespace_an_environment_holds(placed, env):
    preview = _pull_request(placed.app, 5)

    assert namespace_refusal(preview.namespace, organization_id=placed.org.id)
    assert namespace_refusal("acme-test-something-else", organization_id=placed.org.id) is None


def test_two_live_environments_cannot_record_one_namespace(placed, env):
    AppEnvironment.objects.create(
        registered_app=placed.app, tenant_cluster=placed.cluster, name="a", k8s_namespace="shared-ns"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        AppEnvironment.objects.create(
            registered_app=placed.app, tenant_cluster=placed.cluster, name="b", k8s_namespace="shared-ns"
        )


def test_blank_is_the_app_namespace_and_not_unique(placed, env):
    AppEnvironment.objects.create(registered_app=placed.app, tenant_cluster=placed.cluster, name="legacy")

    assert AppEnvironment.objects.filter(registered_app=placed.app, k8s_namespace="").count() == 2


def test_a_preview_created_without_a_namespace_adopts_its_own(placed, env):
    """What a server still on the previous release writes during the
    upgrade: a preview row with a namespace, an environment without one."""
    preview_env = AppEnvironment.objects.create(
        registered_app=placed.app, tenant_cluster=placed.cluster, name="preview-pr-9"
    )
    PreviewEnvironment.objects.create(
        registered_app=placed.app,
        pr_number=9,
        branch="feature",
        hostname="pr-9.hello-app.acme-test",
        namespace="acme-test-hello-app-pr-9",
        app_environment=preview_env,
    )

    adopt_preview_namespace(preview_env)

    preview_env.refresh_from_db()
    assert preview_env.k8s_namespace == "acme-test-hello-app-pr-9"


def test_adopting_leaves_a_non_preview_environment_in_the_app_namespace(env):
    adopt_preview_namespace(env)

    env.refresh_from_db()
    assert env.k8s_namespace == ""
