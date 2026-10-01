# Registry access scopes

The additive [app dependency context](app-dependency-context.md) read requires
`app.read` on one exact app/environment pair and exposes only redacted,
source-stamped persisted observations. It grants no cluster-control or deploy
authority and refuses expected cluster/provider GUID mismatches.

App reads and writes check the named app in the active organization. Workload
keys resolve through their live app. Missing, foreign, deleted or ambiguous
keys require an explicit organization grant; selecting a team or project does
not supply authority for a missing target. Populated app/project/team ancestors
must be live, organization-owned and consistent. A stale ancestor likewise
requires organization authority.

App, workload and container lists narrow their rows before limits, pagination
and counts. App shares remain usable at their delegated level. A viewer share
allows app reads; deployer and owner shares carry the caller's granted actions.
Shares never grant a permission the caller lacks.

A non-inheriting organization binding grants at the organization itself. It can
authorize organization-owned records in mixed collections, but never descendant
teams, projects or apps. Deleted roles supply no authority through either direct
bindings or identity-provider group mappings.

Bearer credentials retain their permission scopes and optional team ceiling,
even when their user has an organization-wide role. An app must belong to the
credential's live team or have a live share that delegates the actual action.
Organization-only source-manifest scans require organization-scoped credentials.
A `read:apps` credential cannot write its own app; registration uses `app:onboard`
(or a broader compatible credential) plus the app/agent creation permission at
the destination project.

Registration checks the live destination project and its live, same-organization
home team. Even an organization operator cannot create or reparent into stale
or foreign project ancestry. Transfers separately require
`app.transfer` at the source and `app.create` at the destination. Assigning an
app to another project preserves the existing destination-access requirement
and also confines the destination to the credential's team. Unassigning a project
keeps the home team. Cross-organization moves remain refused.

These checks change authorization only; request/response fields and mutation
error envelopes stay the same. Operators correcting stale ownership need an
organization grant and an organization-scoped credential.
