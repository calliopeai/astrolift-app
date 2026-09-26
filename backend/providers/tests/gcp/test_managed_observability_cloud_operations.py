"""Google Cloud Operations managed-service lifecycle tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from _sdk.availability import MATRIX
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.observability_cloud_operations import (
    CloudOperationsConfig,
    CloudOperationsDriver,
    CloudOperationsError,
    CloudOperationsNotFound,
    CloudOperationsRestClient,
)
from gcp.plugin import PLUGIN


class FakeCloudOperationsClient:
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, dict[str, Any]]] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.sequence = 0

    def get(self, resource: Any, name: str) -> dict[str, Any]:
        self.calls.append(("get", resource.key, name))
        try:
            return dict(self.resources[resource.key][name])
        except KeyError as exc:
            raise CloudOperationsNotFound(name) from exc

    def list_resources(self, resource: Any, parent: str) -> list[dict[str, Any]]:
        self.calls.append(("list", resource.key, parent))
        prefix = f"{parent}/"
        expected_depth = parent.count("/") + 2
        return [
            dict(item)
            for name, item in self.resources.get(resource.key, {}).items()
            if name.startswith(prefix) and name.count("/") >= expected_depth
        ]

    def list_locations(self, project: str) -> list[str]:
        self.calls.append(("list_locations", project))
        return ["global", "us-central1"]

    def create(
        self,
        resource: Any,
        parent: str,
        resource_id: str,
        body: dict[str, Any],
        *,
        request_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("create", resource.key, parent, resource_id, body, request_params))
        self.sequence += 1
        if resource.identity == "generated":
            name = f"{parent}/{resource.collection}/generated-{self.sequence}"
        elif resource.key == "metric_descriptors":
            name = f"{parent}/{resource.collection}/{body['type']}"
        else:
            name = f"{parent}/{resource.collection}/{resource_id}"
        item = {**body, "name": name}
        if resource.key == "log_sinks":
            params = request_params or {}
            item["writerIdentity"] = params.get(
                "customWriterIdentity",
                "serviceAccount:generated-logging-writer@example.iam.gserviceaccount.com",
            )
        self.resources.setdefault(resource.key, {})[name] = item
        return dict(item)

    def update(
        self,
        resource: Any,
        name: str,
        body: dict[str, Any],
        fields: list[str],
        *,
        request_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("update", resource.key, name, body, fields, request_params))
        existing = self.resources[resource.key][name]
        existing.update(body)
        existing["name"] = name
        if resource.key == "log_sinks" and (request_params or {}).get("customWriterIdentity"):
            existing["writerIdentity"] = request_params["customWriterIdentity"]
        return dict(existing)

    def delete(self, resource: Any, name: str, *, force: bool = False) -> None:
        self.calls.append(("delete", resource.key, name, force))
        if name not in self.resources.get(resource.key, {}):
            raise CloudOperationsNotFound(name)
        del self.resources[resource.key][name]

    def verify_notification_channel(self, name: str, code: str) -> dict[str, Any]:
        self.calls.append(("verify", name, code))
        item = self.resources["notification_channels"][name]
        item["verificationStatus"] = "VERIFIED"
        return dict(item)

    def seed(self, key: str, item: dict[str, Any]) -> None:
        self.resources.setdefault(key, {})[str(item["name"])] = dict(item)


def _driver(
    *,
    logging: FakeCloudOperationsClient | None = None,
    monitoring: FakeCloudOperationsClient | None = None,
    secret_reader: Any | None = None,
) -> tuple[CloudOperationsDriver, FakeCloudOperationsClient, FakeCloudOperationsClient]:
    logging = logging or FakeCloudOperationsClient()
    monitoring = monitoring or FakeCloudOperationsClient()
    return (
        CloudOperationsDriver(
            config=CloudOperationsConfig(
                project_id="acme-prod",
                location="global",
                request_timeout_seconds=1,
            ),
            logging_client=logging,
            monitoring_client=monitoring,
            secret_reader=secret_reader,
        ),
        logging,
        monitoring,
    )


def _spec(config: dict[str, Any] | None = None) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="checkout",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="operations",
        size="small",
        config=config or {},
        binding_id="binding-1",
        managed_service_id="managed-1",
    )


def _full_config() -> dict[str, Any]:
    return {
        "deletion_protection": False,
        "log_bucket": {
            "id": "application-logs",
            "retention_days": 90,
            "analytics_enabled": True,
            "body": {"restrictedFields": ["jsonPayload.patient_id"]},
        },
        "log_views": [
            {"id": "errors", "body": {"filter": "severity>=ERROR"}},
        ],
        "log_links": [
            {
                "id": "analytics",
                "body": {
                    "bigqueryDataset": {},
                },
            },
        ],
        "log_sinks": [
            {
                "id": "warehouse",
                "body": {
                    "destination": "bigquery.googleapis.com/projects/acme-prod/datasets/logs",
                    "filter": "resource.type=k8s_container",
                },
            },
        ],
        "log_metrics": [
            {
                "id": "failures",
                "body": {"filter": "severity>=ERROR", "metricDescriptor": {"metricKind": "DELTA"}},
            },
        ],
        "log_exclusions": [
            {"id": "healthz", "body": {"filter": 'httpRequest.requestUrl:"/healthz"'}},
        ],
        "log_scopes": [
            {
                "id": "production",
                "body": {"resourceNames": ["projects/acme-prod/locations/global/buckets/application-logs"]},
            },
        ],
        "saved_queries": [
            {
                "id": "recent-errors",
                "body": {"displayName": "Recent errors", "loggingQuery": {"filter": "severity>=ERROR"}},
            },
        ],
        "notification_channels": [
            {
                "id": "pager",
                "body": {
                    "type": "pagerduty",
                    "displayName": "Primary on-call",
                    "labels": {},
                },
                "label_secret_refs": {"service_key": "ops/pagerduty#routing_key"},
            },
        ],
        "groups": [
            {
                "id": "platform",
                "body": {"displayName": "Platform", "filter": 'resource.labels.project_id="acme-prod"'},
            },
            {
                "id": "checkout",
                "parent_group_id": "platform",
                "body": {"displayName": "Checkout", "filter": 'metadata.user_labels.app="checkout"'},
            },
        ],
        "alert_policies": [
            {
                "id": "error-rate",
                "body": {
                    "displayName": "Error rate",
                    "combiner": "OR",
                    "conditions": [{"displayName": "errors"}],
                },
                "notification_channel_ids": ["pager"],
            },
        ],
        "uptime_checks": [
            {
                "id": "public-api",
                "body": {
                    "displayName": "Public API",
                    "period": "60s",
                    "timeout": "10s",
                    "monitoredResource": {"type": "uptime_url", "labels": {"host": "api.example.com"}},
                },
            },
        ],
        "metric_descriptors": [
            {
                "id": "triage-count",
                "body": {"metricKind": "CUMULATIVE", "valueType": "INT64", "unit": "1"},
            },
        ],
        "dashboards": [
            {"id": "overview", "body": {"displayName": "Checkout overview", "gridLayout": {"columns": "12"}}},
        ],
        "services": [
            {
                "id": "checkout-api",
                "body": {"displayName": "Checkout API", "custom": {}},
                "service_level_objectives": [
                    {
                        "id": "availability",
                        "body": {
                            "displayName": "Availability",
                            "goal": 0.999,
                            "rollingPeriod": "2592000s",
                            "serviceLevelIndicator": {"basicSli": {"availability": {}}},
                        },
                    },
                ],
            },
        ],
    }


def test_full_bundle_reconciles_all_logging_and_monitoring_resources() -> None:
    driver, logging, monitoring = _driver(
        secret_reader=lambda path: {"routing_key": "secret-routing-key"},
    )

    result = driver.provision(_spec(_full_config()))

    assert result.ok is True
    assert result.ready is True
    assert result.handle == "observability/global/astrolift-observability-operations"
    assert "17 resources" in result.message
    channel = next(iter(monitoring.resources["notification_channels"].values()))
    assert channel["labels"]["service_key"] == "secret-routing-key"
    assert "secret-routing-key" not in repr(_full_config())
    alert = next(iter(monitoring.resources["alert_policies"].values()))
    assert alert["notificationChannels"] == [channel["name"]]
    bucket = next(iter(logging.resources["log_bucket"].values()))
    assert bucket["retentionDays"] == 90
    assert bucket["analyticsEnabled"] is True
    assert bucket["restrictedFields"] == ["jsonPayload.patient_id"]


def test_reprovision_is_idempotent() -> None:
    driver, logging, monitoring = _driver(
        secret_reader=lambda path: {"routing_key": "secret-routing-key"},
    )
    assert driver.provision(_spec(_full_config())).ok
    logging.calls.clear()
    monitoring.calls.clear()

    assert driver.provision(_spec(_full_config())).ok

    assert not [call for call in logging.calls + monitoring.calls if call[0] in {"create", "update", "delete"}]


def test_provider_output_fields_do_not_cause_immutable_link_drift() -> None:
    driver, logging, _ = _driver(
        secret_reader=lambda path: {"routing_key": "secret-routing-key"},
    )
    config = _full_config()
    assert driver.provision(_spec(config)).ok
    link = next(iter(logging.resources["log_links"].values()))
    link["bigqueryDataset"] = {
        "datasetId": "bigquery.googleapis.com/projects/acme-prod/datasets/analytics",
    }
    link["lifecycleState"] = "ACTIVE"
    logging.calls.clear()

    result = driver.provision(_spec(config))

    assert result.ok
    assert not [call for call in logging.calls if call[0] in {"create", "update", "delete"}]


def test_update_reconciles_mutable_provider_native_fields() -> None:
    driver, logging, _ = _driver()
    first = driver.provision(_spec({"deletion_protection": False}))
    assert first.ok
    logging.calls.clear()

    result = driver.update(
        UpdateSpec(
            handle=first.handle,
            config={
                "deletion_protection": False,
                "log_bucket": {"retention_days": 120, "body": {"restrictedFields": ["labels.ssn"]}},
            },
        ),
    )

    assert result.ok
    updates = [call for call in logging.calls if call[0] == "update" and call[1] == "log_bucket"]
    assert len(updates) == 1
    assert set(updates[0][4]) == {"restrictedFields", "retentionDays"}


def test_logging_location_is_immutable_and_carried_by_handle() -> None:
    driver, logging, _ = _driver()
    first = driver.provision(
        _spec({"location": "us-central1", "deletion_protection": False}),
    )
    assert first.handle.startswith("observability/us-central1/")
    logging.calls.clear()

    result = driver.update(
        UpdateSpec(
            handle=first.handle,
            config={"location": "europe-west1", "deletion_protection": False},
        ),
    )

    assert result.ok is False
    assert result.errors == ["immutable_location"]
    assert not [call for call in logging.calls if call[0] in {"create", "update", "delete"}]


def test_log_sink_writer_identity_options_reconcile_through_api_query_params() -> None:
    driver, logging, _ = _driver()
    config = {
        "log_sinks": [
            {
                "id": "audit",
                "body": {"destination": "storage.googleapis.com/audit-logs"},
                "unique_writer_identity": False,
                "custom_writer_identity": "serviceAccount:logging@example.iam.gserviceaccount.com",
            },
        ],
    }

    result = driver.provision(_spec(config))

    assert result.ok
    create = next(call for call in logging.calls if call[0:2] == ("create", "log_sinks"))
    assert create[5] == {
        "customWriterIdentity": "serviceAccount:logging@example.iam.gserviceaccount.com",
    }
    sink = next(iter(logging.resources["log_sinks"].values()))
    assert sink["writerIdentity"] == "serviceAccount:logging@example.iam.gserviceaccount.com"


def test_nested_monitoring_groups_bind_uptime_checks_by_bundle_id() -> None:
    driver, _, monitoring = _driver()
    config = {
        "groups": [
            {
                "id": "children",
                "parent_group_id": "root",
                "body": {"displayName": "Children", "filter": "resource.type=gce_instance"},
            },
            {
                "id": "root",
                "body": {"displayName": "Root", "filter": "resource.type=gce_instance"},
            },
        ],
        "uptime_checks": [
            {
                "id": "group-check",
                "group_id": "children",
                "body": {
                    "displayName": "Group check",
                    "resourceGroup": {"resourceType": "INSTANCE"},
                    "tcpCheck": {},
                    "timeout": "10s",
                    "period": "60s",
                },
            },
        ],
    }

    result = driver.provision(_spec(config))

    assert result.ok
    groups = list(monitoring.resources["groups"].values())
    root = next(item for item in groups if ":root]" in item["displayName"])
    child = next(item for item in groups if ":children]" in item["displayName"])
    assert child["parentName"] == root["name"]
    uptime = next(iter(monitoring.resources["uptime_checks"].values()))
    assert uptime["resourceGroup"]["groupId"] == child["name"].rsplit("/", 1)[-1]


def test_prune_deletes_omitted_owned_resources() -> None:
    driver, _, monitoring = _driver()
    config = {
        "deletion_protection": False,
        "dashboards": [{"id": "one", "body": {"displayName": "One"}}],
    }
    first = driver.provision(_spec(config))
    assert first.ok

    result = driver.update(
        UpdateSpec(
            handle=first.handle,
            config={"deletion_protection": False, "dashboards": []},
        ),
    )

    assert result.ok
    assert monitoring.resources["dashboards"] == {}


def test_prune_false_retains_omitted_owned_resources() -> None:
    driver, _, monitoring = _driver()
    first = driver.provision(
        _spec(
            {
                "deletion_protection": False,
                "dashboards": [{"id": "one", "body": {"displayName": "One"}}],
            },
        ),
    )
    assert first.ok

    result = driver.update(
        UpdateSpec(
            handle=first.handle,
            config={"deletion_protection": False, "prune": False},
        ),
    )

    assert result.ok
    assert len(monitoring.resources["dashboards"]) == 1


def test_prune_refuses_to_omit_log_data_or_custom_metric_series() -> None:
    driver, logging, monitoring = _driver()
    first = driver.provision(
        _spec(
            {
                "deletion_protection": False,
                "metric_descriptors": [
                    {
                        "id": "requests",
                        "body": {"metricKind": "CUMULATIVE", "valueType": "INT64"},
                    },
                ],
            },
        ),
    )
    assert first.ok

    result = driver.update(
        UpdateSpec(
            handle=first.handle,
            config={"deletion_protection": False, "log_bucket": False},
        ),
    )

    assert result.ok is False
    assert "cannot be pruned" in result.message
    assert len(logging.resources["log_bucket"]) == 1
    assert len(monitoring.resources["metric_descriptors"]) == 1


def test_refuses_unowned_deterministic_resource_without_operator_adoption() -> None:
    driver, logging, _ = _driver()
    name = "projects/acme-prod/locations/global/buckets/application-logs"
    logging.seed(
        "log_bucket",
        {"name": name, "retentionDays": 7, "description": "created elsewhere"},
    )

    result = driver.provision(
        _spec({"log_bucket": {"id": "application-logs", "retention_days": 30}}),
    )

    assert result.ok is False
    assert "refusing to adopt" in result.message
    assert "operator-authorized" in result.message
    assert logging.resources["log_bucket"][name] == {
        "name": name,
        "retentionDays": 7,
        "description": "created elsewhere",
    }


@pytest.mark.parametrize(
    "config",
    [
        {"log_bucket": {"id": "application-logs", "retention_days": 30, "adopt": True}},
        {"log_metrics": [{"id": "failures", "body": {"filter": "severity>=ERROR"}, "adopt": True}]},
        {
            "services": [
                {
                    "id": "checkout-api",
                    "body": {"displayName": "Checkout API", "custom": {}},
                    "service_level_objectives": [
                        {"id": "availability", "body": {"goal": 0.999}, "adopt": True},
                    ],
                },
            ],
        },
    ],
)
def test_adopt_flag_is_rejected_not_ignored(config: dict[str, Any]) -> None:
    """Adoption is operator-only (#2021): the old per-declaration ``adopt`` is refused outright."""
    driver, logging, _ = _driver()
    name = "projects/acme-prod/locations/global/buckets/application-logs"
    logging.seed(
        "log_bucket",
        {"name": name, "retentionDays": 7, "description": "created elsewhere"},
    )

    result = driver.provision(_spec(config))

    assert result.ok is False
    assert result.errors == ["invalid_cloud_operations_config"]
    assert "adopt is not accepted" in result.message
    assert "astrolift-observability" not in logging.resources["log_bucket"][name]["description"]


def test_locked_bucket_refuses_mutation_and_data_delete_even_with_force() -> None:
    driver, logging, _ = _driver()
    result = driver.provision(
        _spec({"deletion_protection": False, "log_bucket": {"locked": True}}),
    )
    assert result.ok
    bucket = next(iter(logging.resources["log_bucket"].values()))
    bucket["locked"] = True

    update = driver.update(
        UpdateSpec(
            handle=result.handle,
            config={"deletion_protection": False, "log_bucket": {"retention_days": 365}},
        ),
    )
    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        delete_data=True,
        force_destroy=True,
    )

    assert update.ok is False
    assert "locked" in update.message
    assert deleted.ok is False
    assert deleted.errors == ["locked_log_bucket"]
    assert bucket["name"] in logging.resources["log_bucket"]


def test_safe_deprovision_retains_bucket_and_views_but_deletes_configuration() -> None:
    driver, logging, monitoring = _driver(
        secret_reader=lambda path: {"routing_key": "secret-routing-key"},
    )
    result = driver.provision(_spec(_full_config()))
    assert result.ok
    groups = list(monitoring.resources["groups"].values())
    parent_group = next(item for item in groups if ":platform]" in item["displayName"])
    child_group = next(item for item in groups if ":checkout]" in item["displayName"])

    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
    )

    assert deleted.ok
    assert "retained Cloud Logging" in deleted.message
    assert len(logging.resources["log_bucket"]) == 1
    assert len(logging.resources["log_views"]) == 1
    assert all(
        not values for key, values in logging.resources.items() if key not in {"log_bucket", "log_views", "log_links"}
    )
    assert len(logging.resources["log_links"]) == 1
    assert len(monitoring.resources["metric_descriptors"]) == 1
    assert all(not values for key, values in monitoring.resources.items() if key != "metric_descriptors")
    group_deletes = [call for call in monitoring.calls if call[0:2] == ("delete", "groups")]
    assert [call[2] for call in group_deletes] == [child_group["name"], parent_group["name"]]


def test_destructive_deprovision_deletes_bucket_and_views() -> None:
    driver, logging, _ = _driver()
    result = driver.provision(
        _spec(
            {
                "deletion_protection": False,
                "log_views": [{"id": "errors", "body": {"filter": "severity>=ERROR"}}],
            },
        ),
    )
    assert result.ok

    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        delete_data=True,
    )

    assert deleted.ok
    assert all(not values for values in logging.resources.values())


def test_deletion_protection_is_non_retryable() -> None:
    driver, _, _ = _driver()
    result = driver.provision(_spec())
    assert result.ok

    deleted = driver.deprovision(DeprovisionSpec(result.handle))

    assert deleted.ok is False
    assert deleted.retryable is False
    assert deleted.errors == ["deletion_protection_enabled"]


def test_external_alert_channel_dependency_is_preflighted_before_mutation() -> None:
    driver, _, monitoring = _driver(secret_reader=lambda path: {"value": "secret"})
    config = {
        "deletion_protection": False,
        "notification_channels": [
            {
                "id": "pager",
                "body": {"type": "pagerduty", "labels": {}},
                "label_secret_refs": {"service_key": "pager"},
            },
        ],
    }
    result = driver.provision(_spec(config))
    assert result.ok
    channel = next(iter(monitoring.resources["notification_channels"].values()))
    monitoring.seed(
        "alert_policies",
        {
            "name": "projects/acme-prod/alertPolicies/external",
            "displayName": "External",
            "notificationChannels": [channel["name"]],
        },
    )
    monitoring.calls.clear()

    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
    )

    assert deleted.ok is False
    assert deleted.retryable is False
    assert deleted.errors == ["external_notification_channel_reference"]
    assert not [call for call in monitoring.calls if call[0] == "delete"]


def test_force_destroy_allows_channel_dependency_delete_request() -> None:
    driver, _, monitoring = _driver(secret_reader=lambda path: {"value": "secret"})
    result = driver.provision(
        _spec(
            {
                "deletion_protection": False,
                "notification_channels": [
                    {
                        "id": "pager",
                        "body": {"type": "pagerduty", "labels": {}},
                        "label_secret_refs": {"service_key": "pager"},
                    },
                ],
            },
        ),
    )
    channel = next(iter(monitoring.resources["notification_channels"].values()))
    monitoring.seed(
        "alert_policies",
        {
            "name": "projects/acme-prod/alertPolicies/external",
            "notificationChannels": [channel["name"]],
        },
    )

    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        force_destroy=True,
    )

    assert deleted.ok
    assert any(
        call[0] == "delete" and call[1] == "notification_channels" and call[3] is True for call in monitoring.calls
    )


def test_unmanaged_slo_is_preflighted_before_any_delete() -> None:
    driver, _, monitoring = _driver()
    result = driver.provision(
        _spec(
            {
                "deletion_protection": False,
                "services": [{"id": "api", "body": {"displayName": "API", "custom": {}}}],
            },
        ),
    )
    service = next(iter(monitoring.resources["services"].values()))
    monitoring.seed(
        "service_level_objectives",
        {
            "name": f"{service['name']}/serviceLevelObjectives/external",
            "displayName": "External SLO",
        },
    )
    monitoring.calls.clear()

    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        force_destroy=True,
    )

    assert deleted.ok is False
    assert deleted.errors == ["external_service_level_objective"]
    assert not [call for call in monitoring.calls if call[0] == "delete"]


def test_unmanaged_uptime_check_blocks_monitoring_group_delete() -> None:
    driver, _, monitoring = _driver()
    result = driver.provision(
        _spec(
            {
                "deletion_protection": False,
                "groups": [
                    {"id": "api", "body": {"displayName": "API", "filter": "resource.type=gce_instance"}},
                ],
            },
        ),
    )
    group = next(iter(monitoring.resources["groups"].values()))
    monitoring.seed(
        "uptime_checks",
        {
            "name": "projects/acme-prod/uptimeCheckConfigs/external",
            "displayName": "External",
            "resourceGroup": {"groupId": group["name"].rsplit("/", 1)[-1]},
        },
    )
    monitoring.calls.clear()

    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        force_destroy=True,
    )

    assert deleted.ok is False
    assert deleted.errors == ["external_monitoring_group_reference"]
    assert not [call for call in monitoring.calls if call[0] == "delete"]


def test_unmanaged_log_view_blocks_data_delete_before_any_delete() -> None:
    driver, logging, monitoring = _driver()
    result = driver.provision(_spec({"deletion_protection": False}))
    bucket = next(iter(logging.resources["log_bucket"].values()))
    logging.seed(
        "log_views",
        {
            "name": f"{bucket['name']}/views/external",
            "description": "Unmanaged view",
            "filter": "severity>=WARNING",
        },
    )
    logging.calls.clear()
    monitoring.calls.clear()

    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        delete_data=True,
        force_destroy=True,
    )

    assert deleted.ok is False
    assert deleted.errors == ["external_log_view"]
    assert not [call for call in logging.calls + monitoring.calls if call[0] == "delete"]


def test_unmanaged_sink_blocks_log_bucket_data_delete() -> None:
    driver, logging, monitoring = _driver()
    result = driver.provision(_spec({"deletion_protection": False}))
    bucket = next(iter(logging.resources["log_bucket"].values()))
    logging.seed(
        "log_sinks",
        {
            "name": "projects/acme-prod/sinks/external",
            "destination": f"logging.googleapis.com/{bucket['name']}",
            "description": "Unmanaged sink",
        },
    )
    logging.calls.clear()
    monitoring.calls.clear()

    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        delete_data=True,
        force_destroy=True,
    )

    assert deleted.ok is False
    assert deleted.errors == ["external_log_bucket_reference"]
    assert not [call for call in logging.calls + monitoring.calls if call[0] == "delete"]


def test_unmanaged_alert_blocks_custom_metric_data_delete() -> None:
    driver, logging, monitoring = _driver()
    result = driver.provision(
        _spec(
            {
                "deletion_protection": False,
                "metric_descriptors": [
                    {
                        "id": "requests",
                        "body": {"metricKind": "CUMULATIVE", "valueType": "INT64"},
                    },
                ],
            },
        ),
    )
    descriptor = next(iter(monitoring.resources["metric_descriptors"].values()))
    monitoring.seed(
        "alert_policies",
        {
            "name": "projects/acme-prod/alertPolicies/external-metric",
            "conditions": [
                {"conditionThreshold": {"filter": f'metric.type="{descriptor["type"]}"'}},
            ],
        },
    )
    logging.calls.clear()
    monitoring.calls.clear()

    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        delete_data=True,
        force_destroy=True,
    )

    assert deleted.ok is False
    assert deleted.errors == ["external_metric_reference"]
    assert not [call for call in logging.calls + monitoring.calls if call[0] == "delete"]


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"mystery": True}, "unsupported"),
        ({"log_bucket": []}, "boolean or object"),
        ({"dashboards": {}}, "must be an array"),
        (
            {"log_links": [{"id": "analytics", "body": {"bigqueryDataset": {}}}]},
            "analytics_enabled=true",
        ),
        (
            {
                "groups": [
                    {"id": "child", "parent_group_id": "missing", "body": {"filter": "true"}},
                ],
            },
            "unknown parent_group_id",
        ),
        (
            {
                "groups": [
                    {"id": "one", "parent_group_id": "two", "body": {"filter": "true"}},
                    {"id": "two", "parent_group_id": "one", "body": {"filter": "true"}},
                ],
            },
            "parent cycle",
        ),
        ({"dashboards": [{"id": "Bad_ID", "body": {}}]}, "must match"),
        ({"dashboards": [{"id": "one", "body": {"name": "override"}}]}, "identity"),
        (
            {
                "log_sinks": [
                    {
                        "id": "audit",
                        "body": {"destination": "storage.googleapis.com/audit"},
                        "custom_writer_identity": "serviceAccount:audit@example.iam.gserviceaccount.com",
                    },
                ],
            },
            "unique_writer_identity=false",
        ),
        (
            {
                "notification_channels": [
                    {
                        "id": "pager",
                        "body": {"type": "pagerduty", "labels": {"service_key": "plaintext"}},
                    },
                ],
            },
            "label_secret_refs",
        ),
        (
            {
                "alert_policies": [
                    {"id": "alerts", "body": {}, "notification_channel_ids": "pager"},
                ],
            },
            "string array",
        ),
    ],
)
def test_invalid_config_fails_closed(config: dict[str, Any], message: str) -> None:
    driver, _, _ = _driver()

    result = driver.provision(_spec(config))

    assert result.ok is False
    assert message in result.message
    assert result.errors == ["invalid_cloud_operations_config"]


def test_missing_alert_channel_id_fails_without_creating_alert() -> None:
    driver, _, monitoring = _driver()
    result = driver.provision(
        _spec(
            {
                "alert_policies": [
                    {"id": "alerts", "body": {"displayName": "Alerts"}, "notification_channel_ids": ["missing"]},
                ],
            },
        ),
    )

    assert result.ok is False
    assert "unknown notification channel" in result.message
    assert not monitoring.resources.get("alert_policies")


def test_missing_channel_secret_field_does_not_disclose_values() -> None:
    driver, logging, _ = _driver(secret_reader=lambda path: {"other": "do-not-leak"})
    result = driver.provision(
        _spec(
            {
                "notification_channels": [
                    {
                        "id": "pager",
                        "body": {"type": "pagerduty", "labels": {}},
                        "label_secret_refs": {"service_key": "pager#value"},
                    },
                ],
            },
        ),
    )

    assert result.ok is False
    assert "do-not-leak" not in result.message
    assert "do-not-leak" not in repr(result.errors)
    assert not [call for call in logging.calls if call[0] == "create"]


def test_generic_body_secret_refs_inject_uptime_auth_without_persisting_it() -> None:
    driver, _, monitoring = _driver(
        secret_reader=lambda path: {"password": "runtime-only", "authorization": "Bearer runtime"},
    )
    config = {
        "uptime_checks": [
            {
                "id": "private-api",
                "body": {
                    "displayName": "Private API",
                    "httpCheck": {
                        "authInfo": {"username": "monitor"},
                        "headers": {},
                    },
                    "monitoredResource": {"type": "uptime_url", "labels": {"host": "api.example.com"}},
                },
                "body_secret_refs": {
                    "httpCheck.authInfo.password": "uptime#password",
                    "httpCheck.headers.Authorization": "uptime#authorization",
                },
            },
        ],
    }

    result = driver.provision(_spec(config))

    assert result.ok
    uptime = next(iter(monitoring.resources["uptime_checks"].values()))
    assert uptime["httpCheck"]["authInfo"]["password"] == "runtime-only"
    assert uptime["httpCheck"]["headers"]["Authorization"] == "Bearer runtime"
    assert "runtime-only" not in repr(config)
    assert "Bearer runtime" not in repr(config)


def test_notification_channel_verification_code_is_secret_backed_and_one_time() -> None:
    driver, _, monitoring = _driver(
        secret_reader=lambda path: {"code": "123456"},
    )
    config = {
        "notification_channels": [
            {
                "id": "email",
                "body": {"type": "email", "labels": {"email_address": "ops@example.com"}},
                "verification_code_secret_ref": "ops/channel-code#code",
            },
        ],
    }

    first = driver.provision(_spec(config))
    second = driver.provision(_spec(config))

    assert first.ok and second.ok
    assert [call for call in monitoring.calls if call[0] == "verify"] == [
        (
            "verify",
            next(iter(monitoring.resources["notification_channels"])),
            "123456",
        ),
    ]
    assert "123456" not in repr(config)


@pytest.mark.parametrize(
    "body",
    [
        {"httpCheck": {"authInfo": {"username": "u", "password": "plaintext"}}},
        {"httpCheck": {"headers": {"Authorization": "Bearer plaintext"}}},
    ],
)
def test_uptime_secrets_must_use_secret_references(body: dict[str, Any]) -> None:
    driver, _, _ = _driver()

    result = driver.provision(
        _spec({"uptime_checks": [{"id": "private-api", "body": body}]}),
    )

    assert result.ok is False
    assert "body_secret_refs" in result.message


def test_binding_emits_portable_values_and_least_privilege_modes() -> None:
    driver, _, _ = _driver()
    result = driver.provision(
        _spec(
            {
                "deletion_protection": False,
                "access_mode": "logs",
                "dashboards": [{"id": "overview", "body": {"displayName": "Overview"}}],
            },
        ),
    )
    assert result.ok

    binding = driver.binding(ServiceHandle(result.handle), {"access_mode": "logs"})

    assert binding.env_vars["OBSERVABILITY_PROVIDER"].literal == "cloud_operations"
    assert binding.env_vars["GCP_LOG_BUCKET"].literal.startswith(
        "projects/acme-prod/locations/global/buckets/",
    )
    assert "dashboards/builder" in str(binding.env_vars["DASHBOARD_URL"].literal)
    assert binding.iam_grants[0].actions == ["roles/logging.viewer", "roles/logging.logWriter"]


def test_status_and_schema_contracts() -> None:
    driver, _, _ = _driver()
    missing = driver.status(ServiceHandle("observability/global/missing"))
    result = driver.provision(_spec({"deletion_protection": False}))
    ready = driver.status(ServiceHandle(result.handle))

    assert missing.state == "deprovisioned"
    assert ready.state == "available"
    schema = driver.config_schema()
    assert schema["additionalProperties"] is False
    assert "notification_channels" in schema["properties"]
    assert "services" in schema["properties"]
    assert "GCP_CLOUD_OPERATIONS_BUNDLE" in driver.binding_schema().env_vars
    assert driver.editable_fields() == ["*"]
    with pytest.raises(CloudOperationsError, match="no snapshot API"):
        driver.snapshot(ServiceHandle(result.handle))


def test_plugin_runtime_and_availability_registration() -> None:
    assert PLUGIN.managed_service_drivers[("observability", "cloud_operations")] is CloudOperationsDriver
    entry = next(
        item
        for item in MATRIX.managed_services
        if item.plugin_id == "gcp" and item.kind == "observability" and item.variant == "cloud_operations"
    )
    assert entry.status == "preview"
    assert set(driver_env for driver_env in entry.binding_envs) >= {
        "OBSERVABILITY_PROVIDER",
        "GCP_LOG_BUCKET",
    }


def test_runtime_config_reads_operator_controls() -> None:
    from types import SimpleNamespace

    from core.cluster_observability import managed_config_for

    cluster = SimpleNamespace(
        slug="gcp-prod",
        region="us-central1",
        provider_config={
            "project_id": "acme-prod",
            "cloud_operations_location": "us-central1",
            "cloud_operations_name_prefix": "platform-observability",
            "cloud_operations_retention_days_default": 365,
            "cloud_operations_deletion_protection_default": False,
            "cloud_operations_logging_api_endpoint": "https://logging.example.test",
            "cloud_operations_monitoring_api_endpoint": "https://monitoring.example.test",
            "cloud_operations_request_timeout_seconds": 12,
            "cloud_operations_operation_timeout_seconds": 600,
            "cloud_operations_operation_poll_interval_seconds": 0.25,
            "secret_id_prefix": "platform",
        },
        auth_config={},
    )

    config = managed_config_for(
        "gcp",
        cluster,
        kind="observability",
        variant="cloud_operations",
    )

    assert config == CloudOperationsConfig(
        project_id="acme-prod",
        location="us-central1",
        name_prefix="platform-observability",
        retention_days_default=365,
        deletion_protection_default=False,
        secret_id_prefix="platform",
        logging_api_endpoint="https://logging.example.test",
        monitoring_api_endpoint="https://monitoring.example.test",
        request_timeout_seconds=12,
        operation_timeout_seconds=600,
        operation_poll_interval_seconds=0.25,
    )


@dataclass
class FakeResponse:
    status_code: int
    payload: dict[str, Any]
    text: str = ""

    @property
    def content(self) -> bytes:
        return b"json" if self.payload else b""

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_rest_client_uses_documented_versions_paths_and_parameters() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"buckets": [], "nextPageToken": "next"}),
            FakeResponse(200, {"buckets": []}),
            FakeResponse(200, {"name": "projects/p/locations/global/buckets/logs"}),
            FakeResponse(200, {"name": "projects/p/alertPolicies/a"}),
            FakeResponse(200, {}),
        ],
    )
    client = CloudOperationsRestClient(
        api_endpoint="https://example.test",
        timeout_seconds=7,
        session=session,
    )
    from gcp.managed import observability_cloud_operations as module

    assert client.list_resources(module._BUCKET, "projects/p/locations/global") == []
    client.create(module._BUCKET, "projects/p/locations/global", "logs", {"retentionDays": 30})
    client.update(
        module._ALERT,
        "projects/p/alertPolicies/a",
        {"displayName": "Alert"},
        ["displayName"],
    )
    client.delete(module._CHANNEL, "projects/p/notificationChannels/c", force=True)

    assert session.calls[0]["url"] == "https://example.test/v2/projects/p/locations/global/buckets"
    assert session.calls[0]["params"] == {"pageSize": "1000"}
    assert session.calls[1]["params"] == {"pageSize": "1000", "pageToken": "next"}
    assert session.calls[2]["params"] == {"bucketId": "logs"}
    assert session.calls[3]["url"] == "https://example.test/v3/projects/p/alertPolicies/a"
    assert session.calls[3]["params"] == {"updateMask": "displayName"}
    assert session.calls[4]["params"] == {"force": "true"}
    assert all(call["timeout"] == 7 for call in session.calls)


def test_rest_client_maps_errors_and_empty_responses() -> None:
    from gcp.managed import observability_cloud_operations as module

    not_found = CloudOperationsRestClient(
        api_endpoint="https://example.test",
        session=FakeSession([FakeResponse(404, {}, "missing")]),
    )
    with pytest.raises(CloudOperationsNotFound):
        not_found.get(module._DASHBOARD, "projects/p/dashboards/nope")

    failed = CloudOperationsRestClient(
        api_endpoint="https://example.test",
        session=FakeSession([FakeResponse(400, {"error": {"message": "invalid request"}})]),
    )
    with pytest.raises(CloudOperationsError, match="invalid request"):
        failed.delete(module._ALERT, "projects/p/alertPolicies/nope")

    empty = CloudOperationsRestClient(
        api_endpoint="https://example.test",
        session=FakeSession([FakeResponse(204, {})]),
    )
    empty.delete(module._ALERT, "projects/p/alertPolicies/a")


def test_rest_client_verifies_notification_channel_with_documented_action() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "name": "projects/p/notificationChannels/c",
                    "verificationStatus": "VERIFIED",
                },
            ),
        ],
    )
    client = CloudOperationsRestClient(
        api_endpoint="https://example.test",
        session=session,
    )

    result = client.verify_notification_channel(
        "projects/p/notificationChannels/c",
        "123456",
    )

    assert result["verificationStatus"] == "VERIFIED"
    assert session.calls[0]["url"] == ("https://example.test/v3/projects/p/notificationChannels/c:verify")
    assert session.calls[0]["json"] == {"code": "123456"}


def test_rest_client_waits_for_logging_link_operations() -> None:
    from gcp.managed import observability_cloud_operations as module

    session = FakeSession(
        [
            FakeResponse(200, {"name": "projects/p/locations/global/operations/create-link"}),
            FakeResponse(
                200,
                {
                    "name": "projects/p/locations/global/operations/create-link",
                    "done": True,
                    "response": {
                        "name": "projects/p/locations/global/buckets/logs/links/analytics",
                    },
                },
            ),
            FakeResponse(
                200,
                {
                    "name": "projects/p/locations/global/operations/delete-link",
                    "done": True,
                    "response": {},
                },
            ),
        ],
    )
    client = CloudOperationsRestClient(
        api_endpoint="https://example.test",
        operation_poll_interval_seconds=0,
        session=session,
    )

    created = client.create(
        module._LINK,
        "projects/p/locations/global/buckets/logs",
        "analytics",
        {"description": "analytics link"},
    )
    client.delete(
        module._LINK,
        "projects/p/locations/global/buckets/logs/links/analytics",
    )

    assert created["name"].endswith("/links/analytics")
    assert session.calls[0]["params"] == {"linkId": "analytics"}
    assert session.calls[1]["method"] == "GET"
    assert session.calls[1]["url"].endswith(
        "/v2/projects/p/locations/global/operations/create-link",
    )
