# Bedrock provisioned-throughput lifecycle

On-demand foundation models and inference profiles are capability bindings. They
create no paid throughput resource. Provisioned throughput is a separate paid AWS
resource; selecting a model is not evidence that a commitment is ready.

The Bedrock driver saves the exact native throughput ARN in a versioned
`ManagedService.backend_ref` handle, with the service and organization UUIDs,
model fingerprint, units, commitment and original provision-intent fingerprint.
All fields fit the existing 512-character column; identities are never truncated.
New allocations retain their original log-prefix fingerprint, so changed plugin
settings cannot redirect cleanup after the native resource disappears. The intent digest
uses canonical base64url encoding of all 256 SHA bits, not a shortened hash; the
longest supported recovered handle is 504 characters.
The intent includes the original app, environment, cluster and region identities.
Fresh workflow activities reconstruct the driver and observe AWS rather than
relying on a process-local record. Model lifecycle probes, bindings, updates and
removals receive the current database owner's organization UUID. Readiness probes
also receive the current stored desired configuration. Log-group creation and
retention remain best effort; a binding's log-group name is not proof that AWS
model invocation logging is enabled.

Only `InService` with matching native target and live ownership is available.
`Creating` and `Updating` remain pending; `Failed`, unknown states, denied reads,
malformed responses and mismatched ownership are refused. A disappeared recorded
paid resource is not replaced automatically. Bare SDK status calls without desired
configuration establish recorded native readiness, not current desired-config
admission; the application's readiness activity supplies that configuration.

Bindings invoke the provisioned **ARN**, and grant invocation on that exact ARN.
The unchanged on-demand/inference-profile path uses the configured foundation
model or profile and its existing exact profile/destination grants.

## Creation, uncertainty and recovery

AWS accepts a stable service-derived name and `clientRequestToken`, and an array
of lower-case `key`/`value` tags. The driver looks up only the exact expected names
or recorded ARN. Only a genuine SDK `ResourceNotFoundException` allows initial
creation. A timeout or denied read is an unknown outcome, never absence.

Creation requires canonical service and organization UUIDs, bounded integer model
units, `OneMonth` or `SixMonths`, and a foundation model ID or exact regional model
ARN. Custom models require an exact ARN in the declared credential account;
custom-model names and inference profiles are not accepted as paid model sources.
Native source, desired source, current/desired units and commitment must match.

After creation, native observation and live ownership must confirm the returned
ARN before the driver reports success. A lost response may already have allocated
paid capacity. Retry the original service and provision intent: exact-name recovery
and the same AWS idempotency token prevent a second allocation. Changing the source
or owner does not create a different token. Never bypass this refusal by clearing
`backend_ref`, inventing ownership tags or adopting a resource from a broad list.
AWS's idempotency guarantee is described in
[CreateProvisionedModelThroughput](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_CreateProvisionedModelThroughput.html).

For a legacy name-only **paid** handle, recovery requires the original explicit
`model_id`, `model_units` and `provisioned_throughput` configuration and the original
provision spec. Every original ownership dimension must be present and exact:
platform marker, service UUID, organization/app/environment slugs, cluster and
isolation, plus all originally supplied binding/custom tags. Native source, units
and commitment must match. The driver returns an upgraded ARN handle with the
original request-intent hash, full SHA-256 ownership-tag fingerprint and original
name. Legacy tags do not prove the original CloudWatch log prefix, so recovery
leaves log identity unknown even when today's prefix happens to match. It does
not write AWS tags. Later fresh reads require that tag fingerprint
and current owner to remain unchanged. Bare legacy status or binding cannot infer
this intended source from today's defaults.

If original configuration or tag evidence is missing, ambiguous or foreign, stop.
There is no automatic adoption or forced recovery path. Recover the original
record/configuration evidence before retrying; a guessed model ID or a slug match
alone is insufficient. Legacy on-demand/profile bindings remain supported. A
fresh ambiguous legacy status probe needs `GetProvisionedModelThroughput` to prove
no paid resource exists; denial remains unknown rather than available.

## Updates, removal and rollback

Only on-demand `model_id` is advertised as editable. A full unchanged paid spec,
including its commitment, can be confirmed against live `InService` state; model,
units or commitment changes are refused without a provider update. AWS's native update API does not expose
units or commitment changes, and restricts model changes:
[UpdateProvisionedModelThroughput](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_UpdateProvisionedModelThroughput.html).
This driver does not claim to implement paid custom-model replacement. Plan a
separate replacement service with a new identity and preserve the old resource
until the replacement is verified. Automatic reprovision of the same paid service
identity is unsupported; changing desired configuration cannot manufacture a
completed update or a new purchase generation.

The driver checks the native `commitmentExpirationTime`. Unknown expiry or a future
expiry blocks deletion, including `force_destroy`. AWS cannot delete throughput
before its commitment ends:
[DeleteProvisionedModelThroughput](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_DeleteProvisionedModelThroughput.html).
After expiry, deletion uses only the verified exact ARN. An accepted deletion or
lost reply is retried by reading that ARN; absence is idempotent and does not start
another allocation. New allocations retain the exact service-derived log name
and prefix fingerprint, even after throughput disappears. Changed log-prefix
configuration refuses cleanup before effects; restore the original configuration
rather than guessing a new path. Recovered legacy resources and older versioned
handles lacking proved original log identity remain readable, but delete-data
removal is blocked before native deletion or log cleanup. No automatic path
certifies or deletes their historical log groups. Failed CloudWatch log deletion
remains unconfirmed and retryable; it is not reported as deleted.

Throughput has no snapshot/restore primitive. The generic data-preserving teardown
requires a verifiable snapshot, so this driver refuses that workflow path for paid
throughput. Explicit delete-data teardown can remove throughput after expiry and
its owned invocation log group only when its original log identity is proved.
Preserve/export any required logs separately before that destructive path; a metadata-only snapshot is not a replayable paid resource.
Direct driver removal with `delete_data=False` preserves invocation logs.

Rollback to the prior application binding is useful only while that exact old
throughput is still owned and `InService`. Restoring desired configuration alone
cannot restore a deleted resource or undo its AWS billing commitment.

The lifecycle credential needs `GetProvisionedModelThroughput`,
`ListTagsForResource`, creation and (after expiry) deletion permissions, plus the
existing CloudWatch log-group permissions. Invocation remains workload identity.
Native state fields are documented in
[GetProvisionedModelThroughput](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_GetProvisionedModelThroughput.html),
and ownership tag shape in
[ListTagsForResource](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_ListTagsForResource.html).
This implementation's native tests use actual botocore schemas and owned protocol
fakes; they do not certify account permissions, model availability, purchase,
latency or successful live inference.
