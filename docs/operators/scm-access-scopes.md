# SCM access scopes

SCM routes authorize the resource's owner (#2109). Choosing a team or project
does not grant access to an organization's connections or a sibling app.

Preconditions: this page describes the SCM permission catalog (`scm.read`,
`scm.write`, `scm.connect`, `scm.disconnect`, `scm.key_create`, `scm.key_delete`) plus
`app.update` for CI workflow operations. Read the bundled catalog with
`astro perms list`; trace one account's grant with `astro perms diagnose`.
The tests under Verify prove the behaviour. Placeholders: `<app-slug>` is the
registered app slug (`astro app list`); `<permission>` is a slug from
`astro perms list`.

```sh
astro perms list
astro perms diagnose <app-slug> --permission <permission>
```

Named repository and source-file reads require a live, active connection in
the current organization. Missing, inactive, deleted and foreign connections
return `NOT_FOUND` before any provider request; personal credentials remain
visible only to their owner.

| Surface | Required owner scope |
| --- | --- |
| Source connection lists, repository lists and source-file fetches | `scm.read` at the active organization |
| Create a repository through a connection (`createSourceRepository`) | `scm.write` at the active organization |
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

## Token scopes for SCM (#2605)

An app on the cluster (astrolift-studio first) reaches repositories through
the organization's stored connections instead of carrying its own PAT. Mint
its API token with the narrow SCM scopes:

| Token scope | Unlocks | Where |
| --- | --- | --- |
| `scm:read` | `scm.read`: `astroliftSourceConnections`, `astroliftAvailableRepos`, `astroliftSourceFile` | the token's own organization only |
| `scm:write` | `scm.write`: `createSourceRepository` | the token's own organization only |

Both hold only on an explicit organization scope equal to the token's
organization. They never satisfy an any-scope collection gate (SSH deploy
key lists stay closed to an `scm:read` token), a team-bound token cannot use
them on organization connections, and a token of another organization is
refused. As with every scope they are a ceiling: the token owner still needs
the role binding (`scm.write` comes with the stock org owner and org admin
roles; migration 0054 refreshes existing installs).

Pick them in the API token picker ("Read source", "Create repositories"), or
mint the token without a team so it is organization-bound:

```graphql
mutation {
  createApiToken(input: { name: "studio", scopes: ["scm:read", "scm:write"] }) { ok }
}
```

### createSourceRepository

`createSourceRepository(input: {connectionId, owner, name, visibility,
description?, autoInit?})` returns `{repoFullName, url, defaultBranch,
created}`. `visibility` is `private`, `internal` or `public`; `autoInit`
unset keeps the host's default (GitHub makes an initial commit, the others do
not). The call is idempotent: when `owner/name` already exists and the
connection can push to it, it comes back with `created: false` instead of an
error, so a retried create is safe. Every call is audited as
`scm.repository.create` with the connection, the repository and whether it
was created or already existed.

| Code | Meaning |
| --- | --- |
| `NAME_TAKEN` | the name exists and the connection cannot push to it, or the host refused the name |
| `OWNER_NOT_ALLOWED` | the install's org-only repository policy refuses the owner, or the connection cannot create under it |
| `CONNECTION_INACTIVE` | the connection is inactive or orphaned; reconnect it |
| `FORBIDDEN` | the host refused the connection's credential, or the connection cannot create repositories (a GitHub App installation token has no `administration: write`) |
| `NOT_FOUND` | no such connection in the active organization visible to the caller (another organization's or another user's personal connection reads the same) |
| `PERMISSION_DENIED` | the caller or token lacks `scm.write` at the organization |
| `VALIDATION` | a malformed owner, name or visibility, or one the host refuses for that owner |

The host's own error text never reaches the caller.

SCM uses the registry's live app-owner checks: every populated project and
team must be live, belong to the organization, and agree on the app's home
team. A team bearer follows that canonical home, using the project's team
only when the app has no direct team. A share cannot make an app with invalid
ancestry eligible. SSH collections exclude those app keys before counts and
pagination; a direct stale-key operation retains the explicit organization
fallback and requires an organization credential and grant.

No database migration or GraphQL field change is required. Existing per-app
roles can manage their app's keys and CI workflow. Organizations that previously
let viewers or team-only roles connect SCM hosts must assign `scm.connect` at
the organization to the people who should perform that operation.

## Verify

```sh
./run.sh test astrolift_scm/tests/test_access_scopes_2109.py astrolift_scm/tests/test_owner_coherence_2109.py \
  astrolift_scm/tests/test_create_source_repository_2605.py
```

All three modules pass.
