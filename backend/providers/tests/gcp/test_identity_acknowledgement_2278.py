"""Pure bounded acknowledgments + strict preparation refusal before native transport."""

from dataclasses import replace
from uuid import uuid4

import pytest

from gcp.gke_identity_preparation import GKEPreparationError
from gcp.identity_acknowledgement import (
    AcknowledgementReceipt,
    PolicyAcknowledgement,
    PreparationAcknowledgement,
    acknowledgement_sha256,
)

from .test_gke_identity_preparation_2278 import native as native_fixture


@pytest.fixture
def native(tmp_path, monkeypatch):
    yield from native_fixture.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("strict", [True, False])
def test_strict_preparation_missing_durable_acknowledgement_port_refuses_before_native_transport(native, strict):
    driver, store, state, wire = native
    with pytest.raises(
        GKEPreparationError, match="ACKNOWLEDGEMENT_HOOK_REQUIRED" if strict else "ACKNOWLEDGEMENT_MODE_REQUIRED"
    ):
        driver.prepare(
            operation_id=str(uuid4()),
            ledger=store.ledger,
            commit_submission=store.commit,
            commit_observation=store.commit,
            checkpoint=store.checkpoint,
            strict_acknowledgements=strict,
            acknowledgement_hook=None if strict else lambda ack: None,
        )
    assert not state["requests"] and not state["effects"] and not wire.calls and not store.records


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", "not-a-native-uid"),
        ("uid", 10),
        ("submission_id", "00000000-0000-0000-0000-000000000000"),
        ("request_sha256", "A" * 64),
        ("resource_version", ""),
        ("resource_version", "v" * 257),
        ("resource_version", "rv\nprivate"),
    ],
)
def test_preparation_acknowledgements_refuse_unbounded_or_ambiguous_identity(field, value):
    valid = PreparationAcknowledgement(str(uuid4()), "a" * 64, str(uuid4()), "opaque-original-rv")
    with pytest.raises(ValueError):
        replace(valid, **{field: value})


def test_policy_acknowledgement_is_only_separate_hashes_never_policy_content():
    valid = PolicyAcknowledgement(str(uuid4()), "a" * 64, "b" * 64, "c" * 64)
    assert len(acknowledgement_sha256(valid)) == 64
    with pytest.raises(ValueError):
        replace(valid, policy_sha256={"bindings": []})
    with pytest.raises(ValueError):
        replace(valid, etag_sha256="private-raw-etag")
    with pytest.raises(ValueError):
        acknowledgement_sha256({"body": "not-a-typed-acknowledgement"})
    with pytest.raises(ValueError):
        AcknowledgementReceipt(str(uuid4()), "unconfirmed")
