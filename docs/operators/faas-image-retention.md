# FaaS and static-builder image retention

AWS FaaS deployments retain their container image in ECR before creating or
updating any implied managed-service row or invoking Lambda. A tag-based image
is resolved to its canonical digest; the Lambda provision and update paths
receive that protected digest. A missing image or a failed retention request
blocks the activity before service state or Lambda code changes.

Each deployment owns its environment/deployment retention tags, including
rollback deployments. Retries reuse those tags. The snapshot's
`ecr_retention_pins` combines pins from FaaS, container workloads and static
builder stages; a later stage preserves earlier pins. Successful rollout
retirement applies the same history policy to these pins as container images.

A platform static build also retains its builder image before creating its
IRSA role, ServiceAccount or Kubernetes Job, then runs the Job by canonical
digest. The default builder comes from public ECR and is external to the
install's private registry, so it is unchanged. A private ECR mirror selected
through the builder image constant uses the deployment's retention policy.

ZIP packaging has no container image to retain; its build pipeline remains
separate. Images outside the configured ECR registry remain under their
registry owner's retention policy. Install deploy roles need ECR read/tag
permissions described in the [backend bootstrap](../../backend/bootstrap.md).

Validation: `astrolift_workflows/tests/test_faas_image_retention_2186.py` uses
real PostgreSQL rows, the ECR provider against its SDK fake, the Lambda provider
against its SDK fakes, and the static build renderer with a recording cluster.
It verifies digest propagation, retry and rollback ownership, preservation
across stages, unchanged retries after source tags move, and failure before
service, identity or Job writes.
