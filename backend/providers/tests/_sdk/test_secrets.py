from __future__ import annotations

import pytest

from _sdk.secrets import SecretReferenceError, resolve_secret_reference


class _Secrets:
    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.reads: list[str] = []

    def get(self, path: str):
        self.reads.append(path)
        return self.payloads.get(path)


def test_field_selector_is_not_sent_to_the_backend() -> None:
    backend = _Secrets({"managed/object": {"accessKey": "access", "secretKey": "secret"}})

    value = resolve_secret_reference(backend, "managed/object#secretKey")

    assert value == "secret"
    assert backend.reads == ["managed/object"]


def test_missing_selected_field_fails_closed() -> None:
    backend = _Secrets({"managed/object": {"accessKey": "access"}})

    assert resolve_secret_reference(backend, "managed/object#secretKey") is None


def test_default_key_supports_conventional_single_value_refs() -> None:
    backend = _Secrets({"agent/token": {"value": "token"}})

    assert resolve_secret_reference(backend, "agent/token", default_key="value") == "token"


def test_single_value_bundle_is_portable_without_a_selector() -> None:
    backend = _Secrets({"managed/password": {"password": "secret"}})

    assert resolve_secret_reference(backend, "managed/password") == "secret"


def test_multi_value_bundle_requires_an_explicit_selector() -> None:
    backend = _Secrets({"managed/object": {"accessKey": "access", "secretKey": "secret"}})

    with pytest.raises(SecretReferenceError, match="multiple fields"):
        resolve_secret_reference(backend, "managed/object")


@pytest.mark.parametrize("reference", ["", "#field", "path#", "path#field#extra"])
def test_malformed_reference_is_rejected(reference: str) -> None:
    with pytest.raises(SecretReferenceError):
        resolve_secret_reference(_Secrets({}), reference)


def test_plain_string_backend_payload_is_supported() -> None:
    backend = _Secrets({"legacy/token": "token"})

    assert resolve_secret_reference(backend, "legacy/token") == "token"


def test_plain_string_backend_rejects_field_selector() -> None:
    backend = _Secrets({"legacy/token": "token"})

    with pytest.raises(SecretReferenceError, match="scalar"):
        resolve_secret_reference(backend, "legacy/token#field")
