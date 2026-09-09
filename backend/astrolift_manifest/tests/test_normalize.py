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


def test_port_less_container_keeps_no_healthcheck():
    # #1033: a container with no port must NOT get the default http
    # healthcheck — there is nothing to probe over http, and a defaulted
    # http probe would CrashLoop a port-less worker.
    raw = RawManifest(
        name="hello",
        workloads=(
            WorkloadManifest(
                name="worker",
                kind="deployment",
                containers=(ContainerManifest(name="app", is_primary=True, port=0),),
            ),
        ),
    )
    n = normalize(raw)
    c = n.workloads[0].containers[0]
    assert c.healthcheck_kind == "none"
    assert c.healthcheck_value == ""
    assert c.healthcheck_port is None
    assert "workload.worker.containers[app].healthcheck.kind" not in n.defaults_applied


def test_ported_container_gets_default_http_healthcheck():
    # Regression guard for #1033: a real port still receives the default
    # http healthcheck (the fix is gated on port==0, not all containers).
    raw = RawManifest(
        name="hello",
        workloads=(
            WorkloadManifest(
                name="web",
                kind="deployment",
                containers=(ContainerManifest(name="app", is_primary=True, port=8080),),
            ),
        ),
    )
    n = normalize(raw)
    c = n.workloads[0].containers[0]
    assert c.healthcheck_kind == "http"
    assert c.healthcheck_value == "/health"
    assert "workload.web.containers[app].healthcheck.kind" in n.defaults_applied


def test_faas_public_implies_is_public():
    # #1035: faas_public is the public switch for a faas workload -- normalize
    # implies is_public from it so the hostname/alias/CNAME machinery (all keyed
    # on is_public) fires. Falsifiable: dropping the implication leaves
    # is_public False and the public surface never resolves.
    raw = RawManifest(
        name="hello",
        workloads=(WorkloadManifest(name="api", kind="faas", faas_public=True),),
    )
    n = normalize(raw)
    assert n.workloads[0].is_public is True
    assert "workload.api.is_public" in n.defaults_applied


def test_faas_without_public_stays_private():
    # A private faas (no faas_public) must NOT be made public.
    raw = RawManifest(
        name="hello",
        workloads=(WorkloadManifest(name="api", kind="faas", faas_public=False),),
    )
    n = normalize(raw)
    assert n.workloads[0].is_public is False
    assert "workload.api.is_public" not in n.defaults_applied


def _raw_with_workload(**kw) -> RawManifest:
    return RawManifest(
        name="hello",
        workloads=(
            WorkloadManifest(
                name="web",
                kind="statefulset",
                containers=(ContainerManifest(name="app", is_primary=True),),
                **kw,
            ),
        ),
    )


def test_manifest_hash_changes_when_a_volume_is_declared():
    # A deploy whose only change is a [[workloads.volumes]] entry must not
    # hash identically to the previous one, or it collapses to a no-op and
    # the claim the operator just asked for is never rendered.
    bare = normalize(_raw_with_workload())
    with_vol = normalize(
        _raw_with_workload(
            volumes=({"name": "data", "kind": "pvc", "mount_path": "/app/data", "size": "5Gi"},)
        )
    )
    assert manifest_hash(bare.serialized) != manifest_hash(with_vol.serialized)


def test_manifest_hash_changes_when_volume_size_changes():
    small = normalize(_raw_with_workload(volumes=({"name": "data", "kind": "pvc", "size": "5Gi"},)))
    big = normalize(_raw_with_workload(volumes=({"name": "data", "kind": "pvc", "size": "20Gi"},)))
    assert manifest_hash(small.serialized) != manifest_hash(big.serialized)


def test_manifest_hash_changes_with_fs_group():
    a = normalize(_raw_with_workload(fs_group=1000))
    b = normalize(_raw_with_workload(fs_group=2000))
    assert manifest_hash(a.serialized) != manifest_hash(b.serialized)


def test_manifest_hash_ignores_volume_key_order():
    # Key order in the TOML must not move the hash, or an unrelated reformat
    # reads as a real change and forces a redeploy.
    a = normalize(_raw_with_workload(volumes=({"name": "data", "kind": "pvc", "size": "5Gi"},)))
    b = normalize(_raw_with_workload(volumes=({"size": "5Gi", "kind": "pvc", "name": "data"},)))
    assert manifest_hash(a.serialized) == manifest_hash(b.serialized)
