"""
Reflect a Deployment's lifecycle into GitHub (#1124).

Best-effort mirror: on deploy start / success / failure the platform
stamps a GitHub Deployment (Environments UI + PR "deployed to <env>"
badge) AND a commit status. GitHub is one-way and non-load-bearing
here — every entry point swallows all errors so a reflection problem can
never fail, slow unboundedly, or roll back a platform deploy.

v1 is GitHub-only: the app's ``source_kind`` must be ``github`` with an
``owner/repo`` ``source_repo``, and the org must have installed the
GitHub App (``deployments: write``). Non-GitHub hosts, repo-less apps,
apps with no commit SHA, and orgs without the App all skip cleanly.
GitLab has an equivalent Environments/deployments API — out of scope here.

The three entry points take an already-loaded ``Deployment`` and are
called from the deploy lifecycle sync activities, AFTER the status
transition has committed:

  - ``reflect_deploy_started``   → create GH deployment + in_progress + pending
  - ``reflect_deploy_succeeded`` → GH deployment success + commit success
  - ``reflect_deploy_failed``    → GH deployment failure + commit failure
"""

from __future__ import annotations

import dataclasses
import logging

logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class _ReflectContext:
    """Everything the GitHub calls need, resolved once per reflection."""

    connection: object
    owner: str
    repo: str
    environment: str
    commit_sha: str


def reflect_deploy_started(deployment) -> None:
    """Deploy entered DEPLOYING: create the GitHub Deployment (storing
    its id for idempotent later updates), mark it ``in_progress``, and
    stamp the commit status ``pending``."""
    try:
        ctx = _resolve_context(deployment)
        if ctx is None:
            return
        from astrolift_scm import github_deployments

        log_url = _deployment_detail_url(deployment)
        dep_id = _ensure_github_deployment(deployment, ctx)
        if dep_id is not None:
            github_deployments.post_github_deployment_status(
                ctx.connection,
                ctx.owner,
                ctx.repo,
                dep_id,
                state="in_progress",
                environment=ctx.environment,
                log_url=log_url,
                description=f"Deploying to {ctx.environment}",
            )
        _post_commit_status(ctx, deployment, "deploying", target_url=log_url)
    except Exception:  # noqa: BLE001 — GitHub reflection is never load-bearing
        logger.warning("reflect_deploy_started failed", exc_info=True)


def reflect_deploy_succeeded(deployment) -> None:
    """Deploy reached RUNNING: mark the GitHub Deployment ``success``
    (with the app's live URL as ``environment_url``) and stamp the commit
    status ``success``."""
    try:
        ctx = _resolve_context(deployment)
        if ctx is None:
            return
        from astrolift_scm import github_deployments

        log_url = _deployment_detail_url(deployment)
        env_url = _environment_url(deployment.registered_app)
        dep_id = _ensure_github_deployment(deployment, ctx)
        if dep_id is not None:
            github_deployments.post_github_deployment_status(
                ctx.connection,
                ctx.owner,
                ctx.repo,
                dep_id,
                state="success",
                environment=ctx.environment,
                environment_url=env_url,
                log_url=log_url,
                description=f"Deployed to {ctx.environment}",
            )
        _post_commit_status(ctx, deployment, "running", target_url=log_url or env_url)
    except Exception:  # noqa: BLE001 — GitHub reflection is never load-bearing
        logger.warning("reflect_deploy_succeeded failed", exc_info=True)


def reflect_deploy_failed(deployment, reason: str) -> None:
    """Deploy reached FAILED: mark the GitHub Deployment ``failure``
    (carrying the reason) and stamp the commit status ``failure``."""
    try:
        ctx = _resolve_context(deployment)
        if ctx is None:
            return
        from astrolift_scm import github_deployments

        log_url = _deployment_detail_url(deployment)
        dep_id = _ensure_github_deployment(deployment, ctx)
        detail = (reason or "").strip() or f"Deploy to {ctx.environment} failed"
        if dep_id is not None:
            github_deployments.post_github_deployment_status(
                ctx.connection,
                ctx.owner,
                ctx.repo,
                dep_id,
                state="failure",
                environment=ctx.environment,
                log_url=log_url,
                description=detail,
            )
        _post_commit_status(ctx, deployment, "failed", target_url=log_url)
    except Exception:  # noqa: BLE001 — GitHub reflection is never load-bearing
        logger.warning("reflect_deploy_failed failed", exc_info=True)


# ---- internals --------------------------------------------------------


def _resolve_context(deployment) -> _ReflectContext | None:
    """Resolve the GitHub coordinates + connection, or ``None`` to skip.

    Skips (returns None) when the app isn't a GitHub source, the
    ``source_repo`` isn't a clean ``owner/repo``, there's no commit SHA
    to anchor the deployment to, or the org has no usable App connection.
    """
    app = deployment.registered_app
    # v1: GitHub host only. GitLab/Bitbucket/Gitea have their own
    # deployments API surfaces — out of scope for #1124.
    if str(getattr(app, "source_kind", "")) != "github":
        return None
    owner, repo = _parse_owner_repo(getattr(app, "source_repo", "") or "")
    if not owner or not repo:
        return None
    commit_sha = (deployment.commit_sha or "").strip()
    if not commit_sha:
        return None

    from astrolift_scm.services.connection_resolver import (
        ORG_REPO_WRITE,
        ConnectionResolutionError,
        resolve_connection,
    )

    try:
        connection = resolve_connection(
            app.organization_id,
            purpose=ORG_REPO_WRITE,
            source_kind="github",
        )
    except ConnectionResolutionError:
        # No App / OAuth / PAT connection for this org — nothing to
        # reflect to. This is a normal skip, not an error.
        return None

    env_name = ""
    if deployment.app_environment_id:
        env_name = (deployment.app_environment.name or "").strip()
    return _ReflectContext(
        connection=connection,
        owner=owner,
        repo=repo,
        environment=env_name or "production",
        commit_sha=commit_sha,
    )


def _ensure_github_deployment(deployment, ctx: _ReflectContext) -> int | None:
    """Return the GitHub deployment id to target for a status update.

    Reuses the id stored at deploy-start (idempotent); if it's missing
    (the deploy predates this integration, or the create call failed at
    start) best-effort creates one now so the terminal status still
    lands. Returns ``None`` only when even that create fails.
    """
    existing = deployment.github_deployment_id
    if existing:
        return existing

    from astrolift_scm import github_deployments

    dep_id = github_deployments.create_github_deployment(
        ctx.connection,
        ctx.owner,
        ctx.repo,
        ref=ctx.commit_sha,
        environment=ctx.environment,
        description=f"Astrolift deploy to {ctx.environment}",
    )
    if dep_id is None:
        return None
    deployment.github_deployment_id = dep_id
    # Persist only this column (plus the tracking columns the base save
    # increments) so we never clobber a concurrent status transition.
    deployment.save(update_fields=["github_deployment_id", "updated_at", "version"])
    return dep_id


def _post_commit_status(ctx: _ReflectContext, deployment, status: str, *, target_url: str = "") -> None:
    """Stamp the dormant commit-status integration for this lifecycle
    point. Keyed by SHA (independent of the GitHub deployment id), so it
    fires even when the Deployments API call was skipped."""
    from astrolift_scm.commit_status import deployment_status_to_github, post_commit_status

    payload = deployment_status_to_github(status, env_name=ctx.environment, target_url=target_url)
    post_commit_status(
        connection=ctx.connection,
        owner=ctx.owner,
        repo=ctx.repo,
        sha=ctx.commit_sha,
        payload=payload,
    )


def _parse_owner_repo(source_repo: str) -> tuple[str, str]:
    """``owner/repo`` → ``(owner, repo)``.

    Tolerates surrounding slashes and a trailing ``.git``. Returns
    ``("", "")`` for anything that isn't exactly two non-empty path
    segments (a full URL, a bare name, or blank) so the caller skips.
    """
    value = source_repo.strip().strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    parts = value.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return "", ""
    return parts[0], parts[1]


def _deployment_detail_url(deployment) -> str:
    """Absolute deep-link to the Astrolift deploy detail page, used as
    the GitHub ``log_url`` + commit-status ``target_url``. Empty when
    ``APP_BASE_URL`` isn't configured (local dev) or the app has no slug —
    the GitHub calls omit an empty link cleanly."""
    from django.conf import settings

    base = (getattr(settings, "APP_BASE_URL", "") or "").rstrip("/")
    if not base:
        return ""
    slug = getattr(deployment.registered_app, "slug", "") or ""
    if not slug:
        return ""
    return f"{base}/apps/{slug}/deployments/{deployment.guid}"


def _environment_url(app) -> str:
    """The app's canonical live https URL, for GitHub's "View deployment"
    button. Empty when the app has no resolvable public URL."""
    try:
        from astrolift_observability.url_resolution import app_urls, resolved_public_host

        urls = app_urls(app)
        if urls:
            return urls[0]
        host = resolved_public_host(app)
        return f"https://{host}" if host else ""
    except Exception:  # noqa: BLE001 — URL resolution is best-effort too
        return ""
