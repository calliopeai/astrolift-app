"""Deploying onto the Envoy edge, and moving an app onto it (#2055).

The install writes the one secret the edge needs without it reaching an
error, an app on an envoy cluster renders routes instead of an Ingress,
and the app's old ALB Ingress goes once the route is up, so the app
actually moves. Only the tenant cluster's API server is faked.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from _sdk.cluster import ApplyError, ApplyResult, DeleteResult

from astrolift_workflows.activities.install_prereqs import _apply_edge_oidc_secret, _operator_not_serving
from core.app_deploy import AppDeployError, envoy_edge_routes, prune_edge_leftovers
from core.ingress_reconcile import reconcile_cluster_ingresses

COGNITO = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration",
    "client_id": "central-client-id",
    "auth_proxy_host": "auth.astro.example.net",
}
CLIENT_SECRET = "client-secret-2055-never-leaves"


class _Api:
    def __init__(self, *, existing=None, errors=None, listings=None):
        self.applied: list[tuple[str, list[dict[str, Any]]]] = []
        self.deleted: list[tuple[str, list[str]]] = []
        self._existing = existing or {}
        self._errors = errors
        self._listings = listings or {}

    def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False):
        self.applied.append((namespace, list(manifests)))
        if self._errors:
            return ApplyResult(created=[], updated=[], unchanged=[], errors=list(self._errors))
        return ApplyResult(
            created=[m["metadata"]["name"] for m in manifests], updated=[], unchanged=[], errors=[]
        )

    def get_manifest(self, cluster, namespace, kind, name):
        return self._existing.get((namespace, kind, name))

    def list_manifests(self, cluster, namespace, kind):
        return self._listings.get((namespace, kind), [])

    def delete_manifests(self, cluster, namespace, manifests, *, propagation_policy=None):
        names = [f"{m['kind']}/{m['metadata']['name']}" for m in manifests]
        self.deleted.append((namespace, names))
        return DeleteResult(deleted=names, not_found=[], errors=[])


PROGRAMMED = {
    ("astrolift-edge", "gateway.networking.k8s.io/v1/Gateway", "edge"): {
        "status": {"conditions": [{"type": "Programmed", "status": "True"}]}
    }
}


def _cluster(config=None, ingress_class="envoy", plugin="aws", mode="shared_ingress"):
    return SimpleNamespace(
        oidc_auth_config=config,
        ingress_class=ingress_class,
        slug="c",
        provider_plugin=SimpleNamespace(slug=plugin),
        ingress_mode=mode,
    )


# ---- install ---------------------------------------------------------------


def test_install_writes_the_namespace_and_secret_first():
    api = _Api()
    _apply_edge_oidc_secret(
        api, "c", _cluster({**COGNITO, "client_secret": CLIENT_SECRET}), {"envoy-gateway"}
    )
    [(ns, batch)] = api.applied
    assert ns == "astrolift-edge"
    assert [m["kind"] for m in batch] == ["Namespace", "Secret"]
    assert batch[1]["data"]["client-secret"] == base64.b64encode(CLIENT_SECRET.encode()).decode()


def test_install_skips_when_the_edge_is_not_selected():
    api = _Api()
    _apply_edge_oidc_secret(api, "c", _cluster({**COGNITO, "client_secret": CLIENT_SECRET}), {"cert-manager"})
    assert api.applied == []


def test_install_refuses_a_configured_gate_with_no_secret_anywhere():
    with pytest.raises(AppDeployError, match="client_secret"):
        _apply_edge_oidc_secret(_Api(), "c", _cluster(COGNITO), {"envoy-gateway"})


def test_install_keeps_a_secret_it_does_not_own():
    api = _Api(existing={("astrolift-edge", "Secret", "astrolift-edge-oidc"): {"kind": "Secret"}})
    _apply_edge_oidc_secret(api, "c", _cluster(COGNITO), {"envoy-gateway"})
    assert api.applied == []


def test_install_error_never_carries_the_secret():
    encoded = base64.b64encode(CLIENT_SECRET.encode()).decode()
    api = _Api(
        errors=[
            ApplyError(
                kind="Secret",
                name="astrolift-edge-oidc",
                namespace="astrolift-edge",
                exception_type="ApiException",
                exception_message=f"rejected {CLIENT_SECRET} and {encoded}",
                is_retryable=False,
            )
        ]
    )
    with pytest.raises(AppDeployError) as exc:
        _apply_edge_oidc_secret(
            api, "c", _cluster({**COGNITO, "client_secret": CLIENT_SECRET}), {"envoy-gateway"}
        )
    assert CLIENT_SECRET not in str(exc.value)
    assert encoded not in str(exc.value)


def test_webhook_not_serving_is_retried_like_a_missing_crd():
    assert _operator_not_serving(
        ['Internal error occurred: failed calling webhook "x": no endpoints available']
    )
    assert _operator_not_serving(["no matches found for kind SecurityPolicy"])
    assert not _operator_not_serving(["admission webhook denied the request: invalid"])


# ---- render ----------------------------------------------------------------


def _app(edge=None):
    return SimpleNamespace(
        slug="veruus",
        manifest_normalized={"edge": edge} if edge else {},
        organization_id=1,
        organization=SimpleNamespace(slug="conflict"),
    )


def test_gated_cluster_renders_gated_routes():
    out = envoy_edge_routes(
        _app(),
        namespace="conflict-veruus",
        workloads={"web": (["veruus-demo.astro.example.net"], 8080)},
        cluster=_cluster(COGNITO),
        paused=False,
    )
    [route] = [r for r in out if r["kind"] == "HTTPRoute"]
    assert route["metadata"]["labels"]["astrolift.dev/edge-auth"] == "gated"


def test_cluster_without_a_gate_renders_public_routes():
    out = envoy_edge_routes(
        _app(), namespace="ns", workloads={"web": (["a.z.example"], 80)}, cluster=_cluster(None), paused=False
    )
    [route] = [r for r in out if r["kind"] == "HTTPRoute"]
    assert "astrolift.dev/edge-auth" not in route["metadata"]["labels"]


def test_per_app_identity_mapping_is_refused_not_dropped():
    with pytest.raises(AppDeployError, match="identity_headers"):
        envoy_edge_routes(
            _app({"identity_headers": [["email", "X-Forwarded-Email"]]}),
            namespace="ns",
            workloads={"web": (["a.z.example"], 80)},
            cluster=_cluster(COGNITO),
            paused=False,
        )


# ---- moving onto the edge --------------------------------------------------


def _ingress(name, app, managed=True):
    labels = {"astrolift.dev/app": app}
    if managed:
        labels["astrolift.dev/managed-subdomain"] = "true"
    return {"metadata": {"name": name, "labels": labels}}


def _route(name, namespace):
    return {"metadata": {"name": name, "labels": {"astrolift.dev/namespace": namespace}}}


def test_move_deletes_only_this_apps_old_managed_ingress():
    api = _Api(
        existing=PROGRAMMED,
        listings={
            ("ns", "networking.k8s.io/v1/Ingress"): [
                _ingress("veruus-web", "veruus"),
                _ingress("other-web", "other"),
                _ingress("veruus-custom", "veruus", managed=False),
            ]
        },
    )
    prune_edge_leftovers(api, "c", app_slug="veruus", namespace="ns", rendered=[])
    assert api.deleted == [("ns", ["Ingress/veruus-web"])]


def test_move_keeps_the_old_ingress_until_the_edge_serves():
    api = _Api(listings={("ns", "networking.k8s.io/v1/Ingress"): [_ingress("veruus-web", "veruus")]})
    prune_edge_leftovers(api, "c", app_slug="veruus", namespace="ns", rendered=[])
    assert api.deleted == []


def test_move_prunes_routes_the_environment_no_longer_renders():
    rendered = [{"kind": "HTTPRoute", "metadata": {"name": "ns-web"}}]
    api = _Api(
        listings={
            ("astrolift-edge", "gateway.networking.k8s.io/v1/HTTPRoute"): [
                _route("ns-web", "ns"),
                _route("ns-old", "ns"),
                _route("other-web", "other-ns"),
            ]
        }
    )
    prune_edge_leftovers(api, "c", app_slug="veruus", namespace="ns", rendered=rendered)
    assert api.deleted == [("astrolift-edge", ["HTTPRoute/ns-old"])]


def test_teardown_prunes_every_route_of_the_environment():
    api = _Api(
        listings={
            ("astrolift-edge", "gateway.networking.k8s.io/v1/HTTPRoute"): [_route("ns-web", "ns")],
            ("astrolift-edge", "gateway.envoyproxy.io/v1alpha1/HTTPRouteFilter"): [_route("ns-web", "ns")],
        }
    )
    prune_edge_leftovers(api, "c", app_slug="veruus", namespace="ns", rendered=[])
    assert api.deleted == [
        ("astrolift-edge", ["HTTPRoute/ns-web"]),
        ("astrolift-edge", ["HTTPRouteFilter/ns-web"]),
    ]


def test_prune_failure_never_fails_the_deploy():
    class _Broken(_Api):
        def list_manifests(self, *a, **kw):
            raise RuntimeError("apiserver down")

    assert prune_edge_leftovers(_Broken(), "c", app_slug="a", namespace="ns", rendered=[]) == []


def test_reconcile_is_a_no_op_on_the_envoy_edge():
    assert reconcile_cluster_ingresses(_cluster(COGNITO)) == {"reconciled": 0, "skipped": 0, "errors": []}


# ---- #2124: the app keeps a rule on its own ALB group ----------------------


def _front(out):
    return [r for r in out if r["kind"] == "Ingress"]


def test_shared_ingress_aws_app_gets_a_rule_on_its_alb_group():
    out = envoy_edge_routes(
        _app(),
        namespace="conflict-veruus",
        workloads={"web": (["veruus-demo.astro.example.net"], 8080)},
        cluster=_cluster(COGNITO),
        paused=False,
    )
    [ingress] = _front(out)
    assert ingress["metadata"]["namespace"] == "astrolift-system"
    assert ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/group.name"] == "astrolift-conflict"


def test_no_group_rule_off_aws_or_outside_shared_ingress():
    for cluster in (_cluster(COGNITO, plugin="gcp"), _cluster(COGNITO, mode="per_app")):
        out = envoy_edge_routes(
            _app(), namespace="ns", workloads={"web": (["a.z.example"], 80)}, cluster=cluster, paused=False
        )
        assert _front(out) == []


def test_prune_keeps_the_rendered_group_rule_and_drops_a_stale_one():
    rendered = [{"kind": "Ingress", "metadata": {"name": "ns-alb"}}]
    front = {"astrolift.dev/namespace": "ns", "astrolift.dev/edge-front": "true"}
    api = _Api(
        listings={
            ("astrolift-system", "networking.k8s.io/v1/Ingress"): [
                {"metadata": {"name": "ns-alb", "labels": front}},
                {"metadata": {"name": "ns-old", "labels": front}},
                # The edge's own wildcard front, and anything else in the
                # platform namespace, never carries the marker.
                {"metadata": {"name": "astrolift-edge", "labels": {"astrolift.dev/namespace": "ns"}}},
            ]
        }
    )
    prune_edge_leftovers(api, "c", app_slug="veruus", namespace="ns", rendered=rendered)
    assert api.deleted == [("astrolift-system", ["Ingress/ns-old"])]


def test_teardown_drops_the_group_rule_too():
    front = {"astrolift.dev/namespace": "ns", "astrolift.dev/edge-front": "true"}
    api = _Api(
        listings={
            ("astrolift-system", "networking.k8s.io/v1/Ingress"): [
                {"metadata": {"name": "ns-alb", "labels": front}}
            ]
        }
    )
    prune_edge_leftovers(api, "c", app_slug="veruus", namespace="ns", rendered=[])
    assert api.deleted == [("astrolift-system", ["Ingress/ns-alb"])]
