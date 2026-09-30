# Agent secret owner namespaces

This maintenance release adds explicit preview and apply tools for moving typed
agent refs to their recorded owner. It preserves the existing runtime's
organization namespace checks, which accept both legacy and owner locations.
Owner enforcement for #2102 remains a separate release after migration. These
tools do not run automatically on startup or dispatch.

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
tools in this phase. Runtime reads, writes and provider-minted managed-service
binding validation retain their existing checks until the enforcement release.

## Upgrade legacy refs before serving the new release

Publish and deploy the maintenance image first, then use that exact backend image
in an operator maintenance task. Stop API, worker and
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
   Generate a fresh preview and require zero specs needing copies. The existing
   `audit_agent_secret_namespace --org <org>` additionally checks bundle and
   managed-service locations at their existing organization boundary; it is not
   an owner-enforcement audit in this maintenance release. Resume writers only
   with source manifests updated. Before publishing owner enforcement, rerun the
   complete preview and its stricter owner audit with writers paused; a stale
   repository resync can still restore a legacy ref under the compatible runtime.
   Bad bundle or managed-service findings use their respective migration procedures.

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
