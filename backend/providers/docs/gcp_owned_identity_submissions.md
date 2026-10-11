# Internal owned GCP IAM submission receipts

`NativeGCPIdentity.reconcile(..., submission_hook=...)` adds a strict, internal
submission boundary to the existing Endpoint/GSA IAM union port. It creates no
identity or workload and exposes no public API. The caller supplies the trusted
journal/fence/current-authority implementation; typed Python objects and journal
GUID/version values are not cryptographic durability or authorization proof.

The existing `persist(OwnedGrantLedger)` callback remains for observed ownership,
pending resolution and removal obligations. The strict hook alone introduces new
`UNSENT` and `SENT` phases. For each changing native policy:

1. Create a stable submission UUID and a bounded metadata-only intent binding the
   exact resource, context SHA, before/after full-policy hashes, native etag SHA,
   owned-after grants, and complete desired-union SHA. Commit `UNSENT` through the
   hook. This includes the entire current owned ledger.
2. Re-admit current project/GSA/source/role authority. Commit `SENT` through the
   hook before entering the SDK setter. Every checkpoint must return exactly
   `None`; booleans or other return values refuse.
3. Invoke the actual native setter once, with SDK retries disabled and the existing
   deadline/response bounds. Its original etag remains in the exact native request.
   Verify the full after-policy hash by readback before clearing pending ownership.
4. Preserve foreign/conditional policy entries and complete the same bounded final
   union/role/current-authority observations as the original port.

The hook is `Callable[[PolicySubmission], DurableSubmissionReceipt]`:

- `PolicySubmission(context_sha256, intent, ledger)` supplies immutable objects.
  `submission_sha256` identifies the phase-independent intent/union;
  `ledger_sha256` hashes its entire schema-version-2 snapshot.
- `DurableSubmissionReceipt(journal_id, journal_version, submission_id,
  submission_sha256, phase, ledger_sha256)` must match exactly. The journal GUID
  stays constant and its positive integer version must advance for each hook in
  this invocation. Boolean, `None`, mismatched identity/phase/hash, nonadvancing
  versions or a changed journal refuse before setter entry.
- `owned_ledger_payload` and `owned_ledger_from_payload` provide the strict
  `schema_version: 2` snake-case JSON representation. Phases serialize as strings;
  old intents have empty submission identity/hash fields and a null phase. The
  caller applies `validate_owned_ledger(context, ledger)` for the exact bounded
  context/resource/grant validation; the codec and validator do not establish native
  ownership or admission.
- `desired_owned_union_sha256(context, permissions, *, service_account_uids)` is
  the pure canonical desired-plan digest used by reconciliation. A durable caller
  binds its accepted operation to this exact digest rather than a different app
  snapshot hash. Resource/grant order and duplicate permission entries do not change
  the digest; the same resource, custom-role, principal and size checks apply. Both
  helpers perform no ADC discovery, client construction or native transport.

Strict success adds `NativeIdentityResult.receipt`. Failures raise the sanitized
`NativeIdentityReconciliationError(reason, receipt)`; the receipt contains the
latest **acknowledged** ledger and per-resource steps. A callback exception may
follow a committed write, so the ledger is not a claim that the journal still has
those exact bytes; reread the matching journal generation under its own fence.

`PolicyStepReceipt` states are `UNSENT`, `SENT`, `UNKNOWN`, and `OBSERVED`.
`transport_invoked` reports whether this invocation entered the SDK setter; it
neither proves server delivery nor establishes that an older attempt stopped.
Failed reads before a submission have no setter steps. A lost/failed setter keeps
`UNKNOWN` and the pending `SENT` intent. If admission withdraws after acknowledged
`SENT` but before SDK entry, the receipt says `SENT` with false `transport_invoked`;
that conservative persisted state still blocks automatic resend. A lost SENT-hook
reply is `UNKNOWN`, even with no setter entry. No native or journal exception text
is put in the fixed reason, and receipts contain no tokens or raw policy bodies.

Every incoming `SENT` intent is checked before **any** new setter. A before-only
policy observation cannot clear uncertainty or permit another send. An exact
native after-hash can record observed requested policy without repeating that
setter; this does not prove exclusive authority, stop an old in-flight RPC, or
prevent a late older write. The durable caller must still bind current operation,
generation, complete union, stopped/resolved attempt and accepted authority.
Missing heartbeat/lease is not cancellation; no SENT takeover is provided here.
An `UNSENT` retry requires the unchanged original etag, union and before policy.
An independently appeared after-policy for an `UNSENT` intent refuses with
`UNSENT_POLICY_CONFLICT`; it cannot establish that this caller owns those grants.

Calls without a hook retain explicitly legacy behavior for legacy-only ledgers.
They receive no typed durability claim. Typed pending intents require the strict
hook, and ambiguous legacy pending intents cannot be promoted to proven UNSENT
by a strict caller. Existing deployed legacy integration is not silently migrated.

This provider foundation does not implement the PostgreSQL journal, reservation
mutex, Temporal/app orchestration, Kubernetes changes, actual impersonation or
rollout/inference acceptance. Endpoint permission scope remains the whole
Endpoint. The complete one-KSA/one-GSA mixed-app union, uncertain-send fencing,
legacy migration and full #2278 acceptance remain open.

## Generated client construction

The private client factory pairs each concrete Resource Manager, IAM Admin and
Vertex v1beta1 transport with its corresponding generated client. The generated
`_prep_wrapped_messages` methods accept `ClientInfo` and return `None`; that narrow
boundary is typed explicitly because the supported SDKs omit its annotations.
The factory retains fixed public hosts, bounded receive sizes, current admission
checkpoints and private channels without generated logging interceptors. A failed
constructor closes its own channel and every already constructed client.

Offline tests use actual generated serializers/clients for the declared minimum
and installed current SDKs, including constructor failure at each family and
existing lost-submission, checkpoint, acknowledgement and owned TLS preparation
controls. These checks establish local compatibility and cleanup, not native IAM
access, workload readiness or public pipeline activation.
