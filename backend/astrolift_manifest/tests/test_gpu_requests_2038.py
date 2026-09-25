"""Workloads request GPUs (#2038)."""

from __future__ import annotations

import pytest

from astrolift_manifest.parser import ManifestError, parse_raw

from .test_render import _container, _deployment_workload, _render


def _toml(extra: str) -> str:
    return f"""
name = "hello"

[[workloads]]
name = "llm"
kind = "deployment"
{extra}

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8000
"""


def test_gpu_fields_parse():
    w = parse_raw(_toml('gpu = 2\ngpu_type = "nvidia-l4"')).workloads[0]
    assert (w.gpu, w.gpu_type, w.mig_profile) == (2, "nvidia-l4", None)
    w = parse_raw(_toml('gpu = 1\nmig_profile = "3g.47gb"')).workloads[0]
    assert (w.gpu, w.mig_profile) == (1, "3g.47gb")
    assert parse_raw(_toml("")).workloads[0].gpu == 0


@pytest.mark.parametrize(
    "extra",
    [
        "gpu = -1",
        "gpu = 17",
        'gpu = "2"',
        "gpu = true",
        'gpu_type = "l4"',
        'gpu = 1\nmig_profile = "big"',
        'gpu = 1\ngpu_type = "a b"',
    ],
)
def test_invalid_gpu_fields_are_refused(extra):
    with pytest.raises(ManifestError):
        parse_raw(_toml(extra))


def _gpu_workload(**gpu):
    import dataclasses

    sidecar = _container("proxy", is_primary=False, port=9000)
    return dataclasses.replace(_deployment_workload(containers=(_container(), sidecar)), **gpu)


def _pod(out):
    return next(r for r in out if r["kind"] == "Deployment")["spec"]["template"]["spec"]


def test_gpus_go_on_the_primary_container_only():
    pod = _pod(_render((_gpu_workload(gpu=2),)))
    primary, sidecar = pod["containers"]
    assert primary["resources"]["limits"]["nvidia.com/gpu"] == "2"
    assert "nvidia.com/gpu" not in sidecar.get("resources", {}).get("limits", {})
    assert "affinity" not in pod


def test_a_mig_profile_requests_slices_and_a_gpu_type_pins_the_node():
    pod = _pod(_render((_gpu_workload(gpu=1, mig_profile="3g.47gb", gpu_type="NVIDIA-H100-NVL"),)))
    assert pod["containers"][0]["resources"]["limits"]["nvidia.com/mig-3g.47gb"] == "1"
    terms = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ]
    assert {t["matchExpressions"][0]["key"] for t in terms} == {
        "nvidia.com/gpu.product",
        "cloud.google.com/gke-accelerator",
    }
    assert all(t["matchExpressions"][0]["values"] == ["NVIDIA-H100-NVL"] for t in terms)


def test_a_workload_without_gpus_renders_as_before():
    pod = _pod(_render((_deployment_workload(),)))
    assert "affinity" not in pod
    assert not any(k.startswith("nvidia.com/") for k in pod["containers"][0]["resources"].get("limits", {}))


def test_only_gpu_workloads_tolerate_the_gpu_pool_taints():
    """#2039: GPU pools are tainted so CPU workloads never land there; a GPU
    workload tolerates the taint GKE sets and the platform's own."""
    gpu_pod = _pod(_render((_gpu_workload(gpu=1),)))
    cpu_pod = _pod(_render((_deployment_workload(),)))
    assert {t["key"] for t in gpu_pod["tolerations"]} == {"nvidia.com/gpu", "astrolift.io/gpu"}
    assert all(t["operator"] == "Exists" and t["effect"] == "NoSchedule" for t in gpu_pod["tolerations"])
    assert "tolerations" not in cpu_pod
