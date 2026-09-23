"""Agent task pods carry the sandbox baseline (#1848).

An agent pod runs model-driven code. It must be bounded in CPU and memory,
must not hold the namespace ServiceAccount token, and must not be able to
gain privileges beyond the ones it starts with.
"""

from __future__ import annotations

from astrolift_dispatch.spawners.k8s_job import _render_agent_job
from astrolift_dispatch.tests.test_k8s_job_vnc import _FakeTask, _FakeWorkload


def _render(**kwargs):
    return _render_agent_job(
        job_name="agent-task-t1",
        workload=_FakeWorkload("ghcr.io/calliopeai/astrolift-agent-claude:1.2", 0),
        namespace="ns",
        task=_FakeTask(guid="t-1", vnc_enabled=False),
        **kwargs,
    )


def test_task_pod_has_no_service_account_token_and_runtime_default_seccomp():
    pod = _render()["spec"]["template"]["spec"]

    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}


def test_task_container_cannot_escalate_and_is_bounded():
    container = _render()["spec"]["template"]["spec"]["containers"][0]

    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["resources"] == {
        "requests": {"cpu": "250m", "memory": "512Mi"},
        "limits": {"cpu": "4", "memory": "8Gi"},
    }


def test_bounds_follow_settings(settings):
    settings.AGENT_POD_CPU_LIMIT = "6"
    settings.AGENT_POD_MEMORY_LIMIT = "12Gi"

    limits = _render()["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]

    assert limits == {"cpu": "6", "memory": "12Gi"}


def test_managed_model_service_account_survives_hardening():
    """IRSA and pod identity project their own token volume, so the model SA
    must still be named even though the default token mount is off."""
    pod = _render(service_account="agent-model")["spec"]["template"]["spec"]

    assert pod["serviceAccountName"] == "agent-model"
    assert pod["automountServiceAccountToken"] is False
