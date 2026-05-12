"""
GitOps config repo emitter policy (#7, spec 07 §3.1 + §3.3).

Pure-Python helpers for the GitOps emitter:

* **Repo path layout** — ``clusters/<cluster>/apps/<org>-<app>/<env>/``.
  Pairs with #22 emit-all (full repo regeneration uses the same
  layout).
* **Structured commit message** — ``<verb> <app>/<env>: <reason>``
  with ``Astrolift-WorkflowRun: <run_id>`` trailer so an operator
  reading the repo can trace every commit back to a workflow run.
* **Author identity** — ``astrolift-bot <bot@<platform-domain>>``
  so commits are immediately distinguishable from human pushes
  in the audit log.
* **One-commit-per-event** invariant helpers.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from enum import Enum

# Spec 07 §3.1 path layout. The cluster name is the prefix the
# GitOps tool watches; nesting under ``apps/`` keeps shared
# directories (CRDs, RBAC) at the cluster level distinct from
# tenant manifests.
_REPO_PATH_FMT = "clusters/{cluster}/apps/{org}-{app}/{env}"


# Slug validators reject anything that would break a git path.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")


class GitopsEmitterError(ValueError):
    pass


def repo_path_for(
    *,
    cluster_slug: str,
    org_slug: str,
    app_slug: str,
    env_slug: str,
) -> str:
    """Compose the deterministic repo path. All components must
    pass the slug regex (lowercase alphanumeric + dashes) so a
    typo can't escape the namespace."""
    for label, slug in (
        ("cluster_slug", cluster_slug),
        ("org_slug", org_slug),
        ("app_slug", app_slug),
        ("env_slug", env_slug),
    ):
        if not slug:
            raise GitopsEmitterError(f"{label} is required")
        if not _SLUG_RE.match(slug):
            raise GitopsEmitterError(
                f"{label} {slug!r} is not a valid slug " "(lowercase alphanumeric + dashes; alpha-start)"
            )
    return _REPO_PATH_FMT.format(
        cluster=cluster_slug,
        org=org_slug,
        app=app_slug,
        env=env_slug,
    )


# ---- commit message ------------------------------------------------


class CommitVerb(str, Enum):
    """Closed vocabulary so log readers can grep effectively."""

    DEPLOY = "deploy"
    PROMOTE = "promote"
    ROLLBACK = "rollback"
    REDEPLOY = "redeploy"
    DELETE = "delete"
    UPDATE_CONFIG = "config"


@dataclasses.dataclass(frozen=True, slots=True)
class CommitMessage:
    title: str
    body: str
    """Trailers (RFC 5322-style key: value) used by humans and tools
    to trace commits back to workflow runs."""

    @property
    def full(self) -> str:
        if self.body:
            return f"{self.title}\n\n{self.body}"
        return self.title


def build_commit_message(
    *,
    verb: CommitVerb,
    app_slug: str,
    env_slug: str,
    reason: str,
    workflow_run_id: str,
    deployment_id: int | None = None,
) -> CommitMessage:
    """Compose the structured message. Title format per spec
    07 §3.3: ``<verb> <app>/<env>: <reason>``."""
    if not reason:
        raise GitopsEmitterError("reason is required")
    if not workflow_run_id:
        raise GitopsEmitterError("workflow_run_id is required (audit trail trailer)")
    title = f"{verb.value} {app_slug}/{env_slug}: {reason}"
    body_lines = [f"Astrolift-WorkflowRun: {workflow_run_id}"]
    if deployment_id is not None:
        body_lines.append(f"Astrolift-Deployment: {deployment_id}")
    return CommitMessage(title=title, body="\n".join(body_lines))


# ---- author identity ----------------------------------------------


def bot_author(*, platform_domain: str) -> tuple[str, str]:
    """Return ``(name, email)`` the emitter sets on every commit.

    Distinct from human users so an operator reading ``git log``
    can immediately see what's bot-authored vs human-authored.
    """
    if not platform_domain:
        raise GitopsEmitterError("platform_domain is required")
    return ("astrolift-bot", f"bot@{platform_domain}")


# ---- one-commit-per-event invariant --------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CommitPlan:
    """One workflow event → exactly one commit. The emitter
    composes this and hands it to git."""

    repo_path: str
    files: tuple[tuple[str, str], ...]
    """List of (relative_path, content) pairs. Empty content
    means delete that path."""

    message: CommitMessage
    author_name: str
    author_email: str


def plan_commit(
    *,
    cluster_slug: str,
    org_slug: str,
    app_slug: str,
    env_slug: str,
    files: Sequence[tuple[str, str]],
    verb: CommitVerb,
    reason: str,
    workflow_run_id: str,
    platform_domain: str,
    deployment_id: int | None = None,
) -> CommitPlan:
    """Bundle everything the emitter needs into a single plan
    object. Validation runs once here so the git ops step doesn't
    have to."""
    repo_path = repo_path_for(
        cluster_slug=cluster_slug,
        org_slug=org_slug,
        app_slug=app_slug,
        env_slug=env_slug,
    )
    msg = build_commit_message(
        verb=verb,
        app_slug=app_slug,
        env_slug=env_slug,
        reason=reason,
        workflow_run_id=workflow_run_id,
        deployment_id=deployment_id,
    )
    name, email = bot_author(platform_domain=platform_domain)
    return CommitPlan(
        repo_path=repo_path,
        files=tuple(files),
        message=msg,
        author_name=name,
        author_email=email,
    )
