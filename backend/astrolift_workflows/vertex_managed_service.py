"""Commit Vertex request reservations before reviewed provider effects (#2277)."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import time
from uuid import uuid4

from django.db import transaction
from temporalio import activity
from temporalio.exceptions import ApplicationError

from astrolift_workflows.managed_service_review import reviewed_service_call

JOURNAL_KEY = "vertex_operation"
_MAX_SECONDS = 20 * 60
_POLL_SECONDS = 2


def is_vertex_service(service_id):
    from astrolift_services.models import ManagedService

    row = ManagedService.all_objects.select_related(
        "tenant_cluster__provider_plugin", "app_environment__tenant_cluster__provider_plugin"
    ).get(pk=service_id)
    cluster = row.tenant_cluster or getattr(row.app_environment, "tenant_cluster", None)
    return bool(
        cluster
        and cluster.provider_plugin.slug == "gcp"
        and row.kind == "model_endpoint"
        and row.variant in ("", "vertex_ai")
    )


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def vertex_read_config(row, config):
    from gcp.managed.model_endpoint_vertex import VertexAIEndpointConfig

    from astrolift_services.secret_ref_config import service_organization
    from astrolift_workflows.activities.managed_service_lifecycle import _service_cluster

    if not isinstance(config, VertexAIEndpointConfig):
        return config
    owner = service_organization(row)
    cluster = _service_cluster(row)
    if (
        row.deleted_at
        or owner is None
        or owner.deleted_at
        or cluster is None
        or cluster.deleted_at
        or not cluster.is_active
        or cluster.provider_plugin.deleted_at
        or not cluster.provider_plugin.is_enabled
    ):
        raise ValueError("Vertex current read placement is unavailable")
    state = dict((row.lifecycle_policy or {}).get(JOURNAL_KEY) or {})
    if state:
        context = state.get("context", {})
        facts = {
            "service": str(row.guid),
            "organization": str(owner.guid) if owner else "",
            "project": str(row.project.guid) if row.project_id else "",
            "cluster": str(cluster.guid) if cluster else "",
            "provider": str(cluster.provider_plugin.guid) if cluster else "",
            "cloud_project": config.project_id,
            "region": config.region,
            "desired_digest": _hash(row.config or {}),
        }
        if any(context.get(key) != value for key, value in facts.items()):
            raise ValueError("Vertex recorded operation placement/configuration changed; read refused")
    return dataclasses.replace(config, operation_state=state)


def _load(service_id, binding, action, delete_data, force_destroy):
    from _sdk.managed_service import DeprovisionSpec, UpdateSpec
    from gcp.managed.model_endpoint_vertex import VertexAIEndpointDriver

    from astrolift_services.models import ManagedService
    from astrolift_services.secret_ref_config import service_organization
    from astrolift_workflows.activities.managed_service_lifecycle import (
        _recorded_handle_exclusive,
        _run_managed_service_preflight,
        _service_cluster,
        build_provision_spec,
    )
    from core.cluster_observability import managed_config_for

    row = ManagedService.all_objects.select_related(
        "registered_app__organization",
        "project__organization",
        "app_environment__tenant_cluster__provider_plugin",
        "tenant_cluster__provider_plugin",
    ).get(pk=service_id)
    cluster = _service_cluster(row)
    if (
        row.deleted_at
        or cluster is None
        or cluster.deleted_at
        or not cluster.is_active
        or cluster.provider_plugin.deleted_at
        or not cluster.provider_plugin.is_enabled
    ):
        raise ValueError("Vertex current service placement is unavailable")
    _run_managed_service_preflight(row, cluster)
    config = managed_config_for("gcp", cluster, kind=row.kind, variant=row.variant)
    organization = service_organization(row)
    if organization is None or organization.deleted_at:
        raise ValueError("Vertex organization placement is unavailable")
    context = {
        "service": str(row.guid),
        "organization": str(organization.guid),
        "project": str(row.project.guid) if row.project_id else "",
        "cluster": str(cluster.guid),
        "provider": str(cluster.provider_plugin.guid),
        "cloud_project": config.project_id,
        "region": config.region,
        "desired_digest": _hash(row.config or {}),
        "operation_kind": row.operation_kind,
        "action": action,
        "workflow": row.operation_workflow_id,
        "started_at": row.operation_started_at.isoformat() if row.operation_started_at else "",
        "review_digest": binding.digest if binding else "",
        "delete_data": delete_data,
        "force_destroy": force_destroy,
    }
    policy = dict(row.lifecycle_policy or {})
    state = dict(policy.get(JOURNAL_KEY) or {})
    previous = state.get("context")
    if previous and previous != context:
        # A completed prior operation can seed the next explicitly admitted update/delete.
        stable = ("service", "organization", "project", "cluster", "provider", "cloud_project", "region")
        if not state.get("complete") or any(previous.get(k) != context[k] for k in stable):
            raise ValueError("Vertex original operation or placement changed; operator recovery required")
        state = {
            k: state[k]
            for k in ("endpoint", "deployed_model_id", "model_artifact", "deployment_display_name")
            if k in state
        }
    state["context"] = context
    proof = build_provision_spec(row, cluster=cluster)
    if action == "provision":
        spec = proof
    else:
        from astrolift_drivers.managed_resolution import resolve_managed_driver

        resolved = resolve_managed_driver(cluster_plugin_slug="gcp", kind=row.kind, variant=row.variant)
        exclusive = _recorded_handle_exclusive(row, resolved=resolved, cfg=config)
        if action == "update":
            spec = UpdateSpec(
                handle=row.backend_ref,
                size=str(row.config["size"]) if "size" in (row.config or {}) else None,
                config=dict(row.config or {}),
                managed_service_id=str(row.guid),
                recorded_handle_exclusive=exclusive,
            )
        else:
            spec = DeprovisionSpec(
                handle=row.backend_ref,
                config=dict(row.config or {}),
                managed_service_id=str(row.guid),
                recorded_handle_exclusive=exclusive,
            )
    return row, config, state, spec, VertexAIEndpointDriver


def _save(row, state):
    policy = dict(row.lifecycle_policy or {})
    policy[JOURNAL_KEY] = state
    row.lifecycle_policy = policy
    # The reviewed digest deliberately excludes lifecycle progress and row.version.
    row.save(update_fields=["lifecycle_policy", "updated_at", "version"])


def _prepare(service_id, binding, action, delete_data, force_destroy):
    row, config, state, spec, driver_type = _load(service_id, binding, action, delete_data, force_destroy)
    driver = driver_type(config=dataclasses.replace(config, operation_state=state))
    try:
        plan = driver.operation_plan(action, spec, delete_data=delete_data, force_destroy=force_destroy)
    except ValueError as exc:
        # Preserve verified observations even when a missing operation receipt prevents resumption.
        observed = getattr(driver, "observed_state", state)
        observed["complete"] = False
        _save(row, observed)
        return {"refused": True, "message": str(exc)}
    state = plan.state
    state["complete"] = plan.complete
    if plan.phase:
        if (
            binding is None
            or not row.operation_workflow_id
            or row.operation_started_at is None
            or row.operation_kind != action
        ):
            raise ValueError(
                "Vertex writes require the original reviewed lifecycle operation; legacy direct work refused"
            )
        nonce = str(uuid4())
        state.setdefault("steps", {})[plan.phase] = {
            "state": "reserved",
            "nonce": nonce,
            "request_digest": _hash(plan.request),
        }
        _save(row, state)
        return {"phase": plan.phase, "nonce": nonce}
    _save(row, state)
    return {"pending": plan.pending, "complete": plan.complete, "state": state, "message": plan.message}


def _submit(service_id, binding, action, delete_data, force_destroy, reservation):
    row, config, state, spec, driver_type = _load(service_id, binding, action, delete_data, force_destroy)
    step = state.get("steps", {}).get(reservation["phase"], {})
    if step.get("state") != "reserved" or step.get("nonce") != reservation["nonce"]:
        raise ValueError("Vertex reservation changed; request not sent")
    driver = driver_type(
        config=dataclasses.replace(
            config,
            operation_state=state,
            submit_phase=reservation["phase"],
            submit_nonce=reservation["nonce"],
            record_operation=lambda receipt: _save(row, receipt),
        )
    )
    plan = driver.operation_plan(action, spec, delete_data=delete_data, force_destroy=force_destroy)
    if plan.phase != reservation["phase"] or _hash(plan.request) != step.get("request_digest"):
        raise ValueError("Vertex reserved cloud request changed; request not sent")
    driver.submit_reserved(plan)


def _iteration(service_id, binding, action, delete_data, force_destroy):
    if transaction.get_connection().in_atomic_block:
        raise ValueError("Vertex reservation cannot run inside an uncommitted outer transaction")
    # Two commits are deliberate: a cloud acceptance survives a lost receipt/rollback as unknown intent.
    result = reviewed_service_call(_prepare, service_id, binding, binding, action, delete_data, force_destroy)
    if result.get("phase"):
        reviewed_service_call(
            _submit, service_id, binding, binding, action, delete_data, force_destroy, result
        )
        return {"pending": True}
    return result


async def vertex_lifecycle_call(service_id, binding, action, *, delete_data=False, force_destroy=False):
    from asgiref.sync import sync_to_async

    deadline = time.monotonic() + _MAX_SECONDS
    while time.monotonic() < deadline:
        if activity.in_activity():
            activity.heartbeat()
        try:
            result = await sync_to_async(_iteration)(service_id, binding, action, delete_data, force_destroy)
        except (ValueError, TypeError) as exc:
            raise ApplicationError(str(exc), type="VERTEX_RECOVERY_REQUIRED", non_retryable=True) from None
        if result.get("complete"):
            state = result["state"]
            return {
                "ok": True,
                "ready": action == "provision",
                "handle": f"model_endpoint/{state['endpoint']}",
                "message": result["message"],
                "errors": [],
                "retryable": False,
            }
        if result.get("refused"):
            raise ApplicationError(result["message"], type="VERTEX_RECOVERY_REQUIRED", non_retryable=True)
        await asyncio.sleep(_POLL_SECONDS)
    raise ApplicationError(
        "Vertex operation still pending; recorded receipt retained for recovery", type="VERTEX_PENDING"
    )
