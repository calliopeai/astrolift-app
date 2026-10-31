# Redeploy source pins

`redeployApp(input: {id: ...})` repeats the selected deployment under current
`APP_DEPLOY`, tenant, token and approval checks. It preserves the saved image
reference, configuration snapshot, strategy and immutable source commit in the
new deployment. The existing workflow input carries that saved commit to the
builder, including when approval delays dispatch.

Preconditions: `app.deploy` on the deployment's app, within the bearer token's
team ceiling, plus the app's approval policy when it requires approval.
Surfaces: the `redeployApp` mutation via `astro api graphql`. Placeholders:
`<deployment-id>` is `id` from `astroliftDeploymentsPage(appSlug: "<app-slug>")`;
`<app-slug>` is the registered app slug (`astro app list`);
`<new-deployment-id>` is `data.id` returned by the mutation.

```graphql
mutation { redeployApp(input: {id: "<deployment-id>"}) { ok errors { code message field } data { id status commitSha environmentName } } }
```

When the app's effective build strategy requires a platform rebuild, the
selected deployment must have a full resolved hexadecimal SHA-1 (40 characters)
or SHA-256 (64 characters) commit. Missing or malformed historical source data
returns `PRECONDITION` before a new deployment, original-authority receipt,
execution receipt or workflow is created. Start a normal deployment with an
explicitly reviewed source ref instead; do not infer a commit from an image tag,
change build mode to bypass the refusal, or silently substitute the current
branch.

The builder checks again before build preparation when a redeploy was accepted
with builds off and the strategy changes while approval is pending. It requires
the saved row commit and workflow commit to match exactly; a blank, mutable or
changed pin fails before registry/identity preparation or source resolution.

CI-pushed apps and apps whose effective build strategy is off keep the existing
artifact-only behavior even when the saved commit is blank. The decision uses
both build mode and build strategy, as the deploy workflow does.

This pins the builder's source checkout, not every input of a reproducible
build. Current app configuration, manifest synchronization, external dependencies,
base images and build tools retain their existing behavior. A rebuilt image may
have a different digest. Redeploy remains a normal deployment, not an identity
repair endpoint or a guarantee that an old runtime can safely be overwritten.
Existing pending work and historical workflow status need independent review.
No existing deployment or in-flight workflow is rewritten by this change.

The builder identifies existing redeploy records by their manual trigger and
retained source-deployment lineage. Rollback and promotion have distinct trigger
kinds and keep their existing contracts. Physical deletion of the source can
clear that historical lineage; this change does not reconstruct lost lineage
or retroactively identify ambiguous legacy rows. Normal soft deletion retains
the foreign-key identity.

## Verify

```sh
astro api graphql -q '{ astroliftDeployment(id: "<deployment-id>") { id status commitSha } }'
astro api graphql -q '{ astroliftDeployment(id: "<new-deployment-id>") { id status commitSha } }'
./run.sh test astrolift_lifecycle/tests/test_redeploy_source_pin_2282.py
```

Both reads show the same 40- or 64-character `commitSha`, and the test run passes.
