"""Both render paths agree about the auth gate (#1619, #1539).

Two functions emit an app's managed-subdomain Ingress:
``core.app_deploy._render_managed_subdomain_ingress`` and
``astrolift_workflows.activities.app_lifecycle._render_app_ingresses_and_tls``.
The second omitted the auth annotations entirely -- on ALB it built an
``ALBConfig`` with no ``cognito_auth``, on nginx it hand-built the dict
with only cert-manager annotations. Whether an app required a login
therefore depended on which path had run, with no config difference and
no log line.

These assert the gate on the second path, and that the two paths agree.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.app_lifecycle import _render_app_ingresses_and_tls

pytestmark = pytest.mark.django_db

OIDC_CONFIG = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc",
    "client_id": "1qqhi5irhften18gbbpailuo8v",
    "cookie_secret": "signing-key",
    "auth_proxy_host": "auth.apps.example.net",
}
ALB_CONFIG = {
    "user_pool_arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_abc",
    "user_pool_client_id": "client-abc",
    "user_pool_domain": "acme-auth",
}

AUTH_URL = "nginx.ingress.kubernetes.io/auth-url"
AUTH_SIGNIN = "nginx.ingress.kubernetes.io/auth-signin"


def _make_manifest():
    from astrolift_manifest.types import (
        ContainerManifest,
        NormalizedManifest,
        WorkloadManifest,
    )

    return NormalizedManifest(
        name="hello-app",
        workloads=(
            WorkloadManifest(
                name="web",
                kind="deployment",
                replicas=1,
                is_public=True,
                containers=(ContainerManifest(name="web", port=8080, is_primary=True),),
            ),
        ),
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )


@pytest.fixture
def managed_domain(org):
    from astrolift_clusters.models import ManagedDomain

    return ManagedDomain.objects.create(
        organization=org,
        zone="apps.example.net",
        dns_driver="route53",
        dns_config={},
    )


@pytest.fixture
def managed_env(env, managed_domain):
    env.managed_domain = managed_domain
    env.save()
    return env


@pytest.fixture
def deployment(app, managed_env):
    from astrolift_lifecycle.models import Deployment

    return Deployment.objects.create(
        registered_app=app,
        app_environment=managed_env,
        triggered_by_user_id=None,
        trigger_kind="manual",
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )


def _managed_ingresses(deployment):
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    return [
        r
        for r in out
        if r.get("kind") == "Ingress"
        and r["metadata"].get("labels", {}).get("astrolift.dev/managed-subdomain") == "true"
    ]


# ---- nginx / central auth host --------------------------------------


def test_workflow_renderer_gates_on_the_central_auth_host(deployment, cluster):
    cluster.ingress_class = "nginx"
    cluster.oidc_auth_config = OIDC_CONFIG
    cluster.save()

    ingresses = _managed_ingresses(deployment)
    assert ingresses, "expected a managed-subdomain Ingress"
    for ingress in ingresses:
        annotations = ingress["metadata"]["annotations"]
        assert annotations[AUTH_URL] == "https://auth.apps.example.net/oauth2/auth"
        assert annotations[AUTH_SIGNIN].startswith("https://auth.apps.example.net/oauth2/start?rd=https://")


def test_workflow_renderer_emits_no_gate_without_a_config(deployment, cluster):
    cluster.ingress_class = "nginx"
    cluster.oidc_auth_config = None
    cluster.save()

    for ingress in _managed_ingresses(deployment):
        assert AUTH_URL not in ingress["metadata"]["annotations"]


def test_both_render_paths_produce_the_same_gate(deployment, cluster):
    """The parity assertion. Two producers, one answer."""
    from core.app_deploy import _render_managed_subdomain_ingress

    cluster.ingress_class = "nginx"
    cluster.oidc_auth_config = OIDC_CONFIG
    cluster.save()

    deploy_path = _render_managed_subdomain_ingress(
        deployment,
        _make_manifest(),
        "ns",
        deployment.app_environment.managed_domain,
        cluster,
    )
    deploy_annotations = {
        k: v
        for ingress in deploy_path
        for k, v in ingress["metadata"].get("annotations", {}).items()
        if k.startswith("nginx.ingress.kubernetes.io/auth-")
    }
    workflow_annotations = {
        k: v
        for ingress in _managed_ingresses(deployment)
        for k, v in ingress["metadata"].get("annotations", {}).items()
        if k.startswith("nginx.ingress.kubernetes.io/auth-")
    }
    assert deploy_annotations
    assert workflow_annotations == deploy_annotations


# ---- alb / Cognito ---------------------------------------------------


def test_workflow_renderer_gates_alb_ingresses_too(deployment, cluster):
    """The ALB half of the same omission: ``ALBConfig`` was built without
    ``cognito_auth`` here while the deploy renderer passed it."""
    cluster.ingress_class = "alb"
    cluster.alb_auth_config = ALB_CONFIG
    cluster.save()

    ingresses = _managed_ingresses(deployment)
    assert ingresses, "expected a managed-subdomain Ingress"
    for ingress in ingresses:
        annotations = ingress["metadata"]["annotations"]
        assert annotations["alb.ingress.kubernetes.io/auth-type"] == "cognito"
        assert ALB_CONFIG["user_pool_arn"] in annotations["alb.ingress.kubernetes.io/auth-idp-cognito"]


def test_workflow_renderer_emits_no_alb_gate_without_a_config(deployment, cluster):
    cluster.ingress_class = "alb"
    cluster.alb_auth_config = None
    cluster.save()

    for ingress in _managed_ingresses(deployment):
        assert "alb.ingress.kubernetes.io/auth-type" not in ingress["metadata"]["annotations"]
