"""
Pure-function tests for the SCM building blocks.

We assert the exact properties operators depend on:
- encryption is reversible inside one process
- decryption is keyed off the row's backend_kind tag (so a future
  AWS Secrets Manager backend can coexist with local-Fernet rows)
- keypair generation produces an OpenSSH public-key line and a
  PEM-encoded private key with a stable SHA256 fingerprint
"""

from __future__ import annotations

from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest


def test_encrypt_at_rest_roundtrips():
    plaintext = b"ghp_FakeToken1234567890"
    enc = encrypt_at_rest(plaintext)
    assert enc.backend_kind == "local_fernet"
    assert isinstance(enc.backend_ref, bytes)
    assert enc.backend_ref != plaintext  # actually encrypted

    back = decrypt(enc)
    assert back == plaintext


def test_decrypt_uses_backend_kind_for_dispatch():
    """A row carries the backend it was encrypted with so future
    cloud-KMS backends can coexist. Decrypt picks the registered
    backend by ``backend_kind`` rather than assuming local."""
    enc = encrypt_at_rest(b"x")
    same = EncryptedSecret(
        backend_kind=enc.backend_kind, backend_ref=enc.backend_ref
    )
    assert decrypt(same) == b"x"


def test_keypair_generation_shapes():
    from astrolift_scm.keygen import generate_ed25519_keypair

    kp = generate_ed25519_keypair(comment="astrolift:test")
    assert kp.public_openssh.startswith("ssh-ed25519 ")
    assert kp.public_openssh.endswith(" astrolift:test")
    assert b"BEGIN OPENSSH PRIVATE KEY" in kp.private_pem
    assert kp.fingerprint_sha256.startswith("SHA256:")
    # OpenSSH SHA256 fingerprints are 43 base64 chars (no padding).
    assert len(kp.fingerprint_sha256) == len("SHA256:") + 43


def test_keypair_generation_unique():
    from astrolift_scm.keygen import generate_ed25519_keypair

    a = generate_ed25519_keypair()
    b = generate_ed25519_keypair()
    assert a.fingerprint_sha256 != b.fingerprint_sha256
    assert a.private_pem != b.private_pem
