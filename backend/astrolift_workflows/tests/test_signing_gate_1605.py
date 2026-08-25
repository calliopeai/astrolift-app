"""Image signatures are verified, and only when an org asked (#1605).

The policy engine in `activities.image_signing` was complete and tested and
had no producer and no caller: nothing declared an allowed signer, and the
supply-chain gate documented that it deliberately did not evaluate signing.
Meanwhile `RegisteredApp.security_policy_resolved` returns
`block_on_missing_signature: True` by default -- so the model carried a
policy the platform could not evaluate, and an operator reading that field
was told enforcement was on.

The decision on #1605 was verify-third-party-only. These hold the two halves
that matter: an org that has not opted in is completely unaffected, and an
org that has opted in cannot be silently unenforced.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.image_signing import (
    AllowedSigner,
    SignerKind,
    SigningDecision,
    SigningEnforcement,
    SigningMisconfigured,
    VerifyResult,
    policy_for_org,
    register_verifier,
    unregister_verifier,
    verify_image_for_org,
)

SIGNER = {"kind": "oidc", "identity": "ci@acme.com@https://token.actions.githubusercontent.com"}


class _Org:
    slug = "acme"

    def __init__(self, policy=None):
        self.image_signing_policy = policy or {}


@pytest.fixture
def cosign():
    """A verifier that reports whatever the test wants."""
    state = {"result": None}

    def _verify(image_uri):
        return state["result"] or VerifyResult(image_uri=image_uri, is_signed=False)

    register_verifier("cosign", _verify)
    yield state
    unregister_verifier("cosign")


# ---- the default: nothing changes for anyone ----------------------------


def test_an_org_with_no_policy_is_disabled():
    assert policy_for_org(_Org()).enforcement is SigningEnforcement.DISABLED


def test_a_disabled_org_never_calls_a_verifier():
    """No verifier is registered here at all. If this reached one it would
    raise, so passing proves the disabled path short-circuits."""
    decision = verify_image_for_org(_Org(), "acme/hello@sha256:abc")

    assert decision.decision is SigningDecision.PASS


def test_a_malformed_policy_is_disabled_rather_than_enforcing():
    """Every install has an empty column. Any reading that turned an unset or
    broken policy into enforcement would block every promote everywhere."""
    for junk in ("", [], {"enforcement": "banana"}, {"enforcement": 7}):
        assert policy_for_org(_Org(junk)).enforcement is SigningEnforcement.DISABLED


# ---- opted in -----------------------------------------------------------


def test_a_signed_image_from_an_allowed_signer_passes(cosign):
    org = _Org({"enforcement": "required", "allowed_signers": [SIGNER]})
    cosign["result"] = VerifyResult(
        image_uri="acme/hello@sha256:abc",
        is_signed=True,
        matched_signer=AllowedSigner(kind=SignerKind.OIDC, identity=SIGNER["identity"]),
    )

    assert verify_image_for_org(org, "acme/hello@sha256:abc").decision is SigningDecision.PASS


def test_an_unsigned_image_blocks_when_required(cosign):
    org = _Org({"enforcement": "required", "allowed_signers": [SIGNER]})
    cosign["result"] = VerifyResult(image_uri="acme/hello@sha256:abc", is_signed=False)

    assert verify_image_for_org(org, "acme/hello@sha256:abc").decision is SigningDecision.BLOCK


def test_an_unsigned_image_warns_when_optional(cosign):
    org = _Org({"enforcement": "optional", "allowed_signers": [SIGNER]})
    cosign["result"] = VerifyResult(image_uri="acme/hello@sha256:abc", is_signed=False)

    assert verify_image_for_org(org, "acme/hello@sha256:abc").decision is SigningDecision.WARN


# ---- the misconfigurations that must not read as clean ------------------


def test_enforcement_with_no_signers_is_refused():
    """An empty allow-list with enforcement on rejects everything, so it is
    caught at read time with a message naming the org rather than surfacing
    later as a mysterious block."""
    with pytest.raises(SigningMisconfigured, match="no allowed signers"):
        policy_for_org(_Org({"enforcement": "required", "allowed_signers": []}))


def test_a_malformed_signer_entry_is_refused_not_dropped():
    """Silently skipping a bad entry could shrink a three-signer allow-list
    to one and start blocking images that should pass."""
    with pytest.raises(SigningMisconfigured):
        policy_for_org(
            _Org({"enforcement": "required", "allowed_signers": [SIGNER, {"kind": "smoke-signals"}]})
        )


def test_enforcement_with_no_verifier_registered_is_refused():
    """The likeliest misconfiguration: the repo ships no cosign dependency.
    This must not report a clean pass."""
    unregister_verifier("cosign")
    org = _Org({"enforcement": "required", "allowed_signers": [SIGNER]})

    with pytest.raises(SigningMisconfigured, match="no 'cosign' verifier"):
        verify_image_for_org(org, "acme/hello@sha256:abc")
