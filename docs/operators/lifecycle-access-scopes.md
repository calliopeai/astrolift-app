# Lifecycle access scopes

Lifecycle actions authorize the persisted app owner, independent of selected
team and project headers. App ancestors must be live, belong to the active
organization and agree on the home team. Deployment, environment, preview,
domain, deploy-token and run targets use that app's scope. Task runs without
an environment retain their workload's app owner. Missing, malformed, deleted
or incoherent targets take explicit organization scope; selecting a team does
not supply fallback authority.

Bearer credentials retain their organization, team and action-specific share
ceiling even when their user has an organization role. Collections apply this
ceiling and operation policies before limits, pagination and counts. A viewer
share supports reads; it cannot authorize a deploy-token write. Agent-box pod
reads authorize and refetch the box's recorded owner at `agent_box.attach`.
Deregister cancellation derives the actual app from the workflow identifier.

Every populated environment and workload must agree with its row's app. A
cluster target must be active, live, and owned by the organization or be a
supported platform cluster with no organization. Invalid persisted targets
are refused before writes, workflow signals and provider calls. A named but
invalid environment cannot fall back to the app's default cluster. The legacy
observability fallback for a name that was never registered remains available.

Deployment direct links, approval history, logs and release notes retain their
organization-confined snapshots after app or environment teardown. An explicit
organization operator can read those historical rows, while lists continue to
hide retired owners. A public approval token retains its existing capability
contract: its hash identifies the deployment, after which its own organization,
coherent live target, expiry, status and approver checks still apply.

The builder HTTP API requires a bearer token, its existing `write:apps` scope,
and permissions on the live destination team. Sync and promote also check the
dev environment's actual owner and retain the creator-or-admin rule. A row with
no team requires organization authority and an organization credential; a team
credential cannot supply its own team as a replacement. Re-promoting an app
with incoherent or retired ancestry is refused before workflows start.

The lifecycle event stream authenticates the WebSocket identity and requires
`app.read` at an available scope. It filters every event through the persisted
live deployment, environment, cluster, approvals and permission policy. Event
payload labels do not establish ownership, environment or region. Events are
normalized to the stored app and environment. Each event rechecks the pinned
actor and bearer with a fresh permission cache, so membership, role, policy and
token revocation stop delivery. Denied streams close silently; cancellation and
cross-task generator closure remove their broker queue. GraphQL SDL and HTTP
request and response shapes remain unchanged.

Deploy-token rotation confirmation reads
`astroliftAppDeployTokenRotationMetadata(appSlug)` with `app.update` on the
coherent live app owner. The read retains actual role/policy and bearer ceilings;
read-only, deploy-only and viewer-share grants cannot read this metadata. Missing
or invalid live app ancestry returns null without accessing configuration, even
for an organization updater. This query exposes only `rotationGraceSeconds`,
validated by the same Constance helper as rotation (60 seconds through 7 days;
malformed/unavailable configuration uses the helper's existing server fallback).
It does not expose raw configuration or any token material.

The web dialog reads without caching on each open/retry and blocks confirmation
while loading, unavailable or refused. Delayed replies after close or an
organization/app/token switch cannot enable another target. Durations retain
whole seconds rather than rounding a configured window. This read is a snapshot:
configuration may change before confirmation. Rotation retains its existing
`app.update` and elevation checks, rereads the current setting when issuing the
secret, and returns the actual applied window in its one-time reveal.

The web archive/restore settings card preserves the existing `app.update` fence
and sends the existing app slug. Its current GUID/version/archive observations
only invalidate stale local reviews; they do not add a backend incarnation or
version precondition. Archive/restore acceptance reflects persisted workload
replica settings and deployment suppression, not proof of a live rollout.
Rejected writes do not refresh or navigate. An accepted write survives a failed
view refresh or navigation, with recovery feedback rather than a false write
failure. The backend's current owner and permission checks remain authoritative.
