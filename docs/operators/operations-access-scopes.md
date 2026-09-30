# Operations access scopes

Operations authorize the recorded resource owner (#2107). A selected team or
project supplies context; it does not authorize another team's apps or an
organization resource. Existing permission verbs and public API shapes remain.

| Surface | Permission and actual owner |
| --- | --- |
| App uptime, app metrics, log exports | Existing app read, metrics, or log-export permission at the live app |
| Events, activity and aggregated event feeds | `audit_log.read` at each recorded app, project, team or organization owner; filter before aggregation, cursors and counts |
| Audit trail, audit retention and audit export | Existing audit read or export permission at the organization; descriptive audit target strings do not establish an owner |
| Webhook list and delivery history | `webhook.create` at each subscription's app, team or organization owner |
| Webhook creation and changes | Existing webhook create, update or delete permission at the destination or recorded owner |
| Alert lists and firing history | `app.read` at the rule's app, environment's app, workload's app, managed service owner or organization for global rules |
| Alert creation and changes | Existing webhook create, update or delete permission at the target; changing a managed-service binding also authorizes the new service |
| Set or clear an alert subscription | `app.read` at the live app, plus the existing requirement that the subscription belongs to the caller |
| Bulk restart, manifest resync and bundle attachment | Existing deploy or update permission for every app; a refused app keeps its own failure result and causes no side effect |
| Notification profiles/tests, retention holds and Zentinelle connection/gateway management | Existing organization update, connect or gateway permission at the active organization |

App ancestors must be live, owned by the active organization and coherent.
Missing, foreign or stale target resolution takes an explicit organization
scope instead of falling back to the selected team. New alert targets must
exist; global rules do not take a target ID. Organization operators can inspect
or repair existing orphan alert configuration without granting it to a team.

Bearer credentials retain their organization and optional team ceiling,
including when their user holds an organization role. App shares retain the
permission's existing access-level requirements. Collections filter to live
owners and the credential ceiling before limits, totals and continuation
cursors. Organization-only grants expose organization-owned rows without
including descendant owners.

Bulk actions evaluate actual workload or environment facts for each item.
Manifest resync checks all affected app environments before applying the
manifest. Bundle attachment checks both the destination app and the source
bundle's project, team or organization owner under the destination's persisted
environment context before writing a reference. Alert changes use their
persisted environment or managed-service facts, and each check restores its
operation context before the next target. Provider reads refuse foreign or
deleted cluster targets, including for organization operators.
