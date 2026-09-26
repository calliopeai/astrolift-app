"""Google BigQuery dataset, reservation, assignment, and capacity lifecycle."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import suppress
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
from gcp.managed._ownership import label_adoption_refusal

KIND = "warehouse"
_BQ_API_ROOT = "https://bigquery.googleapis.com/bigquery/v2"
_RESERVATION_API_ROOT = "https://bigqueryreservation.googleapis.com/v1"
_DATASET_FIELDS = {
    "friendly_name": "friendlyName",
    "description": "description",
    "default_table_expiration_ms": "defaultTableExpirationMs",
    "default_partition_expiration_ms": "defaultPartitionExpirationMs",
    "labels": "labels",
    "access": "access",
    "default_encryption_configuration": "defaultEncryptionConfiguration",
    "is_case_insensitive": "isCaseInsensitive",
    "default_collation": "defaultCollation",
    "default_rounding_mode": "defaultRoundingMode",
    "max_time_travel_hours": "maxTimeTravelHours",
    "storage_billing_model": "storageBillingModel",
    "resource_tags": "resourceTags",
    "linked_dataset_source": "linkedDatasetSource",
    "external_dataset_reference": "externalDatasetReference",
    "external_catalog_dataset_options": "externalCatalogDatasetOptions",
}
_RESERVATION_FIELDS = {
    "slot_capacity": "slotCapacity",
    "ignore_idle_slots": "ignoreIdleSlots",
    "autoscale": "autoscale",
    "concurrency": "concurrency",
    "edition": "edition",
    "secondary_location": "secondaryLocation",
    "scaling_mode": "scalingMode",
    "reservation_group": "reservationGroup",
    "labels": "labels",
    "max_slots": "maxSlots",
    "scheduling_policy": "schedulingPolicy",
    "multi_region_auxiliary": "multiRegionAuxiliary",
}
_COMMITMENT_CREATE_FIELDS = {
    "slot_count": "slotCount",
    "plan": "plan",
    "renewal_plan": "renewalPlan",
    "edition": "edition",
    "multi_region_auxiliary": "multiRegionAuxiliary",
}
_COMMITMENT_MUTABLE_FIELDS = {
    "plan": "plan",
    "renewal_plan": "renewalPlan",
}
_ASSIGNMENT_FIELDS = {
    "assignee": "assignee",
    "job_type": "jobType",
    "principal": "principal",
    "scheduling_policy": "schedulingPolicy",
}
_ASSIGNMENT_IMMUTABLE_FIELDS = {
    "assignee": "assignee",
    "principal": "principal",
    "job_type": "jobType",
}
_COMMITMENT_PLANS = {
    "FLEX",
    "FLEX_FLAT_RATE",
    "TRIAL",
    "MONTHLY",
    "MONTHLY_FLAT_RATE",
    "ANNUAL",
    "ANNUAL_FLAT_RATE",
    "THREE_YEAR",
}
_RENEWAL_PLANS = _COMMITMENT_PLANS | {"NONE"}
_RENEWAL_REQUIRED_PLANS = {"TRIAL", "ANNUAL", "ANNUAL_FLAT_RATE", "THREE_YEAR"}
_IMMUTABLE_DATASET_FIELDS = {
    "linked_dataset_source": "linkedDatasetSource",
    "external_dataset_reference": "externalDatasetReference",
}


class BigQueryWarehouseError(RuntimeError):
    pass


class BigQueryWarehouseNotFound(BigQueryWarehouseError):
    pass


@dataclass(frozen=True)
class BigQueryWarehouseConfig:
    project_id: str
    location: str
    dataset_prefix: str = "astrolift"
    deletion_protection_default: bool = True
    dataset_api_endpoint: str = _BQ_API_ROOT
    reservation_api_endpoint: str = _RESERVATION_API_ROOT
    # Connections an external dataset may federate through. A connection holds
    # the credentials or service identity that reads the external source, so a
    # dataset over another tenant's connection reads what that tenant can.
    # Empty refuses every one (#2087).
    allowed_connections: tuple[str, ...] = ()


class BigQueryRestClient:
    """Authenticated request-shaped adapter over BigQuery's two REST APIs."""

    def __init__(
        self,
        *,
        dataset_api_endpoint: str = _BQ_API_ROOT,
        reservation_api_endpoint: str = _RESERVATION_API_ROOT,
        session: Any | None = None,
    ) -> None:
        self._dataset_api_endpoint = dataset_api_endpoint.rstrip("/")
        self._reservation_api_endpoint = reservation_api_endpoint.rstrip("/")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(scopes=("https://www.googleapis.com/auth/cloud-platform",))
            session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._session = session

    def get_dataset(self, project_id: str, dataset_id: str) -> dict[str, Any]:
        return self._dataset_request("GET", f"projects/{project_id}/datasets/{dataset_id}")

    def create_dataset(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._dataset_request("POST", f"projects/{project_id}/datasets", json=body)

    def patch_dataset(self, project_id: str, dataset_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._dataset_request("PATCH", f"projects/{project_id}/datasets/{dataset_id}", json=body)

    def delete_dataset(self, project_id: str, dataset_id: str, *, delete_contents: bool) -> None:
        self._dataset_request(
            "DELETE",
            f"projects/{project_id}/datasets/{dataset_id}",
            params={"deleteContents": str(delete_contents).lower()},
        )

    def list_tables(self, project_id: str, dataset_id: str) -> list[dict[str, Any]]:
        return self._paged_dataset(
            f"projects/{project_id}/datasets/{dataset_id}/tables",
            key="tables",
        )

    def get_reservation(self, name: str) -> dict[str, Any]:
        return self._reservation_request("GET", name)

    def list_reservations(self, parent: str) -> list[dict[str, Any]]:
        return self._paged_reservation(f"{parent}/reservations", key="reservations")

    def create_reservation(self, parent: str, reservation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._reservation_request(
            "POST",
            f"{parent}/reservations",
            params={"reservationId": reservation_id},
            json=body,
        )

    def patch_reservation(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        payload = dict(body)
        payload["name"] = name
        return self._reservation_request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json=payload,
        )

    def delete_reservation(self, name: str) -> None:
        self._reservation_request("DELETE", name)

    def list_assignments(self, parent: str) -> list[dict[str, Any]]:
        return self._paged_reservation(f"{parent}/assignments", key="assignments")

    def create_assignment(self, parent: str, assignment_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._reservation_request(
            "POST",
            f"{parent}/assignments",
            params={"assignmentId": assignment_id},
            json=body,
        )

    def patch_assignment(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        payload = dict(body)
        payload["name"] = name
        return self._reservation_request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json=payload,
        )

    def delete_assignment(self, name: str) -> None:
        self._reservation_request("DELETE", name)

    def get_capacity_commitment(self, name: str) -> dict[str, Any]:
        return self._reservation_request("GET", name)

    def list_capacity_commitments(self, parent: str) -> list[dict[str, Any]]:
        return self._paged_reservation(f"{parent}/capacityCommitments", key="capacityCommitments")

    def create_capacity_commitment(
        self,
        parent: str,
        commitment_id: str,
        body: dict[str, Any],
        *,
        enforce_single_admin_project_per_org: bool,
    ) -> dict[str, Any]:
        return self._reservation_request(
            "POST",
            f"{parent}/capacityCommitments",
            params={
                "capacityCommitmentId": commitment_id,
                "enforceSingleAdminProjectPerOrg": str(enforce_single_admin_project_per_org).lower(),
            },
            json=body,
        )

    def patch_capacity_commitment(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        payload = dict(body)
        payload["name"] = name
        return self._reservation_request(
            "PATCH",
            name,
            params={"updateMask": ",".join(update_mask)},
            json=payload,
        )

    def delete_capacity_commitment(self, name: str, *, force: bool) -> None:
        self._reservation_request("DELETE", name, params={"force": str(force).lower()})

    def _paged_dataset(self, resource: str, *, key: str) -> list[dict[str, Any]]:
        return self._paged(self._dataset_request, resource, key=key)

    def _paged_reservation(self, resource: str, *, key: str) -> list[dict[str, Any]]:
        return self._paged(self._reservation_request, resource, key=key)

    @staticmethod
    def _paged(request: Any, resource: str, *, key: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params = {"maxResults": "1000"} if key == "tables" else {"pageSize": "1000"}
            if page_token:
                params["pageToken"] = page_token
            payload = request("GET", resource, params=params)
            rows.extend(payload.get(key) or [])
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                return rows

    def _dataset_request(self, method: str, resource: str, **kwargs: Any) -> dict[str, Any]:
        return self._request(self._dataset_api_endpoint, method, resource, **kwargs)

    def _reservation_request(self, method: str, resource: str, **kwargs: Any) -> dict[str, Any]:
        return self._request(self._reservation_api_endpoint, method, resource, **kwargs)

    def _request(
        self,
        endpoint: str,
        method: str,
        resource: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._session.request(
            method,
            f"{endpoint}/{resource.lstrip('/')}",
            params=params,
            json=json,
            timeout=30,
        )
        if response.status_code == 404:
            raise BigQueryWarehouseNotFound(resource)
        if not 200 <= response.status_code < 300:
            detail = response.text
            with suppress(AttributeError, TypeError, ValueError):
                detail = str(response.json().get("error", {}).get("message") or detail)
            raise BigQueryWarehouseError(f"BigQuery HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else {}


class BigQueryWarehouseDriver(ManagedServiceDriver):
    def __init__(self, *, config: BigQueryWarehouseConfig, client: Any | None = None) -> None:
        self._config = config
        self._bq = client or BigQueryRestClient(
            dataset_api_endpoint=config.dataset_api_endpoint,
            reservation_api_endpoint=config.reservation_api_endpoint,
        )

    @driver_op(cloud="gcp", driver="warehouse_bigquery", audit=True, sensitive_kind="managed_service_provision")
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_bigquery_warehouse_config"])
        dataset_id = self._dataset_id(spec)
        try:
            try:
                current = self._dataset(dataset_id)
            except BigQueryWarehouseNotFound:
                self._bq.create_dataset(
                    self._config.project_id,
                    self._dataset_document(dataset_id, cfg, spec=spec),
                )
            else:
                # Reconciling rewrites the dataset's labels and access (#1961).
                refusal = label_adoption_refusal(
                    dict(current.get("labels") or {}), spec, resource=f"BigQuery dataset {dataset_id}"
                )
                if refusal is not None:
                    return ProvisionResult(False, "", refusal, [refusal])
                self._reconcile_dataset(dataset_id, cfg, spec=spec, current=current)
            self._reconcile_capacity_commitments(dataset_id, cfg)
            self._reconcile_reservations(dataset_id, cfg)
        except Exception as exc:
            return ProvisionResult(False, f"{KIND}/{dataset_id}", f"provision BigQuery warehouse: {exc}", [str(exc)])
        async_resources = bool(cfg.get("capacity_commitments") or cfg.get("reservations"))
        return ProvisionResult(
            True,
            f"{KIND}/{dataset_id}",
            f"BigQuery dataset {dataset_id} and declared capacity resources reconciled",
            ready=not async_resources,
        )

    @driver_op(cloud="gcp", driver="warehouse_bigquery")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            dataset_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        cfg = dict(spec.config or {})
        error = self._validate_config(cfg)
        if error:
            return UpdateResult(False, spec.handle, error, ["invalid_bigquery_warehouse_config"])
        try:
            current = self._dataset(dataset_id)
            self._reconcile_dataset(dataset_id, cfg, spec=None, current=current)
            self._reconcile_capacity_commitments(dataset_id, cfg)
            self._reconcile_reservations(dataset_id, cfg)
        except BigQueryWarehouseNotFound:
            return UpdateResult(False, spec.handle, f"BigQuery dataset {dataset_id} not found", ["not_found"])
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update BigQuery warehouse: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"BigQuery warehouse {dataset_id} reconciled")

    @driver_op(
        cloud="gcp",
        driver="warehouse_bigquery",
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
        try:
            dataset_id = _parse_handle(spec.handle)
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        dataset_exists = True
        try:
            self._dataset(dataset_id)
        except BigQueryWarehouseNotFound:
            dataset_exists = False
        except Exception as exc:
            return _deprovision_error(spec.handle, "describe BigQuery dataset", exc)
        try:
            orphaned_reservations = self._owned_reservations(dataset_id)
            orphaned_commitments = self._owned_commitments(dataset_id)
        except Exception as exc:
            return _deprovision_error(spec.handle, "list BigQuery capacity resources", exc)
        if not dataset_exists and not orphaned_reservations and not orphaned_commitments:
            return DeprovisionResult(True, spec.handle, f"BigQuery warehouse {dataset_id} already gone")
        protected = bool(
            (spec.config or {}).get("deletion_protection", self._config.deletion_protection_default),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"BigQuery dataset {dataset_id} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        tables: list[dict[str, Any]] = []
        if dataset_exists:
            try:
                tables = self._bq.list_tables(self._config.project_id, dataset_id)
            except Exception as exc:
                return _deprovision_error(spec.handle, "list BigQuery tables", exc)
        if tables and not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                f"BigQuery dataset {dataset_id} contains {len(tables)} table(s); set delete_data=true",
                ["dataset_not_empty"],
                retryable=False,
            )
        parent = self._capacity_parent()
        try:
            reservations = orphaned_reservations
            for reservation in reservations:
                name = str(reservation["name"])
                try:
                    assignments = self._bq.list_assignments(name)
                except BigQueryWarehouseNotFound:
                    assignments = []
                for assignment in assignments:
                    self._delete_ignoring_not_found(self._bq.delete_assignment, str(assignment["name"]))
                self._delete_ignoring_not_found(self._bq.delete_reservation, name)
            commitments = orphaned_commitments
            for commitment in commitments:
                self._delete_commitment(str(commitment["name"]), force=force_destroy)
            if dataset_exists:
                with suppress(BigQueryWarehouseNotFound):
                    self._bq.delete_dataset(self._config.project_id, dataset_id, delete_contents=delete_data)
        except Exception as exc:
            return _deprovision_error(spec.handle, f"delete BigQuery resources under {parent}", exc)
        return DeprovisionResult(
            True,
            spec.handle,
            f"BigQuery dataset {dataset_id}, {len(reservations)} reservation(s), and "
            f"{len(commitments)} capacity commitment(s) deleted",
        )

    @driver_op(cloud="gcp", driver="warehouse_bigquery")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            dataset_id = _parse_handle(handle.handle)
            self._dataset(dataset_id)
            commitments = self._owned_commitments(dataset_id)
            reservations = self._owned_reservations(dataset_id)
        except BigQueryWarehouseNotFound:
            return ServiceStatus(handle.handle, "deprovisioned", "BigQuery dataset does not exist")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe BigQuery warehouse: {exc}")
        for commitment in commitments:
            state = str(commitment.get("state") or "STATE_UNSPECIFIED")
            if state == "FAILED":
                failure = commitment.get("failureStatus") or {}
                return ServiceStatus(
                    handle.handle,
                    "error",
                    f"BigQuery capacity commitment failed: {failure.get('message') or failure}",
                )
            if state != "ACTIVE":
                return ServiceStatus(handle.handle, "provisioning", f"BigQuery capacity commitment is {state}")
        try:
            for reservation in reservations:
                for assignment in self._bq.list_assignments(str(reservation["name"])):
                    state = str(assignment.get("state") or "STATE_UNSPECIFIED")
                    if state != "ACTIVE":
                        return ServiceStatus(handle.handle, "provisioning", f"BigQuery assignment is {state}")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe BigQuery assignments: {exc}")
        return ServiceStatus(
            handle.handle,
            "available",
            f"BigQuery warehouse available ({len(reservations)} reservations, {len(commitments)} commitments)",
        )

    @driver_op(cloud="gcp", driver="warehouse_bigquery")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        dataset_id = _parse_handle(handle.handle)
        self._dataset(dataset_id)
        cfg = dict(config or {})
        access_mode = str(cfg.get("access_mode") or "write")
        if access_mode not in {"read", "write", "admin"}:
            raise BigQueryWarehouseError(f"invalid BigQuery access_mode {access_mode!r}")
        dataset_resource = f"projects/{self._config.project_id}/datasets/{dataset_id}"
        data_role = {
            "read": "roles/bigquery.dataViewer",
            "write": "roles/bigquery.dataEditor",
            "admin": "roles/bigquery.dataOwner",
        }[access_mode]
        grants = [
            Grant(dataset_resource, [data_role]),
            Grant(f"projects/{self._config.project_id}", ["roles/bigquery.jobUser"]),
        ]
        if access_mode == "admin" and cfg.get("manage_reservations"):
            grants.append(
                Grant(f"projects/{self._config.project_id}", ["roles/bigquery.resourceAdmin"]),
            )
        reservation_names = [str(row["name"]) for row in self._owned_reservations(dataset_id)]
        return Binding(
            env_vars={
                "WAREHOUSE_ENDPOINT": ValueRef(literal="https://bigquery.googleapis.com"),
                "WAREHOUSE_DATABASE": ValueRef(literal=dataset_id),
                "WAREHOUSE_URL": ValueRef(literal=f"bigquery://{self._config.project_id}/{dataset_id}"),
                "WAREHOUSE_ENGINE": ValueRef(literal="bigquery"),
                "WAREHOUSE_DEPLOYMENT": ValueRef(literal="serverless"),
                "WAREHOUSE_AUTH_MODE": ValueRef(literal="workload_identity"),
                "WAREHOUSE_REGION": ValueRef(literal=self._config.location),
                "WAREHOUSE_RESOURCE_ARN": ValueRef(literal=dataset_resource),
                "WAREHOUSE_CLUSTER_ID": ValueRef(literal=dataset_id),
                "GCP_BIGQUERY_PROJECT": ValueRef(literal=self._config.project_id),
                "GCP_BIGQUERY_DATASET": ValueRef(literal=dataset_id),
                "GCP_BIGQUERY_LOCATION": ValueRef(literal=self._config.location),
                "GCP_BIGQUERY_RESERVATIONS": ValueRef(literal=json.dumps(reservation_names, separators=(",", ":"))),
            },
            iam_grants=grants,
            notes=f"BigQuery {access_mode} access through GKE Workload Identity.",
        )

    @driver_op(cloud="gcp", driver="warehouse_bigquery")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "BigQuery snapshots are table-scoped and cannot represent a dataset plus reservations "
            "in the portable contract",
        )

    @driver_op(cloud="gcp", driver="warehouse_bigquery")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "BigQuery dataset restore is not portable; restore declared tables from BigQuery snapshots or exports",
        )

    @driver_op(cloud="gcp", driver="warehouse_bigquery", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        string_map = {"type": "object", "additionalProperties": {"type": "string"}}
        assignment = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "assignee": {"type": "string"},
                "principal": {"type": "string"},
                "job_type": {
                    "type": "string",
                    "enum": [
                        "JOB_TYPE_UNSPECIFIED",
                        "PIPELINE",
                        "QUERY",
                        "ML_EXTERNAL",
                        "BACKGROUND",
                        "CONTINUOUS",
                        "BACKGROUND_CHANGE_DATA_CAPTURE",
                        "BACKGROUND_COLUMN_METADATA_INDEX",
                        "BACKGROUND_SEARCH_INDEX_REFRESH",
                        "AUTOMATIC_MATERIALIZED_VIEW_REFRESH",
                    ],
                },
                "scheduling_policy": {
                    "type": "object",
                    "properties": {
                        "concurrency": {"type": "integer", "minimum": 0},
                        "max_slots": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
            },
            "oneOf": [{"required": ["assignee"]}, {"required": ["principal"]}],
            "additionalProperties": False,
        }
        reservation = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "slot_capacity": {"type": "integer", "minimum": 0},
                "ignore_idle_slots": {"type": "boolean"},
                "autoscale": {
                    "type": "object",
                    "properties": {"max_slots": {"type": "integer", "minimum": 0}},
                    "additionalProperties": False,
                },
                "concurrency": {"type": "integer", "minimum": 0},
                "edition": {
                    "type": "string",
                    "enum": ["ENTERPRISE", "ENTERPRISE_PLUS"],
                },
                "secondary_location": {"type": "string"},
                "scaling_mode": {
                    "type": "string",
                    "enum": ["AUTOSCALE_ONLY", "IDLE_SLOTS_ONLY", "ALL_SLOTS"],
                },
                "reservation_group": {"type": "string"},
                "labels": string_map,
                "max_slots": {"type": "integer", "minimum": 0},
                "scheduling_policy": {
                    "type": "object",
                    "properties": {
                        "concurrency": {"type": "integer", "minimum": 0},
                        "max_slots": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                "multi_region_auxiliary": {"type": "boolean"},
                "assignments": {"type": "array", "items": assignment},
            },
            "additionalProperties": False,
        }
        commitment = {
            "type": "object",
            "required": ["name", "slot_count", "plan"],
            "properties": {
                "name": {"type": "string"},
                "slot_count": {"type": "integer", "minimum": 1},
                "plan": {"type": "string", "enum": sorted(_COMMITMENT_PLANS)},
                "renewal_plan": {
                    "type": "string",
                    "enum": sorted(_RENEWAL_PLANS),
                },
                "edition": {
                    "type": "string",
                    "enum": ["STANDARD", "ENTERPRISE", "ENTERPRISE_PLUS"],
                },
                "multi_region_auxiliary": {"type": "boolean"},
            },
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "access_mode": {"type": "string", "enum": ["read", "write", "admin"], "default": "write"},
                "manage_reservations": {"type": "boolean", "default": False},
                "deletion_protection": {"type": "boolean", "default": True},
                "friendly_name": {"type": "string"},
                "description": {"type": "string"},
                "default_table_expiration_ms": {"type": "integer", "minimum": 0},
                "default_partition_expiration_ms": {"type": "integer", "minimum": 0},
                "labels": string_map,
                "access": {"type": "array", "items": {"type": "object"}},
                "default_encryption_configuration": {
                    "type": "object",
                    "required": ["kms_key_name"],
                    "properties": {"kms_key_name": {"type": "string"}},
                    "additionalProperties": False,
                },
                "is_case_insensitive": {"type": "boolean"},
                "default_collation": {"type": "string"},
                "default_rounding_mode": {
                    "type": "string",
                    "enum": ["ROUND_HALF_AWAY_FROM_ZERO", "ROUND_HALF_EVEN"],
                },
                "max_time_travel_hours": {
                    "type": "integer",
                    "enum": [48, 72, 96, 120, 144, 168],
                },
                "storage_billing_model": {
                    "type": "string",
                    "enum": ["LOGICAL", "PHYSICAL"],
                },
                "resource_tags": string_map,
                "linked_dataset_source": {
                    "type": "object",
                    "required": ["source_dataset"],
                    "properties": {
                        "source_dataset": {
                            "type": "object",
                            "required": ["project_id", "dataset_id"],
                            "properties": {
                                "project_id": {"type": "string"},
                                "dataset_id": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
                "external_dataset_reference": {
                    "type": "object",
                    "required": ["connection", "external_source"],
                    "properties": {
                        "connection": {"type": "string"},
                        "external_source": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "external_catalog_dataset_options": {
                    "type": "object",
                    "properties": {
                        "default_storage_location_uri": {"type": "string"},
                        "parameters": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
                "purchase_capacity_commitments": {"type": "boolean", "default": False},
                "enforce_single_admin_project_per_org": {"type": "boolean", "default": False},
                "capacity_commitments": {"type": "array", "items": commitment},
                "reservations": {"type": "array", "items": reservation},
            },
            "additionalProperties": False,
        }

    @driver_op(cloud="gcp", driver="warehouse_bigquery", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "WAREHOUSE_ENDPOINT": "BigQuery public API endpoint",
                "WAREHOUSE_DATABASE": "Dataset id",
                "WAREHOUSE_URL": "Portable BigQuery project/dataset URL",
                "WAREHOUSE_ENGINE": "bigquery",
                "WAREHOUSE_DEPLOYMENT": "serverless",
                "WAREHOUSE_AUTH_MODE": "workload_identity",
                "WAREHOUSE_REGION": "BigQuery dataset/reservation location",
                "WAREHOUSE_RESOURCE_ARN": "Full BigQuery dataset resource path",
                "WAREHOUSE_CLUSTER_ID": "Dataset id compatibility alias",
                "GCP_BIGQUERY_PROJECT": "Google Cloud project id",
                "GCP_BIGQUERY_DATASET": "BigQuery dataset id",
                "GCP_BIGQUERY_LOCATION": "BigQuery location",
                "GCP_BIGQUERY_RESERVATIONS": "JSON list of managed reservation resource names",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access",
            "access_mode",
            "capacity_commitments",
            "default_collation",
            "default_encryption_configuration",
            "default_partition_expiration_ms",
            "default_rounding_mode",
            "default_table_expiration_ms",
            "description",
            "friendly_name",
            "is_case_insensitive",
            "labels",
            "manage_reservations",
            "max_time_travel_hours",
            "reservations",
            "resource_tags",
            "storage_billing_model",
            "external_catalog_dataset_options",
        ]

    def _dataset(self, dataset_id: str) -> dict[str, Any]:
        return self._bq.get_dataset(self._config.project_id, dataset_id)

    def _dataset_id(self, spec: ProvisionSpec) -> str:
        raw = "_".join(
            part
            for part in (
                self._config.dataset_prefix,
                spec.organization_slug,
                spec.app_slug,
                spec.environment_name,
                spec.service_handle_hint or "warehouse",
            )
            if part
        )
        return _dataset_id(raw)

    def _dataset_document(
        self,
        dataset_id: str,
        cfg: dict[str, Any],
        *,
        spec: ProvisionSpec | None,
    ) -> dict[str, Any]:
        labels = _labels_for(spec, cfg)
        document: dict[str, Any] = {
            "datasetReference": {"projectId": self._config.project_id, "datasetId": dataset_id},
            "location": self._config.location,
            "labels": labels,
        }
        for source, target in _DATASET_FIELDS.items():
            if source in cfg and source != "labels":
                document[target] = _camelize(cfg[source])
        return document

    def _reconcile_dataset(
        self,
        dataset_id: str,
        cfg: dict[str, Any],
        *,
        spec: ProvisionSpec | None,
        current: dict[str, Any],
    ) -> None:
        if str(current.get("location") or "").lower() != self._config.location.lower():
            raise BigQueryWarehouseError(
                f"dataset {dataset_id} is in immutable location {current.get('location')!r}, "
                f"not {self._config.location!r}",
            )
        body = self._dataset_document(dataset_id, cfg, spec=spec)
        body.pop("datasetReference", None)
        body.pop("location", None)
        for source, target in _IMMUTABLE_DATASET_FIELDS.items():
            if source not in cfg:
                continue
            desired = _camelize(cfg[source])
            if current.get(target) != desired:
                raise BigQueryWarehouseError(
                    f"dataset {dataset_id} field {source!r} is immutable; create a replacement",
                )
            body.pop(target, None)
        if spec is None and "labels" not in cfg:
            body.pop("labels", None)
        if body:
            self._bq.patch_dataset(self._config.project_id, dataset_id, body)

    def _capacity_parent(self) -> str:
        return f"projects/{self._config.project_id}/locations/{self._config.location}"

    def _owned_reservations(self, dataset_id: str) -> list[dict[str, Any]]:
        prefix = f"{self._capacity_parent()}/reservations/{_capacity_namespace(dataset_id)}-"
        return [
            row
            for row in self._bq.list_reservations(self._capacity_parent())
            if str(row.get("name", "")).startswith(prefix)
        ]

    def _owned_commitments(self, dataset_id: str) -> list[dict[str, Any]]:
        prefix = f"{self._capacity_parent()}/capacityCommitments/{_capacity_namespace(dataset_id)}-"
        return [
            row
            for row in self._bq.list_capacity_commitments(self._capacity_parent())
            if str(row.get("name", "")).startswith(prefix)
        ]

    def _reconcile_capacity_commitments(self, dataset_id: str, cfg: dict[str, Any]) -> None:
        if "capacity_commitments" not in cfg:
            return
        parent = self._capacity_parent()
        existing = {str(row["name"]): row for row in self._owned_commitments(dataset_id)}
        for declaration in list(cfg.get("capacity_commitments") or []):
            commitment_id = _scoped_capacity_id(dataset_id, str(declaration["name"]))
            name = f"{parent}/capacityCommitments/{commitment_id}"
            document = {
                target: _int64_string(declaration[source]) if source == "slot_count" else declaration[source]
                for source, target in _COMMITMENT_CREATE_FIELDS.items()
                if source in declaration
            }
            current = existing.get(name)
            if current is None:
                self._bq.create_capacity_commitment(
                    parent,
                    commitment_id,
                    document,
                    enforce_single_admin_project_per_org=bool(cfg.get("enforce_single_admin_project_per_org")),
                )
                continue
            for source in ("slot_count", "edition", "multi_region_auxiliary"):
                if source not in declaration:
                    continue
                target = _COMMITMENT_CREATE_FIELDS[source]
                desired = _int64_string(declaration[source]) if source == "slot_count" else declaration[source]
                if str(current.get(target)) != str(desired):
                    raise BigQueryWarehouseError(
                        f"capacity commitment {commitment_id} field {source!r} is immutable; create a replacement",
                    )
            mutable = {
                target: declaration[source]
                for source, target in _COMMITMENT_MUTABLE_FIELDS.items()
                if source in declaration
            }
            if mutable:
                self._bq.patch_capacity_commitment(name, mutable, update_mask=sorted(mutable))

    def _reconcile_reservations(self, dataset_id: str, cfg: dict[str, Any]) -> None:
        if "reservations" not in cfg:
            return
        parent = self._capacity_parent()
        existing = {str(row["name"]): row for row in self._owned_reservations(dataset_id)}
        for declaration in list(cfg.get("reservations") or []):
            reservation_id = _scoped_capacity_id(dataset_id, str(declaration["name"]))
            name = f"{parent}/reservations/{reservation_id}"
            document = {
                target: _reservation_value(source, declaration[source])
                for source, target in _RESERVATION_FIELDS.items()
                if source in declaration
            }
            if name not in existing:
                self._bq.create_reservation(parent, reservation_id, document)
            elif document:
                self._bq.patch_reservation(name, document, update_mask=sorted(document))
            self._reconcile_assignments(name, declaration)

    def _reconcile_assignments(self, reservation_name: str, declaration: dict[str, Any]) -> None:
        if "assignments" not in declaration:
            return
        existing = {str(row["name"]): row for row in self._bq.list_assignments(reservation_name)}
        reservation_id = reservation_name.rsplit("/", 1)[-1]
        for item in list(declaration.get("assignments") or []):
            assignment_id = _scoped_capacity_id(reservation_id, str(item["name"]))
            name = f"{reservation_name}/assignments/{assignment_id}"
            document = {
                target: _assignment_value(source, item[source])
                for source, target in _ASSIGNMENT_FIELDS.items()
                if source in item
            }
            if name not in existing:
                self._bq.create_assignment(reservation_name, assignment_id, document)
                continue
            current = existing[name]
            for source, target in _ASSIGNMENT_IMMUTABLE_FIELDS.items():
                desired = document.get(target)
                actual = current.get(target)
                if desired != actual:
                    raise BigQueryWarehouseError(
                        f"assignment {assignment_id} field {source!r} is immutable; create a replacement",
                    )
            scheduling_policy = document.get("schedulingPolicy")
            if scheduling_policy is not None and scheduling_policy != current.get("schedulingPolicy"):
                self._bq.patch_assignment(
                    name,
                    {"schedulingPolicy": scheduling_policy},
                    update_mask=["schedulingPolicy"],
                )

    def _delete_commitment(self, name: str, *, force: bool) -> None:
        with suppress(BigQueryWarehouseNotFound):
            self._bq.delete_capacity_commitment(name, force=force)

    @staticmethod
    def _delete_ignoring_not_found(delete: Any, name: str) -> None:
        with suppress(BigQueryWarehouseNotFound):
            delete(name)

    def _validate_config(self, cfg: dict[str, Any]) -> str | None:
        external = cfg.get("external_dataset_reference")
        if external is not None:
            connection = str(external.get("connection") or "").strip() if isinstance(external, dict) else ""
            if connection not in {str(item).strip() for item in self._config.allowed_connections}:
                return (
                    f"external_dataset_reference.connection {connection!r} is not allowed by the cluster "
                    "install policy bigquery_allowed_connections"
                )
        access_mode = str(cfg.get("access_mode") or "write")
        if access_mode not in {"read", "write", "admin"}:
            return f"access_mode {access_mode!r} is invalid"
        if "max_time_travel_hours" in cfg and cfg["max_time_travel_hours"] not in {48, 72, 96, 120, 144, 168}:
            return "max_time_travel_hours must be a 24-hour increment from 48 through 168"
        if "default_table_expiration_ms" in cfg:
            expiration = cfg["default_table_expiration_ms"]
            if not _is_int(expiration) or (expiration != 0 and expiration < 3_600_000):
                return "default_table_expiration_ms must be 0 or at least 3600000"
        commitments = cfg.get("capacity_commitments")
        if commitments is not None and not isinstance(commitments, list):
            return "capacity_commitments must be an array"
        if commitments and not cfg.get("purchase_capacity_commitments"):
            return "capacity commitments incur committed spend; set purchase_capacity_commitments=true"
        error = _validate_named_rows(commitments or [], "capacity_commitments")
        if error:
            return error
        for index, commitment in enumerate(commitments or []):
            if not _is_int(commitment.get("slot_count")) or commitment["slot_count"] < 1:
                return f"capacity_commitments[{index}].slot_count must be a positive integer"
            plan = str(commitment.get("plan") or "")
            if plan not in _COMMITMENT_PLANS:
                return f"capacity_commitments[{index}].plan is invalid"
            edition = commitment.get("edition")
            if edition is not None and edition not in {"ENTERPRISE", "ENTERPRISE_PLUS"}:
                return f"capacity_commitments[{index}].edition is invalid"
            renewal_plan = commitment.get("renewal_plan")
            if renewal_plan is not None and renewal_plan not in _RENEWAL_PLANS:
                return f"capacity_commitments[{index}].renewal_plan is invalid"
            if plan in _RENEWAL_REQUIRED_PLANS and not renewal_plan:
                return f"capacity_commitments[{index}].renewal_plan is required for {plan}"
        reservations = cfg.get("reservations")
        if reservations is not None and not isinstance(reservations, list):
            return "reservations must be an array"
        error = _validate_named_rows(reservations or [], "reservations")
        if error:
            return error
        for index, reservation in enumerate(reservations or []):
            autoscale = reservation.get("autoscale")
            if autoscale is not None and not isinstance(autoscale, dict):
                return f"reservations[{index}].autoscale must be an object"
            autoscale = autoscale or {}
            scaling_mode = reservation.get("scaling_mode")
            if scaling_mode and "max_slots" not in reservation:
                return f"reservations[{index}].max_slots is required with scaling_mode"
            if reservation.get("max_slots") and not scaling_mode:
                return f"reservations[{index}].scaling_mode is required with max_slots"
            if "max_slots" in reservation and autoscale:
                return f"reservations[{index}] cannot combine max_slots with autoscale"
            if "max_slots" in reservation and not _is_int(reservation["max_slots"]):
                return f"reservations[{index}].max_slots must be an integer"
            if "slot_capacity" in reservation and not _is_int(reservation["slot_capacity"]):
                return f"reservations[{index}].slot_capacity must be an integer"
            if (
                "max_slots" in reservation
                and reservation["max_slots"]
                and reservation["max_slots"] <= (reservation.get("slot_capacity") or 0)
            ):
                return f"reservations[{index}].max_slots must exceed slot_capacity"
            ignore_idle = reservation.get("ignore_idle_slots")
            if scaling_mode == "AUTOSCALE_ONLY" and ignore_idle is not True:
                return f"reservations[{index}].ignore_idle_slots must be true for AUTOSCALE_ONLY"
            if scaling_mode in {"IDLE_SLOTS_ONLY", "ALL_SLOTS"} and ignore_idle is not False:
                return f"reservations[{index}].ignore_idle_slots must be false for {scaling_mode}"
            error = _validate_scheduling_policy(
                reservation.get("scheduling_policy"),
                f"reservations[{index}].scheduling_policy",
            )
            if error:
                return error
            assignments = reservation.get("assignments")
            if assignments is not None and not isinstance(assignments, list):
                return f"reservations[{index}].assignments must be an array"
            error = _validate_named_rows(assignments or [], f"reservations[{index}].assignments")
            if error:
                return error
            for assignment_index, assignment in enumerate(assignments or []):
                targets = [field for field in ("assignee", "principal") if assignment.get(field)]
                if len(targets) != 1:
                    return (
                        f"reservations[{index}].assignments[{assignment_index}] requires exactly one of "
                        "assignee/principal"
                    )
                assignment_path = f"reservations[{index}].assignments[{assignment_index}]"
                policy = assignment.get("scheduling_policy")
                if not assignment.get("job_type") and not policy:
                    return f"{assignment_path}.job_type is required without scheduling_policy"
                if policy:
                    if not str(assignment.get("assignee") or "").startswith("projects/"):
                        return f"{assignment_path}.scheduling_policy requires a project assignee"
                    if assignment.get("job_type") not in {None, "JOB_TYPE_UNSPECIFIED"}:
                        return (
                            f"{assignment_path}.job_type must be unset or JOB_TYPE_UNSPECIFIED with scheduling_policy"
                        )
                error = _validate_scheduling_policy(policy, f"{assignment_path}.scheduling_policy")
                if error:
                    return error
        return None


def _labels_for(spec: ProvisionSpec | None, cfg: dict[str, Any]) -> dict[str, str]:
    labels = dict(cfg.get("labels") or {})
    if spec is not None:
        labels.update(
            {
                "astrolift_managed_by": "platform",
                "astrolift_organization": spec.organization_slug,
                "astrolift_app": spec.app_slug,
                "astrolift_environment": spec.environment_name,
            },
        )
        if spec.binding_id:
            labels["astrolift_io_binding"] = spec.binding_id
        if spec.managed_service_id:
            labels["astrolift_io_managed_service_id"] = spec.managed_service_id
        for key, value in (spec.tags or {}).items():
            labels[f"extra_{key}"] = value
    return {_label(key): _label(value) for key, value in labels.items() if str(value)}


def _label(value: Any) -> str:
    return (re.sub(r"[^a-z0-9_-]+", "_", str(value).lower()).strip("_-") or "value")[:63]


def _dataset_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    if not clean:
        raise ValueError("BigQuery dataset id cannot be empty after normalization")
    if not clean[0].isalpha() and clean[0] != "_":
        clean = f"a_{clean}"
    return clean[:1024].rstrip("_")


def _capacity_id(value: str) -> str:
    clean = re.sub(r"[^a-z0-9-]+", "-", value.lower().replace("_", "-")).strip("-")
    if not clean:
        raise ValueError("BigQuery capacity resource id cannot be empty after normalization")
    if not clean[0].isalpha():
        clean = f"a-{clean}"
    return clean[:64].rstrip("-")


def _capacity_namespace(value: str) -> str:
    clean = _capacity_id(value)
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{clean[:42].rstrip('-')}-{digest}"


def _scoped_capacity_id(scope: str, name: str) -> str:
    namespace = _capacity_namespace(scope)
    clean_name = _capacity_id(name)
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    remaining = 64 - len(namespace) - len(digest) - 2
    return f"{namespace}-{clean_name[:remaining].rstrip('-')}-{digest}"


def _parse_handle(handle: str) -> str:
    kind, separator, dataset_id = handle.partition("/")
    if separator != "/" or kind != KIND or not dataset_id or "/" in dataset_id:
        raise ValueError(f"invalid BigQuery warehouse handle {handle!r}; expected 'warehouse/<dataset-id>'")
    return dataset_id


def _camelize(value: Any) -> Any:
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {_camel_key(str(key)): _camelize(item) for key, item in value.items()}


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _reservation_value(field: str, value: Any) -> Any:
    if field in {"slot_capacity", "concurrency", "max_slots"}:
        return _int64_string(value)
    if field == "autoscale":
        return {"maxSlots": _int64_string((value or {}).get("max_slots", 0))}
    if field == "scheduling_policy":
        return {_camel_key(str(key)): _int64_string(item) for key, item in dict(value or {}).items()}
    return value


def _assignment_value(field: str, value: Any) -> Any:
    if field == "scheduling_policy":
        return {_camel_key(str(key)): _int64_string(item) for key, item in dict(value or {}).items()}
    return value


def _int64_string(value: Any) -> str:
    return str(int(value))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_scheduling_policy(value: Any, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return f"{path} must be an object"
    for field in ("concurrency", "max_slots"):
        if field in value and (not _is_int(value[field]) or value[field] < 0):
            return f"{path}.{field} must be a non-negative integer"
    if value.get("max_slots") and value["max_slots"] < 100:
        return f"{path}.max_slots must be 0 or at least 100"
    return None


def _validate_named_rows(rows: list[Any], field: str) -> str | None:
    names: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return f"{field}[{index}] must be an object"
        name = str(row.get("name") or "")
        if not name:
            return f"{field}[{index}].name is required"
        normalized = _capacity_id(name)
        if normalized in names:
            return f"{field}[{index}].name collides after normalization"
        names.add(normalized)
    return None


def _deprovision_error(handle: str, operation: str, exc: Exception) -> DeprovisionResult:
    return DeprovisionResult(False, handle, f"{operation}: {exc}", [str(exc)])


__all__ = [
    "BigQueryRestClient",
    "BigQueryWarehouseConfig",
    "BigQueryWarehouseDriver",
    "BigQueryWarehouseError",
    "BigQueryWarehouseNotFound",
]
