# Plugin Config Isolation and Least Privilege

This doc captures the security boundaries Astrolift's provider plugin
SDK enforces by default, the IAM grants each plugin emits, and the
operator-side wiring required to keep tenant blast radius bounded.

The audience is plugin authors + operators standing up a tenant
cluster. End-users (app developers) don't need to read this — the
defaults are secure-by-default.

## Boundary 1: Plugin config schema

Each `ProviderPlugin` declares a `config_schema` JSON Schema. The
control plane validates tenant-supplied config against the schema
**before** the driver constructor sees it. Implications:

- Tenants cannot inject unknown keys (additionalProperties=false on
  every plugin schema). A typo or a hostile injection fails fast.
- `required` fields enforce the minimum bind contract. A bind that
  omits `region` / `account_id` / `project_id` / `subscription_id`
  is rejected at register time, never reaching the cloud SDK.
- Sensitive fields (KMS key ARN, IRSA role ARN, OIDC issuer) live in
  the config but the schema constrains their shape so a typo can't
  cross-account-leak a wrong identifier.

The schema is the only API surface a tenant has into the driver.
Drivers MUST NOT read tenant data from any other source (env vars,
kubeconfig context, etc.) at provision time.

## Boundary 2: IAM grants emitted by managed-service drivers

Every managed-service driver returns a `Binding` with `iam_grants`.
The operator wiring (typically Terraform or operator-driven RBAC)
applies the grants at provision time. Each grant is the **minimum**
needed for that workload to use that resource:

| Driver | Grant scope |
|--------|-------------|
| AWS S3 | `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` on the per-app bucket only |
| AWS SQS | `sqs:SendMessage`, `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on the per-app queue only |
| AWS Secrets Manager | `secretsmanager:GetSecretValue` on the path-prefixed secret only |
| AWS SSM | `ssm:GetParameters`, `ssm:GetParametersByPath` on the path-prefixed parameter only |
| GCP GCS | `roles/storage.objectAdmin` on the per-app bucket only |
| GCP Pub/Sub | `roles/pubsub.publisher` + `roles/pubsub.subscriber` on the per-app topic / subscription |
| GCP Secret Manager | implicit via Workload Identity binding to the SA, scoped to the secret prefix |
| Azure Blob | `Storage Blob Data Contributor` on the per-app container only |
| Azure Service Bus | `Azure Service Bus Data Sender` + `Receiver` on the per-app queue |
| Azure Key Vault | implicit via Workload Identity Federated Credential, scoped to vault |

Drivers do **not** request account-wide or subscription-wide grants.
A driver that returns a `*` resource in its `Grant.resource` is a
review-blocker.

## Boundary 3: Workload Identity (no static credentials)

Plugins emit Pod-level workload-identity annotations rather than
secret-mounted static credentials:

- AWS: `eks.amazonaws.com/role-arn` annotation (IRSA)
- GCP: `iam.gke.io/gcp-service-account` annotation (Workload Identity)
- Azure: `azure.workload.identity/client-id` + `tenant-id`
  annotations (Workload Identity Federated Credentials)
- k8s_native: projected SA token + audience annotation

Static credentials (long-lived API keys) appear only as the absolute
fallback for variants where Workload Identity is unsupported (e.g.,
non-GKE clusters reading from Artifact Registry). When a static
credential is required, the plugin emits a marker Secret with an
explicit `astrolift.io/note` annotation explaining the constraint.

## Boundary 4: Storage encryption defaults

Every data-bearing variant has a registered `EncryptionPolicy` in
`_sdk/storage_encryption.py`. New variants without a policy fail
the encryption preflight at bind time. CMEK is supported on every
cloud-managed variant; on k8s_native variants, encryption is
delegated to the StorageClass / CSI driver layer.

Compliance frameworks (PCI, HIPAA, FedRAMP-high) require CMEK on
specific variants. `check_encryption()` enforces — bind fails if
the framework lookup matches but no key was supplied.

Local-dev mode downgrades CMEK-required failures to warnings so
developers can iterate without a full KMS plumbing, but the warning
is preserved so dev → staging promotion re-fails the preflight.

## Boundary 5: Cross-account workload identity chains

Multi-account topologies can declare an `IdentityChain` that walks
from a Pod's k8s SA through one or more cloud roles to a terminal
resource. The validator (`_sdk/identity_chain.py`) detects:

- Cycles (same account/role twice in a chain)
- Unsupported cross-plugin transitions (only the
  `SUPPORTED_CROSS_PLUGIN_PAIRS` set federates)
- Missing OIDC audience claims on cross-plugin hops

A failed validation rejects the bind. The trust policy at each hop
is wired by the operator out of band; the SDK enforces shape, not
the actual trust mutation.

## Boundary 6: Edge security defaults

Every ingress driver applies `DEFAULT_PROFILE` from
`_sdk/edge_security.py` unless the tenant explicitly opts out:

- TLS 1.3 floor (TLS 1.2 only via opt-in)
- HSTS with 1-year max-age + includeSubDomains + preload
- WAF managed common-rules ruleset on
- X-Frame-Options DENY, X-Content-Type-Options nosniff,
  Referrer-Policy strict-origin-when-cross-origin

Per-controller annotation translators emit the right config for
nginx-ingress, ALB, GCP Ingress, App Gateway / AGIC.

## Boundary 7: Postgres extension allow-listing

`_sdk/postgres_extensions.py` declares per-variant allow-lists.
Drivers call `validate_extensions()` at provision time before
running CREATE EXTENSION. Extensions outside the allow-list are
rejected with a clear error, even if a SUPERUSER session would
technically allow them.

The CNPG variant has the broadest list (it runs custom Postgres
images so extension support is image-driven). Cloud-managed
variants are constrained by their respective parameter-group
surfaces.

## Operator checklist

When standing up a new tenant cluster:

1. Validate the bind via `validate_cluster_binding()` — confirms
   every required role + every declared service dependency has a
   driver (or a composition delegation).
2. Run `probe_capabilities()` to detect installed operators (CNPG,
   cert-manager, external-dns, etc.). The cluster registration
   stores the result for the binding validator to consult later.
3. Run preflight via `k8s_native.preflight.preflight()` for each
   operator-backed managed-service variant the tenant intends to
   use. Apply install hints from failures.
4. Apply the StorageClass / VolumeSnapshotClass manifests rendered
   by `_sdk/csi.render_storage_class()` for the cluster's CSI
   driver, with the operator's CMEK key reference.
5. Confirm the WAF / Cloud Armor / App Gateway WAF policy referenced
   by the ingress driver is pre-provisioned in the cluster's account
   — the driver references by name; it does not create the WAF.

## What the SDK does NOT enforce

- Network egress controls — these live in the cluster's network
  policy engine (Calico, Cilium, AWS VPC CNI). The SDK detects via
  `ClusterCapabilities.network_policy_engine` but does not render
  policies.
- Pod security admission level — the SDK records `psa_admission_level`
  in capabilities; per-namespace label decoration is the operator's
  install template.
- Audit logging — k8s audit log + cloud audit log
  (CloudTrail / Cloud Audit Logs / Activity Log) configuration is
  cluster install scope, not per-deploy.


## Ownership labels and tenant custom metadata (#2098)

Managed-service IDs come from the lifecycle's persisted service GUID, never
from a tenant label or a live resource's claimed owner. Eventarc, Managed Kafka,
Pub/Sub and PSC reject the entire normalized Astrolift label namespace in
service config, including dot, slash, underscore, hyphen, case and legacy
`x-astrolift` spellings. Pub/Sub also validates subscription labels. Refusal
happens before resource writes. Updates retain the platform ownership envelope;
Pub/Sub verifies the live topic ID and the actual subscription topic reference
before reconciliation, prune, teardown or returning a binding. A conflicting
child ID is refused even when its topic or name matches. Legacy unlabelled
children may be reconciled only under their verified, immutable parent topic.

Unlabelled legacy topics require the internal exclusive recorded-handle proof;
a conflicting ID always refuses that proof. Reprovision retains the recorded
physical topic name. Missing source IDs fail closed on mutating/binding paths.

The AWS/Azure audit covers every tenant custom-tag serializer in the managed
provider trees: AWS `_base.tags_for` prefixes keys with `astrolift.io/extra/`;
Azure ARM and Files emit `astrolift-extra-<name>-<digest>`; classic Files uses
that same serializer; legacy Blob metadata uses `astrolift_io_extra_`. These
keys cannot overwrite platform IDs, binding IDs or parent identity tags.
Event Grid's separate subscription-label list explicitly rejects its exact
`astrolift-managed` ownership sentinel and retains the checked parent topic
scope. Recording-client regressions exercise hostile custom keys and existing
Azure cross-driver foreign-owner refusals; they do not certify live cloud IAM.


### Immutable AWS managed-service names (#2032, bounded scope)

New S3 buckets, SQS queues and DynamoDB tables use
`<sanitized-operator-prefix>-<complete-managed-service-UUID-hex>`. The platform
passes the saved service GUID; the driver neither generates an ID nor falls
back to tenant slugs, integer keys or hints. The UUID must be canonical and
nonzero (current real service records generate UUIDv7). Missing, malformed,
nil or noncanonical direct SDK identities refuse before provider calls.

The complete 32-hex identity is retained when prefixes are truncated:
[S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html)
uses at most 63 characters;
[SQS](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_CreateQueue.html)
uses at most 80 including its `.fifo` suffix; and
[DynamoDB](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Constraints.html)
uses at most 255. Names are lowercase ASCII with a sanitized operator prefix;
S3 reserved/invalid names refuse instead of issuing an invalid create. Human
org/app/environment/service names remain in display metadata and tags.

The existing `ProvisionSpec.recorded_handle` takes precedence over new naming
defaults, after exact driver-kind and physical-name validation. It is never
truncated, silently replaced or treated as ownership authority. Reprovision
preserves an owned legacy name and its data even after display-name/prefix
changes. Recorded SQS type remains authoritative if an operator default
changes; an explicitly incompatible `fifo` value refuses instead of renaming.
Retries with unchanged operator defaults/type use the same saved identity and
name even before the returned handle is finalized. Operators must keep those
defaults stable until that first handle is recorded.

All three provision paths require the live platform marker and exact source
UUID for a pre-existing target. Neither matching old slugs nor a claimed
`recorded_handle_exclusive` flag permits retagging a foreign/unmarked resource.
Unmarked legacy resources need verified ownership backfill. S3 unavailable
lookup proof is a refusal, and the SQS existing-name exception path rechecks
current ownership before attribute/tag reconciliation. These checks are not
atomic cloud create/compare-and-tag preconditions; independent cloud admins
must not replace/retag resources during operations. A globally occupied S3
UUID name is refused, never replaced with a fresh random identity.

This leaf covers only these three AWS driver families. Other AWS/GCP/Azure/native
families under #2032 remain separate; #2021's explicit tenant-set identities and
`adopt_existing`, and #2029's SES identity contract remain excluded. DynamoDB
restore/adoption, provisioning cloud races and the remaining broad ownership
acceptance in #2098 are not closed by the naming change.

### Immutable GCP Pub/Sub topic names (#2032, bounded scope)

New `topic/pubsub_topic` resources use
`<normalized-operator-topic-prefix>-<complete-managed-service-UUID-hex>`.
Canonical nonzero saved UUIDs are required, including the platform's current
UUIDv7 records; the driver never invents an ID or substitutes human slugs.
Only the cosmetic prefix is truncated to 21 characters, so new topic IDs
retain all 32 identity characters within 54 characters. Prefix normalization
preserves the API's alphabetic start and reserved `goog` rules; invalid/empty
normalized prefixes and invalid recorded IDs refuse before provider calls.
The [official Pub/Sub resource-name contract](https://docs.cloud.google.com/pubsub/docs/pubsub-basics)
limits topic/subscription IDs to 3–255 characters and declares their allowed
alphabet and reserved prefix.

The existing declared subscription suffix is normalized to at most 200
characters. Reserving those 200 characters plus a separator means new child
IDs retain the complete topic UUID and distinct normalized suffixes within
255 characters, even with a long operator prefix. Normalized declaration
collisions refuse. Recorded legacy topic IDs keep their exact physical names
and the existing child-name mapping, including historical truncation. If two
new declarations under a long recorded parent map to the same physical child,
the entire operation refuses before topic or child changes. Existing topic
and subscription data is not migrated or renamed.

The current live source-label and exclusive-record ownership gates remain in
place. A recorded name is not authority to retag a foreign owner. Current child
owners are preflighted before parent reconciliation, and actual SDK typed
not-found/already-exists outcomes—not diagnostic substrings—decide absence or
create reconciliation. Unavailable lookup proof never permits creation.
Operators must keep the prefix stable until the first handle is recorded;
after that, recorded handles override cosmetic prefix/display changes. Cloud
admin replacement/retag races are not certified as atomic transactions.

This leaf covers only GCP `topic/pubsub_topic`, not the separate `queue/pubsub`
driver or other GCP/AWS/Azure/native families. #2032 stays open; #2021's explicit
tenant-set/adoption identities and #2029's SES identities remain excluded.
The PostgreSQL lifecycle proof runs the real Google Pub/Sub SDK through a
bounded local recording gRPC server. It verifies serialized API requests,
identity and no-write refusals, not GCP IAM, storage or delivery semantics.

### Pub/Sub cleanup outcomes (#2098, bounded scope)

The topic driver reports explicit `ownership_refused` for a current source or
child owner mismatch, and `ownership_unknown` when SDK reads, subscription
inventory/pagination or deletion fail without a concrete typed not-found
outcome. Invalid handle/config and outside-declaration refusals retain their
existing specific codes and add unknown authority. The central lifecycle
therefore keeps those operations failed, even if a diagnostic, invalid handle,
reserved custom label or outside-declaration child name contains `not found`
or `NoSuchBucket`. Force/delete-data flags cannot bypass current source or
child ownership. Permanent owner refusal is non-retryable; unavailable provider
operations retain their retry behavior and never imply completed cleanup.

Inventory must complete and every present child's immutable topic and source
labels must pass before any destructive call. A failed second SDK page cannot
produce deletes from a partial first page. A typed parent `NotFound` still
converges for the parent; it does not assert former subscriptions are absent
or delete unobserved children. A child with a typed `NotFound` during preflight
is omitted from the delete plan; one disappearing during deletion is already
gone. A failed child delete stops before deleting the parent, while a denied
parent delete after successful child deletions remains failed partial cleanup.
The retained-message acknowledgement and explicit-force requirements remain.

These checks are current API observations, not atomic cloud replacement guards
or certification of GCP IAM/delivery. Other drivers, broad #2098 acceptance and
external resource replacement/retag races remain separate work. Tests use the
real Google SDK, actual protobuf pagination and controlled localhost responses,
with real PostgreSQL lifecycle records and no tenant cloud calls.

### GCP Pub/Sub queue identity, ownership and retention (#2032, #2098)

New `queue/pubsub` topics use a complete canonical nonzero persisted service
UUID in hexadecimal; only the normalized cosmetic operator prefix is
truncated. Parent IDs are at most 251 characters, reserving the literal `-sub`
inside the subscription's 255-character ID limit. Exact valid recorded parent
and default-child paths override new naming defaults. Invalid recorded IDs,
unknown identities and recorded parents that cannot fit their derived child
refuse; no fallback slug, fresh ID, truncation or rename is attempted. Prefixes
must stay stable until the first handle is recorded.

Provision, binding, readiness, update refusal and cleanup read the current
topic and default subscription before accepting authority. The parent needs
its exact service-ID label or the actual central exclusive legacy record;
matching human labels do not suffice. Returned topic/subscription resource
names must exactly match the requested configured-project paths; missing or
foreign response identities cannot reuse owner labels as authority. A missing
or malformed configured project refuses before any RPC. A legacy unlabelled default child may
use its immutable topic reference only after the current parent's owner has
been established. New children carry the complete service-ID label. Foreign
labels and mismatched topic references always refuse. A missing parent with
a remaining child is unknown authority, including a child pointing to GCP's
`_deleted-topic_` marker; the driver does not adopt or remove it by inference.

No queue operation has implicit SDK retries. Every RPC uses at most five
seconds and the remaining 20-second operation budget. Cleanup explicitly
requests at most ten 100-item inventory pages and at most 1,000 total child
paths. Overflow, repeated cursors, incomplete pages, inaccessible reads and
expired budgets refuse before destructive effects. Outside-default children
require explicit force plus their own current source-ID/topic proof; they
cannot use the unlabelled legacy default-child fallback. Foreign-project
children always refuse. Cloud-admin replacement/retag races remain outside
these observation checks; they are not atomic cloud incarnation locks.

The old "drain" and force-success claims are removed. Pub/Sub
[subscription deletion drops its retained messages](https://docs.cloud.google.com/pubsub/docs/reference/rest/v1/projects.subscriptions/delete),
while [topic deletion leaves subscriptions and their backlog](https://docs.cloud.google.com/pubsub/docs/delete-topic).
Without explicit `delete_data=true`, a present queue returns the existing
retained-message refusal; it never seeks, acknowledges or deletes data.
Snapshot/restore remain unsupported, so the central data-preserving path
refuses without cloud mutation. Force cannot bypass current ownership or turn
failed subscription deletion into success; the parent is not deleted after
an unconfirmed child delete. Concrete typed absence of both recorded targets
still converges. A late parent-delete failure remains failed partial cleanup.
Structured refused/unknown codes prevent diagnostic substrings from becoming
successful cleanup. Binding values, role scopes and the lack of editable
queue settings remain unchanged.

Real PostgreSQL production lifecycle tests use actual Google Publisher and
Subscriber SDK calls through a bounded localhost protobuf/gRPC server. They
verify API identity, deadlines, paging and refused side effects, not live GCP
IAM, persistence or delivery guarantees. Other driver families and broad
#2032/#2098 acceptance stay open; #2021 and #2029 remain excluded. The separate
`topic/pubsub_topic` driver retains its existing transport/inventory limits
and external replacement-race limitations; this queue leaf does not silently
extend those contracts.

### AWS live binding and S3 incarnation checks (#2098)

S3, SQS and DynamoDB bindings require the actual live resource's platform marker
and exact persisted managed-service ID before returning environment values,
mounts or IAM grants. An old handle, matching human slugs, a tenant config flag
or unreadable/unmarked tags cannot authorize a new incarnation. Direct SDK
callers must supply the source ID on `ServiceHandle`; source lifecycle activities
already derive it exclusively from the saved service UUID.

The S3 binding-only mount update and all retained/destructive teardown choices
also check live identity before accepting the operation. `force_destroy` does
not bypass ownership, and missing buckets still converge without a write. These
are checks before actions, not atomic cloud compare-and-delete operations;
independent writers with cloud administration rights must not retag/recreate
resources during platform operations.

The operator identity must be able to read the protected tags with
[`s3:GetBucketTagging`](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetBucketTagging.html),
[`sqs:ListQueueTags`](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_ListQueueTags.html)
and
[`dynamodb:ListTagsOfResource`](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_ListTagsOfResource.html).
These reads do not add tag access to the application's returned grants.
DynamoDB collects complete tag pagination, bounded to ten pages/1,000 tags,
and refuses malformed, repeated or unfinished pages. SQS/DynamoDB bindings also
verify returned resource ARN/name/region identity; missing DynamoDB ARN never
becomes an invented `UNKNOWN` grant.

SQS and DynamoDB now use the same live identity proof for updates (including
no-ops), teardown and provider readiness/status. DynamoDB snapshots check the
source before `CreateBackup` and require the returned backup ARN to name that
source. Direct callers must carry the actual source ID on `UpdateSpec` and
`DeprovisionSpec` as well. Tenant config cannot supply it, force flags cannot
bypass it, and malformed or incomplete provider metadata fails closed. SQS
snapshots/restore remain explicitly unsupported, so data-preserving central
teardown refuses rather than claiming that queue messages have been retained.

Only structured provider not-found outcomes (SQS
[`GetQueueUrl`](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_GetQueueUrl.html)
and DynamoDB
[`DescribeTable`](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_DescribeTable.html))
mean an absent resource. Permission failures containing those words remain
failures. SQS refuses safe deletion when its requested approximate message
counts are missing instead of assuming zero; the counts are advisory and do
not provide an atomic drain/delete guarantee. DynamoDB
[`CreateBackup`](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_CreateBackup.html)
returns a backup identity; this bounded change does not certify asynchronous
backup completion or redesign retention orchestration.

This still does not establish ownership protection for every AWS lifecycle
operation. Provisioning name races/legacy adoption, DynamoDB restore/adoption,
other providers' status/snapshot/update/teardown paths and cloud-admin concurrent
retag/recreate races remain separately tracked acceptance work. Legacy live
resources without the immutable source tag require verified operator backfill;
matching tenant slugs alone do not authorize lifecycle operations. Workload
tag-mutation authority is addressed below.


### SQS workload management excludes ownership and policy administration

The SQS `manage` binding mode grants publish/consume operations, queue reads and
`PurgeQueue` on the verified queue only. It does not grant `TagQueue`,
`UntagQueue`, `SetQueueAttributes`, `AddPermission` or `RemovePermission`.
This removes the previous workload `TagQueue`/`SetQueueAttributes` authority:
applications cannot use the issued grant to replace/remove platform identity
or edit the queue resource policy to grant that authority. Other modes retain
their existing message-operation scope.

AWS supports tag-key conditions for tag actions in its
[SQS authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_sqs.html),
but the current provider SDK `Grant` contract contains only resource/actions;
the workload compiler renders unconditional `Allow` statements. It cannot
express a safe custom-tag-only grant, so all workload tag mutation is omitted.
Unrestricted
[`SetQueueAttributes`](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_SetQueueAttributes.html)
also accepts the queue's `Policy` attribute; retaining that permission would
leave a permission-escalation path around simple tag-action omission.

Queue attributes, custom tags and policy changes must instead use platform
lifecycle administration. This change preserves those driver APIs; their
broader live-ownership acceptance audit remains open. Reconcile existing app
workload identities to replace their previous inline grant policies. These
checks cover the platform-issued policy, not separate administrator-supplied
IAM/resource policies that already confer additional privileges.

### Azure single Service Bus queue identity and ownership (#2032, #2098)

This scope covers only `queue/servicebus` (`ServiceBusDriver`) in an existing
operator-managed namespace. New queues use a cosmetic sanitized prefix plus
all 32 hex digits of the persisted, canonical, nonzero managed-service UUID.
Only the prefix is shortened to fit 260 characters. Repeated provisioning
keeps a valid recorded `queue/<physical-path>` unchanged, including nested
legacy paths; changing app/org/service names or the operator prefix does not
move messages or create a second queue. Unknown/generated caller IDs and unsafe
recorded paths refuse before HTTP. Existing queues without a complete,
unambiguous ownership envelope require a separate authorized adoption/recovery
process; this driver does not backfill an unlabelled queue from a slug or config.

Provision/reprovision, status, workload binding, and destructive teardown
read the actual queue first. They require the same
managed-service UUID, platform marker, nonconflicting known identity aliases,
and a returned ARM resource ID matching the configured subscription, resource
group, namespace and exact recorded queue path (case-insensitive as ARM requires).
A missing returned identity is unknown. Foreign, missing, duplicate or
contradictory ownership metadata refuses. Binding still emits
`SERVICEBUS_NAMESPACE`, `SERVICEBUS_QUEUE`, `SERVICEBUS_ENDPOINT` and the existing
Data Sender/Data Receiver grants at that verified queue ARM ID; no namespace-wide
grant or connection credential is added.

In-place update remains permanently unsupported with
`update_not_supported_in_place` and `retryable=False`. It performs no SDK read
or write, even for a foreign, unavailable or malformed source; actual changes
must use the ownership-checked reprovision path.

The Azure extra now requires `azure-mgmt-servicebus>=10.0`. Stable 8.2 and 9.0
queue models cannot serialize/read the required ARM `userMetadata` property.
SDK 10.0 uses API `2026-01-01`, where the queue properties include it. The driver
uses `SBQueue(properties=SBQueueProperties(...))`, ISO-duration typed values,
and verifies the returned owner/target after PUT; tests assert the actual SDK
request body and API-version, not a fake accepting misplaced dict keys. Metadata
values containing a semicolon, ambiguous entries, or a serialized envelope over
1024 characters refuse instead of truncating identity or injecting another key.
Ordinary custom tag names remain safely namespaced by the existing ARM serializer.
See Microsoft's [Service Bus naming rules](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules#microsoftservicebus)
and [queue API contract](https://learn.microsoft.com/en-us/rest/api/servicebus/controlplane/queues/create-or-update?view=rest-servicebus-controlplane-2026-01-01).

Only the actual SDK `ResourceNotFoundError` permits already-gone convergence.
Access/transport failures remain `ownership_unknown`, irrespective of diagnostic
text; permanent refusals retain `external_resource_collision` and additionally
return `ownership_refused`, preventing central diagnostic-based cleanup coercion.
Every management operation has a 20-second call budget, at most five seconds for
each connect/read phase, no SDK retries or redirects. There is no inventory or
account-wide discovery. `force_destroy` cannot bypass any check or failed delete.
`delete_data=False` refuses while retaining the queue; snapshot/restore remain
unsupported, so the central retained-data path fails before any destructive
call. Explicit `delete_data=True` deletes the currently verified owned queue.

Limits remain explicit: ARM queue PUT/DELETE has no supported conditional
incarnation precondition here, so an external actor replacing a resource between
GET and PUT/DELETE is not atomically excluded. Recorded short handles do not
reconstruct unknown historical subscription/provider placement, and this leaf
does not introduce such provenance. The sibling `queue/azure_servicebus` and
`topic/service_bus_topic` has a separate, stricter saved-placement contract below;
these single-queue checks do not supply its historical provenance. The recording HTTP and PostgreSQL tests prove
SDK serialization, dispatch, owner refusal and no destructive calls on retained
paths; they do not certify Azure message delivery or live-service persistence.

### Azure topic/default subscription: exact saved placement (#2032, #2098)

This separate scope covers only `AzureServiceBusDriver`, registered as
`queue/azure_servicebus` and `topic/service_bus_topic`; it does not change the
single-queue driver above. New topic names retain all 32 hex digits of the
persisted canonical nonzero managed-service UUID, with at most nine cosmetic
prefix characters. Topics are at most 42 characters and their default child is
`<topic>-default`, at most 50; tenant/app/environment slugs are not identity.
Only the prefix is shortened, never the UUID. Retries retain deterministic names.

Successful handles save the complete target:
`<kind>/arm-v1/<subscription UUID>/<resource group>/<namespace>/<topic>/<child>`.
Every encoded coordinate is validated before any SDK request and must agree
with current operator placement; the stored handle fits the existing 512-character
column. The topic alias also validates its complete ARM-ID literal against the
512-character materialized binding column before any SDK call. Reads, binding,
updates and cleanup use the saved child exactly rather
than deriving it again. Valid complete handles retain exact physical names and
messages across descriptive renames and prefix changes. Unknown/malformed source
UUIDs, encoded delimiters, unsafe entity coordinates or incompatible placement
refuse. Existing server-owned `provider_placement_identity`, when present, still
passes through the central lifecycle's provenance verifier before dispatch.

Historical short handles remain stored unchanged. They lack saved namespace,
subscription and child provenance and return structured `ownership_unknown`;
this release does not backfill them from current config, labels, slug guesses or
GCP's recorded-handle exclusivity flag. Historical unlabelled children and ambiguous
metadata cannot be adopted. A saved complete handle whose parent/child is absent
cannot reprovision into a recreated pair. Operator recovery needs separately
verified historical provenance; no automatic migration or handle rewrite occurs.

Each supported operation requires the actual source UUID/platform marker and
exact returned parent/child ARM identities. Parent-changing provision/update,
sender binding and cleanup additionally require a complete attached-subscription
inventory containing only the saved owned child (or its confirmed absence during
cleanup/initial creation). Inventory is bounded to four pages and 64 items, with
validated fixed-host/exact-parent continuation targets; incomplete, denied,
repeated, foreign or excessive pages refuse before these effects. An unexpected
child refuses even if its labels name the same service. Each SDK request has
bounded connect/read timeouts (at most five seconds each), retries/redirects
disabled, and shares a 20-second operation deadline checked before calls. This
bounds provider requests, not arbitrary SDK response-body allocation.

SDK 10/API `2026-01-01` typed `SBTopic(properties=SBTopicProperties(...))` and
`SBSubscription(properties=SBSubscriptionProperties(...))` serialize owner metadata
and settings under the actual `properties` object. Metadata delimiter injection,
duplicate/conflicting aliases and envelopes exceeding 1024 characters refuse;
identity is never truncated. Duration inputs support positive integer ISO day,
hour, minute and second components; malformed, calendar-month/year, fractional or
nonpositive durations refuse instead of guessing. SDK enum `.value` determines
status: missing/unknown/disabled states never imply availability, and binding
requires both entities active. Only `max_size_in_megabytes` and
`default_message_ttl` are declared editable. Updates preserve unchanged
noneditable settings in the full desired snapshot but refuse partitioning or
child-setting changes; unsupported-only updates perform no SDK calls. Supported
updates write typed topic properties and require returned setting confirmation.

Workload identity retains Data Sender on the exact topic and Data Receiver on
the exact saved subscription. Snapshot/restore remain unsupported. Retaining
messages (`delete_data=False`) refuses with no seek/drain/snapshot fiction.
Explicit destructive cleanup deletes the verified child, then parent, and checks
both are actually absent. Only SDK-typed `ResourceNotFoundError` proves relevant
absence; access/transport/delete failures retain `ownership_unknown` irrespective
of diagnostic text. Permanent ownership refusals return
`external_resource_collision` plus `ownership_refused`. Force permits an explicitly
disabled topic to be removed, never bypassing source/placement/child proof, retained
data refusal, incomplete inventory or a failed delete. This sequence is not atomic.

Rollback requires care: the older sibling driver cannot safely interpret new
`arm-v1` handles and must not resume workflows for their targets. Prefer a forward
repair or independently verified compatible build; do not strip the saved context
or rewrite handles for rollback. ARM logical IDs/owner markers are not a conditional
resource-incarnation precondition: external replacement or subscription creation
between completed reads and PUT/DELETE remains a race. No historical provider
provenance or atomic incarnation enforcement is claimed. Failed creation before a
successful saved handle can leave an owned partial pair; retry uses the same
immutable-ID names in unchanged placement, and cannot adopt an unlabelled/foreign
child. Changing placement during such an unrecorded failure needs operator review.

Recording transport and real PostgreSQL lifecycle checks prove actual SDK wire
serialization, enum/errors/paging, owner denial, opaque handle persistence and
binding dispatch. They do not certify Azure message delivery, live-service data
persistence or external-race exclusion. The broader #2032/#2098 audits remain open.
See the actual [topic properties contract](https://learn.microsoft.com/en-us/rest/api/servicebus/controlplane/topics/create-or-update?view=rest-servicebus-controlplane-2026-01-01)
and [subscription properties contract](https://learn.microsoft.com/en-us/rest/api/servicebus/controlplane/subscriptions/create-or-update?view=rest-servicebus-controlplane-2026-01-01).


### Azure Event Hubs owned ARM targets (#2032 / #2098, bounded family)

Both `stream/event_hubs` and `event_stream/event_hubs_kafka` now save
`kind/arm-v1/subscription-UUID/resource-group/namespace/hub/native-group`.
Kafka uses the explicit `-` native-group sentinel; Kafka broker consumer groups
are not ARM consumer groups and are not inventoried or managed here. Subscription
and resource-group coordinates must still match the current provider installation.
The complete handle and emitted hub ARM binding must each fit their actual
512-character storage fields. Validation rejects invalid delimiters/names and
oversize identities before SDK calls; no target is truncated or reconstructed.
Existing server-owned placement identities, when present, remain validated by
central lifecycle dispatch before provider construction.

New default namespace, hub and native managed-group names retain all 32 hex
characters of the canonical nonzero managed-service UUID. Basic, or an explicit
operator choice of `$Default`, uses the observed built-in group instead. Only cosmetic operator
prefixes are bounded; operators must keep them stable until initial provision
records its handle. Explicit namespace/hub override fields remain available, but
new override values must contain that same complete UUID hex and satisfy Azure's
name limits; invalid overrides are refused, never normalized or ignored. Complete
saved targets preserve exact physical names and selected native groups across
human-name/prefix changes, even when those saved names use an older convention.
Legacy short handles omit immutable ARM placement and cannot establish authority:
they remain stored and return `ownership_unknown`; there is no automatic adoption,
backfill, migration, or recreation of missing recorded resources.

Every existing namespace requires the current source UUID/platform tags and exact
returned ARM identity. Each hub and custom ARM consumer group requires a typed
`userMetadata` JSON envelope naming the same UUID/platform and its exact returned
ARM identity. Complete bounded hub/group inventories must agree with exact GETs;
other hubs, unowned/unmarked children, unknown pages or divergent ARM identities
refuse before namespace/network/hub/group writes or namespace deletion. The
Azure-created `$Default` group is structural only: its actual list and GET ARM
identity must match the owned saved parent. The driver never stamps, PUTs, or
individually deletes it. Microsoft documents automatic creation in its
[official SDK sample](https://github.com/Azure/azure-sdk-for-net/blob/main/sdk/eventhub/Azure.Messaging.EventHubs.Processor/samples/Sample04_ProcessingEvents.md)
and exposes it in the [API2024 ARM inventory](https://learn.microsoft.com/en-us/rest/api/eventhub/consumer-groups/list-by-event-hub?view=rest-eventhub-2024-01-01).

Each management observation admits work within a 20-second deadline, with at most
5-second connect/read timeouts per request, retries/redirects disabled, and complete
inventories limited to four pages/128 items. Continuations must retain the exact
trusted ARM collection. A timeout is an unknown/incomplete observation, never
absence. SDK12 supports `polling=False`/`NoPolling`: namespace create/delete starts
no background LRO polling; reconciliation checks actual state with subsequent GETs.
Azure may continue an accepted operation remotely. Connect/read timeouts govern
transport phases, not a hard cancellation deadline for Azure or an absolute bound
on an SDK response stream. Partial namespace/hub creation remains retryable and
incomplete; retries with the same UUID/defaults reconcile the same owned target.
If the workflow exhausts its finite retries, repair/retry is explicit; pending
acceptance never skips unfinished child work or claims applied readiness.

Namespace/inherited locks are fully inventoried within those bounds. Any present
or unknown lock refuses, including under `force_destroy`; namespace ownership does
not authorize removing independent locks. Operators must resolve locks separately.
Only actual SDK `ResourceNotFoundError` proves relevant absence. Denial, invalid
placement, partial inventories and diagnostic text cannot become cleanup success.
Ownership-refused/unknown outcomes remain structured; failure diagnostics do not
expose raw provider configuration.

SDK enum values are decoded through `.value`, preserving API2024 wire spellings.
Naming validation follows the [official Microsoft.EventHub rules](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules#microsofteventhub).
Only documented `Delete`/`Compact` cleanup policies are advertised. Missing/unknown hub state,
Creating, Disabled, SendDisabled and ReceiveDisabled observations are not fully
available. Namespace readiness requires observed Succeeded and no explicitly non-Active
namespace status. The documented API2024 GET example omits namespace status;
that remains reported as unknown, rather than invented Active. Hub readiness
requires observed Active and the selected native group. Whole desired config may
retain unchanged create-only settings; actual immutable changes and custom-group
removals explicitly require reprovision. Supported custom-group additions and
status disable/re-enable updates use exact current owned targets. Existing
Sender/Receiver workload grants remain at the exact hub ARM scope, with no Data
Owner or namespace management grant.

Capture still requires the complete destination/identity block and an operator-
allowlisted, pre-authorized UAMI. This does not certify tenant ownership of the
external destination, create storage/Key Vault permissions, delete archives, or
provide an exact stream snapshot. Private-only/perimeter modes still refuse without
verified dependencies. `delete_data=False` preserves data by refusal (central
snapshot dispatch also refuses); force cannot bypass it. Explicit destructive
cleanup requires complete namespace exclusivity, and success requires observed
namespace absence, rather than merely an accepted DELETE.

The SDK has no atomic ownership compare-and-write/delete across these ARM requests.
Independent cloud administrators must not replace/retag resources or add children
between proof and effects. Controlled SDK12 wire/PostgreSQL checks do not certify
live Azure tiers, data-plane delivery, Capture, or advanced dependency readiness.
Older Event Hubs drivers cannot interpret new `arm-v1` handles safely: do not resume
workflows for newly created targets on an older driver. Prefer forward repair or
explicitly verified compatibility; never automatically rewrite handles on rollback.
Other families and broader #2032/#2098 acceptance remain open.
