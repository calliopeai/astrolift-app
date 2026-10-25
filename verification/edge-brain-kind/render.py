import json, sys, time
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm
from k8s_native.edge_gateway import edge_post_install_manifests, render_app_routes, edge_oidc_secret_manifest
out = sys.argv[1]
cfg = {"discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_xU96Y7DAg/.well-known/openid-configuration",
       "client_id": "testclient", "client_secret": "dummy", "auth_proxy_host": "auth.edge.test"}
ISS = "https://brain-issuer.edge.test"
BRAIN = {"brain_audience": "brain-guid-1", "brain_issuer": ISS, "brain_jwks_uri": ISS + "/jwks.json"}
key, other = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
jwk = json.loads(ECAlgorithm.to_jwk(key.public_key())); jwk.update(kid="k1", alg="ES256", use="sig")
jwks = json.dumps({"keys": [jwk]})
def tok(aud, k=key, iss=ISS):
    now = int(time.time())
    return jwt.encode({"iss": iss, "aud": aud, "sub": "probe", "email": "p@edge.test", "iat": now, "exp": now + 3600},
                      k, algorithm="ES256", headers={"kid": "k1"})
tokens = {"valid": tok("brain-guid-1"), "wrong_aud": tok("brain-guid-2"), "wrong_key": tok("brain-guid-1", other)}
m = list(edge_post_install_manifests(cfg, front=None, access_rules=[]))
m.append(edge_oidc_secret_manifest(cfg))
def backend(ns, text):
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": ns},
         "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "web"}}, "template": {"metadata": {"labels": {"app": "web"}},
          "spec": {"containers": [{"name": "web", "image": "hashicorp/http-echo:1.0", "args": [f"-text={text}", "-listen=:5678"],
                                   "ports": [{"containerPort": 5678}]}]}}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "web", "namespace": ns},
         "spec": {"selector": {"app": "web"}, "ports": [{"port": 5678, "targetPort": 5678}]}},
    ]
m += backend("shop-ns", "SHOP-BACKEND") + backend("brain-ns", "BRAIN-BACKEND")
m += render_app_routes(app_slug="shop", namespace="shop-ns", workloads={"web": (["shop.edge.test"], 5678)},
                       gated=True, paused=False, gateway_secret="gw")
m += render_app_routes(app_slug="kb", namespace="brain-ns", workloads={"web": (["kb.edge.test"], 5678)},
                       gated=True, paused=False, gateway_secret="gw", brain=BRAIN)
for x in m:  # test-only: inline the brain keys instead of fetching them over https
    for p in (x.get("spec", {}).get("jwt") or {}).get("providers", []):
        if p["name"] == "astrolift-brain":
            p.pop("remoteJWKS"); p["localJWKS"] = {"type": "Inline", "inline": jwks}
json.dump({"apiVersion": "v1", "kind": "List", "items": m}, open(out + "/all.json", "w"), indent=1)
json.dump(tokens, open(out + "/tokens.json", "w"))
print("rendered", len(m))
