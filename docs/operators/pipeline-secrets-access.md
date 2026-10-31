# Pipeline secret access

The pipeline secret API stores values in the existing encrypted `OrgSecret`
control-plane store. Keys are `astrolift/pipelines/<pipeline GUID>/secrets/<name>`.
Runtime job dispatch reads this exact namespace; an organization secret with a
bare name or another pipeline's same-name secret cannot supply a missing value.
Values are passed to the existing job secret materialization boundary, never
returned by GraphQL or persisted in a pipeline definition.

Preconditions: `secret.list` at the pipeline's owner (the live app of an
app-associated pipeline, the organization of an unassociated one) to list;
`pipeline.secret_manage` and `secret.write` at that same owner to set or
delete. A bearer credential needs `secret:write` for writes and a read scope
such as `read:apps` for listing, and a team-bound credential reaches only an
app its team owns or holds a share on. Surfaces: the console at
`/pipelines/<pipeline-id>/secrets`, or GraphQL via `astro api graphql`.
Placeholders: `<pipeline-id>` is `id` from `astro pipeline list --json` or
`astroliftPipelinesPage`; `<secret-name>` and `<secret-value>` are the name and
value you supply.

```graphql
query { astroliftPipelineSecrets(pipelineId: "<pipeline-id>") { id name createdAt updatedAt } }
mutation { setPipelineSecret(input: {pipelineId: "<pipeline-id>", name: "<secret-name>", value: "<secret-value>"}) { ok errors { code message field } data { pipelineId name } } }
mutation { deletePipelineSecret(input: {pipelineId: "<pipeline-id>", name: "<secret-name>"}) { ok errors { code message field } data { pipelineId name } } }
```

`astroliftPipelineSecrets(pipelineId: GUID!)` returns only public IDs, names and
creation/update timestamps. It requires `secret.list` at the pipeline's actual
owner. `setPipelineSecret` and `deletePipelineSecret` return typed mutation
results and require both `pipeline.secret_manage` and `secret.write`. Names are
trimmed, nonempty, contain no slash/NUL and fit the store's 512-character key.
Deleting is a soft delete; replacing encrypts the new value through the existing
backend. Storage errors return a generic typed failure without secret values.

An app-associated pipeline resolves that live app, including coherent live
organization, team and project ancestry. An unassociated pipeline resolves
explicit organization scope. Selected team/project context cannot substitute
for either owner. A missing, foreign or stale owner cannot reach the store;
mutation gates recheck the owner after locking the pipeline. Dispatch also
reloads the owner and rejects deleted runs and stale/foreign ancestry.

Bearer scope ceilings apply alongside RoleBindings. `secret:write` covers
`secret.write` and `pipeline.secret_manage`; `write:apps` alone does not grant
pipeline secret management. Listing metadata also needs a scope such as
`read:apps` that covers `secret.list`. The credential must match the active
organization and, when team-bound, cover the actual app through ownership or an
appropriate existing app share. Admin scopes and platform operators cannot
bypass those organization/team ceilings. Team credentials cannot access an
unassociated organization pipeline.

The `/pipelines/<GUID>/secrets` page opens the Secrets section and is linked
from pipeline detail. Its documents participate in schema validation/codegen.
The UI exposes writes only with both permissions, retains drafts after refusal,
keeps a rejected deletion confirmation open, and reports refresh failures
separately after a committed write or deletion.

Validation uses real PostgreSQL RoleBindings, session and bearer HTTP requests,
encrypted storage boundaries and dispatch retrieval. Browser route navigation
uses controlled HTTP fixtures and is a separate frontend proof; it does not
claim to replace backend authorization tests. No production secret migration,
cloud secret write or existing-source deletion is required by this change.

## Verify

```sh
astro api graphql -q '{ astroliftPipelineSecrets(pipelineId: "<pipeline-id>") { id name createdAt updatedAt } }'
./run.sh test astrolift_pipelines/tests/test_secret_api_2171.py
```

The listing names `<secret-name>` after a set and omits it after a delete, never
showing a value, and the test run passes.
