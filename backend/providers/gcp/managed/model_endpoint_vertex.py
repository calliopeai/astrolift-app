"""Vertex Endpoint lifecycle with recorded resource and operation identities (#2277).

Only a committed reviewed write reservation may submit a request. Status and
binding observe existing resources/LROs without advancing deployment. Removal
preserves the registered Model; live traffic requires explicit force_destroy,
and unproved artifact deletion or multi-model teardown is refused.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)
from _sdk.managed_service_tags import MANAGED_SERVICE_ID_LABEL
from gcp.managed._ownership import is_marked_for, label_identity_refusal, managed_service_ids
from gcp.managed.vertex_operations import VertexPlan, operation_receipt, poll_operation, resource_name

if TYPE_CHECKING:
    from collections.abc import Callable

KIND = "model_endpoint"


# Size -> machine type for the serving pool. n1-standard-2 is the
# smallest Vertex prediction accepts; bigger tiers add memory
# headroom for larger models.
_SIZE_TO_MACHINE_TYPE = {
    "small": "n1-standard-2",
    "medium": "n1-standard-4",
    "large": "n1-standard-8",
    "xlarge": "n1-standard-16",
}

_SIZE_TO_REPLICAS = {
    "small": 1,
    "medium": 1,
    "large": 2,
    "xlarge": 3,
}


class VertexAIEndpointError(ValueError):
    """Internal -- surfaced as ``ProvisionResult.errors`` /
    ``DeprovisionResult.errors`` rather than raised across the
    workflow boundary."""


@dataclass(frozen=True)
class VertexAIEndpointConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    project_id: str
    region: str

    name_prefix: str = "astrolift-model"

    default_model_artifact: str = "publishers/google/models/text-bison"

    public_endpoint_enabled_default: bool = False
    """Off by default -- production deployments use the private
    VPC endpoint. Operators with cross-VPC clients opt in via
    spec.config.public_endpoint_enabled."""

    operation_state: dict[str, Any] = field(default_factory=dict, repr=False)
    submit_phase: str = field(default="", repr=False)
    submit_nonce: str = field(default="", repr=False)
    record_operation: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)

    default_traffic_percentage: int = 100
    """Initial traffic split for the lone DeployedModel. Operators
    running A/B between models override via
    ``spec.config.traffic_percentage``."""


class VertexAIEndpointDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: VertexAIEndpointConfig,
        endpoint_client: Any | None = None,
        model_client: Any | None = None,
    ) -> None:
        self._config = config
        if endpoint_client is not None:
            self._ep = endpoint_client
        else:
            from google.cloud import aiplatform_v1

            self._ep = aiplatform_v1.EndpointServiceClient(
                client_options={"api_endpoint": f"{config.region}-aiplatform.googleapis.com"}
            )
        if model_client is not None:
            self._models = model_client
        else:
            from google.cloud import aiplatform_v1

            self._models = aiplatform_v1.ModelServiceClient(
                client_options={"api_endpoint": f"{config.region}-aiplatform.googleapis.com"}
            )

    @property
    def parent(self) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self._config.project_id) or not re.fullmatch(
            r"[a-z][a-z0-9-]{1,62}", self._config.region
        ):
            raise VertexAIEndpointError("Vertex project and region must be explicit")
        return f"projects/{self._config.project_id}/locations/{self._config.region}"

    def _owned_endpoint(self, name: str, identity: Any) -> Any | None:
        name = resource_name(name, parent=self.parent, collection="endpoints")
        endpoint = self._describe_endpoint(name)
        if endpoint is None:
            return None
        if _get(endpoint, "name") != name:
            raise VertexAIEndpointError("Vertex endpoint response identity changed")
        refusal = label_identity_refusal(
            dict(_get(endpoint, "labels", {}) or {}),
            str(identity.managed_service_id or ""),
            record_proves=bool(getattr(identity, "recorded_handle_exclusive", False)),
            resource="Vertex endpoint",
        )
        if refusal:
            raise VertexAIEndpointError(refusal)
        return endpoint

    def _recover_endpoint(self, identity: Any, display: str | None = None) -> Any | None:
        source = str(UUID(str(identity.managed_service_id or "")))
        if not UUID(source).int:
            raise VertexAIEndpointError("Vertex managed-service identity is missing")
        request = {"parent": self.parent, "page_size": 32}
        if display:
            request["filter"] = f'display_name="{display}"'
        matches, tokens = [], set()
        for _ in range(4):
            page = self._ep.list_endpoints(request=request, retry=None, timeout=10)
            # GAPIC pagers expose the first response; never consume an unbounded iterator.
            page = getattr(page, "_response", page)
            rows = list(_get(page, "endpoints", []) or [])
            if len(rows) > 32:
                raise VertexAIEndpointError("Vertex recovery inventory exceeded its bound")
            for endpoint in rows:
                name = resource_name(_get(endpoint, "name"), parent=self.parent, collection="endpoints")
                labels = dict(_get(endpoint, "labels", {}) or {})
                owners = managed_service_ids(labels)
                if source in owners and not is_marked_for(labels, source):
                    raise VertexAIEndpointError("Vertex recovery returned contradictory endpoint owners")
                if not is_marked_for(labels, source):
                    if not owners:
                        raise VertexAIEndpointError("Vertex recovery returned an unproved endpoint owner")
                    continue
                if display is None or _get(endpoint, "display_name") == display:
                    matches.append(self._owned_endpoint(name, identity))
            token = str(_get(page, "next_page_token", "") or "")
            if not token:
                if len(matches) > 1:
                    raise VertexAIEndpointError("Vertex recovery is ambiguous; operator review required")
                return matches[0] if matches else None
            if len(token) > 2048 or token in tokens:
                raise VertexAIEndpointError("Vertex recovery pagination is invalid")
            tokens.add(token)
            request["page_token"] = token
        raise VertexAIEndpointError("Vertex recovery inventory is incomplete")

    def _target(self, identity: Any, state: dict[str, Any]) -> Any | None:
        handle = str(getattr(identity, "recorded_handle", "") or getattr(identity, "handle", "") or "")
        name = state.get("endpoint", "")
        if handle:
            if not handle.startswith(f"{KIND}/"):
                raise VertexAIEndpointError("Vertex handle kind is invalid")
            suffix = handle.removeprefix(f"{KIND}/")
            if suffix.startswith("projects/"):
                parsed = resource_name(suffix, parent=self.parent, collection="endpoints")
                if name and name != parsed:
                    raise VertexAIEndpointError("Vertex recorded endpoint changed")
                name = parsed
            else:
                if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", suffix):
                    raise VertexAIEndpointError("Vertex legacy handle is invalid")
                found = self._recover_endpoint(identity, suffix)
                if found is None:
                    raise VertexAIEndpointError("Vertex legacy endpoint cannot be proved; operator recovery required")
                name = _get(found, "name")
        if name:
            state["endpoint"] = resource_name(name, parent=self.parent, collection="endpoints")
            return self._owned_endpoint(name, identity)
        return None

    def _step(self, state: dict[str, Any], phase: str, response_type: str, *, recovered: bool = False) -> bool | None:
        step = state.setdefault("steps", {}).get(phase)
        if not step:
            return False
        if step.get("state") == "complete":
            return True
        if step.get("operation"):
            result = poll_operation(self._ep, step["operation"], parent=self.parent, response_type=response_type)
            if result is None:
                return None
            if phase == "create":
                endpoint_name = resource_name(_get(result, "name"), parent=self.parent, collection="endpoints")
                if state.get("endpoint") and state["endpoint"] != endpoint_name:
                    raise VertexAIEndpointError("Vertex creation result endpoint changed")
                state["endpoint"] = endpoint_name
            if phase == "deploy":
                deployed = _get(result, "deployed_model")
                state["deployed_model_id"] = self._deployed_id(_get(deployed, "id"))
                if _get(deployed, "model") != state.get("model_artifact"):
                    raise VertexAIEndpointError("Vertex deployment result model changed")
            if phase == "mutate":
                deployed = _get(result, "deployed_model")
                if self._deployed_id(_get(deployed, "id")) != state.get("deployed_model_id") or _get(
                    deployed, "model"
                ) != state.get("model_artifact"):
                    raise VertexAIEndpointError("Vertex mutation result deployment changed")
            step["state"] = "complete"
            return True
        if recovered:
            if phase in {"create", "deploy"}:
                raise VertexAIEndpointError(
                    "Vertex owned resource observed but operation outcome is unknown; operator recovery required"
                )
            step["state"] = "complete"
            return True
        if self._config.submit_phase == phase and self._config.submit_nonce == step.get("nonce"):
            return False
        raise VertexAIEndpointError("Vertex request outcome is unknown; operator recovery required, request not resent")

    @staticmethod
    def _deployed_id(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}", value):
            raise VertexAIEndpointError("Vertex deployed-model identity is unknown")
        return value

    def _selected(self, endpoint: Any, state: dict[str, Any], *, required: bool = True) -> Any | None:
        models = list(_get(endpoint, "deployed_models", []) or [])
        desired_id = state.get("deployed_model_id")
        if desired_id:
            selected = [model for model in models if _get(model, "id") == desired_id]
        else:
            selected = [
                model
                for model in models
                if (
                    _get(model, "model") == state.get("model_artifact")
                    and _get(model, "display_name") == state.get("deployment_display_name")
                )
            ]
        if len(selected) != 1:
            if not required and not models:
                return None
            raise VertexAIEndpointError("Vertex deployed model is missing or ambiguous; operator recovery required")
        model = selected[0]
        state["deployed_model_id"] = self._deployed_id(_get(model, "id"))
        if state.get("model_artifact") and _get(model, "model") != state["model_artifact"]:
            raise VertexAIEndpointError("Vertex selected model artifact changed")
        return model

    @staticmethod
    def _replicas_available(model: Any) -> bool:
        minimum = _get(_get(model, "dedicated_resources"), "min_replica_count")
        available = _get(_get(model, "status"), "available_replica_count")
        return type(minimum) is int and type(available) is int and minimum > 0 and available >= minimum

    def operation_plan(
        self, action: str, spec: Any, *, delete_data: bool = False, force_destroy: bool = False
    ) -> VertexPlan:
        state = copy.deepcopy(self._config.operation_state)
        self.observed_state = state
        state.setdefault("steps", {})
        # These facts are recorded before any write and reused by fresh activity instances.
        source = UUID(str(spec.managed_service_id))
        if not source.int:
            raise VertexAIEndpointError("Vertex managed-service identity is invalid")
        endpoint = self._target(spec, state)
        if action == "provision":
            model = (spec.config or {}).get("model_artifact") or self._config.default_model_artifact
            resource_name(model, parent=self.parent, collection="models")
            state.setdefault("model_artifact", model)
            state.setdefault("deployment_display_name", f"astrolift-{source.hex}")
            if state["model_artifact"] != model:
                raise VertexAIEndpointError("Vertex desired model changed during provisioning")
            if endpoint is None and not state.get("endpoint"):
                endpoint = self._recover_endpoint(spec, self._base_name_for(spec=spec))
                if endpoint is not None:
                    state["endpoint"] = _get(endpoint, "name")
            created = self._step(state, "create", "Endpoint", recovered=endpoint is not None)
            if created is None:
                return VertexPlan(state, pending=True, message="Vertex endpoint creation pending")
            if not created and endpoint is None:
                return VertexPlan(
                    state,
                    phase="create",
                    method="create_endpoint",
                    request={
                        "parent": self.parent,
                        "endpoint": {
                            "display_name": self._base_name_for(spec=spec),
                            "labels": _labels_for(spec),
                            **({"network": spec.config["network"]} if spec.config.get("network") else {}),
                        },
                    },
                )
            endpoint = self._owned_endpoint(state["endpoint"], spec)
            if endpoint is None:
                return VertexPlan(state, pending=True, message="Vertex created endpoint not yet observable")
            selected = self._selected(endpoint, state, required=False)
            deployed = self._step(state, "deploy", "DeployModelResponse", recovered=selected is not None)
            if deployed is None:
                return VertexPlan(state, pending=True, message="Vertex model deployment pending")
            if not deployed and selected is None:
                cfg = spec.config or {}
                minimum = int(cfg.get("min_replica_count", _SIZE_TO_REPLICAS.get(spec.size, 1)))
                maximum = int(cfg.get("max_replica_count", max(minimum, 1) * 2))
                if minimum < 1 or maximum < minimum or maximum > 1000:
                    raise VertexAIEndpointError("Vertex replica request is invalid")
                if int(cfg.get("traffic_percentage", self._config.default_traffic_percentage)) != 100:
                    raise VertexAIEndpointError("A new single Vertex deployment requires 100 percent traffic")
                return VertexPlan(
                    state,
                    phase="deploy",
                    method="deploy_model",
                    request={
                        "endpoint": state["endpoint"],
                        "deployed_model": {
                            "model": model,
                            "display_name": state["deployment_display_name"],
                            "dedicated_resources": {
                                "machine_spec": {
                                    "machine_type": cfg.get("machine_type")
                                    or _SIZE_TO_MACHINE_TYPE.get(spec.size, "n1-standard-2")
                                },
                                "min_replica_count": minimum,
                                "max_replica_count": maximum,
                            },
                        },
                        "traffic_split": {"0": 100},
                    },
                )
            if not self._replicas_available(self._selected(endpoint, state)):
                return VertexPlan(state, pending=True, message="Vertex deployment replicas not yet available")
            return VertexPlan(state, complete=True, message="Vertex deployment operation completed and model observed")

        if endpoint is None:
            if action == "deprovision" and state.get("endpoint"):
                removed = self._step(state, "delete", "Empty", recovered=True)
                if removed is None:
                    return VertexPlan(state, pending=True, message="Vertex endpoint deletion pending")
                return VertexPlan(
                    state, complete=True, message="Vertex recorded endpoint is absent; artifact preserved"
                )
            raise VertexAIEndpointError("Vertex endpoint is not observable")
        if not state.get("model_artifact"):
            # Legacy records may recover only a unique observed deployed model, never a guessed id.
            models = list(_get(endpoint, "deployed_models", []) or [])
            if len(models) != 1:
                raise VertexAIEndpointError("Vertex legacy deployed-model recovery is missing or ambiguous")
            state["model_artifact"] = _get(models[0], "model")
            state["deployed_model_id"] = self._deployed_id(_get(models[0], "id"))
        selected = self._selected(endpoint, state, required=action != "deprovision")
        if action == "update":
            cfg = spec.config or {}
            if cfg.get("model_artifact") and cfg["model_artifact"] != state["model_artifact"]:
                raise VertexAIEndpointError("Vertex model artifact cannot be mutated in place; reprovision required")
            if "network" in cfg and cfg["network"] != _get(endpoint, "network", ""):
                raise VertexAIEndpointError("Vertex network cannot be changed by an in-place model update")
            resources = _get(selected, "dedicated_resources")
            current_machine = _get(_get(resources, "machine_spec"), "machine_type")
            requested_machine = cfg.get("machine_type") or (_SIZE_TO_MACHINE_TYPE.get(spec.size) if spec.size else None)
            if requested_machine and requested_machine != current_machine:
                raise VertexAIEndpointError("Vertex machine type cannot be mutated in place; reprovision required")
            change = {}
            for key in ("min_replica_count", "max_replica_count"):
                value = cfg.get(key)
                if value is None and key == "min_replica_count" and spec.size:
                    value = _SIZE_TO_REPLICAS.get(spec.size)
                if value is not None:
                    value = int(value)
                    if not 1 <= value <= 1000:
                        raise VertexAIEndpointError("Vertex replica request is invalid")
                    if value != _get(resources, key):
                        change[key] = value
            done = self._step(state, "mutate", "MutateDeployedModelResponse")
            if done is None:
                return VertexPlan(state, pending=True, message="Vertex replica update pending")
            if change and not done:
                minimum = change.get("min_replica_count", _get(resources, "min_replica_count", 0))
                maximum = change.get("max_replica_count", _get(resources, "max_replica_count", 0))
                if not 1 <= minimum <= maximum <= 1000:
                    raise VertexAIEndpointError("Vertex replica request is inconsistent with observed limits")
                return VertexPlan(
                    state,
                    phase="mutate",
                    method="mutate_deployed_model",
                    request={
                        "endpoint": state["endpoint"],
                        "deployed_model": {"id": state["deployed_model_id"], "dedicated_resources": change},
                        "update_mask": {"paths": [f"dedicated_resources.{k}" for k in change]},
                    },
                )
            if change and done:
                return VertexPlan(state, pending=True, message="Vertex replica update not yet observable")
            if "traffic_percentage" in cfg:
                traffic = int(cfg["traffic_percentage"])
                if traffic not in (0, 100) or len(list(_get(endpoint, "deployed_models", []) or [])) != 1:
                    raise VertexAIEndpointError(
                        "Vertex single-model traffic must be 0 or 100; multi-model changes require operator review"
                    )
                desired = {state["deployed_model_id"]: traffic} if traffic else {}
                observed = dict(_get(endpoint, "traffic_split", {}) or {}) == desired
                traffic_done = self._step(state, "traffic", "Endpoint", recovered=observed)
                if not observed and traffic_done:
                    return VertexPlan(state, pending=True, message="Vertex traffic update not yet observable")
                if not observed:
                    etag = _get(endpoint, "etag", "")
                    if not isinstance(etag, str) or not etag:
                        raise VertexAIEndpointError("Vertex traffic update requires the observed endpoint etag")
                    return VertexPlan(
                        state,
                        phase="traffic",
                        method="update_endpoint",
                        request={
                            "endpoint": {"name": state["endpoint"], "etag": etag, "traffic_split": desired},
                            "update_mask": {"paths": ["traffic_split"]},
                        },
                    )
            return VertexPlan(state, complete=True, message="Vertex update confirmed")
        if action != "deprovision":
            raise VertexAIEndpointError("Vertex operation is unsupported")
        if delete_data:
            raise VertexAIEndpointError(
                "Vertex model artifact ownership is not proven; delete_data refused, preserve the registered model"
            )
        if len(list(_get(endpoint, "deployed_models", []) or [])) > 1:
            raise VertexAIEndpointError("Vertex endpoint has other deployed models; destructive operation refused")
        if "undeploy" in state.get("steps", {}):
            removed = self._step(state, "undeploy", "UndeployModelResponse", recovered=selected is None)
            if removed is None:
                return VertexPlan(state, pending=True, message="Vertex undeploy pending")
        if selected is not None:
            if not force_destroy and _live_traffic_for(endpoint, state["deployed_model_id"]) > 0:
                raise VertexAIEndpointError("Vertex deployment has live traffic; explicit force_destroy required")
            removed = self._step(state, "undeploy", "UndeployModelResponse")
            if removed is None:
                return VertexPlan(state, pending=True, message="Vertex undeploy pending")
            if not removed:
                return VertexPlan(
                    state,
                    phase="undeploy",
                    method="undeploy_model",
                    request={
                        "endpoint": state["endpoint"],
                        "deployed_model_id": state["deployed_model_id"],
                        "traffic_split": {},
                    },
                )
            return VertexPlan(state, pending=True, message="Vertex undeployed model remains observable")
        deleted = self._step(state, "delete", "Empty")
        if deleted is None:
            return VertexPlan(state, pending=True, message="Vertex endpoint deletion pending")
        if not deleted:
            return VertexPlan(state, phase="delete", method="delete_endpoint", request={"name": state["endpoint"]})
        return VertexPlan(state, pending=True, message="Vertex endpoint deletion not yet observable")

    @driver_op(cloud="gcp", driver="model_endpoint_vertex", audit=True, heartbeat=False)
    def submit_reserved(self, plan: VertexPlan) -> None:
        step = plan.state.get("steps", {}).get(plan.phase, {})
        if (
            not plan.phase
            or self._config.submit_phase != plan.phase
            or not self._config.submit_nonce
            or self._config.submit_nonce != step.get("nonce")
            or step.get("state") != "reserved"
            or not callable(self._config.record_operation)
        ):
            raise VertexAIEndpointError("Vertex write requires a committed lifecycle reservation")
        try:
            result = getattr(self._ep, plan.method)(request=plan.request, retry=None, timeout=10)
        except Exception:
            raise VertexAIEndpointError(
                "Vertex request outcome unknown; reservation retained, operator recovery required"
            ) from None
        if plan.phase == "traffic":
            if _get(result, "name") != plan.state["endpoint"]:
                raise VertexAIEndpointError("Vertex traffic update response identity changed")
            step["state"] = "complete"
        else:
            step["operation"] = operation_receipt(result, parent=self.parent)
            step["state"] = "submitted"
        self._config.record_operation(plan.state)

    def _lifecycle(self, action: str, spec: ProvisionSpec | UpdateSpec | DeprovisionSpec, **kwargs: Any) -> VertexPlan:
        try:
            plan = self.operation_plan(action, spec, **kwargs)
            if plan.phase:
                self.submit_reserved(plan)
            return plan
        except (ValueError, TypeError) as exc:
            raise VertexAIEndpointError(str(exc)) from None

    @driver_op(cloud="gcp", driver="model_endpoint_vertex", audit=True, sensitive_kind="managed_service_provision")
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if not isinstance(spec, ProvisionSpec):
            return ProvisionResult(
                ok=False,
                handle="",
                message="Vertex requires a normalized reviewed lifecycle operation",
                errors=["vertex_refused"],
            )
        try:
            plan = self._lifecycle("provision", spec)
            return ProvisionResult(
                ok=plan.complete,
                handle=f"{KIND}/{plan.state.get('endpoint', '')}" if plan.state.get("endpoint") else "",
                message=plan.message or "Vertex provision pending",
                ready=plan.complete,
            )
        except VertexAIEndpointError as exc:
            return ProvisionResult(ok=False, handle=spec.recorded_handle, message=str(exc), errors=["vertex_refused"])

    @driver_op(cloud="gcp", driver="model_endpoint_vertex")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            plan = self._lifecycle("update", spec)
            return UpdateResult(
                ok=plan.complete,
                handle=f"{KIND}/{plan.state.get('endpoint', '')}",
                message=plan.message or "Vertex update pending",
            )
        except VertexAIEndpointError as exc:
            return UpdateResult(
                ok=False, handle=spec.handle, message=str(exc), errors=["vertex_refused"], retryable=False
            )

    @driver_op(cloud="gcp", driver="model_endpoint_vertex", audit=True, sensitive_kind="managed_service_deprovision")
    def deprovision(
        self, spec: DeprovisionSpec, *, delete_data: bool = False, force_destroy: bool = False
    ) -> DeprovisionResult:
        try:
            plan = self._lifecycle("deprovision", spec, delete_data=delete_data, force_destroy=force_destroy)
            return DeprovisionResult(
                ok=plan.complete, handle=spec.handle, message=plan.message or "Vertex removal pending"
            )
        except VertexAIEndpointError as exc:
            return DeprovisionResult(ok=False, handle=spec.handle, message=str(exc), errors=["vertex_refused"])

    @driver_op(cloud="gcp", driver="model_endpoint_vertex")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            state = copy.deepcopy(self._config.operation_state)
            endpoint = self._target(handle, state)
            if endpoint is None:
                return ServiceStatus(
                    handle=handle.handle, state="deprovisioned", message="Recorded Vertex endpoint absent"
                )
            for phase, response in (
                ("create", "Endpoint"),
                ("deploy", "DeployModelResponse"),
                ("mutate", "MutateDeployedModelResponse"),
            ):
                if phase in state.get("steps", {}) and self._step(state, phase, response) is not True:
                    return ServiceStatus(handle=handle.handle, state="provisioning", message="Vertex operation pending")
            if not state.get("deployed_model_id"):
                raise VertexAIEndpointError("Vertex deployment operation identity is not recorded; recovery required")
            if not self._replicas_available(self._selected(endpoint, state)):
                return ServiceStatus(handle=handle.handle, state="provisioning", message="Vertex replicas unavailable")
            return ServiceStatus(
                handle=handle.handle, state="available", message="Vertex completed deployment observed"
            )
        except (ValueError, TypeError) as exc:
            return ServiceStatus(handle=handle.handle, state="error", message=str(exc))

    @driver_op(cloud="gcp", driver="model_endpoint_vertex", redact_args=("config",))
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        state = copy.deepcopy(self._config.operation_state)
        endpoint = self._target(handle, state)
        if endpoint is None or self.status(handle).state != "available":
            raise VertexAIEndpointError("Vertex binding requires a completed, owned, observed deployment")
        deployed_id = self._deployed_id(state.get("deployed_model_id"))
        endpoint_id = state["endpoint"].rsplit("/", 1)[-1]
        return Binding(
            env_vars={
                "MODEL_ENDPOINT_URL": ValueRef(literal=f"https://{self._config.region}-aiplatform.googleapis.com"),
                "MODEL_DEPLOYMENT_NAME": ValueRef(literal=endpoint_id),
                "MODEL_REGION": ValueRef(literal=self._config.region),
                "MODEL_API_STYLE": ValueRef(literal="vertex_ai"),
                "MODEL_AUTH_MODE": ValueRef(literal="cloud_identity"),
                "MODEL_ENDPOINT_MODEL_ID": ValueRef(literal=deployed_id),
                "MODEL_ENDPOINT_PROVIDER": ValueRef(literal="vertex_ai"),
                "VERTEX_PROJECT_ID": ValueRef(literal=self._config.project_id),
                "VERTEX_REGION": ValueRef(literal=self._config.region),
                "VERTEX_ENDPOINT_ID": ValueRef(literal=endpoint_id),
                "VERTEX_DEPLOYED_MODEL_ID": ValueRef(literal=deployed_id),
            },
            iam_grants=[Grant(resource=state["endpoint"], actions=["roles/aiplatform.user"])],
            notes="Observed Vertex endpoint and deployed-model resource identities; runtime inference not tested.",
        )

    @driver_op(cloud="gcp", driver="model_endpoint_vertex")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        # An endpoint has no replayable snapshot. Model preservation is not an exported snapshot.
        raise VertexAIEndpointError("Vertex has no endpoint snapshot/export; preserve its registered model explicitly")

    @driver_op(cloud="gcp", driver="model_endpoint_vertex")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            ok=False,
            handle="",
            message="Vertex snapshot contains no replayable state; reviewed provision required",
            errors=["restore_not_supported"],
        )

    @driver_op(cloud="gcp", driver="model_endpoint_vertex", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "model_artifact": {"type": "string"},
                "machine_type": {"type": "string"},
                "min_replica_count": {"type": "integer", "minimum": 1},
                "max_replica_count": {"type": "integer", "minimum": 1},
                "traffic_percentage": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
                "public_endpoint_enabled": {"type": "boolean"},
                "network": {"type": "string"},
            },
        }

    @driver_op(cloud="gcp", driver="model_endpoint_vertex", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MODEL_ENDPOINT_URL": ("Vertex AI Platform HTTPS endpoint base URL"),
                "MODEL_DEPLOYMENT_NAME": "Vertex endpoint id the client predicts against",
                "MODEL_REGION": "GCP region of the endpoint",
                "MODEL_API_STYLE": "Client protocol: 'vertex_ai'",
                "MODEL_AUTH_MODE": "Credential kind: 'cloud_identity' (Workload Identity)",
                "MODEL_ENDPOINT_MODEL_ID": ("Deployed-model id within the endpoint"),
                "MODEL_ENDPOINT_PROVIDER": ("Provider literal: 'vertex_ai'"),
                "VERTEX_PROJECT_ID": "GCP project hosting the endpoint",
                "VERTEX_REGION": "Region of the endpoint",
                "VERTEX_ENDPOINT_ID": ("Resource id of the Endpoint to call predict on"),
                "VERTEX_DEPLOYED_MODEL_ID": ("Deployed-model id (string) within the endpoint"),
            },
        )

    def editable_fields(self) -> list[str]:
        return ["min_replica_count", "max_replica_count", "traffic_percentage"]

    # ---- internals ----------------------------------------------------

    def _describe_endpoint(self, name: str) -> Any | None:
        from google.api_core.exceptions import NotFound

        try:
            return self._ep.get_endpoint(request={"name": name}, retry=None, timeout=10)
        except NotFound:
            return None

    def _base_name_for(self, *, spec: ProvisionSpec) -> str:
        raw = (
            f"{self._config.name_prefix}-"
            f"{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}-"
            f"{spec.service_handle_hint or 'm'}"
        ).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        if not clean or not clean[0].isalpha():
            clean = "m" + clean
        return clean[:40]


# ----- module-level helpers --------------------------------------------


def _labels_for(spec: ProvisionSpec) -> dict[str, str]:
    def _sanitize(s: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in s.lower())

    base = {
        "astrolift-managed-by": "platform",
        "astrolift-organization": _sanitize(spec.organization_slug),
        "astrolift-app": _sanitize(spec.app_slug),
        "astrolift-environment": _sanitize(spec.environment_name),
        "astrolift-cluster": _sanitize(spec.tenant_cluster_id),
        "astrolift-isolation": _sanitize(spec.isolation),
    }
    # Per-binding cost-attribution keys (#438). GCP labels are
    # lowercase + [a-z0-9_-], so the dotted/slash form
    # ``astrolift.io/binding`` becomes ``astrolift-binding``.
    if spec.binding_id:
        base["astrolift-binding"] = _sanitize(spec.binding_id)
    if spec.managed_service_id:
        base["astrolift-managed-service-id"] = _sanitize(spec.managed_service_id)
        base[MANAGED_SERVICE_ID_LABEL] = _sanitize(spec.managed_service_id)
    for k, v in (spec.tags or {}).items():
        base[f"astrolift-extra-{_sanitize(k)}"] = _sanitize(str(v))
    return base


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _live_traffic_for(endpoint: Any, deployed_id: str) -> int:
    """Read the endpoint's traffic_split map (dict-like or attr-like)
    and return the integer traffic % for the named deployed model.
    Missing keys / non-numeric values resolve to 0 so callers can
    cleanly check ``> 0`` for live-traffic gating."""
    split = _get(endpoint, "traffic_split", None)
    if split is None:
        return 0
    val = split.get(deployed_id)
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0
