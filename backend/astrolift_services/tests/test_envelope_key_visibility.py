"""``revealManagedServiceConnection``'s secret/public split (#1401).

The reveal mutation walks ``envelope_keys_for(kind)`` and marks each key
``is_secret=not _is_envelope_key_public(key)``, which is what decides whether
the UI masks it. The classifier is suffix-driven, so widening an envelope with
a key whose suffix is not on the public list silently starts masking a value
that is not a credential.

These pin both directions for the keys #1401 added, and for the credential
shapes that must never flip to public.
"""

from __future__ import annotations

import pytest

from astrolift_manifest.env_injection import envelope_keys_for
from astrolift_services.schema.mutations.helpers import _is_envelope_key_public


@pytest.mark.parametrize(
    "key",
    [
        "EMAIL_FROM_ADDRESS",
        "EMAIL_REGION",
        "ENCRYPTION_KEY_SPEC",
        "ENCRYPTION_KEY_USAGE",
        "ENCRYPTION_KEY_MULTI_REGION",
        "MQ_AUTH_STRATEGY",
        "WORKFLOW_ENGINE_TYPE",
        "PRIVATE_ENDPOINT_DNS_NAMES",
        "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS",
        "PRIVATE_ENDPOINT_SERVICE_NAME",
        "PRIVATE_ENDPOINT_TYPE",
    ],
)
def test_keys_added_by_1401_reveal_unmasked(key: str) -> None:
    """None of them is a credential: a sender address, a KMS key spec and
    usage, a broker auth strategy, two kind discriminators, and the private
    endpoint's DNS-name and NIC-id arrays."""
    assert _is_envelope_key_public(key), f"{key} is configuration, not a credential"


@pytest.mark.parametrize(
    "key",
    [
        "POSTGRES_PASSWORD",
        "MYSQL_USER",
        "DOCDB_PASSWORD",
        "SEARCH_PASSWORD",
        "VECTOR_API_KEY",
        "EMAIL_API_KEY",
        "REDIS_URL",
        "DATABASE_URL",
    ],
)
def test_credential_shaped_keys_stay_masked(key: str) -> None:
    """The suffix allow-list must never grow past a credential."""
    assert not _is_envelope_key_public(key), f"{key} carries a credential and must stay masked"


def test_every_key_the_1401_envelopes_publish_is_classified_deliberately() -> None:
    """The five widened envelopes, end to end, so a later addition to any of
    them has to make the secret/public call rather than default to masked."""
    expected: dict[str, dict[str, bool]] = {
        "email": {
            "EMAIL_PROVIDER": True,
            "EMAIL_API_KEY": False,
            "EMAIL_DOMAIN": True,
            "EMAIL_FROM": True,
            "EMAIL_FROM_ADDRESS": True,
            "EMAIL_REGION": True,
        },
        "encryption_key": {
            "ENCRYPTION_KEY_ID": False,
            "ENCRYPTION_KEY_ARN": False,
            "ENCRYPTION_KEY_ALIAS": False,
            "ENCRYPTION_KEY_SPEC": True,
            "ENCRYPTION_KEY_USAGE": True,
            "ENCRYPTION_KEY_MULTI_REGION": True,
        },
        "workflow_engine": {
            "WORKFLOW_ENGINE_ID": False,
            "WORKFLOW_ENGINE_ARN": False,
            "WORKFLOW_ENGINE_REGION": True,
            "WORKFLOW_ENGINE_TYPE": True,
        },
        "private_endpoint": {
            "PRIVATE_ENDPOINT_ID": False,
            "PRIVATE_ENDPOINT_DNS": False,
            "PRIVATE_ENDPOINT_IPS": False,
            "PRIVATE_ENDPOINT_DNS_NAMES": True,
            "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS": True,
            "PRIVATE_ENDPOINT_SERVICE_NAME": True,
            "PRIVATE_ENDPOINT_TYPE": True,
        },
    }
    for kind, classification in expected.items():
        assert set(envelope_keys_for(kind)) == set(
            classification
        ), f"{kind} envelope changed; decide whether the new keys are public"
        actual = {key: _is_envelope_key_public(key) for key in classification}
        assert actual == classification, f"{kind} reveal visibility changed"
