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

## Rollout observation

Rollout is observed up to 10 times, 15 seconds apart, each read under its own
60-second budget. A pending rollout or an exceeded read budget is observed
again; any other observation failure stops. Configuration alone never completes
a Deployment: without an observed rollout the completion step refuses with
`NATIVE_ROLLOUT_UNOBSERVED`. `invocation_verified` stays false: Pod and Node
readback does not prove token exchange or a successful prediction.

## Not in this slice

- The shared organization-owned native-model subscription path.
- Mixed unions and migrating a legacy GSA or its project-wide grants.
- Platform builds for native apps.
- Physical subject evolution (renamed namespaces or service accounts) and
  complete resource removal.
- Live GKE token exchange and Vertex inference acceptance.

Controlled generated-SDK, owned TLS, PostgreSQL and Temporal fixtures are local
evidence, not live GKE scheduling or Vertex inference.

See [original authority](native-app-identity-authority.md),
[preparation](gcp-gke-preparation-journal.md),
[IAM](gcp-workload-identity-journal.md),
[prepared rendering](gcp-prepared-identity-render.md), and
[guarded app configuration](gcp-guarded-app-apply.md).
