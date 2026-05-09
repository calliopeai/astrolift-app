"""
Image signing verification policy (#53, spec 14 §8).

Pure-Python policy + verification contract. The actual signature
verification (calling cosign / sigstore lib) lives in driver land
via a registered ``verifier_callable``; this module owns:

* The per-org enforcement modes (``required`` / ``optional`` /
  ``disabled``).
* The allowed-signer policy (an org may require Sigstore OIDC
  identities like ``ci@acme.com`` or KMS key handles like
  ``arn:aws:kms:us-east-1:...:key/abc``).
* The decision logic that turns 'verifier output + org policy' into
  ``PASS / WARN / BLOCK`` matching the image-scan shape from #146.
* Override semantics: the same audit-stamped reason pattern as
  ``ScanOverride`` (≥10 char reason).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from enum import Enum


class SigningEnforcement(str, Enum):
    DISABLED = "disabled"
    OPTIONAL = "optional"
    REQUIRED = "required"


class SigningDecision(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


class SignerKind(str, Enum):
    OIDC = "oidc"
    KMS = "kms"


@dataclasses.dataclass(frozen=True, slots=True)
class AllowedSigner:
    """One entry in an org's allow-list."""

    kind: SignerKind
    identity: str
    """OIDC: subject + issuer (e.g. 'ci@acme.com@https://token.actions.githubusercontent.com').
    KMS: handle / ARN (e.g. 'arn:aws:kms:us-east-1:111:key/abc')."""

    def __post_init__(self) -> None:
        if not self.identity:
            raise ValueError("identity is required")


@dataclasses.dataclass(frozen=True, slots=True)
class SigningPolicy:
    """Per-org enforcement + allow-list."""

    enforcement: SigningEnforcement = SigningEnforcement.DISABLED
    allowed_signers: tuple[AllowedSigner, ...] = ()

    def __post_init__(self) -> None:
        if self.enforcement != SigningEnforcement.DISABLED and not self.allowed_signers:
            # An empty allow-list with required/optional enforcement
            # would reject everything — surface the misconfig early.
            raise ValueError(
                f"enforcement={self.enforcement.value!r} requires at least "
                "one allowed signer"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class VerifyResult:
    """Result the verifier returns to the policy. Drivers map their
    cosign output (matched signers, errors, etc.) onto this shape."""

    image_uri: str
    is_signed: bool
    matched_signer: AllowedSigner | None = None
    """Set when the signature verified AND matched the org allow-list."""
    verify_error: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: SigningDecision
    matched_signer: AllowedSigner | None
    reason: str


def evaluate_policy(
    *, result: VerifyResult, policy: SigningPolicy
) -> PolicyDecision:
    """Apply ``policy`` to a verifier ``result``."""
    if policy.enforcement == SigningEnforcement.DISABLED:
        return PolicyDecision(
            decision=SigningDecision.PASS,
            matched_signer=None,
            reason="signing enforcement disabled for this org",
        )

    if not result.is_signed:
        if policy.enforcement == SigningEnforcement.REQUIRED:
            return PolicyDecision(
                decision=SigningDecision.BLOCK,
                matched_signer=None,
                reason=(
                    f"image {result.image_uri!r} is not signed; "
                    "org policy requires signed images"
                ),
            )
        return PolicyDecision(
            decision=SigningDecision.WARN,
            matched_signer=None,
            reason=f"image {result.image_uri!r} is not signed (org policy: optional)",
        )

    # Image is signed — verifier may or may not have matched a signer.
    if result.matched_signer is None:
        if policy.enforcement == SigningEnforcement.REQUIRED:
            return PolicyDecision(
                decision=SigningDecision.BLOCK,
                matched_signer=None,
                reason=(
                    f"image is signed but no signer matched the org allow-list "
                    f"({len(policy.allowed_signers)} allowed); "
                    f"verify error: {result.verify_error or 'none'}"
                ),
            )
        return PolicyDecision(
            decision=SigningDecision.WARN,
            matched_signer=None,
            reason=(
                "image is signed but signer not in allow-list "
                "(org policy: optional)"
            ),
        )

    # Confirm the matched signer is actually in the policy's allow-list
    # (defensive — verifier shouldn't return one that isn't, but a
    # misconfigured driver could).
    if result.matched_signer not in policy.allowed_signers:
        return PolicyDecision(
            decision=SigningDecision.BLOCK,
            matched_signer=None,
            reason=(
                f"matched_signer {result.matched_signer.identity!r} is not in "
                f"the org's allowed_signers list (driver bug?)"
            ),
        )

    return PolicyDecision(
        decision=SigningDecision.PASS,
        matched_signer=result.matched_signer,
        reason=f"signed by {result.matched_signer.identity!r}",
    )


# ---- override (mirrors ScanOverride from #146) ----------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SigningOverride:
    by_user_id: int
    reason: str

    def __post_init__(self) -> None:
        if not self.reason or len(self.reason.strip()) < 10:
            raise ValueError(
                "override reason must be at least 10 characters "
                "(operator must explain why)"
            )
        if self.by_user_id <= 0:
            raise ValueError("by_user_id must be positive")


def apply_override(
    decision: PolicyDecision, override: SigningOverride | None
) -> PolicyDecision:
    """Apply an override to a BLOCK decision. Audit log records both
    the original block and the override action."""
    if override is None or decision.decision != SigningDecision.BLOCK:
        return decision
    return PolicyDecision(
        decision=SigningDecision.WARN,
        matched_signer=decision.matched_signer,
        reason=(
            f"BLOCK overridden by user {override.by_user_id}: "
            f"{override.reason}"
        ),
    )


# ---- verifier registry ---------------------------------------------


VerifierCallable = Callable[[str], VerifyResult]
"""Verifier contract: ``verify(image_uri) -> VerifyResult``. Real
implementations call cosign / sigstore-python and map the response."""


_VERIFIERS: dict[str, VerifierCallable] = {}


def register_verifier(name: str, fn: VerifierCallable) -> None:
    _VERIFIERS[name] = fn


def get_verifier(name: str) -> VerifierCallable:
    if name not in _VERIFIERS:
        raise KeyError(
            f"no verifier registered for {name!r}; "
            f"available: {sorted(_VERIFIERS)}"
        )
    return _VERIFIERS[name]


def unregister_verifier(name: str) -> None:
    _VERIFIERS.pop(name, None)
