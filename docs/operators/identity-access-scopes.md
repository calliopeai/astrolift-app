# Identity access scopes

Identity administration authorizes the object's owner, independently of the
selected team or project. The surface guardrail has no `#2103` exemptions;
its 65 routes comprise 50 organization gates, nine team/project gates and
six binding operations or collections.

| Surface                                                          | Scope                                                                                                     |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Organization details/settings/deletion, onboarding and modules   | Active organization                                                                                       |
| Organization member lists, approval picker and principal search  | Active organization                                                                                       |
| Invitations, allowlisted domains and identity providers          | Active organization                                                                                       |
| Role catalog/custom roles, policies and condition/scope catalogs | Active organization                                                                                       |
| API token lists, creation and revocation                         | Active organization                                                                                       |
| Group role mappings, access/grant previews and policy simulation | Active organization                                                                                       |
| Team creation                                                    | Destination organization                                                                                  |
| Team edit/delete, members and bulk member role assignment        | Named live team                                                                                           |
| Team slug availability                                           | Edited live team when `excludeId` is supplied; otherwise organization                                     |
| Project creation                                                 | Destination live team                                                                                     |
| Project edit/delete                                              | Named live project with a matching live team owner                                                        |
| Project slug availability                                        | Edited live project when `excludeId` is supplied, with matching `teamId`; otherwise destination live team |
| Role binding grant                                               | Explicit destination organization/team/project/app                                                        |
| Binding update/revocation                                        | Existing binding's live owner; organization for stale owners                                              |
| Binding collections and bulk revocation                          | Any-scope admission followed by owner filtering                                                           |

A missing, foreign, deleted or inconsistent team/project/app owner checks an
explicit organization scope. Selected headers cannot authorize the miss.
Project mutations also verify the live owning team in their database lookup,
so an organization actor cannot mutate a project through stale denormalized
ownership. Organization administrators can still clear bindings left on a
deleted owner under the existing grant ceiling.

Team/project collections retain any-scope admission and filter grants, live
ownership and credential ceilings before search, pagination and counts.
Binding collections apply the same rule to their polymorphic scope; a team
manager can see and manage bindings in that team and its covered descendants,
without seeing sibling or organization bindings. Bulk revocation filters
rows first and retains per-row refusal and result envelopes.

User and group role bindings contribute authority only when the role definition
belongs to the current organization or is installation/global (`organization`
is null). A custom role owned by another organization cannot authorize a
current-org binding, even if its permission values match. Current-org custom
roles and global roles retain their existing scope, inheritance, expiry and
credential checks; group role mappings use the same owner boundary.

Organization-bound bearers retain the actor's real grants and their permission
ceiling. Team-bound credentials cannot borrow broader organization authority,
even from a platform operator. Named team/project/binding operations remain
within the credential's live team, and foreign-organization credentials grant
no rows or writes. `read:apps` continues to cover organization reads through
`org.read`; member management and token administration still require `admin`.

Binding grants/updates/revocations keep the existing permission grant ceilings,
role ownership checks, last-owner protection, elevation and audit contracts.
An organization grant picker requires organization authority because invitations
land at organization scope. Group mapping changes retain their organization
gate and their separate destination grant ceiling. Custom role administration
cannot use a selected team's grant to change an organization role definition.

Organization discovery, navigation, profile/preferences, session/elevation,
enrollment and invitation acceptance retain their deliberate bootstrap or
self-service contracts. Organization creation retains its existing installation
contract. No schema or migration change is required.

PostgreSQL regressions in
`backend/astrolift_identity/tests/test_owner_scopes_2103.py` exercise real
RoleBindings, selected sibling scopes, owner misses, inconsistent ancestry,
operator and bearer ceilings, collections/counts, binding changes and HTTP
sessions/bearers. Existing grant-ceiling and ABAC suites remain part of validation.

## Reviewed direct user ↔ team membership (#2273)

`Member(scope_kind=TEAM)` records the direct attachment. Direct user
`RoleBinding(scope_kind=TEAM)` rows confer the named role. Neither is a second
organization account or an identity-provider membership. The reviewed membership
API and both UI directions use the existing records and public GUIDs.

A current team manager can add an active organization member with a grantable
TEAM-level role without `org.manage_members`. The actor still needs current
`team.manage_members` on the exact live team, a live authenticated session or
bearer, active organization membership (except the existing browser platform
operator allowance), and the existing role grant ceiling and step-up admission.
No bearer scope is widened: `team:write` does not include member management;
API writes require the existing `admin` ceiling and the actor's scoped grants.
Team-bound credentials remain within their current live team.

Reads and selectors are server paged with search. Team, person and role targets
use GUIDs; a person selector accepts the organization `Member` GUID, not a User
integer ID or a team attachment GUID. The team route resolves its exact
organization-owned slug once, then carries the returned GUID into subsequent
reads and reviews. Missing/foreign/deleted owners, inactive add targets, and
roles outside the same organization or global TEAM catalogue refuse.

1. Read `astroliftTeamMembershipReview` for `ADD` or `REMOVE` and the exact team
   and organization-member GUIDs. For ADD, select the exact role and obtain a
   fresh review including that `roleId`. The paged role selector is
   `astroliftTeamMembershipRolesPage`.
2. Submit `changeAstroliftTeamMembership` with that `expectedSource`, exact
   target/role tuple and a new UUID `requestId`. The server locks existing owner,
   users, memberships and grants, then rechecks persisted authentication,
   session/sidecar revocation and expiry, current authority, elevation, target
   identity, reviewed versions and the grant ceiling before any effects.
3. ADD creates or reuses one active TEAM attachment and direct role grant. It
   never reactivates inactive attachments/accounts/organization memberships or
   silently converts an existing timed grant to a permanent grant.
4. REMOVE soft-deletes only the reviewed TEAM attachment, when present, and all
   reviewed direct user TEAM grants, including expired grants. A newly appeared
   grant, changed target/role, unmanageable superior role or unknown permission
   forces another review. Group/IdP mappings, inherited organization grants,
   other teams/projects/apps and account/organization lifecycle are retained.
5. After a lost response, retry the **original** request UUID, target, role and
   source tuple under the original actor/organization/credential (or browser
   session). Current admission is checked on replay. A replay returns the same
   metadata-only receipt without repeating changes; it does not mint a new
   membership or adopt another person behind a reassigned Member GUID.
6. Refresh the current roster after a committed/replayed result. Receipt
   `remainingSources` are facts from the original review, not a new effective
   permission decision. A failed refresh leaves the change committed and asks
   for a refresh; a missing or mismatched response leaves the outcome uncertain.

Roster provenance distinguishes direct, inherited, IdP-group and mapping sources
and expiry. Live SCIM membership is authoritative over SSO group snapshots;
removed/deleted groups and retired identifiers cannot revive stale memberships.
Source links are returned only where current organization authority permits the
underlying role/group surface. Removing a team attachment is **not** proof that
all access has been revoked: remaining sources and organization policies still
need their own review.

`me.teamAccessNavigation` and the additive `team_access` module are current
credential landing hints. They can expose Access → Teams and permitted rosters
without enabling People, organization administration or infrastructure. They
never replace exact object checks or promise executable action approval.

The new paged projection batches current-page attachment/grant/SCIM and TEAM
permission reads. Actual PostgreSQL/HTTP query-count regressions exercise one
versus 25 mixed-source rows, alongside post-lock membership, role, token,
password/session hash, sidecar and elevation withdrawal tests. These local tests
are not evidence of a production membership change.


The previous bulk role-assignment panel remains at the team's `assign-roles`
route for an organization member manager who can currently read/manage that
team. Its existing server mutation and role constraints are unchanged. This is
legacy role assignment, not reviewed direct-membership removal: its unpaged
reader still exposes at most 500 direct attachments and the existing role
catalogue. The UI states that limit and links back to the complete Members
page; ordinary team managers use the new paged reviewed membership flow.
