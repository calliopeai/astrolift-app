"""Tests for envelope encryption (#29 part, spec 12 §10)."""

from __future__ import annotations

import pytest

cryptography = pytest.importorskip("cryptography")

from cryptography.exceptions import InvalidTag  # noqa: E402

from core.secrets.envelope import (
    DEK_BYTES,
    NONCE_BYTES,
    EnvelopeCiphertext,
    KekUnavailable,
    decrypt,
    encrypt,
    register_kek_provider,
    rewrap,
    unregister_kek_provider,
)

# ---- in-memory KEK stub ---------------------------------------------


class _StubKMS:
    """In-memory KEK that XORs with a fixed key — fine for tests,
    obviously not real crypto. The on-the-wire shape is what we're
    actually testing."""

    def __init__(self, kek_value: bytes):
        self.kek_value = kek_value
        self.calls: list[str] = []

    def __call__(self, *, operation, kek_id, **kw):
        self.calls.append(operation)
        if operation == "wrap":
            dek = kw["plaintext_dek"]
            return bytes(d ^ self.kek_value[i % len(self.kek_value)]
                         for i, d in enumerate(dek))
        if operation == "unwrap":
            wrapped = kw["wrapped_dek_bytes"]
            return bytes(d ^ self.kek_value[i % len(self.kek_value)]
                         for i, d in enumerate(wrapped))
        raise ValueError(operation)


@pytest.fixture
def kms_a():
    stub = _StubKMS(b"\x42" * 32)
    register_kek_provider(kek_id="kek-a", provider=stub)
    yield stub
    unregister_kek_provider("kek-a")


@pytest.fixture
def kms_b():
    stub = _StubKMS(b"\x99" * 32)
    register_kek_provider(kek_id="kek-b", provider=stub)
    yield stub
    unregister_kek_provider("kek-b")


# ---- encrypt / decrypt ----------------------------------------------


def test_encrypt_returns_envelope_with_correct_shape(kms_a):
    out = encrypt(plaintext=b"super-secret-token", kek_id="kek-a")
    assert isinstance(out, EnvelopeCiphertext)
    assert out.wrapped_dek.kek_id == "kek-a"
    assert len(out.nonce) == NONCE_BYTES
    assert len(out.wrapped_dek.ciphertext) == DEK_BYTES


def test_decrypt_round_trip(kms_a):
    plaintext = b"the database password"
    envelope = encrypt(plaintext=plaintext, kek_id="kek-a")
    assert decrypt(envelope) == plaintext


def test_decrypt_with_aad_round_trip(kms_a):
    plaintext = b"row-bound secret"
    aad = b"users:api_key:42"
    envelope = encrypt(plaintext=plaintext, kek_id="kek-a", aad=aad)
    assert decrypt(envelope, aad=aad) == plaintext


def test_decrypt_with_wrong_aad_fails(kms_a):
    """AAD mismatch must fail — that's the whole point of binding
    ciphertext to row identity."""
    envelope = encrypt(plaintext=b"x", kek_id="kek-a", aad=b"users:api_key:42")
    with pytest.raises(InvalidTag):
        decrypt(envelope, aad=b"users:api_key:99")


def test_encrypt_uses_fresh_dek_per_call(kms_a):
    """Two encrypts of the same plaintext produce different
    ciphertexts (fresh DEK + fresh nonce). Captured ciphertext can't
    be replayed for an attacker who learns one row."""
    a = encrypt(plaintext=b"same", kek_id="kek-a")
    b = encrypt(plaintext=b"same", kek_id="kek-a")
    assert a.ciphertext != b.ciphertext
    assert a.wrapped_dek.ciphertext != b.wrapped_dek.ciphertext


def test_encrypt_rejects_non_bytes(kms_a):
    with pytest.raises(TypeError):
        encrypt(plaintext="not bytes", kek_id="kek-a")  # type: ignore[arg-type]


# ---- KEK provider registry ------------------------------------------


def test_encrypt_unavailable_kek_raises():
    """Fail closed when no KMS is registered — silently storing
    plaintext would defeat the whole point."""
    with pytest.raises(KekUnavailable, match="no KEK provider"):
        encrypt(plaintext=b"x", kek_id="missing-kek")


def test_register_rejects_empty_kek_id():
    with pytest.raises(ValueError):
        register_kek_provider(kek_id="", provider=lambda **_: b"")


def test_decrypt_unavailable_kek_raises(kms_a):
    """Encrypt with kek-a, then unregister it before decrypt — must
    fail rather than silently return junk."""
    envelope = encrypt(plaintext=b"x", kek_id="kek-a")
    unregister_kek_provider("kek-a")
    with pytest.raises(KekUnavailable):
        decrypt(envelope)
    # Re-register for fixture teardown
    register_kek_provider(kek_id="kek-a", provider=_StubKMS(b"\x42" * 32))


# ---- rewrap (KEK rotation) -------------------------------------------


def test_rewrap_changes_wrapped_dek_only(kms_a, kms_b):
    """KEK rotation only re-wraps the DEK — the (large) ciphertext
    stays. That's what makes rotation cheap."""
    plaintext = b"unchanged through rotation"
    envelope = encrypt(plaintext=plaintext, kek_id="kek-a")

    rotated = rewrap(envelope, new_kek_id="kek-b")
    assert rotated.wrapped_dek.kek_id == "kek-b"
    # Ciphertext + nonce unchanged
    assert rotated.ciphertext == envelope.ciphertext
    assert rotated.nonce == envelope.nonce
    # Wrapped DEK bytes differ (KEK changed)
    assert rotated.wrapped_dek.ciphertext != envelope.wrapped_dek.ciphertext

    # And the new envelope still decrypts back to the original
    assert decrypt(rotated) == plaintext


def test_wire_size_sums_components(kms_a):
    envelope = encrypt(plaintext=b"hello", kek_id="kek-a")
    expected = (
        len(envelope.wrapped_dek.ciphertext)
        + len(envelope.nonce)
        + len(envelope.ciphertext)
    )
    assert envelope.wire_size == expected
