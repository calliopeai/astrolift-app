# Managed CI region and preview

For GitHub app workflows, CI setup displays and copies the current backend
rendered workflow, the same stamped body used by workflow sync. The configured
deploy branch is preserved. Refresh the app after changing registry or cluster
configuration; a rendered preview does not inspect the source repository.

Preconditions: `app.update` on the app for `pushAstroliftCiWorkflowToRepo` and `resyncAstroliftCiWorkflow`; the rendered body is `ciWorkflowSyncStatus.renderedText` on `astroliftApp(slug)`, shown under Build settings at `/apps/<app-slug>/settings`, and `astro ci setup <app-slug>` pushes it with the webhook and secrets. For an AWS build the app needs a private ECR repository URI and a live, active, AWS-backed default cluster owned by its organization or platform-shared. Placeholders: `<app-slug>` is `slug` from `astroliftAppsPage`.

The AWS build authenticates in the region encoded by the app's private ECR
repository URI. This remains the registry region when a coherently owned
cluster pulls across regions. A bound default cluster must be live, active,
AWS-backed, and owned by the app's organization or platform-shared. An invalid
binding or malformed/non-private ECR coordinate produces no AWS preview and
refuses workflow sync before push-role provisioning or source-host writes.
Repair the configuration before retrying; the UI disables copying an
unavailable workflow rather than substituting a sample region.

Without an ECR URI, the workflow only notifies Astrolift of the commit SHA.
It uses the default AWS cluster's configured region when available. With no
cluster or no region it omits AWS authentication, which deploy-only does not
need. No install-wide or `us-west-2` region is assumed.

GitLab, Bitbucket and Gitea retain their existing operator-supplied
`ASTROLIFT_AWS_DEFAULT_REGION` contract. GCP, Azure and native-Kubernetes
reference steps remain provider-specific and use the app's deploy branch;
this correction does not add cloud support to the managed ECR build templates.

Template version 8 (the current template is 11) made previous managed GitHub bodies eligible for the
existing drift/safe-sync flow. Existing operator edits still require review
through that flow. Rendering and previewing do not change AWS resources or
repository files; syncing remains an explicit existing operation.

## Verify

```sh
astro api graphql -q 'query { astroliftApp(slug: "<app-slug>") { ciWorkflowSyncStatus { state currentTemplateVersion syncedTemplateVersion renderedText } } }'
cd backend && pytest astrolift_scm/tests/test_ci_region_2148.py
```

`renderedText` contains an `aws-actions/configure-aws-credentials` step whose `aws-region` is the region in the app's ECR repository URI (no such step without an ECR URI and cluster region), `currentTemplateVersion` is `11`, and the test module passes.
