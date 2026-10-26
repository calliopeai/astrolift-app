"""Render the edge and three apps as Astrolift does, for one phase of #2504.

    render.py <out-dir> <phase>

Phases (what the control plane holds for the ``pub`` app and its cluster):
  public   allow_public_apps on,  pub.edge_public on
  private  allow_public_apps on,  pub.edge_public off   (made private again)
  refused  allow_public_apps off, pub.edge_public on    (the cluster refuses)
  mutant   as ``refused``, but ``pub``'s route is rendered ungated anyway and
           ``kb`` public without its token route (what dropping the brain's
           ``/mcp`` route for a public brain would do): the negative control,
           which the probe must catch.

``kb`` is a brain that follows ``pub``: its ``/mcp`` route must keep its own
token policy whether it is public or not. ``shop`` never asks to be public;
``lst`` asks but has an access list. The gate decision is
``app_route_gated``, the function the deploy path calls. The one test-only
change: the brain provider's keys are inlined (``localJWKS``) instead of
fetched from the control plane over https.
"""

import json
import sys
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

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
ISS = "https://brain-issuer.edge.test"
BRAIN = {"brain_audience": "brain-guid-1", "brain_issuer": ISS, "brain_jwks_uri": ISS + "/jwks.json"}
key = ec.generate_private_key(ec.SECP256R1())
jwk = json.loads(ECAlgorithm.to_jwk(key.public_key()))
jwk.update(kid="k1", alg="ES256", use="sig")
now = int(time.time())
token = jwt.encode(
    {"iss": ISS, "aud": "brain-guid-1", "sub": "probe", "email": "p@edge.test", "iat": now, "exp": now + 86400},
    key,
    algorithm="ES256",
    headers={"kid": "k1"},
)


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


def app(slug, *, edge_public, restricted=False, force_ungated=False, brain=None, edge_gated=True):
    gated = app_route_gated(
        cluster_gated=True,
        allow_public_apps=allow,
        edge_public=edge_public,
        restricted=restricted,
    )
    if force_ungated:
        gated = False
    return render_app_routes(
        app_slug=slug,
        namespace=f"{slug}-ns",
        workloads={"web": ([f"{slug}.edge.test"], 8080)},
        gated=gated,
        paused=False,
        gateway_secret=cfg["gateway_secret"],
        brain=brain,
        edge_gated=edge_gated,
    )


infra = list(edge_post_install_manifests(cfg, front=None, access_rules=[]))
infra.append(edge_oidc_secret_manifest(cfg))
for ns in ("pub-ns", "shop-ns", "lst-ns", "kb-ns"):
    infra += backend(ns)
routes = (
    app("pub", edge_public=pub_public, force_ungated=phase == "mutant")
    + app("shop", edge_public=False)
    + app("lst", edge_public=True, restricted=True)
    + (
        app("kb", edge_public=True, force_ungated=True, brain=BRAIN, edge_gated=False)
        if phase == "mutant"
        else app("kb", edge_public=pub_public, brain=BRAIN)
    )
)
for x in routes:  # test-only: inline the brain keys instead of fetching them over https
    for p in (x.get("spec", {}).get("jwt") or {}).get("providers", []):
        if p["name"] == "astrolift-brain":
            p.pop("remoteJWKS")
            p["localJWKS"] = {"type": "Inline", "inline": json.dumps({"keys": [jwk]})}
json.dump({"apiVersion": "v1", "kind": "List", "items": infra}, open(f"{out}/infra.json", "w"), indent=1)
json.dump({"apiVersion": "v1", "kind": "List", "items": routes}, open(f"{out}/routes-{phase}.json", "w"), indent=1)
json.dump({"valid": token}, open(f"{out}/tokens-{phase}.json", "w"))
print("rendered", phase, len(infra), len(routes))
