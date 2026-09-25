from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.warehouse_bigquery import (
    BigQueryRestClient,
    BigQueryWarehouseConfig,
    BigQueryWarehouseDriver,
    BigQueryWarehouseError,
    BigQueryWarehouseNotFound,
    _capacity_namespace,
    _parse_handle,
    _scoped_capacity_id,
)


@dataclass
class CloudState:
    datasets: dict[str, dict[str, Any]] = field(default_factory=dict)
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    reservations: dict[str, dict[str, Any]] = field(default_factory=dict)
    assignments: dict[str, dict[str, Any]] = field(default_factory=dict)
    commitments: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class FakeClient:
    state: CloudState
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def get_dataset(self, project_id: str, dataset_id: str) -> dict[str, Any]:
        try:
            return self.state.datasets[dataset_id]
        except KeyError as exc:
            raise BigQueryWarehouseNotFound(dataset_id) from exc

    def create_dataset(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        dataset_id = body["datasetReference"]["datasetId"]
        self.calls.append(("create_dataset", body))
        self.state.datasets[dataset_id] = dict(body)
        return body

    def patch_dataset(self, project_id: str, dataset_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("patch_dataset", body))
        self.state.datasets[dataset_id].update(body)
        return self.state.datasets[dataset_id]

    def delete_dataset(self, project_id: str, dataset_id: str, *, delete_contents: bool) -> None:
        self.calls.append(("delete_dataset", {"delete_contents": delete_contents}))
        self.state.datasets.pop(dataset_id, None)
        if delete_contents:
            self.state.tables.pop(dataset_id, None)

    def list_tables(self, project_id: str, dataset_id: str) -> list[dict[str, Any]]:
        return list(self.state.tables.get(dataset_id, []))

    def get_reservation(self, name: str) -> dict[str, Any]:
        try:
            return self.state.reservations[name]
        except KeyError as exc:
            raise BigQueryWarehouseNotFound(name) from exc

    def list_reservations(self, parent: str) -> list[dict[str, Any]]:
        return [row for name, row in self.state.reservations.items() if name.startswith(f"{parent}/reservations/")]

    def create_reservation(self, parent: str, reservation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        name = f"{parent}/reservations/{reservation_id}"
        row = {"name": name, **body}
        self.calls.append(("create_reservation", row))
        self.state.reservations[name] = row
        return row

    def patch_reservation(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        self.calls.append(("patch_reservation", {"body": body, "mask": update_mask}))
        self.state.reservations[name].update(body)
        return self.state.reservations[name]

    def delete_reservation(self, name: str) -> None:
        if name not in self.state.reservations:
            raise BigQueryWarehouseNotFound(name)
        self.calls.append(("delete_reservation", name))
        del self.state.reservations[name]

    def list_assignments(self, parent: str) -> list[dict[str, Any]]:
        return [row for name, row in self.state.assignments.items() if name.startswith(f"{parent}/assignments/")]

    def create_assignment(self, parent: str, assignment_id: str, body: dict[str, Any]) -> dict[str, Any]:
        name = f"{parent}/assignments/{assignment_id}"
        row = {"name": name, "state": "ACTIVE", **body}
        self.calls.append(("create_assignment", row))
        self.state.assignments[name] = row
        return row

    def patch_assignment(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        self.calls.append(("patch_assignment", {"body": body, "mask": update_mask}))
        self.state.assignments[name].update(body)
        return self.state.assignments[name]

    def delete_assignment(self, name: str) -> None:
        if name not in self.state.assignments:
            raise BigQueryWarehouseNotFound(name)
        self.calls.append(("delete_assignment", name))
        del self.state.assignments[name]

    def get_capacity_commitment(self, name: str) -> dict[str, Any]:
        try:
            return self.state.commitments[name]
        except KeyError as exc:
            raise BigQueryWarehouseNotFound(name) from exc

    def list_capacity_commitments(self, parent: str) -> list[dict[str, Any]]:
        return [
            row for name, row in self.state.commitments.items() if name.startswith(f"{parent}/capacityCommitments/")
        ]

    def create_capacity_commitment(
        self,
        parent: str,
        commitment_id: str,
        body: dict[str, Any],
        *,
        enforce_single_admin_project_per_org: bool,
    ) -> dict[str, Any]:
        name = f"{parent}/capacityCommitments/{commitment_id}"
        row = {"name": name, "state": "ACTIVE", **body}
        self.calls.append(
            (
                "create_capacity_commitment",
                {"body": row, "enforce": enforce_single_admin_project_per_org},
            ),
        )
        self.state.commitments[name] = row
        return row

    def patch_capacity_commitment(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        self.calls.append(("patch_capacity_commitment", {"body": body, "mask": update_mask}))
        self.state.commitments[name].update(body)
        return self.state.commitments[name]

    def delete_capacity_commitment(self, name: str, *, force: bool) -> None:
        if name not in self.state.commitments:
            raise BigQueryWarehouseNotFound(name)
        self.calls.append(("delete_capacity_commitment", {"name": name, "force": force}))
        del self.state.commitments[name]


@dataclass
class Harness:
    state: CloudState
    client: FakeClient
    driver: BigQueryWarehouseDriver


@pytest.fixture
def harness() -> Harness:
    state = CloudState()
    client = FakeClient(state)
    driver = BigQueryWarehouseDriver(
        config=BigQueryWarehouseConfig(
            project_id="acme-prod",
            location="US",
            dataset_prefix="astrolift",
            deletion_protection_default=True,
        ),
        client=client,
    )
    return Harness(state, client, driver)


def _spec(config: dict[str, Any] | None = None) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="analytics",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="gke-prod",
        service_handle_hint="events",
        size="small",
        config=config or {},
        tags={"Data Class": "Internal"},
        binding_id="binding-1",
        managed_service_id="service-1",
    )


def _provision(harness: Harness, config: dict[str, Any] | None = None):
    result = harness.driver.provision(_spec(config))
    assert result.ok, result
    return result


def _full_config() -> dict[str, Any]:
    return {
        "friendly_name": "Event analytics",
        "description": "Project warehouse",
        "labels": {"owner": "data-platform"},
        "default_table_expiration_ms": 86_400_000,
        "default_partition_expiration_ms": 3_600_000,
        "default_encryption_configuration": {
            "kms_key_name": "projects/acme-prod/locations/us/keyRings/data/cryptoKeys/bigquery",
        },
        "is_case_insensitive": True,
        "default_collation": "und:ci",
        "default_rounding_mode": "ROUND_HALF_EVEN",
        "max_time_travel_hours": 96,
        "storage_billing_model": "PHYSICAL",
        "resource_tags": {"123/environment": "production"},
        "purchase_capacity_commitments": True,
        "enforce_single_admin_project_per_org": True,
        "capacity_commitments": [
            {
                "name": "baseline",
                "slot_count": 100,
                "plan": "ANNUAL",
                "renewal_plan": "ANNUAL",
                "edition": "ENTERPRISE",
                "multi_region_auxiliary": False,
            },
        ],
        "reservations": [
            {
                "name": "queries",
                "slot_capacity": 50,
                "ignore_idle_slots": False,
                "max_slots": 200,
                "concurrency": 10,
                "edition": "ENTERPRISE",
                "scaling_mode": "ALL_SLOTS",
                "scheduling_policy": {"concurrency": 5, "max_slots": 150},
                "assignments": [
                    {
                        "name": "project-query",
                        "assignee": "projects/acme-prod",
                        "job_type": "QUERY",
                    },
                    {
                        "name": "project-cap",
                        "assignee": "projects/acme-prod",
                        "scheduling_policy": {"concurrency": 2, "max_slots": 100},
                    },
                ],
            },
        ],
    }


def test_provision_reconciles_dataset_capacity_reservation_and_assignment(harness: Harness) -> None:
    result = _provision(harness, _full_config())
    assert result.handle == "warehouse/astrolift_acme_analytics_prod_events"
    assert not result.ready
    dataset = next(iter(harness.state.datasets.values()))
    assert dataset["location"] == "US"
    assert dataset["defaultEncryptionConfiguration"]["kmsKeyName"].endswith("/bigquery")
    assert dataset["maxTimeTravelHours"] == 96
    assert dataset["labels"]["astrolift_io_binding"] == "binding-1"
    commitment = next(iter(harness.state.commitments.values()))
    assert commitment["slotCount"] == "100"
    reservation = next(iter(harness.state.reservations.values()))
    assert reservation["maxSlots"] == "200"
    assert reservation["schedulingPolicy"] == {"concurrency": "5", "maxSlots": "150"}
    assignment = next(row for row in harness.state.assignments.values() if row.get("jobType") == "QUERY")
    assert assignment["jobType"] == "QUERY"
    project_cap = next(row for row in harness.state.assignments.values() if "schedulingPolicy" in row)
    assert project_cap["schedulingPolicy"] == {"concurrency": "2", "maxSlots": "100"}


def test_provision_is_idempotent_and_updates_mutable_fields(harness: Harness) -> None:
    config = _full_config()
    first = _provision(harness, config)
    config["description"] = "Updated"
    config["capacity_commitments"][0]["renewal_plan"] = "NONE"
    config["reservations"][0]["slot_capacity"] = 75
    second = harness.driver.provision(_spec(config))
    assert second.ok and second.handle == first.handle
    assert next(iter(harness.state.datasets.values()))["description"] == "Updated"
    assert next(iter(harness.state.commitments.values()))["renewalPlan"] == "NONE"
    assert next(iter(harness.state.reservations.values()))["slotCapacity"] == "75"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"access_mode": "execute"}, "access_mode"),
        ({"max_time_travel_hours": 50}, "24-hour increment"),
        ({"default_table_expiration_ms": 3_599_999}, "at least 3600000"),
        ({"capacity_commitments": {}}, "must be an array"),
        ({"capacity_commitments": [{"name": "c", "slot_count": 100}]}, "committed spend"),
        (
            {
                "purchase_capacity_commitments": True,
                "capacity_commitments": [{"name": "c", "slot_count": 0, "plan": "FLEX"}],
            },
            "positive integer",
        ),
        (
            {
                "purchase_capacity_commitments": True,
                "capacity_commitments": [
                    {"name": "c", "slot_count": 100, "plan": "ANNUAL", "edition": "ENTERPRISE"},
                ],
            },
            "renewal_plan",
        ),
        ({"reservations": {}}, "must be an array"),
        (
            {"reservations": [{"name": "q", "scaling_mode": "ALL_SLOTS"}]},
            "max_slots",
        ),
        (
            {"reservations": [{"name": "q", "max_slots": 100}]},
            "scaling_mode",
        ),
        (
            {
                "reservations": [
                    {
                        "name": "q",
                        "slot_capacity": 100,
                        "max_slots": 100,
                        "scaling_mode": "ALL_SLOTS",
                        "ignore_idle_slots": False,
                    },
                ],
            },
            "must exceed slot_capacity",
        ),
        (
            {
                "reservations": [
                    {
                        "name": "q",
                        "max_slots": 100,
                        "autoscale": {"max_slots": 200},
                        "scaling_mode": "ALL_SLOTS",
                        "ignore_idle_slots": False,
                    },
                ],
            },
            "cannot combine",
        ),
        (
            {"reservations": [{"name": "q", "assignments": [{"name": "a"}]}]},
            "exactly one of",
        ),
        (
            {
                "reservations": [
                    {
                        "name": "q",
                        "assignments": [{"name": "a", "assignee": "projects/acme-prod"}],
                    },
                ],
            },
            "job_type is required",
        ),
        (
            {
                "reservations": [
                    {
                        "name": "q",
                        "assignments": [
                            {
                                "name": "a",
                                "assignee": "folders/123",
                                "scheduling_policy": {"max_slots": 100},
                            },
                        ],
                    },
                ],
            },
            "requires a project assignee",
        ),
        (
            {
                "reservations": [
                    {
                        "name": "q",
                        "assignments": [
                            {
                                "name": "a",
                                "assignee": "projects/acme-prod",
                                "job_type": "QUERY",
                                "scheduling_policy": {"max_slots": 100},
                            },
                        ],
                    },
                ],
            },
            "must be unset",
        ),
        (
            {"reservations": [{"name": "q", "scheduling_policy": {"max_slots": 99}}]},
            "at least 100",
        ),
    ],
)
def test_rejects_invalid_config(harness: Harness, config: dict[str, Any], message: str) -> None:
    result = harness.driver.provision(_spec(config))
    assert not result.ok
    assert message in result.message
    assert harness.state.datasets == {}


def test_update_reports_missing_and_rejects_immutable_location(harness: Harness) -> None:
    missing = harness.driver.update(UpdateSpec("warehouse/missing", config={"description": "x"}))
    assert not missing.ok and missing.errors == ["not_found"]
    provisioned = _provision(harness)
    next(iter(harness.state.datasets.values()))["location"] = "EU"
    moved = harness.driver.update(UpdateSpec(provisioned.handle, config={"description": "x"}))
    assert not moved.ok
    assert "immutable location" in moved.message


def test_commitment_slot_count_and_edition_are_immutable(harness: Harness) -> None:
    config = _full_config()
    provisioned = _provision(harness, config)
    config["capacity_commitments"][0]["slot_count"] = 200
    result = harness.driver.update(UpdateSpec(provisioned.handle, config=config))
    assert not result.ok
    assert "slot_count" in result.message and "immutable" in result.message


def test_assignment_reconcile_only_patches_scheduling_policy(harness: Harness) -> None:
    config = _full_config()
    provisioned = _provision(harness, config)
    harness.client.calls.clear()
    config["reservations"][0]["assignments"][1]["scheduling_policy"]["max_slots"] = 200
    result = harness.driver.update(UpdateSpec(provisioned.handle, config=config))
    assert result.ok
    patches = [payload for name, payload in harness.client.calls if name == "patch_assignment"]
    assert patches == [
        {
            "body": {"schedulingPolicy": {"concurrency": "2", "maxSlots": "200"}},
            "mask": ["schedulingPolicy"],
        },
    ]
    config["reservations"][0]["assignments"][0]["job_type"] = "PIPELINE"
    immutable = harness.driver.update(UpdateSpec(provisioned.handle, config=config))
    assert not immutable.ok
    assert "job_type" in immutable.message and "immutable" in immutable.message


def test_immutable_dataset_sources_and_commitment_region_fail_closed(harness: Harness) -> None:
    config = _full_config()
    config["linked_dataset_source"] = {
        "source_dataset": {"project_id": "source", "dataset_id": "events"},
    }
    provisioned = _provision(harness, config)
    config["linked_dataset_source"]["source_dataset"]["dataset_id"] = "other"
    moved_source = harness.driver.update(UpdateSpec(provisioned.handle, config=config))
    assert not moved_source.ok
    assert "linked_dataset_source" in moved_source.message and "immutable" in moved_source.message
    config["linked_dataset_source"]["source_dataset"]["dataset_id"] = "events"
    config["capacity_commitments"][0]["multi_region_auxiliary"] = True
    moved_commitment = harness.driver.update(UpdateSpec(provisioned.handle, config=config))
    assert not moved_commitment.ok
    assert "multi_region_auxiliary" in moved_commitment.message and "immutable" in moved_commitment.message


def test_external_dataset_fields_and_principal_assignment_are_native(harness: Harness) -> None:
    config = {
        "external_dataset_reference": {
            "connection": "projects/acme-prod/locations/us/connections/lakehouse",
            "external_source": "hive://catalog/events",
        },
        "external_catalog_dataset_options": {
            "default_storage_location_uri": "gs://events/warehouse",
            "parameters": {"catalog_type": "ICEBERG"},
        },
        "reservations": [
            {
                "name": "principals",
                "assignments": [
                    {
                        "name": "service-agent",
                        "principal": "principal://iam.googleapis.com/projects/-/serviceAccounts/agent@acme.iam.gserviceaccount.com",
                        "job_type": "AUTOMATIC_MATERIALIZED_VIEW_REFRESH",
                    },
                ],
            },
        ],
    }
    _provision(harness, config)
    dataset = next(iter(harness.state.datasets.values()))
    assert dataset["externalDatasetReference"]["externalSource"] == "hive://catalog/events"
    assert dataset["externalCatalogDatasetOptions"]["defaultStorageLocationUri"] == "gs://events/warehouse"
    assignment = next(iter(harness.state.assignments.values()))
    assert assignment["principal"].startswith("principal://iam.googleapis.com/")
    assert assignment["jobType"] == "AUTOMATIC_MATERIALIZED_VIEW_REFRESH"


def test_status_reports_pending_and_failed_capacity(harness: Harness) -> None:
    provisioned = _provision(harness, _full_config())
    commitment = next(iter(harness.state.commitments.values()))
    commitment["state"] = "PENDING"
    pending = harness.driver.status(ServiceHandle(provisioned.handle))
    assert pending.state == "provisioning"
    commitment["state"] = "FAILED"
    commitment["failureStatus"] = {"message": "quota exhausted"}
    failed = harness.driver.status(ServiceHandle(provisioned.handle))
    assert failed.state == "error"
    assert "quota exhausted" in failed.message


def test_status_reports_pending_assignment_then_available(harness: Harness) -> None:
    provisioned = _provision(harness, _full_config())
    assignment = next(iter(harness.state.assignments.values()))
    assignment["state"] = "PENDING"
    assert harness.driver.status(ServiceHandle(provisioned.handle)).state == "provisioning"
    assignment["state"] = "ACTIVE"
    status = harness.driver.status(ServiceHandle(provisioned.handle))
    assert status.state == "available"
    assert "1 reservations" in status.message


def test_binding_exposes_portable_contract_and_least_privilege_roles(harness: Harness) -> None:
    provisioned = _provision(harness, {"reservations": [{"name": "queries"}]})
    binding = harness.driver.binding(
        ServiceHandle(provisioned.handle),
        config={"access_mode": "read"},
    )
    assert binding.env_vars["WAREHOUSE_ENDPOINT"].literal == "https://bigquery.googleapis.com"
    assert binding.env_vars["WAREHOUSE_ENGINE"].literal == "bigquery"
    assert binding.env_vars["WAREHOUSE_AUTH_MODE"].literal == "workload_identity"
    assert json.loads(binding.env_vars["GCP_BIGQUERY_RESERVATIONS"].literal or "null")
    assert [grant.actions for grant in binding.iam_grants] == [
        ["roles/bigquery.dataViewer"],
        ["roles/bigquery.jobUser"],
    ]


def test_admin_binding_can_manage_reservations(harness: Harness) -> None:
    provisioned = _provision(harness)
    binding = harness.driver.binding(
        ServiceHandle(provisioned.handle),
        config={"access_mode": "admin", "manage_reservations": True},
    )
    assert [grant.actions[0] for grant in binding.iam_grants] == [
        "roles/bigquery.dataOwner",
        "roles/bigquery.jobUser",
        "roles/bigquery.resourceAdmin",
    ]
    with pytest.raises(BigQueryWarehouseError):
        harness.driver.binding(ServiceHandle(provisioned.handle), config={"access_mode": "root"})


def test_deprovision_honors_protection_and_dataset_contents(harness: Harness) -> None:
    provisioned = _provision(harness)
    protected = harness.driver.deprovision(DeprovisionSpec(provisioned.handle))
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    dataset_id = provisioned.handle.split("/", 1)[1]
    harness.state.tables[dataset_id] = [{"tableReference": {"tableId": "events"}}]
    nonempty = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle),
        force_destroy=True,
    )
    assert not nonempty.ok and nonempty.errors == ["dataset_not_empty"]
    deleted = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert deleted.ok
    assert harness.state.datasets == {}


def test_deprovision_removes_assignments_reservations_commitments_and_is_idempotent(harness: Harness) -> None:
    config = _full_config()
    config["deletion_protection"] = False
    provisioned = _provision(harness, config)
    first = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle, config=config),
        force_destroy=True,
    )
    second = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle, config=config),
        force_destroy=True,
    )
    assert first.ok and second.ok
    assert harness.state.assignments == {}
    assert harness.state.reservations == {}
    assert harness.state.commitments == {}


def test_deprovision_cleans_orphaned_capacity_after_dataset_is_already_gone(harness: Harness) -> None:
    config = _full_config()
    config["deletion_protection"] = False
    provisioned = _provision(harness, config)
    harness.state.datasets.clear()
    result = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle, config=config),
        force_destroy=True,
    )
    assert result.ok
    assert harness.state.reservations == {}
    assert harness.state.commitments == {}


def test_foreign_capacity_resources_are_never_deleted(harness: Harness) -> None:
    config = _full_config()
    config["deletion_protection"] = False
    provisioned = _provision(harness, config)
    parent = "projects/acme-prod/locations/US"
    foreign = f"{parent}/reservations/unrelated"
    harness.state.reservations[foreign] = {"name": foreign}
    result = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle, config=config),
        force_destroy=True,
    )
    assert result.ok
    assert list(harness.state.reservations) == [foreign]


def test_snapshot_restore_and_schema_contract(harness: Harness) -> None:
    with pytest.raises(UnsupportedOperationError, match="table-scoped"):
        harness.driver.snapshot(ServiceHandle("warehouse/events"))
    with pytest.raises(UnsupportedOperationError, match="not portable"):
        harness.driver.restore(None, _spec())  # type: ignore[arg-type]
    schema = harness.driver.config_schema()["properties"]
    assert {"capacity_commitments", "reservations", "default_encryption_configuration"}.issubset(schema)
    assert (
        "BACKGROUND_SEARCH_INDEX_REFRESH"
        in (schema["reservations"]["items"]["properties"]["assignments"]["items"]["properties"]["job_type"]["enum"])
    )
    binding = harness.driver.binding_schema().env_vars
    assert {"WAREHOUSE_ENDPOINT", "GCP_BIGQUERY_DATASET", "GCP_BIGQUERY_RESERVATIONS"}.issubset(binding)


def test_capacity_ids_are_stable_scoped_and_collision_resistant() -> None:
    scope = "dataset-" + ("x" * 200)
    first = _scoped_capacity_id(scope, "reservation-a")
    second = _scoped_capacity_id(scope, "reservation-b")
    assert len(first) <= 64
    assert first != second
    assert first.startswith(f"{_capacity_namespace(scope)}-")
    assert _parse_handle("warehouse/events") == "events"
    with pytest.raises(ValueError):
        _parse_handle("postgres/events")


@dataclass
class FakeResponse:
    status_code: int
    payload: dict[str, Any] | None = None
    text: str = ""

    @property
    def content(self) -> bytes:
        return b"json" if self.payload is not None else b""

    def json(self) -> dict[str, Any]:
        return dict(self.payload or {})


@dataclass
class FakeSession:
    responses: list[FakeResponse]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_rest_client_uses_documented_api_roots_masks_and_pagination() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"tables": [{"id": "one"}], "nextPageToken": "next"}),
            FakeResponse(200, {"tables": [{"id": "two"}]}),
            FakeResponse(200, {}),
            FakeResponse(200, {}),
        ],
    )
    client = BigQueryRestClient(session=session)
    assert [row["id"] for row in client.list_tables("acme", "events")] == ["one", "two"]
    client.patch_reservation(
        "projects/acme/locations/US/reservations/query",
        {"slotCapacity": "100"},
        update_mask=["slotCapacity"],
    )
    client.create_capacity_commitment(
        "projects/acme/locations/US",
        "baseline",
        {"slotCount": "100", "plan": "FLEX", "edition": "ENTERPRISE"},
        enforce_single_admin_project_per_org=True,
    )
    assert session.calls[0]["url"].endswith("/bigquery/v2/projects/acme/datasets/events/tables")
    assert session.calls[2]["params"]["updateMask"] == "slotCapacity"
    assert session.calls[3]["params"]["capacityCommitmentId"] == "baseline"
    assert session.calls[3]["params"]["enforceSingleAdminProjectPerOrg"] == "true"


def test_rest_client_maps_not_found_and_provider_errors() -> None:
    not_found = BigQueryRestClient(session=FakeSession([FakeResponse(404, {"error": {"message": "gone"}})]))
    with pytest.raises(BigQueryWarehouseNotFound):
        not_found.get_dataset("acme", "missing")
    denied = BigQueryRestClient(
        session=FakeSession([FakeResponse(403, {"error": {"message": "permission denied"}})]),
    )
    with pytest.raises(BigQueryWarehouseError, match="permission denied"):
        denied.get_dataset("acme", "events")


def test_provision_does_not_adopt_another_services_resource(harness) -> None:
    """Names are slug-joined, so another service can map to this one's name (#1961)."""
    import dataclasses

    first = harness.driver.provision(dataclasses.replace(_spec(), managed_service_id="svc-a"))
    second = harness.driver.provision(dataclasses.replace(_spec(), managed_service_id="svc-b"))

    assert first.ok, first.message
    assert not second.ok and "refusing to adopt" in second.message
