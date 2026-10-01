"""Naming rejects unknown identities/invalid targets before any provider effects."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from aws._naming import managed_service_name
from tests.aws.test_managed_dynamodb import _spec as ddb_spec
from tests.aws.test_managed_live_ownership_2098 import MSID
from tests.aws.test_managed_live_ownership_2098 import service as _service_fixture
from tests.aws.test_managed_object_store_s3 import _spec as s3_spec
from tests.aws.test_managed_queue_sqs import _spec as sqs_spec

service = _service_fixture


def _spec(service, **kwargs):
    return {"s3": s3_spec, "sqs": sqs_spec, "dynamodb": ddb_spec}[service.kind](**kwargs)


@pytest.mark.parametrize(
    "identity",
    [
        "",
        None,
        "svc-a",
        "1",
        "00000000-0000-0000-0000-000000000000",
        "11111111111141118111111111111111",
        "ABCDEFAB-ABCD-4ABC-8ABC-ABCDEFABCDEF",
    ],
)
@pytest.mark.parametrize("recorded", [False, True])
def test_provision_never_generates_or_guesses_an_unknown_source_identity(service, identity, recorded):
    result = service.driver.provision(
        _spec(service, managed_service_id=identity, recorded_handle=service.handle if recorded else "")
    )
    assert not result.ok and result.errors == ["invalid_resource_identity"]
    assert service.calls == []


@pytest.mark.parametrize(
    "target", ["foreign_kind/legal-name", "invalid-handle", "queue/https://target.invalid/resource"]
)
def test_recorded_handle_cannot_redirect_to_a_different_kind_or_free_url(service, target):
    result = service.driver.provision(_spec(service, recorded_handle=target))
    assert not result.ok and result.errors == ["invalid_resource_identity"]
    assert service.calls == []


def test_same_source_id_is_stable_across_tenant_slug_and_hint_changes(service):
    first = service.driver.provision(_spec(service))
    second = service.driver.provision(
        _spec(
            service,
            organization_slug="different-org",
            app_slug="different-app",
            environment_name="different-env",
            service_handle_hint="different-name",
        )
    )
    assert first.ok and second.ok and first.handle == second.handle == service.handle
    assert MSID.replace("-", "") in first.handle


@pytest.mark.parametrize("service", ["sqs"], indirect=True)
def test_recorded_queue_type_outweighs_changed_operator_default_without_renaming(service):
    service.driver._config = dataclasses.replace(service.driver._config, fifo_default=True)
    result = service.driver.provision(_spec(service, recorded_handle=service.handle))
    assert result.ok and result.handle == service.handle and not result.handle.endswith(".fifo")
    service.calls.clear()
    refusal = service.driver.provision(_spec(service, recorded_handle=service.handle, config={"fifo": True}))
    assert not refusal.ok and "immutable" in refusal.message and service.calls == []


@pytest.mark.parametrize("service", ["sqs"], indirect=True)
def test_new_fifo_keeps_complete_guid_inside_the_80_character_limit(service):
    service.driver._config = dataclasses.replace(service.driver._config, queue_name_prefix="P" * 500)
    result = service.driver.provision(_spec(service, config={"fifo": True}))
    assert result.ok
    name = result.handle.split("/", 1)[1]
    assert len(name) == 80 and name.endswith(MSID.replace("-", "") + ".fifo")


@pytest.mark.parametrize("service", ["s3", "sqs", "dynamodb"], indirect=True)
def test_invalid_recorded_length_is_not_silently_truncated_or_replaced(service):
    kind = service.handle.split("/", 1)[0]
    limit = {"s3": 63, "sqs": 80, "dynamodb": 255}[service.kind]
    result = service.driver.provision(_spec(service, recorded_handle=f"{kind}/" + "a" * (limit + 1)))
    assert not result.ok and result.errors == ["invalid_resource_identity"] and service.calls == []


@pytest.mark.parametrize("prefix", ["sthree", "amzn-s3-demo"])
@pytest.mark.parametrize("service", ["s3"], indirect=True)
def test_reserved_s3_prefix_refuses_configuration_instead_of_issuing_a_bad_create(service, prefix):
    service.driver._config = dataclasses.replace(service.driver._config, bucket_name_prefix=prefix)
    result = service.driver.provision(_spec(service))
    assert not result.ok and result.errors == ["invalid_resource_identity"] and service.calls == []


def test_impossible_name_budget_cannot_truncate_the_identity():
    with pytest.raises(ValueError, match="complete"):
        managed_service_name(MSID, prefix="astrolift", max_len=32)


@pytest.mark.parametrize("case", ["legacy-slugs", "missing-marker", "foreign"])
def test_preexisting_target_does_not_downgrade_live_identity_to_human_slugs(service, case):
    tags = {"astrolift.io/managed-by": "platform", "astrolift.io/managed_service_id": MSID}
    if case == "legacy-slugs":
        tags = {"astrolift.io/managed-by": "platform", "astrolift.io/organization": "acme", "astrolift.io/app": "api"}
    elif case == "missing-marker":
        tags.pop("astrolift.io/managed-by")
    else:
        tags["astrolift.io/managed_service_id"] = "22222222-2222-4222-8222-222222222222"
    service.replace(tags)
    service.calls.clear()
    result = service.driver.provision(_spec(service, recorded_handle=service.handle, recorded_handle_exclusive=True))
    assert not result.ok and result.errors == ["ownership_refused"]
    allowed = {
        "HeadBucket",
        "GetBucketTagging",
        "GetQueueUrl",
        "ListQueueTags",
        "GetQueueAttributes",
        "describe_table",
        "list_tags_of_resource",
    }
    assert all((call[0] if isinstance(call, tuple) else call) in allowed for call in service.calls)


def test_unavailable_provision_lookup_cannot_be_downgraded_to_absence(service):
    lookup = {"s3": "head_bucket", "sqs": "get_queue_url", "dynamodb": "describe_table"}[service.kind]
    error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "NoSuchBucket ResourceNotFoundException"}}, "Lookup"
    )
    with patch.object(service.api, lookup, side_effect=error):
        result = service.driver.provision(_spec(service))
    assert not result.ok and result.errors == ["ownership_unknown"] and service.calls == []


@pytest.mark.parametrize("service", ["sqs"], indirect=True)
def test_existing_queue_exception_rechecks_current_owner_before_policy_attributes_or_retag(service):
    # Controlled external retag after initial ownership proof, while the actual
    # SQS SDK rejects a conflicting create. No assertion of atomic cloud writes.
    foreign = "22222222-2222-4222-8222-222222222222"
    fired = False

    def changed_before_create(**_):
        nonlocal fired
        if not fired:
            fired = True
            service.replace({"astrolift.io/managed-by": "platform", "astrolift.io/managed_service_id": foreign})

    service.api.meta.events.register("before-call.sqs.CreateQueue", changed_before_create)
    result = service.driver.provision(_spec(service, config={"visibility_timeout_seconds": 123}))
    assert fired and not result.ok and "ownership" in result.message
    assert "SetQueueAttributes" not in service.calls
    assert service.calls.count("TagQueue") == 1  # only the controlled external writer
    url = service.api.get_queue_url(QueueName=service.name)["QueueUrl"]
    assert service.api.list_queue_tags(QueueUrl=url)["Tags"]["astrolift.io/managed_service_id"] == foreign
