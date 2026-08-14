from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.event_bus_eventbridge import EventBridgeConfig, EventBridgeDriver

BUS_NAME = "platform-steadymd-triage-prod-events"
BUS_ARN = f"arn:aws:events:us-west-2:123456789012:event-bus/{BUS_NAME}"
RULE_ARN = f"arn:aws:events:us-west-2:123456789012:rule/{BUS_NAME}/route-bugs"


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "steadymd",
        "app_id": "app-1",
        "app_slug": "triage",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "events",
        "size": "small",
        "config": {},
        "tags": {"owner": "agents"},
        "isolation": "shared",
        "binding_id": "binding-1",
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _config() -> EventBridgeConfig:
    return EventBridgeConfig(
        region="us-west-2",
        account_id="123456789012",
        event_bus_name_prefix="platform",
        deletion_protection_default=True,
    )


def _client() -> MagicMock:
    client = MagicMock()
    client.create_event_bus.return_value = {"EventBusArn": BUS_ARN}
    client.describe_event_bus.return_value = {
        "Name": BUS_NAME,
        "Arn": BUS_ARN,
        "Policy": '{"Version":"2012-10-17","Statement":[]}',
        "KmsKeyIdentifier": "arn:aws:kms:us-west-2:123456789012:key/key-1",
    }
    client.list_rules.return_value = {"Rules": []}
    client.list_targets_by_rule.return_value = {"Targets": []}
    client.list_archives.return_value = {"Archives": []}
    client.put_rule.return_value = {"RuleArn": RULE_ARN}
    client.put_targets.return_value = {"FailedEntryCount": 0, "FailedEntries": []}
    client.remove_targets.return_value = {"FailedEntryCount": 0, "FailedEntries": []}
    client.list_tags_for_resource.return_value = {
        "Tags": [{"Key": "astrolift.io/managed-by", "Value": "platform"}],
    }
    client.describe_archive.side_effect = _not_found("DescribeArchive")
    return client


def _not_found(operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "ResourceNotFoundException", "Message": "not found"},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        operation,
    )


def _already_exists(operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "ResourceAlreadyExistsException", "Message": "already exists"},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        operation,
    )


def _validate(operation: str, params: dict) -> None:
    service = Session().get_service_model("events")
    validate_parameters(params, service.operation_model(operation).input_shape)


def test_provision_reconciles_bus_permission_rule_full_target_and_archive():
    client = _client()
    driver = EventBridgeDriver(config=_config(), client=client)

    result = driver.provision(
        _spec(
            config={
                "description": "EMR triage events",
                "kms_key_identifier": "arn:aws:kms:us-west-2:123456789012:key/key-1",
                "dead_letter_queue_arn": "arn:aws:sqs:us-west-2:123456789012:eventbridge-dlq",
                "log_config": {"include_detail": "FULL", "level": "ERROR"},
                "permissions": [
                    {
                        "sid": "partner-account",
                        "principal": "111122223333",
                        "condition": {
                            "type": "StringEquals",
                            "key": "aws:PrincipalOrgID",
                            "value": "o-example",
                        },
                    },
                ],
                "rules": [
                    {
                        "name": "route-bugs",
                        "event_pattern": {"source": ["steadymd.emr"]},
                        "state": "ENABLED",
                        "targets": [
                            {
                                "id": "triage-stream",
                                "arn": "arn:aws:kinesis:us-west-2:123456789012:stream/triage",
                                "role_arn": "arn:aws:iam::123456789012:role/eventbridge-target",
                                "input_transformer": {
                                    "input_paths_map": {"detail": "$.detail"},
                                    "input_template": '"<detail>"',
                                },
                                "kinesis": {"PartitionKeyPath": "$.detail.id"},
                                "dead_letter_queue_arn": "arn:aws:sqs:us-west-2:123456789012:target-dlq",
                                "retry_policy": {
                                    "maximum_event_age_seconds": 3600,
                                    "maximum_retry_attempts": 10,
                                },
                            },
                        ],
                    },
                ],
                "archive": {
                    "retention_days": 30,
                    "event_pattern": {"source": ["steadymd.emr"]},
                    "kms_key_identifier": "arn:aws:kms:us-west-2:123456789012:key/key-1",
                },
            },
        ),
    )

    assert result.ok and result.ready
    assert result.handle == f"event_bus/{BUS_ARN}"
    create = client.create_event_bus.call_args.kwargs
    assert create["Name"] == BUS_NAME
    assert create["DeadLetterConfig"]["Arn"].endswith("eventbridge-dlq")
    assert create["LogConfig"] == {"IncludeDetail": "FULL", "Level": "ERROR"}
    _validate("CreateEventBus", create)
    permission = client.put_permission.call_args.kwargs
    assert permission["StatementId"] == "astrolift-partner-account"
    _validate("PutPermission", permission)
    rule = client.put_rule.call_args.kwargs
    assert rule["EventPattern"] == '{"source":["steadymd.emr"]}'
    _validate("PutRule", rule)
    targets = client.put_targets.call_args.kwargs
    assert targets["Targets"][0]["KinesisParameters"] == {"PartitionKeyPath": "$.detail.id"}
    assert targets["Targets"][0]["RetryPolicy"]["MaximumRetryAttempts"] == 10
    _validate("PutTargets", targets)
    archive = client.create_archive.call_args.kwargs
    assert archive["EventSourceArn"] == BUS_ARN
    assert archive["RetentionDays"] == 30
    _validate("CreateArchive", archive)


def test_provision_existing_bus_is_idempotently_reconciled():
    client = _client()
    client.create_event_bus.side_effect = _already_exists("CreateEventBus")
    driver = EventBridgeDriver(config=_config(), client=client)

    result = driver.provision(_spec())

    assert result.ok
    assert result.handle == f"event_bus/{BUS_ARN}"
    client.describe_event_bus.assert_called_with(Name=BUS_NAME)
    client.tag_resource.assert_called_once()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"access_mode": "consume"}, "publish or manage"),
        ({"resource_policy": {}, "permissions": []}, "either resource_policy"),
        ({"permissions": [{"sid": "missing-principal"}]}, "requires sid and principal"),
        ({"rules": [{"name": "empty"}]}, "event_pattern or schedule_expression"),
        (
            {"rules": [{"name": "scheduled", "schedule_expression": "rate(5 minutes)"}]},
            "cannot use schedule_expression on a custom event bus",
        ),
        (
            {"rules": [{"name": "bad-pattern", "event_pattern": "not-json"}]},
            "Expecting value",
        ),
        (
            {
                "rules": [
                    {
                        "name": "too-many",
                        "event_pattern": {"source": ["x"]},
                        "targets": [{"id": f"target-{index}", "arn": f"arn:target:{index}"} for index in range(6)],
                    },
                ],
            },
            "more than five targets",
        ),
        (
            {
                "rules": [
                    {
                        "name": "bad-target",
                        "event_pattern": {"source": ["x"]},
                        "targets": [{"id": "x", "arn": "arn:x", "input": {}, "input_path": "$.detail"}],
                    },
                ],
            },
            "mutually exclusive",
        ),
        (
            {
                "rules": [
                    {
                        "name": "bad-retry",
                        "event_pattern": {"source": ["x"]},
                        "targets": [
                            {
                                "id": "x",
                                "arn": "arn:x",
                                "retry_policy": {"maximum_event_age_seconds": 59},
                            },
                        ],
                    },
                ],
            },
            "maximum_event_age_seconds must be an integer from 60 through 86400",
        ),
        ({"archive": {"retention_days": -1}}, "non-negative integer"),
        ({"archive": {"event_pattern": "[]"}}, "must contain a JSON object"),
    ],
)
def test_invalid_eventbridge_configuration_is_rejected(config, message):
    driver = EventBridgeDriver(config=_config(), client=_client())

    result = driver.provision(_spec(config=config))

    assert not result.ok
    assert message in result.message


def test_target_partial_failure_is_not_reported_as_success():
    client = _client()
    client.put_targets.return_value = {
        "FailedEntryCount": 1,
        "FailedEntries": [{"TargetId": "broken", "ErrorCode": "ConcurrentModificationException"}],
    }
    driver = EventBridgeDriver(config=_config(), client=client)

    result = driver.provision(
        _spec(
            config={
                "rules": [
                    {
                        "name": "route",
                        "event_pattern": {"source": ["x"]},
                        "targets": [{"id": "broken", "arn": "arn:aws:lambda:us-west-2:123:function:broken"}],
                    },
                ],
            },
        ),
    )

    assert not result.ok
    assert "partially failed" in result.message


def test_update_prunes_stale_targets_and_only_astrolift_owned_rules():
    client = _client()
    client.list_rules.return_value = {
        "Rules": [
            {"Name": "route-bugs", "Arn": RULE_ARN},
            {
                "Name": "stale",
                "Arn": f"arn:aws:events:us-west-2:123456789012:rule/{BUS_NAME}/stale",
            },
        ],
    }

    def targets(**request):
        if request["Rule"] == "route-bugs":
            return {
                "Targets": [
                    {"Id": "keep", "Arn": "arn:aws:lambda:us-west-2:123:function:keep"},
                    {"Id": "old", "Arn": "arn:aws:lambda:us-west-2:123:function:old"},
                ],
            }
        return {"Targets": [{"Id": "stale-target", "Arn": "arn:stale"}]}

    client.list_targets_by_rule.side_effect = targets
    driver = EventBridgeDriver(config=_config(), client=client)

    result = driver.update(
        UpdateSpec(
            handle=f"event_bus/{BUS_ARN}",
            config={
                "rules": [
                    {
                        "name": "route-bugs",
                        "event_pattern": {"source": ["steadymd.emr"]},
                        "targets": [{"id": "keep", "arn": "arn:aws:lambda:us-west-2:123:function:keep"}],
                    },
                ],
                "prune_rules": True,
            },
        ),
    )

    assert result.ok
    remove_calls = client.remove_targets.call_args_list
    assert any(call.kwargs["Ids"] == ["old"] for call in remove_calls)
    assert any(call.kwargs["Ids"] == ["stale-target"] for call in remove_calls)
    client.delete_rule.assert_called_once_with(Name="stale", EventBusName=BUS_NAME, Force=True)


def test_update_refuses_to_overwrite_an_external_rule_with_the_same_name():
    client = _client()
    client.list_rules.return_value = {"Rules": [{"Name": "route", "Arn": "arn:external-rule"}]}
    client.list_tags_for_resource.return_value = {"Tags": []}
    driver = EventBridgeDriver(config=_config(), client=client)

    result = driver.update(
        UpdateSpec(
            handle=f"event_bus/{BUS_ARN}",
            config={"rules": [{"name": "route", "event_pattern": {"source": ["x"]}}]},
        ),
    )

    assert not result.ok
    assert "outside this resource declaration" in result.message
    client.put_rule.assert_not_called()


def test_archive_disable_requires_explicit_data_deletion():
    client = _client()
    client.describe_archive.side_effect = None
    client.describe_archive.return_value = {
        "ArchiveName": f"{BUS_NAME}-archive",
        "State": "ENABLED",
        "EventCount": 42,
    }
    driver = EventBridgeDriver(config=_config(), client=client)

    refused = driver.update(
        UpdateSpec(
            handle=f"event_bus/{BUS_ARN}",
            config={"archive": {"enabled": False}},
        ),
    )
    deleted = driver.update(
        UpdateSpec(
            handle=f"event_bus/{BUS_ARN}",
            config={"archive": {"enabled": False, "delete_data": True}},
        ),
    )

    assert not refused.ok and "delete_data=true" in refused.message
    assert deleted.ok
    client.delete_archive.assert_called_once_with(ArchiveName=f"{BUS_NAME}-archive")


def test_deprovision_preflights_protection_archives_and_external_rules_before_mutating():
    client = _client()
    client.list_archives.return_value = {
        "Archives": [{"ArchiveName": f"{BUS_NAME}-archive", "State": "ENABLED"}],
    }
    driver = EventBridgeDriver(config=_config(), client=client)
    handle = f"event_bus/{BUS_ARN}"

    protected = driver.deprovision(DeprovisionSpec(handle))
    archived = driver.deprovision(
        DeprovisionSpec(handle, config={"deletion_protection": False}),
    )

    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    assert not archived.ok and archived.errors == ["retained_archives_require_delete_data"]
    client.delete_archive.assert_not_called()
    client.delete_event_bus.assert_not_called()

    client.list_archives.return_value = {"Archives": []}
    client.list_rules.return_value = {"Rules": [{"Name": "external", "Arn": "arn:external-rule"}]}
    client.list_tags_for_resource.return_value = {"Tags": []}
    external = driver.deprovision(
        DeprovisionSpec(handle, config={"deletion_protection": False}),
        delete_data=True,
    )

    assert not external.ok and external.errors == ["external_rules_present"]
    client.delete_rule.assert_not_called()
    client.delete_event_bus.assert_not_called()


def test_force_destroy_removes_archives_targets_rules_and_bus():
    client = _client()
    client.list_archives.return_value = {
        "Archives": [{"ArchiveName": "external-archive", "State": "ENABLED"}],
    }
    client.list_rules.return_value = {"Rules": [{"Name": "external", "Arn": "arn:external-rule"}]}
    client.list_targets_by_rule.return_value = {"Targets": [{"Id": "target", "Arn": "arn:target"}]}
    client.list_tags_for_resource.return_value = {"Tags": []}
    driver = EventBridgeDriver(config=_config(), client=client)

    result = driver.deprovision(
        DeprovisionSpec(f"event_bus/{BUS_ARN}"),
        delete_data=True,
        force_destroy=True,
    )

    assert result.ok
    client.delete_archive.assert_called_once_with(ArchiveName="external-archive")
    client.remove_targets.assert_called_once_with(
        Rule="external",
        EventBusName=BUS_NAME,
        Ids=["target"],
        Force=True,
    )
    client.delete_rule.assert_called_once_with(Name="external", EventBusName=BUS_NAME, Force=True)
    client.delete_event_bus.assert_called_once_with(Name=BUS_NAME)


def test_deprovision_never_claims_an_undeclared_default_named_archive():
    client = _client()
    client.list_archives.return_value = {
        "Archives": [{"ArchiveName": f"{BUS_NAME}-archive", "State": "ENABLED"}],
    }
    driver = EventBridgeDriver(config=_config(), client=client)

    result = driver.deprovision(
        DeprovisionSpec(
            f"event_bus/{BUS_ARN}",
            config={"deletion_protection": False},
        ),
        delete_data=True,
    )

    assert not result.ok and result.errors == ["external_archives_present"]
    client.delete_archive.assert_not_called()
    client.delete_event_bus.assert_not_called()


def test_binding_status_and_snapshot_contracts_are_honest_and_portable():
    client = _client()
    driver = EventBridgeDriver(config=_config(), client=client)
    handle = f"event_bus/{BUS_ARN}"

    target_role = "arn:aws:iam::123456789012:role/eventbridge-target"
    binding = driver.binding(
        ServiceHandle(handle),
        {
            "access_mode": "manage",
            "rules": [
                {
                    "name": "route",
                    "event_pattern": {"source": ["x"]},
                    "targets": [{"id": "target", "arn": "arn:target", "role_arn": target_role}],
                },
            ],
        },
    )
    status = driver.status(ServiceHandle(handle))

    assert binding.env_vars["EVENT_BUS_NAME"].literal == BUS_NAME
    assert binding.env_vars["EVENT_BUS_ARN"].literal == BUS_ARN
    grants = {(grant.resource, tuple(grant.actions)) for grant in binding.iam_grants}
    assert (BUS_ARN, ("events:DescribeEventBus", "events:PutEvents")) in grants
    assert any(resource.endswith(f":rule/{BUS_NAME}/*") and "events:PutRule" in actions for resource, actions in grants)
    assert any(resource == "*" and "events:ListRules" in actions for resource, actions in grants)
    assert (
        "arn:aws:kms:us-west-2:123456789012:key/key-1",
        ("kms:Decrypt",),
    ) in grants
    assert (target_role, ("iam:PassRole",)) in grants
    assert status.state == "available"
    with pytest.raises(ManagedServiceError, match="does not expose snapshots"):
        driver.snapshot(ServiceHandle(handle))
    assert "EVENT_BUS_NAME" in driver.binding_schema().env_vars
    assert "archive" in driver.config_schema()["properties"]


def test_missing_bus_status_and_update_do_not_claim_success():
    client = _client()
    client.describe_event_bus.side_effect = _not_found("DescribeEventBus")
    driver = EventBridgeDriver(config=_config(), client=client)
    handle = f"event_bus/{BUS_ARN}"

    status = driver.status(ServiceHandle(handle))
    update = driver.update(UpdateSpec(handle=handle, config={"description": "x"}))

    assert status.state == "deprovisioned"
    assert not update.ok and update.errors == ["not_found"]
