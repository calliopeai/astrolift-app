"""Cluster ingress mode reaches the rendered Ingress (#64, spec 13 §4).

``astrolift_clusters.ingress_modes`` has described ``shared_ingress`` vs
``per_app_ingress`` since #64, including the per-driver group annotation
table, and no renderer ever read it: no Ingress carried
``alb.ingress.kubernetes.io/group.name`` anywhere in the tree, so the AWS
Load Balancer Controller provisioned a separate ALB per app and the
documented shared mode was unreachable. There was also no field for an
operator to pick a mode.

The default stays per-app: switching an existing cluster re-groups load
balancers that are already serving traffic, so it has to be an
operator's decision.
"""

from __future__ import annotations

from types import SimpleNamespace

from astrolift_clusters.ingress_modes import IngressMode
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from core.app_deploy import _render_managed_subdomain_ingress, shared_ingress_annotations

_GROUP_NAME = "alb.ingress.kubernetes.io/group.name"
_GROUP_ORDER = "alb.ingress.kubernetes.io/group.order"

_MANIFEST = """name = "hello-web"
[[workloads]]
name = "web"
kind = "deployment"
is_public = true
replicas = 1
  [[workloads.containers]]
  name = "app"
  is_primary = true
  image_ref = "docker.io/calliopeai/astrolift-sample-web:main-80acde1"
  port = 8080
"""


def _render(*, ingress_class: str, ingress_mode: str | None):
    manifest = normalize(parse_raw(_MANIFEST), defaults=NormalizationDefaults())
    deployment = SimpleNamespace(
        pk=1,
        registered_app=SimpleNamespace(
            slug="hello-web",
            organization=SimpleNamespace(slug="acme"),
            organization_id=1,
            # No per-app subdomain override — these cases are about ALB
            # grouping, so the hostname should come from the slug.
            subdomain="",
        ),
        app_environment=SimpleNamespace(ingress_paused=False),
    )
    managed_domain = SimpleNamespace(zone="apps.example.net", dns_config={})
    cluster = SimpleNamespace(
        ingress_class=ingress_class,
        region="us-west-2",
        alb_auth_config=None,
    )
    if ingress_mode is not None:
        cluster.ingress_mode = ingress_mode
    return _render_managed_subdomain_ingress(
        deployment,
        manifest,
        namespace="acme-hello-web",
        managed_domain=managed_domain,
        cluster=cluster,
    )


def test_shared_mode_groups_the_alb_by_org():
    out = _render(ingress_class="alb", ingress_mode=IngressMode.SHARED_INGRESS.value)

    annotations = out[0]["metadata"]["annotations"]
    assert annotations[_GROUP_NAME] == "astrolift-acme"
    assert annotations[_GROUP_ORDER] == "100"


def test_shared_mode_keeps_the_annotations_the_alb_driver_renders():
    """The group annotations are merged, not substituted -- dropping the
    healthcheck path here would re-open #997."""
    out = _render(ingress_class="alb", ingress_mode=IngressMode.SHARED_INGRESS.value)

    annotations = out[0]["metadata"]["annotations"]
    assert "alb.ingress.kubernetes.io/healthcheck-path" in annotations
    assert annotations["alb.ingress.kubernetes.io/scheme"] == "internet-facing"


def test_per_app_mode_renders_no_group_annotation():
    out = _render(ingress_class="alb", ingress_mode=IngressMode.PER_APP_INGRESS.value)

    assert _GROUP_NAME not in out[0]["metadata"]["annotations"]


def test_cluster_without_a_mode_renders_no_group_annotation():
    """Pre-migration rows and any non-model caller keep the old shape."""
    out = _render(ingress_class="alb", ingress_mode=None)

    assert _GROUP_NAME not in out[0]["metadata"]["annotations"]


def test_shared_mode_is_a_no_op_for_nginx():
    """The policy's nginx entry is the ingress-class annotation, not a
    group key: stamping it would take the Ingress away from its
    controller, and an nginx cluster already shares one load balancer."""
    out = _render(ingress_class="nginx", ingress_mode=IngressMode.SHARED_INGRESS.value)

    annotations = out[0]["metadata"].get("annotations", {})
    assert "kubernetes.io/ingress.class" not in annotations
    assert _GROUP_NAME not in annotations


def test_shared_ingress_annotations_defaults_to_empty():
    assert (
        shared_ingress_annotations(
            SimpleNamespace(ingress_class="alb"),
            org_slug="acme",
            app_slug="hello-web",
        )
        == {}
    )
