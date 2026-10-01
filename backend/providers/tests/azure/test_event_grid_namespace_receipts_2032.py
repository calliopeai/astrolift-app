"""Local receipt invariants against actual SDK10.4 observations, without Azure."""

import json
from dataclasses import replace

import pytest

from azure._event_grid_namespace_ownership import (
    OwnershipUnknown,
    Receipts,
    Target,
    assert_namespace_owner,
    child_name,
)
from azure.mgmt.eventgrid import models
from tests.azure.test_event_grid_wire_2032 import source

SUB = "018f42f0-4420-7000-8000-000000000002"
RG = "controlled-rg"


def target(config=None, spec=None, **overrides):
    args = dict(
        subscription=SUB,
        resource_group=RG,
        namespace_prefix="astrolift-egns",
        topic_prefix="events",
        source=spec or source(),
        config=config or {},
    )
    args.update(overrides)
    return Target.new(**args)


def topic(t, *, state="Succeeded", retention=1, identity=None, name=None):
    return models.NamespaceTopic.deserialize(
        {
            "id": identity or t.topic_id,
            "name": name or t.topic,
            "properties": {
                "provisioningState": state,
                "publisherType": "Custom",
                "inputSchema": "CloudEventSchemaV1_0",
                "eventRetentionInDays": retention,
            },
            "systemData": None,
        }
    )


PARAMS = {"properties": {"publisherType": "Custom", "inputSchema": "CloudEventSchemaV1_0", "eventRetentionInDays": 1}}


def accepted(t=None):
    t = t or target()
    r = Receipts.empty(t, source())
    r.reserve(t.topic_id, PARAMS)
    persisted = Receipts.load(r.dump(), t, source())
    persisted.accept(t.topic_id, topic(t, state="Creating"), PARAMS)
    return persisted


def test_full_uuid_names_and_saved_coordinates_survive_descriptive_renames():
    spec = source()
    t = target(spec=spec)
    changed = replace(spec, app_slug="renamed", service_handle_hint="renamed-service", recorded_handle=t.handle)
    assert target(spec=changed) == t
    assert Target.saved(t.handle, subscription=SUB, resource_group=RG, source=changed) == t
    other = replace(spec, managed_service_id="018f42f0-4420-7000-8000-000000000099")
    assert target(spec=other).namespace != t.namespace
    assert target(spec=other).topic != t.topic
    assert spec.managed_service_id.replace("-", "") in t.namespace
    assert spec.managed_service_id.replace("-", "") in t.topic


@pytest.mark.parametrize(
    "identity",
    [
        "",
        "service-id",
        "00000000-0000-0000-0000-000000000000",
        "018F42F0-4420-7000-8000-000000000001",
        "018f42f0442070008000000000000001",
    ],
)
def test_missing_or_ambiguous_source_identity_refuses_before_cloud(identity):
    with pytest.raises(OwnershipUnknown):
        target(spec=replace(source(), managed_service_id=identity))


@pytest.mark.parametrize(
    "handle",
    [
        "event_bus/legacy-namespace/legacy-topic",
        "event_bus/arm-v1/foreign-sub/rg/namespace/topic",
        "event_bus/arm-v1/018f42f0-4420-7000-8000-000000000002/foreign-rg/namespace/topic",
    ],
)
def test_no_guessed_historical_or_foreign_placement(handle):
    with pytest.raises(OwnershipUnknown):
        Target.saved(handle, subscription=SUB, resource_group=RG, source=source())


@pytest.mark.parametrize("prefix", ["x" * 18, "prefix/escape", "", "-prefix"])
def test_prefix_cannot_truncate_immutable_identity(prefix):
    with pytest.raises(OwnershipUnknown):
        target(namespace_prefix=prefix)


def test_standard_child_fits_stricter_api_limit_with_full_uuid():
    name = child_name(source(), "x" * 50)
    assert len(name) == 50 and name.isalnum()
    assert source().managed_service_id.replace("-", "") in name
    assert child_name(source(), "pull-feed") == child_name(source(), "PULL-FEED")
    assert child_name(source(), "pull-feed") != child_name(source(), "push-feed")
    with pytest.raises(OwnershipUnknown):
        child_name(source(), "x" * 51)


@pytest.mark.parametrize(
    "change", ["foreign-arm", "foreign-name", "missing-source", "foreign-source", "source-alias", "platform-alias"]
)
def test_current_namespace_authority_requires_exact_arm_and_all_aliases(change):
    t = target()
    payload = {
        "id": t.namespace_id,
        "name": t.namespace,
        "tags": {"astrolift-managed-by": "platform", "astrolift-managed-service-id": source().managed_service_id},
        "location": "eastus",
    }
    if change == "foreign-arm":
        payload["id"] = t.namespace_id.replace(RG, "foreign-rg")
    elif change == "foreign-name":
        payload["name"] = "other-namespace"
    elif change == "missing-source":
        payload["tags"].pop("astrolift-managed-service-id")
    elif change == "foreign-source":
        payload["tags"]["astrolift-managed-service-id"] = "018f42f0-4420-7000-8000-000000000099"
    elif change == "source-alias":
        payload["tags"]["Astrolift.io/managed-service-id"] = "other"
    else:
        payload["tags"]["X-Astrolift-Managed-By"] = "operator"
    with pytest.raises(OwnershipUnknown):
        assert_namespace_owner(models.Namespace.deserialize(payload), t, source())


def test_current_namespace_owner_uses_actual_shared_update_verifier():
    t = target()
    n = models.Namespace.deserialize(
        {
            "id": t.namespace_id,
            "name": t.namespace,
            "tags": {"astrolift-managed-by": "platform", "astrolift-managed-service-id": source().managed_service_id},
            "location": "eastus",
        }
    )
    assert_namespace_owner(n, t, source())


@pytest.mark.parametrize(
    "raw", ["{}", '{"old-child":"Queue"}', '{"version":2,"version":2}', '{"version":NaN}', "[]", "null", '"bad"']
)
def test_legacy_malformed_or_ambiguous_registry_never_grants_authority(raw):
    with pytest.raises(OwnershipUnknown):
        Receipts.load(raw, target(), source())


def test_reserved_name_and_lost_response_cannot_authorize_existing_cloud_object():
    t = target()
    r = Receipts.empty(t, source())
    r.reserve(t.topic_id, PARAMS)
    persisted = Receipts.load(r.dump(), t, source())
    for operation in (persisted.assert_authority, persisted.observe):
        with pytest.raises(OwnershipUnknown, match="reserved-only"):
            operation(t.topic_id, topic(t))
    assert persisted.resources[t.topic_id.casefold()]["state"] == "reserved"


@pytest.mark.parametrize(
    "state,retention", [("Creating", 1), ("Updating", 1), ("Failed", 1), (None, 1), ("Succeeded", 7)]
)
def test_accepted_is_not_observed_completion(state, retention):
    r = accepted()
    assert r.observe(r.target.topic_id, topic(r.target, state=state, retention=retention)) is False
    assert r.resources[r.target.topic_id.casefold()]["state"] == "accepted"


def test_observed_completion_requires_actual_sdk_desired_fields():
    r = accepted()
    assert r.observe(r.target.topic_id, topic(r.target)) is True
    saved = Receipts.load(r.dump(), r.target, source())
    assert saved.resources[r.target.topic_id.casefold()]["state"] == "observed"
    assert saved.observe(r.target.topic_id, topic(r.target, retention=7)) is False


@pytest.mark.parametrize("phase", ["accept", "assert_authority", "observe"])
def test_mismatched_returned_arm_refuses_every_receipt_phase(phase):
    r = accepted()
    wrong = topic(r.target, identity=r.target.topic_id.replace(RG, "foreign-rg"))
    with pytest.raises(OwnershipUnknown):
        if phase == "accept":
            r.accept(r.target.topic_id, wrong, PARAMS)
        else:
            getattr(r, phase)(r.target.topic_id, wrong)


@pytest.mark.parametrize(
    "change", ["owner", "topic_id", "record-id", "record-key", "empty-fields", "state", "version-bool"]
)
def test_receipt_is_bound_to_complete_source_and_placement(change):
    r = accepted()
    payload = json.loads(r.dump())
    row = payload["resources"][r.target.topic_id.casefold()]
    if change == "owner":
        payload["owner"] = "018f42f0-4420-7000-8000-000000000099"
    elif change == "topic_id":
        payload["topic_id"] = r.target.topic_id.replace(RG, "foreign-rg")
    elif change == "record-id":
        row["id"] = r.target.topic_id.replace(RG, "foreign-rg")
    elif change == "record-key":
        payload["resources"][r.target.topic_id.upper()] = payload["resources"].pop(r.target.topic_id.casefold())
    elif change == "empty-fields":
        row["parameters"] = {}
    elif change == "state":
        row["state"] = "adopted"
    else:
        payload["version"] = True
    with pytest.raises(OwnershipUnknown):
        Receipts.load(json.dumps(payload), r.target, source())


def test_receipt_secret_names_include_full_source_and_placement_digest():
    first = Receipts.empty(target(), source())
    other = Receipts.empty(target(resource_group="other-rg"), source())
    assert first.secret_name != other.secret_name
    assert source().managed_service_id.replace("-", "") in first.secret_name
