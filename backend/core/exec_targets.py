"""Reviewed app exec targets. Pod UID checks are preflight checks, never atomic exec binding."""

from dataclasses import asdict, dataclass
from types import SimpleNamespace

from astrolift_lifecycle.action_preconditions import locked_workload, recheck_action
from astrolift_lifecycle.visibility import live_app_rows
from astrolift_lifecycle.workload_targets import action_target, check_target_match
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission, PermissionDenied
from core.scope_args import read_guid
from core.tenancy import get_current_tenant


class ExecTargetError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ReviewedExecTarget:
    facts: dict
    cluster: object
    driver: object


def _pod_target(workload, environment, pod_name, container, *, expected_uid=None):
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    target = action_target(workload, environment)
    if target is None:
        raise ExecTargetError("PRECONDITION", "exec environment target is unavailable")
    try:
        recheck_action(Permission.APP_EXEC_POD, workload, environment)
    except PermissionDenied as exc:
        raise ExecTargetError("PERMISSION_DENIED", exc.reason) from exc
    cluster = environment.tenant_cluster
    driver = _driver_for_cluster(cluster)
    try:
        pod = driver.get_manifest(_context_for_cluster(cluster).slug, target.namespace, "Pod", pod_name)
    except Exception as exc:
        raise ExecTargetError("PRECONDITION", "reviewed pod is unavailable") from exc
    if not isinstance(pod, dict):
        raise ExecTargetError("PRECONDITION", "reviewed pod is unavailable")
    metadata = pod.get("metadata", {})
    labels = metadata.get("labels", {})
    names = {item.get("name") for item in pod.get("spec", {}).get("containers", [])} if pod else set()
    if (
        metadata.get("name") != pod_name
        or metadata.get("namespace") != target.namespace
        or labels.get("astrolift.dev/app") != workload.registered_app.slug
        or labels.get("astrolift.dev/environment") != environment.name
        or labels.get("astrolift.dev/workload") != workload.slug
        or container not in names
        or not metadata.get("uid")
        or (expected_uid is not None and metadata["uid"] != expected_uid)
    ):
        raise ExecTargetError(
            "PRECONDITION", "pod, workload or container changed; review the exact target again"
        )
    facts = {
        **asdict(target),
        "pod_name": pod_name,
        "pod_uid": metadata["uid"],
        "container": container,
        "pod_binding": "PREFLIGHT_ONLY",
        "resumable": False,
    }
    return ReviewedExecTarget(facts=facts, cluster=cluster, driver=driver)


def review_exec_target(*, app_slug, workload_slug, environment_guid, pod_name, container):
    tenant = get_current_tenant()
    app = (
        live_app_rows(RegisteredApp.objects.all())
        .filter(slug=app_slug)
        .select_related("organization")
        .first()
    )
    if app is None or tenant is None or app.organization_id != tenant.organization_id:
        raise ExecTargetError("NOT_FOUND", "app target not found")
    workload = Workload.objects.filter(registered_app=app, slug=workload_slug).first()
    if workload is None:
        raise ExecTargetError("NOT_FOUND", "workload target not found")
    guid = read_guid({"id": environment_guid}, "id")
    if guid is None:
        raise ExecTargetError("VALIDATION", "environmentId must be a GUID")
    with locked_workload(str(workload.guid), environment_guid=guid) as (workload, environment):
        if workload is None or environment is None:
            raise ExecTargetError("PRECONDITION", "exec environment target is unavailable")
        return _pod_target(workload, environment, pod_name, container)


def admit_exec_target(*, app_slug, pod_name, container, review, check_pod=True):
    if not isinstance(review, dict):
        raise ExecTargetError("VALIDATION", "target must be a reviewed object")
    if (
        review.get("requireAtomicPodBinding")
        or review.get("requireActionAdmission")
        or review.get("actionAdmissionProof")
        or review.get("resumable")
        or review.get("podBinding", "PREFLIGHT_ONLY") != "PREFLIGHT_ONLY"
    ):
        raise ExecTargetError("UNSUPPORTED", "atomic pod binding and action-admission proofs are unavailable")
    fields = ("workloadId", "appId", "environmentId", "clusterId")
    ids = {field: read_guid(review, field) for field in fields}
    if any(value is None for value in ids.values()):
        raise ExecTargetError(
            "VALIDATION", "reviewed target requires immutable app, workload, environment and cluster GUIDs"
        )
    versions = ("workloadVersion", "appVersion", "environmentVersion", "clusterVersion")
    if any(type(review.get(field)) is not int or review[field] < 0 for field in versions):
        raise ExecTargetError("VALIDATION", "reviewed target requires current integer versions")
    if review.get("podName") != pod_name or review.get("container") != container or not review.get("podUid"):
        raise ExecTargetError("VALIDATION", "reviewed pod and container must match the requested target")
    with locked_workload(ids["workloadId"], environment_guid=ids["environmentId"]) as (workload, environment):
        if (
            workload is None
            or str(workload.registered_app.guid) != str(ids["appId"])
            or workload.registered_app.slug != app_slug
        ):
            raise ExecTargetError("PRECONDITION", "reviewed app or workload target is unavailable")
        input = SimpleNamespace(
            environment_id=ids["environmentId"],
            expected_cluster_id=ids["clusterId"],
            if_match_environment_version=review["environmentVersion"],
            if_match_cluster_version=review["clusterVersion"],
            if_match_app_version=review["appVersion"],
            expected_namespace=review.get("namespace"),
        )
        refusal = check_target_match(workload, environment, input, if_match_version=review["workloadVersion"])
        if refusal is not None:
            raise ExecTargetError(refusal.errors[0].code, refusal.errors[0].message)
        if not check_pod:
            try:
                recheck_action(Permission.APP_EXEC_POD, workload, environment)
            except PermissionDenied as exc:
                raise ExecTargetError("PERMISSION_DENIED", exc.reason) from exc
            return None
        return _pod_target(workload, environment, pod_name, container, expected_uid=review["podUid"])
