# Registry access scopes

The additive [app dependency context](app-dependency-context.md) read requires
`app.read` on one exact app/environment pair and exposes only redacted,
source-stamped persisted observations. It grants no cluster-control or deploy
authority and refuses expected cluster/provider GUID mismatches.

Preconditions: this page describes the registry permission catalog (`app.read`,
`app.create`, `app.update`, `app.transfer`, `app.approve_deploy`) and the bearer
scopes `read:apps`, `write:apps` and `app:onboard`. Read the bundled catalog
with `astro perms list`; trace one account's grant with `astro perms diagnose`.
The tests under Verify prove the behaviour. Placeholders: `<app-slug>` is the
registered app slug (`astro app list`); `<permission>` is a slug from
`astro perms list`.

```sh
astro perms list
astro perms diagnose <app-slug> --permission <permission>
```

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

Another organization's team or project reads exactly like a missing one. Moves,
team shares, approver teams and repository registration answer a foreign guid
with the `NOT_FOUND` a guid that does not exist gets, and write nothing. An
app's approver list names only active members of its organization, the people
the approval picker offers; a pending deploy neither shows nor counts anyone
else stored on it (#1866). An approver team counts only those of its members
who are active members of the organization, and so, with neither a list nor a
team, do the organization's `app.approve_deploy` holders. When the list or the
team leaves nobody who can approve, every approver is refused and the deploy
waits on nobody; fix the approver policy to release it.

## Verify

```sh
./run.sh test astrolift_registry/tests/test_app_permissions.py astrolift_registry/tests/test_app_team_access.py astrolift_registry/tests/test_transfer_app.py astrolift_registry/tests/test_assign_project.py astrolift_registry/tests/test_dependency_context_2208.py astrolift_registry/tests/test_cross_org_references_1866.py
```

Every module passes; `test_cross_org_references_1866.py` holds the foreign-GUID
`NOT_FOUND` and approver-membership cases.
