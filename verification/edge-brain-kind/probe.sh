#!/bin/bash
# Every case of #2526 against the real Envoy Gateway on kind. Prints PASS/FAIL per case.
set -u
: "${OUT:?set OUT to the directory up.sh rendered into}"
BODY=$(mktemp)
kubectl config use-context kind-brainedge >/dev/null
SVC=$(kubectl -n envoy-gateway-system get svc -l gateway.envoyproxy.io/owning-gateway-name=edge -o jsonpath='{.items[0].metadata.name}')
kubectl -n envoy-gateway-system port-forward svc/$SVC 18080:80 >/dev/null 2>&1 & PF=$!
sleep 4
V=$(python3 -c "import json;print(json.load(open('${OUT}/tokens.json'))['valid'])")
W=$(python3 -c "import json;print(json.load(open('${OUT}/tokens.json'))['wrong_aud'])")
X=$(python3 -c "import json;print(json.load(open('${OUT}/tokens.json'))['wrong_key'])")
H=X-Astrolift-Brain-Token
fails=0
check() { # name expect host path [header]
  if [ -n "${5:-}" ]; then out=$(curl -s -m 10 -o "$BODY" -w "%{http_code}" -H "Host: $3" -H "$5" "http://127.0.0.1:18080$4"); else out=$(curl -s -m 10 -o "$BODY" -w "%{http_code}" -H "Host: $3" "http://127.0.0.1:18080$4"); fi
  body=$(head -c 60 "$BODY" | tr -d '\n')
  if [[ "$out" =~ ^($2)$ ]]; then r=PASS; else r=FAIL; fails=$((fails+1)); fi
  printf "%-4s %-48s got %s want %s  %s\n" "$r" "$1" "$out" "$2" "$body"
}
check "shop / no cookie -> login"              302 shop.edge.test /
check "shop /mcp no cookie -> login"           302 shop.edge.test /mcp
check "shop / with valid brain token -> login" 302 shop.edge.test / "$H: $V"
check "brain / no cookie -> login"             302 kb.edge.test /
check "brain / valid token -> still login"     302 kb.edge.test / "$H: $V"
check "brain /mcp no token -> refused"         401 kb.edge.test /mcp
check "brain /mcp wrong-key token -> refused"  401 kb.edge.test /mcp "$H: $X"
check "brain /mcp other-brain token -> denied" 403 kb.edge.test /mcp "$H: $W"
check "brain /mcp valid token -> backend"      200 kb.edge.test /mcp "$H: $V"
check "brain /mcpx valid token -> login"       302 kb.edge.test /mcpx "$H: $V"
check "auth host callback route exists"        "30[0-9]|40[0-9]" auth.edge.test /oauth2/callback
kill $PF 2>/dev/null
echo "FAILS=$fails"
[ "$fails" = 0 ]
