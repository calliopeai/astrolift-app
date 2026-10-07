"""No per-app load balancer Ingress when the install withholds load balancers (calliope-installer#447)."""

from __future__ import annotations

import pytest

from astrolift_clusters.models import ManagedDomain
from astrolift_lifecycle.models import AppEnvironment, Deployment
from core.app_deploy import render_resources_for_deployment
from core.tests.utils.scope_world import ScopeWorld, make_cluster

pytestmark = pytest.mark.django_db

_TOML = (
    'name = "web"\n\n[[workloads]]\nname = "web"\nkind = "deployment"\nis_public = true\n\n'
    '  [[workloads.containers]]\n  name = "web"\n  is_primary = true\n  port = 8080\n'
)
MARKER = {"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {"name": "web-managed"}}


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))
    monkeypatch.setattr("core.app_deploy._render_managed_subdomain_ingress", lambda *a, **k: [dict(MARKER)])
    monkeypatch.setattr("core.app_deploy._stamp_storage_class_for_claims", lambda *a, **k: None)
    monkeypatch.delenv("ASTROLIFT_WITHHELD_CAPABILITIES", raising=False)


def _deployment(ingress_class: str) -> Deployment:
    w = ScopeWorld(f"wi447{ingress_class}")
    cluster = make_cluster(w, f"wi447{ingress_class}")
    cluster.ingress_class = ingress_class
    cluster.save()
    w.medops_app.manifest_raw = _TOML
    w.medops_app.save(update_fields=["manifest_raw"])
    domain = ManagedDomain.objects.create(
        organization=w.org, zone=f"{ingress_class}.wi447.test", dns_driver="route53"
    )
    env = AppEnvironment.objects.create(
        registered_app=w.medops_app, tenant_cluster=cluster, name="prod", managed_domain=domain
    )
    return Deployment.objects.create(registered_app=w.medops_app, app_environment=env, image_tag="v1")


def _ingresses(deployment) -> list[str]:
    resources = render_resources_for_deployment(deployment, include_managed_filesystems=False)
    return [r["metadata"]["name"] for r in resources if r["kind"] == "Ingress"]


def test_unset_renders_the_app_ingress():
    assert _ingresses(_deployment("alb")) == ["web-managed"]


def test_withheld_load_balancers_skip_the_alb_app_ingress(monkeypatch):
    monkeypatch.setenv("ASTROLIFT_WITHHELD_CAPABILITIES", "load_balancers")
    assert _ingresses(_deployment("alb")) == []


def test_withheld_load_balancers_keep_ingresses_behind_the_cluster_load_balancer(monkeypatch):
    monkeypatch.setenv("ASTROLIFT_WITHHELD_CAPABILITIES", "dns,load_balancers")
    assert _ingresses(_deployment("nginx")) == ["web-managed"]
