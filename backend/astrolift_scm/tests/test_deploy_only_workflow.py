"""Deploy-only CI workflow rendering for externally-built images.

When an app has no platform-built image — ``RegisteredApp.registry_repo_uri`` is
blank — the managed CI workflow must NOT try to build+push a Docker image. An
empty registry URI would otherwise render an invalid ``:<sha>`` blank tag
(``docker build/push`` → ``invalid tag`` → red CI), and for a multi-agent repo
whose images are built by a separate pipeline (e.g. ``steadymd/smd-agents``'
``build-agent-images.yml``) there is nothing for this workflow to build. So
deploy-only keeps checkout + creds + the Astrolift notify step and drops the
build/push — CI's only job is to tell the platform a new SHA exists.

Pure render tests — SimpleNamespace stand-ins, no DB. ``settings.PLATFORM_API_URL``
is pinned because the renderers fold it into the body.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_scm.services.workflow_sync import (
    _apply_blocks,
    render_astrolift_bitbucket_pipeline,
    render_astrolift_ci_workflow,
    render_astrolift_gitea_ci_workflow,
    render_astrolift_gitlab_ci_workflow,
)

# host -> renderer under test.
_RENDERERS = {
    "github": render_astrolift_ci_workflow,
    "gitlab": render_astrolift_gitlab_ci_workflow,
    "gitea": render_astrolift_gitea_ci_workflow,
    "bitbucket": render_astrolift_bitbucket_pipeline,
}

# The ``:<sha>`` image-tag suffix each host emits. In build-and-deploy mode it
# appears as ``<registry-uri>:<sha>`` (a valid tag); a blank registry_repo_uri
# would collapse it to a leading-colon blank tag — exactly the emr-bug-triage
# prod bug deploy-only must avoid. So: present in the platform-built render,
# absent in deploy-only.
_COLON_SHA_SUFFIX = {
    "github": ":${{ github.sha }}",
    "gitea": ":${{ github.sha }}",
    "gitlab": ":$CI_COMMIT_SHA",
    "bitbucket": ":$BITBUCKET_COMMIT",
}

_PLATFORM_URI = "123456789012.dkr.ecr.us-west-2.amazonaws.com/emr-bug-triage"

# What the notify step's deploy-endpoint path looks like per host: GitHub and
# GitLab route through env-var indirection ($APP_SLUG); Gitea and Bitbucket
# inline the slug literal.
_NOTIFY_PATH = {
    "github": "/api/cli/v1/apps/$APP_SLUG/deploy/",
    "gitlab": "/api/cli/v1/apps/$ASTROLIFT_APP_SLUG/deploy/",
    "gitea": "/api/cli/v1/apps/emr-bug-triage/deploy/",
    "bitbucket": "/api/cli/v1/apps/emr-bug-triage/deploy/",
}


def _app(*, registry_repo_uri: str, source_kind: str) -> SimpleNamespace:
    """An app-shaped object; ``registry_repo_uri`` toggles build vs deploy-only."""
    return SimpleNamespace(
        slug="emr-bug-triage",
        deploy_branch="main",
        registry_repo_uri=registry_repo_uri,
        push_role_ref="arn:aws:iam::123456789012:role/astrolift-push-emr",
        source_kind=source_kind,
        source_repo="steadymd/smd-agents",
    )


@pytest.mark.parametrize("host", sorted(_RENDERERS))
def test_platform_built_renders_build_and_push(host, settings):
    """An app WITH registry_repo_uri keeps the build+push step (unchanged path)."""
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    body = _RENDERERS[host](_app(registry_repo_uri=_PLATFORM_URI, source_kind=host))

    assert "docker build" in body
    assert "docker push" in body
    # The valid ``<uri>:<sha>`` tag: the registry prefix sits in front of the colon.
    assert _PLATFORM_URI in body
    assert _COLON_SHA_SUFFIX[host] in body
    # Notify step targets the real CI deploy endpoint (#1220).
    assert _NOTIFY_PATH[host] in body
    assert "/api/v1/deploys" not in body


@pytest.mark.parametrize("host", sorted(_RENDERERS))
def test_deploy_only_drops_build_keeps_notify(host, settings):
    """An app WITHOUT registry_repo_uri renders deploy-only."""
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    body = _RENDERERS[host](_app(registry_repo_uri="", source_kind=host))

    # No image build/push anywhere.
    assert "docker build" not in body
    assert "docker push" not in body
    # No blank ``:<sha>`` tag — this is the emr-bug-triage prod bug.
    assert _COLON_SHA_SUFFIX[host] not in body
    # Notify still present: CI's only job is to tell the platform to deploy.
    assert _NOTIFY_PATH[host] in body
    assert "/api/v1/deploys" not in body


@pytest.mark.parametrize("host", sorted(_RENDERERS))
def test_deploy_only_tolerates_whitespace_only_uri(host, settings):
    """A whitespace-only registry_repo_uri is treated as blank (deploy-only)."""
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    body = _RENDERERS[host](_app(registry_repo_uri="   ", source_kind=host))
    assert "docker build" not in body
    assert "docker push" not in body


def test_github_deploy_only_keeps_checkout_and_oidc_drops_ecr(settings):
    """GitHub deploy-only keeps checkout + OIDC creds, drops the ECR-login step."""
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    body = render_astrolift_ci_workflow(_app(registry_repo_uri="", source_kind="github"))

    assert "actions/checkout@v4" in body
    assert "Configure AWS credentials (OIDC)" in body  # harmless, kept
    assert "aws-actions/configure-aws-credentials@v4" in body
    assert "aws-actions/amazon-ecr-login" not in body  # only needed for the build
    assert "Build and push image" not in body
    # Deploy-only notifies with the bare commit SHA as the wildcard tag.
    assert '{\\"image_tags\\":{\\"*\\":\\"${{ github.sha }}\\"}' in body
    # Template block/var markers must all be resolved.
    assert "{%" not in body
    assert "{{ ecr_uri }}" not in body
    assert "{{ ecr_repo_name }}" not in body


def test_github_build_render_still_has_every_build_step(settings):
    """The platform-built GitHub render is untouched by deploy-only support."""
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    body = render_astrolift_ci_workflow(_app(registry_repo_uri=_PLATFORM_URI, source_kind="github"))

    assert "aws-actions/amazon-ecr-login@v2" in body
    assert "Build and push image" in body
    assert f"{_PLATFORM_URI}:${{{{ github.sha }}}}" in body
    # Skip-if-built probe (#1220): re-runs on an already-built SHA must not
    # die on the registry's immutable tags.
    assert "Check for existing image" in body
    assert "aws ecr describe-images" in body
    assert "ECR_REPO: emr-bug-triage" in body
    assert "if: steps.image_exists.outputs.exists != 'true'" in body
    assert "{%" not in body  # blocks resolved
    assert "{{ ecr_repo_name }}" not in body


def test_github_managed_workflow_cancels_superseded_runs(settings):
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    body = render_astrolift_ci_workflow(_app(registry_repo_uri=_PLATFORM_URI, source_kind="github"))

    assert "group: astrolift-emr-bug-triage" in body
    assert "cancel-in-progress: true" in body


def test_gitlab_deploy_only_drops_build_stage(settings):
    """GitLab deploy-only has no ``build`` stage / ``build-image`` job / ``needs``."""
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    body = render_astrolift_gitlab_ci_workflow(_app(registry_repo_uri="", source_kind="gitlab"))

    assert "build-image" not in body
    assert "needs:" not in body
    assert "  - build\n" not in body
    assert "docker:24-dind" not in body
    assert 'ASTROLIFT_IMAGE: "$CI_COMMIT_SHA"' in body  # bare sha, no blank tag
    assert "notify-astrolift" in body


def test_bitbucket_deploy_only_drops_docker_service(settings):
    """Bitbucket deploy-only drops the docker service + ECR login."""
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    body = render_astrolift_bitbucket_pipeline(_app(registry_repo_uri="", source_kind="bitbucket"))

    assert "definitions:" not in body
    assert "services:" not in body
    assert "get-login-password" not in body
    # The notify body carries the bare commit SHA — no export, no blank tag.
    assert '\\"*\\":\\"$BITBUCKET_COMMIT\\"' in body
    assert "export ASTROLIFT_IMAGE" not in body
    assert "name: Notify Astrolift" in body


def test_gitea_deploy_only_drops_aws_and_build_steps(settings):
    """Gitea deploy-only drops the AWS-cred + build/push steps, keeps notify."""
    settings.PLATFORM_API_URL = "https://platform.astrolift.test"
    body = render_astrolift_gitea_ci_workflow(_app(registry_repo_uri="", source_kind="gitea"))

    assert "actions/checkout@v4" in body
    assert "Configure AWS credentials" not in body
    assert "Build and push image to ECR" not in body
    assert "get-login-password" not in body
    assert "Notify Astrolift" in body


# ---------------------------------------------------------------------------
# _apply_blocks — the tiny ``{% if %}`` block resolver behind the GitHub render.
# ---------------------------------------------------------------------------

# Mirrors the real template shape: a blank line lives INSIDE the block so the
# kept output is byte-identical to a template that never carried the markers.
_BLOCK_TPL = "a\n{% if x %}\n\nkept\n{% endif %}\nb\n"


def test_apply_blocks_keeps_inner_body_when_flag_true():
    assert _apply_blocks(_BLOCK_TPL, {"x": True}) == "a\n\nkept\nb\n"


def test_apply_blocks_drops_whole_block_when_flag_false():
    assert _apply_blocks(_BLOCK_TPL, {"x": False}) == "a\nb\n"


def test_apply_blocks_leaves_unknown_flag_untouched():
    assert _apply_blocks(_BLOCK_TPL, {"other": True}) == _BLOCK_TPL


def test_apply_blocks_ignores_github_actions_expressions():
    """``${{ … }}`` must survive — only ``{% … %}`` blocks are processed."""
    tpl = "run: echo ${{ github.sha }}\n{% if x %}\nbuild\n{% endif %}\n"
    assert _apply_blocks(tpl, {"x": False}) == "run: echo ${{ github.sha }}\n"
