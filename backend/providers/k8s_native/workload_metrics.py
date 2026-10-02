"""Read exact pod/container incarnations and verify the controller chain."""

from uuid import UUID

from _sdk.workload_metrics import (
    APP_ID_LABEL,
    ENVIRONMENT_ID_LABEL,
    WORKLOAD_ID_LABEL,
    MetricContainer,
    MetricMembershipUnavailable,
)
from k8s_native.observability import build_api_client

_MAX_PODS = 64
_MAX_CONTAINERS = 128
_KIND = {
    "deployment": "Deployment",
    "agent": "Deployment",
    "workflow": "Deployment",
    "statefulset": "StatefulSet",
    "daemonset": "DaemonSet",
    "job": "Job",
    "task": "Job",
    "cronjob": "CronJob",
}


def _uid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise MetricMembershipUnavailable("OWNERSHIP_UNVERIFIED") from exc


def metric_containers(*, auth, namespace, app_slug, app_id, environment_id, workload_slug, workload_id, workload_kind):
    from kubernetes import client
    from kubernetes.client.exceptions import ApiException

    expected = {
        APP_ID_LABEL: _uid(app_id),
        ENVIRONMENT_ID_LABEL: _uid(environment_id),
        WORKLOAD_ID_LABEL: _uid(workload_id),
    }
    kind = _KIND.get(workload_kind)
    if kind is None:
        raise MetricMembershipUnavailable("NOT_SUPPORTED_BY_PROVIDER")
    api_client = build_api_client(auth)
    core, apps, batch = client.CoreV1Api(api_client), client.AppsV1Api(api_client), client.BatchV1Api(api_client)
    readers = {
        "ReplicaSet": apps.read_namespaced_replica_set,
        "Deployment": apps.read_namespaced_deployment,
        "StatefulSet": apps.read_namespaced_stateful_set,
        "DaemonSet": apps.read_namespaced_daemon_set,
        "Job": batch.read_namespaced_job,
        "CronJob": batch.read_namespaced_cron_job,
    }
    cache = {}

    def assert_identity(metadata):
        labels = metadata.labels or {}
        if metadata.namespace != namespace or any(labels.get(key) != value for key, value in expected.items()):
            raise MetricMembershipUnavailable("OWNERSHIP_UNVERIFIED")

    def verify_owner(metadata):
        assert_identity(metadata)
        refs = [ref for ref in metadata.owner_references or [] if ref.controller]
        visited = set()
        for _ in range(4):
            if len(refs) != 1:
                raise MetricMembershipUnavailable("OWNERSHIP_UNVERIFIED")
            ref = refs[0]
            key = (ref.kind, ref.name, _uid(ref.uid))
            if key in visited or ref.kind not in readers:
                raise MetricMembershipUnavailable("OWNERSHIP_UNVERIFIED")
            visited.add(key)
            if key not in cache:
                cache[key] = readers[ref.kind](name=ref.name, namespace=namespace, _request_timeout=10)
            owner = cache[key].metadata
            if _uid(owner.uid) != key[2] or owner.deletion_timestamp is not None:
                raise MetricMembershipUnavailable("OWNERSHIP_UNVERIFIED")
            assert_identity(owner)
            if ref.kind == kind and ref.name == workload_slug:
                return
            refs = [parent for parent in owner.owner_references or [] if parent.controller]
        raise MetricMembershipUnavailable("OWNERSHIP_UNVERIFIED")

    try:
        page = core.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"astrolift.dev/app={app_slug},astrolift.dev/workload={workload_slug}",
            limit=_MAX_PODS + 1,
            _request_timeout=10,
        )
        if page.metadata._continue or len(page.items) > _MAX_PODS:
            raise MetricMembershipUnavailable("MEMBERSHIP_LIMIT_EXCEEDED")
        if not page.items:
            raise MetricMembershipUnavailable("NO_PODS")
        out = []
        for pod in page.items:
            metadata = pod.metadata
            if metadata.deletion_timestamp is not None:
                raise MetricMembershipUnavailable("PARTIAL_MEMBERSHIP")
            verify_owner(metadata)
            uid = _uid(metadata.uid)
            statuses = {row.name: row for row in pod.status.container_statuses or []}
            for spec in pod.spec.containers or []:
                row = statuses.get(spec.name)
                running = getattr(getattr(row, "state", None), "running", None)
                runtime_id = (getattr(row, "container_id", "") or "").partition("://")[2]
                if (
                    running is None
                    or running.started_at is None
                    or not runtime_id
                    or not all(char in "0123456789abcdef" for char in runtime_id)
                    or len(runtime_id) < 32
                ):
                    raise MetricMembershipUnavailable("PARTIAL_MEMBERSHIP")
                out.append(MetricContainer(metadata.name, uid, spec.name, runtime_id, running.started_at.timestamp()))
            if len(out) > _MAX_CONTAINERS:
                raise MetricMembershipUnavailable("MEMBERSHIP_LIMIT_EXCEEDED")
        if not out:
            raise MetricMembershipUnavailable("NO_PODS")
        return tuple(out)
    except ApiException as exc:
        raise MetricMembershipUnavailable(
            "PERMISSION_DENIED" if exc.status in (401, 403) else "PROVIDER_ERROR"
        ) from exc
    finally:
        api_client.close()
