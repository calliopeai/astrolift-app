"""The doctor answers "is this app built?" honestly (#1550).

`autowire_state` covered the repo half -- webhook, CI file, deploy secret --
and nothing checked the cloud half, so an app could read as autowired while
its registry repo, push role, pod identity or latest image did not exist.

The load-bearing property is the distinction between UNKNOWN and PASS. A
check whose answer needs a live cloud call reports UNKNOWN, never PASS: "we
did not look" and "we looked and it is fine" are different answers, and
collapsing them is how a doctor panel becomes a green light nobody trusts.
"""

from __future__ import annotations

import pytest

from astrolift_registry.app_doctor import CheckStatus, diagnose, is_healthy

pytestmark = pytest.mark.django_db


@pytest.fixture
def app(db):
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_registry.models import RegisteredApp

    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Core", slug="core")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="hello",
        slug="hello",
        manifest_hash="sha256:m1",
        last_synced_hash="sha256:m1",
        registry_repo_uri="123.dkr.ecr.us-west-2.amazonaws.com/acme/hello",
        push_role_ref="arn:aws:iam::123456789012:role/astrolift-hello-push",
        autowire_state={"webhook": "present", "ci_workflow": "present", "secrets": "present"},
    )


def _env_for(app):
    """An AppEnvironment needs a cluster; the FK is non-null."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_lifecycle.models import AppEnvironment

    plugin = ProviderPlugin.objects.create(name="aws", slug="aws", capabilities_manifest={}, config_schema={})
    cluster = TenantCluster.objects.create(
        organization=app.organization,
        slug=f"{app.slug}-cluster",
        name=f"{app.slug}-cluster",
        provider_plugin=plugin,
        provider_config={"account_id": "123456789012", "region": "us-west-2"},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return AppEnvironment.objects.create(registered_app=app, name="prod", tenant_cluster=cluster)


def _by_key(app):
    return {c.key: c for c in diagnose(app)}


def test_a_fully_wired_app_has_no_failures(app):
    checks = diagnose(app)

    assert is_healthy(checks)
    assert {c.key for c in checks} == {
        "manifest",
        "repo_wiring",
        "registry",
        "push_role",
        "identity",
        "image",
        "deployments",
    }


def test_the_order_is_stable(app):
    """So the panel does not reorder between loads and a diff of two
    diagnoses is readable."""
    assert [c.key for c in diagnose(app)] == [c.key for c in diagnose(app)]


# ---- the checks that catch real breakage --------------------------------


def test_a_missing_registry_repo_fails(app):
    app.registry_repo_uri = ""
    app.save(update_fields=["registry_repo_uri"])

    assert _by_key(app)["registry"].status is CheckStatus.FAIL
    assert not is_healthy(diagnose(app))


def test_a_missing_push_role_fails(app):
    app.push_role_ref = ""
    app.save(update_fields=["push_role_ref"])

    assert _by_key(app)["push_role"].status is CheckStatus.FAIL


def test_a_manifest_that_drifted_from_the_repo_fails(app):
    app.last_synced_hash = "sha256:m2"
    app.save(update_fields=["last_synced_hash"])

    check = _by_key(app)["manifest"]
    assert check.status is CheckStatus.FAIL
    assert "resync" in check.fix


def test_autowire_errors_surface_with_their_detail(app):
    app.autowire_state = {"errors": {"webhook": "403 from host"}}
    app.save(update_fields=["autowire_state"])

    check = _by_key(app)["repo_wiring"]
    assert check.status is CheckStatus.FAIL
    assert "403 from host" in check.detail


def test_a_stranded_deployment_fails(app):
    from astrolift_lifecycle.models import Deployment

    env = _env_for(app)
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        status=Deployment.Status.DEPLOYING.value,
        image_tag="v1",
    )

    check = _by_key(app)["deployments"]
    assert check.status is CheckStatus.FAIL
    assert "stuck in flight" in check.detail


def test_an_unpinned_image_fails(app):
    """Knowable from the row: a deployment on a floating tag cannot be
    reproduced, whatever the registry currently holds."""
    from astrolift_lifecycle.models import Deployment

    env = _env_for(app)
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        status=Deployment.Status.RUNNING.value,
        image_tag="latest",
    )

    assert _by_key(app)["image"].status is CheckStatus.FAIL


# ---- UNKNOWN is not PASS ------------------------------------------------


def test_a_never_autowired_app_is_unknown_not_failed(app):
    """It has not failed at anything; nobody has run autowire yet."""
    app.autowire_state = {}
    app.save(update_fields=["autowire_state"])

    check = _by_key(app)["repo_wiring"]
    assert check.status is CheckStatus.UNKNOWN
    assert is_healthy(diagnose(app)), "UNKNOWN must not report the app broken"


def test_an_unbound_app_has_unknown_identity(app):
    check = _by_key(app)["identity"]

    assert check.status is CheckStatus.UNKNOWN


def test_a_never_deployed_app_has_no_image_to_check(app):
    assert _by_key(app)["image"].status is CheckStatus.NOT_APPLICABLE


def test_the_push_role_check_says_it_did_not_verify_trust():
    """#1532 was a role that existed and refused every assume. Reporting a
    bare PASS here would claim the thing that bug disproved."""
    from astrolift_registry.app_doctor import _check_push_role

    class _App:
        push_role_ref = "arn:aws:iam::1:role/x"

    assert "not verified" in _check_push_role(_App()).detail


def test_a_cluster_missing_its_account_id_fails_identity(app):
    """The real defect this catches: `_workload_identity_annotations` returns
    `{}` for an AWS cluster with no `account_id`, so pods deploy with no
    identity and every AWS call they make fails at runtime."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    plugin = ProviderPlugin.objects.create(name="aws", slug="aws", capabilities_manifest={}, config_schema={})
    app.default_tenant_cluster = TenantCluster.objects.create(
        organization=app.organization,
        slug="no-account",
        name="no-account",
        provider_plugin=plugin,
        provider_config={"region": "us-west-2"},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app.save(update_fields=["default_tenant_cluster"])

    check = _by_key(app)["identity"]

    assert check.status is CheckStatus.FAIL
    assert "pod identity" in check.detail
