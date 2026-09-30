"""Reviewed, resumable migration of legacy typed agent refs and store payloads.

No values enter plans, migration audit events or outward command errors.
Existing provider telemetry may retain raw store exceptions. Sources are never deleted.
The portable store has no compare-and-swap; all secret writers must be paused
for apply, including out-of-band writers. A plan is not an authorization grant.
"""

from __future__ import annotations

import hashlib
import json

from django.db import connection, transaction

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.services.agent_cluster import resolve_agent_cluster
from astrolift_agents.services.agent_package import normalize_secret_references
from astrolift_agents.services.secret_owner_namespaces import (
    agent_secret_prefix,
    assert_spec_scoped_secret_ref,
)
from astrolift_dispatch.agent_secrets import (
    AGENT_SECRET_ROOTS,
    SecretRefNamespaceError,
    bundle_location_key,
    canonical_secret_ref,
    in_org_secret_namespace,
    resolve_secrets_backend,
)
from core.permissions import is_platform_operator

SCHEMA = "astrolift.agent-secret-owner-plan/v1"


class SecretOwnerMigrationError(ValueError):
    pass


def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _specs(organization):
    return AgentEnvironmentSpec.objects.filter(
        organization=organization, deleted_at__isnull=True
    ).select_related("organization", "project__team", "team")


def _snapshot(spec, *, lock=False):
    try:
        refs = normalize_secret_references(spec.secret_refs)
    except ValueError:
        raise SecretOwnerMigrationError("spec has malformed refs; repair metadata before migration") from None
    overrides = spec.secret_binding_overrides.filter(deleted_at__isnull=True).order_by("guid")
    if lock:
        overrides = overrides.select_for_update()
    return {
        "refs": refs,
        "overrides": [
            {"guid": str(row.guid), "env_var": row.env_var, "uri": row.uri, "removed": row.removed}
            for row in overrides
        ],
    }


def _destination(uri, spec):
    canonical = canonical_secret_ref(uri)
    try:
        assert_spec_scoped_secret_ref(canonical, spec=spec)
    except SecretRefNamespaceError:
        pass
    else:
        return uri
    prefix = agent_secret_prefix(spec)
    if not in_org_secret_namespace(canonical, organization=spec.organization, roots=AGENT_SECRET_ROOTS):
        raise SecretOwnerMigrationError("ref is not a legacy location in this organization")
    path, separator, field = canonical.partition("#")
    scheme = ""
    for candidate in ("sm:", "ssm:"):
        if path.startswith(candidate):
            scheme, path = candidate, path[len(candidate) :]
            break
    path = path.lstrip("/").removeprefix("astrolift/")
    suffix = path.removeprefix(f"agents/{spec.organization.guid}/")
    source_key = bundle_location_key(canonical)
    reserved = (
        bundle_location_key(f"agents/{spec.organization.guid}/{kind}/") + "-"
        for kind in ("shared", "teams", "projects")
    )
    if suffix.split("/", 1)[0].lower() in {"shared", "teams", "projects"} or any(
        source_key.startswith(key) for key in reserved
    ):
        raise SecretOwnerMigrationError("ref names a different owner namespace; migration cannot adopt it")
    destination = f"{scheme}{prefix}{suffix}" + (f"#{field}" if separator else "")
    assert_spec_scoped_secret_ref(destination, spec=spec)
    return destination


def _entry(spec, snapshot):
    after = {"refs": [], "overrides": []}
    copies = set()
    for field in ("refs", "overrides"):
        for row in snapshot[field]:
            migrated = dict(row)
            # Tombstones are metadata only. They remain removed, but a legacy
            # URI is rewritten so restoring it cannot reintroduce an old ref.
            if row["uri"]:
                migrated["uri"] = _destination(row["uri"], spec)
                if migrated["uri"] != row["uri"] and not row.get("removed", False):
                    copies.add(
                        (
                            canonical_secret_ref(row["uri"]).partition("#")[0],
                            migrated["uri"].partition("#")[0],
                        )
                    )
            after[field].append(migrated)
    return {
        "spec_guid": str(spec.guid),
        "owner_prefix": agent_secret_prefix(spec),
        "before": snapshot,
        "after": after,
        "copies": [{"source": source, "destination": target} for source, target in sorted(copies)],
    }


def _validate_destinations(entries):
    destinations = {}
    for entry in entries:
        for copy in entry["copies"]:
            key = bundle_location_key(copy["destination"])
            source = bundle_location_key(copy["source"])
            if key in destinations and destinations[key] != source:
                raise SecretOwnerMigrationError(
                    "migration destinations collide after provider name normalization"
                )
            destinations[key] = source


def build_plan(organization):
    """Read metadata only; resolving the cluster does not open its store."""
    cluster = resolve_agent_cluster(organization)
    entries = []
    for spec in _specs(organization).order_by("guid"):
        entry = _entry(spec, _snapshot(spec))
        if entry["before"] != entry["after"]:
            entries.append(entry)
    _validate_destinations(entries)
    source_owners = {}
    for entry in entries:
        for copy in entry["copies"]:
            source_owners.setdefault(bundle_location_key(copy["source"]), set()).add(entry["owner_prefix"])
    return {
        "schema": SCHEMA,
        "organization_guid": str(organization.guid),
        "cluster_guid": str(cluster.guid),
        "cluster_version": cluster.version,
        "specs": entries,
        "sources_used_by_multiple_owners": sorted(
            key for key, owners in source_owners.items() if len(owners) > 1
        ),
    }


def apply_plan(organization, plan, *, operator, writers_paused):
    """Copy + verify each spec's full payloads, then atomically switch its refs.

    A failure leaves that spec's refs unchanged. A retry accepts already copied
    payloads only when identical, and recognizes a completed metadata snapshot.
    Completed specs stay committed when a later spec fails.
    """
    if not writers_paused or not is_platform_operator(operator):
        raise SecretOwnerMigrationError("apply requires paused writers and an active platform operator")
    cluster = resolve_agent_cluster(organization)
    if (
        plan.get("schema") != SCHEMA
        or plan.get("organization_guid") != str(organization.guid)
        or plan.get("cluster_guid") != str(cluster.guid)
        or plan.get("cluster_version") != cluster.version
        or not isinstance(plan.get("specs"), list)
    ):
        raise SecretOwnerMigrationError("plan organization, cluster or format has changed; preview again")
    _validate_destinations(plan["specs"])
    seen = set()
    # Serialize preflight and each spec write per organization. External
    # store writers still require the operator's maintenance window.
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [2102, organization.pk])
        for entry in plan["specs"]:
            guid = entry["spec_guid"]
            if guid in seen:
                raise SecretOwnerMigrationError("plan contains duplicate specs")
            seen.add(guid)
            spec = _specs(organization).select_for_update(of=("self",)).filter(guid=guid).first()
            if spec is None or _entry(spec, entry["before"]) != entry:
                raise SecretOwnerMigrationError("plan owner or reference mapping is invalid; preview again")
            current = _snapshot(spec, lock=True)
            if current not in (entry["before"], entry["after"]):
                raise SecretOwnerMigrationError("spec or bindings changed since preview; preview again")
    # Persist one complete spec at a time so apply resumes after a process crash.
    completed = 0
    for entry in plan["specs"]:
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [2102, organization.pk])
            spec = _specs(organization).select_for_update(of=("self",)).get(guid=entry["spec_guid"])
            current_cluster = resolve_agent_cluster(organization)
            if current_cluster.guid != cluster.guid or current_cluster.version != cluster.version:
                raise SecretOwnerMigrationError("cluster changed during apply; preview again")
            if _entry(spec, entry["before"]) != entry:
                raise SecretOwnerMigrationError("spec owner changed during apply; preview again")
            current = _snapshot(spec, lock=True)
            if current == entry["after"]:
                completed += 1
                continue
            if current != entry["before"]:
                raise SecretOwnerMigrationError("spec or bindings changed during apply; preview again")
            try:
                backend = resolve_secrets_backend(cluster)
                for copy in entry["copies"]:
                    payload = backend.get(copy["source"])
                    if not isinstance(payload, dict) or not all(
                        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
                    ):
                        raise SecretOwnerMigrationError("source payload is missing or unsupported")
                    target = backend.get(copy["destination"])
                    if target is not None and target != payload:
                        raise SecretOwnerMigrationError("destination already contains a different payload")
                    if target is None:
                        backend.upsert(copy["destination"], dict(payload))
                    if backend.get(copy["destination"]) != payload:
                        raise SecretOwnerMigrationError("destination copy could not be verified")
            except SecretOwnerMigrationError:
                raise
            except Exception:
                raise SecretOwnerMigrationError(
                    "secret store operation failed; inspect provider audit logs"
                ) from None
            spec.secret_refs = entry["after"]["refs"]
            spec.updated_by = operator
            spec.save(update_fields=["secret_refs", "updated_at", "updated_by", "version"])
            for row in entry["after"]["overrides"]:
                override = spec.secret_binding_overrides.get(guid=row["guid"], deleted_at__isnull=True)
                if override.uri != row["uri"]:
                    override.uri = row["uri"]
                    override.updated_by = operator
                    override.save(update_fields=["uri", "updated_at", "updated_by", "version"])
            from astrolift_operations.models import AuditEvent

            AuditEvent.objects.create(
                organization=organization,
                actor_kind="user",
                actor_id=str(operator.pk),
                action="agents.secret.owner_migrate",
                decision="ALLOW",
                target_kind="agent_environment_spec",
                target_id=str(spec.guid),
                data={"plan_sha256": plan_digest(plan), "copied_locations": len(entry["copies"])},
            )
            completed += 1
    return completed
