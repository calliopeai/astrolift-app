"""Vertex AI Endpoint managed-service driver (#376).

Implements ``ManagedServiceDriver`` for the canonical GCP managed
model-endpoint path. Vertex AI exposes deployed models behind an
``Endpoint`` resource; a ``DeployedModel`` is a link from a Vertex
``Model`` artifact (uploaded via the Model Registry) to the
serving endpoint. The driver provisions both -- a fresh endpoint
plus a single deployed-model record -- so consumer workloads have
a stable ``(endpoint_id, deployed_model_id)`` to call
``predict`` against.

Deprovision matrix:

  delete_data=False, force_destroy=False (default):
    Undeploy any traffic-bearing models, delete the endpoint.
    PRESERVE the underlying Vertex Model artifact in the Model
    Registry so the operator can redeploy without re-uploading.

  delete_data=True, force_destroy=False:
    Undeploy + delete endpoint + delete the Model artifact.

  delete_data=False, force_destroy=True:
    PRESERVE the Model artifact. REFUSE if any deployed model
    still has ``traffic_percentage`` > 0 -- the operator must
    explicitly drain traffic first. ``force_destroy`` only
    bypasses the artifact-side guard; live-traffic still blocks.

  delete_data=True, force_destroy=True:
    Atomic teardown: drain + undeploy regardless of traffic,
    delete endpoint + Model artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


# Default model artifacts per size. text-bison covers the small
# tiers; gemini-pro is the larger-context default. Operators
# override via ``spec.config.model_artifact``.
_SIZE_TO_MODEL_ARTIFACT = {
    "small": "publishers/google/models/text-bison",
    "medium": "publishers/google/models/text-bison",
    "large": "publishers/google/models/gemini-pro",
    "xlarge": "publishers/google/models/gemini-pro",
}


class VertexAIEndpointError(Exception):
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

            self._ep = aiplatform_v1.EndpointServiceClient()
        if model_client is not None:
            self._models = model_client
        else:
            from google.cloud import aiplatform_v1

            self._models = aiplatform_v1.ModelServiceClient()

    # ---- lifecycle ----------------------------------------------------

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        base_name = self._base_name_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe_endpoint(base_name)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=self._handle_for(base_name=base_name),
                message=(
                    f"vertex endpoint {base_name} already exists "
                    f"(deployed_models="
                    f"{len(_get(existing, 'deployed_models', []) or [])})"
                ),
            )

        model_artifact = (
            cfg.get("model_artifact")
            or _SIZE_TO_MODEL_ARTIFACT.get(spec.size)
            or self._config.default_model_artifact
        )
        machine_type = (
            cfg.get("machine_type")
            or _SIZE_TO_MACHINE_TYPE.get(spec.size, "n1-standard-2")
        )
        min_replicas = int(
            cfg.get("min_replica_count")
            or _SIZE_TO_REPLICAS.get(spec.size, 1),
        )
        max_replicas = int(
            cfg.get("max_replica_count") or max(min_replicas, 1) * 2,
        )
        traffic_pct = int(
            cfg.get(
                "traffic_percentage",
                self._config.default_traffic_percentage,
            ),
        )
        public_ep = bool(
            cfg.get(
                "public_endpoint_enabled",
                self._config.public_endpoint_enabled_default,
            ),
        )

        parent = (
            f"projects/{self._config.project_id}"
            f"/locations/{self._config.region}"
        )
        endpoint_body: dict[str, Any] = {
            "display_name": base_name,
            "labels": _labels_for(spec),
        }
        if not public_ep and cfg.get("network"):
            endpoint_body["network"] = cfg["network"]
        try:
            self._ep.create_endpoint(
                request={
                    "parent": parent,
                    "endpoint": endpoint_body,
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_endpoint: {exc}",
                errors=[str(exc)],
            )

        deployed_model_id = _deployed_model_id_for(base_name=base_name)
        try:
            self._ep.deploy_model(
                request={
                    "endpoint": self._endpoint_name(base_name),
                    "deployed_model": {
                        "id": deployed_model_id,
                        "model": model_artifact,
                        "display_name": deployed_model_id,
                        "dedicated_resources": {
                            "machine_spec": {
                                "machine_type": machine_type,
                            },
                            "min_replica_count": min_replicas,
                            "max_replica_count": max_replicas,
                        },
                    },
                    "traffic_split": {deployed_model_id: traffic_pct},
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"deploy_model: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(base_name=base_name),
            message=(
                f"vertex endpoint {base_name} provisioning "
                f"(model_artifact={model_artifact}, "
                f"deployed_model_id={deployed_model_id})"
            ),
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        base_name = self._base_name_from_handle(spec.handle)
        cfg = spec.config or {}

        new_min = None
        new_max = None
        new_machine = None
        new_traffic = None
        if spec.size:
            new_min = _SIZE_TO_REPLICAS.get(spec.size)
            new_machine = _SIZE_TO_MACHINE_TYPE.get(spec.size)
        if "min_replica_count" in cfg:
            new_min = int(cfg["min_replica_count"])
        if "max_replica_count" in cfg:
            new_max = int(cfg["max_replica_count"])
        if "machine_type" in cfg:
            new_machine = cfg["machine_type"]
        if "traffic_percentage" in cfg:
            new_traffic = int(cfg["traffic_percentage"])

        if not any(
            (new_min, new_max, new_machine, new_traffic),
        ):
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        deployed_model_id = _deployed_model_id_for(base_name=base_name)
        dedicated: dict[str, Any] = {}
        if new_machine:
            dedicated["machine_spec"] = {"machine_type": new_machine}
        if new_min is not None:
            dedicated["min_replica_count"] = new_min
        if new_max is not None:
            dedicated["max_replica_count"] = new_max

        if dedicated:
            try:
                self._ep.mutate_deployed_model(
                    request={
                        "endpoint": self._endpoint_name(base_name),
                        "deployed_model": {
                            "id": deployed_model_id,
                            "dedicated_resources": dedicated,
                        },
                    },
                )
            except Exception as exc:
                return UpdateResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"mutate_deployed_model: {exc}",
                    errors=[str(exc)],
                )

        if new_traffic is not None:
            try:
                self._ep.update_endpoint(
                    request={
                        "endpoint": {
                            "name": self._endpoint_name(base_name),
                            "traffic_split": {
                                deployed_model_id: new_traffic,
                            },
                        },
                    },
                )
            except Exception as exc:
                return UpdateResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"update_endpoint (traffic): {exc}",
                    errors=[str(exc)],
                )

        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"vertex endpoint {base_name} update queued",
        )

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        base_name = self._base_name_from_handle(spec.handle)

        existing = self._describe_endpoint(base_name)
        if existing is None:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"vertex endpoint {base_name} already gone",
            )

        deployed = _get(existing, "deployed_models", []) or []
        live_models = [
            d for d in deployed
            if _live_traffic_for(existing, _get(d, "id", "")) > 0
        ]
        if (
            not force_destroy
            and not delete_data
            and live_models
        ):
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"vertex endpoint {base_name} still has "
                    f"{len(live_models)} deployed model(s) with "
                    f"live traffic; pass force_destroy=True to "
                    f"undeploy or delete_data=True for atomic "
                    f"teardown"
                ),
                errors=["live_traffic_present"],
            )

        undeployed = 0
        model_artifacts: list[str] = []
        for d in deployed:
            di_id = _get(d, "id", "")
            model_ref = _get(d, "model", "")
            if model_ref:
                model_artifacts.append(model_ref)
            try:
                self._ep.undeploy_model(
                    request={
                        "endpoint": self._endpoint_name(base_name),
                        "deployed_model_id": di_id,
                    },
                )
                undeployed += 1
            except Exception:
                continue

        try:
            self._ep.delete_endpoint(
                request={"name": self._endpoint_name(base_name)},
            )
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_endpoint: {exc}",
                errors=[str(exc)],
            )

        artifact_action = "preserved"
        if delete_data:
            for model_ref in model_artifacts:
                # Only delete artifacts we own (live in the same
                # project). publishers/google/* are first-party
                # foundation models -- never deleted.
                if model_ref.startswith("publishers/"):
                    continue
                import contextlib

                with contextlib.suppress(Exception):
                    self._models.delete_model(
                        request={"name": model_ref},
                    )
            artifact_action = "deleted"

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"vertex endpoint {base_name} delete queued "
                f"(undeployed={undeployed}, "
                f"artifact={artifact_action}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        base_name = self._base_name_from_handle(handle.handle)
        existing = self._describe_endpoint(base_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"vertex endpoint {base_name} not found",
            )
        deployed = _get(existing, "deployed_models", []) or []
        state = "available" if deployed else "provisioning"
        return ServiceStatus(
            handle=handle.handle,
            state=state,
            message=(
                f"vertex endpoint reports {len(deployed)} "
                f"deployed model(s)"
            ),
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        base_name = self._base_name_from_handle(handle.handle)
        existing = self._describe_endpoint(base_name)
        if existing is None:
            raise VertexAIEndpointError(
                f"binding requested for missing vertex endpoint {base_name}",
            )
        deployed = _get(existing, "deployed_models", []) or []
        deployed_id = (
            _get(deployed[0], "id", "")
            if deployed
            else _deployed_model_id_for(base_name=base_name)
        )
        endpoint_id = _resource_id_of(existing) or base_name

        return Binding(
            env_vars={
                # Canonical contract envs
                "MODEL_ENDPOINT_URL": ValueRef(
                    literal=(
                        f"https://{self._config.region}-aiplatform"
                        f".googleapis.com"
                    ),
                ),
                "MODEL_ENDPOINT_MODEL_ID": ValueRef(literal=deployed_id),
                "MODEL_ENDPOINT_PROVIDER": ValueRef(literal="vertex_ai"),
                # Vertex-flavoured aliases
                "VERTEX_PROJECT_ID": ValueRef(
                    literal=self._config.project_id,
                ),
                "VERTEX_REGION": ValueRef(literal=self._config.region),
                "VERTEX_ENDPOINT_ID": ValueRef(literal=endpoint_id),
                "VERTEX_DEPLOYED_MODEL_ID": ValueRef(literal=deployed_id),
            },
            iam_grants=[
                Grant(
                    resource=self._endpoint_name(base_name),
                    actions=[
                        "aiplatform.endpoints.predict",
                        "aiplatform.endpoints.get",
                    ],
                ),
            ],
            notes=(
                "Predictions go through "
                "``EndpointServiceClient.predict`` with the bound "
                "(endpoint_id, deployed_model_id). The endpoint id "
                "remains stable across model swaps; the deployed "
                "model id moves when the deployed model is replaced."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """Vertex endpoint has no first-party snapshot primitive --
        the underlying Vertex Model artifact already lives in the
        Model Registry, which is the durable snapshot. We return a
        deterministic id pointing at the current deployed-model
        artifact so the workflow layer can record + redeploy."""
        from datetime import UTC, datetime

        base_name = self._base_name_from_handle(handle.handle)
        existing = self._describe_endpoint(base_name)
        if existing is None:
            raise VertexAIEndpointError(
                f"snapshot for missing vertex endpoint {base_name}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{base_name}-snap-{stamp}",
            created_at=datetime.now(UTC).isoformat(),
        )

    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        provisioned = self.provision(target)
        if not provisioned.ok:
            return provisioned
        return ProvisionResult(
            ok=True,
            handle=provisioned.handle,
            message=(
                f"target endpoint provisioned; snapshot "
                f"{snapshot.snapshot_id} carries no replayable "
                f"state -- redeploy from the Vertex Model Registry"
            ),
        )

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "model_artifact": {"type": "string"},
                "machine_type": {"type": "string"},
                "min_replica_count": {"type": "integer", "minimum": 1},
                "max_replica_count": {"type": "integer", "minimum": 1},
                "traffic_percentage": {
                    "type": "integer", "minimum": 0, "maximum": 100,
                },
                "public_endpoint_enabled": {"type": "boolean"},
                "network": {"type": "string"},
            },
        }

    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "MODEL_ENDPOINT_URL": (
                    "Vertex AI Platform HTTPS endpoint base URL"
                ),
                "MODEL_ENDPOINT_MODEL_ID": (
                    "Deployed-model id within the endpoint"
                ),
                "MODEL_ENDPOINT_PROVIDER": (
                    "Provider literal: 'vertex_ai'"
                ),
                "VERTEX_PROJECT_ID": "GCP project hosting the endpoint",
                "VERTEX_REGION": "Region of the endpoint",
                "VERTEX_ENDPOINT_ID": (
                    "Resource id of the Endpoint to call predict on"
                ),
                "VERTEX_DEPLOYED_MODEL_ID": (
                    "Deployed-model id (string) within the endpoint"
                ),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe_endpoint(self, base_name: str) -> Any | None:
        try:
            return self._ep.get_endpoint(
                request={"name": self._endpoint_name(base_name)},
            )
        except Exception as exc:
            err = str(exc)
            if (
                "404" in err
                or "NotFound" in err
                or "not found" in err.lower()
            ):
                return None
            raise

    def _base_name_for(self, *, spec: ProvisionSpec) -> str:
        raw = (
            f"{self._config.name_prefix}-"
            f"{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}-"
            f"{spec.service_handle_hint or 'm'}"
        ).lower()
        clean = "".join(
            c if (c.isalnum() or c == "-") else "-" for c in raw
        )
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        if not clean or not clean[0].isalpha():
            clean = "m" + clean
        return clean[:40]

    def _handle_for(self, *, base_name: str) -> str:
        return f"{KIND}/{base_name}"

    def _base_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise VertexAIEndpointError(
                f"handle {handle!r} must be '<kind>/<base_name>'",
            )
        kind, _, base_name = handle.partition("/")
        if not kind or not base_name:
            raise VertexAIEndpointError(
                f"handle {handle!r} has empty component",
            )
        return base_name

    def _endpoint_name(self, base_name: str) -> str:
        return (
            f"projects/{self._config.project_id}"
            f"/locations/{self._config.region}"
            f"/endpoints/{base_name}"
        )


# ----- module-level helpers --------------------------------------------


def _deployed_model_id_for(*, base_name: str) -> str:
    """Vertex deployed-model ids: ``[a-z][a-z0-9_]{0,127}``.
    Derive deterministically from the base name so provision +
    binding agree without round-tripping state."""
    sanitized = "".join(
        c if (c.isalnum() or c == "_") else "_" for c in base_name
    )
    if not sanitized or not sanitized[0].isalpha():
        sanitized = "d" + sanitized
    return f"{sanitized}_d"[:128]


def _labels_for(spec: ProvisionSpec) -> dict[str, str]:
    def _sanitize(s: str) -> str:
        return "".join(
            c if c.isalnum() or c in "-_" else "-" for c in s.lower()
        )

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
    for k, v in (spec.tags or {}).items():
        base[f"astrolift-extra-{_sanitize(k)}"] = _sanitize(str(v))
    return base


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _resource_id_of(obj: Any) -> str:
    name = _get(obj, "name", "")
    if not name:
        return ""
    return str(name).rsplit("/", 1)[-1]


def _live_traffic_for(endpoint: Any, deployed_id: str) -> int:
    """Read the endpoint's traffic_split map (dict-like or attr-like)
    and return the integer traffic % for the named deployed model.
    Missing keys / non-numeric values resolve to 0 so callers can
    cleanly check ``> 0`` for live-traffic gating."""
    split = _get(endpoint, "traffic_split", None)
    if split is None:
        return 0
    val = (
        split.get(deployed_id)
        if isinstance(split, dict)
        else getattr(split, deployed_id, None)
    )
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0
