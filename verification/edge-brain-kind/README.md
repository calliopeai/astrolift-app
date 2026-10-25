# Edge brain token, on a real Envoy Gateway (#2526)

Unit tests compare dicts; Envoy Gateway decides what a policy actually does.
#2320 shipped a shared-gate pass-through that opened every app the first time
it reached a cluster (#2524 hid it behind a 422 until then). This applies the
edge exactly as `k8s_native.edge_gateway` renders it, a gated app and a brain,
and checks every case by status code:

    PYTHON=/path/to/backend/venv/bin/python ./up.sh   # prints OUT=...
    OUT=<dir> ./probe.sh                              # exits non-zero on any FAIL
    kind delete cluster --name brainedge

The one test-only change: the brain provider's keys are inlined (`localJWKS`)
instead of fetched from the control plane over https.

Negative control: setting `spec.oidc.passThroughAuthHeader: true` on
`edge-oidc` makes `shop / no cookie` answer 200; the probe fails 7 cases.
