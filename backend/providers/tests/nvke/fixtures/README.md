# CAPI admission fixture

`cluster.x-k8s.io_clusters.yaml` is the unmodified Cluster CRD from
[Cluster API v1.13.6](https://github.com/kubernetes-sigs/cluster-api/blob/v1.13.6/config/crd/bases/cluster.x-k8s.io_clusters.yaml),
the version selected by the pinned NVKE management installer. SHA-256:
`b72f04bb258d22fafd0c4fbba98feea08b103f1b854a98ebdb14e6a3ab8e9f24`.
Its Apache-2.0 license is preserved in `LICENSE.cluster-api`.

Access integration tests register this fixture only against the explicitly selected
task-owned loopback envtest API. Tests use real API admission and metadata UIDs;
they do not install CAPI controllers or claim workload provisioning qualification.
