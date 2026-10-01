"""Persisted source GUIDs reach live queue/table update, readiness and retained teardown."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from aws.managed._base import ManagedServiceError
from botocore.exceptions import ClientError

from astrolift_workflows.activities.managed_service_lifecycle import (
    _check_ready_sync,
    _deprovision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_aws_live_ownership_2098 import world  # noqa: F401 - actual PG/SDK fixture

pytestmark = pytest.mark.django_db
FOREIGN = "22222222-2222-4222-8222-222222222222"


def _replace_owner(context):
    tags = {"astrolift.io/managed-by": "platform", "astrolift.io/managed_service_id": FOREIGN}
    if context.variant == "sqs":
        url = context.api.get_queue_url(QueueName=context.name)["QueueUrl"]
        context.api.tag_queue(QueueUrl=url, Tags=tags)
    else:
        context.api.tables[context.name]["Tags"] = [
            {"Key": key, "Value": value} for key, value in tags.items()
        ]


def _only_reads(context):
    allowed = {
        "GetQueueUrl",
        "ListQueueTags",
        "GetQueueAttributes",
        "describe_table",
        "list_tags_of_resource",
    }
    assert all((call[0] if isinstance(call, tuple) else call) in allowed for call in context.calls)


@pytest.mark.parametrize("world", ["sqs", "dynamodb"], indirect=True)
@pytest.mark.parametrize("operation", ["update", "noop", "status", "destroy", "retained"])
def test_actual_lifecycle_refuses_foreign_incarnation_and_cannot_forge_owner_in_config(
    world,  # noqa: F811 - imported pytest fixture
    operation,
):
    _replace_owner(world)
    config = {"managed_service_id": FOREIGN, "recorded_handle_exclusive": True}
    if operation == "update":
        config.update(
            {"visibility_timeout_seconds": 60} if world.variant == "sqs" else {"deletion_protection": False}
        )
    world.svc.config = config
    world.svc.save(update_fields=["config"])
    world.calls.clear()
    if operation in {"update", "noop"}:
        result = _update_sync(world.svc.pk)
        assert not result["ok"] and result["errors"] == ["ownership_refused"]
    elif operation == "status":
        assert _check_ready_sync(world.svc.pk, world.svc.backend_ref) == "error"
    elif operation == "destroy":
        result = _deprovision_sync(world.svc.pk, True, True)
        assert not result["ok"] and result["errors"] == ["ownership_refused"]
    else:
        with pytest.raises(ManagedServiceError):
            _deprovision_sync(world.svc.pk, False, True)
    _only_reads(world)
    world.svc.refresh_from_db()
    assert world.svc.deleted_at is None and world.svc.provider_cleanup_receipt is None
    assert "last_retained_snapshot" not in world.svc.lifecycle_policy
    assert world.svc.backend_ref.endswith(world.name)


@pytest.mark.parametrize("world", ["sqs", "dynamodb"], indirect=True)
def test_actual_current_owner_can_update_observe_and_destroy_only_its_recorded_resource(
    world,  # noqa: F811 - imported pytest fixture
):
    world.svc.config = (
        {"visibility_timeout_seconds": 61} if world.variant == "sqs" else {"deletion_protection": True}
    )
    world.svc.save(update_fields=["config"])
    assert _update_sync(world.svc.pk)["ok"]
    assert _check_ready_sync(world.svc.pk, world.svc.backend_ref) == "available"
    if world.variant == "sqs":
        url = world.api.get_queue_url(QueueName=world.name)["QueueUrl"]
        assert (
            world.api.get_queue_attributes(QueueUrl=url, AttributeNames=["VisibilityTimeout"])["Attributes"][
                "VisibilityTimeout"
            ]
            == "61"
        )
    else:
        assert world.api.tables[world.name]["DeletionProtectionEnabled"]
        # Actual data-preserving production path invokes the owner-checked snapshot
        # before the force-clear/delete; the receipt uses the returned source ARN.
        assert _deprovision_sync(world.svc.pk, False, True)["ok"]
        world.svc.refresh_from_db()
        retained = world.svc.lifecycle_policy["last_retained_snapshot"]
        assert retained["source_handle"] == world.svc.backend_ref
        assert retained["snapshot_id"] in world.api.backups[world.name]
        assert world.name not in world.api.tables
        return
    assert _deprovision_sync(world.svc.pk, True, True)["ok"]
    assert world.api.list_queues().get("QueueUrls", []) == []


@pytest.mark.parametrize("world", ["sqs", "dynamodb"], indirect=True)
def test_actual_unknown_provider_lookup_is_failure_not_already_gone(
    world,  # noqa: F811 - imported pytest fixture
):
    lookup = "get_queue_url" if world.variant == "sqs" else "describe_table"
    with patch.object(
        world.api,
        lookup,
        side_effect=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "NonExistentQueue ResourceNotFoundException"}},
            "Lookup",
        ),
    ):
        result = _deprovision_sync(world.svc.pk, True, True)
        assert not result["ok"] and result["errors"] == ["ownership_unknown"]
        assert _check_ready_sync(world.svc.pk, world.svc.backend_ref) == "error"
    assert world.calls == []


@pytest.mark.parametrize("world", ["sqs", "dynamodb"], indirect=True)
def test_actual_confirmed_missing_target_still_converges_without_provider_mutations(
    world,  # noqa: F811 - imported pytest fixture
):
    if world.variant == "sqs":
        url = world.api.get_queue_url(QueueName=world.name)["QueueUrl"]
        world.api.delete_queue(QueueUrl=url)
    else:
        world.api.delete_table(TableName=world.name)
    world.calls.clear()
    assert _deprovision_sync(world.svc.pk, True, True)["ok"]
    assert _check_ready_sync(world.svc.pk, world.svc.backend_ref) == "deprovisioned"
    _only_reads(world)
