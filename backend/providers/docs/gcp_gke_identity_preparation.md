# Internal durable GKE subject preparation

`GKEIdentityPreparation` is a private provider port for preparing original
GUID-owned namespace/ServiceAccount subjects and subsequently annotating them with
the already verified GSA. It performs no IAM, model allocation, Kubernetes workload
rendering, deletion, rollout or inference. The unchanged `GKEIdentityObserver`
provides fixed Container/ResourceManager RPCs, admitted ADC, original native
`Cluster.id`, project/location/workload-pool and complete Standard node or Autopilot
configuration checks. Kubernetes requests use that fresh native endpoint and CA,
including verified RFC1918 endpoints, with finite deadlines/response bounds,
no ambient proxy, redirects, executable ADC or credential/body logs.

## Caller contract

`prepare(operation_id, ledger, commit_submission, commit_observation, checkpoint)`
creates missing exact namespace/ServiceAccount names. `annotate` takes the same
parameters, a mandatory `VerifiedIAMConfigurationReceipt`, and explicit
`expected_desired_union_sha256` from the accepted current plan. Every checkpoint
must return exactly `None` or raise a fixed refusal, before ADC, each operation,
and after native responses. The caller is responsible for current actor/token/
session/target/complete-union admission and generation fencing.

- `preparation_target_sha256(context)` binds the identity fingerprint, exact
  cluster resource/incarnation and ordered original namespace/KSA UID tuple.
- `GKEPreparationLedger(target_sha256, objects, pending)` records metadata only.
  `PreparedObject` contains kind, exact path, original observed UID, opaque bounded
  resourceVersion and creation operation UUID. Existing objects require originally
  pinned UIDs; namespace UID declarations must agree across subjects.
- `PreparationIntent` binds stable submission UUID, original accepted operation
  UUID, kind/path/method, canonical pre-marker request SHA, full actual wire request SHA, original UID/resourceVersion for
  patch and `UNSENT`/`SENT` phase. For create, the request hash excludes its own
  `astrolift.io/preparation-request` annotation to avoid a circular digest; all
  remaining canonical JSON request bytes, including accepted operation marker and
  GUID ownership labels, are hashed. `request_sha256` is this pre-marker digest;
  `wire_request_sha256` separately hashes the full actual canonical wire JSON with
  the marker included. PATCH uses the same full-patch SHA in both fields.
- `PreparationSubmission(target_sha256, intent, ledger)` is sent to the trusted
  `commit_submission` hook first for `UNSENT`, then for `SENT` before HTTP entry.
  The caller must commit each snapshot in an independent short transaction before
  returning; typed Python values and booleans are not database durability proof.
- `ObjectObservation(target_sha256, operation_id, object, ledger)` is sent to
  `commit_observation` with pending cleared only after verified UID/readback. Its
  committed receipt is required before the observed UID can be returned for IAM
  consumption. Losing its acknowledgement leaves the last acknowledged snapshot
  uncertain; reread the exact journal under its current fence.
- Both hooks return exact `PreparationCommitReceipt(journal_id, journal_version,
  operation_id, target_sha256, ledger_sha256, record_sha256, phase)`. The positive
  integer version advances and the journal GUID remains unchanged within each
  invocation. Phase is `UNSENT`, `SENT` or `OBSERVED`; boolean/mismatched receipts
  refuse before another effect. The caller must validate the same original
  operation/target and ledger transitions durably, not merely echo the hashes.

Initial blank UIDs cannot adopt existing generic objects, even with identical GUID
labels. A lost create can recover only through its original durable `SENT` intent,
accepted operation/request markers, exact GUID owner metadata, current native
cluster and UID observation followed by committed observation. An `UNSENT`
coincidence cannot establish creation ownership. Markers are correlation metadata,
not cryptographic ownership or permission proof. Privileged native object writers
must remain trusted; current labels alone never permit adopting a replacement
once an original UID is pinned.

All incoming `SENT` objects are resolved in a bounded preflight before any new
submission. Absent or ambiguous `SENT` objects refuse without resending. Missing heartbeat or
lease does not cancel an older HTTP request. `transport_invoked` reports only this
invocation's adapter entry, not native delivery or termination of older attempts.
The caller must prevent concurrent generations and resolve older in-flight
attempts before advancing. No blind retries, lease takeover or cleanup are provided.

## Annotation boundary

A trusted durable caller supplies `VerifiedIAMConfigurationReceipt` only from a
committed successful full-union IAM result with no unresolved obligations and fresh
admission. It binds journal GUID/version, accepted operation UUID, identity SHA,
full desired-union SHA and exact original KSA UID set, with `completed=True`.
The port requires equality with the independently supplied expected accepted-union
SHA before native discovery or annotation; another well-formed SHA is insufficient.
Constructing this dataclass from an unverified callback or arbitrary reference is
not verification. The caller checkpoint must continue to verify the receipt's
current generation and accepted IAM/authority state throughout annotation.

The port refuses foreign GSA links. It patches only the original KSA UID/current
opaque resourceVersion, with JSON Patch `test` operations for UID, resourceVersion
and the existing annotations map before adding the single GSA annotation. Foreign
labels and annotations are preserved. If annotations are absent, the guarded
resourceVersion protects creating the map. Native Kubernetes optimistic concurrency
protects that object, not a transaction across namespace/KSA, GCP IAM and cloud
metadata. Final reads check original UIDs/resourceVersions and the requested link;
they are sequential observations, not an atomic snapshot or rollout proof.

`GKEPreparationReceipt.configuration_observed` means the subject preparation/link
configuration passed these checks. `workload_ready` and `impersonation_verified`
remain false. Errors contain fixed reasons plus the last acknowledged ledger and
per-subject `UNSENT`/`SENT`/`UNKNOWN`/`OBSERVED` steps. No UID is successfully returned
from a create until its observation hook is acknowledged.

The tests use actual Container/ResourceManager GAPIC serializers and an owned TLS
Kubernetes HTTP server; the in-memory hook fixture is not PostgreSQL durability.
Public workflow/API integration, accepted session authority, storage, one-KSA/one-GSA
mixed-app union, workload rendering, cleanup and full #2278 acceptance remain open.

Primary protocol references: [Kubernetes API concurrency and JSON Patch](https://kubernetes.io/docs/reference/using-api/api-concepts/),
[GKE IAM/KSA linking](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/workload-identity).
