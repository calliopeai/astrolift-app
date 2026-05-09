"""
Preview environment build policy (#85, spec 18 §5-6).

Pure-Python policy. ``BuildPreviewWorkflow`` consults this for:

* **Step ordering** — upsert → namespace → env synthesis →
  cost containment → managed services → deploy → bookkeeping.
  Reverse of teardown (#87).
* **Naming conventions** — namespace, AppEnvironment slug,
  hostname format.
* **Cost containment** — resource scaling, replica clamp,
  HPA disable. Spec defaults: 50% resources, 1 replica, no HPA.
* **Update vs create** — same PR with new commit reuses
  namespace + AppEnvironment, only commit_sha + redeploy.
* **PR comment templates** — success URL / failure log link.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from enum import Enum


class PreviewBuildError(ValueError):
    pass


# ---- step ordering -------------------------------------------------


class BuildStep(str, Enum):
    """Build flow per spec §5. Locked sequence."""

    UPSERT_PREVIEW = "upsert_preview"
    """Create or update PreviewEnvironment row with
    status=building."""

    ENSURE_NAMESPACE = "ensure_namespace"
    """Create namespace + default network policies."""

    SYNTHESIZE_ENV = "synthesize_env"
    """Create the per-PR AppEnvironment (name=preview-pr-N)."""

    APPLY_COST_CONTAINMENT = "apply_cost_containment"
    """Scale resources, clamp replicas, disable HPA."""

    PROVISION_MANAGED_SERVICES = "provision_managed_services"
    """Per binding: shared_with_main → carve preview slice;
    dedicated → spin up dedicated instance."""

    RUN_DEPLOY = "run_deploy"
    """Invoke DeployAppWorkflow against the preview env."""

    MARK_RUNNING = "mark_running"
    """Status=running, last_deployed_at=now, on success."""

    MARK_FAILED = "mark_failed"
    """Status=failed on deploy failure."""

    EMIT_EVENT = "emit_event"
    """preview_env.created event."""

    COMMENT_PR = "comment_pr"
    """PR comment with URL (success) or log link (failure)."""


BUILD_ORDER = (
    BuildStep.UPSERT_PREVIEW,
    BuildStep.ENSURE_NAMESPACE,
    BuildStep.SYNTHESIZE_ENV,
    BuildStep.APPLY_COST_CONTAINMENT,
    BuildStep.PROVISION_MANAGED_SERVICES,
    BuildStep.RUN_DEPLOY,
    # Followed by either MARK_RUNNING or MARK_FAILED depending
    # on RUN_DEPLOY outcome, then EMIT_EVENT, then COMMENT_PR.
)


def steps_for_outcome(*, deploy_succeeded: bool) -> tuple[BuildStep, ...]:
    """Full build flow including the conditional success/failure
    branch."""
    tail = (
        BuildStep.MARK_RUNNING if deploy_succeeded else BuildStep.MARK_FAILED,
        BuildStep.EMIT_EVENT,
        BuildStep.COMMENT_PR,
    )
    return BUILD_ORDER + tail


# ---- naming conventions --------------------------------------------


_RFC1123_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
"""K8s namespace = RFC 1123 label (max 63)."""


def namespace_for_preview(
    *,
    org_slug: str,
    app_slug: str,
    pr_number: int,
) -> str:
    """Spec §5: ``<org>-<app>-pr-<n>``. Validated against k8s
    namespace rules (truncates components if needed; raises if
    even truncation can't fit).
    """
    if not org_slug or not app_slug or pr_number <= 0:
        raise PreviewBuildError(
            "namespace_for_preview requires non-empty org_slug, "
            "app_slug, and positive pr_number"
        )
    raw = f"{org_slug}-{app_slug}-pr-{pr_number}"
    if len(raw) > 63:
        # Truncate the app slug first since org is shorter and
        # appears in many places. Reserve space for separators
        # + 'pr-N' suffix.
        suffix = f"-pr-{pr_number}"
        budget = 63 - len(org_slug) - 1 - len(suffix)
        if budget <= 0:
            raise PreviewBuildError(
                f"org_slug {org_slug!r} too long; can't fit a preview "
                "namespace within 63 chars"
            )
        truncated_app = app_slug[:budget].rstrip("-")
        raw = f"{org_slug}-{truncated_app}{suffix}"
    if not _RFC1123_LABEL_RE.match(raw):
        raise PreviewBuildError(
            f"namespace {raw!r} not RFC 1123 valid"
        )
    return raw


def env_slug_for_preview(*, pr_number: int) -> str:
    """Spec §5: ``preview-pr-<n>`` for the AppEnvironment slug."""
    if pr_number <= 0:
        raise PreviewBuildError(
            f"pr_number must be positive, got {pr_number}"
        )
    return f"preview-pr-{pr_number}"


def hostname_for_preview(
    *,
    pr_number: int,
    base_zone: str,
    org_slug: str,
) -> str:
    """Hostname under the platform's preview wildcard:
    ``pr-<n>.<org>.<base_zone>``. Pairs with the wildcard cert
    SAN ``*.pr.<org>.<base_zone>`` from #70.
    """
    if not base_zone or not org_slug:
        raise PreviewBuildError(
            "hostname_for_preview requires base_zone and org_slug"
        )
    if pr_number <= 0:
        raise PreviewBuildError(
            f"pr_number must be positive, got {pr_number}"
        )
    return f"pr-{pr_number}.{org_slug}.{base_zone}".lower()


# ---- cost containment ----------------------------------------------


DEFAULT_RESOURCE_SCALE = 0.5
"""Spec §6: 'scale resource requests down (default 50%)'.
Aggressive enough to keep preview costs low; high enough that
most apps still boot."""

DEFAULT_PREVIEW_REPLICAS = 1
"""Spec §6: replicas=1 for previews regardless of prod replica
count."""

DEFAULT_PREVIEW_HPA_ENABLED = False
"""Spec §6: HPA disabled — autoscaling for a preview wastes
budget on idle PRs."""


@dataclasses.dataclass(frozen=True, slots=True)
class CostContainment:
    """Per-workload cost-containment overrides applied to
    preview deploys."""

    resource_scale: float = DEFAULT_RESOURCE_SCALE
    """Multiplier on cpu/memory requests + limits."""

    replicas: int = DEFAULT_PREVIEW_REPLICAS
    hpa_enabled: bool = DEFAULT_PREVIEW_HPA_ENABLED

    def __post_init__(self) -> None:
        if not 0 < self.resource_scale <= 1.0:
            raise PreviewBuildError(
                f"resource_scale {self.resource_scale} not in (0, 1]"
            )
        if self.replicas < 0:
            raise PreviewBuildError(
                f"replicas {self.replicas} must be non-negative"
            )


def apply_cost_containment(
    *,
    workload_resources: Mapping,
    containment: CostContainment = CostContainment(),
) -> dict:
    """Scale a workload's resource block. Returns a fresh dict
    with cpu_request/memory_request/cpu_limit/memory_limit
    multiplied by ``resource_scale``.

    Resource values can be in k8s notation (e.g. '500m', '256Mi');
    the scaling preserves units. Unparsable values pass through
    unchanged with a warning surfaced via the workflow log
    activity (this module returns the unchanged value; the
    activity logs).
    """
    out = dict(workload_resources)
    for key in ("cpu_request", "cpu_limit", "memory_request", "memory_limit"):
        if key in out:
            out[key] = _scale_resource_value(
                value=out[key], scale=containment.resource_scale,
            )
    return out


_RESOURCE_VALUE_RE = re.compile(r"^(\d+(?:\.\d+)?)([a-zA-Z]*)$")


def _scale_resource_value(*, value: str, scale: float) -> str:
    """Multiply a k8s quantity by ``scale``, preserving unit
    suffix. Returns the unchanged value when the format isn't
    recognized (preview budget hint applied where possible)."""
    if not isinstance(value, str):
        return value
    match = _RESOURCE_VALUE_RE.match(value.strip())
    if not match:
        return value
    number, unit = match.groups()
    scaled = float(number) * scale
    # Preserve int-ness when input was int.
    if "." not in number:
        scaled_str = str(max(1, int(scaled)))
    else:
        scaled_str = f"{scaled:.1f}".rstrip("0").rstrip(".")
    return f"{scaled_str}{unit}"


# ---- create vs update ----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ExistingPreview:
    """Minimum projection used to decide create vs update."""

    preview_id: int
    namespace: str
    env_id: int
    last_commit_sha: str


def is_update_flow(
    *,
    existing: ExistingPreview | None,
    new_commit_sha: str,
) -> bool:
    """Spec §5: 'subsequent pushes redeploy to the existing
    preview (no duplicate namespaces)'.

    Update flow: existing preview + different SHA. Same SHA
    means a re-delivery of the same webhook (handled by #83's
    workflow_id determinism, but defensive here too)."""
    if existing is None:
        return False
    return existing.last_commit_sha != new_commit_sha


# ---- PR comment templates ------------------------------------------


def pr_comment_for_success(
    *,
    pr_number: int,
    preview_url: str,
) -> str:
    """Spec §5: 'PR comment includes the preview URL on
    success.'"""
    return (
        f"**Preview environment ready**\n\n"
        f"PR #{pr_number} is deployed.\n\n"
        f"[Open preview]({preview_url})\n\n"
        f"This preview will be torn down when the PR is "
        f"closed or merged."
    )


def pr_comment_for_failure(
    *,
    pr_number: int,
    logs_url: str,
) -> str:
    """Spec §5: 'PR comment includes log link on failure.'"""
    body = (
        f"**Preview build failed**\n\n"
        f"The preview environment for PR #{pr_number} couldn't "
        f"be deployed."
    )
    if logs_url:
        body += f"\n\n[View build logs]({logs_url})"
    return body
