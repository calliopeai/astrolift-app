# Native Endpoint deployment on GKE

`DeployAppWorkflow` deploys an app whose only managed services are Vertex AI
Endpoints with native GKE Workload Identity (#2278). This is the first slice:
Endpoint-only apps on a GKE cluster, with the GUID-owned Google service account
the original-source bootstrap creates for the app. Every other app deploys
exactly as before.

## Route

The first activity, `validate_deployment_identity_origin`, admits the protected
original caller and binds the actual Temporal run. An admitted native origin
answers `NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED`, as it did before this slice.
A run carrying the `deployment-native-endpoint-pipeline-v1` patch takes that as
the route to the native steps; an older history refuses on it. The activity's
signature and outcomes are unchanged, so a worker of either release can run it.
No legacy step (manifest resync, build, namespace, workload identity, render,
apply, rollout poll) runs for a native Deployment, in success or failure.

The native steps:

1. `astrolift.deploy.mark_deploying`.
2. `astrolift.app.configure_native_endpoint`, one heartbeating attempt (60
   minutes, 30-second heartbeat), never retried:
   - preflight refuses, before any effect: a platform build, mixed service
     unions (Cloud SQL, GCS, Pub/Sub or any non-Endpoint service, shared-model
     subscriptions), secret bundles, secret-valued bindings, saved or current
     literal secrets, managed volumes, unsupported controllers and workload
     identity overrides. The app's existing workloads, annotation and identity
     are left as they are;
   - a prior operation's journal still `SENT` or `UNKNOWN` refuses with
     `PRIOR_SEND_UNRESOLVED`, and any other incomplete prior journal with
     `PRIOR_OPERATION_INCOMPLETE`, before original-source bootstrap;
   - original-source bootstrap, the complete Endpoint union, KSA preparation
     with receipt binding, resource-scoped Endpoint IAM union reconcile (current
     etag, before-hash, foreign and conditional bindings kept), KSA annotation,
     prepared render and guarded apply, then bounded rollout observation bound
     to the completed apply journal handoff.
3. `astrolift.app.complete_native_endpoint`, guarded completion: it requires
   an observed rollout in the receipt, re-admits the original caller and run,
   checks that every journal the receipt names is `OBSERVED` for this
   operation and the apply operation carries the receipt's plan hash, then
   marks the Deployment `running`.

Any refusal goes through `mark_deployment_identity_refused`, which writes
`aborted_reason = NATIVE_CONFIGURATION_REFUSED:<REASON>`. Reasons are fixed
upper-case codes, never native text. A timeout or lost worker records
`CONFIGURATION_UNCONFIRMED`.

## Recovery

A later Deployment of the same app on the same cluster is the next generation
over resolved history: it reuses the original GSA and KSA UIDs, supplies the
previous completed IAM fence, and writes IAM only when the Endpoint union
changed. The IAM port reconciles the complete current union, so detaching one
Endpoint removes only grants no current consumer still needs.

A lost reply, a dead worker or an expired heartbeat never proves an old send
was cancelled. The configuration activity is not retried, and while any journal
stays `SENT` or `UNKNOWN`, or incomplete, every later Deployment refuses before
it reads or writes anything native.

### Settling a stuck journal

Resolving it is an operator step, `resolve_native_gcp_journal`:

```
python manage.py resolve_native_gcp_journal --app <app guid> --cluster <cluster guid>
python manage.py resolve_native_gcp_journal --app <app guid> --cluster <cluster guid> \
    --operator <email> --attestation "<what you checked>" --apply
```

Without `--apply` it lists the unresolved journals. With it, every unresolved
preparation, IAM and apply journal of the app on that cluster becomes
`RESOLVED` in one transaction, or none does, with one append-only
`GCPNativeJournalResolution` row each (operator, attestation, prior state and
ledger hash, and the dropped intents). It calls no cloud or cluster API and
never records an observation. It refuses:

- `OPERATION_STILL_ACTIVE` while the Deployment that owns the operation is not
  `failed`, `superseded` or `rolled_back`;
- `JOURNAL_BUSY` while a worker holds the journal's mutex;
- `RESOLUTION_GUARD_WINDOW` for a `SENT`/`UNKNOWN` send until the journal has
  not moved for two hours, longer than the activity's 60-minute budget and
  every bounded RPC deadline;
- `ATTESTATION_REQUIRED` without a 20 to 500 character attestation, and
  `OPERATOR_REQUIRED` without an active user.

What settling does:

- Preparation: pending intents are dropped; recorded namespace and KSA UIDs
  stay. The next Deployment does not adopt an object a dropped create may have
  made: it refuses `EXISTING_SUBJECT_UID_UNRECORDED`. Before applying, check
  each dropped `POST` path and delete a stray namespace or KSA the lost create
  left (it carries the `astrolift.io/preparation-operation` annotation of the
  failed operation).
- IAM: grants a pending write or an unfinished removal may have left are folded
  into the owned set. They name the app's own GSA and KSA principals, so the
  next reconcile removes any the current Endpoint union no longer wants, and
  keeps foreign and conditional bindings as always.
- Apply: the pending intent is dropped; recorded resource UIDs stay.

The next Deployment takes the settled journals as its previous generation.

A teardown's grant removal (see [Teardown](#teardown)) has no Deployment. It is
settled only while the app is tearing down, with the same guard window, and its
`GCPNativeJournalResolution` row names no Deployment. Its unfinished writes and
removals are folded into the owned set, so the next teardown run removes
whatever is still there.

## Rollback

`rollbackDeployment` on a native app no longer refuses
`NATIVE_HUMAN_ORIGIN_REQUIRED`. It captures a fresh protected origin
(the caller needs `app.deploy` as well as `app.rollback`), creates a `rollback`
Deployment of the prior superseded image, honours the environment's approval
quorum like any native Deployment, and runs it as the canonical
`DeployAppWorkflow`, so the native steps above apply unchanged. The legacy
`RollbackDeploymentWorkflow` still refuses a native app. The current Deployment
stays `running` until the rollback is observed live; completion then marks it
`rolled_back`.

## Teardown

Deregistering a native app (`deregisterAstroliftApp`, or a temporary app's
expiry) or tearing it down (`tearDownApp`) revokes every grant its IAM journals
own, through those journals, before any managed service is deprovisioned. The
activity `astrolift.app.remove_native_endpoint_identity` runs after the
namespace delete. On each cluster where the app has an IAM journal it:

- takes the executor's preparation and IAM mutexes, without waiting, so it
  never runs beside a native configuration;
- reserves a removal operation on the IAM journal, the next revision, with the
  empty desired union: no Endpoint grant and no KSA principal;
- runs the IAM port. Every Endpoint prediction grant and every Workload
  Identity binding on the GSA that the journal owns is removed against the
  current etag and before-hash, and a final pass reads every policy back.

Bindings the journal does not own stay: another app's grant on the same
Endpoint, foreign and conditional bindings, and an equivalent grant that was
there before the app's. The removal reads the project, the GSA and IAM policies
and writes only those policies. No Endpoint, model, GSA or KSA is created or
deleted; the GSA stays, without grants or impersonation bindings.

It refuses before any native call, as `NATIVE_IDENTITY_REMOVAL_PENDING:<REASON>`:

- `APP_NOT_TEARING_DOWN` unless the app is tearing down;
- `PRIOR_SEND_UNRESOLVED` or `PRIOR_OPERATION_INCOMPLETE` while a Deployment's
  IAM operation is unresolved (settle it first, above);
- `PREPARATION_BUSY` or `JOURNAL_BUSY` while a worker holds a mutex;
- `CURRENT_IDENTITY_SOURCE_UNAVAILABLE` when the cluster's declared credential
  changed, a renamed cluster included, and `ORIGINAL_TARGET_INTEGRITY_FAILED`
  when the recorded target does not match its hash.

Partial inventory stays pending. A policy that cannot be read (denied, or an
Endpoint deleted elsewhere), a lost reply, or a GSA that was replaced or
disabled leaves the journal unobserved, with its removal obligations kept.
Every native call is fenced by a checkpoint that re-reads the app's teardown
state and the declared credential; a change while it runs stops it before the
next write (`CURRENT_OPERATION_UNCONFIRMED`). While the removal is pending:

- `DeregisterAppWorkflow` reports `native_identity` still live and holds the
  managed services (`managed_services` still live, nothing deprovisioned), so
  the Endpoints whose policies still name the app stay readable. It keeps the
  platform rows and does not remove the CI workflow. An expiring app's kept
  services stay kept. Re-firing the deregister resumes the same removal
  operation.
- `TearDownAppWorkflow` revokes the deploy tokens and stops: no managed service
  is deprovisioned and the rows stay. Running it again resumes the removal.

The activity runs once per teardown run, never retried. A `SENT` write is
resolved only by its exact after-hash, never by sending again. If another
writer moved the policy since, the removal stays `SENT_SUBMISSION_UNRESOLVED`
until an operator settles it ([above](#settling-a-stuck-journal)); the next
run then removes what the settled ledger still owns.

After the removal the IAM journal has moved past the last Deployment's fence,
and the native configuration refuses an app that is tearing down or
deregistered (`APP_TEARDOWN_IN_PROGRESS`): at its preflight, and again on the
locked app row when it reserves its IAM operation. A configuration that was
already running when the teardown began either reserved its IAM operation
first, so the removal finds that journal and stays pending (`PREPARATION_BUSY`)
until the run ends and the next teardown run revokes its grants, or is refused
there before it grants anything. Either way no Deployment adds the grants back.
Removing a grant from a policy does not revoke a token already issued, and
access another grant allows is not denied.

## Rollout observation

Rollout is observed up to 10 times, 15 seconds apart, each read under its own
60-second budget. The budget counts the Kubernetes and GKE reads only: the
original caller and run are re-admitted before and after every read, about 80
times in one observation, and that time does not spend it. A pending rollout or
an exceeded read budget is observed again; any other observation failure stops.
Configuration alone never completes a Deployment: without an observed rollout
the completion step refuses with `NATIVE_ROLLOUT_UNOBSERVED`.
`invocation_verified` stays false: Pod and Node readback does not prove token
exchange or a successful prediction.

## Not in this slice

- The shared organization-owned native-model subscription path.
- Mixed unions and migrating a legacy GSA or its project-wide grants.
- Platform builds for native apps.
- Physical subject evolution (renamed namespaces or service accounts).
- The rest of resource removal: deleting the app's GSA at teardown, and
  deleting its namespaces and KSAs by their recorded UIDs (the legacy namespace
  delete still runs, by name). A teardown whose GSA was deleted outside
  Astrolift stays pending with no recovery yet.
- Live GKE token exchange and Vertex inference acceptance.

Controlled generated-SDK, owned TLS, PostgreSQL and Temporal fixtures are local
evidence, not live GKE scheduling or Vertex inference.

See [original authority](native-app-identity-authority.md),
[preparation](gcp-gke-preparation-journal.md),
[IAM](gcp-workload-identity-journal.md),
[prepared rendering](gcp-prepared-identity-render.md), and
[guarded app configuration](gcp-guarded-app-apply.md).
