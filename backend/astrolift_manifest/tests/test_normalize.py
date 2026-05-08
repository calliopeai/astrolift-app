"""Tests for the manifest normalizer (#40)."""

from __future__ import annotations

from astrolift_manifest.normalize import (
    NormalizationDefaults,
    manifest_hash,
    normalize,
)
from astrolift_manifest.types import (
    ContainerManifest,
    RawManifest,
    WorkloadManifest,
)


def _raw_with_one_workload() -> RawManifest:
    return RawManifest(
        name="hello",
        workloads=(
            WorkloadManifest(
                name="web",
                kind="deployment",
                containers=(ContainerManifest(name="app", is_primary=True),),
            ),
        ),
    )


def test_defaults_fill_missing_resource_fields():
    raw = _raw_with_one_workload()
    n = normalize(raw)
    w = n.workloads[0]
    assert w.cpu_request == "100m"
    assert w.cpu_limit == "500m"
    assert w.memory_request == "128Mi"
    assert w.memory_limit == "512Mi"
    assert "workload.web.cpu_request" in n.defaults_applied
    assert "workload.web.memory_limit" in n.defaults_applied


def test_explicit_values_are_kept():
    raw = RawManifest(
        name="hello",
        workloads=(
            WorkloadManifest(
                name="web",
                kind="deployment",
                cpu_request="250m",
                memory_request="256Mi",
                containers=(ContainerManifest(name="app", is_primary=True),),
            ),
        ),
    )
    n = normalize(raw)
    w = n.workloads[0]
    assert w.cpu_request == "250m"
    assert w.memory_request == "256Mi"
    # cpu_limit + memory_limit still default-applied
    assert "workload.web.cpu_limit" in n.defaults_applied
    assert "workload.web.cpu_request" not in n.defaults_applied


def test_custom_defaults_take_effect():
    raw = _raw_with_one_workload()
    n = normalize(raw, defaults=NormalizationDefaults(cpu_request="50m"))
    assert n.workloads[0].cpu_request == "50m"


def test_serialized_shape_is_stable():
    raw = _raw_with_one_workload()
    n = normalize(raw)
    assert n.serialized["name"] == "hello"
    assert isinstance(n.serialized["workloads"], list)
    assert n.serialized["workloads"][0]["name"] == "web"


def test_manifest_hash_is_deterministic():
    raw = _raw_with_one_workload()
    n1 = normalize(raw)
    n2 = normalize(raw)
    assert manifest_hash(n1.serialized) == manifest_hash(n2.serialized)


def test_manifest_hash_changes_with_content():
    raw1 = _raw_with_one_workload()
    raw2 = RawManifest(
        name="hello",
        workloads=(
            WorkloadManifest(
                name="web",
                kind="deployment",
                cpu_request="999m",
                containers=(ContainerManifest(name="app", is_primary=True),),
            ),
        ),
    )
    h1 = manifest_hash(normalize(raw1).serialized)
    h2 = manifest_hash(normalize(raw2).serialized)
    assert h1 != h2
