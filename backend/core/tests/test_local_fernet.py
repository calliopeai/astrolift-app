"""Tests for the LocalFernet secrets backend (#13, #44)."""

from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken
from django.test import override_settings

from core.secrets.local_fernet import LocalFernetBackend


def test_round_trip():
    backend = LocalFernetBackend()
    plaintext = b"hunter2"
    ct = backend.encrypt(plaintext)
    assert ct != plaintext  # actually encrypted
    assert backend.decrypt(ct) == plaintext


def test_two_encrypts_yield_different_ciphertext():
    """Fernet uses a random IV per encrypt — same plaintext should
    not produce the same ciphertext twice. Important for not leaking
    equality between stored secrets."""
    backend = LocalFernetBackend()
    a = backend.encrypt(b"same")
    b = backend.encrypt(b"same")
    assert a != b
    assert backend.decrypt(a) == backend.decrypt(b) == b"same"


def test_key_rotates_with_secret_key():
    backend = LocalFernetBackend()
    ct = backend.encrypt(b"value")
    with override_settings(SECRET_KEY="totally-different-secret-key-please"):
        # Same backend, different SECRET_KEY → cannot decrypt the
        # earlier ciphertext.
        with pytest.raises(InvalidToken):
            LocalFernetBackend().decrypt(ct)


def test_kind_label_is_stable():
    """The backend identifier travels with each ciphertext so we can
    migrate to a different backend later without losing the ability
    to decrypt legacy rows."""
    assert LocalFernetBackend.kind == "local_fernet"
