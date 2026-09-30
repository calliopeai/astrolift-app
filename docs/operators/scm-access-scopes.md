# SCM access scopes

SCM routes authorize the resource's owner (#2109). Choosing a team or project
does not grant access to an organization's connections or a sibling app.

| Surface | Required owner scope |
| --- | --- |
| Source connection lists, repository lists and source-file fetches | `scm.read` at the active organization |
| Connect/adopt/update a connection, rotate its secret or install its webhook | `scm.connect` at the active organization |
| Disconnect a source connection | `scm.disconnect` at the active organization |
| GitHub/GitLab OAuth start and callback; GitHub App manifest start, callback and setup | `scm.connect` at the active organization, checked again at every callback |
| Generate/delete an app's SSH deploy key | `scm.key_create` / `scm.key_delete` at that app |
| Generate/delete a shared organization SSH key | `scm.key_create` / `scm.key_delete` at the organization |
| Push/resync/pull/adopt/refresh/reconcile an app's CI workflow | `app.update` at that app |

SSH key collections admit a read grant at any scope and filter by the live
app owner before sorting, pagination and counts. Organization keys require an
organization read grant. Existing team shares retain their permission ceilings:
deployer/owner shares can carry SCM permissions; viewer shares cannot. Deleted
apps and keys, foreign organization rows and stale app owners are excluded.
App/project policies also narrow these collections before pagination and counts;
an owner role or team share cannot restore a key whose app is denied. SSH key
metadata has no operation environment, so it does not borrow deployment facts.
Missing or stale object targets check an explicit organization scope instead
of falling back to the selected team/project.

Connections remain confined to the active organization. Lists and named
repository/file reads expose organization credentials plus the caller's own
personal credentials, never another user's personal token. Installation
callbacks reject pending state belonging to another organization before
exchanging credentials or activating a connection. A removed `scm.connect`
binding prevents completing an already-started OAuth/App flow.

API bearer permissions remain a ceiling on the token owner's current role
bindings. Organization-bound tokens need the permission's token scope as
well as its role binding. Team-bound tokens cannot authorize organization
connections, organization keys or OAuth/App flows, even for an organization
owner or platform operator with `admin`. App operations additionally require
the target to lie under the token's live team ownership or an applicable live
team share; an organization role cannot widen that ceiling. Session selections
remain navigation context, not an additional grant.

No database migration or GraphQL field change is required. Existing per-app
roles can manage their app's keys and CI workflow. Organizations that previously
let viewers or team-only roles connect SCM hosts must assign `scm.connect` at
the organization to the people who should perform that operation.
