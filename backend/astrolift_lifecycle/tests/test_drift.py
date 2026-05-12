"""Tests for drift detection comparator (#12, spec 07 §7)."""

from __future__ import annotations

from astrolift_lifecycle.drift import (
    DriftKind,
    WorkloadExpected,
    WorkloadObserved,
    build_report,
    compare_ingress,
    compare_workload,
    should_redeploy,
)


def _expected(**kw) -> WorkloadExpected:
    base = {
        "name": "api",
        "image_tag": "api:1.0",
        "replicas": 3,
        "env_literals": {"FOO": "bar"},
        "resources": {"cpu_request": "100m", "memory_limit": "512Mi"},
        "annotations": {"team": "platform"},
        "labels": {"app": "api"},
    }
    base.update(kw)
    return WorkloadExpected(**base)


def _observed(**kw) -> WorkloadObserved:
    base = {
        "name": "api",
        "image_tag": "api:1.0",
        "replicas": 3,
        "env_literals": {"FOO": "bar"},
        "resources": {"cpu_request": "100m", "memory_limit": "512Mi"},
        "annotations": {"team": "platform"},
        "labels": {"app": "api"},
        "hpa_active": False,
    }
    base.update(kw)
    return WorkloadObserved(**base)


# ---- happy path ----------------------------------------------------


def test_no_drift_returns_empty():
    drifts = compare_workload(expected=_expected(), observed=_observed())
    assert drifts == ()


# ---- image_tag drift -----------------------------------------------


def test_image_tag_drift():
    drifts = compare_workload(
        expected=_expected(image_tag="api:1.0"),
        observed=_observed(image_tag="api:1.1"),
    )
    assert len(drifts) == 1
    assert drifts[0].kind == DriftKind.IMAGE_TAG
    assert drifts[0].expected == "api:1.0"
    assert drifts[0].actual == "api:1.1"


# ---- replicas drift + HPA exception --------------------------------


def test_replicas_drift_when_hpa_off():
    drifts = compare_workload(
        expected=_expected(replicas=3),
        observed=_observed(replicas=5, hpa_active=False),
    )
    assert any(d.kind == DriftKind.REPLICAS for d in drifts)


def test_no_replicas_drift_when_hpa_active():
    """Critical: when HPA is on, the comparator must NOT flag
    drift on replicas — HPA legitimately scales the pod count."""
    drifts = compare_workload(
        expected=_expected(replicas=3),
        observed=_observed(replicas=8, hpa_active=True),
    )
    assert not any(d.kind == DriftKind.REPLICAS for d in drifts)


# ---- env / resources / annotations / labels diffs ------------------


def test_env_literal_diff_changed_key():
    drifts = compare_workload(
        expected=_expected(env_literals={"FOO": "bar"}),
        observed=_observed(env_literals={"FOO": "baz"}),
    )
    env_diffs = [d for d in drifts if d.kind == DriftKind.ENV_LITERAL]
    assert len(env_diffs) == 1
    assert env_diffs[0].expected == "bar"
    assert env_diffs[0].actual == "baz"


def test_env_literal_diff_added_key_in_cluster():
    """Operator added an env var via kubectl edit — drift."""
    drifts = compare_workload(
        expected=_expected(env_literals={"FOO": "bar"}),
        observed=_observed(env_literals={"FOO": "bar", "EXTRA": "leaked"}),
    )
    env_diffs = [d for d in drifts if d.kind == DriftKind.ENV_LITERAL]
    assert len(env_diffs) == 1
    assert env_diffs[0].field_path.endswith(".EXTRA")
    assert env_diffs[0].expected is None  # didn't exist in DB
    assert env_diffs[0].actual == "leaked"


def test_env_literal_diff_removed_in_cluster():
    """Cluster lost an env var — drift the other direction."""
    drifts = compare_workload(
        expected=_expected(env_literals={"FOO": "bar", "BAZ": "qux"}),
        observed=_observed(env_literals={"FOO": "bar"}),
    )
    env_diffs = [d for d in drifts if d.kind == DriftKind.ENV_LITERAL]
    assert len(env_diffs) == 1
    assert env_diffs[0].expected == "qux"
    assert env_diffs[0].actual is None


def test_resources_diff():
    drifts = compare_workload(
        expected=_expected(resources={"cpu_request": "100m"}),
        observed=_observed(resources={"cpu_request": "500m"}),
    )
    res_diffs = [d for d in drifts if d.kind == DriftKind.RESOURCES]
    assert len(res_diffs) == 1


def test_annotations_and_labels_diff():
    drifts = compare_workload(
        expected=_expected(
            annotations={"team": "platform"},
            labels={"app": "api"},
        ),
        observed=_observed(
            annotations={"team": "platform", "rogue": "drift"},
            labels={"app": "api", "env": "prod"},
        ),
    )
    ann = [d for d in drifts if d.kind == DriftKind.ANNOTATIONS]
    lab = [d for d in drifts if d.kind == DriftKind.LABELS]
    assert len(ann) == 1
    assert len(lab) == 1


# ---- ingress -------------------------------------------------------


def test_ingress_hostname_drift():
    out = compare_ingress(
        expected_hostname="api.acme.com",
        observed_hostname="api-old.acme.com",
        rule_id=42,
    )
    assert len(out) == 1
    assert out[0].kind == DriftKind.INGRESS_HOSTNAME
    assert out[0].field_path == "ingress[42].hostname"


def test_ingress_no_drift_when_match():
    out = compare_ingress(
        expected_hostname="api.acme.com",
        observed_hostname="api.acme.com",
        rule_id=42,
    )
    assert out == ()


# ---- aggregation ---------------------------------------------------


def test_build_report_combines_workload_and_ingress():
    workload_drifts = compare_workload(
        expected=_expected(image_tag="api:1.0"),
        observed=_observed(image_tag="api:1.1"),
    )
    ingress_drifts = compare_ingress(
        expected_hostname="api.acme.com",
        observed_hostname="old.acme.com",
        rule_id=1,
    )
    report = build_report(
        deployment_id=42,
        workload_diffs=workload_drifts,
        ingress_diffs=ingress_drifts,
        auto_correct=True,
    )
    assert report.deployment_id == 42
    assert report.is_drifted is True
    assert DriftKind.IMAGE_TAG in report.kinds
    assert DriftKind.INGRESS_HOSTNAME in report.kinds


def test_should_redeploy_when_drifted_and_auto_correct():
    report = build_report(
        deployment_id=1,
        workload_diffs=compare_workload(
            expected=_expected(image_tag="a"),
            observed=_observed(image_tag="b"),
        ),
        auto_correct=True,
    )
    assert should_redeploy(report) is True


def test_no_redeploy_when_clean_even_with_auto_correct():
    report = build_report(
        deployment_id=1,
        workload_diffs=(),
        auto_correct=True,
    )
    assert should_redeploy(report) is False


def test_no_redeploy_without_auto_correct():
    """Drift detected but auto_correct=False → emit event but
    don't kick redeploy. UI surfaces a 'drift' badge."""
    report = build_report(
        deployment_id=1,
        workload_diffs=compare_workload(
            expected=_expected(image_tag="a"),
            observed=_observed(image_tag="b"),
        ),
        auto_correct=False,
    )
    assert should_redeploy(report) is False
    assert report.is_drifted is True  # event still emitted
