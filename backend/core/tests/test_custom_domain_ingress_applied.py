"""A validated, active custom domain gets its Ingress in the render that is applied.

``apply_manifests`` ships ``render_resources_for_deployment``, not the
``render_manifests`` activity output, so the CustomDomain fold-in has to live
in the former or the domain never reaches the cluster.
"""

from __future__ import annotations

import logging

import pytest

from astrolift_lifecycle.models import AppEnvironment, CustomDomain, Deployment
from astrolift_workflows.activities.app_lifecycle import (
    _custom_domain_skip_reason,
    _no_route_reason,
)
from core.app_deploy import render_resources_for_deployment
from core.tests.utils.scope_world import ScopeWorld, make_cluster

pytestmark = pytest.mark.django_db

_TOML = (
    'name = "web"\n\n[[workloads]]\nname = "web"\nkind = "deployment"\nis_public = true\n\n'
    '  [[workloads.containers]]\n  name = "web"\n  is_primary = true\n  port = {port}\n'
)
_ARN = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))
    monkeypatch.setattr("core.app_deploy._stamp_storage_class_for_claims", lambda *a, **k: None)


def _world(
    *, ingress_class="nginx", port=8080, namespace="", cert_state=CustomDomain.CertificateState.ACTIVE
):
    w = ScopeWorld(f"cdi{ingress_class}{port}")
    cluster = make_cluster(w, f"cdi{ingress_class}{port}")
    cluster.ingress_class = ingress_class
    cluster.save()
    app = w.medops_app
    app.manifest_raw = _TOML.format(port=port)
    app.save(update_fields=["manifest_raw"])
    env = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="production", k8s_namespace=namespace
    )
    CustomDomain.objects.create(
        registered_app=app,
        hostname="www.example.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        is_active=True,
        is_platform_managed_zone=True,
        certificate_id=_ARN,
        certificate_state=cert_state,
    )
    return Deployment.objects.create(registered_app=app, app_environment=env, image_tag="v1")


def _custom_ingresses(deployment) -> list[dict]:
    resources = render_resources_for_deployment(deployment, include_managed_filesystems=False)
    return [
        r
        for r in resources
        if r["kind"] == "Ingress" and r["metadata"].get("labels", {}).get("astrolift.dev/custom-domain")
    ]


def test_applied_render_carries_the_custom_domain_ingress():
    (ingress,) = _custom_ingresses(_world())
    assert ingress["spec"]["rules"][0]["host"] == "www.example.com"
    assert ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"] == {"number": 8080}


def test_applied_render_emits_the_custom_domain_ingress_once_beside_the_managed_one(monkeypatch):
    marker = {"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {"name": "web-managed"}}
    monkeypatch.setattr("core.app_deploy._render_managed_subdomain_ingress", lambda *a, **k: [dict(marker)])
    deployment = _world()
    from astrolift_clusters.models import ManagedDomain

    env = deployment.app_environment
    env.managed_domain = ManagedDomain.objects.create(
        organization=env.registered_app.organization, zone="apps.example.com", dns_driver="route53"
    )
    env.save()
    names = [
        r["metadata"]["name"]
        for r in render_resources_for_deployment(deployment, include_managed_filesystems=False)
        if r["kind"] == "Ingress"
    ]
    assert sorted(names) == sorted(["web-managed", f"{deployment.registered_app.slug}-www-example-com"])


def test_applied_render_leaves_a_namespaced_environment_alone():
    assert _custom_ingresses(_world(namespace="preview-1")) == []


def test_skipped_domain_is_logged_with_the_reason(caplog):
    deployment = _world(port=0)
    with caplog.at_level(logging.WARNING, logger="astrolift_workflows.activities.app_lifecycle"):
        assert _custom_ingresses(deployment) == []
    assert "www.example.com" in caplog.text
    assert "has no port" in caplog.text


def test_in_flight_certificate_is_logged_with_the_reason(caplog):
    deployment = _world(cert_state=CustomDomain.CertificateState.ISSUING)
    with caplog.at_level(logging.WARNING, logger="astrolift_workflows.activities.app_lifecycle"):
        assert _custom_ingresses(deployment) == []
    assert "certificate_state" in caplog.text


def test_skip_reason_helpers():
    class _C:
        def __init__(self, port):
            self.port = port
            self.name = "web"

    class _W:
        name = "web"

    assert _no_route_reason(None, None) == "the manifest has no deployment workload"
    assert "no containers" in _no_route_reason(_W(), None)
    assert "no port" in _no_route_reason(_W(), _C(0))
    assert _no_route_reason(_W(), _C(80)) is None

    byo = CustomDomain(certificate_state=CustomDomain.CertificateState.BYO, byo_certificate_pem="junk")
    assert "END CERTIFICATE" in _custom_domain_skip_reason(byo)
    active = CustomDomain(certificate_state=CustomDomain.CertificateState.ACTIVE)
    assert _custom_domain_skip_reason(active) is None
    failed = CustomDomain(certificate_state=CustomDomain.CertificateState.FAILED)
    assert "FAILED" in _custom_domain_skip_reason(failed) or "failed" in _custom_domain_skip_reason(failed)
