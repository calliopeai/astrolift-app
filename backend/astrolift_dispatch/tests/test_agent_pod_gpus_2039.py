"""Agent task and box pods request GPUs from their environment spec (#2039)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_agents.schema.mutations import _agent_gpu_error
from astrolift_dispatch.pod_hardening import harden_agent_pod


def _pod(**gpu):
    spec = SimpleNamespace(**{"gpu": 0, "gpu_type": "", "mig_profile": "", **gpu})
    return harden_agent_pod({"containers": [{"name": "agent"}, {"name": "vnc"}]}, spec=spec)


def test_a_gpu_spec_puts_the_gpus_on_the_agent_container_only():
    pod = _pod(gpu=2)
    assert pod["containers"][0]["resources"]["limits"]["nvidia.com/gpu"] == "2"
    assert "nvidia.com/gpu" not in pod["containers"][1]["resources"]["limits"]
    assert {t["key"] for t in pod["tolerations"]} == {"nvidia.com/gpu", "astrolift.io/gpu"}
    assert "affinity" not in pod


def test_mig_slices_and_a_pinned_type():
    pod = _pod(gpu=1, mig_profile="3g.47gb", gpu_type="nvidia-a100")
    assert pod["containers"][0]["resources"]["limits"]["nvidia.com/mig-3g.47gb"] == "1"
    terms = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ]
    assert terms[0]["matchExpressions"][0]["values"] == ["nvidia-a100"]


def test_no_gpu_means_no_tolerations():
    pod = _pod()
    assert "tolerations" not in pod
    assert harden_agent_pod({"containers": [{"name": "agent"}]}).get("tolerations") is None


@pytest.mark.parametrize(
    ("gpu", "gpu_type", "mig", "ok"),
    [
        (1, "", "", True),
        (0, "", "", True),
        (0, "nvidia-l4", "", False),
        (1, "", "bogus", False),
        (99, "", "", False),
    ],
)
def test_the_manifest_rules_validate_spec_gpus(gpu, gpu_type, mig, ok):
    assert (_agent_gpu_error(gpu, gpu_type, mig) == "") is ok
