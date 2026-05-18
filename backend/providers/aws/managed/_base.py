"""Shared helpers for AWS managed-service drivers.

Each driver follows the same pattern:
- handle = ``<kind>/<aws-resource-id>`` (parsed in/out via
  handle_for / parse_handle)
- platform tags applied so the operator can filter in AWS console
  + apply tag-based budgets
"""

from __future__ import annotations

from _sdk.managed_service import ProvisionSpec


class ManagedServiceError(Exception):
    """Base for managed-service driver errors. Distinct from
    ProviderError used by infra drivers because the workflow
    layer's failure-handling logic differs (managed-service
    failures often need partial cleanup of half-created
    resources)."""


def handle_for(*, kind: str, resource_id: str) -> str:
    """Canonical service handle. Stored on the
    ManagedServiceBinding row + parsed on every subsequent op."""
    if not kind or not resource_id:
        raise ManagedServiceError(
            "handle_for requires both kind and resource_id",
        )
    if "/" in kind:
        raise ManagedServiceError(
            f"kind {kind!r} cannot contain '/'",
        )
    return f"{kind}/{resource_id}"


def parse_handle(handle: str) -> tuple[str, str]:
    """Returns (kind, resource_id). Raises on malformed handles."""
    if "/" not in handle:
        raise ManagedServiceError(
            f"handle {handle!r} must be '<kind>/<resource_id>'",
        )
    kind, _, resource_id = handle.partition("/")
    if not kind or not resource_id:
        raise ManagedServiceError(
            f"handle {handle!r} has empty component",
        )
    return kind, resource_id


def tags_for(spec: ProvisionSpec) -> list[dict[str, str]]:
    """Standard AWS tag set the platform applies to every
    managed-service resource. Lets operators filter in the AWS
    console + apply tag-based budgets / IAM policies.

    Per memory: universal native cloud tagging schema with
    ``astrolift.io/*`` namespace. Includes ``astrolift.io/binding``
    and ``astrolift.io/managed_service_id`` when the spec carries
    them (#438) — these are the keys the cost collector joins on
    in the billing API to write per-binding ``CostSnapshot`` rows.
    """
    base = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/organization": spec.organization_slug,
        "astrolift.io/app": spec.app_slug,
        "astrolift.io/environment": spec.environment_name,
        "astrolift.io/cluster": spec.tenant_cluster_id,
        "astrolift.io/isolation": spec.isolation,
    }
    if spec.binding_id:
        base["astrolift.io/binding"] = spec.binding_id
    if spec.managed_service_id:
        base["astrolift.io/managed_service_id"] = spec.managed_service_id
    base.update({
        f"astrolift.io/extra/{k}": v
        for k, v in (spec.tags or {}).items()
    })
    # AWS tag list shape
    return [{"Key": k, "Value": v} for k, v in base.items()]
