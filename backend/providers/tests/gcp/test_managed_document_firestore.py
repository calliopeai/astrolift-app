from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from gcp.managed.document_firestore import (
    FirestoreConfig,
    FirestoreError,
    FirestoreNativeDriver,
    FirestoreNotFound,
    FirestoreRestClient,
)


@dataclass
class CloudState:
    databases: dict[str, dict[str, Any]] = field(default_factory=dict)
    schedules: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    indexes: dict[str, dict[str, Any]] = field(default_factory=dict)
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    backups: list[dict[str, Any]] = field(default_factory=list)
    exports: list[dict[str, Any]] = field(default_factory=list)
    imports: list[dict[str, Any]] = field(default_factory=list)


class FakeFirestoreClient:
    def __init__(self, state: CloudState) -> None:
        self.state = state
        self.calls: list[tuple[str, Any]] = []
        self._sequence = 0

    def _operation(self, response: dict[str, Any] | None = None) -> dict[str, Any]:
        self._sequence += 1
        return {
            "name": f"projects/acme-prod/databases/(default)/operations/{self._sequence}",
            "done": True,
            "response": response or {},
        }

    def get_database(self, name: str) -> dict[str, Any]:
        try:
            return copy.deepcopy(self.state.databases[name])
        except KeyError as exc:
            raise FirestoreNotFound(name) from exc

    def create_database(self, project_id: str, database_id: str, body: dict[str, Any]) -> dict[str, Any]:
        name = f"projects/{project_id}/databases/{database_id}"
        row = copy.deepcopy(body)
        row.update(
            {
                "name": name,
                "type": "FIRESTORE_NATIVE",
                "etag": f"etag-{database_id}",
                "uid": f"uid-{database_id}",
                "realtimeUpdatesMode": row.get(
                    "realtimeUpdatesMode",
                    "REALTIME_UPDATES_MODE_ENABLED",
                ),
            },
        )
        self.calls.append(("create_database", copy.deepcopy(row)))
        self.state.databases[name] = row
        return self._operation(row)

    def patch_database(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        self.calls.append(("patch_database", {"name": name, "body": copy.deepcopy(body), "mask": update_mask}))
        self.state.databases[name].update(copy.deepcopy(body))
        return self._operation(self.state.databases[name])

    def delete_database(self, name: str, *, etag: str = "") -> dict[str, Any]:
        if name not in self.state.databases:
            raise FirestoreNotFound(name)
        self.calls.append(("delete_database", {"name": name, "etag": etag}))
        del self.state.databases[name]
        return self._operation()

    def clone_database(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        target = f"projects/{project_id}/databases/{body['databaseId']}"
        source = body["pitrSnapshot"]["database"]
        row = copy.deepcopy(self.state.databases[source])
        row.update({"name": target, "etag": "clone-etag"})
        if body.get("encryptionConfig"):
            row["cmekConfig"] = copy.deepcopy(body["encryptionConfig"])
        self.calls.append(("clone_database", copy.deepcopy(body)))
        self.state.databases[target] = row
        return self._operation(row)

    def restore_database(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        target = f"projects/{project_id}/databases/{body['databaseId']}"
        row = {
            "name": target,
            "type": "FIRESTORE_NATIVE",
            "locationId": "nam5",
            "databaseEdition": "STANDARD",
            "deleteProtectionState": "DELETE_PROTECTION_DISABLED",
            "realtimeUpdatesMode": "REALTIME_UPDATES_MODE_ENABLED",
            "etag": "restore-etag",
        }
        self.calls.append(("restore_database", copy.deepcopy(body)))
        self.state.databases[target] = row
        return self._operation(row)

    def export_documents(self, database_name: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("export_documents", {"database": database_name, **copy.deepcopy(body)}))
        self.state.exports.append(copy.deepcopy(body))
        return self._operation({"outputUriPrefix": body["outputUriPrefix"]})

    def import_documents(self, database_name: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("import_documents", {"database": database_name, **copy.deepcopy(body)}))
        self.state.imports.append(copy.deepcopy(body))
        return self._operation()

    def get_operation(self, name: str) -> dict[str, Any]:
        raise AssertionError(f"completed fake operations must not be polled: {name}")

    def list_backup_schedules(self, database_name: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self.state.schedules.get(database_name, []))

    def create_backup_schedule(self, database_name: str, body: dict[str, Any]) -> dict[str, Any]:
        rows = self.state.schedules.setdefault(database_name, [])
        kind = "daily" if "dailyRecurrence" in body else "weekly"
        row = {
            "name": f"{database_name}/backupSchedules/{kind}",
            **copy.deepcopy(body),
        }
        self.calls.append(("create_backup_schedule", copy.deepcopy(row)))
        rows.append(row)
        return copy.deepcopy(row)

    def patch_backup_schedule(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        self.calls.append(("patch_backup_schedule", {"name": name, "body": body, "mask": update_mask}))
        for rows in self.state.schedules.values():
            for row in rows:
                if row["name"] == name:
                    row.update(copy.deepcopy(body))
                    return copy.deepcopy(row)
        raise FirestoreNotFound(name)

    def delete_backup_schedule(self, name: str) -> None:
        self.calls.append(("delete_backup_schedule", name))
        for database_name, rows in self.state.schedules.items():
            self.state.schedules[database_name] = [row for row in rows if row["name"] != name]

    def list_backups(self, project_id: str, location: str) -> list[dict[str, Any]]:
        self.calls.append(("list_backups", {"project": project_id, "location": location}))
        return copy.deepcopy(self.state.backups)

    def list_indexes(self, parent: str) -> list[dict[str, Any]]:
        database_name, collection_group = parent.split("/collectionGroups/", 1)
        rows = []
        for name, row in self.state.indexes.items():
            if not name.startswith(f"{database_name}/collectionGroups/"):
                continue
            if collection_group != "-" and f"/collectionGroups/{collection_group}/" not in name:
                continue
            rows.append(copy.deepcopy(row))
        return rows

    def create_index(self, parent: str, body: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        name = f"{parent}/indexes/index-{self._sequence}"
        row = copy.deepcopy(body)
        fields = row.setdefault("fields", [])
        if not fields or fields[-1].get("fieldPath") != "__name__":
            direction = fields[-1].get("order", "ASCENDING") if fields else "ASCENDING"
            fields.append({"fieldPath": "__name__", "order": direction})
        row.setdefault("apiScope", "ANY_API")
        row.setdefault("density", "SPARSE_ALL")
        row.update({"name": name, "state": "READY"})
        self.calls.append(("create_index", copy.deepcopy(row)))
        self.state.indexes[name] = row
        return self._operation(row)

    def delete_index(self, name: str) -> dict[str, Any]:
        if name not in self.state.indexes:
            raise FirestoreNotFound(name)
        self.calls.append(("delete_index", name))
        del self.state.indexes[name]
        return self._operation()

    def get_field(self, name: str) -> dict[str, Any]:
        try:
            return copy.deepcopy(self.state.fields[name])
        except KeyError as exc:
            raise FirestoreNotFound(name) from exc

    def list_fields(self, parent: str, *, filter_value: str) -> list[dict[str, Any]]:
        database_name = parent.split("/collectionGroups/", 1)[0]
        return [
            copy.deepcopy(row)
            for name, row in self.state.fields.items()
            if name.startswith(f"{database_name}/collectionGroups/") and "ttlConfig" in row
        ]

    def patch_field(self, name: str, body: dict[str, Any], *, update_mask: list[str]) -> dict[str, Any]:
        row = self.state.fields.setdefault(name, {"name": name})
        for field_name in update_mask:
            if field_name in body:
                row[field_name] = copy.deepcopy(body[field_name])
                if field_name == "ttlConfig":
                    row[field_name]["state"] = "ACTIVE"
            else:
                row.pop(field_name, None)
        self.calls.append(("patch_field", {"name": name, "body": body, "mask": update_mask}))
        return self._operation(row)


@dataclass
class Harness:
    state: CloudState
    client: FakeFirestoreClient
    driver: FirestoreNativeDriver


@pytest.fixture
def harness() -> Harness:
    state = CloudState()
    client = FakeFirestoreClient(state)
    driver = FirestoreNativeDriver(
        config=FirestoreConfig(
            project_id="acme-prod",
            location="nam5",
            database_name_prefix="astrolift",
            snapshot_bucket="gs://acme-firestore-exports",
            poll_interval_seconds=0,
        ),
        client=client,
        sleep=lambda _: None,
    )
    return Harness(state, client, driver)


def _spec(config: dict[str, Any] | None = None, *, hint: str = "documents") -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="records",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="gke-prod",
        service_handle_hint=hint,
        size="small",
        config=config or {},
        tags={"data-class": "sensitive"},
    )


def _full_config() -> dict[str, Any]:
    return {
        "database_id": "astrolift-records",
        "delete_adopted": True,
        "location": "nam5",
        "database_edition": "ENTERPRISE",
        "concurrency_mode": "OPTIMISTIC",
        "point_in_time_recovery": "POINT_IN_TIME_RECOVERY_ENABLED",
        "app_engine_integration_mode": "DISABLED",
        "delete_protection": False,
        "kms_key_name": "projects/acme-prod/locations/nam5/keyRings/data/cryptoKeys/firestore",
        "tags": {"123/environment": "production"},
        "realtime_updates_mode": "REALTIME_UPDATES_MODE_DISABLED",
        "firestore_data_access_mode": "DATA_ACCESS_MODE_ENABLED",
        "mongodb_compatible_data_access_mode": "DATA_ACCESS_MODE_ENABLED",
        "backup_schedules": [
            {"recurrence": "daily", "retention": "604800s"},
            {"recurrence": "weekly", "day_of_week": "SUNDAY", "retention": "8467200s"},
        ],
        "composite_indexes": [
            {
                "collection_group": "embeddings",
                "query_scope": "COLLECTION_GROUP",
                "api_scope": "ANY_API",
                "fields": [
                    {"field_path": "tenant", "order": "ASCENDING"},
                    {"field_path": "embedding", "vector_config": {"dimension": 768, "flat": {}}},
                ],
            },
            {
                "collection_group": "articles",
                "query_scope": "COLLECTION_GROUP",
                "api_scope": "ANY_API",
                "fields": [
                    {
                        "field_path": "body",
                        "search_config": {
                            "text_spec": {
                                "index_specs": [
                                    {"match_type": "MATCH_GLOBALLY", "index_type": "TOKENIZED"},
                                ],
                            },
                        },
                    },
                ],
            },
        ],
        "field_overrides": [
            {
                "collection_group": "sessions",
                "field_path": "expires_at",
                "index_config": {"indexes": []},
                "ttl": {"enabled": True, "expiration_offset": "3600s"},
            },
        ],
    }


def test_full_native_lifecycle_reconcile_is_idempotent(harness: Harness) -> None:
    result = harness.driver.provision(_spec(_full_config()))

    assert result.ok and result.ready
    database = harness.state.databases["projects/acme-prod/databases/astrolift-records"]
    assert database["databaseEdition"] == "ENTERPRISE"
    assert database["pointInTimeRecoveryEnablement"] == "POINT_IN_TIME_RECOVERY_ENABLED"
    assert database["cmekConfig"]["kmsKeyName"].endswith("/cryptoKeys/firestore")
    assert len(harness.state.schedules[database["name"]]) == 2
    assert len(harness.state.indexes) == 2
    assert len(harness.state.fields) == 1

    mutation_count = len(harness.client.calls)
    second = harness.driver.update(UpdateSpec(result.handle, config=_full_config()))
    assert second.ok, second
    assert len(harness.client.calls) == mutation_count


def test_generated_database_id_is_owned_and_retry_safe_without_adoption_flag(harness: Harness) -> None:
    first = harness.driver.provision(_spec())
    second = harness.driver.provision(_spec())

    assert first.ok and second.ok
    assert first.handle == second.handle
    assert first.handle.startswith("document_db/astrolift-acme-records-prod-documents-")
    assert len([call for call in harness.client.calls if call[0] == "create_database"]) == 1


def test_preexisting_default_database_is_refused_without_operator_adoption(harness: Harness) -> None:
    name = "projects/acme-prod/databases/(default)"
    harness.state.databases[name] = {
        "name": name,
        "type": "FIRESTORE_NATIVE",
        "locationId": "nam5",
        "databaseEdition": "STANDARD",
        "deleteProtectionState": "DELETE_PROTECTION_DISABLED",
        "realtimeUpdatesMode": "REALTIME_UPDATES_MODE_ENABLED",
        "cmekConfig": {"kmsKeyName": "projects/p/locations/l/keyRings/r/cryptoKeys/existing"},
    }

    refused = harness.driver.provision(_spec({"database_id": "(default)"}))
    assert not refused.ok
    assert "operator-authorized" in refused.message

    # No config knob reopens it -- adoption of an existing, unlabelable
    # database is operator-only, never a tenant config flag (#2021).
    still_refused = harness.driver.provision(
        _spec({"database_id": "(default)", "delete_protection": False}),
    )
    assert not still_refused.ok
    assert "operator-authorized" in still_refused.message
    assert harness.state.databases[name]["databaseEdition"] == "STANDARD"


def test_immutable_field_mismatch_fails_closed_on_reconcile(harness: Harness) -> None:
    config = {
        "location": "nam5",
        "kms_key_name": "projects/p/locations/l/keyRings/r/cryptoKeys/existing",
        "delete_protection": False,
    }
    assert harness.driver.provision(_spec(config)).ok

    mismatch = harness.driver.provision(
        _spec({**config, "kms_key_name": "projects/p/locations/l/keyRings/r/cryptoKeys/different"}),
    )
    assert not mismatch.ok
    assert "immutable" in mismatch.message


def test_explicit_database_id_creates_fresh_and_reconciles_through_update(harness: Harness) -> None:
    """A custom database_id names a database that does not exist yet, so there is nothing to adopt."""
    config = {"database_id": "customer-records", "location": "nam5", "delete_protection": False}

    created = harness.driver.provision(_spec(config))
    assert created.ok, created
    assert "projects/acme-prod/databases/customer-records" in harness.state.databases

    reconciled = harness.driver.update(UpdateSpec(created.handle, config=config))
    assert reconciled.ok, reconciled


def test_preexisting_custom_named_database_is_refused_without_operator_adoption(harness: Harness) -> None:
    name = "projects/acme-prod/databases/customer-records"
    harness.state.databases[name] = {
        "name": name,
        "type": "FIRESTORE_NATIVE",
        "locationId": "nam5",
        "databaseEdition": "STANDARD",
        "deleteProtectionState": "DELETE_PROTECTION_DISABLED",
        "realtimeUpdatesMode": "REALTIME_UPDATES_MODE_ENABLED",
    }

    refused = harness.driver.provision(
        _spec({"database_id": "customer-records", "location": "nam5"}),
    )
    assert not refused.ok
    assert "operator-authorized" in refused.message
    assert harness.state.databases[name]["deleteProtectionState"] == "DELETE_PROTECTION_DISABLED"


def test_update_reconciles_mutable_database_schedule_and_ttl(harness: Harness) -> None:
    assert harness.driver.provision(_spec(_full_config())).ok
    config = _full_config()
    config["point_in_time_recovery"] = "POINT_IN_TIME_RECOVERY_DISABLED"
    config["backup_schedules"][0]["retention"] = "1209600s"
    config["field_overrides"][0]["ttl"] = {"enabled": False}

    result = harness.driver.update(UpdateSpec("document_db/astrolift-records", config=config))

    assert result.ok, result
    database = harness.state.databases["projects/acme-prod/databases/astrolift-records"]
    assert database["pointInTimeRecoveryEnablement"] == "POINT_IN_TIME_RECOVERY_DISABLED"
    assert harness.state.schedules[database["name"]][0]["retention"] == "1209600s"
    field_row = next(iter(harness.state.fields.values()))
    assert "ttlConfig" not in field_row


def test_index_pruning_handles_empty_desired_set(harness: Harness) -> None:
    assert harness.driver.provision(_spec(_full_config())).ok

    config = _full_config()
    config["composite_indexes"] = []
    config["prune_composite_indexes"] = True
    result = harness.driver.update(UpdateSpec("document_db/astrolift-records", config=config))

    assert result.ok, result
    assert harness.state.indexes == {}


def test_binding_uses_workload_identity_without_fake_credentials_or_emulator(harness: Harness) -> None:
    assert harness.driver.provision(_spec(_full_config())).ok

    binding = harness.driver.binding(
        ServiceHandle("document_db/astrolift-records"),
        {"access_mode": "read"},
    )

    assert binding.env_vars["DOCDB_URI"].literal == "firestore://acme-prod/astrolift-records"
    assert binding.env_vars["DOCDB_AUTH_MODE"].literal == "workload_identity"
    assert "DOCDB_USER" not in binding.env_vars
    assert "DOCDB_PASSWORD" not in binding.env_vars
    assert "FIRESTORE_EMULATOR_HOST" not in binding.env_vars
    assert binding.iam_grants[0].actions == ["roles/datastore.viewer"]


def test_enterprise_mongodb_binding_uses_google_oidc_endpoint(harness: Harness) -> None:
    assert harness.driver.provision(_spec(_full_config())).ok

    binding = harness.driver.binding(
        ServiceHandle("document_db/astrolift-records"),
        {"client_api": "mongodb", "access_mode": "write"},
    )

    uri = binding.env_vars["DOCDB_URI"].literal or ""
    assert uri.startswith("mongodb://uid-astrolift-records.nam5.firestore.goog:443/astrolift-records?")
    assert "authMechanism=MONGODB-OIDC" in uri
    assert "TOKEN_RESOURCE:FIRESTORE" in uri
    assert binding.env_vars["DOCDB_AUTH_MODE"].literal == "mongodb_oidc_gcp"
    assert binding.env_vars["GCP_FIRESTORE_CLIENT_API"].literal == "mongodb"


def test_status_surfaces_index_and_ttl_control_plane_state(harness: Harness) -> None:
    assert harness.driver.provision(_spec(_full_config())).ok
    index = next(iter(harness.state.indexes.values()))
    index["state"] = "CREATING"
    assert harness.driver.status(ServiceHandle("document_db/astrolift-records")).state == "provisioning"

    index["state"] = "NEEDS_REPAIR"
    status = harness.driver.status(ServiceHandle("document_db/astrolift-records"))
    assert status.state == "error"
    assert "NEEDS_REPAIR" in status.message


def test_snapshot_and_export_restore_round_trip(harness: Harness) -> None:
    assert harness.driver.provision(_spec(_full_config())).ok
    snapshot = harness.driver.snapshot(ServiceHandle("document_db/astrolift-records"))
    assert snapshot.snapshot_id.startswith("gs://acme-firestore-exports/astrolift/firestore/")

    restored = harness.driver.restore(
        snapshot,
        _spec(
            {
                "database_id": "astrolift-restored",
                "location": "nam5",
            },
        ),
    )
    assert restored.ok, restored
    assert harness.state.imports == [{"inputUriPrefix": snapshot.snapshot_id}]


def test_scheduled_backup_restore_reconciles_explicit_target(harness: Harness) -> None:
    backup = SnapshotHandle(
        "document_db/astrolift-records",
        "projects/acme-prod/locations/nam5/backups/backup-1",
        "2026-08-14T00:00:00+00:00",
    )

    restored = harness.driver.restore(
        backup,
        _spec(
            {
                "database_id": "customer-restored",
                "location": "nam5",
                "database_edition": "STANDARD",
                "delete_protection": False,
            },
        ),
    )

    assert restored.ok, restored
    assert "restored from backup" in restored.message
    assert "projects/acme-prod/databases/customer-restored" in harness.state.databases


def test_deprovision_respects_protection_and_retains_ready_backup(harness: Harness) -> None:
    config = _full_config()
    config["delete_protection"] = True
    assert harness.driver.provision(_spec(config)).ok
    handle = "document_db/astrolift-records"

    refused = harness.driver.deprovision(DeprovisionSpec(handle, config=config))
    assert not refused.ok and refused.retryable is False
    assert "delete protection" in refused.message

    database_name = "projects/acme-prod/databases/astrolift-records"
    harness.state.backups.append(
        {
            "name": "projects/acme-prod/locations/us/backups/latest",
            "database": database_name,
            "state": "READY",
            "snapshotTime": "2026-08-14T12:00:00Z",
        },
    )
    backup_only_driver = FirestoreNativeDriver(
        config=FirestoreConfig(
            project_id="acme-prod",
            location="nam5",
            database_name_prefix="astrolift",
            snapshot_bucket="",
            poll_interval_seconds=0,
        ),
        client=harness.client,
        sleep=lambda _: None,
    )
    deleted = backup_only_driver.deprovision(
        DeprovisionSpec(handle, config=config),
        force_destroy=True,
    )
    assert deleted.ok, deleted
    assert "retained snapshot" in deleted.message
    assert "backups/latest" in deleted.message
    assert database_name not in harness.state.databases
    assert ("list_backups", {"project": "acme-prod", "location": "-"}) in harness.client.calls


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"database_edition": "PREMIUM"}, "database_edition"),
        ({"concurrency_mode": "OPTIMISTIC_WITH_ENTITY_GROUPS"}, "concurrency_mode"),
        (
            {
                "database_edition": "STANDARD",
                "mongodb_compatible_data_access_mode": "DATA_ACCESS_MODE_ENABLED",
            },
            "ENTERPRISE",
        ),
        (
            {
                "composite_indexes": [
                    {"collection_group": "items", "fields": [{"field_path": "created", "order": "ASCENDING"}]},
                ],
            },
            "query_scope",
        ),
        (
            {
                "composite_indexes": [
                    {
                        "collection_group": "items",
                        "query_scope": "COLLECTION_GROUP",
                        "fields": [{"field_path": "embedding", "vector_config": {"dimension": 4096}}],
                    },
                ],
            },
            "1-2048",
        ),
    ],
)
def test_invalid_native_options_fail_before_cloud_calls(
    harness: Harness,
    config: dict[str, Any],
    message: str,
) -> None:
    result = harness.driver.provision(_spec(config))
    assert not result.ok
    assert message in result.message
    assert harness.client.calls == []


def test_clone_uses_pitr_source_and_reconciles_target(harness: Harness) -> None:
    source_config = _full_config()
    assert harness.driver.provision(_spec(source_config)).ok

    clone_config = {
        "database_id": "astrolift-clone",
        "location": "nam5",
        "database_edition": "ENTERPRISE",
        "kms_key_name": source_config["kms_key_name"],
        "clone_source_database": "astrolift-records",
        "clone_snapshot_time": "2026-08-14T12:00:00Z",
        "delete_protection": False,
    }
    result = harness.driver.provision(_spec(clone_config))

    assert result.ok, result
    clone_call = next(payload for name, payload in harness.client.calls if name == "clone_database")
    assert clone_call["pitrSnapshot"] == {
        "database": "projects/acme-prod/databases/astrolift-records",
        "snapshotTime": "2026-08-14T12:00:00Z",
    }


def test_weekly_backup_day_change_requires_explicit_replacement(harness: Harness) -> None:
    assert harness.driver.provision(_spec(_full_config())).ok
    config = _full_config()
    config["backup_schedules"][1]["day_of_week"] = "MONDAY"

    refused = harness.driver.update(UpdateSpec("document_db/astrolift-records", config=config))
    assert not refused.ok
    assert "replace_backup_schedules" in refused.message

    config["replace_backup_schedules"] = True
    replaced = harness.driver.update(UpdateSpec("document_db/astrolift-records", config=config))
    assert replaced.ok, replaced
    weekly = next(
        row
        for row in harness.state.schedules["projects/acme-prod/databases/astrolift-records"]
        if "weeklyRecurrence" in row
    )
    assert weekly["weeklyRecurrence"]["day"] == "MONDAY"


def test_adopted_database_cannot_be_deleted_without_second_acknowledgement(harness: Harness) -> None:
    name = "projects/acme-prod/databases/customer-records"
    harness.state.databases[name] = {
        "name": name,
        "type": "FIRESTORE_NATIVE",
        "locationId": "nam5",
        "databaseEdition": "STANDARD",
        "deleteProtectionState": "DELETE_PROTECTION_DISABLED",
        "etag": "external-etag",
    }
    spec = DeprovisionSpec(
        "document_db/customer-records",
        config={"database_id": "customer-records"},
    )

    result = harness.driver.deprovision(spec, delete_data=True)

    assert not result.ok and result.retryable is False
    assert result.errors == ["adopted_resource_guard"]
    assert name in harness.state.databases

    missing_stored_config = harness.driver.deprovision(
        DeprovisionSpec("document_db/customer-records"),
        delete_data=True,
    )
    assert not missing_stored_config.ok
    assert missing_stored_config.errors == ["adopted_resource_guard"]


def test_deprovision_exports_when_no_scheduled_backup_exists(harness: Harness) -> None:
    config = _full_config()
    config["delete_protection"] = False
    assert harness.driver.provision(_spec(config)).ok

    result = harness.driver.deprovision(
        DeprovisionSpec("document_db/astrolift-records", config=config),
    )

    assert result.ok, result
    assert harness.state.exports
    assert "gs://acme-firestore-exports/" in result.message


def test_deprovision_falls_back_to_export_when_backup_listing_fails(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _full_config()
    config["delete_protection"] = False
    assert harness.driver.provision(_spec(config)).ok

    def fail_list_backups(project_id: str, location: str) -> list[dict[str, Any]]:
        raise FirestoreError(f"backup listing unavailable for {project_id}/{location}")

    monkeypatch.setattr(harness.client, "list_backups", fail_list_backups)
    result = harness.driver.deprovision(
        DeprovisionSpec("document_db/astrolift-records", config=config),
    )

    assert result.ok, result
    assert harness.state.exports


def test_binding_rejects_unknown_access_mode(harness: Harness) -> None:
    assert harness.driver.provision(_spec(_full_config())).ok

    with pytest.raises(FirestoreError, match="access_mode"):
        harness.driver.binding(
            ServiceHandle("document_db/astrolift-records"),
            {"access_mode": "superuser"},
        )


def test_operation_error_is_not_reported_as_success(harness: Harness) -> None:
    with pytest.raises(FirestoreError, match="permission denied"):
        harness.driver._wait_operation(
            {
                "name": "operations/failed",
                "done": True,
                "error": {"code": 7, "message": "permission denied"},
            },
        )


def test_config_and_binding_schema_expose_native_and_mongodb_surfaces(harness: Harness) -> None:
    schema = harness.driver.config_schema()
    properties = schema["properties"]

    assert properties["client_api"]["enum"] == ["firestore", "mongodb"]
    assert set(properties["database_edition"]["enum"]) == {"STANDARD", "ENTERPRISE"}
    index = properties["composite_indexes"]["items"]
    assert {"collection_group", "query_scope", "fields"} <= set(index["required"])
    assert "vector_config" in index["properties"]["fields"]["items"]["properties"]
    assert "search_config" in index["properties"]["fields"]["items"]["properties"]
    binding = harness.driver.binding_schema().env_vars
    assert binding["DOCDB_AUTH_MODE"] == "workload_identity or mongodb_oidc_gcp"
    assert "GCP_FIRESTORE_UID" in binding
    assert {"delete_adopted", "client_api", "backup_schedules", "composite_indexes"} <= set(
        harness.driver.editable_fields(),
    )


class _Response:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any] | None = None,
        *,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = b"json" if payload is not None else b""

    def json(self) -> dict[str, Any]:
        return copy.deepcopy(self._payload)


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_rest_client_uses_v1_paths_masks_and_pagination() -> None:
    session = _Session(
        [
            _Response(200, {"backups": [{"name": "backup-1"}], "nextPageToken": "next"}),
            _Response(200, {"backups": [{"name": "backup-2"}]}),
            _Response(200, {"name": "operations/patch", "done": False}),
        ],
    )
    client = FirestoreRestClient(endpoint="https://firestore.example/v1", session=session)

    backups = client.list_backups("acme-prod", "-")
    operation = client.patch_database(
        "projects/acme-prod/databases/docs",
        {"deleteProtectionState": "DELETE_PROTECTION_ENABLED"},
        update_mask=["deleteProtectionState"],
    )

    assert [row["name"] for row in backups] == ["backup-1", "backup-2"]
    assert session.calls[0]["url"].endswith("/projects/acme-prod/locations/-/backups")
    assert session.calls[0]["params"] == {"pageSize": "1000"}
    assert session.calls[1]["params"] == {"pageSize": "1000", "pageToken": "next"}
    assert session.calls[2]["params"] == {"updateMask": "deleteProtectionState"}
    assert session.calls[2]["json"]["name"] == "projects/acme-prod/databases/docs"
    assert operation["name"] == "operations/patch"


def test_rest_client_maps_not_found_and_provider_errors() -> None:
    session = _Session(
        [
            _Response(404, {"error": {"message": "missing"}}),
            _Response(403, {"error": {"message": "permission denied"}}, text="forbidden"),
        ],
    )
    client = FirestoreRestClient(session=session)

    with pytest.raises(FirestoreNotFound):
        client.get_database("projects/acme-prod/databases/missing")
    with pytest.raises(FirestoreError, match="permission denied"):
        client.get_database("projects/acme-prod/databases/denied")
