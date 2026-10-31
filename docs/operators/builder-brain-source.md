# Builder source to an existing brain (#2456)

The two-step `builder-brain-source-v1` protocol builds a private immutable Builder
ZIP with the real Kaniko BuildDriver, verifies its pushed image against the
registry, then consumes the artifact in a **fresh authenticated HTTP request**.
`artifact_ready` has no app, binding, deployment or running URL. Studio must call
`host/` and observe the resulting deployment before claiming success.

Source tests and synthetic native SDK/HTTP controls do not prove an operator's
IAM policy, registry, built image or running application. Production acceptance
requires an operator-configured target and authorized artifact/runtime canary.
This change does not perform that canary.

Preconditions: on the control plane, `BUILDER_BRAIN_BUILD_TARGETS` holding the destination project's target and an HTTPS `APP_BASE_URL`; the operator-provisioned namespace, ServiceAccount, IAM role and ECR repository in the cluster's AWS account; the caller's current `alft_at_` bearer with `write:apps`, the organization's `chat_studio_integration` module on, `app.create` and `app.deploy` on the destination project, source access, and read access to the brain in that project. Surface: the Builder REST routes under `/api/builder/v1/`. Placeholders: `<project-guid>` is `id` from `astroliftProjectsPage`; `<organization-guid>` is `id` from `astroliftOrganizations`; `<managed-aws-cluster-guid>` is `id` from `astroliftClustersPage`; `<actual-namespace-uid>` and `<actual-serviceaccount-uid>` are `.metadata.uid` from `kubectl get namespace` and `kubectl get serviceaccount`; `<dev-guid>` is the Builder dev environment's id from the Builder API; `<build-guid>` is `id` in the POST response; `<64-lowercase-hex>` is `archive_sha256` from the source read, or `image_digest` from the build read for `host/`.

## Operator target and trust

`BUILDER_BRAIN_BUILD_TARGETS` is JSON keyed by the exact destination project GUID:

```json
{
  "<project-guid>": {
    "organization_guid": "<organization-guid>",
    "cluster_guid": "<managed-aws-cluster-guid>",
    "namespace": "project-source-builds",
    "namespace_uid": "<actual-namespace-uid>",
    "service_account": "project-source-builds",
    "service_account_uid": "<actual-serviceaccount-uid>",
    "role_arn": "arn:aws:iam::123456789012:role/project-source-build-push",
    "registry_repo_name": "project-source-builds",
    "registry_repo_uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/project-source-builds",
    "attested_project_isolation": true
  }
}
```

The operator provisions these resources independently. The cluster must be live,
managed, and belong to the organization or be explicitly shared. Its enabled AWS
provider must already have account/region and the recorded cluster OIDC issuer.
Existing brain hosting also requires the install's HTTPS `APP_BASE_URL` for
scoped brain-token issuance.
Missing/malformed/unsupported targets refuse; there is no provider inference,
fallback image, resource adoption, issuer discovery, provisioning or IAM grant.

The operator attests that the namespace, service account, IAM role and repository
are isolated to this project. The IRSA role must grant only Kaniko's required
push/pull actions for **this repository**, plus ECR's authorization-token action;
no other tenants' image reads, image/repository deletion, IAM mutation or
Kubernetes API/Secret access. No two configured projects may share the repository,
role or cluster/namespace/service-account tuple. External sharing and IAM least
privilege are explicit operator trust, not inferred from a service-account annotation.

Native reads verify namespace UID, service-account UID and exact role annotation,
and repository name/URI/strict `IMMUTABLE` tags without exclusions. Those reads do
not inspect IAM policy or prove network isolation/runtime health. Repository
lifecycle policy must retain `intent-*` tags while any build/app needs them.
Normal deployment retention pins also apply. Source image/archive retirement is
not automated here; the operator preserves these dependencies. The flow never
adopts/deletes the preprovisioned repository, namespace, service account or role.

## Discovery and requests

The handshake capability is `builder.brain_source_artifacts`. Authorized Builder
`GET /api/builder/v1/capabilities/` adds `brain_source`:

```json
{"protocol":"builder-brain-source-v1","phases":["build","host"],"requires_project_target":true,"providers":["aws"],"runtime_verified":false}
```

This advertises a callable protocol, not a configured/healthy deployment target.
Other providers refuse. Generic Kaniko support does not imply native GCP registry
push identity or enrollment support.

Use the original person's current `alft_at_…` bearer with `write:apps` and enabled
`chat_studio_integration`. Sessions/deploy tokens refuse. The server sets trusted
`chat_studio` surface; headers cannot select it. Source access retains the live
TEAM/ORG plus creator-or-team-admin guard. Destination admission checks current
project `app.create` **and** `app.deploy`, coherent organization/team/project
ancestry, bearer team ceiling, and a readable served brain in that exact project.

1. Create/sync source through the existing [Builder API](../builder-api.md).
2. `GET /api/builder/v1/dev-environments/<dev-guid>/source/` returns
   `archive_sha256`, `archive_bytes`, `expanded_bytes` for the exact current ZIP.
   This creates no archive/cloud resource.
3. `POST /api/builder/v1/dev-environments/<dev-guid>/brain-source-builds/`:

```json
{
  "project_guid": "<project-guid>",
  "brain_slug": "existing-brain",
  "app_name": "SQL explorer",
  "app_slug": "sql-explorer",
  "idempotency_key": "studio-conversation-action-unique-id",
  "expected_archive_sha256": "<64-lowercase-hex>",
  "expires_in": "4h",
  "delete_data_on_expiry": false
}
```

All shown fields except expiry are required. Deletion preference is an explicit
JSON boolean. ISO `expires_at` is an alternative to `expires_in`; both refuse.
Current policy is checked at build admission and again at atomic app creation.
Relative TTL starts at app creation; an absolute time must still meet minimum
and maximum TTL then. Omitted expiry requests permanence only where current
Chat Studio/member policy permits it.

Admission locks the source row and verifies its digest. Later sync cannot change
the admitted archive/recipe. Current source owner/destination/credential changes
still stop later effects. Keys are unique per organization/person. An identical
request returns the same intent; changed source/destination/app/TTL/deletion
preference conflicts. Repeat the exact request to recover an uncertain response
or join the original execution. A closed failed build is never restarted by a
repeated POST; a new reviewed build requires a new intent/key.

4. Poll `GET /api/builder/v1/brain-source-builds/<build-guid>/` for `queued`,
   `building`, `failed`, `artifact_ready`, `hosted`. `failure_code` contains fixed
   metadata, never source/build output/credentials.
5. At `artifact_ready`, make a fresh
   `POST /api/builder/v1/brain-source-builds/<build-guid>/host/`:

```json
{"expected_image_digest":"sha256:<64-lowercase-hex>"}
```

The digest is checked against the intent and registry again. The original person
and original current bearer must still pass source, ceiling, destination and
brain-read checks. Host cannot change target/source/initial TTL. App, normal
`BrainAppBinding`, deployment and intent association commit atomically. Existing
production action safety, approvals and native-origin admission remain in force;
no background request/system deployer is invented. A repeat preserves the same
app/deployment/current expiry, and cannot recreate a deleted app. Extensions use
the existing `setAppExpiry` mutation. Legacy team promotion/native-origin refusal
is unchanged.

The response/read includes `id`, `protocol`, `status`, `archive_sha256`,
`project_guid`, `brain_guid`, `brain_slug`, `image_digest`, `image_ref`,
`failure_code`, `app_guid`, `app_slug`, `deployment_guid`, `deployment_status`,
`provisioning_status`, `runtime_url`, `expires_at`, `delete_data_on_expiry`,
`data_persistent`, `runtime_verified`. Before hosting app/deployment/URL/expiry
are null. An assigned URL/submitted deployment does not prove reachability.
Runtime readiness is a separate ordinary deployment observation.

`hosting_status` distinguishes `not_created` from `created`. Independent
`build_submission`/`deployment_submission` fields distinguish `pending`,
`submitting`, `enqueued`, `uncertain`, `refused` (and deployment
`not_requested`/`approval_pending`). `build_execution_status` and
`deployment_execution_status` are separate engine observations, null until
observed. `hosted` means an app/binding/deployment record exists; it never means
the workload is running.

Both execution identities use the existing exact-history once/recovery helpers.
The deployment payload is encrypted at rest, including any original native
authority reference. Accepted payloads are never refreshed on retry. A durable
first-submission marker commits **before** the sole elected start RPC. Later
requests recover exact matching history, including a closed run, without replay.
If history is absent after any recorded attempt, no new start is authorized,
even after engine retention expires. A crash after the marker but before the RPC
requires explicit new review/intent rather than a blind retry. Revoked authority
refuses effects. Current source/project/brain and normal deployment permissions
are rechecked before submission or recovery; no native origin is invented.

## Bytes, credentials and cleanup

Server-generated recipes support existing Python/Node/Ruby/Go/static runtimes;
an uploaded Dockerfile cannot replace the recipe. Versions are validated,
non-static start command is required, port must be unprivileged, and dependency
installation failure fails the build. Runtime env is not baked into the image;
`BRAIN_*`/`ASTROLIFT_*` overrides refuse. Normal per-deployment brain binding
materializes scoped `BRAIN_*`; no owner/brain token is put into uploads or browser env.

Only an intent ID enters Temporal. The original bearer is a bound hash reference,
not saved plaintext. A signed archive grant is minted inside the activity, scoped
to intent/digest/live authority/current build state with maximum age 30 minutes.
Only the fetch init container mounts it. The actual downloader rejects redirects,
checks compressed size/digest/expanded size and refuses traversal, duplicate or
symlink entries. Kaniko uses `dir:///workspace` without the credential mount.
Kubernetes API token automount is disabled; separately projected IRSA is the
operator-prepared push identity.

Files and SQLite seed reach the image. The entrypoint seeds a missing file into
an actual `/data` emptyDir. It resets on pod replacement: `data_persistent=false`
with seed, null without. No PVC/managed database/hidden retention promise exists.
Normal expiry removes app runtime and brain binding; immutable image/archive is
an explicitly retained dependency.

Jobs have a 30-minute deadline and use atomic create-only POST. Cleanup records
actual created Job/credential Secret UIDs, rechecks the original namespace UID,
and deletes only those UIDs. Replacement/foreign resources refuse. A lost create
acknowledgement without a UID receipt cannot be adopted on retry. Cleanup refusal
keeps the build failed and prevents hosting even after a push; confirmed cleanup
can recover the same verified tag without rebuilding. No namespace/repository/SA
or IAM role deletion occurs.

Before checkpointing, a native read must confirm the original observed Job UID,
intent label and positive successful-completion count; completion by name alone
is insufficient. Authority checks reload role/group/policy facts with fresh caches
and clock before later effects.

An existing intent tag is reusable only with this intent's durable successful
owned-Job receipt and previously verified digest. An unproven preexisting tag
refuses instead of being adopted. The successful digest checkpoint commits
before cleanup, allowing an exact cleanup retry to recover that proven image.

No new `astro` command is claimed. API/CLI consumers may make these bearer REST
calls. Studio keeps the intent GUID, polls, then calls `host/` with the observed
digest. It must not substitute team `promote/`, fabricate an image, edit TTL after
creation or inject brain secrets into the raw Builder runtime.

## Verify

```text
GET /api/builder/v1/capabilities/
Authorization: Bearer <alft_at token>

GET /api/builder/v1/brain-source-builds/<build-guid>/
Authorization: Bearer <alft_at token>

cd backend && pytest astrolift_lifecycle/tests/test_builder_brain_source_2456.py
```

Capabilities carry `brain_source` with `protocol: builder-brain-source-v1`; after `host/`, the build read shows `status: hosted`, `hosting_status: created` and non-null `app_guid`, `deployment_guid` and `runtime_url`, and that deployment reaches a running state in the ordinary deployment views; the test module passes.
