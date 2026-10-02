"""Shared helpers for AWS managed-service drivers.

Each driver follows the same pattern:
- handle = ``<kind>/<aws-resource-id>`` (parsed in/out via
  handle_for / parse_handle)
- platform tags applied so the operator can filter in AWS console
  + apply tag-based budgets
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _sdk.managed_service import ProvisionSpec


class ManagedServiceError(Exception):
    """Base for managed-service driver errors. Distinct from
    ProviderError used by infra drivers because the workflow
    layer's failure-handling logic differs (managed-service
    failures often need partial cleanup of half-created
    resources)."""


class LiveOwnershipError(ManagedServiceError):
    """Live identity proof failed; diagnostics are never a missing-resource signal."""

    def __init__(self, message: str, *, code: str = "ownership_unknown") -> None:
        super().__init__(message)
        self.code = code


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


def adoption_refusal(existing_tags, spec: ProvisionSpec, *, resource: str) -> str | None:
    """Why ``provision`` must not adopt an existing resource, or ``None`` (#1961).

    Provision is name-idempotent: a resource that already exists under the
    computed name is taken as this service's. Names are built from slugs,
    which can collide across orgs, so adopting on name alone would hand one
    org another org's database. ``existing_tags`` is the resource's AWS tag
    list (``[{"Key", "Value"}]``) or dict. It is adopted only when its
    ``astrolift.io/managed_service_id`` tag is this service's, or, for a
    resource tagged before that tag existed, when its organization and app
    tags are this spec's.
    """
    if isinstance(existing_tags, dict):
        tags = {str(k): str(v) for k, v in existing_tags.items()}
    else:
        tags = {
            str(t.get("Key", t.get("key"))): str(t.get("Value", t.get("value")))
            for t in existing_tags or []
            if isinstance(t, dict)
        }
    owner_id = tags.get("astrolift.io/managed_service_id")
    if owner_id and spec.managed_service_id:
        if owner_id == spec.managed_service_id:
            return None
        return f"{resource} already exists and belongs to another managed service; refusing to adopt it"
    if (
        tags.get("astrolift.io/organization") == spec.organization_slug
        and tags.get("astrolift.io/app") == spec.app_slug
        and not (owner_id and spec.managed_service_id and owner_id != spec.managed_service_id)
    ):
        return None
    return f"{resource} already exists and is not tagged as this service's; refusing to adopt it"


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
    base.update({f"astrolift.io/extra/{k}": v for k, v in (spec.tags or {}).items()})
    # AWS tag list shape
    return [{"Key": k, "Value": v} for k, v in base.items()]


def live_ownership_refusal(existing_tags, *, managed_service_id: str, resource: str) -> str | None:
    """Live binding/update/teardown never infer an incarnation from slugs.

    Custom tags are namespaced separately. Only the platform marker and exact
    persisted managed-service identity authorize the current resource.
    """
    if not managed_service_id:
        return f"{resource}: managed-service identity is required to verify live ownership"
    if isinstance(existing_tags, dict):
        tags = existing_tags
    elif isinstance(existing_tags, list):
        tags = {}
        for row in existing_tags:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("Key"), str)
                or not isinstance(row.get("Value"), str)
            ):
                return f"{resource}: live ownership tags are unknown"
            if row["Key"] in tags:
                return f"{resource}: duplicate live ownership tags are unknown"
            tags[row["Key"]] = row["Value"]
    else:
        return f"{resource}: live ownership tags are unknown"
    if tags.get("astrolift.io/managed-by") != "platform":
        return f"{resource}: platform ownership marker is missing; refusing live access"
    owner = tags.get("astrolift.io/managed_service_id")
    if owner != managed_service_id:
        return f"{resource}: live managed-service ownership does not match; refusing access"
    return None


def assert_resource_arn(arn: str, *, service: str, region: str, resource: str, account: str = "") -> None:
    parts = arn.split(":", 5) if isinstance(arn, str) else []
    if (
        len(parts) != 6
        or parts[0] != "arn"
        or not parts[1]
        or parts[2] != service
        or parts[3] != region
        or len(parts[4]) != 12
        or not parts[4].isdigit()
        or (account and parts[4] != account)
        or parts[5] != resource
    ):
        raise ManagedServiceError("live AWS resource identity does not match the recorded driver target")


def managed_name_for(spec: ProvisionSpec, *, kind: str, prefix: str, max_len: int) -> str:
    """A recorded physical target outranks naming defaults, but confers no ownership."""
    from aws._naming import managed_service_identity, managed_service_name

    managed_service_identity(spec.managed_service_id)
    if spec.recorded_handle:
        recorded_kind, name = parse_handle(spec.recorded_handle)
        if recorded_kind != kind:
            raise ManagedServiceError("recorded AWS handle belongs to a different driver kind")
        return name
    if max_len < 34:
        from _sdk.physical_naming import physical_name

        return physical_name(spec.managed_service_id, prefix=prefix, max_length=max_len)
    return managed_service_name(spec.managed_service_id, prefix=prefix, max_len=max_len)
