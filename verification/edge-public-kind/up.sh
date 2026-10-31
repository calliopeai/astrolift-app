#!/bin/bash
# Bring up kind + the pinned Envoy Gateway and the edge as Astrolift renders it (#2504).
# Needs kind, helm, kubectl, and a Python that imports the backend's providers: PYTHON=...
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BACKEND=$HERE/../../backend
OUT=${OUT:-$(mktemp -d)}
PY=${PYTHON:-python3}
VERSION=$(grep '^EDGE_CHART_VERSION' "$BACKEND/providers/k8s_native/edge_gateway.py" | cut -d'"' -f2)
kind get clusters | grep -q '^publicedge$' || kind create cluster --name publicedge --wait 300s
kubectl config use-context kind-publicedge
helm status eg -n envoy-gateway-system >/dev/null 2>&1 || \
  helm install eg oci://docker.io/envoyproxy/gateway-helm --version "v$VERSION" \
    -n envoy-gateway-system --create-namespace --wait --timeout 15m
for phase in public private refused mutant; do
  (cd "$BACKEND" && PYTHONPATH="$PWD/providers:$PWD" "$PY" "$HERE/render.py" "$OUT" "$phase")
done
kubectl apply --server-side --field-manager=astrolift -f "$OUT/infra.json"
for ns in pub-ns shop-ns lst-ns kb-ns; do kubectl -n $ns rollout status deploy/web --timeout=10m; done
kubectl -n envoy-gateway-system wait --for=condition=Available deploy \
  -l gateway.envoyproxy.io/owning-gateway-name=edge --timeout=10m
echo "up. Next: OUT=$OUT $HERE/probe.sh"
