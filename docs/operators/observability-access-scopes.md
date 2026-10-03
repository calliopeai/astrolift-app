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
