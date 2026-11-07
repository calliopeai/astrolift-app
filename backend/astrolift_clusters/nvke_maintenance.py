"""Historical maintenance metadata; scheduling timestamps are never certificate expiry."""

from datetime import datetime
from functools import lru_cache

from nvke.client import NvkeError
from nvke.contract import controller_spec

from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.nvke_diagnostics import _bool, _date, _uuid
from core.permissions import Permission

_RENEWAL_PHASES = frozenset(
    {
        "admission",
        "two-control-plane-voters",
        "complete",
        "list-control-plane-nodes",
        "preflight",
        "renew-node",
        "postflight",
    }
)


@lru_cache(maxsize=1)
def _vocabulary():
    schemas = controller_spec()["components"]["schemas"]
    return {
        (kind, field): frozenset(definition["enum"])
        for kind in ("CertificateRenewalStatus", "EtcdKeyRotationStatus", "UpdateInProgress")
        for field, definition in schemas[kind]["properties"].items()
        if "enum" in definition
    }


def _enum(value, kind, field):
    return value if isinstance(value, str) and value in _vocabulary()[kind, field] else None


def _workflow(value):
    try:
        return (
            value
            if isinstance(value, str) and value and len(value.encode("utf-8")) <= 1000 and value.isprintable()
            else None
        )
    except UnicodeError:
        return None


def _retained(state, owner, reported, kind):
    if state is None:
        return None
    if state != "failed" or reported is False:
        return False
    return owner == kind if owner is not None else None


def project(data, observed_at):
    conditions = data.get("conditions")
    known = isinstance(conditions, dict)
    conditions = conditions if known else {}
    lock = conditions.get("updateInProgress")
    reported = ("updateInProgress" in conditions) if known else None
    owner = _enum(lock.get("type"), "UpdateInProgress", "type") if isinstance(lock, dict) else None
    result = {
        "next_certificate_renewal_due": _date(data.get("nextCertRenewalDue")),
        "renewal_schedule_overdue_at_observation": None,
        "mutation_lock_reported": reported,
        "mutation_lock_owner": owner,
        "mutation_lock_started_at": _date(lock.get("startedAt")) if isinstance(lock, dict) else None,
        "renewal": None,
        "etcd_key_rotation": None,
    }
    renewal = conditions.get("certificateRenewal")
    if isinstance(renewal, dict):
        state = _enum(renewal.get("state"), "CertificateRenewalStatus", "state")
        phase = renewal.get("phase")
        result["renewal"] = {
            "workflow_id": _workflow(renewal.get("cycleID")),
            "state": state,
            "outcome": _enum(renewal.get("outcome"), "CertificateRenewalStatus", "outcome")
            if state == "succeeded"
            else None,
            "forced": _bool(renewal.get("forced")),
            "scheduled_for": _date(renewal.get("scheduledFor")),
            "started_at": _date(renewal.get("startedAt")),
            "completed_at": _date(renewal.get("completedAt")),
            "failed_node_id": _uuid(renewal.get("failedNodeID")),
            "phase": phase if isinstance(phase, str) and phase in _RENEWAL_PHASES else None,
            "retained_mutation_lock": _retained(state, owner, reported, "certificate_renewal"),
        }
    rotation = conditions.get("etcdKeyRotation")
    if isinstance(rotation, dict):
        state = _enum(rotation.get("state"), "EtcdKeyRotationStatus", "state")
        result["etcd_key_rotation"] = {
            "workflow_id": _workflow(rotation.get("workflowID")),
            "state": state,
            "phase": _enum(rotation.get("phase"), "EtcdKeyRotationStatus", "phase"),
            "started_at": _date(rotation.get("startedAt")),
            "last_progress_at": _date(rotation.get("lastProgressAt")),
            "completed_at": _date(rotation.get("completedAt")),
            "failure_reason": _enum(rotation.get("failureReason"), "EtcdKeyRotationStatus", "failureReason"),
            "retained_mutation_lock": _retained(state, owner, reported, "etcd_key_rotation"),
        }
    state_known = renewal is None or (
        result["renewal"] is not None and result["renewal"]["state"] is not None
    )
    if renewal is None and reported is not False and owner in {None, "certificate_renewal"}:
        state_known = False
    due = result["next_certificate_renewal_due"]
    if known and state_known and due and observed_at is not None:
        result["renewal_schedule_overdue_at_observation"] = (
            False
            if result["renewal"] is not None and result["renewal"]["state"] == "accepted"
            else datetime.fromisoformat(due) < observed_at
        )
    return result


def read(*, site_id, operation_id, expected_actor_id, expected_version):
    _org, actor = sites.authorized(Permission.NVKE_READ)
    if type(expected_actor_id) is not int or actor.pk != expected_actor_id:
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE caller changed")
    row = operations.read(operation_id)
    if str(row.site.guid) != site_id:
        raise NvkeError("NOT_FOUND", "NVKE observation not found")
    if (
        row.operation_key != "getCluster"
        or row.status != "succeeded"
        or type(expected_version) is not int
        or row.version != expected_version
        or row.authority.get("inventory") is True
    ):
        raise NvkeError(
            "SNAPSHOT_UNAVAILABLE", "Choose a successful controller cluster read at its reviewed version"
        )
    request = operations.decode_input(row)
    path = request.get("path") if isinstance(request, dict) else None
    target = path.get("clusterID") if isinstance(path, dict) else None
    saved = operations.decode_result(row)
    response = saved.get("response") if isinstance(saved, dict) else None
    data = response.get("data") if isinstance(response, dict) else None
    cluster_id = _uuid(target)
    if (
        cluster_id is None
        or cluster_id != target
        or not isinstance(data, dict)
        or data.get("id") != cluster_id
        or not isinstance(response, dict)
        or type(response.get("status_code")) is not int
        or not 200 <= response["status_code"] < 300
    ):
        raise NvkeError(
            "SNAPSHOT_UNAVAILABLE", "The saved controller read did not prove its fixed cluster identity"
        )
    return row, {"cluster_id": cluster_id, **project(data, row.finished_at)}
