# Agent secret owner namespaces

The compatible maintenance tools preview and apply reviewed typed-secret owner
migrations. This readiness leaf additionally inventories every live spec's
manifest refs, binding overrides and removed tombstones without opening a secret
store. Both preserve runtime organization namespace checks, which accept legacy
and owner locations. They do not enable owner enforcement or run automatically
on startup or dispatch.

| Release phase | Behavior |
|---|---|
| Compatible maintenance | Explicit reviewed migration command; existing runtime continues to accept org-scoped refs. |
| Compatible readiness (this leaf) | Metadata inventory, optional strict readiness failure, and sanitized namespace-audit output; shared organization-owned model services included. |
| Held future owner enforcement | Coordinated web/worker runtime change, only after actual operator migration, source review and clean strict readiness/audit. |

Readiness is deploy-compatible tooling, **not completion of #2102**. The held
future enforcement commits must not be deployed with this staging leaf. Until
cutover, the runtime retains the issue's same-organization sibling-secret risk.

The migration's destination prefixes are independent of selected team/project
headers:

| Spec owner | Prefix |
|---|---|
| Project | `agents/<org guid>/projects/<project guid>/` |
| Team, without a project | `agents/<org guid>/teams/<team guid>/` |
| Neither, explicitly org-shared | `agents/<org guid>/shared/` |

A project owner takes precedence over a team owner; both must have consistent,
live ancestry in the spec's organization. Stale or foreign ancestry is refused.
`secret://`, provider `sm:`/`ssm:`, the install `astrolift/` root and field selectors
retain their existing meanings, checked against the canonical store location.
The migration refuses refs naming sibling owners, reusable secret-bundle payloads
or managed-service credentials. Its owner validator is confined to maintenance
tools in this phase. Runtime reads, writes, selector mutation behavior and
provider-minted managed-service binding validation retain their existing checks
until the enforcement release.

## Inventory readiness without contacting a store

```sh
python manage.py audit_agent_secret_owner_cutover --org <org>
python manage.py audit_agent_secret_owner_cutover --org <org> --require-ready
python manage.py audit_agent_secret_namespace --org <org>
```

The readiness JSON includes current organization/cluster/spec/override GUIDs and
row versions, owner counts, **every stored manifest ref including shadowed refs**,
active overrides and removed tombstones. Deleted specs and deleted override rows
are excluded. An empty removed tombstone needs no migration; its row is still
inventoried. A missing managed cluster fails readiness without hiding the specs.
Stale ancestry, malformed metadata, legacy refs and foreign refs fail readiness.
`--require-ready` prints the inventory and exits with an error when findings exist.

Raw refs and repository settings never enter this report. It identifies locations
with spec/override GUIDs or a manifest index, static reason codes and SHA256
digests. The source metadata hash covers the **current stored repository, branch
and manifest path**; the spec row version identifies the current metadata snapshot.
It is not a fetched commit SHA. `source_revision_verified=false` explicitly
requires repository/package review; this command does not fetch source or certify
payload existence, ownership assignment or a migration's completion. Treat hashes
as correlation metadata, not as encryption for low-entropy input.

The existing namespace audit remains an **organization-compatible** runtime
audit in this phase; its invalid-ref output now uses record GUIDs, hashes and a
static reason instead of echoing raw locations. It also checks explicitly
organization-owned shared-model services, alongside app/project services. Use the
new readiness command for typed owner-boundary findings; a zero namespace-audit
count alone does not establish owner readiness.

## Upgrade legacy refs before serving the new release

Publish and deploy the maintenance image first, then use that exact backend image
in an operator maintenance task. Stop API, worker, metadata, repository-resync and
external secret writers while applying; the portable provider API does not offer
compare-and-swap. This is an explicit operator operation, not a Django migration
or automatic startup task. No schema migration is added by this change.

1. Preview against the actual organization and cluster:

   ```sh
   python manage.py migrate_agent_secret_owners --org <org> --plan-file /private/agent-secret-plan.json
   ```

   Preview reads control-plane metadata only. The new file has mode `0600`, is
   never overwritten, and contains refs/owners, never secret values. Record the
   printed SHA256. Review every ownership assignment, especially the printed
   count and plan list of legacy locations referenced by multiple owners. Each
   affected owner receives a separate copy; possession of an old ref alone is
   insufficient evidence that the owner should receive its payload. Correct
   metadata before previewing again when a legacy assignment is inappropriate.

2. Pause all writers, then apply the exact reviewed file as an active platform
   operator, identified by the Django user ID:

   ```sh
   python manage.py migrate_agent_secret_owners --org <org> --plan-file /private/agent-secret-plan.json --apply --confirm-plan <sha256> --operator <user-id> --writers-paused
   ```

   Apply rechecks organization, cluster version, spec ownership and refs. It
   refuses foreign refs, refs already naming another owner, normalized destination
   collisions, missing/unsupported sources, and nonidentical destination payloads.
   It copies the **entire** source dictionary, verifies the copy, then changes a
   spec's manifest refs and live binding overrides atomically in PostgreSQL. A
   removed override stays removed and its location is rewritten without copying
   its payload. Each completed spec records a metadata-only audit event.

3. If the process stops, rerun the same reviewed apply command. Identical existing
   copies are accepted; completed specs are recognized without repeating store
   writes or audit events. Changed owners/refs/cluster need a new reviewed preview.
   Completed specs remain committed if a later spec fails. No source is deleted,
   including after successful migration; retire old locations only through a
   separate reviewed retention operation.

4. Update source repository manifests and imported packages to the reviewed owner
   locations; a later registration/resync cannot restore an old org-only ref.
   Generate a fresh preview and require zero specs needing copies. Run
   `audit_agent_secret_owner_cutover --org <org> --require-ready` and require a clean
   inventory for every live spec, override and tombstone. The existing
   `audit_agent_secret_namespace --org <org>` additionally checks bundle and
   managed-service locations at their existing organization boundary; it is not
   an owner-enforcement audit in this maintenance release. Resume writers only
   with source manifests updated. Before publishing owner enforcement, rerun the
   complete preview, strict readiness and namespace audit with writers paused; a stale
   repository resync can still restore a legacy ref under the compatible runtime.
   Bad bundle or managed-service findings use their respective migration procedures.

5. Only after the actual reviewed plan has been applied, current source repositories
   and imported packages have been reviewed/updated, and both metadata audits are
   clean, authorize the held owner-enforcement image for coordinated web/worker
   rollout. A local test proof or `metadata_ready=true` does not authorize this
   production cutover. No production migration or cutover was performed by this leaf.

## Rollback boundary

The readiness leaf has no runtime enforcement, schema migration, store mutation or
startup task; removing it changes reporting availability only. A migration is a
separate authorized operation. After migration, keep new owner refs intact when
rolling software back to a compatible maintenance image: its organization checks
accept those locations. Do not reverse metadata or point back to retained sources;
old locations are retained, not synchronized after later rotations. The portable
store API provides no atomic reverse migration or automatic credential revocation.
Rolling a future enforcement image back to compatibility restores the sibling-secret
exposure and is a recovery action, not an enforced #2102 fix.

## Local verification of this readiness leaf

Real PostgreSQL checks: readiness/audit/reviewed-migration selection **47 passed**;
existing dispatch namespace and secret-value mutation selection **132 passed**.
The readiness selection invokes the actual guarded `manage.py` entrypoint in
separate processes against the owned test database: clean strict readiness exits
`0`, legacy metadata refusal exits `1`, and both prohibit backend construction.
It also verifies unchanged org-compatible legacy/sibling dispatch, hidden refs,
tombstone IDs/versions, opaque malformed refs and shared-model audit inclusion.
All six changed backend Python files pass Ruff format/check.

Only `test_astrolift_secret_readiness_2102` was created/removed. No production
metadata, secret stores, remote source repositories, Kubernetes or cloud resources
were read or changed. This is local tooling/compatibility evidence; the full
backend/provider suites and production migration/cutover were not run.

## Conflict preflight, 2026-09-30 UTC

An authenticated read returned 28 visible environment specs, below the legacy
100-row cap: 22 project-owned and 6 organization-shared. Of these, 24 specs contain
54 typed refs. No payload was resolved or printed. This is not a complete operator
plan: hidden/deleted rows, binding overrides, other organizations and source
repository manifests need the maintenance tool's full review. No migration ran.

Migration command output/errors and `agents.secret.owner_migrate` audit records
do not echo payloads. Existing provider telemetry may retain raw store exceptions
in logs and provider audit records; handle access to those records accordingly.
Keep the private review file outside source control.
