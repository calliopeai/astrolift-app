"""Tests for preview build policy (#85, spec 18 §5-6)."""

from __future__ import annotations

import pytest

from astrolift_workflows.preview_build import (
    BUILD_ORDER,
    DEFAULT_PREVIEW_HPA_ENABLED,
    DEFAULT_PREVIEW_REPLICAS,
    DEFAULT_RESOURCE_SCALE,
    BuildStep,
    CostContainment,
    ExistingPreview,
    PreviewBuildError,
    apply_cost_containment,
    env_slug_for_preview,
    hostname_for_preview,
    is_update_flow,
    namespace_for_preview,
    pr_comment_for_failure,
    pr_comment_for_success,
    steps_for_outcome,
)

# ---- step ordering -------------------------------------------------


def test_build_order_locked():
    """Spec §5: step order is part of the contract — out-of-order
    execution can leak resources or deploy before namespace exists."""
    assert BUILD_ORDER == (
        BuildStep.UPSERT_PREVIEW,
        BuildStep.ENSURE_NAMESPACE,
        BuildStep.SYNTHESIZE_ENV,
        BuildStep.APPLY_COST_CONTAINMENT,
        BuildStep.PROVISION_MANAGED_SERVICES,
        BuildStep.RUN_DEPLOY,
    )


def test_steps_for_success_includes_mark_running():
    steps = steps_for_outcome(deploy_succeeded=True)
    assert BuildStep.MARK_RUNNING in steps
    assert BuildStep.MARK_FAILED not in steps
    assert steps[-1] == BuildStep.COMMENT_PR


def test_steps_for_failure_includes_mark_failed():
    steps = steps_for_outcome(deploy_succeeded=False)
    assert BuildStep.MARK_FAILED in steps
    assert BuildStep.MARK_RUNNING not in steps
    assert steps[-1] == BuildStep.COMMENT_PR


# ---- naming conventions --------------------------------------------


def test_namespace_basic():
    """Spec §5: ``<org>-<app>-pr-<n>``."""
    ns = namespace_for_preview(
        org_slug="acme",
        app_slug="api",
        pr_number=42,
    )
    assert ns == "acme-api-pr-42"


def test_namespace_truncates_long_app_slug():
    """K8s namespace max = 63 chars. Truncate app slug to fit."""
    ns = namespace_for_preview(
        org_slug="acme",
        app_slug="some-extremely-long-application-name-that-exceeds-limits",
        pr_number=42,
    )
    assert len(ns) <= 63
    assert ns.startswith("acme-")
    assert ns.endswith("-pr-42")


def test_namespace_rejects_too_long_org():
    """If even with max truncation the org_slug doesn't fit, raise."""
    with pytest.raises(PreviewBuildError):
        namespace_for_preview(
            org_slug="x" * 60,  # leaves no room for app + suffix
            app_slug="api",
            pr_number=99999,
        )


def test_namespace_rejects_empty_inputs():
    with pytest.raises(PreviewBuildError):
        namespace_for_preview(org_slug="", app_slug="api", pr_number=1)
    with pytest.raises(PreviewBuildError):
        namespace_for_preview(org_slug="acme", app_slug="", pr_number=1)
    with pytest.raises(PreviewBuildError):
        namespace_for_preview(org_slug="acme", app_slug="api", pr_number=0)


def test_env_slug_format():
    assert env_slug_for_preview(pr_number=42) == "preview-pr-42"


def test_env_slug_rejects_zero_or_negative():
    with pytest.raises(PreviewBuildError):
        env_slug_for_preview(pr_number=0)
    with pytest.raises(PreviewBuildError):
        env_slug_for_preview(pr_number=-1)


def test_hostname_under_preview_wildcard():
    """Pairs with #70 wildcard SAN ``*.pr.<org>.<base_zone>``."""
    host = hostname_for_preview(
        pr_number=42,
        base_zone="apps.platform.example",
        org_slug="acme",
    )
    assert host == "pr-42.acme.apps.platform.example"


def test_hostname_lowercases():
    """RFC 1035 — DNS labels are case-insensitive; canonicalize."""
    host = hostname_for_preview(
        pr_number=42,
        base_zone="APPS.PLATFORM.EXAMPLE",
        org_slug="ACME",
    )
    assert host.islower()


# ---- cost containment ----------------------------------------------


def test_cost_containment_defaults():
    """Spec §6 defaults locked."""
    assert DEFAULT_RESOURCE_SCALE == 0.5
    assert DEFAULT_PREVIEW_REPLICAS == 1
    assert DEFAULT_PREVIEW_HPA_ENABLED is False


def test_cost_containment_resource_scale_bounds():
    """Scale > 1 = inflation (the opposite of containment); refuse."""
    with pytest.raises(PreviewBuildError):
        CostContainment(resource_scale=1.5)
    with pytest.raises(PreviewBuildError):
        CostContainment(resource_scale=0.0)
    with pytest.raises(PreviewBuildError):
        CostContainment(resource_scale=-0.1)


def test_cost_containment_replicas_must_be_non_negative():
    with pytest.raises(PreviewBuildError):
        CostContainment(replicas=-1)


def test_apply_cost_containment_scales_cpu():
    """500m * 0.5 = 250m."""
    scaled = apply_cost_containment(
        workload_resources={"cpu_request": "500m", "cpu_limit": "1"},
    )
    assert scaled["cpu_request"] == "250m"


def test_apply_cost_containment_scales_memory():
    """1Gi * 0.5 = 0.5Gi (Mi conversion isn't done; preserves unit).
    Result formats as float-ish to keep the unit valid."""
    scaled = apply_cost_containment(
        workload_resources={"memory_request": "1024Mi"},
    )
    assert scaled["memory_request"] == "512Mi"


def test_apply_cost_containment_passes_unparsable():
    """Unknown format passes through. Workflow logs separately."""
    scaled = apply_cost_containment(
        workload_resources={"cpu_request": "not-a-quantity"},
    )
    assert scaled["cpu_request"] == "not-a-quantity"


def test_apply_cost_containment_minimum_one():
    """Don't round down to 0 — k8s rejects zero-resource."""
    scaled = apply_cost_containment(
        workload_resources={"cpu_request": "1m"},
    )
    # 1m * 0.5 = 0.5; floor would be 0, we clamp to 1.
    assert scaled["cpu_request"] == "1m"


def test_apply_cost_containment_custom_scale():
    cc = CostContainment(resource_scale=0.25)
    scaled = apply_cost_containment(
        workload_resources={"cpu_request": "1000m"},
        containment=cc,
    )
    assert scaled["cpu_request"] == "250m"


def test_apply_cost_containment_preserves_other_keys():
    """Non-resource keys pass through unchanged."""
    scaled = apply_cost_containment(
        workload_resources={
            "cpu_request": "500m",
            "ephemeral_storage": "1Gi",
        },
    )
    assert scaled["ephemeral_storage"] == "1Gi"


# ---- update vs create ----------------------------------------------


def test_is_update_flow_no_existing():
    assert is_update_flow(existing=None, new_commit_sha="abc") is False


def test_is_update_flow_new_sha():
    existing = ExistingPreview(
        preview_id=1,
        namespace="x",
        env_id=1,
        last_commit_sha="aaa",
    )
    assert is_update_flow(existing=existing, new_commit_sha="bbb") is True


def test_is_update_flow_same_sha():
    """Same SHA = re-delivery of the same webhook; not an update."""
    existing = ExistingPreview(
        preview_id=1,
        namespace="x",
        env_id=1,
        last_commit_sha="aaa",
    )
    assert is_update_flow(existing=existing, new_commit_sha="aaa") is False


# ---- PR comment templates ------------------------------------------


def test_pr_comment_success_includes_url():
    body = pr_comment_for_success(
        pr_number=42,
        preview_url="https://pr-42.acme.platform.example",
    )
    assert "Preview environment ready" in body
    assert "#42" in body
    assert "https://pr-42.acme.platform.example" in body


def test_pr_comment_failure_includes_logs():
    body = pr_comment_for_failure(
        pr_number=42,
        logs_url="https://app.platform/builds/42/logs",
    )
    assert "failed" in body.lower()
    assert "https://app.platform/builds/42/logs" in body


def test_pr_comment_failure_without_logs():
    body = pr_comment_for_failure(pr_number=42, logs_url="")
    assert "failed" in body.lower()
    assert "[View build logs]" not in body
