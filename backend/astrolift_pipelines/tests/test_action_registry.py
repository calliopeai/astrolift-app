"""Tests for the built-in action stdlib and registry (#76).

All tests are pure (no DB) — actions are plain Python functions.
"""

from __future__ import annotations

import pytest

from astrolift_pipelines.actions import ActionInputError
from astrolift_pipelines.actions.registry import (
    UnknownActionError,
    list_actions,
    resolve_action,
)

# ---------------------------------------------------------------------------
# Registry resolution
# ---------------------------------------------------------------------------


def test_resolve_versioned_key():
    action = resolve_action("astrolift/git-checkout@v1")
    assert action.name == "astrolift/git-checkout"


def test_resolve_bare_name_uses_latest():
    action = resolve_action("astrolift/git-checkout")
    assert action.name == "astrolift/git-checkout"


def test_resolve_unknown_raises():
    with pytest.raises(UnknownActionError, match="Unknown built-in action"):
        resolve_action("unknown/not-a-thing@v1")


def test_list_actions_returns_all_four():
    actions = list_actions()
    names = {a.name for a in actions}
    assert names == {
        "astrolift/git-checkout",
        "astrolift/docker-build",
        "astrolift/kubectl-apply",
        "astrolift/astrolift-deploy",
    }


# ---------------------------------------------------------------------------
# git-checkout@v1
# ---------------------------------------------------------------------------


def test_git_checkout_renders_clone_and_checkout():
    action = resolve_action("astrolift/git-checkout@v1")
    steps = action.render_steps(
        with_params={"repository": "https://github.com/example/repo.git", "ref": "main"},
        env={},
        context={},
    )
    assert len(steps) == 2
    assert "git clone" in steps[0]["run"]
    assert "git checkout" in steps[1]["run"]
    assert "main" in steps[1]["run"]


def test_git_checkout_shallow_clone_default():
    action = resolve_action("astrolift/git-checkout@v1")
    steps = action.render_steps(
        with_params={"repository": "https://github.com/example/repo.git", "ref": "abc"},
        env={},
        context={},
    )
    assert "--depth=1" in steps[0]["run"]


def test_git_checkout_full_history_when_depth_zero():
    action = resolve_action("astrolift/git-checkout@v1")
    steps = action.render_steps(
        with_params={
            "repository": "https://github.com/example/repo.git",
            "ref": "abc",
            "fetch_depth": 0,
        },
        env={},
        context={},
    )
    assert "--depth=" not in steps[0]["run"]


def test_git_checkout_injects_token_into_https_url():
    action = resolve_action("astrolift/git-checkout@v1")
    steps = action.render_steps(
        with_params={
            "repository": "https://github.com/example/private.git",
            "ref": "main",
            "token": "ghp_secret",
        },
        env={},
        context={},
    )
    # Token must appear in the clone URL.
    assert "x-token:ghp_secret@" in steps[0]["run"]


def test_git_checkout_missing_required_input():
    action = resolve_action("astrolift/git-checkout@v1")
    with pytest.raises(ActionInputError, match="required input 'repository'"):
        action.render_steps(with_params={"ref": "main"}, env={}, context={})


# ---------------------------------------------------------------------------
# docker-build@v1
# ---------------------------------------------------------------------------


def test_docker_build_renders_buildx_command():
    action = resolve_action("astrolift/docker-build@v1")
    steps = action.render_steps(
        with_params={"tags": "registry/app:latest"},
        env={},
        context={},
    )
    assert len(steps) == 1
    run = steps[0]["run"]
    assert "docker buildx build" in run
    assert "registry/app:latest" in run
    assert steps[0]["env"]["DOCKER_BUILDKIT"] == "1"


def test_docker_build_multiple_tags():
    action = resolve_action("astrolift/docker-build@v1")
    steps = action.render_steps(
        with_params={"tags": ["registry/app:sha123", "registry/app:latest"]},
        env={},
        context={},
    )
    run = steps[0]["run"]
    assert "registry/app:sha123" in run
    assert "registry/app:latest" in run


def test_docker_build_push_flag():
    action = resolve_action("astrolift/docker-build@v1")
    steps = action.render_steps(
        with_params={"tags": "registry/app:latest", "push": True},
        env={},
        context={},
    )
    assert "--push" in steps[0]["run"]


def test_docker_build_no_push_by_default():
    action = resolve_action("astrolift/docker-build@v1")
    steps = action.render_steps(
        with_params={"tags": "registry/app:latest"},
        env={},
        context={},
    )
    assert "--push" not in steps[0]["run"]


def test_docker_build_build_args():
    action = resolve_action("astrolift/docker-build@v1")
    steps = action.render_steps(
        with_params={"tags": "registry/app:latest", "build_args": {"ENV": "prod"}},
        env={},
        context={},
    )
    assert "--build-arg" in steps[0]["run"]
    assert "ENV=prod" in steps[0]["run"]


def test_docker_build_missing_tags():
    action = resolve_action("astrolift/docker-build@v1")
    with pytest.raises(ActionInputError, match="required input 'tags'"):
        action.render_steps(with_params={}, env={}, context={})


# ---------------------------------------------------------------------------
# kubectl-apply@v1
# ---------------------------------------------------------------------------


def test_kubectl_apply_renders_apply_command():
    action = resolve_action("astrolift/kubectl-apply@v1")
    steps = action.render_steps(
        with_params={"manifest": "k8s/deployment.yaml"},
        env={},
        context={},
    )
    assert len(steps) == 1
    run = steps[0]["run"]
    assert "kubectl apply -f" in run
    assert "k8s/deployment.yaml" in run


def test_kubectl_apply_namespace_flag():
    action = resolve_action("astrolift/kubectl-apply@v1")
    steps = action.render_steps(
        with_params={"manifest": "k8s/", "namespace": "production"},
        env={},
        context={},
    )
    assert "--namespace=production" in steps[0]["run"]


def test_kubectl_apply_no_namespace_by_default():
    action = resolve_action("astrolift/kubectl-apply@v1")
    steps = action.render_steps(
        with_params={"manifest": "k8s/"},
        env={},
        context={},
    )
    assert "--namespace" not in steps[0]["run"]


def test_kubectl_apply_cluster_metadata():
    action = resolve_action("astrolift/kubectl-apply@v1")
    steps = action.render_steps(
        with_params={"manifest": "k8s/", "cluster": "prod-cluster"},
        env={},
        context={},
    )
    assert steps[0].get("cluster") == "prod-cluster"


def test_kubectl_apply_missing_manifest():
    action = resolve_action("astrolift/kubectl-apply@v1")
    with pytest.raises(ActionInputError, match="required input 'manifest'"):
        action.render_steps(with_params={}, env={}, context={})


# ---------------------------------------------------------------------------
# astrolift-deploy@v1
# ---------------------------------------------------------------------------


def test_astrolift_deploy_renders_astro_command():
    action = resolve_action("astrolift/astrolift-deploy@v1")
    steps = action.render_steps(
        with_params={
            "app_slug": "my-app",
            "environment": "production",
            "image_tag": "registry/app:abc1234",
        },
        env={},
        context={},
    )
    assert len(steps) == 1
    run = steps[0]["run"]
    assert "astro deploy" in run
    assert "my-app" in run
    assert "--environment=production" in run
    assert "registry/app:abc1234" in run


def test_astrolift_deploy_cluster_flag():
    action = resolve_action("astrolift/astrolift-deploy@v1")
    steps = action.render_steps(
        with_params={
            "app_slug": "my-app",
            "environment": "production",
            "image_tag": "registry/app:abc1234",
            "cluster": "prod-cluster",
        },
        env={},
        context={},
    )
    assert "--cluster=prod-cluster" in steps[0]["run"]


def test_astrolift_deploy_no_cluster_flag_by_default():
    action = resolve_action("astrolift/astrolift-deploy@v1")
    steps = action.render_steps(
        with_params={
            "app_slug": "my-app",
            "environment": "production",
            "image_tag": "registry/app:abc1234",
        },
        env={},
        context={},
    )
    assert "--cluster" not in steps[0]["run"]


def test_astrolift_deploy_missing_app_slug():
    action = resolve_action("astrolift/astrolift-deploy@v1")
    with pytest.raises(ActionInputError, match="required input 'app_slug'"):
        action.render_steps(
            with_params={"environment": "production", "image_tag": "registry/app:latest"},
            env={},
            context={},
        )
