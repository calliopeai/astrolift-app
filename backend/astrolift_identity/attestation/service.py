"""Attestation service — orchestrates verifier → session → audit (#496).

The GraphQL mutations stay thin; this module owns:

* Per-install config reads (Constance + Django settings fallbacks).
* Challenge issuance + consumption.
* Calling the right verifier, persisting the outcome on the session
  row, and emitting the audit entry on both success and failure.
* The ``requires_attestation`` decision: returns the per-mutation
  gate-state given the session + the install-wide policy flags.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from astrolift_identity.attestation import (
    AttestationError,
    VerifiedAssertion,
    VerifiedAttestation,
    VerifiedIntegrity,
)
from astrolift_identity.attestation import (
    android_play_integrity as android_verifier,
)
from astrolift_identity.attestation import (
    ios_appattest as ios_verifier,
)
from astrolift_identity.models import (
    AstroliftSession,
    AttestationChallenge,
    AttestationKind,
    AttestationTrustLevel,
)
from core.mutations import AuditEntry, emit_audit

log = logging.getLogger(__name__)

# Default challenge TTL when Constance isn't yet seeded. 5 minutes is
# the spec ceiling — Apple recommends nonces stay valid no longer
# than the time it takes a user to tap "approve" on a device, which
# is well under a minute even on a slow link.
_DEFAULT_CHALLENGE_TTL_SECONDS = 300


@dataclass(slots=True, frozen=True)
class _Config:
    """Resolved per-install attestation config.

    Built once per request so the eight Constance lookups don't fan
    out into eight independent queries.
    """

    challenge_ttl_seconds: int
    ios_app_id: str
    android_package_name: str
    google_api_key: str
    require_for_mobile: bool
    require_for_sensitive_ops: bool
    require_strong_android_integrity: bool


def _read_config() -> _Config:
    """Resolve the per-install attestation config.

    Reads Constance (operator-tunable at runtime) first, falls back
    to Django settings (deploy-time defaults), then to the in-code
    fallbacks. Never raises — a missing entry yields the safer
    default (off + empty string).
    """

    def _const(name: str, default: Any) -> Any:
        try:
            from constance import config as constance_config

            return getattr(constance_config, name, default)
        except Exception:  # noqa: BLE001 — Constance must never break this
            return default

    def _coerce_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "y", "t")
        if isinstance(value, int):
            return bool(value)
        return default

    def _coerce_int(value: Any, default: int) -> int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        return default

    def _coerce_str(value: Any, default: str) -> str:
        if isinstance(value, str):
            return value
        return default

    return _Config(
        challenge_ttl_seconds=_coerce_int(
            _const("ATTESTATION_CHALLENGE_TTL_SECONDS", _DEFAULT_CHALLENGE_TTL_SECONDS),
            _DEFAULT_CHALLENGE_TTL_SECONDS,
        ),
        ios_app_id=_coerce_str(_const("IOS_APP_ID", ""), ""),
        android_package_name=_coerce_str(_const("ANDROID_PACKAGE_NAME", ""), ""),
        google_api_key=_coerce_str(_const("GOOGLE_PLAY_INTEGRITY_API_KEY", ""), ""),
        require_for_mobile=_coerce_bool(_const("REQUIRE_ATTESTATION_FOR_MOBILE", False), False),
        require_for_sensitive_ops=_coerce_bool(_const("REQUIRE_ATTESTATION_FOR_SENSITIVE_OPS", False), False),
        require_strong_android_integrity=_coerce_bool(
            _const("REQUIRE_STRONG_ANDROID_INTEGRITY", False), False
        ),
    )


# ---- challenge lifecycle ------------------------------------------


def issue_challenge(*, user, kind: str) -> AttestationChallenge:
    """Create a one-shot nonce for ``user`` + ``kind``.

    Returns the persisted row; the caller surfaces ``nonce`` +
    ``expires_at`` to the client.
    """
    kind_normalized = _normalize_kind(kind)
    cfg = _read_config()
    expires = timezone.now() + dt.timedelta(seconds=cfg.challenge_ttl_seconds)
    return AttestationChallenge.objects.create(
        user=user,
        kind=kind_normalized,
        expires_at=expires,
    )


def _consume_challenge(*, user, kind: str, nonce: str) -> AttestationChallenge:
    """Find + delete a matching challenge atomically.

    Single-use enforcement is the DB-level delete: if two concurrent
    submissions race for the same nonce, exactly one wins the
    ``delete()`` and the other gets zero rows back and raises
    ``CHALLENGE_NOT_FOUND``.
    """
    with transaction.atomic():
        row = (
            AttestationChallenge.objects.select_for_update().filter(user=user, kind=kind, nonce=nonce).first()
        )
        if row is None:
            raise AttestationError(
                "CHALLENGE_NOT_FOUND",
                "no matching challenge — already consumed, expired, or wrong kind",
            )
        if row.is_expired():
            row.delete()
            raise AttestationError(
                "CHALLENGE_EXPIRED",
                "challenge expired before submission",
            )
        # Consume the row even on success — single-use semantics.
        row.delete()
        return row


def sweep_expired_challenges() -> int:
    """Drop every expired challenge row. Returns the number removed.

    Cheap operation — called from the periodic stale-session sweeper
    so we share one schedule rather than spinning up another worker.
    """
    deleted, _ = AttestationChallenge.objects.filter(expires_at__lt=timezone.now()).delete()
    return deleted


# ---- attestation submission ---------------------------------------


def attest_session(
    *,
    session: AstroliftSession,
    kind: str,
    challenge: str,
    attestation_object: str | None = None,
    key_id: str | None = None,
    integrity_token: str | None = None,
) -> AstroliftSession:
    """Run the right verifier and persist the outcome on ``session``.

    On success: marks ``attestation_trust_level=genuine`` + stamps
    ``attestation_verified_at`` + emits an ``ALLOW`` audit row.

    On failure: marks ``attestation_trust_level=failed`` + emits a
    ``DENY`` audit row carrying the reason code and re-raises the
    :class:`AttestationError` so the mutation can surface it in the
    ``MutationResult`` envelope.
    """
    cfg = _read_config()
    kind_normalized = _normalize_kind(kind)
    _consume_challenge(user=session.user, kind=kind_normalized, nonce=challenge)

    try:
        if kind_normalized == AttestationKind.IOS_APPATTEST.value:
            verified = _verify_ios(
                attestation_object=attestation_object,
                key_id=key_id,
                challenge=challenge,
                app_id=cfg.ios_app_id,
            )
            _persist_ios_attestation(session, verified, kind_normalized)
        elif kind_normalized == AttestationKind.ANDROID_PLAY_INTEGRITY.value:
            verified = _verify_android(
                token=integrity_token,
                expected_nonce=challenge,
                cfg=cfg,
            )
            _persist_android_attestation(session, verified, kind_normalized)
        else:
            raise AttestationError(
                "UNSUPPORTED_KIND",
                f"attestation kind {kind!r} not supported",
            )
    except AttestationError as exc:
        _mark_failed(session, kind_normalized, exc)
        _audit(
            session=session,
            action="auth.attestation.failed",
            decision="DENY",
            error_code=exc.reason_code,
            error_message=exc.message,
            extra={"kind": kind_normalized},
        )
        raise

    _audit(
        session=session,
        action="auth.attestation.verified",
        decision="ALLOW",
        extra={"kind": kind_normalized},
    )
    return session


def assert_session(
    *,
    session: AstroliftSession,
    challenge: str,
    assertion: str,
) -> AstroliftSession:
    """iOS-only periodic assertion re-check.

    Verifies a signature from the device against the public key we
    stored at attestation time, bumps the counter, and stamps a fresh
    ``attestation_verified_at`` so the operator-facing list shows the
    session as recently re-confirmed.
    """
    cfg = _read_config()
    if session.attestation_kind != AttestationKind.IOS_APPATTEST.value:
        raise AttestationError(
            "WRONG_KIND",
            "assertSession is iOS-only; this session is not App-Attested",
        )
    _consume_challenge(
        user=session.user,
        kind=AttestationKind.IOS_APPATTEST.value,
        nonce=challenge,
    )

    try:
        verified = ios_verifier.verify_assertion(
            stored_public_key_pem=session.attestation_public_key,
            assertion=assertion,
            challenge=challenge,
            prev_counter=session.attestation_counter,
            app_id=cfg.ios_app_id,
        )
    except AttestationError as exc:
        _mark_failed(session, session.attestation_kind, exc)
        _audit(
            session=session,
            action="auth.attestation.assertion_failed",
            decision="DENY",
            error_code=exc.reason_code,
            error_message=exc.message,
            extra={"kind": session.attestation_kind},
        )
        raise

    _persist_ios_assertion(session, verified)
    _audit(
        session=session,
        action="auth.attestation.assertion_verified",
        decision="ALLOW",
        extra={"kind": session.attestation_kind, "counter": verified.counter},
    )
    return session


# ---- policy gate --------------------------------------------------


def attestation_required(*, session: AstroliftSession | None, sensitive_op: bool) -> bool:
    """Decide whether ``session`` must be attested to run this operation.

    Returns True when ALL of:

    * The session is a mobile session (other kinds aren't gated
      because they can't attest).
    * The relevant Constance flag is on
      (``REQUIRE_ATTESTATION_FOR_MOBILE`` for any mobile mutation
      OR ``REQUIRE_ATTESTATION_FOR_SENSITIVE_OPS`` for a sensitive
      mutation).
    * The session's current ``attestation_trust_level`` is not
      ``genuine``.

    The caller turns True into a ``STEP_UP_REQUIRED`` envelope with
    ``requiresAttestation: true`` so the FE knows to open the
    attest-prompt rather than the password-prompt.
    """
    if session is None:
        return False
    if session.client_kind != "mobile":
        return False
    if session.attestation_trust_level == AttestationTrustLevel.GENUINE.value:
        return False

    cfg = _read_config()
    if cfg.require_for_mobile:
        return True
    if sensitive_op and cfg.require_for_sensitive_ops:
        return True
    return False


# ---- internals ----------------------------------------------------


def _normalize_kind(kind: str) -> str:
    if not isinstance(kind, str):
        raise AttestationError("UNSUPPORTED_KIND", f"kind must be a string, got {type(kind).__name__}")
    normalized = kind.strip().lower()
    if normalized not in {
        AttestationKind.IOS_APPATTEST.value,
        AttestationKind.ANDROID_PLAY_INTEGRITY.value,
    }:
        raise AttestationError("UNSUPPORTED_KIND", f"unsupported attestation kind {kind!r}")
    return normalized


def _verify_ios(
    *,
    attestation_object: str | None,
    key_id: str | None,
    challenge: str,
    app_id: str,
) -> VerifiedAttestation:
    if not attestation_object:
        raise AttestationError("MISSING_ATTESTATION", "attestationObject is required for iOS")
    if not key_id:
        raise AttestationError("MISSING_KEY_ID", "keyId is required for iOS")
    if not app_id:
        raise AttestationError(
            "MISSING_APP_ID",
            "IOS_APP_ID is not configured for this install",
        )
    return ios_verifier.verify_attestation(
        key_id=key_id,
        attestation_object=attestation_object,
        challenge=challenge,
        app_id=app_id,
    )


def _verify_android(
    *,
    token: str | None,
    expected_nonce: str,
    cfg: _Config,
) -> VerifiedIntegrity:
    if not token:
        raise AttestationError("MISSING_TOKEN", "integrityToken is required for Android")
    if not cfg.android_package_name:
        raise AttestationError(
            "MISSING_PACKAGE",
            "ANDROID_PACKAGE_NAME is not configured for this install",
        )
    return android_verifier.verify_integrity_token(
        token=token,
        expected_nonce=expected_nonce,
        expected_package=cfg.android_package_name,
        api_key=cfg.google_api_key,
        require_strong_integrity=cfg.require_strong_android_integrity,
    )


def _persist_ios_attestation(
    session: AstroliftSession,
    verified: VerifiedAttestation,
    kind_normalized: str,
) -> None:
    now = timezone.now()
    session.attestation_kind = kind_normalized
    session.attestation_trust_level = AttestationTrustLevel.GENUINE.value
    session.attestation_verified_at = now
    session.attestation_public_key = verified.public_key_pem
    session.attestation_counter = verified.counter
    session.attestation_payload = {
        "key_id": verified.key_id,
        "rp_id_hash_b64": verified.rp_id_hash.hex(),
        "receipt_size_bytes": len(verified.receipt),
        "verified_at": now.isoformat(),
    }
    session.save(
        update_fields=[
            "attestation_kind",
            "attestation_trust_level",
            "attestation_verified_at",
            "attestation_public_key",
            "attestation_counter",
            "attestation_payload",
            "updated_at",
            "version",
        ]
    )


def _persist_android_attestation(
    session: AstroliftSession,
    verified: VerifiedIntegrity,
    kind_normalized: str,
) -> None:
    now = timezone.now()
    session.attestation_kind = kind_normalized
    session.attestation_trust_level = AttestationTrustLevel.GENUINE.value
    session.attestation_verified_at = now
    session.attestation_public_key = ""  # Play Integrity is per-token
    session.attestation_counter = 0
    session.attestation_payload = {
        "package_name": verified.package_name,
        "device_verdict": list(verified.device_verdict),
        "app_verdict": verified.app_verdict,
        "account_verdict": verified.account_verdict,
        "verified_at": now.isoformat(),
    }
    session.save(
        update_fields=[
            "attestation_kind",
            "attestation_trust_level",
            "attestation_verified_at",
            "attestation_public_key",
            "attestation_counter",
            "attestation_payload",
            "updated_at",
            "version",
        ]
    )


def _persist_ios_assertion(session: AstroliftSession, verified: VerifiedAssertion) -> None:
    session.attestation_counter = verified.counter
    session.attestation_verified_at = timezone.now()
    session.save(
        update_fields=[
            "attestation_counter",
            "attestation_verified_at",
            "updated_at",
            "version",
        ]
    )


def _mark_failed(session: AstroliftSession, kind_normalized: str, exc: AttestationError) -> None:
    """Stamp a failed-verification outcome on the session.

    Does NOT clear a previously-good attestation: a session that was
    ``genuine`` at attestation time and then failed a single
    assertion stays ``failed`` (the auditor wants to see that the
    session DID once pass; the trust-level moving to ``failed``
    blocks the policy gate).
    """
    session.attestation_kind = kind_normalized or AttestationKind.NONE.value
    session.attestation_trust_level = AttestationTrustLevel.FAILED.value
    session.attestation_payload = {
        **(session.attestation_payload or {}),
        "last_failure_code": exc.reason_code,
        "last_failure_message": exc.message,
        "last_failure_at": timezone.now().isoformat(),
    }
    session.save(
        update_fields=[
            "attestation_kind",
            "attestation_trust_level",
            "attestation_payload",
            "updated_at",
            "version",
        ]
    )


def _audit(
    *,
    session: AstroliftSession,
    action: str,
    decision: str,
    error_code: str | None = None,
    error_message: str | None = None,
    extra: dict | None = None,
) -> None:
    try:
        emit_audit(
            AuditEntry(
                actor_user_id=session.user_id,
                organization_id=session.organization_id,
                action=action,
                decision=decision,
                target_kind="astrolift_session",
                target_id=str(session.guid),
                duration_ms=0,
                permissions=(),
                error_code=error_code,
                error_message=error_message,
                extra=extra,
            )
        )
    except Exception:  # noqa: BLE001 — audit must never break the response
        log.exception("attestation audit emit failed for action=%s", action)


__all__ = [
    "assert_session",
    "attest_session",
    "attestation_required",
    "issue_challenge",
    "sweep_expired_challenges",
]
