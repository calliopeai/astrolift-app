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

This bounded change does not establish ownership protection for every AWS
lifecycle operation. SQS/DynamoDB update/teardown, provider status/snapshot
paths and provisioning name races/legacy adoption remain separately tracked
acceptance work. Workload tag-mutation authority is addressed below.


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
