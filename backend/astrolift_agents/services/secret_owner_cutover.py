"""Metadata-only readiness using the deployed maintenance owner validator."""

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_agents.services.agent_cluster import NoAgentClusterError, resolve_agent_cluster
from astrolift_agents.services.agent_package import normalize_secret_references
from astrolift_agents.services.secret_audit_reporting import metadata_digest
from astrolift_agents.services.secret_owner_migration import _destination
from astrolift_agents.services.secret_owner_namespaces import (
    agent_secret_prefix,
    assert_spec_scoped_secret_ref,
)
from astrolift_dispatch.agent_secrets import SecretRefNamespaceError


def _ref_status(uri, spec):
    try:
        assert_spec_scoped_secret_ref(uri, spec=spec)
    except SecretRefNamespaceError:
        try:
            _destination(uri, spec)
        except ValueError:
            return "foreign_or_invalid_ref"
        return "legacy_ref_requires_reviewed_migration"
    return "owner_scoped"


def cutover_readiness(organization):
    findings, inventory = [], []
    try:
        cluster = resolve_agent_cluster(organization)
    except NoAgentClusterError:
        cluster = None
        findings.append({"source": "cluster", "reason": "managed_cluster_unavailable"})
    specs = (
        AgentEnvironmentSpec.objects.filter(organization=organization, deleted_at__isnull=True)
        .select_related("organization", "project__team", "team")
        .prefetch_related("secret_binding_overrides")
        .order_by("guid")
    )
    counts = {"project": 0, "team": 0, "shared": 0}
    ref_count = removed_count = 0
    for spec in specs:
        holder = {"spec_guid": str(spec.guid), "spec_version": spec.version}
        source_metadata = {
            "config_repo": spec.config_repo,
            "config_branch": spec.config_branch,
            "config_manifest_path": spec.config_manifest_path,
        }
        locations = []
        try:
            normalize_secret_references(spec.secret_refs)
        except ValueError:
            findings.append(
                {
                    **holder,
                    "source": "manifest",
                    "reason": "malformed_refs",
                    "metadata_sha256": metadata_digest(spec.secret_refs),
                }
            )
        raw_refs = spec.secret_refs if isinstance(spec.secret_refs, list) else []
        for index, raw in enumerate(raw_refs):
            location = {
                "source": "manifest",
                "manifest_index": index,
                "ref_sha256": metadata_digest(raw.get("uri") if isinstance(raw, dict) else raw),
            }
            try:
                normalized = normalize_secret_references([raw])[0]
            except ValueError:
                locations.append({**location, "reason": "malformed_ref"})
                continue
            locations.append({**location, "reason": _ref_status(normalized["uri"], spec)})
        for row in sorted(spec.secret_binding_overrides.all(), key=lambda row: str(row.guid)):
            if row.deleted_at is not None:
                continue
            removed_count += bool(row.removed)
            location = {
                "source": "removed_override" if row.removed else "override",
                "override_guid": str(row.guid),
                "override_version": row.version,
                "ref_sha256": metadata_digest(row.uri),
            }
            reason = (
                _ref_status(row.uri, spec)
                if row.uri
                else ("empty_tombstone" if row.removed else "empty_override")
            )
            locations.append({**location, "reason": reason})
        ref_count += len(locations)
        try:
            agent_secret_prefix(spec)
        except SecretRefNamespaceError:
            owner = "invalid"
            findings.append({**holder, "source": "owner", "reason": "stale_or_inconsistent_owner"})
        else:
            owner = "project" if spec.project_id else "team" if spec.team_id else "shared"
            counts[owner] += 1
        findings.extend(
            {**holder, **location}
            for location in locations
            if location["reason"] not in {"owner_scoped", "empty_tombstone"}
        )
        inventory.append(
            {
                **holder,
                "owner_kind": owner,
                "source_metadata_sha256": metadata_digest(source_metadata),
                "source_configured": bool(spec.config_repo),
                "source_revision_verified": False,
                "locations": locations,
            }
        )
    return {
        "schema": "astrolift.agent-secret-owner-cutover/v1",
        "organization_guid": str(organization.guid),
        "organization_version": organization.version,
        "cluster_guid": str(cluster.guid) if cluster else None,
        "cluster_version": cluster.version if cluster else None,
        "live_specs": len(inventory),
        "owner_counts": counts,
        "typed_ref_locations": ref_count,
        "removed_overrides": removed_count,
        "metadata_ready": not findings,
        "findings": findings,
        "inventory": inventory,
        "secret_store_contacted": False,
        "runtime_owner_enforcement": False,
        "operator_prerequisites": [
            "review source repositories and imported packages",
            "pause API, worker, metadata, repository resync and external secret writers",
            "apply the exact reviewed plan as an active platform operator",
            "rerun strict readiness and namespace audit before coordinated web/worker owner enforcement",
        ],
    }
