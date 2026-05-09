"""Tests for delivery strategy dispatch (#2, spec 07 §2)."""

from __future__ import annotations

import pytest

from astrolift_clusters.delivery import (
    DeliveryConfigError,
    DeliveryMode,
    DeliveryPlan,
    DirectApplyAction,
    GitOpsCommitAction,
    GitOpsConfig,
    plan_delivery,
)


def _gitops() -> GitOpsConfig:
    return GitOpsConfig(
        repo_url="git@github.com:acme/platform.git",
        branch="main",
        path_prefix="clusters/prod-us-east",
    )


def _deployment() -> dict:
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "api"}}


def _crd() -> dict:
    return {"apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition"}


def _cluster_role() -> dict:
    return {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole"}


def _namespace() -> dict:
    return {"apiVersion": "v1", "kind": "Namespace"}


# ---- direct_api ----------------------------------------------------


def test_direct_api_plan_routes_everything_to_apiserver():
    plan = plan_delivery(
        mode=DeliveryMode.DIRECT_API,
        objects=[_deployment(), _crd()],
    )
    assert plan.direct is not None
    assert plan.gitops is None
    assert len(plan.direct.objects) == 2


def test_direct_api_does_not_require_gitops_config():
    plan_delivery(mode=DeliveryMode.DIRECT_API, objects=[_deployment()])


# ---- pure GitOps modes ---------------------------------------------


def test_argocd_routes_everything_to_git():
    plan = plan_delivery(
        mode=DeliveryMode.GITOPS_ARGOCD,
        objects=[_deployment(), _crd()],
        gitops_config=_gitops(),
    )
    assert plan.direct is None
    assert plan.gitops is not None
    assert len(plan.gitops.objects) == 2
    assert plan.gitops.syncer == "argocd"


def test_flux_routes_everything_to_git():
    plan = plan_delivery(
        mode=DeliveryMode.GITOPS_FLUX,
        objects=[_deployment()],
        gitops_config=_gitops(),
    )
    assert plan.gitops.syncer == "flux"


def test_gitops_modes_require_config():
    for mode in (DeliveryMode.GITOPS_ARGOCD, DeliveryMode.GITOPS_FLUX):
        with pytest.raises(DeliveryConfigError, match="gitops_config"):
            plan_delivery(mode=mode, objects=[_deployment()])


def test_gitops_config_construction_guards():
    """Catch admin UI typos before they reach the syncer."""
    with pytest.raises(DeliveryConfigError, match="repo_url"):
        GitOpsConfig(repo_url="", branch="main", path_prefix="x")
    with pytest.raises(DeliveryConfigError, match="branch"):
        GitOpsConfig(repo_url="git@x", branch="", path_prefix="x")


# ---- hybrid --------------------------------------------------------


def test_hybrid_splits_workloads_and_platform_objects():
    """Hybrid should route Deployments to apiserver and CRDs/RBAC
    to git in the same pass."""
    plan = plan_delivery(
        mode=DeliveryMode.HYBRID,
        objects=[_deployment(), _crd(), _cluster_role(), _namespace()],
        gitops_config=_gitops(),
    )
    assert plan.direct is not None
    assert plan.gitops is not None
    direct_kinds = {o["kind"] for o in plan.direct.objects}
    gitops_kinds = {o["kind"] for o in plan.gitops.objects}
    assert direct_kinds == {"Deployment"}
    assert gitops_kinds == {
        "CustomResourceDefinition", "ClusterRole", "Namespace",
    }


def test_hybrid_only_workloads_no_gitops_action():
    """Hybrid with no platform-level objects shouldn't emit a
    pointless empty git commit."""
    plan = plan_delivery(
        mode=DeliveryMode.HYBRID,
        objects=[_deployment()],
        gitops_config=_gitops(),
    )
    assert plan.direct is not None
    assert plan.gitops is None


def test_hybrid_only_workloads_doesnt_require_gitops_config():
    """If no platform-level objects → no GitOps action → config
    isn't strictly needed. Operators get fewer foot-guns."""
    plan = plan_delivery(
        mode=DeliveryMode.HYBRID,
        objects=[_deployment()],
        gitops_config=None,
    )
    assert plan.direct is not None
    assert plan.gitops is None


def test_hybrid_with_platform_objects_requires_gitops_config():
    with pytest.raises(DeliveryConfigError, match="gitops_config"):
        plan_delivery(
            mode=DeliveryMode.HYBRID,
            objects=[_deployment(), _crd()],
            gitops_config=None,
        )


def test_hybrid_only_platform_objects_no_direct_action():
    plan = plan_delivery(
        mode=DeliveryMode.HYBRID,
        objects=[_crd(), _namespace()],
        gitops_config=_gitops(),
    )
    assert plan.direct is None
    assert plan.gitops is not None
    assert plan.gitops.syncer == "argocd"


def test_unknown_mode_raises():
    with pytest.raises(DeliveryConfigError, match="unsupported"):
        plan_delivery(mode="ftp_upload", objects=[])  # type: ignore[arg-type]
