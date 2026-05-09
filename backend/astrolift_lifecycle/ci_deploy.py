"""
CI deploy endpoint policy (#17, spec 07 §9).

Pure-Python policy. POST /api/cli/v1/apps/<slug>/deploy/ consults
this for:

* **Request validation** — image_tags, commit_sha, branch,
  environment, trigger_kind. Refuses malformed input loudly.
* **Trigger-kind whitelist** — closed vocabulary of CI sources
  the endpoint accepts.
* **Idempotency key** — optional client-supplied; deduplicates
  re-deliveries within a window.
* **Workflow handle** — deterministic so the polling URL stays
  stable.

Pairs with #142's deploy_tokens.py (auth) and #83's webhook
dispatch (the github-PR-event sister flow).
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Mapping
from enum import Enum


class CiDeployError(ValueError):
    pass


# ---- trigger kinds -------------------------------------------------


class TriggerKind(str, Enum):
    """Locked vocabulary. Anything else gets refused so the
    audit log doesn't pile up free-text trigger sources."""

    CI = "ci"
    """Generic CI system (GitHub Actions, GitLab CI, etc.)
    posting via this endpoint."""

    MANUAL_CLI = "manual_cli"
    """Operator running ``astro app deploy <slug>`` from their
    laptop. Distinguished from CI for audit + rate-limit
    purposes (CI tokens get higher RPM)."""

    WEBHOOK_SCM = "webhook_scm"
    """Internal: the GitHub PR webhook handler dispatches via
    this endpoint after #83's classification."""

    SCHEDULED = "scheduled"
    """Internal: scheduled redeploy (e.g. nightly rebuild)."""


# ---- image tag validation ------------------------------------------


_VALID_DIGEST_RE = re.compile(r"^[0-9a-f]{12,64}$")
_HEX_RE = re.compile(r"^[0-9a-f]+$")
_VALID_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


def validate_image_tag(*, value: str) -> str:
    """Image tag value: SHA prefix (12-64 hex chars) OR OCI
    tag rules (RFC 1123 + dot/underscore). Both accepted —
    operators usually pass commit SHA but legacy CI may pass
    the raw OCI tag name.

    Returns the value (unchanged when already valid)."""
    if not value:
        raise CiDeployError("image tag cannot be empty")
    if _VALID_DIGEST_RE.match(value):
        return value
    if _VALID_TAG_RE.match(value):
        return value
    raise CiDeployError(
        f"image tag {value!r} not a valid OCI tag or hex digest"
    )


def validate_image_tags(
    *,
    workload_tags: Mapping[str, str],
    declared_workloads: tuple[str, ...],
) -> dict[str, str]:
    """Spec §9: image_tags is keyed by workload slug. Refuse:
    - empty mapping (every CI deploy must specify SOMETHING)
    - tags for workloads not in the manifest (operator typo
      defense — silent ignore would mean the unrelated workload
      they meant to update doesn't get the new image)
    - missing tag for any declared workload (defaults to
      previous tag — surfaced as 'partial deploy' warning;
      this layer just validates shape, the workflow decides
      what to do with absent keys).
    """
    if not workload_tags:
        raise CiDeployError(
            "image_tags is required and cannot be empty"
        )

    declared = set(declared_workloads)
    out: dict[str, str] = {}
    for slug, tag in workload_tags.items():
        if not slug:
            raise CiDeployError("workload slug in image_tags is empty")
        if slug not in declared:
            raise CiDeployError(
                f"workload {slug!r} not declared in app manifest "
                f"(declared workloads: {sorted(declared)})"
            )
        out[slug] = validate_image_tag(value=tag)

    return out


# ---- request validation -------------------------------------------


_VALID_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")


def validate_branch(*, value: str) -> str:
    """Branch names per Git's check-ref-format roughly. We
    accept anything that fits a path-segment shape; refuse
    obvious garbage."""
    if not value:
        raise CiDeployError("branch is required")
    if not _VALID_BRANCH_RE.match(value):
        raise CiDeployError(
            f"branch {value!r} contains invalid characters"
        )
    return value


def validate_commit_sha(*, value: str) -> str:
    """SHA-1 (40 hex) or SHA-256 (64 hex). Refuse short SHAs —
    CI provides full ones, and short SHAs collide."""
    if not value or not _HEX_RE.match(value):
        raise CiDeployError(
            f"commit_sha {value!r} not a valid hex digest"
        )
    if len(value) < 40:
        raise CiDeployError(
            f"commit_sha {value!r} too short (min 40 hex chars); "
            "CI should provide the full SHA"
        )
    if len(value) > 64:
        raise CiDeployError(
            f"commit_sha {value!r} too long (max 64 hex chars)"
        )
    return value


def validate_environment(
    *,
    name: str,
    registered_envs: tuple[str, ...],
) -> str:
    """Environment must exist on the app. Typo defense: 'preod'
    instead of 'prod' would silently deploy nothing."""
    if not name:
        raise CiDeployError("environment is required")
    if name not in registered_envs:
        raise CiDeployError(
            f"environment {name!r} not registered for this app "
            f"(known: {sorted(registered_envs)})"
        )
    return name


def validate_trigger_kind(*, value: str) -> TriggerKind:
    try:
        return TriggerKind(value)
    except ValueError as exc:
        raise CiDeployError(
            f"trigger_kind {value!r} not in vocabulary "
            f"{[k.value for k in TriggerKind]}"
        ) from exc


# ---- request shape -------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CiDeployRequest:
    """Validated request shape. View layer constructs this via
    ``parse_request``; raises CiDeployError on bad input."""

    app_slug: str
    image_tags: dict[str, str]
    commit_sha: str
    branch: str
    environment: str
    trigger_kind: TriggerKind
    idempotency_key: str = ""
    """Optional client-supplied de-dupe key. When set, the
    workflow ID embeds it so re-delivery within the dedupe
    window lands on the same workflow."""


def parse_request(
    *,
    app_slug: str,
    raw_body: Mapping,
    declared_workloads: tuple[str, ...],
    registered_envs: tuple[str, ...],
) -> CiDeployRequest:
    """Validate the full POST body. Raises with a specific
    field reference so the view layer can surface 400 errors
    that point the operator at what to fix."""
    if not app_slug:
        raise CiDeployError("app_slug is required")
    if not isinstance(raw_body, Mapping):
        raise CiDeployError("request body must be a JSON object")

    return CiDeployRequest(
        app_slug=app_slug,
        image_tags=validate_image_tags(
            workload_tags=raw_body.get("image_tags") or {},
            declared_workloads=declared_workloads,
        ),
        commit_sha=validate_commit_sha(
            value=str(raw_body.get("commit_sha", "")),
        ),
        branch=validate_branch(value=str(raw_body.get("branch", ""))),
        environment=validate_environment(
            name=str(raw_body.get("environment", "")),
            registered_envs=registered_envs,
        ),
        trigger_kind=validate_trigger_kind(
            value=str(raw_body.get("trigger_kind", "ci")),
        ),
        idempotency_key=str(raw_body.get("idempotency_key", "")),
    )


# ---- workflow handle ----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class WorkflowHandle:
    """Returned to the CI client. Polling URL stays stable
    across re-deliveries when idempotency_key is provided."""

    workflow_id: str
    polling_url: str


def workflow_id_for(*, request: CiDeployRequest) -> str:
    """Deterministic workflow ID per spec acceptance:
    'duplicate deliveries don't create duplicate workflows.'

    Fingerprint includes:
    - app_slug + environment (multi-env apps deploy independently)
    - commit_sha (different commits = different workflows)
    - sorted image_tags (image swap with same commit = different
      workflow; e.g. retag from same SHA)
    - idempotency_key when provided (CI's explicit dedupe)
    """
    parts = [
        f"app={request.app_slug}",
        f"env={request.environment}",
        f"commit={request.commit_sha}",
    ]
    for slug in sorted(request.image_tags.keys()):
        parts.append(f"img:{slug}={request.image_tags[slug]}")
    if request.idempotency_key:
        parts.append(f"idem={request.idempotency_key}")

    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return f"deploy-{request.app_slug}-{request.environment}-{digest}"


def polling_url_for(
    *,
    workflow_id: str,
    api_base: str = "/api/cli/v1",
) -> str:
    """Spec §9: response includes polling URL."""
    return f"{api_base}/workflow_runs/{workflow_id}"


def build_handle(
    *,
    request: CiDeployRequest,
    api_base: str = "/api/cli/v1",
) -> WorkflowHandle:
    workflow_id = workflow_id_for(request=request)
    return WorkflowHandle(
        workflow_id=workflow_id,
        polling_url=polling_url_for(
            workflow_id=workflow_id, api_base=api_base,
        ),
    )


# ---- per-token rate limit -----------------------------------------


# Spec acceptance: 'rate limiting applied per deploy token.'
# Different defaults per trigger kind — manual_cli is operator
# at a keyboard, ci is automated.
RATE_LIMIT_PER_MINUTE = {
    TriggerKind.CI: 60,
    TriggerKind.MANUAL_CLI: 30,
    TriggerKind.WEBHOOK_SCM: 60,
    TriggerKind.SCHEDULED: 10,
}


def rate_limit_for(*, kind: TriggerKind) -> int:
    if kind not in RATE_LIMIT_PER_MINUTE:
        raise CiDeployError(f"unknown trigger_kind {kind!r}")
    return RATE_LIMIT_PER_MINUTE[kind]
