"""Resolve manual deployment inputs before creating or superseding work."""

from __future__ import annotations

import dataclasses

from astrolift_registry.models import RegisteredApp
from astrolift_scm.providers import ProviderError
from astrolift_scm.providers.revisions import fetch_commit
from astrolift_scm.services.webhooks import _pick_source_connection


@dataclasses.dataclass(frozen=True)
class DeploymentSource:
    image_tag: str
    commit_sha: str
    branch: str


def resolve_deployment_source(
    app: RegisteredApp, *, image_tag="", commit_sha="", branch="", source_ref=""
) -> DeploymentSource:
    image_tag = (image_tag or "").strip()
    commit_sha = (commit_sha or "").strip()
    branch = (branch or "").strip()
    source_ref = (source_ref or "").strip()
    mode = app.build_mode
    if mode not in RegisteredApp.BuildMode.values:
        raise ProviderError("VALIDATION", f"Unknown app build mode: {mode!r}")
    if mode == RegisteredApp.BuildMode.CI_PUSHED and not image_tag:
        raise ProviderError("VALIDATION", "image_tag is required for ci_pushed apps")
    if source_ref and commit_sha:
        raise ProviderError("VALIDATION", "Specify source_ref or commit_sha, not both")

    needs_build_tag = mode == RegisteredApp.BuildMode.PLATFORM_BUILD and not image_tag
    if needs_build_tag and app.effective_build_strategy == RegisteredApp.BuildStrategy.OFF:
        raise ProviderError(
            "PRECONDITION", "Select a platform build strategy before deploying without an image tag"
        )
    if source_ref or needs_build_tag:
        connection = _pick_source_connection(app)
        if connection is None:
            raise ProviderError(
                "PRECONDITION", "An active source connection is required to resolve the deployment revision"
            )
        ref = source_ref or commit_sha or branch or app.deploy_branch or app.default_branch or "main"
        commit_sha = fetch_commit(connection, repo_full_name=app.source_repo, ref=ref)
        branch = branch or source_ref or app.deploy_branch or app.default_branch or "main"
        if needs_build_tag:
            image_tag = commit_sha

    return DeploymentSource(image_tag=image_tag, commit_sha=commit_sha, branch=branch)
