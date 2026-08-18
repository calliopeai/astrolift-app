# Verification assets — tri-cloud certification campaign

Phase 1 of [spec 43](../../astrolift-spec/specs/43-tri-cloud-certification-campaign.md)
(epic #1471): the manifests and scanners that make the metered run short and its
result reviewable. Nothing here touches a cloud. Manifests are data; the
scanners are code with fakes.

Extends spec 41 (the lifecycle cycle) and spec 42 (the ten `astrolift-sample-*`
fixture repos) from one cloud to three.

## The campaign identity

Campaign slug: **`cert2026q3`**. Two handles find its residue, because no single
one reaches everything the campaign creates.

**The app name.** Every app here is `cert2026q3-<cell>`. Every managed-service
driver stamps the app slug on what it creates, and IAM surfaces — a GCP service
account, an Azure user-assigned identity, a role assignment — carry no usable
tag at all, so their deterministic name is the only handle there is. Putting the
campaign inside the app name puts it inside both.

**The `campaign` operator tag.** Declared by every manifest in
`[campaign.tags]`. It does not reach a resource yet; see the findings below.

`backend/providers/_cert/campaign.py` owns both, including the per-cloud tag
spellings. It reads *every* spelling the drivers write (four for the app key on
GCP, two on Azure) because a scanner that knows one of them reports clean for
whichever driver used another.

## Layout

```
manifests/
  happy-path/{aws,gcp,azure}.toml      the certification target: web + postgres
                                       + redis + queue + object_store
  per-kind/<kind>/{aws,gcp,azure}.toml 62 cells: 23 default-tier kinds × 3 clouds,
                                       minus 7 with no executable variant
  negative/<case>/{aws,gcp,azure}.toml 4 safety cases × 3 clouds
```

Every manifest names `calliopeai/astrolift-sample-api` — the managed-service
workhorse from spec 42 — pinned to `main-df5ffa3`, never `latest` (campaign
blocker B3). `/selftest` opens a real client against postgres, redis, the queue
and object storage; every other kind is verified by binding presence in `/debug`
plus a clean VERIFY-CLEAN.

The happy-path and per-kind manifests are **generated and committed** from the
ledger in `backend/providers/_cert/collection.py`, the same contract as
`docs/managed_service_coverage.md`: the ledger is the review surface (which
variant each cell provisions, and which cells have none), the tree is what you
diff, and a test fails when they disagree.

```
cd backend/providers && make verification-manifests
```

The negative manifests are hand-authored. Each one encodes a different refusal
and a table would hide the only part worth reading.

Per-cell assertions live inside each manifest (`[campaign]`, the binding
envelope in the header comment, `[campaign.negative]`) rather than in the
parallel `expected/` tree spec 43 sketches. One file per cell cannot drift
against itself.

## The negative cases

These are the point. A campaign that only proves the happy path certifies
nothing about safety, and these are the cells that exercise the fail-closed
ownership and grant work directly.

| Case | Must happen |
| --- | --- |
| `foreign-binding-collision` | Provision onto a name another binding owns: refused before any mutating call, existing resource byte-identical afterwards |
| `untagged-teardown` | Deprovision a resource whose identity tag is absent: refused, not deleted |
| `keyless-binding-no-role` | A keyless binding whose role assignment never lands: deploy fails loudly, nothing reports ready |
| `teardown-mid-provision` | Teardown while the resource is still creating: converges unattended, no orphan |

Each manifest carries its precondition, its assertions, and — where the driver
has no gate today — a `known_gap` saying so, so a red cell is a filed defect
rather than a surprise.

## Orphan scanners

`backend/providers/_cert/orphans/`, one module per cloud over a shared report.

| | Families queried |
| --- | --- |
| GCP | Cloud SQL, Memorystore, Pub/Sub topics, Pub/Sub subscriptions, GCS, IAM service accounts, project IAM bindings |
| Azure | Postgres Flexible, Redis, Service Bus namespaces, Service Bus entities, storage accounts, blob containers, managed identities, role assignments |

Both fail loud rather than returning a count: `ScanReport.raise_if_dirty()`
raises with every leftover itemized, and a resource family that could not be
*read* fails exactly like a leftover, because an unread family is an unknown
result, not a clean one.

Each cloud has a `Live*Inventory` adapter built from the same clients the
drivers use. Those adapters are the only part of this work that a live account
will exercise for the first time in Phase 2; the scan logic they feed is covered
offline with fakes (`backend/providers/tests/_cert/`).

```
cd backend/providers && make test
```

## Findings this work surfaced

Offline, from reading the code the campaign depends on. Each one changes what
Phase 3 will see.

1. **The campaign tag never reaches a resource.**
   `astrolift_workflows/activities/managed_service_lifecycle.py::_provision_sync`
   builds its `ProvisionSpec` without `tags=`, so a manifest's operator tags
   stop at the database. Until it is threaded through, the scanners find residue
   by app name only.
2. **Two GCP drivers drop `spec.tags` even when populated.**
   `gcp/managed/object_store_gcs.py` and `gcp/managed/queue_pubsub.py` build
   their label sets without the `astrolift-extra-*` pass every other GCP driver
   has. Fixing (1) alone would still leave buckets and topics untagged.
3. **The spend gate refuses 50 of the 62 per-kind cells.**
   `_cert/billing.py` classifies AWS *preview* variants only, so `postgres:rds`,
   `redis:elasticache`, `queue:sqs` and `object_store:s3` — the whole AWS happy
   path — raise `UnclassifiedVariant`, and GCP and Azure have no table at all.
   Classifying them is a decision, not a transcription, so it needs its own
   ticket before the switch-on gate.
4. **Ownership gates are Azure-deep and thin elsewhere.** 23 Azure managed
   drivers route through `_sdk/azure_ownership.py`; on AWS only EFS, FSx and the
   WebSocket API gateway have one, and `postgres_rds.py`, `object_store_s3.py`
   and `postgres_cloudsql.py` have none. The collision and untagged-teardown
   cells are predicted RED on AWS and GCP for that reason, recorded in each
   manifest's `known_gap`.
5. **An in-cluster variant cannot cover a cloud's gap from a manifest.** Driver
   lookup is `plugins.get(<cluster plugin>, "managed:<kind>:<variant>")` and
   neither the Azure nor the GCP plugin registers any `k8s_native` driver, so an
   AKS-hosted app cannot book `kube_prometheus_stack` or `argo_workflows`. Spec
   43's exit criterion allows "executable on all three clouds *or* an in-cluster
   variant"; three cells (`observability/azure`, `search/gcp`,
   `workflow_engine/azure`) can only satisfy it in principle.

## Not here

- **Topology grid** (statefulset, cronjob, task, function, agent). Spec 43 §3.1
  lists it; this pass covers happy path, per-kind and negative.
- **The AWS scanner.** Spec 43 describes it as existing. No orphan scanner is
  committed in this repo for any cloud, so the shared report shape in
  `orphans/model.py` is written for three clouds and AWS needs its families
  filled in.
- **Harness target selection and the coverage report** (spec 43 §3.2). The
  scanners are the half that had a defined contract; the runner does not exist
  in this repo either.
