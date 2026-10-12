"""The install's agent secret stores, for the platform operator (#1861).

Read-only. Each organization's agent secrets live in the store of the
cluster agents dispatch onto (``resolve_agent_cluster``); this lists those
stores and, for one organization, every secret ref its agent environment
specs carry with whether the store holds a value. Presence comes from
``probe_ref_statuses``, the probe the per-spec secret status uses, over
``effective_secret_refs``, what dispatch resolves at spawn, so a ref this
reports healthy is one spawn can read. Values are never read back.

Setting or rotating a value stays with the organization's own agent secret
flow, under its permissions, action-safety rules and step-up: an
install-wide writer of every organization's secrets would step around them.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class SecretStore:
    organization_slug: str
    organization_name: str
    cluster_slug: str
    """Empty when the organization has no managed cluster to dispatch agents onto."""
    shared_cluster: bool
    spec_count: int


@dataclasses.dataclass(frozen=True)
class SecretRefStatus:
    spec_slug: str
    env_var: str
    uri: str
    exists: bool
    error: str


@dataclasses.dataclass(frozen=True)
class SecretInventory:
    store: SecretStore
    provider: str
    can_reveal: bool
    refs: list[SecretRefStatus]


def _agent_cluster(organization):
    from astrolift_agents.services.agent_cluster import NoAgentClusterError, resolve_agent_cluster

    try:
        return resolve_agent_cluster(organization)
    except NoAgentClusterError:
        return None


def _store(organization, spec_count: int) -> SecretStore:
    cluster = _agent_cluster(organization)
    return SecretStore(
        organization_slug=organization.slug,
        organization_name=organization.name,
        cluster_slug=cluster.slug if cluster is not None else "",
        shared_cluster=cluster is not None and cluster.organization_id is None,
        spec_count=spec_count,
    )


def list_stores() -> list[SecretStore]:
    """Every organization with the cluster its agent secrets resolve from."""
    from django.db.models import Count, Q

    from astrolift_identity.models import Organization

    orgs = Organization.objects.annotate(
        specs=Count("agent_environment_specs", filter=Q(agent_environment_specs__deleted_at__isnull=True))
    ).order_by("slug")
    return [_store(org, org.specs) for org in orgs]


def inventory(organization_slug: str) -> SecretInventory | None:
    """One organization's agent secret refs and whether its store holds each."""
    from astrolift_agents.models import AgentEnvironmentSpec
    from astrolift_dispatch.agent_secrets import (
        effective_secret_refs,
        probe_ref_statuses,
        unscoped_secret_refs,
    )
    from astrolift_identity.models import Organization

    organization = Organization.objects.filter(slug=organization_slug).first()
    if organization is None:
        return None
    specs = list(AgentEnvironmentSpec.objects.filter(organization=organization).order_by("slug"))
    cluster = _agent_cluster(organization)
    provider, can_reveal, refs = "unavailable", False, []
    for spec in specs:
        rows = probe_ref_statuses(
            cluster=cluster, refs=effective_secret_refs(spec), unscoped=unscoped_secret_refs(spec)
        )
        for row in rows:
            provider, can_reveal = row.get("provider") or provider, bool(row.get("can_reveal"))
            refs.append(
                SecretRefStatus(
                    spec_slug=spec.slug,
                    env_var=row["env_var"],
                    uri=row["uri"],
                    exists=bool(row["exists"]),
                    error=row.get("error") or "",
                )
            )
    return SecretInventory(
        store=_store(organization, len(specs)), provider=provider, can_reveal=can_reveal, refs=refs
    )
