"""
ed25519 SSH keypair generation.

Returns OpenSSH-format public key (one line, ``ssh-ed25519 ...``)
and PEM-format PKCS8 private key. The fingerprint is the standard
``SHA256:base64-no-padding`` form OpenSSH itself prints with
``ssh-keygen -lf``.

ed25519 over RSA: smaller, faster, modern, and every git host that
supports SSH supports it. Fixed key size means no operator
ergonomic choices to expose.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)


@dataclasses.dataclass(frozen=True, slots=True)
class GeneratedKeypair:
    public_openssh: str  # one-line "ssh-ed25519 AAAA... <comment>"
    private_pem: bytes  # OpenSSH-PEM encoded
    fingerprint_sha256: str  # "SHA256:abc123..."


def generate_ed25519_keypair(*, comment: str = "astrolift") -> GeneratedKeypair:
    private = Ed25519PrivateKey.generate()
    public = private.public_key()

    public_bytes = public.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    public_openssh = f"{public_bytes.decode()} {comment}"

    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # OpenSSH SHA256 fingerprint = base64-nopad of sha256(raw public key blob).
    raw_public = public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    # OpenSSH actually fingerprints the wire-format pubkey blob, not
    # the raw key. Reconstruct it: 4-byte length-prefix + b"ssh-ed25519",
    # then 4-byte length-prefix + raw_public.
    blob = (
        len(b"ssh-ed25519").to_bytes(4, "big")
        + b"ssh-ed25519"
        + len(raw_public).to_bytes(4, "big")
        + raw_public
    )
    digest = hashlib.sha256(blob).digest()
    fingerprint = "SHA256:" + base64.b64encode(digest).decode().rstrip("=")

    return GeneratedKeypair(
        public_openssh=public_openssh,
        private_pem=private_pem,
        fingerprint_sha256=fingerprint,
    )
