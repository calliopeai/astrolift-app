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

A registration that names no build context gets one detected from its manifest
path: `extensions/demo/astrolift.toml` builds `extensions/demo`, with `Dockerfile`
inside it. A manifest that sets `build_context` or `dockerfile_path` itself keeps
the repository root as the app's context, so those paths apply from the manifest's
directory. `astro app register` and the new-app wizard show the detected paths
before registering; an explicit context, including `.`, is stored as given.

Detection runs only at registration. An app registered earlier keeps the context
it saved, usually `.`, until someone changes it with
`astro app set-build-context <app> <path>` or the app's **Settings → Source and
build → Build**. The workflow in the repository keeps the old paths until it is
pushed again: sync it under **CI setup**, or run
`astro ci setup <app> --skip-webhook --skip-secrets`. A full `astro ci setup` also
re-pushes the CI secrets, which rotates the deploy token.

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

## Change the source repository

A repository renamed on GitHub keeps its webhook, workflow file and Actions
secrets, but the app keeps the old `owner/name`, and everything that matches by
name stops matching: the GitHub App webhook receiver finds apps by repository
name, the per-repository receiver refuses a delivery from any other
repository, and the push role trusts `repo:<owner>/<name>:*`. The
`setAppSource(input: {id, sourceRepo, ifMatchVersion})` mutation points the app
at the new name, or at a different repository (#2478). It needs `app.update` on
the app and is audited as `app.set_source` with both names.

The repository must be in the organization's GitHub App installation on its
owner; the app stores the name as GitHub lists it. The organization's
repository policy applies, and another app in the organization registered from
the same repository and manifest path is a conflict. A `sourceUrl` that named
the old repository follows it.

Each app moves on its own, so after a rename change every app the organization
registered from the repository. The workflow definitions the old repository
declared (`workflows/*.toml`) are keyed on its name; once no other app of the
organization names it, they follow, and the next sync updates them in place and
retires the ones the repository no longer declares. They never merge into a
repository that already has definitions of its own. A reviewed schedule (#2463)
approved a review that includes each definition's repository and version, so
once its definitions move it skips every fire ("reviewed schedule requires a
new review or current authority") until someone approves it again.

The change then runs autowire against the repository. The workflow sync
re-points the push role's trust and rewrites the app's workflow, whose
ownership comment names the repository; on a protected deploy branch that is a
review pull request to merge. The CI secrets are pushed to the repository,
which rotates the deploy token, so restart runs holding the old one. After a
move to a different repository the old repository keeps its workflow file, but
it can no longer assume the push role or deploy. The manifest is not read
again: run **Resync from source** when the new repository's differs. The
returned app's `autowire` names any step that needs attention.

## Deregister removes the app's workflow

Deregistering an app removes its managed GitHub workflow from the source
repository (#2480). The workflow assumes the app's push role and sends its
deploy token, and deregister removes both, so every later run would fail. On
the deploy branch, deregister checks the app path, the agent path and the path
the app's last sync recorded. It deletes a file only when its ownership comment
names this app, organization and repository and its content stamp is intact;
the delete sends the inspected blob SHA, so a concurrent edit is refused. A
protected deploy branch gets the deletion on
`astrolift/remove-ci-workflow-<app-guid-without-hyphens>` and a pull request to
merge. An edited, legacy or other registration's file stays in place.

The removal runs after the app's platform rows are soft-deleted, so its commit
cannot redeploy the app and the CI resync sweep does not restore the file. The
`ci_workflow` step of the `DeregisterAppWorkflow-<app-guid>` result names each
file removed, put up for review or left in place. A removal that cannot reach
the repository does not reopen the finished teardown; the step names the files
to delete by hand. GitLab, Gitea and Bitbucket keep the managed CI in a
repository-wide file that can hold other jobs, so deregister names it and
leaves it. A sync pull request the app opened earlier is not closed.
