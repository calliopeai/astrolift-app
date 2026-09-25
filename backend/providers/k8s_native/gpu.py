"""GPU capacity discovery for a cluster (#2038).

Kubernetes reports what the scheduler will match: allocatable extended
resources (``nvidia.com/gpu``, ``amd.com/gpu``, ``nvidia.com/mig-<profile>``)
and node labels set by NVIDIA GPU feature discovery (``nvidia.com/gpu.product``,
``nvidia.com/gpu.memory``, ``nvidia.com/mig.config``) or the cloud
(``cloud.google.com/gke-accelerator``). This module turns node objects into
facts and facts into the ``gpu`` capability summary; it reads nothing else.
"""

from __future__ import annotations

from typing import Any

GPU_RESOURCES = ("nvidia.com/gpu", "amd.com/gpu")
MIG_PREFIX = "nvidia.com/mig-"
_LABELS = (
    "nvidia.com/gpu.product",
    "nvidia.com/gpu.memory",
    "nvidia.com/gpu.count",
    "nvidia.com/mig.config",
    "cloud.google.com/gke-accelerator",
    "node.kubernetes.io/instance-type",
)
_POOL_LABELS = ("eks.amazonaws.com/nodegroup", "karpenter.sh/nodepool", "agentpool", "cloud.google.com/gke-nodepool")


def _count(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def node_gpu_facts(node: dict[str, Any]) -> dict[str, Any] | None:
    """GPU facts of one node (``{"metadata", "status", "spec"}`` as dicts), or
    ``None`` for a node with no GPU or MIG capacity."""
    metadata = node.get("metadata") or {}
    labels = metadata.get("labels") or {}
    allocatable = (node.get("status") or {}).get("allocatable") or {}
    gpus = {name: _count(allocatable.get(name)) for name in GPU_RESOURCES if _count(allocatable.get(name))}
    mig = {
        name[len(MIG_PREFIX) :]: _count(value)
        for name, value in allocatable.items()
        if name.startswith(MIG_PREFIX) and _count(value)
    }
    if not gpus and not mig:
        return None
    pool = next((str(labels[key]) for key in _POOL_LABELS if labels.get(key)), "")
    return {
        "name": str(metadata.get("name") or ""),
        "gpus": gpus,
        "mig": mig,
        "labels": {key: str(labels[key]) for key in _LABELS if key in labels},
        "pool": pool,
        "taints": [
            {"key": str(t.get("key") or ""), "value": str(t.get("value") or ""), "effect": str(t.get("effect") or "")}
            for t in ((node.get("spec") or {}).get("taints") or [])
            if isinstance(t, dict)
        ],
    }


def summarize(nodes: list[dict[str, Any]], pods: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The ``gpu`` capability: GPU nodes plus totals and GPU stack presence.

    ``pods`` are the probe's cluster-wide pod summaries (``{"name", ...}``);
    the NVIDIA device plugin, GPU operator and DCGM exporter are detected by
    their daemonset pod names, whichever way they were installed.
    """
    facts = [f for f in (node_gpu_facts(n) for n in nodes) if f is not None]
    totals: dict[str, int] = {}
    mig_totals: dict[str, int] = {}
    products: set[str] = set()
    for fact in facts:
        for name, n in fact["gpus"].items():
            totals[name] = totals.get(name, 0) + n
        for profile, n in fact["mig"].items():
            mig_totals[profile] = mig_totals.get(profile, 0) + n
        product = fact["labels"].get("nvidia.com/gpu.product") or fact["labels"].get("cloud.google.com/gke-accelerator")
        if product:
            products.add(product)
    names = [str(p.get("name") or "") for p in (pods or [])]
    return {
        "nodes": facts,
        "total": totals,
        "mig_total": mig_totals,
        "products": sorted(products),
        "device_plugin": any("nvidia-device-plugin" in n for n in names),
        "gpu_operator": any(n.startswith("gpu-operator") for n in names),
        "dcgm_exporter": any("dcgm-exporter" in n for n in names),
    }
