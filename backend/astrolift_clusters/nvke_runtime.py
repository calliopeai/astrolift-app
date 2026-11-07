"""One leased, bounded attempt; persisted sending state forbids blind replay."""

from __future__ import annotations

import hashlib
from dataclasses import asdict

from django.db import connection as database
from django.db.models import F
from django.utils import timezone
from nvke.client import NvkeError
from nvke.lifecycle import cluster_ready, contains, declarative_ready, pool_ready
from nvke.operations import action, execute_action

from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters.models import NvkeOperation
from astrolift_clusters.nvke_connections import open_connection
from astrolift_clusters.nvke_sites import NvkeSiteError
from core.mutations import AuditEntry, emit_audit


def _state(row, status, stage, *, progress=None, error=None, reference=None):
    fields = {
        "status": status,
        "stage": stage,
        "updated_at": timezone.now(),
        "version": F("version") + 1,
        "error_code": error.code if error else "",
        "error_message": str(error)[:256] if error else "",
    }
    if status in {"succeeded", "failed", "cancelled"}:
        fields["finished_at"] = timezone.now()
    if progress is not None:
        outbox.save_result(row, progress)
        fields.update(result_backend_kind=row.result_backend_kind, result_ciphertext=row.result_ciphertext)
    if reference is not None:
        fields["remote_reference"] = reference
    updated = NvkeOperation.objects.filter(pk=row.pk, generation=row.generation).update(**fields)
    row.refresh_from_db()
    if not updated:
        return "execution_refused"
    emit_audit(
        AuditEntry(
            actor_user_id=row.actor_id,
            organization_id=row.organization_id,
            action="nvke.operation_state",
            permissions=(action(row.operation_key).permission,),
            decision="ALLOW",
            target_kind="nvke_operation",
            target_id=str(row.guid),
            duration_ms=0,
            extra={"status": status, "stage": stage, "operation_key": row.operation_key},
        )
    )
    return status


def _target(selected, request, response=None):
    if selected.surface == "datastore":
        name = request.get("name") or request.get("document", {}).get("metadata", {}).get("name")
        return {"key": f"datastore.get{selected.kind}", "request": {"name": name}}
    if selected.surface in {"tenant", "operator"}:
        name = request.get("path", {}).get("name") or request.get("body", {}).get("metadata", {}).get("name")
        return {
            "key": f"{selected.surface}.get{selected.kind or selected.http.resource}",
            "request": {"path": {"name": name}},
        }
    if selected.key == "updateClusterSpireServer":
        return {"key": "getClusterSpireServer", "request": {"path": request["path"]}}
    kind = "NodePool" if "NodePool" in selected.key else "Node" if selected.key == "deleteNode" else "Cluster"
    field = {"NodePool": "nodePoolID", "Node": "nodeID", "Cluster": "clusterID"}[kind]
    identity = request.get("path", {}).get(field) or (response or {}).get("id")
    if not identity:
        return None
    return {"key": f"get{kind}", "request": {"path": {field: identity}}}


def _get(client, target):
    try:
        return execute_action(client, action(target["key"]), target["request"])
    except NvkeError as exc:
        if exc.code == "NOT_FOUND":
            return None
        raise


def _preflight(client, selected, request):
    creating = selected.verb == "create" or bool(selected.http and selected.http.method == "POST")
    target = _target(selected, request)
    if creating and selected.surface == "controller":
        key = "listClusters" if selected.key == "createCluster" else "listNodePools"
        body = request["body"]
        query = {"name": body["name"]}
        if "clusterID" in body:
            query["clusterID"] = body["clusterID"]
        value = client.execute(key, query=query).data
        if value.get("clusters" if key == "listClusters" else "nodePools"):
            raise NvkeError("CONFLICT", "The requested NVKE resource name is already in use")
        return {"baseline": None, "target": None}
    previous = _get(client, target) if target else None
    if creating:
        if previous is not None:
            raise NvkeError("CONFLICT", "The requested NVKE resource name is already in use")
    elif previous is None and selected.verb not in {"delete", "DELETE"}:
        raise NvkeError("NOT_FOUND", "The requested NVKE resource does not exist")
    if previous is not None:
        version = request.get("resource_version")
        if version is not None and previous.resource_version != version:
            raise NvkeError("CONFLICT", "The NVKE resource changed before execution")
        uid = request.get("uid")
        if uid is not None and previous.data.get("metadata", {}).get("uid") != uid:
            raise NvkeError("CONFLICT", "The NVKE resource identity changed before execution")
    return {
        "baseline": asdict(previous) if previous else None,
        "target": target,
        "already_absent": previous is None and selected.verb in {"delete", "DELETE"},
    }


def _same_identity(before, current):
    if not before:
        return True
    old, new = before.get("data", {}), current.data
    if old.get("metadata", {}).get("uid"):
        if not new.get("metadata", {}).get("uid"):
            raise NvkeError("INVALID_RESPONSE", "NVKE resource observation omitted its identity")
        return old["metadata"]["uid"] == new.get("metadata", {}).get("uid")
    if old.get("id") and not new.get("id"):
        raise NvkeError("INVALID_RESPONSE", "NVKE resource observation omitted its identity")
    return not old.get("id") or old["id"] == new.get("id")


def _ready(client, selected, request, current, progress):
    value = current.data
    if selected.surface == "controller":
        if selected.key == "updateClusterSpireServer":
            requested = request["body"]
            return (
                bool(value.get("nodes"))
                and contains(value.get("requested"), requested)
                and all(
                    n.get("reachable") is True
                    and n.get("hasValidSVID") is True
                    and n.get("serverAddress") == requested["address"]
                    and n.get("serverPort") == requested["port"]
                    for n in value.get("nodes", [])
                )
            )
        desired = dict(request.get("body") or {})
        desired.pop("nodePools", None)
        if selected.key == "updateCluster":
            # No read-back field exists for the one-way OIDC switch. An idle
            # controller alone cannot prove that switch took effect.
            if "enableOidc" in desired:
                return False
            if "enableEtcdEncryption" in desired:
                desired["etcdEncrypted"] = desired.pop("enableEtcdEncryption")
        if not contains(value, desired):
            return False
        if selected.key in {"createNodePool", "updateNodePool"}:
            nodes = client.execute("listNodes", query={"nodePoolID": value["id"]}).data["nodes"]
            progress["observation_nodes"] = nodes
            return pool_ready(value, nodes)
        identity = value["id"]
        plane = client.execute("getClusterControlPlane", path={"clusterID": identity}).data
        pools = client.execute("listNodePools", query={"clusterID": identity}).data["nodePools"]
        nodes = client.execute("listNodes", query={"clusterID": identity}).data["nodes"]
        progress.update(observation_control_plane=plane, observation_pools=pools, observation_nodes=nodes)
        requested_pools = request.get("body", {}).get("nodePools")
        if requested_pools is not None:
            by_name = {pool.get("name"): pool for pool in pools}
            if set(by_name) != {pool["name"] for pool in requested_pools} or not all(
                contains(by_name[pool["name"]], pool) for pool in requested_pools
            ):
                return False
        return cluster_ready(value, plane, pools, nodes)
    desired = request.get("document") or request.get("body")
    if desired is not None and not contains(value, desired):
        return False
    if (
        selected.verb == "deprecate"
        and value.get("spec", {}).get("lifecycle", {}).get("deprecated", False) != request["deprecated"]
    ):
        return False
    kind = selected.kind or (selected.http.resource if selected.http else "")
    return declarative_ready(value) if kind == "NVKECluster" else True


def _observe(row, client, selected, request, progress):
    target = progress.get("target")
    if target is None:
        return _state(
            row,
            "uncertain",
            "reconciliation_required",
            progress=progress,
            error=NvkeError("MUTATION_UNCERTAIN", "NVKE mutation identity requires operator reconciliation"),
        )
    current = _get(client, target)
    deleting = selected.verb in {"delete", "DELETE"}
    if current is None:
        if deleting:
            return _state(row, "succeeded", "deleted", progress=progress)
        return _state(row, "awaiting_ready", "waiting_for_resource", progress=progress)
    if not _same_identity(progress.get("baseline") or progress.get("response"), current):
        if deleting:
            return _state(row, "succeeded", "original_resource_deleted", progress=progress)
        return _state(
            row,
            "uncertain",
            "identity_changed",
            progress=progress,
            error=NvkeError("IDENTITY_CHANGED", "NVKE resource was replaced during reconciliation"),
        )
    progress["observation"] = asdict(current)
    if deleting:
        return _state(row, "awaiting_ready", "deleting", progress=progress)
    if _ready(client, selected, request, current, progress):
        return _state(row, "succeeded", "ready", progress=progress)
    return _state(row, "awaiting_ready", "waiting_for_readiness", progress=progress)


def attempt(row_id, generation, *, execution):
    row = NvkeOperation.objects.select_related("site").get(pk=row_id)
    if row.generation != generation:
        return "execution_refused"
    if row.terminal:
        return row.status
    if execution[0] != row.workflow_id or execution[2] != "NvkeOperationWorkflow" or not execution[1]:
        return "execution_refused"
    if row.workflow_run_id and row.workflow_run_id != execution[1]:
        return "execution_refused"
    lease = int.from_bytes(hashlib.sha256(str(row.guid).encode()).digest()[:8], "big", signed=True)
    with database.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lease])
        acquired = cursor.fetchone()[0]
    if not acquired:
        return "pending"
    progress = {}
    applying = False
    try:
        row.refresh_from_db()
        if row.generation != generation:
            return "execution_refused"
        if row.terminal:
            return row.status
        if row.workflow_run_id and row.workflow_run_id != execution[1]:
            return "execution_refused"
        NvkeOperation.objects.filter(pk=row.pk, workflow_run_id="").update(workflow_run_id=execution[1])
        NvkeOperation.objects.filter(pk=row.pk, started_at__isnull=True).update(started_at=timezone.now())
        selected = action(row.operation_key)
        request = outbox.decode_input(row)
        progress = outbox.decode_result(row)
        if row.cancellation_requested_at and not progress.get("sending"):
            return _state(row, "cancelled", "cancelled_before_dispatch")
        if timezone.now() >= row.deadline:
            status = "uncertain" if progress.get("sending") else "failed"
            return _state(
                row,
                status,
                "deadline_reached",
                error=NvkeError("DEADLINE_EXCEEDED", "NVKE operation deadline was reached"),
            )

        def fence():
            current = NvkeOperation.objects.get(pk=row.pk)
            if (
                current.generation != generation
                or current.workflow_run_id != execution[1]
                or current.terminal
            ):
                raise NvkeError("EXECUTION_REFUSED", "NVKE execution authority changed")
            return outbox.checkpoint(row)

        connection = fence()
        with open_connection(
            connection, namespace=row.site.organization_namespace, checkpoint=fence
        ) as client:
            if not selected.mutates:
                response = execute_action(client, selected, request)
                return _state(row, "succeeded", "read_complete", progress={"response": asdict(response)})
            if not progress.get("sending"):
                progress = _preflight(client, selected, request)
                if progress.get("already_absent"):
                    return _state(row, "succeeded", "already_absent", progress=progress)
                outbox.checkpoint(row)
                progress["sending"] = True
                _state(row, "running", "sending", progress=progress)
                applying = True
                response = execute_action(client, selected, request)
                applying = False
                progress["response"] = asdict(response)
                progress["target"] = _target(selected, request, response.data)
                _state(row, "awaiting_ready", "accepted", progress=progress)
                if request.get("dry_run") is True:
                    return _state(row, "succeeded", "dry_run_complete", progress=progress)
            return _observe(row, client, selected, request, progress)
    except (NvkeError, NvkeSiteError) as exc:
        if not isinstance(exc, NvkeError):
            exc = NvkeError(exc.code, str(exc))
        if applying and not exc.ambiguous:
            return _state(row, "failed", "request_refused", error=exc)
        if progress.get("sending") or exc.ambiguous:
            return _state(row, "uncertain", "reconciliation_required", error=exc)
        if exc.retryable:
            return _state(row, "awaiting_ready", row.stage or "preflight", error=exc)
        return _state(row, "failed", "authority_or_request_refused", error=exc)
    except Exception:
        return _state(
            row,
            "uncertain",
            "reconciliation_required",
            error=NvkeError(
                "EXECUTION_UNCERTAIN", "NVKE operation requires reconciliation after an execution failure"
            ),
        )
    finally:
        with database.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lease])
