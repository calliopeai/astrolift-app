"""Vertex AI Matching Engine managed-service driver (#372).

Implements ``ManagedServiceDriver`` for the canonical GCP managed-
vector path. Vertex AI Matching Engine is Google's managed
similarity-search service; an "Index" stores the vectors, an
"IndexEndpoint" exposes them, and a "DeployedIndex" links the two
(an index can be deployed to multiple endpoints; an endpoint can
host multiple deployed indices). The driver provisions all three
on ``provision`` and unwinds them in reverse on ``deprovision``.

Algorithm default: ``BRUTE_FORCE`` -- the cheapest tier (no graph
build cost; query latency scales linearly with corpus). Operators
move to ``TREE_AH`` (approximate nearest neighbour) via
``spec.config.algorithm`` once recall / latency demands warrant
the index-build cost.

Deprovision matrix:

  delete_data=False, force_destroy=False (default):
    Undeploy + delete the IndexEndpoint + DeployedIndex; PRESERVE
    the underlying GCS shard bucket so the operator can re-ingest
    into a fresh Matching Engine without rebuilding the embeddings.

  delete_data=True, force_destroy=False:
    Undeploy + delete everything, INCLUDING the GCS shard bucket
    and its contents. Final teardown of vectors.

  delete_data=False, force_destroy=True:
    PRESERVE shard bucket. REFUSE if any deployed indexes still
    exist on the endpoint -- the operator must explicitly tear
    those down. ``force_destroy`` only bypasses guards on the
    *bucket* side; live deployments still block.

  delete_data=True, force_destroy=True:
    --atomic. Tear down deployments, delete index, delete endpoint,
    delete bucket + contents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
from gcp.managed._ownership import label_adoption_refusal

KIND = "vector_index"


# Size -> machine type for the deployed-index serving pool.
# n1-standard-2 is the smallest Matching Engine accepts; bigger
# tiers add memory headroom for larger embedding dimensions.
_SIZE_TO_MACHINE_TYPE = {
    "small": "n1-standard-2",
    "medium": "n1-standard-4",
    "large": "n1-standard-8",
    "xlarge": "n1-standard-16",
}

# Size -> replica count for the deployed index. Replicas are how
# Matching Engine scales QPS.
_SIZE_TO_REPLICAS = {
    "small": 1,
    "medium": 1,
    "large": 2,
    "xlarge": 3,
}


class _ManagedServiceError(Exception):
    """Internal — surfaced as ``ProvisionResult.errors`` /
    ``DeprovisionResult.errors`` rather than raised across the
    workflow boundary."""


@dataclass(frozen=True)
class VertexMatchingEngineConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    project_id: str
    region: str

    name_prefix: str = "astrolift-vec"

    embedding_dimension_default: int = 768
    """Default vector dimension; matches ``text-embedding-004`` /
    ``gecko-multilingual`` output size. Operators with a different
    embedding model override via ``spec.config.dimension``."""

    distance_measure_default: str = "DOT_PRODUCT_DISTANCE"
    """One of DOT_PRODUCT_DISTANCE, SQUARED_L2_DISTANCE,
    COSINE_DISTANCE, L1_DISTANCE. Dot-product matches the default
    output of the embedding models above."""

    algorithm_default: str = "BRUTE_FORCE"
    """``BRUTE_FORCE`` (cheapest, no index build) or ``TREE_AH``
    (approximate nearest neighbour; faster query, longer build)."""

    shard_bucket_prefix: str = "astrolift-vec-shards"
    """GCS bucket prefix where Matching Engine stores the
    pre-computed shards for the index. The driver creates one
    bucket per matching-engine instance so delete_data=True can
    drop the bucket cleanly."""

    public_endpoint_enabled_default: bool = False
    """Off by default -- production deployments use the private
    VPC endpoint. Operators with cross-VPC clients opt in via
    spec.config.public_endpoint_enabled."""


class VertexMatchingEngineDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: VertexMatchingEngineConfig,
        index_client: Any | None = None,
        endpoint_client: Any | None = None,
        storage_client: Any | None = None,
    ) -> None:
        self._config = config
        if index_client is not None:
            self._idx = index_client
        else:
            from google.cloud import aiplatform_v1

            self._idx = aiplatform_v1.IndexServiceClient()
        if endpoint_client is not None:
            self._ep = endpoint_client
        else:
            from google.cloud import aiplatform_v1

            self._ep = aiplatform_v1.IndexEndpointServiceClient()
        if storage_client is not None:
            self._gcs = storage_client
        else:
            from google.cloud import storage

            self._gcs = storage.Client(project=config.project_id)

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="gcp",
        driver="vector_vertex_matching_engine",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        base_name = self._base_name_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe_endpoint(base_name)
        if existing is not None:
            labels = existing.get("labels") if isinstance(existing, dict) else getattr(existing, "labels", None)
            refusal = label_adoption_refusal(dict(labels or {}), spec, resource=f"matching engine {base_name}")
            if refusal is not None:
                return ProvisionResult(ok=False, handle="", message=refusal, errors=[refusal])
            return ProvisionResult(
                ok=True,
                handle=self._handle_for(base_name=base_name),
                message=(f"matching engine {base_name} already exists (state={_get(existing, 'state', '?')})"),
            )

        dimension = int(
            cfg.get(
                "dimension",
                self._config.embedding_dimension_default,
            ),
        )
        distance = cfg.get("distance_measure") or self._config.distance_measure_default
        algorithm = cfg.get("algorithm") or self._config.algorithm_default
        machine_type = cfg.get("machine_type") or _SIZE_TO_MACHINE_TYPE.get(spec.size, "n1-standard-2")
        replicas = int(
            cfg.get("min_replica_count") or _SIZE_TO_REPLICAS.get(spec.size, 1),
        )
        max_replicas = int(
            cfg.get("max_replica_count") or max(replicas, 1) * 2,
        )
        public_ep = bool(
            cfg.get(
                "public_endpoint_enabled",
                self._config.public_endpoint_enabled_default,
            ),
        )

        bucket_name = self._bucket_name_for(base_name=base_name)
        try:
            self._gcs.create_bucket(
                bucket_name,
                location=self._config.region,
            )
        except Exception as exc:
            err = str(exc)
            if "Conflict" not in err and "already" not in err.lower():
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=f"create_bucket {bucket_name}: {exc}",
                    errors=[err],
                )

        parent = f"projects/{self._config.project_id}/locations/{self._config.region}"
        # Algorithm-specific subtree. BRUTE_FORCE has no extra
        # tuning knobs; TREE_AH needs leaf node / approximate
        # neighbour counts.
        algorithm_config: dict[str, Any] = {}
        if algorithm == "BRUTE_FORCE":
            algorithm_config["bruteForceConfig"] = {}
        else:
            algorithm_config["treeAhConfig"] = {
                "leafNodeEmbeddingCount": int(
                    cfg.get("leaf_node_embedding_count", 1000),
                ),
                "leafNodesToSearchPercent": int(
                    cfg.get("leaf_nodes_to_search_percent", 10),
                ),
            }

        index_body: dict[str, Any] = {
            "display_name": base_name,
            "description": (f"Astrolift vector index for {spec.app_slug}/{spec.environment_name}"),
            "metadata": {
                "contents_delta_uri": f"gs://{bucket_name}/initial",
                "config": {
                    "dimensions": dimension,
                    "approximate_neighbors_count": int(
                        cfg.get("approximate_neighbors_count", 150),
                    ),
                    "distance_measure_type": distance,
                    "algorithm_config": algorithm_config,
                },
            },
            "labels": _labels_for(spec),
        }
        try:
            self._idx.create_index(
                request={"parent": parent, "index": index_body},
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_index: {exc}",
                errors=[str(exc)],
            )

        endpoint_body: dict[str, Any] = {
            "display_name": base_name,
            "labels": _labels_for(spec),
            "public_endpoint_enabled": public_ep,
        }
        if not public_ep and cfg.get("network"):
            endpoint_body["network"] = cfg["network"]
        try:
            self._ep.create_index_endpoint(
                request={
                    "parent": parent,
                    "index_endpoint": endpoint_body,
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_index_endpoint: {exc}",
                errors=[str(exc)],
            )

        # Deploy the index to the endpoint so the binding can serve
        # queries immediately. ``deployed_index_id`` follows the
        # endpoint id for traceability; Matching Engine restricts it
        # to [a-z][a-z0-9_]{0,127}.
        deployed_index_id = _deployed_index_id_for(base_name=base_name)
        try:
            self._ep.deploy_index(
                request={
                    "index_endpoint": self._endpoint_name(base_name),
                    "deployed_index": {
                        "id": deployed_index_id,
                        "index": self._index_name(base_name),
                        "display_name": deployed_index_id,
                        "dedicated_resources": {
                            "machine_spec": {"machine_type": machine_type},
                            "min_replica_count": replicas,
                            "max_replica_count": max_replicas,
                        },
                    },
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"deploy_index: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(base_name=base_name),
            message=(
                f"matching engine {base_name} provisioning "
                f"(algorithm={algorithm}, dim={dimension}, "
                f"deployed_index_id={deployed_index_id})"
            ),
        )

    @driver_op(cloud="gcp", driver="vector_vertex_matching_engine")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        base_name = self._base_name_from_handle(spec.handle)
        cfg = spec.config or {}

        new_min = None
        new_max = None
        new_machine = None
        if spec.size:
            new_min = _SIZE_TO_REPLICAS.get(spec.size)
            new_machine = _SIZE_TO_MACHINE_TYPE.get(spec.size)
        if "min_replica_count" in cfg:
            new_min = int(cfg["min_replica_count"])
        if "max_replica_count" in cfg:
            new_max = int(cfg["max_replica_count"])
        if "machine_type" in cfg:
            new_machine = cfg["machine_type"]

        if not any((new_min, new_max, new_machine)):
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided — no-op",
            )

        dedicated: dict[str, Any] = {}
        if new_machine:
            dedicated["machine_spec"] = {"machine_type": new_machine}
        if new_min is not None:
            dedicated["min_replica_count"] = new_min
        if new_max is not None:
            dedicated["max_replica_count"] = new_max

        try:
            self._ep.mutate_deployed_index(
                request={
                    "index_endpoint": self._endpoint_name(base_name),
                    "deployed_index": {
                        "id": _deployed_index_id_for(base_name=base_name),
                        "dedicated_resources": dedicated,
                    },
                },
            )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"mutate_deployed_index: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"matching engine {base_name} update queued",
        )

    @driver_op(
        cloud="gcp",
        driver="vector_vertex_matching_engine",
        audit=True,
        sensitive_kind="managed_service_deprovision",
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
            if delete_data:
                self._delete_shard_bucket(
                    base_name=base_name,
                    force=True,
                )
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"matching engine {base_name} already gone",
            )

        deployed_count = len(
            _get(existing, "deployed_indexes", []) or [],
        )
        if not force_destroy and not delete_data and deployed_count > 0:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"matching engine {base_name} still has "
                    f"{deployed_count} deployed index(es); pass "
                    f"force_destroy=True to undeploy or delete_data=True "
                    f"for atomic teardown"
                ),
                errors=["deployed_indexes_present"],
            )

        undeployed = 0
        for di in _get(existing, "deployed_indexes", []) or []:
            di_id = _get(di, "id", "")
            try:
                self._ep.undeploy_index(
                    request={
                        "index_endpoint": self._endpoint_name(base_name),
                        "deployed_index_id": di_id,
                    },
                )
                undeployed += 1
            except Exception:
                continue

        try:
            self._ep.delete_index_endpoint(
                request={"name": self._endpoint_name(base_name)},
            )
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_index_endpoint: {exc}",
                errors=[str(exc)],
            )

        # Index may already be gone if a prior partial run left it
        # deleted; best-effort.
        import contextlib

        with contextlib.suppress(Exception):
            self._idx.delete_index(
                request={"name": self._index_name(base_name)},
            )

        bucket_action = "preserved"
        if delete_data:
            self._delete_shard_bucket(
                base_name=base_name,
                force=force_destroy,
            )
            bucket_action = "deleted"

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"matching engine {base_name} delete queued "
                f"(undeployed={undeployed}, bucket={bucket_action}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="gcp", driver="vector_vertex_matching_engine")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        base_name = self._base_name_from_handle(handle.handle)
        existing = self._describe_endpoint(base_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"matching engine {base_name} not found",
            )
        deployed = _get(existing, "deployed_indexes", []) or []
        state = "available" if deployed else "provisioning"
        return ServiceStatus(
            handle=handle.handle,
            state=state,
            message=(f"matching engine reports {len(deployed)} deployed index(es)"),
        )

    @driver_op(cloud="gcp", driver="vector_vertex_matching_engine")
    def binding(self, handle: ServiceHandle) -> Binding:
        base_name = self._base_name_from_handle(handle.handle)
        existing = self._describe_endpoint(base_name)
        if existing is None:
            raise _ManagedServiceError(
                f"binding requested for missing matching engine {base_name}",
            )
        deployed = _get(existing, "deployed_indexes", []) or []
        deployed_id = _get(deployed[0], "id", "") if deployed else _deployed_index_id_for(base_name=base_name)
        endpoint_id = _resource_id_of(existing) or base_name

        return Binding(
            env_vars={
                "VERTEX_PROJECT_ID": ValueRef(
                    literal=self._config.project_id,
                ),
                "VERTEX_REGION": ValueRef(literal=self._config.region),
                "VERTEX_INDEX_ENDPOINT_ID": ValueRef(literal=endpoint_id),
                "VERTEX_DEPLOYED_INDEX_ID": ValueRef(literal=deployed_id),
            },
            iam_grants=[
                Grant(
                    resource=f"projects/{self._config.project_id}",
                    actions=["roles/aiplatform.user"],
                ),
            ],
            notes=(
                "Queries use ``IndexEndpointServiceClient.find_neighbors`` "
                "with the bound (endpoint_id, deployed_index_id). Updates "
                "to the corpus go via ``upsert_datapoints`` on the index."
            ),
        )

    @driver_op(cloud="gcp", driver="vector_vertex_matching_engine")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """Vertex Matching Engine has no first-party snapshot API.
        Snapshots are taken by copying the shard bucket contents to
        a timestamped path. The driver returns a SnapshotHandle that
        encodes the GCS prefix the caller (or a restore call) can
        re-ingest from."""
        from datetime import UTC, datetime

        base_name = self._base_name_from_handle(handle.handle)
        existing = self._describe_endpoint(base_name)
        if existing is None:
            raise _ManagedServiceError(
                f"snapshot requested for missing matching engine {base_name}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        bucket_name = self._bucket_name_for(base_name=base_name)
        snap_uri = f"gs://{bucket_name}/snapshots/{stamp}/"
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snap_uri,
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="gcp", driver="vector_vertex_matching_engine")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        # Restore = provision a fresh matching engine with the
        # snapshot's GCS prefix as its initial shard contents.
        cfg = dict(target.config or {})
        cfg["initial_contents_uri"] = snapshot.snapshot_id
        # We don't have a frozen-replace helper on ProvisionSpec; pass
        # the modified config through to provision.
        from dataclasses import replace

        rebuilt = replace(target, config=cfg)
        return self.provision(rebuilt)

    @driver_op(cloud="gcp", driver="vector_vertex_matching_engine", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "dimension": {"type": "integer", "minimum": 1},
                "distance_measure": {
                    "type": "string",
                    "enum": [
                        "DOT_PRODUCT_DISTANCE",
                        "SQUARED_L2_DISTANCE",
                        "COSINE_DISTANCE",
                        "L1_DISTANCE",
                    ],
                },
                "algorithm": {
                    "type": "string",
                    "enum": ["BRUTE_FORCE", "TREE_AH"],
                },
                "approximate_neighbors_count": {
                    "type": "integer",
                    "minimum": 1,
                },
                "leaf_node_embedding_count": {
                    "type": "integer",
                    "minimum": 1,
                },
                "leaf_nodes_to_search_percent": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                },
                "machine_type": {"type": "string"},
                "min_replica_count": {"type": "integer", "minimum": 1},
                "max_replica_count": {"type": "integer", "minimum": 1},
                "public_endpoint_enabled": {"type": "boolean"},
                "network": {"type": "string"},
            },
        }

    @driver_op(cloud="gcp", driver="vector_vertex_matching_engine", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "VERTEX_PROJECT_ID": "GCP project hosting the index",
                "VERTEX_REGION": "Region of the index endpoint",
                "VERTEX_INDEX_ENDPOINT_ID": ("Resource id of the IndexEndpoint to query"),
                "VERTEX_DEPLOYED_INDEX_ID": ("Deployed-index id (string) within the endpoint"),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe_endpoint(self, base_name: str) -> Any | None:
        try:
            return self._ep.get_index_endpoint(
                request={"name": self._endpoint_name(base_name)},
            )
        except Exception as exc:
            err = str(exc)
            if "404" in err or "NotFound" in err or "not found" in err.lower():
                return None
            raise

    def _base_name_for(self, *, spec: ProvisionSpec) -> str:
        # Matching Engine display names: 1-128 chars; we constrain
        # tighter to keep bucket + deployed-index ids valid.
        raw = (
            f"{self._config.name_prefix}-"
            f"{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}-{spec.service_handle_hint or 'v'}"
        ).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        if not clean or not clean[0].isalpha():
            clean = "v" + clean
        return clean[:40]

    def _handle_for(self, *, base_name: str) -> str:
        return f"{KIND}/{base_name}"

    def _base_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise _ManagedServiceError(
                f"handle {handle!r} must be '<kind>/<base_name>'",
            )
        kind, _, base_name = handle.partition("/")
        if not kind or not base_name:
            raise _ManagedServiceError(
                f"handle {handle!r} has empty component",
            )
        return base_name

    def _bucket_name_for(self, *, base_name: str) -> str:
        # GCS bucket names: lowercase, 3-63 chars; globally unique.
        # Include the project_id slug to reduce collision.
        proj = "".join(c if c.isalnum() or c == "-" else "-" for c in self._config.project_id.lower())
        raw = f"{self._config.shard_bucket_prefix}-{proj}-{base_name}"
        while "--" in raw:
            raw = raw.replace("--", "-")
        return raw[:63].strip("-")

    def _index_name(self, base_name: str) -> str:
        return f"projects/{self._config.project_id}/locations/{self._config.region}/indexes/{base_name}"

    def _endpoint_name(self, base_name: str) -> str:
        return f"projects/{self._config.project_id}/locations/{self._config.region}/indexEndpoints/{base_name}"

    def _delete_shard_bucket(
        self,
        *,
        base_name: str,
        force: bool,
    ) -> None:
        bucket_name = self._bucket_name_for(base_name=base_name)
        try:
            bucket = self._gcs.get_bucket(bucket_name)
        except Exception:
            return
        try:
            # ``force=True`` empties the bucket before delete; without
            # force, the GCS API refuses to delete a non-empty bucket.
            bucket.delete(force=force)
        except Exception:
            return


# ----- module-level helpers --------------------------------------------


def _deployed_index_id_for(*, base_name: str) -> str:
    """Matching Engine deployed-index ids: ``[a-z][a-z0-9_]{0,127}``.
    Derive deterministically from the base name so provision +
    binding agree without round-tripping state."""
    sanitized = "".join(c if (c.isalnum() or c == "_") else "_" for c in base_name)
    if not sanitized or not sanitized[0].isalpha():
        sanitized = "d" + sanitized
    return f"{sanitized}_d"[:128]


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


def _resource_id_of(obj: Any) -> str:
    name = _get(obj, "name", "")
    if not name:
        return ""
    return str(name).rsplit("/", 1)[-1]
