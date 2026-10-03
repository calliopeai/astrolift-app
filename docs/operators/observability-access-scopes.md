# Observability access scopes

Metrics, traces, URL health, PromQL and historical log reads authorize the live
app named by the request, inside the selected organization. The selected team
or project header supplies context; it cannot authorize a sibling app or replace
an absent, foreign or stale owner. Such targets require an explicit organization
grant. Every populated app team/project ancestor must be live, in the same
organization and consistent with the ownership chain.

App metrics and traces retain `app.read`. Historical logs retain the separate
`app.read_logs` permission. App, project and inherited team grants reach their
own apps; organization grants still reach all live apps in that organization.

Bearer scopes cap those normal grants. A team-bound credential stays within
its live team ownership and applicable app shares, even when its owner holds
an organization grant or is a platform operator. A viewer share covers
`app.read`; log access requires the stronger share level associated with
`app.read_logs`. A team credential cannot use organization fallback for a
missing target. Organization-mismatched or insufficient bearer scopes fail
before any provider query or URL probe.

Managed-service metrics resolve their live app or project owner. An app-private
service uses the same app gate and credential boundary. A project-owned service
requires the project's grant and a bearer covering its owning team. Deleted
services or stale project/team ownership require organization fallback. The
existing metric response and supported provider behavior are unchanged.

The internal Postgres, object-store and model-endpoint metric catalogs are
module constants. They no longer appear as the accidental root fields
`PostgresMetrics`, `ObjectStoreMetrics` and `ModelEndpointMetrics`. Existing
observability query names, arguments and result types remain intact; generated
GraphQL SDL and client types are refreshed together. No migration is required.

## Historical log record ownership

CloudWatch historical reads require JSON records with collector-controlled
`kubernetes.namespace_name` and `kubernetes.labels["astrolift.io/app"]`. Both
values must exactly match the selected environment namespace and app slug.
A workload filter also requires an exact
`kubernetes.labels["astrolift.io/workload"]` match. The driver sends these
predicates to CloudWatch and checks each returned record again before exposing
its message. Literal text search applies only after this ownership check.

Collectors must stamp verified Kubernetes metadata and prevent application
messages from overriding it. A matching text fragment, pod/stream name or
request namespace cannot establish record ownership. Plaintext, unattributed,
foreign and duplicate-key JSON records are excluded. Older records without this
metadata therefore remain unavailable through scoped reads; changing query
configuration cannot safely attribute them after collection. Existing Loki
namespace/app/workload selectors retain their format.

## Explorer setup

The logs and traces explorers select a live app environment before reading
telemetry. Historical logs require the cluster's configured `log_driver` and
`log_config`; without them, use the app's live pod logs. Traces require a reachable
Tempo endpoint and `trace_config.attribution="collector-resource-v1"`, plus a
collector that removes application-supplied ownership attributes and stamps
verified organization, app, environment, cluster and namespace resource values.
The attribution flag declares that operator contract; it does not verify a
collector's implementation. This change installs no collector or storage backend.

## Reviewed EKS collector installation (#1706)

The dedicated collector operation is separate from generic prerequisites.
`astroliftClusterLogCollectorReview(clusterId, retentionDays)` requires current
`cluster.manage` authority and returns a tracked cluster/provider source, version,
nonsecret pinned policy and **unattached candidate** reader grant. Shared
organization-null clusters additionally require platform-operator authority and
an original credential ceiling covering that authority. Review calls no provider.

Configure `ASTROLIFT_COLLECTOR_PROBE_IMAGE` explicitly with an operator-approved
image digest. It must provide `/bin/sh` for the probe command; there is no default
or caller-controlled image. The worker needs its pinned Helm runtime and HTTPS
access to the fixed upstream GitHub release and `release-assets.githubusercontent.com`.
Downloaded bytes must match the reviewed checksum. The collector retains raw CRI
records with operator-managed Kubernetes metadata; app JSON cannot overwrite
that metadata. It covers Linux EC2 workers, not Fargate or Windows. Mixed fleets
report EC2-only coverage; unsupported-only fleets are refused before writes.

Submit `astroliftInstallClusterLogCollector` with the original canonical request
UUID, exact reviewed cluster GUID/version/source and retention. Retain that tuple
for retries. Recovery retains the original actor/token GUID/team/scopes, source,
probe policy, deadline and immutable query window. A replacement token cannot
silently reattribute an old request. Status reads require current authority and
the original credential to remain valid.

Before writes the worker verifies the declared AWS account, registered EKS
endpoint/CA/OIDC, live platform-owned namespace, EC2 eligibility and renderer.
It prepares only immutable cluster-GUID-owned log-group/IRSA identities; foreign
collisions, unrelated policies and replaced RoleIds/group creation identities
are refused. Private finite transport timeouts and original source/run/generation
admission apply before effects, credential refresh and bounded idle polls.
UID/resourceVersion and probe checkpoints survive retry. Credentials, kubeconfig,
log bodies and provider response bodies are absent from operation receipts.

`READER_GRANT_PENDING` means AWS positively denied the registered reader's exact
`FilterLogEvents` grant. Apply the returned policy through the separately
controlled runtime-reader role; Astrolift does not attach it or replace external
IAM policies. Group identity/tagging uses the plain ARN; IAM group-action grants
use that exact ARN with `:*`, without neighboring groups. Generic reader failures
remain `UNCERTAIN`, rather than fabricating a grant diagnosis.

Readiness, ingestion, probe deletion and post-loss reads have separate pending
states. Only a fresh scoped probe-event read after the recorded original Pod UID
is confirmed absent, plus fresh source/resource checks, permits atomic activation
of `log_driver`/`log_config`. A lost DELETE reply can recover through observed
absence; that does not invent an acknowledgement. Failed, refused and pending
attempts preserve the current reader. External backends are refused; an existing
owned binding needs its exact prior activation receipt, not adoption by name.

`ACTIVATED`, `postLossVerifiedAt` and `activatedAt` are installation receipts,
not continuing health. `cleanupPending` identifies a possibly retained owned
probe. Group/role/collector resources remain after partial failures. Withdrawn
sources and expired deadlines cannot be widened by retry. Resolve retained
resources through operator review of exact recorded identities before a fresh
operation; never delete by name alone. Local HTTP/Helm proof does not establish
live Fluent Bit ingestion or deployed app/CLI acceptance. Verify those before
claiming #1706 complete. Trace collectors and agent-task log retention are separate.
