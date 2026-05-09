"""Tests for image signing verification policy (#53, spec 14 §8)."""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.image_signing import (
    AllowedSigner,
    PolicyDecision,
    SignerKind,
    SigningDecision,
    SigningEnforcement,
    SigningOverride,
    SigningPolicy,
    VerifyResult,
    apply_override,
    evaluate_policy,
    get_verifier,
    register_verifier,
    unregister_verifier,
)


def _signer_oidc() -> AllowedSigner:
    return AllowedSigner(
        kind=SignerKind.OIDC,
        identity="ci@acme.com@https://token.actions.githubusercontent.com",
    )


def _signer_kms() -> AllowedSigner:
    return AllowedSigner(
        kind=SignerKind.KMS,
        identity="arn:aws:kms:us-east-1:111:key/abc",
    )


# ---- policy guards --------------------------------------------------


def test_signing_policy_required_with_empty_allowlist_rejected():
    """Reject everything-by-default = misconfig. Surface early."""
    with pytest.raises(ValueError, match="at least one allowed"):
        SigningPolicy(enforcement=SigningEnforcement.REQUIRED)
    with pytest.raises(ValueError, match="at least one allowed"):
        SigningPolicy(enforcement=SigningEnforcement.OPTIONAL)


def test_signing_policy_disabled_allows_empty_allowlist():
    """Disabled = signing not enforced; allow-list irrelevant."""
    p = SigningPolicy(enforcement=SigningEnforcement.DISABLED)
    assert p.allowed_signers == ()


def test_allowed_signer_rejects_empty_identity():
    with pytest.raises(ValueError):
        AllowedSigner(kind=SignerKind.OIDC, identity="")


# ---- evaluate_policy ------------------------------------------------


def test_disabled_passes_anything():
    policy = SigningPolicy(enforcement=SigningEnforcement.DISABLED)
    result = VerifyResult(image_uri="reg/api:abc", is_signed=False)
    out = evaluate_policy(result=result, policy=policy)
    assert out.decision == SigningDecision.PASS


def test_required_blocks_unsigned():
    policy = SigningPolicy(
        enforcement=SigningEnforcement.REQUIRED,
        allowed_signers=(_signer_oidc(),),
    )
    out = evaluate_policy(
        result=VerifyResult(image_uri="reg/api:abc", is_signed=False),
        policy=policy,
    )
    assert out.decision == SigningDecision.BLOCK
    assert "not signed" in out.reason


def test_optional_warns_unsigned():
    policy = SigningPolicy(
        enforcement=SigningEnforcement.OPTIONAL,
        allowed_signers=(_signer_oidc(),),
    )
    out = evaluate_policy(
        result=VerifyResult(image_uri="reg/api:abc", is_signed=False),
        policy=policy,
    )
    assert out.decision == SigningDecision.WARN


def test_required_passes_when_signer_matches_allowlist():
    signer = _signer_oidc()
    policy = SigningPolicy(
        enforcement=SigningEnforcement.REQUIRED,
        allowed_signers=(signer,),
    )
    out = evaluate_policy(
        result=VerifyResult(
            image_uri="reg/api:abc", is_signed=True, matched_signer=signer,
        ),
        policy=policy,
    )
    assert out.decision == SigningDecision.PASS
    assert out.matched_signer is signer


def test_required_blocks_signed_but_no_match():
    """Signed but signer not in allow-list — different from
    unsigned. Operator may have a stale CI key."""
    policy = SigningPolicy(
        enforcement=SigningEnforcement.REQUIRED,
        allowed_signers=(_signer_oidc(),),
    )
    out = evaluate_policy(
        result=VerifyResult(
            image_uri="reg/api:abc", is_signed=True, matched_signer=None,
            verify_error="no matching signer",
        ),
        policy=policy,
    )
    assert out.decision == SigningDecision.BLOCK
    assert "no signer matched" in out.reason


def test_optional_warns_signed_but_no_match():
    policy = SigningPolicy(
        enforcement=SigningEnforcement.OPTIONAL,
        allowed_signers=(_signer_oidc(),),
    )
    out = evaluate_policy(
        result=VerifyResult(image_uri="reg/api:abc", is_signed=True),
        policy=policy,
    )
    assert out.decision == SigningDecision.WARN


def test_block_when_matched_signer_not_in_allowlist():
    """Defensive: a misconfigured verifier returns a 'matched' signer
    that isn't actually on the allow-list. Treat as block."""
    declared = _signer_oidc()
    policy = SigningPolicy(
        enforcement=SigningEnforcement.REQUIRED,
        allowed_signers=(declared,),
    )
    other = AllowedSigner(kind=SignerKind.KMS, identity="some-other-key")
    out = evaluate_policy(
        result=VerifyResult(
            image_uri="reg/api:abc", is_signed=True, matched_signer=other,
        ),
        policy=policy,
    )
    assert out.decision == SigningDecision.BLOCK
    assert "driver bug" in out.reason


def test_supports_both_oidc_and_kms_signers():
    oidc = _signer_oidc()
    kms = _signer_kms()
    policy = SigningPolicy(
        enforcement=SigningEnforcement.REQUIRED,
        allowed_signers=(oidc, kms),
    )
    # OIDC pass
    out = evaluate_policy(
        result=VerifyResult(image_uri="reg/api:abc", is_signed=True, matched_signer=oidc),
        policy=policy,
    )
    assert out.decision == SigningDecision.PASS
    # KMS pass
    out = evaluate_policy(
        result=VerifyResult(image_uri="reg/api:abc", is_signed=True, matched_signer=kms),
        policy=policy,
    )
    assert out.decision == SigningDecision.PASS


# ---- override -------------------------------------------------------


def test_override_rejects_short_reason():
    with pytest.raises(ValueError, match="reason"):
        SigningOverride(by_user_id=1, reason="fix")


def test_override_converts_block_to_warn():
    decision = PolicyDecision(
        decision=SigningDecision.BLOCK,
        matched_signer=None,
        reason="image is not signed",
    )
    override = SigningOverride(by_user_id=42, reason="emergency hotfix prod outage")
    new_decision = apply_override(decision, override)
    assert new_decision.decision == SigningDecision.WARN
    assert "user 42" in new_decision.reason
    assert "emergency hotfix" in new_decision.reason


def test_override_no_op_on_pass_or_warn():
    pass_dec = PolicyDecision(
        decision=SigningDecision.PASS, matched_signer=None, reason="ok",
    )
    override = SigningOverride(by_user_id=1, reason="just to confirm always")
    assert apply_override(pass_dec, override) == pass_dec


# ---- verifier registry ---------------------------------------------


def test_verifier_registry_round_trip():
    def fake_cosign(image_uri):
        return VerifyResult(image_uri=image_uri, is_signed=False)

    register_verifier("cosign", fake_cosign)
    try:
        out = get_verifier("cosign")("reg/api:abc")
        assert out.is_signed is False
    finally:
        unregister_verifier("cosign")


def test_get_verifier_raises_when_unregistered():
    with pytest.raises(KeyError, match="no verifier"):
        get_verifier("not-a-real")
