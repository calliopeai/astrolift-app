# Managed GitHub CI for multiple registered apps

Template generation 9 gives each ordinary GitHub app its own
`.github/workflows/astrolift-app-<app-guid-without-hyphens>.yml`, concurrency
group and review branch. Default dispatch, repo pulls and drift checks use
that same app path. Agent-only registrations retain their separate
`astrolift-agent-<slug>.yml` package validator; mixed app/agent registrations
use the ordinary app workflow. Source delivery for agent tasks remains separate.

## Configure each registration

Register each manifest independently, including its actual root or nested
`manifest_path`. For a CI-published image, persist `build_mode = ci_pushed`,
a private ECR `registry_repo_uri`, `dockerfile_path`, `build_context` and any
string `build_args` on that app. The build context is relative to the repository
checkout; the Dockerfile is relative to that context. For example, a nested
`extensions/demo/astrolift.toml` can use context `extensions/demo` and Dockerfile
`Dockerfile`. CI resolves that file to `extensions/demo/Dockerfile` at checkout.
The manifest's directory alone does not replace the saved build inputs.

Missing registry, Dockerfile or context refuses workflow sync with an actionable
`PRECONDITION`, before repository writes. Unsafe paths, malformed arguments and
embedded GitHub Actions expressions are also refused. An explicitly selected
`none` or `platform_build` mode keeps the deploy-only workflow, with image
building respectively outside this workflow or in the platform. A blank registry
on a `ci_pushed` registration never silently enables deploy-only behavior.

Use the app's **Settings → CI setup** controls to push secrets and sync its
workflow. The installed GitHub App needs access to the source repository;
workflow writes require Contents and Workflows write permissions. The existing
OIDC push-role setup must authorize the app's ECR repository. The role's region
comes from the private ECR URI, not a fixed region. Workflow sync attempts the
existing push-role provisioning path when the app has a registry-capable cluster.

`pushAstroliftCiSecretsToRepo` pushes five secret names ending in the selected
app's uppercase GUID without hyphens: `ASTROLIFT_PUSH_ROLE_ARN`,
`ASTROLIFT_ECR_URI`, `ASTROLIFT_APP_SLUG`, `ASTROLIFT_API_URL` and
`ASTROLIFT_DEPLOY_TOKEN`. The rendered workflow references its own deploy-token
name. `validateAstroliftCiSecrets` checks those exact names. Pushing one app's
secrets immediately rotates that app's deploy token; it preserves sibling app
secrets and tokens. Runs holding the rotated token must be restarted.

## Verify the generated sequence

Each app workflow checks whether its own commit-tagged image already exists.
Otherwise it builds using the selected Dockerfile, context and argument values,
then pushes `<registry_repo_uri>:<commit-sha>`. Argument values are passed as
argv entries rather than interpolated shell commands. A build or push failure
stops the job before notification. Notification then sends the same commit SHA,
branch and selected app to the deploy endpoint and requires a 2xx response.

A sync acknowledgement establishes file creation or a review PR, not image
publication or deployment success. Merge protected-branch review PRs and inspect
the actual Actions run, published image and platform deployment for each app.
Two registrations sharing one repository must each have that proof.

Managed GitHub writes use the reviewed blob SHA, or a create-only condition for
an absent file. A concurrent edit or creation is refused rather than overwritten;
review the current repository file before retrying. Reconciliation also reviews
the app's PR branch and refuses independent edits there. An unchanged owned
generated file can be refreshed on that branch; the write still uses its reviewed
SHA. A refusal does not advance the saved sync receipt or open a new PR. Other
providers that lack conditional writes refuse requests for this contract.

## Upgrade without removing someone else's workflow

Re-sync and push the new scoped secrets for each app after the platform update.
Review old shared `astrolift-ci.yml` and unsuffixed `ASTROLIFT_*` usage in the
repository. Legacy files lack enough app/organization/repository provenance to
be deleted automatically; disable or remove a confirmed obsolete workflow
through repository review. Retain any file used by another registration or by
an independent pipeline. A hand-edited managed file is also retained.

New files record app, organization and source repository ownership. Cleanup of
an app's former counterpart requires matching ownership and an intact content
stamp. Conditional deletion sends the inspected Git blob SHA, so a concurrent
edit is refused. The ownership comment is provenance, not a cryptographic
signature or permission grant. GitLab, Gitea and Bitbucket retain their existing
host-specific paths and contracts; this change does not certify equivalent
multi-app isolation for those hosts.
