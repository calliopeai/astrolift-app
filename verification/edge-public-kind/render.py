"""Render the edge and three apps as Astrolift does, for one phase of #2504.

    render.py <out-dir> <phase>

Phases (what the control plane holds for the ``pub`` app and its cluster):
  public   allow_public_apps on,  pub.edge_public on
  private  allow_public_apps on,  pub.edge_public off   (made private again)
  refused  allow_public_apps off, pub.edge_public on    (the cluster refuses)
  mutant   as ``refused``, but the route is rendered ungated anyway: the
           negative control, which the probe must catch.

``shop`` never asks to be public; ``lst`` asks but has an access list. The
gate decision is ``app_route_gated``, the function the deploy path calls.
"""

import json
import sys

from k8s_native.edge_gateway import (
    app_route_gated,
    edge_oidc_secret_manifest,
    edge_post_install_manifests,
    render_app_routes,
)

out, phase = sys.argv[1], sys.argv[2]
cfg = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_xU96Y7DAg/.well-known/openid-configuration",
    "client_id": "testclient",
    "client_secret": "dummy",
    "auth_proxy_host": "auth.edge.test",
    "gateway_secret": "gw-secret",
}
allow = phase in ("public", "private")
pub_public = phase != "private"


def backend(ns):
    # agnhost netexec answers /header?key=<name> with that request header's value.
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}},
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "web", "namespace": ns},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "web"}},
                "template": {
                    "metadata": {"labels": {"app": "web"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "web",
                                "image": "registry.k8s.io/e2e-test-images/agnhost:2.53",
                                "args": ["netexec", "--http-port=8080"],
                                "ports": [{"containerPort": 8080}],
                            }
                        ]
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "web", "namespace": ns},
            "spec": {"selector": {"app": "web"}, "ports": [{"port": 8080, "targetPort": 8080}]},
        },
    ]


def app(slug, *, edge_public, restricted=False, force_ungated=False):
    gated = app_route_gated(
        cluster_gated=True,
        allow_public_apps=allow,
        edge_public=edge_public,
        restricted=restricted,
        brain=False,
    )
    if force_ungated:
        gated = False
    return render_app_routes(
        app_slug=slug,
        namespace=f"{slug}-ns",
        workloads={"web": ([f"{slug}.edge.test"], 8080)},
        gated=gated,
        paused=False,
        gateway_secret=cfg["gateway_secret"] if gated else "",
    )


infra = list(edge_post_install_manifests(cfg, front=None, access_rules=[]))
infra.append(edge_oidc_secret_manifest(cfg))
for ns in ("pub-ns", "shop-ns", "lst-ns"):
    infra += backend(ns)
routes = (
    app("pub", edge_public=pub_public, force_ungated=phase == "mutant")
    + app("shop", edge_public=False)
    + app("lst", edge_public=True, restricted=True)
)
json.dump({"apiVersion": "v1", "kind": "List", "items": infra}, open(f"{out}/infra.json", "w"), indent=1)
json.dump({"apiVersion": "v1", "kind": "List", "items": routes}, open(f"{out}/routes-{phase}.json", "w"), indent=1)
print("rendered", phase, len(infra), len(routes))
