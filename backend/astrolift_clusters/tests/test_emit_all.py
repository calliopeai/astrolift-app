"""Tests for emit-all DR planning (#22, spec 07 §11)."""

from __future__ import annotations

from astrolift_clusters.emit_all import (
    GITOPS_MODES,
    ActiveDeployment,
    DeliveryMode,
    commits_per_cluster,
    plan_emit_all,
    repo_path_for,
)


def _dep(deployment_id: int, **kw) -> ActiveDeployment:
    base = dict(
        cluster_id=1,
        cluster_slug="prod-us-east",
        cluster_path_prefix="clusters/prod-us-east",
        cluster_delivery_mode=DeliveryMode.GITOPS_ARGOCD,
        app_slug="api",
        env_slug="prod",
    )
    base.update(kw)
    return ActiveDeployment(deployment_id=deployment_id, **base)


# ---- repo_path_for -------------------------------------------------


def test_repo_path_format():
    out = repo_path_for(
        path_prefix="clusters/prod-us-east",
        app_slug="api", env_slug="prod",
    )
    assert out == "clusters/prod-us-east/api/prod/manifests.yaml"


def test_repo_path_normalizes_trailing_slash():
    """A path_prefix with a stray trailing slash shouldn't produce
    a doubled separator."""
    out = repo_path_for(
        path_prefix="clusters/prod-us-east/",
        app_slug="api", env_slug="prod",
    )
    assert out == "clusters/prod-us-east/api/prod/manifests.yaml"


def test_repo_path_custom_filename():
    out = repo_path_for(
        path_prefix="clusters/x", app_slug="api", env_slug="prod",
        filename="kustomization.yaml",
    )
    assert out.endswith("/kustomization.yaml")


# ---- plan_emit_all -------------------------------------------------


def test_filters_out_direct_api():
    """Direct-apply clusters aren't represented in any repo."""
    deps = [
        _dep(1, cluster_delivery_mode=DeliveryMode.DIRECT_API),
        _dep(2, cluster_delivery_mode=DeliveryMode.GITOPS_ARGOCD),
    ]
    out = plan_emit_all(deps)
    assert len(out) == 1
    assert out[0].deployment_id == 2


def test_includes_argocd_flux_hybrid():
    deps = [
        _dep(1, cluster_delivery_mode=DeliveryMode.GITOPS_ARGOCD,
             app_slug="api", env_slug="prod"),
        _dep(2, cluster_delivery_mode=DeliveryMode.GITOPS_FLUX,
             cluster_slug="eu", cluster_path_prefix="clusters/eu",
             app_slug="api", env_slug="prod"),
        _dep(3, cluster_delivery_mode=DeliveryMode.HYBRID,
             cluster_slug="canary", cluster_path_prefix="clusters/canary",
             app_slug="api", env_slug="canary"),
    ]
    out = plan_emit_all(deps)
    assert len(out) == 3


def test_idempotent_ordering():
    """Same input → same output, byte-identical."""
    deps = [
        _dep(2, app_slug="api", env_slug="prod"),
        _dep(1, app_slug="api", env_slug="staging"),
        _dep(3, app_slug="other", env_slug="prod"),
    ]
    out_a = plan_emit_all(deps)
    out_b = plan_emit_all(deps)
    assert out_a == out_b
    # Sorted by (cluster, app, env) — every output has the same
    # cluster here, so app first then env
    paths = [t.repo_path for t in out_a]
    assert paths == sorted(paths)


def test_last_write_wins_for_duplicate_app_env():
    """During a partial rollout there might be more than one active
    Deployment for the same (cluster, app, env); the most recent
    (highest id) wins so the repo reflects current intent."""
    deps = [
        _dep(1, app_slug="api", env_slug="prod"),
        _dep(5, app_slug="api", env_slug="prod"),  # newer
        _dep(3, app_slug="api", env_slug="prod"),
    ]
    out = plan_emit_all(deps)
    assert len(out) == 1
    assert out[0].deployment_id == 5


def test_gitops_modes_set_pinned():
    """Lock-test: changing the set should be deliberate."""
    assert GITOPS_MODES == {
        DeliveryMode.GITOPS_ARGOCD,
        DeliveryMode.GITOPS_FLUX,
        DeliveryMode.HYBRID,
    }


# ---- commits_per_cluster -------------------------------------------


def test_commits_grouped_by_cluster():
    deps = [
        _dep(1, cluster_slug="us", cluster_path_prefix="clusters/us",
             app_slug="api", env_slug="prod"),
        _dep(2, cluster_slug="us", cluster_path_prefix="clusters/us",
             app_slug="api", env_slug="staging"),
        _dep(3, cluster_slug="eu", cluster_path_prefix="clusters/eu",
             app_slug="api", env_slug="prod"),
    ]
    plan = plan_emit_all(deps)
    grouped = commits_per_cluster(plan)
    assert set(grouped.keys()) == {"us", "eu"}
    assert len(grouped["us"]) == 2
    assert len(grouped["eu"]) == 1


def test_commits_empty_plan_returns_empty_dict():
    assert commits_per_cluster([]) == {}
