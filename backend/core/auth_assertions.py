"""
MFA step-up + session-freshness + device-assertion policy
(#148, spec 12 §2.3 + spec 03 §5).

ABAC policies on sensitive operations declare requirements like::

    require:
      amr: [otp, webauthn]      # at least one of these MFA methods
      max_session_age_seconds: 900   # within last 15 min
      device_assertion: true     # WebAuthn assertion present

This module decides whether a presented session satisfies those
requirements, and — when not — what specific step-up is needed.

Why a discrete module:

- The check runs in middleware before resolver execution; it must
  be cheap and not reach into the database. We get a snapshot of
  the session's auth claims at request time and evaluate against
  the policy declared on the resolver.
- The 'what step-up is needed' answer drives the GraphQL error
  payload that the UI uses to drive the re-auth modal. A typed
  ``StepUpRequired`` exception with structured fields beats string
  matching.

The policy DSL is intentionally tiny — three knobs map to the spec
exactly. Anything more elaborate belongs in the broader ABAC
evaluator.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import datetime

# Spec 12 §2.3 keywords. amr values mirror RFC 8176; the platform's
# IdP integration normalises to this fixed vocabulary.
AMR_PASSWORD = "pwd"
AMR_TOTP = "otp"
AMR_WEBAUTHN = "webauthn"
AMR_HARDWARE = "hwk"
AMR_SMS = "sms"     # accepted for legacy IdPs but not allowed for step-up
KNOWN_AMR = {AMR_PASSWORD, AMR_TOTP, AMR_WEBAUTHN, AMR_HARDWARE, AMR_SMS}

# Methods that count as 'strong' MFA — anything in AMR_REQUIRED list
# in a policy must be one of these (not pwd, not sms).
STRONG_AMR = {AMR_TOTP, AMR_WEBAUTHN, AMR_HARDWARE}


@dataclasses.dataclass(frozen=True, slots=True)
class AssertionPolicy:
    """The ABAC requirement attached to a sensitive resolver."""

    required_amr_any: tuple[str, ...] = ()
    """Any one of these AMR values satisfies the MFA requirement.
    Empty = no MFA required beyond baseline auth."""

    max_session_age_seconds: int | None = None
    """If set, the session's authentication must be no older than this.
    Catches 'I logged in two days ago, now I'm deleting prod.'"""

    device_assertion_required: bool = False
    """If True, the session must carry a fresh WebAuthn assertion
    (typically a touch on a hardware key in the last few seconds).
    Distinct from MFA: a session can have MFA from yesterday's TOTP
    but lack a fresh device assertion."""

    def __post_init__(self) -> None:
        for amr in self.required_amr_any:
            if amr not in KNOWN_AMR:
                raise ValueError(f"unknown AMR value {amr!r}")
            if self.required_amr_any and amr not in STRONG_AMR:
                raise ValueError(
                    f"AMR {amr!r} is not a strong MFA method "
                    f"(allowed: {sorted(STRONG_AMR)})"
                )
        if (
            self.max_session_age_seconds is not None
            and self.max_session_age_seconds <= 0
        ):
            raise ValueError("max_session_age_seconds must be positive")


@dataclasses.dataclass(frozen=True, slots=True)
class SessionAssertion:
    """The auth claims snapshot the middleware extracts from the
    request's session row + ID token claims."""

    user_id: int
    amr: tuple[str, ...]                 # AMR values from the IdP
    authenticated_at: datetime           # when this session was first authed
    device_assertion_at: datetime | None = None  # last WebAuthn touch


@dataclasses.dataclass(frozen=True, slots=True)
class StepUpRequired(Exception):
    """Raised by :func:`evaluate` when the session doesn't satisfy
    the policy. Caller turns this into a GraphQL error envelope::

        {
          "errors": [{
            "extensions": {
              "code": "STEP_UP_REQUIRED",
              "missing_amr_any": ["otp", "webauthn"],
              "session_too_old": false,
              "device_assertion_required": true,
            }
          }]
        }

    The structured fields drive the re-auth modal: 'tap your security
    key' vs. 'enter your TOTP' vs. 'sign in again'.
    """

    missing_amr_any: tuple[str, ...] = ()
    session_too_old: bool = False
    device_assertion_required: bool = False

    def __str__(self) -> str:
        parts = []
        if self.missing_amr_any:
            parts.append(f"need MFA: {sorted(self.missing_amr_any)}")
        if self.session_too_old:
            parts.append("session too old")
        if self.device_assertion_required:
            parts.append("device assertion required")
        return "; ".join(parts) or "step-up required"


# ``StepUpRequired`` inheriting from ``Exception`` via dataclass
# requires the dataclass init to run *and* Exception.__init__. The
# safest path is making it a plain class.
class StepUpRequired(Exception):  # noqa: F811 — intentional override
    """Structured step-up signal. See module docstring for the
    error envelope shape callers should produce."""

    def __init__(
        self,
        *,
        missing_amr_any: Iterable[str] = (),
        session_too_old: bool = False,
        device_assertion_required: bool = False,
    ) -> None:
        self.missing_amr_any = tuple(missing_amr_any)
        self.session_too_old = session_too_old
        self.device_assertion_required = device_assertion_required
        parts = []
        if self.missing_amr_any:
            parts.append(f"need MFA: {sorted(self.missing_amr_any)}")
        if self.session_too_old:
            parts.append("session too old")
        if self.device_assertion_required:
            parts.append("device assertion required")
        super().__init__("; ".join(parts) or "step-up required")


def evaluate(policy: AssertionPolicy, session: SessionAssertion, *, now: datetime) -> None:
    """Raise :class:`StepUpRequired` if the session doesn't satisfy
    the policy. Returns ``None`` on success.

    All applicable failures are reported in *one* exception so the
    UI can ask for everything at once instead of looping
    ('OK now MFA' → 'now device assertion' → 'now sign in fresh').
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if session.authenticated_at.tzinfo is None:
        raise ValueError("session.authenticated_at must be timezone-aware")
    if (
        session.device_assertion_at is not None
        and session.device_assertion_at.tzinfo is None
    ):
        raise ValueError("session.device_assertion_at must be timezone-aware when provided")

    missing_amr: tuple[str, ...] = ()
    if policy.required_amr_any:
        if not any(amr in session.amr for amr in policy.required_amr_any):
            missing_amr = policy.required_amr_any

    too_old = False
    if policy.max_session_age_seconds is not None:
        age = (now - session.authenticated_at).total_seconds()
        if age > policy.max_session_age_seconds:
            too_old = True

    needs_device = False
    if policy.device_assertion_required:
        # The assertion must exist AND be reasonably fresh — a
        # touch from 5 minutes ago is fine, from yesterday isn't.
        # The freshness window mirrors max_session_age when set,
        # else falls back to 5 minutes (sensitive operations want
        # a recent touch).
        fresh_window_seconds = policy.max_session_age_seconds or 300
        if session.device_assertion_at is None:
            needs_device = True
        else:
            age = (now - session.device_assertion_at).total_seconds()
            if age > fresh_window_seconds:
                needs_device = True

    if missing_amr or too_old or needs_device:
        raise StepUpRequired(
            missing_amr_any=missing_amr,
            session_too_old=too_old,
            device_assertion_required=needs_device,
        )
