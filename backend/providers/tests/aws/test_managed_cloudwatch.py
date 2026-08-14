"""Lifecycle, ownership, request-shape, and safety tests for AWS CloudWatch bundles."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.observability_cloudwatch import CloudWatchConfig, CloudWatchDriver


class NotFound(Exception):
    response: ClassVar[dict] = {"Error": {"Code": "ResourceNotFoundException"}}


class FakeLogs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.groups: dict[str, dict] = {}
        self.tags: dict[str, dict[str, str]] = {}
        self.metric_filters: dict[str, dict[str, dict]] = {}
        self.subscription_filters: dict[str, dict[str, dict]] = {}
        self.policies: dict[tuple[str, str], str] = {}
        self.transformers: dict[str, list[dict]] = {}

    def _call(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs_for(self, name: str, occurrence: int = -1) -> dict:
        return [kwargs for call, kwargs in self.calls if call == name][occurrence]

    def create_log_group(self, **kwargs):
        self._call("create_log_group", kwargs)
        name = kwargs["logGroupName"]
        if name in self.groups:
            raise RuntimeError("exists")
        arn = f"arn:aws:logs:us-east-1:123456789012:log-group:{name}"
        self.groups[name] = {
            "logGroupName": name,
            "logGroupArn": arn,
            "arn": f"{arn}:*",
            "logGroupClass": kwargs.get("logGroupClass", "STANDARD"),
            "deletionProtectionEnabled": bool(kwargs.get("deletionProtectionEnabled", False)),
            "kmsKeyId": kwargs.get("kmsKeyId"),
            "retentionInDays": None,
        }
        self.tags[arn] = dict(kwargs.get("tags") or {})
        return {}

    def describe_log_groups(self, **kwargs):
        self._call("describe_log_groups", kwargs)
        prefix = kwargs.get("logGroupNamePrefix", "")
        return {"logGroups": [dict(group) for name, group in self.groups.items() if name.startswith(prefix)]}

    def list_log_groups(self, **kwargs):
        self._call("list_log_groups", kwargs)
        tag_filter = kwargs.get("logGroupTags") or {}
        groups = []
        for group in self.groups.values():
            arn = group["logGroupArn"]
            if all(self.tags.get(arn, {}).get(key) == value for key, value in tag_filter.items()):
                groups.append(dict(group))
        return {"logGroups": groups}

    def list_tags_for_resource(self, **kwargs):
        self._call("list_tags_for_resource", kwargs)
        return {"tags": dict(self.tags.get(kwargs["resourceArn"], {}))}

    def tag_resource(self, **kwargs):
        self._call("tag_resource", kwargs)
        self.tags.setdefault(kwargs["resourceArn"], {}).update(kwargs["tags"])

    def put_log_group_deletion_protection(self, **kwargs):
        self._call("put_log_group_deletion_protection", kwargs)
        self.groups[kwargs["logGroupIdentifier"]]["deletionProtectionEnabled"] = kwargs["deletionProtectionEnabled"]

    def associate_kms_key(self, **kwargs):
        self._call("associate_kms_key", kwargs)
        self.groups[kwargs["logGroupName"]]["kmsKeyId"] = kwargs["kmsKeyId"]

    def disassociate_kms_key(self, **kwargs):
        self._call("disassociate_kms_key", kwargs)
        self.groups[kwargs["logGroupName"]]["kmsKeyId"] = None

    def put_retention_policy(self, **kwargs):
        self._call("put_retention_policy", kwargs)
        self.groups[kwargs["logGroupName"]]["retentionInDays"] = kwargs["retentionInDays"]

    def delete_retention_policy(self, **kwargs):
        self._call("delete_retention_policy", kwargs)
        self.groups[kwargs["logGroupName"]]["retentionInDays"] = None

    def put_data_protection_policy(self, **kwargs):
        self._call("put_data_protection_policy", kwargs)
        self.policies[(kwargs["logGroupIdentifier"], "data")] = kwargs["policyDocument"]

    def delete_data_protection_policy(self, **kwargs):
        self._call("delete_data_protection_policy", kwargs)
        self.policies.pop((kwargs["logGroupIdentifier"], "data"), None)

    def put_index_policy(self, **kwargs):
        self._call("put_index_policy", kwargs)
        self.policies[(kwargs["logGroupIdentifier"], "index")] = kwargs["policyDocument"]

    def delete_index_policy(self, **kwargs):
        self._call("delete_index_policy", kwargs)
        self.policies.pop((kwargs["logGroupIdentifier"], "index"), None)

    def put_transformer(self, **kwargs):
        self._call("put_transformer", kwargs)
        self.transformers[kwargs["logGroupIdentifier"]] = kwargs["transformerConfig"]

    def delete_transformer(self, **kwargs):
        self._call("delete_transformer", kwargs)
        self.transformers.pop(kwargs["logGroupIdentifier"], None)

    def put_metric_filter(self, **kwargs):
        self._call("put_metric_filter", kwargs)
        self.metric_filters.setdefault(kwargs["logGroupName"], {})[kwargs["filterName"]] = dict(kwargs)

    def describe_metric_filters(self, **kwargs):
        self._call("describe_metric_filters", kwargs)
        return {"metricFilters": list(self.metric_filters.get(kwargs["logGroupName"], {}).values())}

    def delete_metric_filter(self, **kwargs):
        self._call("delete_metric_filter", kwargs)
        self.metric_filters.get(kwargs["logGroupName"], {}).pop(kwargs["filterName"], None)

    def put_subscription_filter(self, **kwargs):
        self._call("put_subscription_filter", kwargs)
        self.subscription_filters.setdefault(kwargs["logGroupName"], {})[kwargs["filterName"]] = dict(kwargs)

    def describe_subscription_filters(self, **kwargs):
        self._call("describe_subscription_filters", kwargs)
        return {
            "subscriptionFilters": list(self.subscription_filters.get(kwargs["logGroupName"], {}).values()),
        }

    def delete_subscription_filter(self, **kwargs):
        self._call("delete_subscription_filter", kwargs)
        self.subscription_filters.get(kwargs["logGroupName"], {}).pop(kwargs["filterName"], None)

    def delete_log_group(self, **kwargs):
        self._call("delete_log_group", kwargs)
        name = kwargs["logGroupName"]
        if name not in self.groups:
            raise NotFound(name)
        if self.groups[name].get("deletionProtectionEnabled"):
            raise RuntimeError("deletion protection")
        arn = self.groups[name]["logGroupArn"]
        self.groups.pop(name)
        self.tags.pop(arn, None)

    def seed(self, name: str, *, bundle: str = "foreign", protected: bool = False) -> dict:
        self.create_log_group(
            logGroupName=name,
            logGroupClass="STANDARD",
            deletionProtectionEnabled=protected,
            tags={"astrolift.io/observability-bundle": bundle},
        )
        self.calls.clear()
        return self.groups[name]


class FakeCloudWatch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.alarms: dict[str, dict] = {}
        self.dashboards: dict[str, dict] = {}
        self.insights: dict[str, dict] = {}
        self.streams: dict[str, dict] = {}
        self.tags: dict[str, dict[str, str]] = {}
        self.dashboard_validation_messages: list[dict] = []
        self.insight_delete_failures: list[dict] = []

    def _call(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs_for(self, name: str, occurrence: int = -1) -> dict:
        return [kwargs for call, kwargs in self.calls if call == name][occurrence]

    @staticmethod
    def _tag_map(values: list[dict] | None) -> dict[str, str]:
        return {str(item["Key"]): str(item["Value"]) for item in values or []}

    def put_metric_alarm(self, **kwargs):
        self._call("put_metric_alarm", kwargs)
        name = kwargs["AlarmName"]
        arn = f"arn:aws:cloudwatch:us-east-1:123456789012:alarm:{name}"
        self.alarms[name] = {**kwargs, "AlarmArn": arn, "StateValue": "INSUFFICIENT_DATA", "kind": "metric"}
        self.tags.setdefault(arn, {}).update(self._tag_map(kwargs.get("Tags")))
        return {}

    def put_composite_alarm(self, **kwargs):
        self._call("put_composite_alarm", kwargs)
        name = kwargs["AlarmName"]
        arn = f"arn:aws:cloudwatch:us-east-1:123456789012:alarm:{name}"
        self.alarms[name] = {**kwargs, "AlarmArn": arn, "StateValue": "OK", "kind": "composite"}
        self.tags.setdefault(arn, {}).update(self._tag_map(kwargs.get("Tags")))
        return {}

    def describe_alarms(self, **kwargs):
        self._call("describe_alarms", kwargs)
        values = list(self.alarms.values())
        if kwargs.get("AlarmNames"):
            values = [item for item in values if item["AlarmName"] in kwargs["AlarmNames"]]
        return {
            "MetricAlarms": [dict(item) for item in values if item["kind"] == "metric"],
            "CompositeAlarms": [dict(item) for item in values if item["kind"] == "composite"],
        }

    def delete_alarms(self, **kwargs):
        self._call("delete_alarms", kwargs)
        for name in kwargs["AlarmNames"]:
            self.alarms.pop(name, None)

    def put_dashboard(self, **kwargs):
        self._call("put_dashboard", kwargs)
        name = kwargs["DashboardName"]
        arn = f"arn:aws:cloudwatch::123456789012:dashboard/{name}"
        self.dashboards[name] = {
            "DashboardName": name,
            "DashboardArn": arn,
            "DashboardBody": kwargs["DashboardBody"],
        }
        self.tags.setdefault(arn, {}).update(self._tag_map(kwargs.get("Tags")))
        return {"DashboardValidationMessages": self.dashboard_validation_messages}

    def get_dashboard(self, **kwargs):
        self._call("get_dashboard", kwargs)
        if kwargs["DashboardName"] not in self.dashboards:
            raise NotFound(kwargs["DashboardName"])
        return dict(self.dashboards[kwargs["DashboardName"]])

    def list_dashboards(self, **kwargs):
        self._call("list_dashboards", kwargs)
        prefix = kwargs.get("DashboardNamePrefix", "")
        return {
            "DashboardEntries": [dict(item) for name, item in self.dashboards.items() if name.startswith(prefix)],
        }

    def delete_dashboards(self, **kwargs):
        self._call("delete_dashboards", kwargs)
        for name in kwargs["DashboardNames"]:
            self.dashboards.pop(name, None)

    def put_insight_rule(self, **kwargs):
        self._call("put_insight_rule", kwargs)
        name = kwargs["RuleName"]
        arn = f"arn:aws:cloudwatch:us-east-1:123456789012:insight-rule/{name}"
        self.insights[name] = {**kwargs, "Name": name, "RuleArn": arn}
        self.tags.setdefault(arn, {}).update(self._tag_map(kwargs.get("Tags")))
        return {}

    def describe_insight_rules(self, **kwargs):
        self._call("describe_insight_rules", kwargs)
        return {"InsightRules": [dict(item) for item in self.insights.values()]}

    def delete_insight_rules(self, **kwargs):
        self._call("delete_insight_rules", kwargs)
        if self.insight_delete_failures:
            return {"Failures": list(self.insight_delete_failures)}
        for name in kwargs["RuleNames"]:
            self.insights.pop(name, None)
        return {"Failures": []}

    def put_metric_stream(self, **kwargs):
        self._call("put_metric_stream", kwargs)
        name = kwargs["Name"]
        arn = f"arn:aws:cloudwatch:us-east-1:123456789012:metric-stream/{name}"
        self.streams[name] = {**kwargs, "Name": name, "Arn": arn}
        self.tags.setdefault(arn, {}).update(self._tag_map(kwargs.get("Tags")))
        return {"Arn": arn}

    def get_metric_stream(self, **kwargs):
        self._call("get_metric_stream", kwargs)
        if kwargs["Name"] not in self.streams:
            raise NotFound(kwargs["Name"])
        return dict(self.streams[kwargs["Name"]])

    def list_metric_streams(self, **kwargs):
        self._call("list_metric_streams", kwargs)
        return {"Entries": [dict(item) for item in self.streams.values()]}

    def delete_metric_stream(self, **kwargs):
        self._call("delete_metric_stream", kwargs)
        self.streams.pop(kwargs["Name"], None)
        return {}

    def tag_resource(self, **kwargs):
        self._call("tag_resource", kwargs)
        self.tags.setdefault(kwargs["ResourceARN"], {}).update(self._tag_map(kwargs["Tags"]))

    def list_tags_for_resource(self, **kwargs):
        self._call("list_tags_for_resource", kwargs)
        return {
            "Tags": [
                {"Key": key, "Value": value} for key, value in sorted(self.tags.get(kwargs["ResourceARN"], {}).items())
            ],
        }

    def seed_alarm(self, name: str, *, bundle: str = "foreign") -> dict:
        self.put_metric_alarm(
            AlarmName=name,
            Namespace="Test",
            MetricName="Count",
            ComparisonOperator="GreaterThanThreshold",
            EvaluationPeriods=1,
            Threshold=1,
            Tags=[{"Key": "astrolift.io/observability-bundle", "Value": bundle}],
        )
        self.calls.clear()
        return self.alarms[name]


def _config() -> CloudWatchConfig:
    return CloudWatchConfig(region="us-east-1", account_id="123456789012")


def _driver() -> tuple[CloudWatchDriver, FakeLogs, FakeCloudWatch]:
    logs = FakeLogs()
    cw = FakeCloudWatch()
    return CloudWatchDriver(config=_config(), logs_client=logs, cloudwatch_client=cw), logs, cw


def _spec(config: dict | None = None) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="steady",
        app_id="app-id",
        app_slug="triage",
        environment_id="env-id",
        environment_name="production",
        tenant_cluster_id="cluster-id",
        service_handle_hint="observability",
        size="small",
        config=config if config is not None else {},
        binding_id="binding-id",
        managed_service_id="service-id",
    )


def _bundle(result) -> str:
    return result.handle.split("/", 1)[1]


def test_default_bundle_creates_log_group_dashboard_and_portable_binding() -> None:
    driver, logs, cw = _driver()
    result = driver.provision(_spec())
    assert result.ok and result.ready
    bundle = _bundle(result)
    group_name = f"/astrolift/{bundle}"
    assert group_name in logs.groups
    assert logs.groups[group_name]["retentionInDays"] == 30
    assert logs.tags[logs.groups[group_name]["logGroupArn"]]["astrolift.io/binding"] == "binding-id"
    assert f"astrolift-{bundle}" in cw.dashboards

    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["OBSERVABILITY_PROVIDER"].literal == "cloudwatch"
    assert binding.env_vars["LOG_GROUP"].literal == group_name
    assert binding.env_vars["CLOUDWATCH_LOGS_ENDPOINT"].literal == "https://logs.us-east-1.amazonaws.com"
    assert "dashboards:name=" in (binding.env_vars["DASHBOARD_URL"].literal or "")
    assert [grant.actions for grant in binding.iam_grants] == [
        ["logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents"],
        ["cloudwatch:PutMetricData"],
    ]


def test_full_log_group_features_are_reconciled() -> None:
    driver, logs, _ = _driver()
    config = {
        "dashboard": False,
        "log_group": {
            "name": "/apps/triage",
            "class": "STANDARD",
            "retention_days": 90,
            "kms_key_id": "arn:aws:kms:us-east-1:123:key/1",
            "deletion_protection": False,
            "data_protection_policy": {"Name": "mask"},
            "index_policy": {"Fields": ["requestId"]},
            "transformer": [{"parseJSON": {}}],
            "metric_filters": [
                {
                    "name": "errors",
                    "request": {
                        "filterPattern": "ERROR",
                        "metricTransformations": [
                            {
                                "metricName": "Errors",
                                "metricNamespace": "Astrolift/Triage",
                                "metricValue": "1",
                            },
                        ],
                    },
                },
            ],
            "subscription_filters": [
                {
                    "name": "archive",
                    "request": {
                        "filterPattern": "",
                        "destinationArn": "arn:aws:firehose:us-east-1:123:deliverystream/logs",
                        "roleArn": "arn:aws:iam::123:role/logs",
                    },
                },
            ],
        },
    }
    result = driver.provision(_spec(config))
    assert result.ok
    group = logs.groups["/apps/triage"]
    assert group["kmsKeyId"].endswith("key/1")
    assert group["retentionInDays"] == 90
    assert ("/apps/triage", "data") in logs.policies
    assert ("/apps/triage", "index") in logs.policies
    assert logs.transformers["/apps/triage"] == [{"parseJSON": {}}]
    assert "errors" in logs.metric_filters["/apps/triage"]
    assert "archive" in logs.subscription_filters["/apps/triage"]


def test_provision_is_idempotent_and_updates_mutable_log_settings() -> None:
    driver, logs, _ = _driver()
    first = driver.provision(_spec({"dashboard": False}))
    second = driver.provision(_spec({"dashboard": False}))
    assert first.ok and second.ok and first.handle == second.handle
    assert logs.names().count("create_log_group") == 1
    updated = driver.update(
        UpdateSpec(
            first.handle,
            config={
                "dashboard": False,
                "log_group": {
                    "retention_days": 365,
                    "kms_key_id": "arn:aws:kms:us-east-1:123:key/new",
                    "deletion_protection": False,
                },
            },
        ),
    )
    assert updated.ok
    name = logs.kwargs_for("associate_kms_key")["logGroupName"]
    assert logs.groups[name]["retentionInDays"] == 365
    assert logs.groups[name]["deletionProtectionEnabled"] is False


def test_log_filters_are_pruned_but_log_group_is_never_pruned_on_update() -> None:
    driver, logs, _ = _driver()
    config = {
        "dashboard": False,
        "log_group": {
            "metric_filters": [
                {
                    "name": "errors",
                    "request": {
                        "filterPattern": "ERROR",
                        "metricTransformations": [
                            {"metricName": "Errors", "metricNamespace": "Test", "metricValue": "1"},
                        ],
                    },
                },
            ],
        },
    }
    result = driver.provision(_spec(config))
    assert result.ok
    assert driver.update(UpdateSpec(result.handle, config={"dashboard": False})).ok
    assert "delete_metric_filter" in logs.names()
    renamed = driver.update(
        UpdateSpec(
            result.handle,
            config={"dashboard": False, "log_group": {"name": "/different"}},
        ),
    )
    assert not renamed.ok and "requires deprovisioning" in renamed.message


def test_log_group_class_is_immutable() -> None:
    driver, _, _ = _driver()
    result = driver.provision(_spec({"dashboard": False}))
    updated = driver.update(
        UpdateSpec(
            result.handle,
            config={"dashboard": False, "log_group": {"class": "INFREQUENT_ACCESS"}},
        ),
    )
    assert not updated.ok and "class is immutable" in updated.message


def test_alarms_dashboard_insight_rules_and_streams_are_managed_and_pruned() -> None:
    driver, _, cw = _driver()
    config = {
        "metric_alarms": [
            {
                "name": "triage-errors",
                "request": {
                    "Namespace": "Astrolift/Triage",
                    "MetricName": "Errors",
                    "ComparisonOperator": "GreaterThanThreshold",
                    "EvaluationPeriods": 1,
                    "Threshold": 1.0,
                    "Statistic": "Sum",
                    "Period": 60,
                },
            },
        ],
        "composite_alarms": [
            {"name": "triage-unhealthy", "request": {"AlarmRule": 'ALARM("triage-errors")'}},
        ],
        "dashboard": {"name": "triage-dashboard", "body": {"widgets": []}},
        "insight_rules": [
            {
                "name": "top-errors",
                "request": {"RuleDefinition": "{}", "RuleState": "ENABLED"},
            },
        ],
        "metric_streams": [
            {
                "name": "triage-stream",
                "request": {
                    "FirehoseArn": "arn:aws:firehose:us-east-1:123:deliverystream/metrics",
                    "RoleArn": "arn:aws:iam::123:role/metrics",
                    "OutputFormat": "json",
                },
            },
        ],
    }
    result = driver.provision(_spec(config))
    assert result.ok
    assert set(cw.alarms) == {"triage-errors", "triage-unhealthy"}
    assert set(cw.dashboards) == {"triage-dashboard"}
    assert set(cw.insights) == {"top-errors"}
    assert set(cw.streams) == {"triage-stream"}
    status = driver.status(ServiceHandle(result.handle))
    assert status.state == "available" and "2 alarms" in status.message

    updated = driver.update(UpdateSpec(result.handle, config={"dashboard": False}))
    assert updated.ok
    assert cw.alarms == {} and cw.dashboards == {} and cw.insights == {} and cw.streams == {}


def test_dashboard_validation_messages_fail_closed() -> None:
    driver, _, cw = _driver()
    cw.dashboard_validation_messages = [{"DataPath": "/widgets/0", "Message": "invalid widget"}]
    result = driver.provision(_spec())
    assert not result.ok and "invalid widget" in result.message


def test_dashboard_validation_messages_may_be_explicitly_accepted() -> None:
    driver, _, cw = _driver()
    cw.dashboard_validation_messages = [{"DataPath": "/widgets/0", "Message": "ignored warning"}]
    result = driver.provision(_spec({"dashboard": {"allow_validation_messages": True}}))
    assert result.ok


def test_failed_contributor_insights_deletion_is_not_reported_as_success() -> None:
    driver, _, cw = _driver()
    result = driver.provision(
        _spec(
            {
                "dashboard": False,
                "insight_rules": [
                    {"name": "top-errors", "request": {"RuleDefinition": "{}"}},
                ],
            },
        ),
    )
    assert result.ok
    cw.insight_delete_failures = [
        {"FailureResource": "top-errors", "ExceptionDescription": "still referenced"},
    ]
    updated = driver.update(UpdateSpec(result.handle, config={"dashboard": False}))
    assert not updated.ok and "top-errors: still referenced" in updated.message


def test_delivery_log_group_default_dashboard_avoids_logs_insights() -> None:
    driver, _, cw = _driver()
    result = driver.provision(
        _spec({"log_group": {"class": "DELIVERY", "retention_days": 1}}),
    )
    assert result.ok
    body = next(iter(cw.dashboards.values()))["DashboardBody"]
    assert '"type":"text"' in body
    assert '"type":"log"' not in body
    assert "Logs Insights is unavailable" in body


def test_foreign_log_group_and_alarm_are_never_adopted() -> None:
    driver, logs, cw = _driver()
    result = driver.provision(_spec({"dashboard": False}))
    bundle = _bundle(result)
    group_name = f"/astrolift/{bundle}"
    logs.tags[logs.groups[group_name]["logGroupArn"]]["astrolift.io/observability-bundle"] = "other"
    update = driver.update(UpdateSpec(result.handle, config={"dashboard": False}))
    assert not update.ok and "foreign" in update.message

    alarm_name = "foreign-alarm"
    cw.seed_alarm(alarm_name)
    alarm_result = driver.update(
        UpdateSpec(
            result.handle,
            config={
                "dashboard": False,
                "metric_alarms": [
                    {
                        "name": alarm_name,
                        "request": {
                            "Namespace": "Test",
                            "MetricName": "Count",
                            "ComparisonOperator": "GreaterThanThreshold",
                            "EvaluationPeriods": 1,
                            "Threshold": 1,
                        },
                    },
                ],
            },
        ),
    )
    assert not alarm_result.ok and "foreign" in alarm_result.message


def test_deprovision_retains_logs_without_delete_data() -> None:
    driver, logs, cw = _driver()
    result = driver.provision(_spec())
    deleted = driver.deprovision(DeprovisionSpec(result.handle, {}), force_destroy=True)
    assert deleted.ok and "retained" in deleted.message
    assert logs.groups
    assert cw.dashboards == {}


def test_delete_data_respects_both_protection_layers_and_force() -> None:
    driver, logs, _ = _driver()
    result = driver.provision(_spec())
    platform = driver.deprovision(DeprovisionSpec(result.handle, {}), delete_data=True)
    assert not platform.ok and platform.errors == ["deletion_protection_enabled"]

    cloud = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        delete_data=True,
    )
    assert not cloud.ok and cloud.errors == ["cloud_deletion_protection_enabled"]
    forced = driver.deprovision(
        DeprovisionSpec(result.handle, {}),
        delete_data=True,
        force_destroy=True,
    )
    assert forced.ok and logs.groups == {}
    assert "put_log_group_deletion_protection" in logs.names()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"log_group": "bad"}, "boolean or object"),
        ({"log_group": {"name": "aws/reserved"}}, "reserved"),
        ({"log_group": {"name": "bad name"}}, "invalid"),
        ({"log_group": {"class": "ARCHIVE"}}, "class"),
        ({"log_group": {"retention_days": 2}}, "not supported"),
        ({"log_group": {"retention_days": "later"}}, "integer"),
        ({"log_group": {"class": "DELIVERY", "retention_days": 30}}, "exactly one"),
        ({"log_group": {"class": "INFREQUENT_ACCESS", "index_policy": {"Fields": ["x"]}}}, "STANDARD"),
        ({"log_group": {"create": {"logGroupName": "escape"}}}, "Astrolift-owned"),
        ({"log_group": {"data_protection_policy": "bad"}}, "valid JSON"),
        (
            {
                "log_group": {
                    "subscription_filters": [
                        {"name": "a", "request": {}},
                        {"name": "b", "request": {}},
                        {"name": "c", "request": {}},
                    ],
                },
            },
            "at most two",
        ),
        ({"metric_alarms": "bad"}, "array"),
        ({"metric_alarms": [{"name": "x", "request": {"AlarmName": "escape"}}]}, "Astrolift-owned"),
        ({"metric_alarms": [{"name": "x", "request": {}}, {"name": "x", "request": {}}]}, "unique"),
        ({"metric_alarms": [{"name": "x" * 256, "request": {}}]}, "exceeds 255"),
        ({"composite_alarms": [{"name": "x", "request": {}}]}, "AlarmRule"),
        ({"insight_rules": [{"name": "x" * 129, "request": {"RuleDefinition": "{}"}}]}, "1-128"),
        ({"metric_streams": [{"name": "x", "request": {}}]}, "FirehoseArn"),
        (
            {
                "log_group": {
                    "metric_filters": [
                        {
                            "name": "bad:name",
                            "request": {"filterPattern": "", "metricTransformations": []},
                        },
                    ],
                },
            },
            "name is invalid",
        ),
        ({"dashboard": {"name": "bad.name"}}, "dashboard.name"),
        ({"dashboard": "bad"}, "boolean or object"),
        ({"dashboard": {"body": "bad-json"}}, "valid JSON"),
        ({"access_mode": "admin"}, "access_mode"),
        ({"log_group": False, "dashboard": False}, "at least one"),
    ],
)
def test_invalid_config_is_rejected_before_mutation(config: dict, message: str) -> None:
    driver, logs, cw = _driver()
    result = driver.provision(_spec(config))
    assert not result.ok and message in result.message
    assert "create_log_group" not in logs.names()
    assert "put_dashboard" not in cw.names()


def test_access_modes_emit_least_privilege_grants() -> None:
    driver, _, _ = _driver()
    result = driver.provision(_spec())
    read = driver.binding(ServiceHandle(result.handle), {"access_mode": "read"})
    assert [grant.actions for grant in read.iam_grants] == [
        ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:StartQuery"],
        ["logs:DescribeQueries", "logs:GetQueryResults", "logs:StopQuery"],
        ["cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"],
    ]
    assert driver.binding(ServiceHandle(result.handle), {"access_mode": "none"}).iam_grants == []


def test_snapshot_and_restore_are_explicitly_unsupported() -> None:
    driver, _, _ = _driver()
    with pytest.raises(Exception, match="cannot be snapshotted"):
        driver.snapshot(ServiceHandle("observability/bundle"))
    restored = driver.restore(
        SimpleNamespace(handle="snapshot/x", snapshot_id="x", created_at="now"),
        _spec(),
    )
    assert not restored.ok and restored.errors == ["not_implemented"]


def test_native_requests_match_current_botocore_shapes() -> None:
    driver, logs, cw = _driver()
    config = {
        "metric_alarms": [
            {
                "name": "errors",
                "request": {
                    "Namespace": "Test",
                    "MetricName": "Errors",
                    "ComparisonOperator": "GreaterThanThreshold",
                    "EvaluationPeriods": 1,
                    "Threshold": 1.0,
                },
            },
        ],
        "dashboard": {"name": "dash", "body": {"widgets": []}},
    }
    assert driver.provision(_spec(config)).ok
    logs_model = Session().get_service_model("logs")
    cw_model = Session().get_service_model("cloudwatch")
    validate_parameters(
        logs.kwargs_for("create_log_group"),
        logs_model.operation_model("CreateLogGroup").input_shape,
    )
    validate_parameters(
        cw.kwargs_for("put_metric_alarm"),
        cw_model.operation_model("PutMetricAlarm").input_shape,
    )
    validate_parameters(
        cw.kwargs_for("put_dashboard"),
        cw_model.operation_model("PutDashboard").input_shape,
    )


def test_registration_catalogue_cost_and_runtime_config() -> None:
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from aws.cost import SERVICE_CODE_BY_VARIANT
    from aws.plugin import PLUGIN

    assert ("observability", "cloudwatch") in PLUGIN.managed_service_drivers
    assert SERVICE_CODE_BY_VARIANT[("observability", "cloudwatch")] == "AmazonCloudWatch"
    entry = next(
        item
        for item in MATRIX.managed_services
        if item.plugin_id == "aws" and item.kind == "observability" and item.variant == "cloudwatch"
    )
    assert entry.status == "preview" and "OBSERVABILITY_PROVIDER" in entry.binding_envs
    assert "CLOUDWATCH_LOGS_ENDPOINT" in entry.binding_envs
    cluster = SimpleNamespace(
        slug="cluster",
        region="us-west-2",
        provider_config={
            "account_id": "123456789012",
            "cloudwatch_log_group_prefix": "/company/apps",
            "cloudwatch_retention_days_default": 90,
            "cloudwatch_log_group_class_default": "INFREQUENT_ACCESS",
            "cloudwatch_deletion_protection_default": False,
            "cloudwatch_dashboard_enabled_default": False,
        },
        auth_config={},
    )
    config = managed_config_for("aws", cluster, kind="observability", variant="cloudwatch")
    assert config.region == "us-west-2" and config.account_id == "123456789012"
    assert config.log_group_prefix == "/company/apps"
    assert config.retention_days_default == 90
    assert config.dashboard_enabled_default is False
