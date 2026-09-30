# Identity access scopes

Identity administration authorizes the object's owner, independently of the
selected team or project. The surface guardrail has no `#2103` exemptions;
its 65 routes comprise 50 organization gates, nine team/project gates and
six binding operations or collections.

| Surface | Scope |
| --- | --- |
| Organization details/settings/deletion, onboarding and modules | Active organization |
| Organization member lists, approval picker and principal search | Active organization |
| Invitations, allowlisted domains and identity providers | Active organization |
| Role catalog/custom roles, policies and condition/scope catalogs | Active organization |
| API token lists, creation and revocation | Active organization |
| Group role mappings, access/grant previews and policy simulation | Active organization |
| Team creation | Destination organization |
| Team edit/delete, members and bulk member role assignment | Named live team |
| Team slug availability | Edited live team when `excludeId` is supplied; otherwise organization |
| Project creation | Destination live team |
| Project edit/delete | Named live project with a matching live team owner |
| Project slug availability | Edited live project when `excludeId` is supplied, with matching `teamId`; otherwise destination live team |
| Role binding grant | Explicit destination organization/team/project/app |
| Binding update/revocation | Existing binding's live owner; organization for stale owners |
| Binding collections and bulk revocation | Any-scope admission followed by owner filtering |

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
