"""A Job and a Deployment project onto the same two numbers (#133).

``WorkloadStatus.ready_replicas`` / ``desired_replicas`` were read straight
off the Deployment keys, so a Job -- which spells the same two counts
``status.ready`` / ``spec.parallelism`` -- came back 0/0. That is
indistinguishable from a Deployment scaled to zero, so a healthy agent-box
sat at ``provisioning`` for the life of the box.

The payloads here are what the apiserver actually returned on prd, not a
shape invented to match the projection: a Job's status genuinely has no
``readyReplicas`` and its spec genuinely has no ``replicas``, and a test
that synthesized those keys would have passed against the bug.
"""

from __future__ import annotations

import pytest

from _sdk.cluster import workload_status_from_object

# The box Job from the #133 report, as ``kubectl get job -o json`` returned
# it while the pod was up and tmux was holding the session.
RUNNING_BOX_JOB = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {
        "name": "agent-box-01a0179226c370ac",
        "namespace": "astrolift-agents-steadymd",
    },
    "spec": {
        "backoffLimit": 0,
        "completions": 1,
        "parallelism": 1,
        "ttlSecondsAfterFinished": 300,
    },
    "status": {
        "active": 1,
        "ready": 1,
        "startTime": "2026-08-19T01:10:49Z",
        "terminating": 0,
        "uncountedTerminatedPods": {},
    },
}

# The same Job in the window between the apiserver accepting it and the
# kubelet starting a pod. ``status`` is all but empty; there is no ``ready``
# and no ``active``.
JUST_CREATED_BOX_JOB = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {
        "name": "agent-box-01a0179226c370ac",
        "namespace": "astrolift-agents-steadymd",
    },
    "spec": {
        "backoffLimit": 0,
        "completions": 1,
        "parallelism": 1,
        "ttlSecondsAfterFinished": 300,
    },
    "status": {
        "startTime": "2026-08-19T01:10:47Z",
        "uncountedTerminatedPods": {},
    },
}


def test_running_job_reads_as_one_of_one_ready():
    status = workload_status_from_object(
        "Job",
        "agent-box-01a0179226c370ac",
        "astrolift-agents-steadymd",
        RUNNING_BOX_JOB,
    )
    assert (status.ready_replicas, status.desired_replicas) == (1, 1)


def test_just_created_job_is_wanted_but_not_yet_ready():
    """The ordering trap: a Job exists before its pod does.

    Wanting one pod is not the same as having one, and a box that reports
    attachable before its pod has started fails an attach just as surely as
    one that never reports attachable at all.
    """
    status = workload_status_from_object(
        "Job",
        "agent-box-01a0179226c370ac",
        "astrolift-agents-steadymd",
        JUST_CREATED_BOX_JOB,
    )
    assert status.ready_replicas == 0
    assert status.desired_replicas == 1


@pytest.mark.parametrize(
    "job_status",
    [
        {"active": 1, "ready": 0, "startTime": "2026-08-19T01:10:48Z"},
        # Pre-1.24 clusters do not report ``ready`` at all. Absent must read
        # as "not ready", not fall back to ``active``.
        {"active": 1, "startTime": "2026-08-19T01:10:48Z"},
    ],
)
def test_a_scheduled_but_unstarted_pod_is_not_ready(job_status):
    """``active`` counts Pending pods, so readiness must not be read off it."""
    status = workload_status_from_object(
        "Job",
        "b",
        "ns",
        {**RUNNING_BOX_JOB, "status": job_status},
    )
    assert status.ready_replicas == 0


def test_job_without_parallelism_wants_one_pod():
    """``parallelism`` defaults to 1 in the Job spec, so an omitted field is
    one wanted pod, not zero."""
    obj = {"spec": {"backoffLimit": 0}, "status": {"active": 1, "ready": 1}}
    status = workload_status_from_object("Job", "build-abc", "ns", obj)
    assert (status.ready_replicas, status.desired_replicas) == (1, 1)


def test_completed_job_has_no_ready_pods():
    """A finished Job wanted a pod and has none up. Terminal state stays in
    ``conditions``, which is where every Job caller already reads it."""
    obj = {
        "spec": {"parallelism": 1},
        "status": {
            "succeeded": 1,
            "ready": 0,
            "conditions": [{"type": "Complete", "status": "True"}],
        },
    }
    status = workload_status_from_object("Job", "b", "ns", obj)
    assert status.ready_replicas == 0
    assert status.conditions == [{"type": "Complete", "status": "True"}]


def test_deployment_projection_is_unchanged():
    obj = {
        "spec": {"replicas": 5},
        "status": {
            "readyReplicas": 3,
            "conditions": [{"type": "Progressing", "status": "True"}],
        },
    }
    status = workload_status_from_object("Deployment", "api", "ns", obj)
    assert (status.ready_replicas, status.desired_replicas) == (3, 5)
    assert status.kind == "Deployment"
    assert len(status.conditions) == 1


def test_deployment_scaled_to_zero_still_reads_zero():
    """0/0 has to keep meaning something, or the Job mapping would have
    bought liveness by making emptiness unrepresentable."""
    obj = {"spec": {"replicas": 0}, "status": {}}
    status = workload_status_from_object("Deployment", "api", "ns", obj)
    assert (status.ready_replicas, status.desired_replicas) == (0, 0)


@pytest.mark.parametrize("kind", ["StatefulSet", "DaemonSet"])
def test_other_workload_kinds_keep_the_deployment_keys(kind):
    obj = {"spec": {"replicas": 2}, "status": {"readyReplicas": 2}}
    status = workload_status_from_object(kind, "w", "ns", obj)
    assert (status.ready_replicas, status.desired_replicas) == (2, 2)


# ---- every cloud reads the same Job the same way -------------------
#
# The projection was copy-pasted identically into all four cloud drivers,
# which is why the bug was cloud-agnostic. Now that there is one
# implementation, this pins every driver to it: a box must not read as
# running or not-running depending on which cloud its cluster happens to
# be in. Driven through the driver method rather than the helper so a
# driver that quietly hand-rolls the projection again fails here.

CLOUD_DRIVERS = {
    "aws": ("aws.cluster_eks", "EKSClusterDriver"),
    "gcp": ("gcp.cluster_gke", "GKEClusterDriver"),
    "azure": ("azure.cluster_aks", "AKSClusterDriver"),
    "k8s_native": ("k8s_native.cluster", "K8sNativeClusterDriver"),
}


class _OneObjectCluster:
    """Enough of a driver for ``get_workload_status`` to run: the apiserver
    read, and nothing else. Bypasses each cloud's very different
    constructor, which has no bearing on how a payload is projected."""

    def __init__(self, obj):
        self._obj = obj

    def _k8s(self, cluster):
        class _Client:
            @staticmethod
            def get(**_kwargs):
                return self._obj

        return _Client()


@pytest.mark.parametrize("plugin_id", sorted(CLOUD_DRIVERS))
def test_every_cloud_driver_reads_a_running_job_as_ready(plugin_id):
    module_name, class_name = CLOUD_DRIVERS[plugin_id]
    driver_cls = getattr(__import__(module_name, fromlist=[class_name]), class_name)
    stub = _OneObjectCluster(RUNNING_BOX_JOB)

    status = driver_cls.get_workload_status(
        stub,
        "some-cluster",
        "astrolift-agents-steadymd",
        "Job",
        "agent-box-01a0179226c370ac",
    )

    assert (status.ready_replicas, status.desired_replicas) == (1, 1)
    assert status.kind == "Job"
