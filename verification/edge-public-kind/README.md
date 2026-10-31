# Per-app public switch, on a real Envoy Gateway (#2504)

A public app must be public by its own route going without the gate label,
never by loosening the shared `edge-oidc` policy: a change to that one policy
opened every gated app on a cluster on 2026-10-25 (#2526). Unit tests compare
dicts; this applies the edge exactly as `k8s_native.edge_gateway` renders it,
with the gate decided by `app_route_gated` (the function the deploy path
calls), and checks each case by status code:

    PYTHON=/path/to/backend/venv/bin/python ./up.sh   # prints OUT=...
    OUT=<dir> ./probe.sh                              # exits non-zero on any FAIL
    OUT=<dir> REFUSED_PHASE=mutant ./probe.sh         # negative control: must FAIL
    kind delete cluster --name publicedge

Four apps on one edge: `pub` (asks to be public), `kb` (a brain that follows
`pub`, with its `/mcp` route that admits a brain read token and nothing
else), `shop` (never asks) and `lst` (asks, but has an access list). The probe applies four renders in turn,
server-side with the control plane's field manager, as a deploy or the switch
does: not public (the baseline), public (the gate label comes off the live
route), made private again, then the cluster refusing while the app still
asks. The backend echoes request headers, so it also shows that a public
route carries no gateway secret and that a client cannot supply one or an
identity header.

The one test-only change: the brain provider's keys are inlined (`localJWKS`)
instead of fetched from the control plane over https.

The negative control renders the refusing cluster's route ungated anyway, and
the brain public without its token route; the probe must report both cases as
FAIL.
