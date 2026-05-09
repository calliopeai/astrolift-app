"""
Managed services in previews — shared vs dedicated policy
(#90, spec 18 §8).

Pure-Python module. The deploy-preview workflow consults this to
decide HOW to materialize a binding for a preview environment:

* **shared_with_main** (default for cheap services): re-use the
  main env's instance, but carve out per-preview namespacing
  (database, key prefix, bucket prefix, queue suffix) so previews
  don't collide.
* **dedicated**: provision a fresh instance at the smallest
  variant size. Same provisioning workflow as a real env, just
  with the preview's lifecycle.

Per-kind shared-mode plans (database name / key prefix / bucket
prefix / queue suffix) live here so the teardown workflow can
compute the same resources for cleanup without re-running deploy.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import Enum


class PreviewPolicy(str, Enum):
    SHARED_WITH_MAIN = "shared_with_main"
    DEDICATED = "dedicated"


# Spec 18 §8: cheap services default to shared, stateful/expensive
# default to dedicated. Operators override per-binding via
# manifest's [[managed_services]] preview_policy field.
_DEFAULT_POLICY: dict[str, PreviewPolicy] = {
    "postgres": PreviewPolicy.SHARED_WITH_MAIN,
    "mysql": PreviewPolicy.SHARED_WITH_MAIN,
    "redis": PreviewPolicy.SHARED_WITH_MAIN,
    "queue": PreviewPolicy.SHARED_WITH_MAIN,
    "topic": PreviewPolicy.SHARED_WITH_MAIN,
    "object_store": PreviewPolicy.SHARED_WITH_MAIN,
    # Stateful / expensive — dedicated by default
    "kv_store": PreviewPolicy.DEDICATED,
    "search": PreviewPolicy.DEDICATED,
    "vector_index": PreviewPolicy.DEDICATED,
    "time_series": PreviewPolicy.DEDICATED,
    "document_db": PreviewPolicy.DEDICATED,
    "mq": PreviewPolicy.DEDICATED,
    "nfs": PreviewPolicy.DEDICATED,
    # Externally-managed
    "cdn": PreviewPolicy.DEDICATED,
    "email": PreviewPolicy.SHARED_WITH_MAIN,
    "sms": PreviewPolicy.SHARED_WITH_MAIN,
}


def default_policy_for(kind: str) -> PreviewPolicy:
    """Returns the spec-default preview policy for ``kind``.
    Unknown kinds default to DEDICATED — fail-safe (fresh instance,
    no shared-state contamination)."""
    return _DEFAULT_POLICY.get(kind, PreviewPolicy.DEDICATED)


def resolve_policy(
    *, kind: str, manifest_override: str | None,
) -> PreviewPolicy:
    """Manifest's per-binding ``preview_policy`` field wins; else
    kind-specific default. Caller raises an error on unknown
    enum string at parse time."""
    if manifest_override:
        return PreviewPolicy(manifest_override)
    return default_policy_for(kind)


# ---- shared-mode plan ------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SharedResource:
    """One per-preview carve-out the deploy/teardown workflow
    creates/cleans up.

    Shape varies by kind but the common bits are: ``resource_type``
    identifies what's being carved out (e.g. 'database', 'user',
    'key_prefix'), and ``identifier`` is the per-preview suffix.
    """

    resource_type: str
    identifier: str


@dataclasses.dataclass(frozen=True, slots=True)
class SharedPlan:
    """Deploy-time plan for a shared_with_main binding."""

    kind: str
    resources: tuple[SharedResource, ...]


def shared_plan_for(*, kind: str, pr_number: int, app_slug: str) -> SharedPlan:
    """Compute the per-preview namespacing for a shared binding.

    Naming is deterministic: workflow can re-derive these on
    teardown without persisting them, and re-running deploy
    produces no extra resources.
    """
    if pr_number <= 0:
        raise ValueError("pr_number must be positive")
    if not app_slug:
        raise ValueError("app_slug is required")
    suffix = f"pr_{pr_number}"
    if kind in ("postgres", "mysql"):
        # Per-preview database + user
        return SharedPlan(
            kind=kind,
            resources=(
                SharedResource(
                    resource_type="database",
                    identifier=f"{app_slug}_{suffix}",
                ),
                SharedResource(
                    resource_type="user",
                    identifier=f"{app_slug}_{suffix}",
                ),
            ),
        )
    if kind == "redis":
        # Per-preview keyspace prefix + ACL user (when supported).
        return SharedPlan(
            kind=kind,
            resources=(
                SharedResource(
                    resource_type="key_prefix",
                    identifier=f"{suffix}:",
                ),
                SharedResource(
                    resource_type="acl_user",
                    identifier=f"{app_slug}_{suffix}",
                ),
            ),
        )
    if kind == "object_store":
        # Per-preview bucket key prefix.
        return SharedPlan(
            kind=kind,
            resources=(
                SharedResource(
                    resource_type="bucket_prefix",
                    identifier=f"{suffix}/",
                ),
            ),
        )
    if kind == "queue":
        return SharedPlan(
            kind=kind,
            resources=(
                SharedResource(
                    resource_type="queue",
                    identifier=f"{app_slug}-{suffix}",
                ),
            ),
        )
    if kind == "topic":
        return SharedPlan(
            kind=kind,
            resources=(
                SharedResource(
                    resource_type="topic",
                    identifier=f"{app_slug}-{suffix}",
                ),
            ),
        )
    if kind == "email" or kind == "sms":
        # External providers — share account, just tag messages
        # with a preview marker so deliveries don't go to real users.
        return SharedPlan(
            kind=kind,
            resources=(
                SharedResource(
                    resource_type="message_tag",
                    identifier=suffix,
                ),
            ),
        )
    # Other kinds default to DEDICATED — they shouldn't reach this
    # function. Surface loudly if they do (bug in the dispatcher).
    raise ValueError(
        f"shared_with_main not supported for kind {kind!r}; "
        "use DEDICATED policy"
    )


def teardown_plan_for(*, kind: str, pr_number: int, app_slug: str) -> SharedPlan:
    """Re-derive the same plan for cleanup. Identical to
    shared_plan_for — exposed under a different name so caller
    code reads clearly: 'I'm computing what to clean up'."""
    return shared_plan_for(kind=kind, pr_number=pr_number, app_slug=app_slug)
