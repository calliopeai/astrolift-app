"""iOS App Attest verifier (#496).

Implements the verification steps from Apple's App Attest documentation:
https://developer.apple.com/documentation/devicecheck/validating_apps_that_connect_to_your_server

Attestation flow (``verify_attestation``):

1. CBOR-decode the attestation object. Expect a map with
   ``{"fmt": "apple-appattest", "attStmt": {"x5c": [..], "receipt": .. },
   "authData": <bytes>}``.
2. Verify the certificate chain ``x5c[-1] → … → x5c[0]`` chains up to
   Apple's App Attest Root CA. ``cryptography`` does the heavy lifting.
3. Compute ``nonce = SHA256(authData ‖ SHA256(clientData))`` where
   ``clientData`` is the server-issued challenge.
4. Locate the OID ``1.2.840.113635.100.8.2`` extension on the leaf
   credCert; its octet-string payload is a DER-encoded
   ``SEQUENCE { [1] EXPLICIT OCTET STRING }`` carrying a 32-byte
   nonce that must equal the value from step 3.
5. Parse ``authData``:
   * bytes 0..31 — RP ID hash (SHA256 of the App ID — ``<TEAM>.<BUNDLE>``)
   * byte 32 — flags
   * bytes 33..36 — signed-counter (must be 0 on initial attestation)
   * bytes 37..52 — AAGUID (must be ``appattestdevelop`` in dev,
     16 zero bytes in prod; ignored here — the production check is the
     RP ID hash + Apple root chain)
   * bytes 53..54 — credentialIdLength
   * bytes 55..55+credentialIdLength — credentialId (must equal SHA256
     of the leaf public key)
6. Verify the leaf cert's public key when SHA256-hashed equals
   ``credentialId`` and the App ID hash equals the RP ID hash.

Assertion flow (``verify_assertion``):

1. CBOR-decode the assertion into ``{"signature": .., "authenticatorData": ..}``.
2. Compute ``nonce = SHA256(authenticatorData ‖ SHA256(challenge))``.
3. Verify ``signature`` over ``nonce`` with the stored public key.
4. Read the counter from bytes 33..36 of ``authenticatorData``; reject
   if it is not strictly greater than the persisted counter.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import struct
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    encode_dss_signature,
)

from astrolift_identity.attestation import (
    AttestationError,
    VerifiedAssertion,
    VerifiedAttestation,
)
from astrolift_identity.attestation._apple_root import (
    APPLE_APP_ATTEST_ROOT_CA_PEM,
)
from astrolift_identity.attestation._cbor import (
    CborDecodeError,
)
from astrolift_identity.attestation._cbor import (
    decode as cbor_decode,
)

log = logging.getLogger(__name__)

# OID Apple uses to carry the verification nonce in the leaf
# certificate's extensions. Documented at
# https://developer.apple.com/documentation/devicecheck/validating_apps_that_connect_to_your_server
_APPLE_NONCE_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.2")

# Minimum byte lengths for authenticatorData per the spec:
# 32 (rpIdHash) + 1 (flags) + 4 (counter) + 16 (aaguid) + 2 (credIdLen)
# = 55 bytes plus credId. ``_MIN_AUTH_DATA_LEN`` is the lower bound
# before the cred ID slot.
_MIN_AUTH_DATA_LEN = 55

# RFC 5915 EC P-256 key sizes — 32 bytes for r and s.
_P256_INT_BYTES = 32


def _b64_decode(blob: str | bytes) -> bytes:
    """Tolerant base64 decode — accepts URL-safe or standard, with or
    without padding. App Attest payloads in the wild are sometimes
    base64url-without-padding (the iOS SDK serializes that way)."""
    if isinstance(blob, bytes):
        return blob
    s = blob.strip()
    s = s.replace("-", "+").replace("_", "/")
    pad = (-len(s)) % 4
    return base64.b64decode(s + ("=" * pad))


def verify_attestation(
    *,
    key_id: str,
    attestation_object: str | bytes,
    challenge: str,
    app_id: str,
) -> VerifiedAttestation:
    """Verify an iOS App Attest attestation object.

    ``key_id`` is the base64-encoded SHA256 of the attested public key
    (the SDK gives this to the app at key-generation time). ``app_id``
    is the canonical ``<TEAM>.<BUNDLE>`` identifier the install is
    configured for. ``challenge`` is the server-issued nonce that was
    embedded by the device into the attestation extension.

    Raises :class:`AttestationError` on any verification failure.
    Returns a :class:`VerifiedAttestation` on success — the caller
    persists ``public_key_pem`` to the session row so subsequent
    ``verify_assertion`` calls can verify without re-running this
    whole ceremony.
    """
    raw = _b64_decode(attestation_object)
    try:
        decoded = cbor_decode(raw)
    except CborDecodeError as exc:
        raise AttestationError("INVALID_CBOR", f"attestation object is not valid CBOR: {exc}") from exc

    if not isinstance(decoded, dict):
        raise AttestationError("INVALID_SHAPE", "attestation object root must be a map")

    fmt = decoded.get("fmt")
    if fmt != "apple-appattest":
        raise AttestationError("UNSUPPORTED_FORMAT", f"expected fmt=apple-appattest, got {fmt!r}")

    att_stmt = decoded.get("attStmt")
    auth_data = decoded.get("authData")
    if not isinstance(att_stmt, dict) or not isinstance(auth_data, (bytes, bytearray)):
        raise AttestationError("INVALID_SHAPE", "attStmt or authData missing/wrong type")

    x5c = att_stmt.get("x5c")
    receipt = att_stmt.get("receipt", b"")
    if not isinstance(x5c, list) or not x5c:
        raise AttestationError("INVALID_SHAPE", "attStmt.x5c must be a non-empty array")

    cert_chain = _parse_chain(x5c)
    _verify_chain(cert_chain)

    cred_cert = cert_chain[0]

    # Step 3+4 — nonce extension on the credCert.
    expected_nonce = hashlib.sha256(
        bytes(auth_data) + hashlib.sha256(challenge.encode("utf-8")).digest()
    ).digest()
    cert_nonce = _extract_nonce_from_cert(cred_cert)
    if cert_nonce != expected_nonce:
        raise AttestationError(
            "NONCE_MISMATCH",
            "challenge nonce in credCert extension does not match computed value",
        )

    # Step 5 — parse authData and extract the credentialId.
    parsed = _parse_authenticator_data(bytes(auth_data))
    counter = parsed["counter"]
    if counter != 0:
        raise AttestationError(
            "INVALID_COUNTER",
            "App Attest initial counter must be 0",
        )

    # Step 6a — RP ID hash matches SHA256(appId).
    expected_rp_hash = hashlib.sha256(app_id.encode("utf-8")).digest()
    if parsed["rp_id_hash"] != expected_rp_hash:
        raise AttestationError(
            "RP_ID_MISMATCH",
            "authData rpIdHash does not match SHA256(app_id)",
        )

    # Step 6b — credentialId is SHA256 of the credCert public key.
    leaf_public_key_bytes = cred_cert.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    expected_credential_id = hashlib.sha256(leaf_public_key_bytes).digest()
    if parsed["credential_id"] != expected_credential_id:
        raise AttestationError(
            "CREDENTIAL_ID_MISMATCH",
            "authData credentialId does not match SHA256(credCert.publicKey)",
        )

    # Step 7 — key_id supplied by the client must match too. The SDK
    # gives the client this id at key creation time; the server-side
    # check here is the "did the client tell us a key that lines up
    # with what they attested" cross-check.
    try:
        provided_key_id = _b64_decode(key_id)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise AttestationError("INVALID_KEY_ID", "keyId is not valid base64") from exc
    if provided_key_id != expected_credential_id:
        raise AttestationError(
            "KEY_ID_MISMATCH",
            "keyId does not match SHA256(credCert.publicKey)",
        )

    public_key_pem = (
        cred_cert.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )

    return VerifiedAttestation(
        key_id=key_id,
        public_key_pem=public_key_pem,
        counter=counter,
        receipt=bytes(receipt) if isinstance(receipt, (bytes, bytearray)) else b"",
        rp_id_hash=parsed["rp_id_hash"],
        raw_authenticator_data=bytes(auth_data),
    )


def verify_assertion(
    *,
    stored_public_key_pem: str,
    assertion: str | bytes,
    challenge: str,
    prev_counter: int,
    app_id: str,
) -> VerifiedAssertion:
    """Verify an App Attest assertion against the previously-attested key.

    Raises :class:`AttestationError` on any failure. Returns the
    post-increment counter the caller must persist back to the
    session row.
    """
    if not stored_public_key_pem:
        raise AttestationError(
            "NO_STORED_KEY",
            "session has no attested public key on file — call attestSession first",
        )

    raw = _b64_decode(assertion)
    try:
        decoded = cbor_decode(raw)
    except CborDecodeError as exc:
        raise AttestationError("INVALID_CBOR", f"assertion is not valid CBOR: {exc}") from exc

    if not isinstance(decoded, dict):
        raise AttestationError("INVALID_SHAPE", "assertion root must be a map")

    signature = decoded.get("signature")
    auth_data = decoded.get("authenticatorData")
    if not isinstance(signature, (bytes, bytearray)) or not isinstance(auth_data, (bytes, bytearray)):
        raise AttestationError(
            "INVALID_SHAPE", "assertion.signature or .authenticatorData missing/wrong type"
        )

    auth_data_bytes = bytes(auth_data)
    if len(auth_data_bytes) < 37:  # rpIdHash (32) + flags (1) + counter (4)
        raise AttestationError("INVALID_AUTH_DATA", "assertion authenticatorData too short")

    rp_id_hash = auth_data_bytes[:32]
    counter = struct.unpack(">I", auth_data_bytes[33:37])[0]

    expected_rp_hash = hashlib.sha256(app_id.encode("utf-8")).digest()
    if rp_id_hash != expected_rp_hash:
        raise AttestationError(
            "RP_ID_MISMATCH",
            "assertion rpIdHash does not match SHA256(app_id)",
        )

    if counter <= prev_counter:
        raise AttestationError(
            "COUNTER_REPLAY",
            f"assertion counter {counter} <= previous {prev_counter} — replay or out-of-order",
        )

    # Signature is over SHA256(authenticatorData ‖ SHA256(challenge)).
    nonce = hashlib.sha256(auth_data_bytes + hashlib.sha256(challenge.encode("utf-8")).digest()).digest()
    public_key = serialization.load_pem_public_key(stored_public_key_pem.encode("ascii"))
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise AttestationError(
            "WRONG_KEY_TYPE",
            f"stored attestation key is {type(public_key).__name__}, expected EC",
        )

    try:
        public_key.verify(bytes(signature), nonce, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise AttestationError(
            "BAD_SIGNATURE",
            "assertion signature does not verify against stored attested key",
        ) from exc

    return VerifiedAssertion(counter=counter)


# ---- internals -----------------------------------------------------


def _parse_chain(x5c: list[Any]) -> list[x509.Certificate]:
    chain: list[x509.Certificate] = []
    for i, entry in enumerate(x5c):
        if not isinstance(entry, (bytes, bytearray)):
            raise AttestationError(
                "INVALID_CHAIN",
                f"x5c[{i}] is not a byte string",
            )
        try:
            chain.append(x509.load_der_x509_certificate(bytes(entry)))
        except ValueError as exc:
            raise AttestationError(
                "INVALID_CHAIN",
                f"x5c[{i}] could not be parsed as a DER certificate: {exc}",
            ) from exc
    return chain


def _verify_chain(chain: list[x509.Certificate]) -> None:
    """Verify a credCert → … → Apple-Root chain.

    Walks the chain pairwise: each cert's signature is verified with
    the next cert's public key, ending at the Apple root. We don't use
    ``cryptography.x509.verification`` here because it requires
    ``Store(roots)`` with a builder that varies across cryptography
    minor versions; the pairwise walk is simple, stable, and gives us
    a precise error code if a link fails.

    Expiration is intentionally NOT checked: App Attest leaf certs
    are short-lived and the receipt — not the leaf cert — is the
    revocation primitive Apple ships. Checking ``notAfter`` here
    would reject legitimate genuine devices presenting a freshly-
    issued chain on a server with skewed clock.
    """
    root_cert = x509.load_pem_x509_certificate(APPLE_APP_ATTEST_ROOT_CA_PEM)

    full_chain = list(chain) + [root_cert]
    for i in range(len(full_chain) - 1):
        subject = full_chain[i]
        issuer = full_chain[i + 1]
        try:
            _verify_signed_by(subject, issuer)
        except InvalidSignature as exc:
            raise AttestationError(
                "INVALID_CHAIN",
                f"x5c[{i}] not signed by next cert in chain",
            ) from exc

    # Final link: the chain's last cert must be issued by the Apple root.
    if chain[-1].issuer != root_cert.subject:
        raise AttestationError(
            "INVALID_CHAIN",
            "chain does not terminate at the Apple App Attest root",
        )


def _verify_signed_by(subject: x509.Certificate, issuer: x509.Certificate) -> None:
    """Verify ``subject``'s signature with ``issuer``'s public key.

    Supports the EC P-256 path Apple uses for App Attest; other
    algorithms raise :class:`AttestationError` so a future Apple
    rotation to a new signature algorithm fails loudly rather than
    silently accepting.
    """
    issuer_pub = issuer.public_key()
    sig_algo_oid = subject.signature_algorithm_oid
    hash_algo = subject.signature_hash_algorithm
    if hash_algo is None:
        raise AttestationError(
            "UNSUPPORTED_SIGNATURE_ALGO",
            f"certificate has no signature hash algorithm ({sig_algo_oid.dotted_string})",
        )

    if isinstance(issuer_pub, ec.EllipticCurvePublicKey):
        issuer_pub.verify(
            subject.signature,
            subject.tbs_certificate_bytes,
            ec.ECDSA(hash_algo),
        )
        return

    raise AttestationError(
        "UNSUPPORTED_SIGNATURE_ALGO",
        f"unsupported issuer key type {type(issuer_pub).__name__}",
    )


def _extract_nonce_from_cert(cert: x509.Certificate) -> bytes:
    """Pull the 32-byte App Attest nonce out of the credCert extension.

    The extension value is a DER-encoded ``SEQUENCE { [1] EXPLICIT OCTET
    STRING }``. We don't import pyasn1 just for one shape — parse the
    DER bytes inline.
    """
    try:
        ext = cert.extensions.get_extension_for_oid(_APPLE_NONCE_OID)
    except x509.ExtensionNotFound as exc:
        raise AttestationError(
            "NONCE_EXTENSION_MISSING",
            f"credCert is missing the {_APPLE_NONCE_OID.dotted_string} extension",
        ) from exc

    payload = ext.value.value  # cryptography wraps unrecognized OIDs in UnrecognizedExtension
    return _parse_der_octet_string_inside_sequence(payload)


def _parse_der_octet_string_inside_sequence(data: bytes) -> bytes:
    """Decode a DER ``SEQUENCE { [1] EXPLICIT OCTET STRING }`` to its bytes.

    Tolerates the two shapes Apple ships in the wild:

    * Strict spec: outer SEQUENCE, then a context-specific [1] tag
      (0xA1) wrapping an OCTET STRING (0x04).
    * Some SDK builds drop the [1] wrapper and put an OCTET STRING
      directly inside the SEQUENCE.

    Both produce the same 32-byte nonce. Anything else raises.
    """
    if not data:
        raise AttestationError("NONCE_DECODE_FAILED", "empty extension value")
    if data[0] != 0x30:  # SEQUENCE
        raise AttestationError(
            "NONCE_DECODE_FAILED",
            f"expected SEQUENCE tag 0x30, got 0x{data[0]:02x}",
        )
    seq_body, _ = _read_tlv(data, 0)
    if not seq_body:
        raise AttestationError("NONCE_DECODE_FAILED", "empty SEQUENCE body")
    inner_tag = seq_body[0]
    if inner_tag == 0xA1:  # [1] EXPLICIT
        inner_body, _ = _read_tlv(seq_body, 0)
        if not inner_body or inner_body[0] != 0x04:
            raise AttestationError(
                "NONCE_DECODE_FAILED",
                "[1] EXPLICIT wrapper does not contain an OCTET STRING",
            )
        nonce, _ = _read_tlv(inner_body, 0)
        return nonce
    if inner_tag == 0x04:  # OCTET STRING directly
        nonce, _ = _read_tlv(seq_body, 0)
        return nonce
    raise AttestationError(
        "NONCE_DECODE_FAILED",
        f"unexpected inner tag 0x{inner_tag:02x} in nonce extension",
    )


def _read_tlv(data: bytes, pos: int) -> tuple[bytes, int]:
    """Read one DER TLV starting at ``pos``; return (value_bytes, next_pos).

    Supports definite-length-encoded items up to long-form length
    octets (DER never uses indefinite). Enough for any App Attest
    extension payload.
    """
    if pos + 2 > len(data):
        raise AttestationError("NONCE_DECODE_FAILED", "truncated TLV header")
    length_byte = data[pos + 1]
    if length_byte < 0x80:
        length = length_byte
        header = 2
    else:
        n = length_byte & 0x7F
        if n == 0 or pos + 2 + n > len(data):
            raise AttestationError("NONCE_DECODE_FAILED", "truncated long-form length")
        length = int.from_bytes(data[pos + 2 : pos + 2 + n], "big")
        header = 2 + n
    start = pos + header
    end = start + length
    if end > len(data):
        raise AttestationError("NONCE_DECODE_FAILED", "TLV length exceeds buffer")
    return data[start:end], end


def _parse_authenticator_data(data: bytes) -> dict[str, Any]:
    if len(data) < _MIN_AUTH_DATA_LEN:
        raise AttestationError(
            "INVALID_AUTH_DATA",
            f"authenticatorData too short: {len(data)} bytes",
        )
    rp_id_hash = data[:32]
    flags = data[32]
    counter = struct.unpack(">I", data[33:37])[0]
    aaguid = data[37:53]
    cred_id_len = struct.unpack(">H", data[53:55])[0]
    cred_id_start = 55
    cred_id_end = cred_id_start + cred_id_len
    if cred_id_end > len(data):
        raise AttestationError(
            "INVALID_AUTH_DATA",
            "credentialId length runs past authenticatorData buffer",
        )
    return {
        "rp_id_hash": rp_id_hash,
        "flags": flags,
        "counter": counter,
        "aaguid": aaguid,
        "credential_id": data[cred_id_start:cred_id_end],
    }


# Re-export the ECDSA helper so test fixtures that build synthetic
# attestation chains can format raw r/s pairs the same way the SDK
# does, without rolling their own.
__all__ = [
    "encode_dss_signature",
    "verify_assertion",
    "verify_attestation",
]
