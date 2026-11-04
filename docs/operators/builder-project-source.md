# Builder source to an ordinary app in a project (#2471)

`builder-project-source-v1` hosts a Builder app in an exact project without a
brain. It is the [brain protocol](builder-brain-source.md) without the brain:
the same operator build target, immutable archive, owned Kaniko build receipt,
once-only submission and recovery, and fresh original-person host request.
Hosting creates no BrainAppBinding and no `BRAIN_*` secret. The brain protocol
is unchanged and still requires a readable, served brain in the project.

Source tests use native SDK doubles. They do not prove an operator's build
target, registry, built image or running app; that needs an operator-configured
target and an authorized runtime canary, which this change does not perform.

Preconditions: on the control plane, `BUILDER_BRAIN_BUILD_TARGETS` holding the destination project's target and an HTTPS `APP_BASE_URL`; the operator-provisioned namespace, ServiceAccount, IAM role and ECR repository, configured as in the [brain protocol](builder-brain-source.md#operator-target-and-trust); the caller's current `alft_at_` bearer with `write:apps`, the organization's `chat_studio_integration` module on, source access, and `app.create` and `app.deploy` on the destination project. No brain access is needed. Surface: the Builder REST routes under `/api/builder/v1/`. Placeholders: `<project-guid>` is `id` from `astroliftProjectsPage`; `<dev-guid>` is the Builder dev environment's id from the Builder API; `<build-guid>` is `id` in the POST response; `<64-lowercase-hex>` is `archive_sha256` from the source read, or `image_digest` from the build read for `host/`.

## Discovery

The handshake capability is `builder.project_source_artifacts`. Authorized
`GET /api/builder/v1/capabilities/` adds `project_source`:

```json
{"protocol":"builder-project-source-v1","phases":["build","host"],"requires_project_target":true,"requires_brain":false,"providers":["aws"],"runtime_verified":false}
```

Offer the flow only when both are present. Without them, do not fall back to
team `promote/` or to an unrelated brain. The descriptor advertises a callable
protocol, not a configured or healthy build target.

## Requests

1. `GET /api/builder/v1/dev-environments/<dev-guid>/project-source/` returns
   `protocol`, `archive_sha256`, `archive_bytes` and `expanded_bytes` for the
   exact current ZIP. It creates no archive or cloud resource.
2. `POST /api/builder/v1/dev-environments/<dev-guid>/project-source-builds/`:

```json
{
  "project_guid": "<project-guid>",
  "app_name": "Order tracker",
  "app_slug": "order-tracker",
  "idempotency_key": "studio-conversation-action-unique-id",
  "expected_archive_sha256": "<64-lowercase-hex>",
  "expires_in": "4h",
  "delete_data_on_expiry": false
}
```

All shown fields except the expiry are required, and `delete_data_on_expiry`
is an explicit JSON boolean. ISO `expires_at` replaces `expires_in`; both
refuse. Any other field refuses, `brain_slug`, project or team aliases and a
caller image included. Idempotency keys share one namespace per person with
brain intents.

3. Poll `GET /api/builder/v1/project-source-builds/<build-guid>/` for `queued`,
   `building`, `failed`, `artifact_ready` and `hosted`.
4. At `artifact_ready`, make a fresh
   `POST /api/builder/v1/project-source-builds/<build-guid>/host/` as the same
   person with the same bearer:

```json
{"expected_image_digest":"sha256:<64-lowercase-hex>"}
```

Host rechecks source ownership, the bearer and its team ceiling, and
`app.create` plus `app.deploy` on the project. It then registers the app in that
project through the shared registration path and admits an ordinary deployment
of the verified image. App, deployment and intent commit together or not at all.
Production action safety, approvals and native-origin admission apply as for
any deployment.

## Expiry

Policy is checked at build admission and again when the app is created. A
relative TTL starts at app creation; an absolute time must still fit the minimum
and maximum then. Omitting the expiry asks for a permanent app, which current
policy may refuse (`ttl_required_surfaces` naming `chat_studio`, or members
limited to temporary apps). A refusal at host creates nothing and leaves the
intent `artifact_ready`; the same intent hosts once policy admits it. The
response returns the app's actual `expires_at` and `delete_data_on_expiry`, and
`data_persistent`: false with a seed data file, which lives on an ephemeral
`/data`, and null without one. Extend or shorten the expiry with the existing
`setAppExpiry` mutation.

## Responses and recovery

Responses carry the brain protocol's fields with
`protocol: builder-project-source-v1`; `brain_guid` and `brain_slug` are always
null. Build readiness (`status: artifact_ready`), app and deployment records
(`hosting_status: created`, `app_guid`, `deployment_guid`), submission
(`build_submission`, `deployment_submission`), engine observations
(`build_execution_status`, `deployment_execution_status`) and the deployment's
own state (`deployment_status`, `runtime_verified`) stay separate. `hosted`
means the records exist, never that the workload runs.

Replays follow the brain protocol. The identical request returns the same
intent and a changed one conflicts. A recorded attempt recovers its exact
execution and is never resubmitted, and a failed build stays failed under its
key. A deleted hosted app is never recreated (`HOSTED_APP_UNAVAILABLE`). Each
route family serves only its own intents: a project intent answers 404 on the
brain routes, and a brain intent on these.

## Rollout

Lifecycle migration 0053 adds `hosting_mode` (DB default `brain`) to the build
intent and lets its brain be null, under a check constraint that keeps a brain
on every brain intent and none on a project intent. It is additive; keep it
applied on rollback. Release the API and workers together: a worker from before
this change refuses a project build before any native effect.

## Verify

```text
GET /api/builder/v1/capabilities/
Authorization: Bearer <alft_at token>

GET /api/builder/v1/project-source-builds/<build-guid>/
Authorization: Bearer <alft_at token>

cd backend && pytest astrolift_lifecycle/tests/test_builder_project_source_2471.py astrolift_lifecycle/tests/test_builder_project_temporal_2471.py
```

Capabilities carry `project_source` with `protocol: builder-project-source-v1`; after `host/`, the build read shows `status: hosted`, `hosting_status: created`, non-null `app_guid` and `deployment_guid`, and null `brain_guid`; the test modules pass.
