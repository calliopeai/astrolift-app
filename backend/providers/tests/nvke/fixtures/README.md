# CAPI admission fixture

`cluster.x-k8s.io_clusters.yaml` is the unmodified Cluster CRD from
[Cluster API v1.13.6](https://github.com/kubernetes-sigs/cluster-api/blob/v1.13.6/config/crd/bases/cluster.x-k8s.io_clusters.yaml),
the version selected by the pinned NVKE management installer. SHA-256:
`b72f04bb258d22fafd0c4fbba98feea08b103f1b854a98ebdb14e6a3ab8e9f24`.
Its Apache-2.0 license is preserved in `LICENSE.cluster-api`.

Access integration tests register this fixture only against the explicitly selected
task-owned loopback envtest API. Tests use real API admission and metadata UIDs;
they do not install CAPI controllers or claim workload provisioning qualification.

## Immutable install artifact driver

`nvke_operator_immutable_driver.py <agent-source-root> <providers-root>` exercises
the SDK against the real local mTLS agent fixture. It prepares each phase payload
and the sealed remaining execution tree, discards successful replies, restarts
the agent, and checks historical reads and retained-intent recovery without
repeating image rewriting or tree assembly. It also checks original source
preservation and no additional registry acquisition. Select the owned native
bundle, Helm and source-built chart helpers through the same environment as the
agent integration tests. Run against frozen sources while recording their hashes.

These controls do not authorize native continuation. The SDK unit suite separately
checks closed requests, capability negotiation, lineage, invalid selections,
redacted summaries and ambiguous lost POST handling. A unit pass alone does not
qualify the real driver or the App API, CLI, UI and deployment paths.
