"""Deploy refuses GPU workloads a probed cluster cannot schedule (#2038)."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from astrolift_manifest.types import WorkloadManifest
from core.app_deploy import gpu_capacity_refusal

GPU = {"nodes": [{"gpus": {"nvidia.com/gpu": 4}, "mig": {}}, {"gpus": {}, "mig": {"3g.47gb": 2}}]}


def _manifest(**gpu):
    return SimpleNamespace(
        workloads=(dataclasses.replace(WorkloadManifest(name="llm", kind="deployment"), **gpu),)
    )


def _cluster(gpu, **provider_config):
    return SimpleNamespace(slug="c1", capabilities={"gpu": gpu}, provider_config=provider_config)


def test_fits_on_one_node():
    assert gpu_capacity_refusal(_manifest(gpu=4), _cluster(GPU)) is None
    assert gpu_capacity_refusal(_manifest(gpu=2, mig_profile="3g.47gb"), _cluster(GPU)) is None


def test_more_gpus_than_any_one_node_is_refused():
    assert "no node" in gpu_capacity_refusal(_manifest(gpu=5), _cluster(GPU))
    assert gpu_capacity_refusal(_manifest(gpu=1, mig_profile="1g.10gb"), _cluster(GPU))


def test_unprobed_autoprovisioning_or_cpu_workloads_are_never_refused():
    assert gpu_capacity_refusal(_manifest(gpu=8), _cluster({})) is None
    assert gpu_capacity_refusal(_manifest(gpu=8), _cluster({"nodes": []}, gpu_autoprovision=True)) is None
    assert gpu_capacity_refusal(_manifest(), _cluster({"nodes": []})) is None
    assert "has more than 0" in gpu_capacity_refusal(_manifest(gpu=1), _cluster({"nodes": []}))
