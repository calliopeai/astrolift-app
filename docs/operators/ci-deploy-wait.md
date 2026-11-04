# Managed CI waits for the rollout

From template version 12 the GitHub-managed workflow's **Notify Astrolift**
step no longer ends green when the platform accepts a deploy. A `2xx` from
`POST /api/cli/v1/apps/<app-slug>/deploy/` only means accepted, so the step
then polls the response's `polling_url`
(`/api/cli/v1/deployments/<deployment-id>/status/`) with the same deploy token:

| Deployment outcome | Step result |
|---|---|
| Reaches `running` | exit 0, `Astrolift deploy succeeded (running).` |
| Stopped or replaced by a newer deploy to the same app and environment | exit 0, `Deployment superseded by a newer deploy; ...` |
| Fails | exit 1, `Astrolift deploy FAILED (<status>): <reason>` |
| Not terminal after 15 minutes | exit 1, `Astrolift deploy did not finish within 15 minutes; ...` |
| Status endpoint answers `3xx` or `4xx` (other than `429`) | exit 1 at once |

The poll runs every 10 seconds; `5xx`, `429` and network errors are retried
inside the 15 minute bound. GitHub's `cancel-in-progress` concurrency already
cancels most superseded runs before they reach this point. GitLab, Bitbucket
and Gitea templates are unchanged and still end on the accept.

Preconditions: the app's deploy token (`alft_dt_...`, the secret the workflow
already sends). The status endpoint accepts any live token issued for the app
that owns the deployment; it answers `404` for another app's deployment.

## Superseded reads as superseded

The platform keeps one in-flight deploy per (app, environment). A deploy a
newer one stops mid-flight is stored `failed` with the reason
`superseded by a newer deploy` (or, when its own workflow observed the abort,
`aborted: superseded by a newer deploy or cancelled`). Readers now see it as
`superseded`:

- GraphQL `status` on deployments, previews and app summaries is `superseded`,
  and the `status` / `statuses` list filters match on that: `failed` excludes
  these rows, `superseded` includes them. The app health summary's recent
  failure flag ignores them.
- The status endpoint returns `status: "superseded"`, `superseded: true`,
  `reason`, and `state: "cancelled"` (terminal for older `astro ci deploy`
  clients). A deploy that ran and was later replaced returns `state:
  "succeeded"` and `superseded: true`.
- The deployments list and detail show the Superseded pill and no failure
  panel; the lifecycle log line reads `superseded: <reason>`.

## Existing apps

A repository keeps its old workflow until it is re-synced. A stamped file at
version 11 or older reads `template_stale` in `ciWorkflowSyncStatus` and is
safe to overwrite. Re-sync one app with `resyncAstroliftCiWorkflow` (Build
settings, **Sync workflow file**, at `/apps/<app-slug>/settings`), the whole
fleet with the platform-admin `resyncAllAstroliftCiWorkflows` mutation, or
enable the held `ci_workflow_resync` schedule through
`ASTROLIFT_ACTIVE_SCHEDULES`. A hand-edited file reads `repo_drift` and is
never overwritten by the sweep.

## Verify

```sh
astro api graphql -q 'query { astroliftApp(slug: "<app-slug>") { ciWorkflowSyncStatus { state currentTemplateVersion syncedTemplateVersion } } }'
cd backend && pytest astrolift_scm/tests/test_ci_notify_waits_for_rollout.py astrolift_lifecycle/tests/test_cli_deploy_api.py
```

`currentTemplateVersion` is `12`; after a re-sync `syncedTemplateVersion` is
`12` and the next push's Actions run ends with one of the lines above.
