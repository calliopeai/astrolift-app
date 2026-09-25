"""GPU capacity discovery from node objects (#2038)."""

from __future__ import annotations

from k8s_native.gpu import node_gpu_facts, summarize


def _node(name, *, allocatable=None, labels=None, taints=None):
    return {
        "metadata": {"name": name, "labels": labels or {}},
        "spec": {"taints": taints or []},
        "status": {"allocatable": {"cpu": "8", "memory": "32Gi", **(allocatable or {})}},
    }


A100 = _node(
    "gpu-a",
    allocatable={"nvidia.com/gpu": "8"},
    labels={
        "nvidia.com/gpu.product": "NVIDIA-A100-SXM4-40GB",
        "nvidia.com/gpu.memory": "40960",
        "eks.amazonaws.com/nodegroup": "gpu-p4d",
        "kubernetes.io/hostname": "ignored",
    },
    taints=[{"key": "nvidia.com/gpu", "value": "true", "effect": "NoSchedule"}],
)
H100_MIG = _node(
    "gpu-h",
    allocatable={"nvidia.com/mig-3g.47gb": "2"},
    labels={"nvidia.com/mig.config": "all-3g.47gb", "agentpool": "gpupool"},
)
GKE_L4 = _node(
    "gke-l4",
    allocatable={"nvidia.com/gpu": "1"},
    labels={"cloud.google.com/gke-accelerator": "nvidia-l4", "cloud.google.com/gke-nodepool": "l4-pool"},
)
CPU = _node("cpu-1", labels={"eks.amazonaws.com/nodegroup": "general"})


def test_a_node_without_gpu_or_mig_capacity_has_no_facts():
    assert node_gpu_facts(CPU) is None
    assert node_gpu_facts(_node("zero", allocatable={"nvidia.com/gpu": "0"})) is None


def test_facts_keep_gpus_mig_pool_labels_and_taints():
    facts = node_gpu_facts(A100)
    assert facts["gpus"] == {"nvidia.com/gpu": 8}
    assert facts["pool"] == "gpu-p4d"
    assert facts["labels"] == {"nvidia.com/gpu.product": "NVIDIA-A100-SXM4-40GB", "nvidia.com/gpu.memory": "40960"}
    assert facts["taints"] == [{"key": "nvidia.com/gpu", "value": "true", "effect": "NoSchedule"}]
    assert node_gpu_facts(H100_MIG)["mig"] == {"3g.47gb": 2}


def test_summary_totals_products_and_gpu_stack():
    pods = [{"name": "nvidia-device-plugin-daemonset-x1"}, {"name": "gpu-operator-6f"}, {"name": "web-1"}]
    gpu = summarize([A100, H100_MIG, GKE_L4, CPU], pods)
    assert [n["name"] for n in gpu["nodes"]] == ["gpu-a", "gpu-h", "gke-l4"]
    assert gpu["total"] == {"nvidia.com/gpu": 9}
    assert gpu["mig_total"] == {"3g.47gb": 2}
    assert gpu["products"] == ["NVIDIA-A100-SXM4-40GB", "nvidia-l4"]
    assert (gpu["device_plugin"], gpu["gpu_operator"], gpu["dcgm_exporter"]) == (True, True, False)


def test_a_cpu_only_cluster_summarizes_to_no_gpus():
    gpu = summarize([CPU], [])
    assert gpu["nodes"] == [] and gpu["total"] == {} and gpu["device_plugin"] is False


def test_the_capability_probe_reports_the_gpu_summary():
    from dataclasses import dataclass

    from k8s_native.management import probe_cluster_capabilities

    from .test_management import FakeManagementBackend, _ctx

    @dataclass
    class _GpuBackend(FakeManagementBackend):
        def list_nodes(self, *, auth):
            return [A100, CPU]

    backend = _GpuBackend(pods_by_namespace={"gpu-operator": [{"name": "nvidia-device-plugin-daemonset-a"}]})
    caps = probe_cluster_capabilities(backend=backend, cluster=_ctx())

    assert caps["gpu"]["total"] == {"nvidia.com/gpu": 8}
    assert caps["gpu"]["device_plugin"] is True


def test_a_backend_without_node_listing_leaves_gpu_unprobed():
    from k8s_native.management import probe_cluster_capabilities

    from .test_management import FakeManagementBackend, _ctx

    caps = probe_cluster_capabilities(backend=FakeManagementBackend(), cluster=_ctx())
    assert caps["gpu"] == {}
