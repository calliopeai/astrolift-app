"""
Envelope encryption (#29 part, spec 12 §10).

Two-layer encryption pattern: a per-row Data Encryption Key (DEK)
encrypts the payload with AES-GCM; the DEK itself is wrapped by a
Key Encryption Key (KEK) held in a KMS. Decrypting requires
unwrapping the DEK via KMS, then AES-GCM-decrypting the payload.

Why envelope, not 'encrypt directly with KEK':

- KMS APIs are slow + rate-limited. Per-row KMS calls would tank
  throughput on bulk reads.
- DEKs are short-lived (one per row); a leaked DEK only exposes
  one row, not the whole DB.
- KEK rotation only needs to re-wrap the (small) set of DEKs, not
  re-encrypt the (large) payloads.

Pure-Python module. The actual KMS calls (wrap / unwrap) are
driver-side — registered via ``register_kek_provider``. The
platform tags every wrapped row with the KEK's id so a future
``manage.py rotate_keks`` can re-wrap in place.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable

# AES-GCM. Defer cryptography import until first use so test code
# that doesn't actually call encrypt/decrypt doesn't need the dep.
DEK_BYTES = 32  # AES-256
NONCE_BYTES = 12  # GCM standard
GCM_TAG_BYTES = 16


@dataclasses.dataclass(frozen=True, slots=True)
class WrappedDEK:
    """A KMS-wrapped DEK. ``kek_id`` lets a rotation script find
    every row encrypted under a specific KEK and re-wrap them."""

    kek_id: str
    ciphertext: bytes
    """KMS-specific wrapped key bytes (an opaque blob from the KMS)."""


@dataclasses.dataclass(frozen=True, slots=True)
class EnvelopeCiphertext:
    """The on-row shape. Caller serializes this to JSONB or split
    columns however the table is laid out."""

    wrapped_dek: WrappedDEK
    nonce: bytes
    ciphertext: bytes  # includes GCM auth tag

    @property
    def wire_size(self) -> int:
        """Bytes on the wire (sum of components). Useful for the
        cost/usage calculation."""
        return len(self.wrapped_dek.ciphertext) + len(self.nonce) + len(self.ciphertext)


# ---- KEK provider registry ------------------------------------------


KekProvider = Callable[..., bytes]
"""Two methods rolled into one callable for simplicity:

  provider(operation='wrap', kek_id, plaintext_dek) -> wrapped_bytes
  provider(operation='unwrap', kek_id, wrapped_dek_bytes) -> dek_bytes

Real implementations call the cloud KMS (AWS KMS / GCP KMS /
Azure Key Vault / Vault Transit / k8s KMSv2 plugin). Tests use an
in-memory stub.
"""

_PROVIDERS: dict[str, KekProvider] = {}


def register_kek_provider(*, kek_id: str, provider: KekProvider) -> None:
    """Plugin registration; tests call inside fixtures."""
    if not kek_id:
        raise ValueError("kek_id is required")
    _PROVIDERS[kek_id] = provider


def unregister_kek_provider(kek_id: str) -> None:
    _PROVIDERS.pop(kek_id, None)


class KekUnavailable(Exception):
    """KMS not registered or not reachable. Caller fails the
    encrypt/decrypt rather than silently storing plaintext."""


def _provider_for(kek_id: str) -> KekProvider:
    fn = _PROVIDERS.get(kek_id)
    if fn is None:
        raise KekUnavailable(
            f"no KEK provider registered for {kek_id!r}; " f"available: {sorted(_PROVIDERS)}"
        )
    return fn


# ---- encrypt / decrypt ----------------------------------------------


def encrypt(
    *,
    plaintext: bytes,
    kek_id: str,
    aad: bytes = b"",
) -> EnvelopeCiphertext:
    """Generate a fresh DEK, wrap it via the KEK provider, AES-GCM
    encrypt the payload.

    ``aad`` is Additional Authenticated Data — bind the ciphertext
    to a specific column / row identity so a captured ciphertext
    can't be 'transplanted' to another row. Caller passes a stable
    string like ``f"{table}:{column}:{row_id}".encode()``.
    """
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes")

    provider = _provider_for(kek_id)
    dek = os.urandom(DEK_BYTES)
    wrapped = provider(operation="wrap", kek_id=kek_id, plaintext_dek=dek)

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aesgcm = AESGCM(dek)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = aesgcm.encrypt(nonce, bytes(plaintext), aad or None)

    return EnvelopeCiphertext(
        wrapped_dek=WrappedDEK(kek_id=kek_id, ciphertext=wrapped),
        nonce=nonce,
        ciphertext=ciphertext,
    )


def decrypt(envelope: EnvelopeCiphertext, *, aad: bytes = b"") -> bytes:
    """Unwrap the DEK via KMS, then AES-GCM-decrypt.

    AAD must match the value used at encrypt time; mismatched AAD
    fails the GCM auth tag check (raises ``InvalidTag``).
    """
    provider = _provider_for(envelope.wrapped_dek.kek_id)
    dek = provider(
        operation="unwrap",
        kek_id=envelope.wrapped_dek.kek_id,
        wrapped_dek_bytes=envelope.wrapped_dek.ciphertext,
    )
    if len(dek) != DEK_BYTES:
        raise KekUnavailable(f"unwrap returned {len(dek)} bytes; expected {DEK_BYTES}")

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aesgcm = AESGCM(dek)
    return aesgcm.decrypt(envelope.nonce, envelope.ciphertext, aad or None)


# ---- rotation -------------------------------------------------------


def rewrap(envelope: EnvelopeCiphertext, *, new_kek_id: str) -> EnvelopeCiphertext:
    """Re-wrap the DEK under a new KEK. The payload ciphertext is
    untouched — that's the cheap part of envelope rotation; only the
    (small) wrapped-DEK column is rewritten.

    Used by ``manage.py rotate_keks``: scans rows by old kek_id, calls
    rewrap, writes back. Old KEK stays available for decrypt-only
    until the scan completes."""
    old_provider = _provider_for(envelope.wrapped_dek.kek_id)
    new_provider = _provider_for(new_kek_id)

    dek = old_provider(
        operation="unwrap",
        kek_id=envelope.wrapped_dek.kek_id,
        wrapped_dek_bytes=envelope.wrapped_dek.ciphertext,
    )
    new_wrapped = new_provider(
        operation="wrap",
        kek_id=new_kek_id,
        plaintext_dek=dek,
    )
    return EnvelopeCiphertext(
        wrapped_dek=WrappedDEK(kek_id=new_kek_id, ciphertext=new_wrapped),
        nonce=envelope.nonce,
        ciphertext=envelope.ciphertext,
    )
