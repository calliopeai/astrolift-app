"""A paused custom domain answers a 503 while the app's other hosts keep serving.

The environment pause still pauses every host; the domain pause is one host only
and never touches the managed-subdomain Ingress.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import ManagedDomain
from astrolift_lifecycle.models import AppEnvironment, CustomDomain, Deployment
from astrolift_workflows.activities.app_lifecycle import _render_app_ingresses_and_tls
from core.app_deploy import render_resources_for_deployment
from core.tests.test_custom_domain_ingress_applied import _quiet  # noqa: F401
from core.tests.utils.scope_world import ScopeWorld, make_cluster

pytestmark = pytest.mark.django_db

_TOML = (
    'name = "web"\n\n[[workloads]]\nname = "web"\nkind = "deployment"\nis_public = true\n\n'
    '  [[workloads.containers]]\n  name = "web"\n  is_primary = true\n  port = 8080\n'
)
_SNIPPET = "nginx.ingress.kubernetes.io/server-snippet"
_BODY = 'return 503 "Astrolift: app is paused";'


def _domain(app, hostname, *, paused=False):
    return CustomDomain.objects.create(
        registered_app=app,
        hostname=hostname,
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        is_active=True,
        is_platform_managed_zone=True,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        paused=paused,
    )


def _world(*, env_paused=False):
    w = ScopeWorld("cdpause")
    cluster = make_cluster(w, "cdpause")
    cluster.ingress_class = "nginx"
    cluster.save()
    app = w.medops_app
    app.manifest_raw = _TOML
    app.save(update_fields=["manifest_raw"])
    env = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="production", ingress_paused=env_paused
    )
    env.managed_domain = ManagedDomain.objects.create(
        organization=app.organization, zone="apps.example.com", dns_driver="route53"
    )
    env.save()
    _domain(app, "paused.example.com", paused=True)
    _domain(app, "live.example.com")
    return Deployment.objects.create(registered_app=app, app_environment=env, image_tag="v1")


def _by_host(deployment) -> dict[str, dict]:
    resources = render_resources_for_deployment(deployment, include_managed_filesystems=False)
    return {
        r["metadata"]["labels"]["astrolift.dev/custom-domain"]: r
        for r in resources
        if r["kind"] == "Ingress" and r["metadata"].get("labels", {}).get("astrolift.dev/custom-domain")
    }


def test_paused_domain_answers_503_and_the_other_domain_does_not():
    hosts = _by_host(_world())
    paused, live = hosts["paused.example.com"], hosts["live.example.com"]
    assert paused["metadata"]["annotations"][_SNIPPET] == _BODY
    assert paused["metadata"]["labels"]["astrolift.dev/ingress-state"] == "paused"
    assert _SNIPPET not in live["metadata"]["annotations"]
    assert live["metadata"]["labels"]["astrolift.dev/ingress-state"] == "live"


def test_domain_pause_leaves_the_managed_subdomain_ingress_live():
    deployment = _world()
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _manifest())
    managed = [
        r
        for r in out
        if r["kind"] == "Ingress" and r["metadata"]["labels"].get("astrolift.dev/managed-subdomain")
    ]
    assert managed, "the managed-subdomain Ingress must still render"
    for ing in managed:
        assert _SNIPPET not in ing["metadata"].get("annotations", {})
        assert ing["metadata"]["labels"]["astrolift.dev/ingress-state"] == "live"


def test_environment_pause_still_pauses_every_domain():
    hosts = _by_host(_world(env_paused=True))
    assert set(hosts) == {"paused.example.com", "live.example.com"}
    for ing in hosts.values():
        assert ing["metadata"]["annotations"][_SNIPPET] == _BODY
        assert ing["metadata"]["labels"]["astrolift.dev/ingress-state"] == "paused"


def _manifest():
    from astrolift_manifest.types import ContainerManifest, NormalizedManifest, WorkloadManifest

    return NormalizedManifest(
        name="web",
        managed_services=(),
        defaults_applied=(),
        serialized={},
        workloads=(
            WorkloadManifest(
                name="web",
                kind="deployment",
                replicas=1,
                is_public=True,
                containers=(ContainerManifest(name="web", port=8080, is_primary=True),),
            ),
        ),
    )
