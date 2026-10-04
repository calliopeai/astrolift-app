# Vertex endpoint lifecycle and recovery

The GCP `model_endpoint/vertex_ai` driver provisions a Vertex Endpoint and a
single deployed model through the existing reviewed managed-service workflow.
Install the provider package's `gcp` extra, including `google-cloud-aiplatform`.
This path uses regional Vertex API clients and a registered Model resource such
as `projects/<project>/locations/<region>/models/<model-id>`. This implementation
requires that artifact to be in the configured project and region; publisher
model names, artifact upload and cross-region/version-alias adoption are not
implemented. Model registration, access, inference compatibility and billing
remain operator responsibilities.

## Resource and operation identities

`display_name` is a human label, not an endpoint resource ID. The service's
`backend_ref` records `model_endpoint/projects/<project>/locations/<region>/endpoints/<endpoint-id>`.
The server-owned `lifecycle_policy.vertex_operation` journal records the actual
endpoint name, deployed-model ID, long-running operation names and per-phase
progress. It preserves other lifecycle-policy keys. Public configuration cannot
provide a write reservation or operation receipt.

Each create, deploy, replica mutation, traffic update, undeploy and delete has a
separately committed request reservation before its bounded SDK call. The next
transaction rechecks the existing reviewed owner/placement/configuration digest
and exact original operation, then checks the reserved request digest before
sending. The journal binds the service, organization, project, cluster, provider,
GCP project/region, desired configuration, workflow identity and operation start.
It does not add worker actor/credential reauthorization to the existing reviewed
placement contract.

The SDK's returned operation name is saved promptly. Subsequent activity attempts
construct fresh clients and poll that recorded operation with bounded individual
reads, rather than waiting on `Operation.result()` while holding database locks.
The activity has a bounded polling window and heartbeats between attempts.
Known pending operations retain their receipt for an original-workflow retry.
Failed or malformed operations never imply that the model is deployed.

Provision readiness requires a completed deploy operation, an owned endpoint,
the exact observed deployed-model ID/artifact, machine type, minimum/maximum
replicas and traffic matching the saved reviewed serving request, and available
replicas meeting that requested positive minimum. Native settings cannot lower
the saved minimum or substitute another machine to make a request appear ready.
Divergent or not-yet-converged observations stay pending without redeploying.
Status and binding only read resources/operations;
they never create, deploy, mutate or delete. A binding uses the actual endpoint
and deployed-model IDs. This is not an inference, IAM, network-reachability or
model-quality test.

## Lost replies and legacy records

A timeout, transport failure or receipt transaction rollback can leave a cloud
request accepted but its result unknown. The prior reservation survives. An
attempt without its verifiable operation receipt refuses to resend that request.
Observing a uniquely owned endpoint or deployed model may preserve its identity
for investigation, but does not establish completion of a lost create/deploy
operation. Resources can continue incurring charges while recovery is pending.

Operators must inspect the original workflow, saved reservation/receipts and
the exact owned Vertex resource or operation before deciding recovery. Do not
clear the journal or start another allocation merely because a reply was lost.
There is no new automatic repair or reservation-reset API in this leaf. Unknown
outcomes are deliberately a recovery-required failure; this provides at-most-once
submission per reserved phase, not exactly-once network delivery or guaranteed
automatic recovery. A process failure before sending can therefore produce zero
cloud executions and still require review.

Legacy `model_endpoint/<display-name>` handles use bounded inventory reads and
require one coherent service owner in the configured project/region. Missing,
ambiguous, contradictory or incomplete inventory refuses; a display name is
never converted into a fabricated resource ID. A legacy endpoint with no saved
deployment operation is not reported ready simply because it exists. An
explicit reviewed removal can recover a unique observed deployment ID,
but an unreviewed direct write or the legacy app-onboarding allocator refuses
before provisioning. Existing Temporal workflow inputs and command order remain
unchanged.

An old record with no saved serving request cannot infer its desired settings
from the current endpoint: status/binding remain unavailable and in-place update
refuses with an operator-recovery reason. The original reviewed provision may
record its exact unchanged requested configuration; this does not resend an
unknown reservation or create an automatic repair path.

## Update and removal limits

- Replica updates use the actual deployed-model ID and explicit supported
  `dedicated_resources.min_replica_count`/`max_replica_count` update masks.
  In-place machine type, artifact and network changes refuse; use a separately
  reviewed reprovisioning plan after resolving any existing operation.
- Single-model traffic accepts only 100 percent or an empty map for no traffic.
  Traffic updates include the observed Endpoint etag and a `traffic_split`
  update mask. A changed observation cannot silently rewrite a reserved request.
- Removal refuses endpoints containing other deployments. Live traffic requires
  explicit `force_destroy`; undeploy and endpoint delete have separate recorded
  operations. The registered Model is preserved. `delete_data=true` refuses
  because ownership of that artifact has not been proven, including when the
  recorded Endpoint is already absent. Endpoint absence never establishes Model
  artifact deletion.
- Endpoint snapshot/export and restore are unsupported. Retaining a registered
  Model is not a replayable endpoint snapshot. The legacy
  `public_endpoint_enabled` configuration field does not verify private routing;
  this path passes an explicit Vertex `network` resource when configured and
  leaves transport verification to the operator.

## Evidence boundary

Focused tests use generated Google request/response types and actual API-core
operation futures, with distinct human labels and numeric resource IDs. Real
PostgreSQL tests cover committed reservations, response/commit rollback,
concurrent single submission and placement withdrawal. A native Temporal test
executes the production workflow/activities and replays its history. All provider
effects in this proof are controlled SDK fixtures; no Google cloud allocation,
purchase, model upload or inference is performed.

Primary contracts: [Endpoint and DeployedModel](https://github.com/googleapis/googleapis/blob/master/google/cloud/aiplatform/v1/endpoint.proto),
[EndpointService requests and operations](https://github.com/googleapis/googleapis/blob/master/google/cloud/aiplatform/v1/endpoint_service.proto),
and [Python API-core operations](https://googleapis.dev/python/google-api-core/latest/operation.html).
