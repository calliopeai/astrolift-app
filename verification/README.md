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
  per-kind/<kind>/{aws,gcp,azure}.toml 69 cells: 23 default-tier kinds × 3 clouds,
                                       with none left out. Five
                                       book an in-cluster variant, because that
                                       is what a portable app gets on a cloud
                                       that sells no managed equivalent.
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
| AWS | RDS instances, RDS clusters, ElastiCache clusters, ElastiCache replication groups, ElastiCache serverless caches, SQS, SNS, S3, IAM roles, IAM customer-managed policies |
| GCP | Cloud SQL, Memorystore, Pub/Sub topics, Pub/Sub subscriptions, GCS, IAM service accounts, project IAM bindings |
| Azure | Postgres Flexible, Redis, Service Bus namespaces, Service Bus entities, storage accounts, blob containers, managed identities, role assignments |

A family is split wherever one API call cannot see the whole of it. On AWS that
is three ElastiCache APIs for three drivers, and RDS instances separately from
RDS clusters because `DescribeDBInstances` never returns a cluster — an Aurora,
DocumentDB or Neptune cluster with no members left is invisible to an
instance-only scan and still bills for storage and backups.

All three fail loud rather than returning a count: `ScanReport.raise_if_dirty()`
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

## The lifecycle runner

`backend/providers/_cert/harness/`. Given a manifest cell it executes the spec
43 §0.1 cycle, asserts every step, and emits the grid.

```
BUILDOUT      no app of this name may already exist · register · every declared
              managed service reaches ACTIVE · first deploy succeeds
VERIFY-UP     declared workloads have ready replicas · the health endpoint
              answers 2xx over verified TLS · every binding is present in
              /debug · /selftest connects to each service for real
UPDATE        redeploy the second pinned image · rollback lands on the first
              image · roll forward again
VERIFY-UPD    the live revision is the update · nothing is still running the
              old image · health and /selftest still pass
TEARDOWN      deregister · the app stops being served · no binding survives it
VERIFY-CLEAN  the cloud carries nothing owned by the campaign
REPRODUCE     the whole thing again, unattended, with an end-state comparison
```

```
cd backend/providers
python -m _cert.harness --cell happy-path/aws --project-id <uuid> \
    --update-image docker.io/calliopeai/astrolift-sample-api:main-<sha7> \
    --region us-west-2 --i-have-credentials
```

Three properties are deliberate and each is pinned by a test.

**It drives the platform, never the database or a cloud SDK.** Every step goes
through the CLI and the GraphQL API, which is the dogfooding spec 41 intended
and the only way a green cell says anything about what a customer hits.
VERIFY-CLEAN is the single exception: "nothing was left behind" is a claim about
the cloud, so it is the orphan scanner's answer, injected as a handle so the
runner imports no cloud SDK.

**Teardown runs even after the cycle has already failed.** Steps 1–4
short-circuit on the first red; steps 5–6 always run. A cell that fails at
BUILDOUT has usually created something first, and abandoning it on a metered
account costs more than the defect that stranded it. When there was genuinely
nothing to tear down, TEARDOWN reports *skipped* rather than green — the grid is
the certification evidence, and a green step the run never exercised is the grid
claiming coverage it does not have. VERIFY-CLEAN still runs in that case, because
a register that failed halfway can have created cloud resources the platform
never recorded a row for.

**A failure is a sentence.** Each red carries the cell, the step and the
assertion that did not hold, then the observed value underneath. No tracebacks:
those are for the harness's own bugs, and they are reported with the step named
too.

### REPRODUCE, and what "end state identical" compares

Step 7 has never run for any cell on any cloud. Written as a loop counter it
never would mean anything either: two green passes prove the cycle is
*repeatable*, and the defect the step exists for — non-idempotent teardown,
already found by hand on AWS — hides between repeatable and *idempotent*. So the
comparison is the deliverable, not the second run.

Two fingerprints are taken per cycle, at the high-water mark after VERIFY-UP and
at the end after VERIFY-CLEAN, and both are compared:

- workloads by name and ready replica count
- managed services by binding name, kind, variant and status
- **every campaign-owned cloud resource at the high-water mark**, taken with the
  same orphan scanner. At VERIFY-CLEAN its output is a defect list; at VERIFY-UP
  the identical call is a census. This is the only place the harness can see
  cloud-side resource *names* — `AstroliftManagedService` exposes name, kind,
  variant and status but not `ManagedService.backend_ref`, the provider-side
  handle — and names are exactly where a released-but-not-freed resource shows
  up: run 2 books `…-records-2` because run 1's teardown never released the name
- after teardown: whether the app is still served, and what survived (empty in a
  passing run)
- after teardown: **which families the scan actually read**. Two clean scans that
  read different families are not the same result; one of them was partly blind

Excluded on purpose, because two correct runs differ in each by construction and
comparing them would make REPRODUCE permanently red: ids and timestamps,
endpoints and hostnames and ARN suffixes (an RDS endpoint carries a token minted
per creation; the identifier it derives from does not, and that is what the
census reports), and pod names. The exclusion is a projection over named fields
rather than a filter, so a field added to an observation later stays out of the
comparison until somebody puts it in deliberately.

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
3. **The spend gate refuses most of the per-kind cells.**
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
5. ~~**An in-cluster variant cannot cover a cloud's gap from a manifest.**~~
   Fixed in astrolift-app#1484. Driver lookup was
   `plugins.get(<cluster plugin>, "managed:<kind>:<variant>")` and no cloud
   plugin registers a `k8s_native` driver, so an AKS-hosted app could not book
   `kube_prometheus_stack` or `argo_workflows` and spec 43's exit criterion
   ("executable on all three clouds *or* an in-cluster variant") held only in
   principle. Resolution now falls back to `k8s_native`
   (`astrolift_drivers/managed_resolution.py`), and the five cells that were
   excused for this reason — `cache/{gcp,azure}`, `observability/azure`,
   `search/gcp`, `workflow_engine/azure` — are generated manifests like any
   other. They are metered like any other too, which is what makes the
   portability claim evidence rather than an argument.

Building the runner surfaced four more, all of them things the switch-on gate
has to clear.

6. **There is no route for an operator tag from a manifest into a resource.**
   Finding 1 is one link of it; the other is that `ProvisionManagedServiceInput`
   has no `tags` field at all, so `[campaign.tags]` cannot reach the platform
   from any surface, CLI or API. The scanners find campaign residue by app name
   only until both are fixed.
7. **UPDATE has no second image to roll to.** `calliopeai/astrolift-sample-api`
   has two commits and the first one's build workflow was the thing the second
   commit fixed, so `main-df5ffa3` is the only published tag. The runner refuses
   rather than defaulting — campaign blocker B3 was a deploy that failed on a
   moved tag, and a default that is not in the registry would report a fixture
   problem as a platform defect. Publishing a second fixture tag is a Phase 2
   prerequisite.
8. **`astro app register` cannot read these manifests.** The CLI parses an
   `[app] slug` / `display_name` table; the campaign manifests use the backend
   parser's top-level `name`. That is campaign blocker B1 (manifest schema
   unification) still open, and it is why the runner registers through
   `registerApp` rather than the CLI.
9. **No CLI command provisions an app-scoped managed service.**
   `astro project resources add` covers project-scoped ones only, so the runner
   calls `provisionManagedService` directly. The full surface-by-operation
   ledger is in `_cert/harness/platform.py`; every GraphQL row in it is a CLI
   gap.

## Not here

- **Topology grid** (statefulset, cronjob, task, function, agent). Spec 43 §3.1
  lists it; this pass covers happy path, per-kind and negative.
- **A live run.** Every `Live*Inventory` adapter and `CliPlatformClient` is
  written against the clients and commands the platform already uses and has
  never spoken to a control plane or a cloud. Phase 2 is their first real run,
  by design.
- **The negative cells, through the runner.** Each one needs a precondition
  created out of band and expects a refusal, so a cycle would report the thing
  it is testing for as a BUILDOUT failure. `Cell.load` refuses them by name;
  they are run by hand against the assertions in each manifest.
