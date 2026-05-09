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
