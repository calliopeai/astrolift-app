# Atomic workflow review and reviewed schedule consent

Servers advertising `workflows.atomic_full_review` provide exact-GUID full review and explicit reviewed schedule consent. Review reads native definitions; it does not edit, enable or execute them.

Preconditions: `workflow.read` at the actual scope of the root and every reachable child definition and `agent.read` on every source and configured agent to review; activation adds root `workflow.update`, root and child `workflow.trigger` and `agent.dispatch` on each selected agent, plus the install's step-up elevation for a browser session; the surface is GraphQL (the operations below) via `astro api graphql -f <file>`, on a server whose `astroliftServerInfo.capabilities` lists `workflows.atomic_full_review`. Placeholders: `$workflowId` is the configured workflow `guid` (from `createWorkflow` or `astro workflow list`); `id` for the definition review is the definition `guid` from `astro workflow definitions`; `expectedScheduleReviewDigest` is `scheduleReviewDigest` from the review you just read; `requestId` is a new UUID you persist before calling.

`workflowDefinitionFullReview(id: GUID!)` reads the root, reachable child definitions, stages and source agent identities from one dedicated PostgreSQL `REPEATABLE READ, READ ONLY` transaction. Its `canonicalReview` is the authoritative complete graph. `reviewDigest` is SHA-256 of those exact UTF-8 bytes. `revision` is the existing native definition revision, computed from the same captured rows. `capturedAt` is the capture transaction's statement time. `rootManifest` is a native TOML projection of the root only; it is not a replacement for reviewing the full graph.

Every root and child needs current `workflow.read` at its actual scope. Source and configured agents independently need `agent.read`, including a source agent FK that a configured binding replaces. Organization membership, bearer scope/team ceilings, live owners, IdP group membership and ABAC are checked freshly. A missing, foreign or unreadable child/agent refuses the whole review before raw content is returned.

`workflowScheduleFullReview(workflowId: GUID!)` adds canonical configured inputs, resolved defaults, stage bindings, trigger kind/reference, cron, enabled state, owner identity and delivery channel identities/versions. Credentials for delivery channels are never included. `compiledPlansDigest` covers the compiled root and nested plans with the configured root bindings applied. `scheduleReviewDigest` binds the full graph digest, configuration digest, compiled plan digest and `ENABLE_REVIEWED_SCHEDULE` intent. Workflow's operational `version` counter is informational and excluded from this content pin; engine observations and internal schedule revisions/hashes do not invalidate their own approval.

Enable a disabled definition separately using the existing revision-bound definition activation API. Schedule consent cannot enable a definition or satisfy a run's human gates. Capture a new schedule review after any intentional content change.

```graphql
query ReviewSchedule($workflowId: GUID!) {
  workflowScheduleFullReview(workflowId: $workflowId) {
    workflowId definitionId capturedAt consistency canonicalization
    canonicalReview rootManifest canonicalConfiguration
    reviewDigest revision inputSchemaDigest configurationDigest
    compiledPlansDigest scheduleReviewDigest activationIntent
  }
}

mutation ApproveSchedule($input: ActivateReviewedWorkflowScheduleInput!) {
  activateReviewedWorkflowSchedule(input: $input) {
    ok errors { code message field }
    data {
      id workflowId definitionId approvedByUserId approvedAt
      scheduleReviewDigest reviewDigest definitionRevision
      configurationDigest compiledPlansDigest credentialKind credentialExpiresAt
      isCurrent databaseStatus engineState
    }
  }
}
```

Pass the exact configured workflow GUID, captured `expectedScheduleReviewDigest`, a new `requestId` and `confirmed: true` after the person reviews the returned content and consent intent.

- **Signature:** `activateReviewedWorkflowSchedule` answers `CONFLICT` with the review error (the content changed since the digest was captured, or the request ID was reused with different arguments), or `PERMISSION_DENIED` `Current schedule authority is required`; a saved receipt always reports `engineState: UNCONFIRMED`; a later timer skips with a review-required reason when the content differs from the approved pin.
- **Fix:** capture a fresh `workflowScheduleFullReview`, review it, and activate again with the new digest and a new `requestId`; for `UNCONFIRMED`, inspect and reconcile the schedule with `workflowSchedule(workflowId:)` / `reconcileWorkflowSchedule` (see [configuration and schedule recovery](workflow-configuration.md)).
- **Rollback:** a refused activation saves nothing; a saved receipt is superseded by the next activation and does not by itself change the engine, so disable the configured workflow (`updateWorkflow(isEnabled: false)`) to stop fires. Activation requires current root `workflow.update`, root/child `workflow.trigger`, and selected agent `agent.dispatch`, as well as all read authority. It rechecks after content locks. The existing install-wide step-up/elevation policy applies to person consent, including a fresh persisted browser-session check after waits. API bearers follow the existing native exemption from session step-up; their current credential and ceilings are still required. The receipt records the actual approver and optional API-token row/ceiling; it stores no plaintext bearer credential. Repeating the same actor's request ID and arguments recovers the saved receipt. Reusing that ID for changed arguments refuses. Recovery does not reactivate a superseded receipt.

The intended enable change is pinned deliberately: the person's digest identifies the pre-activation configuration; the sealed receipt preserves it and the resulting configuration with `isEnabled: true`. `isCurrent` means this receipt is the configured workflow's current approval pointer. It does not prove current content, authority or engine state.

The mutation saves `databaseStatus: SAVED` and always returns `engineState: UNCONFIRMED`. It performs no Temporal transport. Inspect/reconcile the configured schedule through the existing schedule APIs to observe or request engine activation; never display the saved receipt as proof that the engine is active.

Each timer reservation checks the current approver's account, organization membership, live grants, IdP groups, policy and recorded bearer validity. A revoked, expired, deleted, changed or narrowed bearer stops future reservations; its scope/team/expiry ceiling cannot expand an existing approval. Background admission carries current operation facts but no fabricated client IP, sign-in, MFA or IdP request assertion. A policy requiring unavailable human request facts refuses the timer. This is revocable consent, not unlimited credential delegation.

A timer whose definition, stages, inputs, bindings, trigger, owner or selected delivery channel content differs from the approved pin skips with a review-required reason. It reserves no run. A successful reservation records a system actor, links the actual approval as provenance, and executes the encrypted immutable root/nested plans. It does not resolve live bindings after reservation. A changed agent or app identity/version/owner refuses its later dispatch.

Reviewed stage executions save all execution-significant stage fields, including kind, role, prompt and approvers. Human gate authority, pending lists, notifications and projections use that saved descriptor. Later edits cannot turn an approved gate into a checkpoint or substitute a different named approver for an already reserved run. Existing run-specific human decisions remain required.

Limits are 128 reachable definitions, 2,048 stages/agent candidates, nesting depth 8, 50 selected delivery channels, and 2 MiB per canonical review or sealed approval payload. Unsupported graphs, invalid inputs, invalid bindings and unavailable/corrupt sealed plans refuse. Migration `workflows.0021_reviewed_schedule_approval` adds the durable receipt/run association, saved stage descriptors and PostgreSQL immutability guards. Existing schedules without a reviewed receipt keep their previous behavior.

## Verify

```bash
astro workflow schedule inspect <workflow-guid>
pytest workflows/tests/test_atomic_review_2459.py astrolift_workflows/tests/test_reviewed_schedule_temporal_2459.py
```

The schedule observation reports `confirmed: true` with `desiredActive` and `observedState` agreeing, `activateReviewedWorkflowSchedule` returned `databaseStatus: SAVED` with `isCurrent: true` for the receipt, and the test modules pass.

## Rollout

Deploy and drain old worker images before exposing the new activation API, then deploy the matching API image. The saved-descriptor database guard refuses old stage-creation paths on a run already linked to a reviewed approval. It does not make arbitrary mixed versions safe: an old scheduler can reserve an unlinked run without knowing the approval association. Old API images also lack immutable gate projections. Do not activate reviewed schedules while those images remain in service. After adoption, drain reviewed schedules before any rollback to old API/worker code; use forward repair when possible. A saved approval by itself is never proof of deployment or engine activation.
