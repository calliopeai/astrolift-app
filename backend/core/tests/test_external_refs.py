"""Tests for BYO external secret references (#150, spec 12 §6.5)."""

from __future__ import annotations

import pytest

from core.secrets.external_refs import (
    SUPPORTED_SCHEMES,
    ExternalSecretRef,
    ExternalSecretRefError,
    ExternalSecretResolutionError,
    parse_secret_ref,
    register_resolver,
    resolve,
    unregister_resolver,
)


# ---- parser -----------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (
            "vault://kv/data/db/passwords#pg-prod",
            ExternalSecretRef(scheme="vault", path="kv/data/db/passwords", key="pg-prod"),
        ),
        (
            "aws-sm://prod/db-password",
            ExternalSecretRef(scheme="aws-sm", path="prod/db-password", key=""),
        ),
        (
            "gcp-sm://projects/foo/secrets/bar/versions/latest",
            ExternalSecretRef(
                scheme="gcp-sm",
                path="projects/foo/secrets/bar/versions/latest",
                key="",
            ),
        ),
        (
            "azure-kv://my-vault.vault.azure.net/secrets/db-pwd",
            ExternalSecretRef(
                scheme="azure-kv",
                path="my-vault.vault.azure.net/secrets/db-pwd",
                key="",
            ),
        ),
    ],
)
def test_parse_well_formed_refs(value, expected):
    assert parse_secret_ref(value) == expected


def test_parse_normalises_scheme_to_lowercase():
    """Manifest authors might write VAULT://; accept it."""
    out = parse_secret_ref("VAULT://kv/foo")
    assert out.scheme == "vault"


def test_parse_rejects_empty():
    with pytest.raises(ExternalSecretRefError):
        parse_secret_ref("")


def test_parse_rejects_non_string():
    with pytest.raises(ExternalSecretRefError):
        parse_secret_ref(None)  # type: ignore[arg-type]


def test_parse_rejects_missing_scheme():
    with pytest.raises(ExternalSecretRefError, match="missing scheme"):
        parse_secret_ref("just/a/path")


def test_parse_rejects_unknown_scheme():
    """Strict: ``http://`` would turn this into an SSRF vector."""
    with pytest.raises(ExternalSecretRefError, match="not supported"):
        parse_secret_ref("http://example.com/secret")


def test_parse_rejects_missing_path():
    with pytest.raises(ExternalSecretRefError, match="missing path"):
        parse_secret_ref("vault://")


def test_parse_strips_leading_slash():
    """``aws-sm:///prod/foo`` and ``aws-sm://prod/foo`` resolve the
    same — urlparse splits ``netloc`` and ``path`` differently across
    these forms, and we don't want one typo turning into a different
    secret."""
    a = parse_secret_ref("aws-sm:///prod/foo")
    b = parse_secret_ref("aws-sm://prod/foo")
    assert a.path == b.path == "prod/foo"


def test_supported_schemes_locked():
    """Adding a scheme should be a deliberate change — this test
    pins the current set so a silent expansion is caught in review."""
    assert SUPPORTED_SCHEMES == frozenset(
        {"vault", "aws-sm", "aws-ssm", "gcp-sm", "azure-kv"}
    )


def test_str_round_trip():
    ref = parse_secret_ref("vault://kv/foo#bar")
    assert str(ref) == "vault://kv/foo#bar"
    ref2 = parse_secret_ref("aws-sm://prod/x")
    assert str(ref2) == "aws-sm://prod/x"


# ---- resolver registry -----------------------------------------------


@pytest.fixture(autouse=True)
def _clean_resolvers():
    """Each test starts with no registered resolvers."""
    for s in SUPPORTED_SCHEMES:
        unregister_resolver(s)
    yield
    for s in SUPPORTED_SCHEMES:
        unregister_resolver(s)


def test_resolve_dispatches_to_registered_resolver():
    seen = {}

    def fake_vault(ref):
        seen["ref"] = ref
        return b"hunter2"

    register_resolver("vault", fake_vault)
    out = resolve(parse_secret_ref("vault://kv/foo#bar"))
    assert out == b"hunter2"
    assert seen["ref"].key == "bar"


def test_resolve_unregistered_scheme_fails_closed():
    """A deploy must NOT proceed when the resolver isn't loaded —
    silent fall-back to empty bytes would mint a workload with no
    real DB password and start emitting 5xx in prod."""
    with pytest.raises(ExternalSecretResolutionError, match="no resolver"):
        resolve(parse_secret_ref("vault://kv/foo"))


def test_resolve_wraps_resolver_exception():
    def bad_resolver(ref):
        raise RuntimeError("vault sealed")

    register_resolver("vault", bad_resolver)
    with pytest.raises(ExternalSecretResolutionError, match="vault sealed"):
        resolve(parse_secret_ref("vault://kv/foo"))


def test_resolve_passes_through_resolution_error():
    def bad_resolver(ref):
        raise ExternalSecretResolutionError("custom failure shape")

    register_resolver("vault", bad_resolver)
    with pytest.raises(ExternalSecretResolutionError, match="custom failure"):
        resolve(parse_secret_ref("vault://kv/foo"))


def test_resolve_rejects_non_bytes_return():
    """A resolver that returned a str would silently slip through to
    the workload secret writer. Force bytes; wrong-type is a bug."""

    def str_resolver(ref):
        return "not bytes"

    register_resolver("vault", str_resolver)
    with pytest.raises(ExternalSecretResolutionError, match="non-bytes"):
        resolve(parse_secret_ref("vault://kv/foo"))


def test_resolve_accepts_bytearray_and_normalises_to_bytes():
    def ba_resolver(ref):
        return bytearray(b"abc")

    register_resolver("vault", ba_resolver)
    out = resolve(parse_secret_ref("vault://kv/foo"))
    assert out == b"abc"
    assert isinstance(out, bytes)


def test_register_rejects_unsupported_scheme():
    with pytest.raises(ExternalSecretRefError):
        register_resolver("http", lambda ref: b"")
