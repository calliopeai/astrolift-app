#!/bin/bash
# Every case of #2504 against the real Envoy Gateway on kind. Prints PASS/FAIL per case.
# Each phase is applied the way the control plane applies routes: server-side,
# field manager "astrolift", so a label the new render drops is removed.
# REFUSED_PHASE=mutant is the negative control: it must report FAIL.
set -u
REFUSED_PHASE=${REFUSED_PHASE:-refused}
: "${OUT:?set OUT to the directory up.sh rendered into}"
BODY=$(mktemp)
kubectl config use-context kind-publicedge >/dev/null
SVC=$(kubectl -n envoy-gateway-system get svc -l gateway.envoyproxy.io/owning-gateway-name=edge -o jsonpath='{.items[0].metadata.name}')
kubectl -n envoy-gateway-system port-forward svc/$SVC 18081:80 >/dev/null 2>&1 & PF=$!
trap 'kill $PF 2>/dev/null' EXIT
sleep 4
fails=0
apply() { # phase
  kubectl apply --server-side --field-manager=astrolift -f "$OUT/routes-$1.json" >/dev/null
  # Prune what this render no longer has, as prune_edge_leftovers does after a deploy.
  keep=$(python3 -c "import json;print(' '.join(i['kind'].lower()+'/'+i['metadata']['name'] for i in json.load(open('$OUT/routes-$1.json'))['items']))")
  for obj in $(kubectl -n astrolift-edge get httproute,securitypolicy -l astrolift.dev/managed-subdomain=true -o name); do
    short=$(echo "$obj" | sed -E 's#^([a-z]+)\.[^/]*/#\1/#')
    [[ " $keep " == *" $short "* ]] || kubectl -n astrolift-edge delete "$obj" >/dev/null
  done
  V=$(python3 -c "import json;print(json.load(open('$OUT/tokens-$1.json'))['valid'])")
  sleep 8 # Envoy Gateway reconciles the xDS
  echo "--- phase $1: pub route label edge-auth=$(kubectl -n astrolift-edge get httproute pub-ns-web -o jsonpath='{.metadata.labels.astrolift\.dev/edge-auth}')"
}
check() { # name expect host path [header]
  if [ -n "${5:-}" ]; then out=$(curl -s -m 10 -o "$BODY" -w "%{http_code}" -H "Host: $3" -H "$5" "http://127.0.0.1:18081$4"); else out=$(curl -s -m 10 -o "$BODY" -w "%{http_code}" -H "Host: $3" "http://127.0.0.1:18081$4"); fi
  body=$(head -c 60 "$BODY" | tr -d '\n')
  if [[ "$out" =~ ^($2)$ ]]; then r=PASS; else r=FAIL; fails=$((fails+1)); fi
  printf "%-4s %-56s want %-4s got %s  %s\n" "$r" "$1" "$2" "$out" "$body"
}
body_is() { # name expect-body host path [header]
  out=$(curl -s -m 10 -H "Host: $3" ${5:+-H "$5"} "http://127.0.0.1:18081$4")
  if [ "$out" = "$2" ]; then r=PASS; else r=FAIL; fails=$((fails+1)); fi
  printf "%-4s %-56s want %-4s got '%s'\n" "$r" "$1" "'$2'" "$out"
}
apply private
check "before: not public / no cookie -> login"            302 pub.edge.test /
apply public
check "public app / no cookie -> app"                      200 pub.edge.test /
check "gated app / no cookie -> login (same edge)"         302 shop.edge.test /
check "public+access list / no cookie -> login"            302 lst.edge.test /
body_is "echo works: a client header reaches the app"     "yes" pub.edge.test "/header?key=X-Probe" "X-Probe: yes"
body_is "public app gets no gateway secret"                "" pub.edge.test "/header?key=X-Astrolift-Gateway-Secret"
body_is "public app: spoofed gateway secret stripped"      "" pub.edge.test "/header?key=X-Astrolift-Gateway-Secret" "X-Astrolift-Gateway-Secret: gw-secret"
body_is "public app: spoofed identity header stripped"     "" pub.edge.test "/header?key=X-Auth-Request-User" "X-Auth-Request-User: admin"
check "public brain / no cookie -> app"                    200 kb.edge.test /
check "public brain /mcp no token -> refused"              401 kb.edge.test /mcp
check "public brain /mcp valid token -> app"               200 kb.edge.test /mcp "X-Astrolift-Brain-Token: $V"
check "gated app beside the public brain -> login"         302 shop.edge.test /
apply private
check "made private again / no cookie -> login"            302 pub.edge.test /
check "brain made private / no cookie -> login"            302 kb.edge.test /
check "private brain /mcp no token -> refused"             401 kb.edge.test /mcp
check "private brain /mcp valid token -> app"              200 kb.edge.test /mcp "X-Astrolift-Brain-Token: $V"
check "gated app / no cookie -> login"                     302 shop.edge.test /
apply "$REFUSED_PHASE"
check "cluster refuses, app asks / no cookie -> login"     302 pub.edge.test /
check "brain /mcp no token -> refused"                     401 kb.edge.test /mcp
check "gated app / no cookie -> login"                     302 shop.edge.test /
echo "FAILS=$fails"
[ "$fails" = 0 ]
