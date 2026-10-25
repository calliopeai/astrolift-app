#!/bin/bash
# Bring up kind + the pinned Envoy Gateway, and apply the edge as Astrolift renders it (#2526).
# Needs kind, helm, kubectl, and a Python with the backend's deps (PyJWT, cryptography): PYTHON=...
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BACKEND=$HERE/../../backend
OUT=${OUT:-$(mktemp -d)}
PY=${PYTHON:-python3}
VERSION=$(grep '^EDGE_CHART_VERSION' "$BACKEND/providers/k8s_native/edge_gateway.py" | cut -d'"' -f2)
kind get clusters | grep -q '^brainedge$' || kind create cluster --name brainedge --wait 300s
kubectl config use-context kind-brainedge
helm status eg -n envoy-gateway-system >/dev/null 2>&1 || \
  helm install eg oci://docker.io/envoyproxy/gateway-helm --version "v$VERSION" \
    -n envoy-gateway-system --create-namespace --wait --timeout 15m
(cd "$BACKEND" && PYTHONPATH="$PWD/providers:$PWD" "$PY" "$HERE/render.py" "$OUT")
kubectl apply --server-side -f "$OUT/all.json"
kubectl -n shop-ns rollout status deploy/web --timeout=10m
kubectl -n brain-ns rollout status deploy/web --timeout=10m
kubectl -n envoy-gateway-system wait --for=condition=Available deploy \
  -l gateway.envoyproxy.io/owning-gateway-name=edge --timeout=10m
echo "up; tokens in $OUT/tokens.json. Next: OUT=$OUT $HERE/probe.sh"
