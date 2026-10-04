"""Persisted ledger boundaries are strict and require no credential discovery."""

import copy
from dataclasses import replace

import pytest

from gcp.gke_identity_preparation import GKEPreparationLedger, PreparedObject, preparation_target_sha256
from gcp.gke_preparation_codec import preparation_ledger_from_payload, preparation_ledger_payload
from tests.gcp.test_gke_identity_observation_2278 import CONTEXT, SUBJECT


def ledger():
    return GKEPreparationLedger(
        preparation_target_sha256(CONTEXT),
        (PreparedObject("Namespace", "/api/v1/namespaces/" + SUBJECT.namespace, SUBJECT.namespace_uid, "opaque:rv-A"),),
    )


def test_metadata_roundtrip_retains_exact_opaque_resource_version_without_adc(monkeypatch):
    monkeypatch.setattr("google.auth.default", lambda **kwargs: pytest.fail("metadata parsing must not discover ADC"))
    value = ledger()
    parsed = preparation_ledger_from_payload(preparation_ledger_payload(value), context=CONTEXT)
    assert parsed == value and parsed.sha256 == value.sha256


@pytest.mark.parametrize(
    "change",
    [
        "extra",
        "bool_version",
        "foreign_target",
        "duplicate",
        "foreign_path",
        "coerced_uid",
        "oversize_rv",
        "unknown_phase",
    ],
)
def test_malformed_or_foreign_persisted_metadata_has_fixed_refusal(change):
    payload = copy.deepcopy(preparation_ledger_payload(ledger()))
    if change == "extra":
        payload["extra"] = "not accepted"
    elif change == "bool_version":
        payload["schema_version"] = True
    elif change == "foreign_target":
        payload["target_sha256"] = "e" * 64
    elif change == "duplicate":
        payload["objects"] *= 2
    elif change == "foreign_path":
        payload["objects"][0]["path"] = "/api/v1/namespaces/other"
    elif change == "coerced_uid":
        payload["objects"][0]["uid"] = 123
    elif change == "oversize_rv":
        payload["objects"][0]["resource_version"] = "x" * 257
    else:
        payload["pending"] = [{"phase": "UNKNOWN"}]
    with pytest.raises(ValueError, match=r"^INVALID_PREPARATION_LEDGER$"):
        preparation_ledger_from_payload(payload, context=CONTEXT)


def test_pinned_uid_substitution_refuses_without_adoption():
    payload = preparation_ledger_payload(ledger())
    other = replace(CONTEXT, subjects=(replace(SUBJECT, namespace_uid="12345678-0000-4000-8000-000000000099"),))
    payload["target_sha256"] = preparation_target_sha256(other)
    with pytest.raises(ValueError, match=r"^INVALID_PREPARATION_LEDGER$"):
        preparation_ledger_from_payload(payload, context=other)
