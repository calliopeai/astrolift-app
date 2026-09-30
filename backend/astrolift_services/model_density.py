"""Tenant-owned shared model inventory, separate requested and observed facts."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

import strawberry
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_operations import prometheus_client as prom
from astrolift_services.model_observations import (
    ModelMetricObservation,
    ModelObservationState,
    _target,
    empty_observations,
    fail_observations,
    observation_window,
    parse_observation_rows,
    prometheus_endpoint,
)

INVENTORY_LIMIT = 20
CAPACITY_FRESHNESS_SECONDS = 1800
_QUANTITY = re.compile(r"([0-9]+(?:\.[0-9]+)?)(m|Ki|Mi|Gi|Ti)?\Z")
_OBSERVED = {
    "running_replicas",
    "ready_replicas",
    "cpu_usage",
    "memory_usage",
    "cpu_requests",
    "memory_requests",
}


@strawberry.type
class ModelResourceRequests:
    source: str
    observed_at: datetime | None
    replicas: int | None
    cpu_cores_per_replica: float | None
    memory_bytes_per_replica: float | None
    gpu_devices_per_replica: int | None
    gpu_resource: str | None
    total_cpu_cores: float | None
    total_memory_bytes: float | None
    total_gpu_devices: int | None


@strawberry.type
class SharedModelDensityRow:
    service_id: GUID
    name: str
    status: str
    desired: ModelResourceRequests
    applied: ModelResourceRequests | None
    observations: list[ModelMetricObservation]


@strawberry.type
class ModelGpuCapacity:
    resource: str
    devices: int


@strawberry.type
class ModelClusterCapacity:
    state: ModelObservationState
    source: str
    observed_at: datetime | None
    gpu_devices: list[ModelGpuCapacity]
    cpu_cores: float | None = None
    memory_bytes: float | None = None
    vram_bytes: float | None = None
    freshness_seconds: int = CAPACITY_FRESHNESS_SECONDS


@strawberry.type
class ClusterModelDensity:
    cluster_id: GUID
    start: datetime
    end: datetime
    retrieved_at: datetime
    model_count: int
    returned_count: int
    items: list[SharedModelDensityRow]
    capacity: ModelClusterCapacity
    scope: str = "organization_cluster_owned_models"
    source: str = "persisted_model_inventory"
    inventory_limit: int = INVENTORY_LIMIT
    truncated: bool = False


def _quantity(value):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    rendered = str(value)
    if len(rendered) > 32 or not (match := _QUANTITY.fullmatch(rendered)):
        return None
    factors = {None: 1, "m": 0.001, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}
    result = float(match[1]) * factors[match[2]]
    return result if math.isfinite(result) else None


def _integer(value, maximum=1024):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum else None


def resource_requests(config, *, source, observed_at):
    config = config if isinstance(config, dict) else {}
    replicas = _integer(config.get("replicas"), 32)
    gpu = _integer(config.get("gpu"), 16)
    cpu, memory = _quantity(config.get("cpu")), _quantity(config.get("memory"))
    mig = config.get("mig_profile")
    resource = (
        f"nvidia.com/mig-{mig}"
        if isinstance(mig, str) and re.fullmatch(r"[1-7]g\.[0-9]+gb", mig)
        else "nvidia.com/gpu"
        if gpu is not None and gpu > 0
        else None
    )
    return ModelResourceRequests(
        source=source,
        observed_at=observed_at,
        replicas=replicas,
        cpu_cores_per_replica=cpu,
        memory_bytes_per_replica=memory,
        gpu_devices_per_replica=gpu,
        gpu_resource=resource,
        total_cpu_cores=cpu * replicas if cpu is not None and replicas is not None else None,
        total_memory_bytes=memory * replicas if memory is not None and replicas is not None else None,
        total_gpu_devices=gpu * replicas if gpu is not None and replicas is not None else None,
    )


def _capacity(cluster):
    result = ModelClusterCapacity(
        state=ModelObservationState.NO_DATA,
        source="persisted_gpu_capability_probe",
        observed_at=None,
        gpu_devices=[],
    )
    if cluster.organization_id is None:
        result.state = ModelObservationState.UNSUPPORTED
        result.source = "unsupported_tenant_node_pool_mapping"
        return result
    caps = cluster.capabilities if isinstance(cluster.capabilities, dict) else {}
    gpu = caps.get("gpu")
    if not isinstance(gpu, dict) or cluster.capabilities_probed_at is None:
        return result
    totals, mig = gpu.get("total"), gpu.get("mig_total")
    if not isinstance(totals, dict) or not isinstance(mig, dict):
        result.state = ModelObservationState.UNAVAILABLE
        return result
    rows = []
    for key, count in list(totals.items()) + [(f"nvidia.com/mig-{key}", count) for key, count in mig.items()]:
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"(?:nvidia\.com/gpu|amd\.com/gpu|nvidia\.com/mig-[1-7]g\.[0-9]+gb)", key)
            or _integer(count, 1_000_000) is None
        ):
            result.state = ModelObservationState.UNAVAILABLE
            return result
        rows.append(ModelGpuCapacity(resource=key, devices=count))
    result.observed_at = cluster.capabilities_probed_at
    result.gpu_devices = rows
    now = timezone.now()
    result.state = (
        ModelObservationState.UNAVAILABLE
        if result.observed_at > now
        else ModelObservationState.STALE
        if result.observed_at < now - timedelta(seconds=CAPACITY_FRESHNESS_SECONDS)
        else ModelObservationState.AVAILABLE
    )
    return result


def density_query(targets):
    from astrolift_services.model_observations import observation_query

    # Four exact targets per bounded form POST, never a namespace fallback.
    return " or ".join(
        observation_query(guid, namespace, name, keys=_OBSERVED)
        for guid, (namespace, name) in targets.items()
    )


def cluster_density(cluster, rows, start, end, *, transport_available):
    start_unix, end_unix, step = observation_window(start, end)
    total = rows.count()
    services = list(
        rows.select_related("organization", "tenant_cluster", "tenant_cluster__provider_plugin").order_by(
            "guid"
        )[:INVENTORY_LIMIT]
    )
    items = []
    observations = {}
    targets = {}
    for service in services:
        values = empty_observations(_OBSERVED)
        observations[str(service.guid)] = values
        items.append(
            SharedModelDensityRow(
                service_id=GUID(str(service.guid)),
                name=service.name,
                status=service.status,
                desired=resource_requests(
                    service.config, source="desired_config", observed_at=service.updated_at
                ),
                applied=resource_requests(
                    service.applied_config,
                    source="last_provider_applied_config",
                    observed_at=service.operation_completed_at,
                )
                if isinstance(service.applied_config, dict)
                else None,
                observations=list(values.values()),
            )
        )
        if not transport_available:
            fail_observations(values)
            continue
        try:
            targets[str(service.guid)] = _target(service)
        except (ValueError, TypeError, AttributeError):
            fail_observations(values)
    if targets:
        endpoint, auth = prometheus_endpoint(cluster)
        if endpoint:
            target_items = list(targets.items())
            for index in range(0, len(target_items), 4):
                batch = dict(target_items[index : index + 4])
                selected = {guid: observations[guid] for guid in batch}
                try:
                    matrix = prom.query_range(
                        endpoint=endpoint,
                        query=density_query(batch),
                        start_unix=start_unix,
                        end_unix=end_unix,
                        step_seconds=step,
                        timeout=3,
                        auth=auth,
                        strict=True,
                        max_series=24,
                        max_samples=120,
                        max_response_bytes=524288,
                        request_method="POST",
                        max_request_bytes=65536,
                    )
                    parse_observation_rows(matrix, selected, batch, end_unix, step)
                except (prom.PrometheusError, ValueError, TypeError, AttributeError, OverflowError):
                    for values in selected.values():
                        fail_observations(values)
    return ClusterModelDensity(
        cluster_id=GUID(str(cluster.guid)),
        start=start,
        end=end,
        retrieved_at=timezone.now(),
        model_count=total,
        returned_count=len(items),
        items=items,
        capacity=_capacity(cluster),
        truncated=total > len(items),
    )
