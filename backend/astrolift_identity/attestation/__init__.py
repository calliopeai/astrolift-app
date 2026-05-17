"""Device attestation verifiers (#496).

Two flavours: Apple's App Attest (iOS) and Google's Play Integrity
(Android). Both share the same submit-then-verify shape:

1. Client requests a nonce via ``requestAttestationChallenge``.
2. Client constructs an attestation/integrity blob over that nonce
   using the platform SDK.
3. Client submits the blob via ``attestSession`` — backend hands the
   blob + the nonce we issued to the right verifier.
4. Verifier returns either a successful ``VerifiedAttestation`` /
   ``VerifiedIntegrity`` or raises :class:`AttestationError` with a
   reason code the mutation surfaces in the audit row.

The two verifiers don't share much code — App Attest is a CBOR /
x.509 / COSE ceremony done locally against Apple's root cert; Play
Integrity hands the token to Google's REST API and trusts the
response. Both are in this package so the schema layer has one
import root.
"""

from __future__ import annotations

import dataclasses


class AttestationError(Exception):
    """A verification ceremony rejected the supplied artefact.

    ``reason_code`` is the stable tag the audit row + the
    ``MutationResult`` envelope carries; ``message`` is a human-
    readable expansion.
    """

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.message = message


@dataclasses.dataclass(slots=True, frozen=True)
class VerifiedAttestation:
    """Successful iOS App Attest verification.

    ``public_key_pem`` is the attested key the device will use to
    sign subsequent assertions; it's persisted on the session row so
    later ``assertSession`` calls can verify without re-running the
    full attestation. ``counter`` is the App Attest signed counter at
    attestation time (always 0 per the spec; surfaced anyway so the
    assertion verifier has the baseline). ``receipt`` is Apple's
    opaque receipt blob used for an optional revocation re-check.
    """

    key_id: str
    public_key_pem: str
    counter: int
    receipt: bytes
    rp_id_hash: bytes
    raw_authenticator_data: bytes


@dataclasses.dataclass(slots=True, frozen=True)
class VerifiedAssertion:
    """Successful iOS App Attest assertion (per-call re-check).

    ``counter`` is the post-increment counter that the caller must
    persist back to the session row before the next assertion arrives.
    """

    counter: int


@dataclasses.dataclass(slots=True, frozen=True)
class VerifiedIntegrity:
    """Successful Android Play Integrity verification.

    ``package_name`` echoes the bundle the device claimed in the
    token — verified against the per-install
    ``ANDROID_PACKAGE_NAME``. ``device_verdict`` and ``app_verdict``
    are the Google-issued trust labels; surfaced verbatim into the
    session's ``attestation_payload`` for forensics.
    """

    package_name: str
    device_verdict: tuple[str, ...]
    app_verdict: str
    account_verdict: str
    raw: dict


__all__ = [
    "AttestationError",
    "VerifiedAssertion",
    "VerifiedAttestation",
    "VerifiedIntegrity",
]
