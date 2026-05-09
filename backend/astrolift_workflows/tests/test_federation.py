"""Tests for federation policy (#32)."""

from __future__ import annotations

import pytest

from astrolift_workflows.federation import (
    CLOCK_SKEW_SECONDS,
    DEFAULT_TRUST_TTL_SECONDS,
    HANDSHAKE_ORDER,
    JWT_TTL_SECONDS,
    MAX_PULL_INTERVAL_SECONDS,
    MAX_TRUST_TTL_SECONDS,
    ChallengeResponse,
    DiscoveryAppEntry,
    FederatedRequestClaims,
    FederationCapability,
    FederationError,
    FederationSyncMode,
    FederationSyncSetting,
    FederationTrust,
    HandshakeStep,
    HelloMessage,
    ShareableKind,
    VerifyResult,
    authorize_share,
    build_federated_claims,
    capability_for_kind,
    filter_discovery_response,
    grants_capability,
    is_auto_sync_enabled,
    is_trust_active,
    jwks_url_for,
    next_handshake_step,
    normalize_install_id,
    validate_trust_ttl,
    verify_federated_request,
    verify_handshake_pair,
)

# ---- install id normalization --------------------------------------


def test_normalize_install_id_basic():
    assert normalize_install_id(
        value="acme.platform.example",
    ) == "acme.platform.example"


def test_normalize_install_id_lowercases():
    assert normalize_install_id(
        value="ACME.PLATFORM.EXAMPLE",
    ) == "acme.platform.example"


def test_normalize_install_id_strips_trailing_dot():
    assert normalize_install_id(
        value="acme.platform.example.",
    ) == "acme.platform.example"


def test_normalize_install_id_rejects_empty():
    with pytest.raises(FederationError):
        normalize_install_id(value="")


def test_normalize_install_id_rejects_invalid_chars():
    with pytest.raises(FederationError):
        normalize_install_id(value="my_zone.example.com")


# ---- FederationTrust invariants ------------------------------------


def _trust(**overrides) -> FederationTrust:
    base = dict(
        local_install="acme.platform.example",
        remote_install="globex.platform.example",
        capabilities=frozenset({FederationCapability.APP_DISCOVERY_READONLY}),
        jwks_url="https://globex.platform.example/.well-known/astrolift-federation/jwks",
        granted_at_unix=1_700_000_000,
        expires_at_unix=1_700_000_000 + DEFAULT_TRUST_TTL_SECONDS,
    )
    base.update(overrides)
    return FederationTrust(**base)


def test_trust_basic():
    t = _trust()
    assert FederationCapability.APP_DISCOVERY_READONLY in t.capabilities


def test_trust_rejects_self_federation():
    """Mesh rule: install can't federate with itself."""
    with pytest.raises(FederationError, match="itself"):
        _trust(remote_install="acme.platform.example")


def test_trust_rejects_empty_capabilities():
    with pytest.raises(FederationError):
        _trust(capabilities=frozenset())


def test_trust_rejects_empty_jwks_url():
    with pytest.raises(FederationError):
        _trust(jwks_url="")


def test_trust_rejects_inverted_window():
    with pytest.raises(FederationError):
        _trust(
            granted_at_unix=1_700_000_000,
            expires_at_unix=1_699_999_999,
        )


# ---- TTL bounds ----------------------------------------------------


def test_trust_ttl_zero_rejected():
    with pytest.raises(FederationError):
        validate_trust_ttl(seconds=0)


def test_trust_ttl_max_5_years():
    with pytest.raises(FederationError, match="5 years"):
        validate_trust_ttl(seconds=MAX_TRUST_TTL_SECONDS + 1)


def test_trust_ttl_default_one_year():
    assert DEFAULT_TRUST_TTL_SECONDS == 365 * 86400


# ---- is_trust_active -----------------------------------------------


def test_trust_active_within_window():
    t = _trust()
    assert is_trust_active(trust=t, now_unix=1_700_000_100) is True


def test_trust_inactive_when_revoked():
    t = _trust(is_revoked=True, revoke_reason="operator")
    assert is_trust_active(trust=t, now_unix=1_700_000_100) is False


def test_trust_inactive_when_expired():
    t = _trust()
    assert is_trust_active(
        trust=t, now_unix=t.expires_at_unix + 1,
    ) is False


# ---- grants_capability ---------------------------------------------


def test_grants_capability_present():
    t = _trust(capabilities=frozenset({
        FederationCapability.APP_TRANSFER,
        FederationCapability.APP_DISCOVERY_READONLY,
    }))
    assert grants_capability(
        trust=t, capability=FederationCapability.APP_TRANSFER,
        now_unix=1_700_000_100,
    ) is True


def test_grants_capability_absent():
    t = _trust(capabilities=frozenset({
        FederationCapability.APP_DISCOVERY_READONLY,
    }))
    assert grants_capability(
        trust=t, capability=FederationCapability.APP_TRANSFER,
        now_unix=1_700_000_100,
    ) is False


def test_grants_capability_revoked_short_circuits():
    t = _trust(
        capabilities=frozenset({FederationCapability.APP_TRANSFER}),
        is_revoked=True,
    )
    assert grants_capability(
        trust=t, capability=FederationCapability.APP_TRANSFER,
        now_unix=1_700_000_100,
    ) is False


# ---- handshake state machine ---------------------------------------


def test_handshake_order_locked():
    """Symmetric mutual challenge-response. The fingerprint OOB
    confirmations are the human-in-the-loop trust anchor; lock
    the order so they stay mid-flow."""
    assert HANDSHAKE_ORDER[0] == HandshakeStep.INIT
    assert HANDSHAKE_ORDER[-1] == HandshakeStep.PERSISTED_TRUST
    # SENT_HELLO before RECEIVED_CHALLENGE_RESPONSE
    assert (
        HANDSHAKE_ORDER.index(HandshakeStep.SENT_HELLO)
        < HANDSHAKE_ORDER.index(HandshakeStep.RECEIVED_CHALLENGE_RESPONSE)
    )
    # Local fingerprint confirm before final ack
    assert (
        HANDSHAKE_ORDER.index(HandshakeStep.OPERATOR_CONFIRMED_REMOTE_FINGERPRINT)
        < HANDSHAKE_ORDER.index(HandshakeStep.SENT_FINAL_ACK)
    )
    # Remote operator confirm before persist
    assert (
        HANDSHAKE_ORDER.index(HandshakeStep.REMOTE_OPERATOR_CONFIRMED)
        < HANDSHAKE_ORDER.index(HandshakeStep.PERSISTED_TRUST)
    )


def test_next_handshake_step():
    assert next_handshake_step(
        current=HandshakeStep.INIT,
    ) == HandshakeStep.SENT_HELLO


def test_next_handshake_step_terminal():
    assert next_handshake_step(
        current=HandshakeStep.PERSISTED_TRUST,
    ) is None


# ---- HelloMessage --------------------------------------------------


def test_hello_message_basic():
    h = HelloMessage(
        iss="a.example",
        nonce="x" * 32,
        jwks_url="https://a.example/.well-known/astrolift-federation/jwks",
        requested_capabilities=(FederationCapability.APP_TRANSFER,),
    )
    assert len(h.nonce) == 32


def test_hello_message_short_nonce_rejected():
    """Nonce too short = brute-forcable challenge → refuse."""
    with pytest.raises(FederationError, match="32 chars"):
        HelloMessage(
            iss="a.example", nonce="short",
            jwks_url="https://a/jwks",
            requested_capabilities=(FederationCapability.APP_TRANSFER,),
        )


def test_hello_message_empty_caps_rejected():
    """No-cap handshake is meaningless."""
    with pytest.raises(FederationError):
        HelloMessage(
            iss="a.example", nonce="x" * 32,
            jwks_url="https://a/jwks",
            requested_capabilities=(),
        )


# ---- ChallengeResponse --------------------------------------------


def test_challenge_response_basic():
    cr = ChallengeResponse(
        iss="b.example", nonce="y" * 32,
        signed_peer_nonce="abc123",
        jwks_url="https://b/jwks",
        granted_capabilities=(FederationCapability.APP_DISCOVERY_READONLY,),
        fingerprint="sha256:abc",
    )
    assert cr.fingerprint == "sha256:abc"


def test_challenge_response_requires_fingerprint():
    with pytest.raises(FederationError, match="fingerprint"):
        ChallengeResponse(
            iss="b.example", nonce="y" * 32,
            signed_peer_nonce="sig",
            jwks_url="https://b/jwks",
            granted_capabilities=(FederationCapability.APP_TRANSFER,),
            fingerprint="",
        )


# ---- verify_handshake_pair ----------------------------------------


def test_verify_handshake_pair_happy():
    hello = HelloMessage(
        iss="a.example", nonce="x" * 32,
        jwks_url="https://a/jwks",
        requested_capabilities=(
            FederationCapability.APP_TRANSFER,
            FederationCapability.APP_DISCOVERY_READONLY,
        ),
    )
    response = ChallengeResponse(
        iss="b.example", nonce="y" * 32,
        signed_peer_nonce="sig_b",
        jwks_url="https://b/jwks",
        granted_capabilities=(FederationCapability.APP_TRANSFER,),
        fingerprint="fp_b",
    )
    verify_handshake_pair(local_hello=hello, remote_response=response)


def test_verify_handshake_pair_rejects_self_loop():
    """If response.iss == hello.iss, something's broken (DNS
    misconfig or attacker reflecting the message)."""
    hello = HelloMessage(
        iss="a.example", nonce="x" * 32,
        jwks_url="https://a/jwks",
        requested_capabilities=(FederationCapability.APP_TRANSFER,),
    )
    response = ChallengeResponse(
        iss="a.example", nonce="y" * 32,
        signed_peer_nonce="sig",
        jwks_url="https://a/jwks",
        granted_capabilities=(FederationCapability.APP_TRANSFER,),
        fingerprint="fp",
    )
    with pytest.raises(FederationError, match="differ"):
        verify_handshake_pair(local_hello=hello, remote_response=response)


def test_verify_handshake_pair_remote_must_grant_subset():
    """Remote can grant a subset but not capabilities A didn't
    request — strange behavior, refuse loudly."""
    hello = HelloMessage(
        iss="a.example", nonce="x" * 32,
        jwks_url="https://a/jwks",
        requested_capabilities=(
            FederationCapability.APP_DISCOVERY_READONLY,
        ),
    )
    response = ChallengeResponse(
        iss="b.example", nonce="y" * 32,
        signed_peer_nonce="sig",
        jwks_url="https://b/jwks",
        granted_capabilities=(FederationCapability.SECRET_SHARING,),
        fingerprint="fp",
    )
    with pytest.raises(FederationError, match="not in request"):
        verify_handshake_pair(local_hello=hello, remote_response=response)


def test_verify_handshake_pair_remote_can_decline():
    """Remote granting empty caps = decline; refuse to persist."""
    hello = HelloMessage(
        iss="a.example", nonce="x" * 32,
        jwks_url="https://a/jwks",
        requested_capabilities=(FederationCapability.APP_TRANSFER,),
    )
    response = ChallengeResponse(
        iss="b.example", nonce="y" * 32,
        signed_peer_nonce="sig",
        jwks_url="https://b/jwks",
        granted_capabilities=(),
        fingerprint="fp",
    )
    with pytest.raises(FederationError, match="declined"):
        verify_handshake_pair(local_hello=hello, remote_response=response)


# ---- jwks_url ------------------------------------------------------


def test_jwks_url_format():
    url = jwks_url_for(install_id="acme.platform.example")
    assert url == (
        "https://acme.platform.example"
        "/.well-known/astrolift-federation/jwks"
    )


# ---- federated claims ----------------------------------------------


def test_build_claims_basic():
    claims = build_federated_claims(
        iss="acme.platform.example",
        aud="globex.platform.example",
        sub="user-123",
        issued_at_unix=1_700_000_000, jti="rpc-1",
        capability=FederationCapability.APP_DISCOVERY_READONLY,
    )
    assert claims.exp == 1_700_000_000 + JWT_TTL_SECONDS


def test_build_claims_rejects_self_audience():
    with pytest.raises(FederationError, match="must differ"):
        build_federated_claims(
            iss="a.example", aud="a.example",
            sub="x", issued_at_unix=0, jti="j",
            capability=FederationCapability.APP_TRANSFER,
        )


def test_build_claims_rejects_oversized_ttl():
    with pytest.raises(FederationError):
        build_federated_claims(
            iss="a.example", aud="b.example", sub="x",
            issued_at_unix=0, jti="j",
            capability=FederationCapability.APP_TRANSFER,
            ttl_seconds=JWT_TTL_SECONDS + 1,
        )


def test_build_claims_requires_jti():
    with pytest.raises(FederationError, match="jti"):
        build_federated_claims(
            iss="a.example", aud="b.example", sub="x",
            issued_at_unix=0, jti="",
            capability=FederationCapability.APP_TRANSFER,
        )


def test_build_claims_requires_sub():
    with pytest.raises(FederationError, match="sub"):
        build_federated_claims(
            iss="a.example", aud="b.example", sub="",
            issued_at_unix=0, jti="j",
            capability=FederationCapability.APP_TRANSFER,
        )


# ---- verify_federated_request --------------------------------------


def _claims(**overrides) -> FederatedRequestClaims:
    base = dict(
        iss="acme.platform.example",
        aud="globex.platform.example",
        sub="user-123",
        issued_at_unix=1_700_000_000,
        jti="rpc-1",
        capability=FederationCapability.APP_DISCOVERY_READONLY,
    )
    base.update(overrides)
    return build_federated_claims(**base)


def test_verify_happy_path():
    trust = _trust(
        local_install="globex.platform.example",
        remote_install="acme.platform.example",
        capabilities=frozenset({FederationCapability.APP_DISCOVERY_READONLY}),
    )
    decision = verify_federated_request(
        claims=_claims(),
        expected_local_install="globex.platform.example",
        trust_lookup=lambda local, remote: trust,
        seen_jti_lookup=lambda jti: False,
        now_unix=1_700_000_005,
    )
    assert decision.accepted is True


def test_verify_audience_mismatch():
    decision = verify_federated_request(
        claims=_claims(),
        expected_local_install="widget.platform.example",
        trust_lookup=lambda local, remote: None,
        seen_jti_lookup=lambda jti: False,
        now_unix=1_700_000_005,
    )
    assert decision.result == VerifyResult.AUDIENCE_MISMATCH


def test_verify_no_trust():
    decision = verify_federated_request(
        claims=_claims(),
        expected_local_install="globex.platform.example",
        trust_lookup=lambda local, remote: None,
        seen_jti_lookup=lambda jti: False,
        now_unix=1_700_000_005,
    )
    assert decision.result == VerifyResult.NO_TRUST


def test_verify_trust_revoked():
    trust = _trust(
        local_install="globex.platform.example",
        remote_install="acme.platform.example",
        is_revoked=True, revoke_reason="incident",
    )
    decision = verify_federated_request(
        claims=_claims(),
        expected_local_install="globex.platform.example",
        trust_lookup=lambda local, remote: trust,
        seen_jti_lookup=lambda jti: False,
        now_unix=1_700_000_005,
    )
    assert decision.result == VerifyResult.TRUST_REVOKED


def test_verify_insufficient_capability():
    trust = _trust(
        local_install="globex.platform.example",
        remote_install="acme.platform.example",
        capabilities=frozenset({FederationCapability.APP_DISCOVERY_READONLY}),
    )
    decision = verify_federated_request(
        claims=_claims(capability=FederationCapability.APP_TRANSFER),
        expected_local_install="globex.platform.example",
        trust_lookup=lambda local, remote: trust,
        seen_jti_lookup=lambda jti: False,
        now_unix=1_700_000_005,
    )
    assert decision.result == VerifyResult.INSUFFICIENT_CAPABILITY


def test_verify_replay():
    trust = _trust(
        local_install="globex.platform.example",
        remote_install="acme.platform.example",
        capabilities=frozenset({FederationCapability.APP_DISCOVERY_READONLY}),
    )
    decision = verify_federated_request(
        claims=_claims(),
        expected_local_install="globex.platform.example",
        trust_lookup=lambda local, remote: trust,
        seen_jti_lookup=lambda jti: jti == "rpc-1",
        now_unix=1_700_000_005,
    )
    assert decision.result == VerifyResult.REPLAY


def test_verify_readonly_skips_replay_check():
    trust = _trust(
        local_install="globex.platform.example",
        remote_install="acme.platform.example",
        capabilities=frozenset({FederationCapability.APP_DISCOVERY_READONLY}),
    )
    decision = verify_federated_request(
        claims=_claims(),
        expected_local_install="globex.platform.example",
        trust_lookup=lambda local, remote: trust,
        seen_jti_lookup=lambda jti: True,
        now_unix=1_700_000_005,
        require_jti_uniqueness=False,
    )
    assert decision.accepted is True


def test_verify_clock_skew_tolerance():
    now_unix = 1_700_000_000 + JWT_TTL_SECONDS + (CLOCK_SKEW_SECONDS - 1)
    trust = _trust(
        local_install="globex.platform.example",
        remote_install="acme.platform.example",
        capabilities=frozenset({FederationCapability.APP_DISCOVERY_READONLY}),
    )
    decision = verify_federated_request(
        claims=_claims(),
        expected_local_install="globex.platform.example",
        trust_lookup=lambda local, remote: trust,
        seen_jti_lookup=lambda jti: False,
        now_unix=now_unix,
    )
    assert decision.accepted is True


# ---- shareable kinds + capability mapping --------------------------


@pytest.mark.parametrize("kind,expected_cap", [
    (ShareableKind.APP_INVENTORY, FederationCapability.APP_DISCOVERY_READONLY),
    (ShareableKind.APP_MANIFEST, FederationCapability.APP_TRANSFER),
    (ShareableKind.ENV_CONFIG, FederationCapability.ENV_CONFIG_SHARE),
    (ShareableKind.SECRET_VALUE, FederationCapability.SECRET_SHARING),
])
def test_capability_for_kind(kind, expected_cap):
    assert capability_for_kind(kind=kind) == expected_cap


# ---- FederationSyncSetting ----------------------------------------


def _sync(**overrides) -> FederationSyncSetting:
    base = dict(
        local_install="acme.platform.example",
        peer_install="globex.platform.example",
        kind=ShareableKind.APP_MANIFEST,
        mode=FederationSyncMode.AUTO_PUSH,
    )
    base.update(overrides)
    return FederationSyncSetting(**base)


def test_sync_setting_basic():
    s = _sync()
    assert s.kind == ShareableKind.APP_MANIFEST


def test_sync_setting_rejects_self_peer():
    with pytest.raises(FederationError, match="distinct"):
        _sync(peer_install="acme.platform.example")


def test_sync_setting_pull_interval_required_for_auto_pull():
    """AUTO_PULL with explicit interval bound."""
    s = _sync(
        mode=FederationSyncMode.AUTO_PULL,
        pull_interval_seconds=120,
    )
    assert s.pull_interval_seconds == 120


def test_sync_setting_auto_pull_default_interval():
    """AUTO_PULL with pull_interval_seconds=0 → use default."""
    s = _sync(
        mode=FederationSyncMode.AUTO_PULL,
        pull_interval_seconds=0,
    )
    # No raise; the default applies at activity time
    assert s.mode == FederationSyncMode.AUTO_PULL


def test_sync_setting_pull_interval_below_min_rejected():
    with pytest.raises(FederationError, match="below minimum"):
        _sync(
            mode=FederationSyncMode.AUTO_PULL,
            pull_interval_seconds=5,
        )


def test_sync_setting_pull_interval_above_max_rejected():
    with pytest.raises(FederationError, match="exceeds maximum"):
        _sync(
            mode=FederationSyncMode.AUTO_PULL,
            pull_interval_seconds=MAX_PULL_INTERVAL_SECONDS + 1,
        )


def test_sync_setting_pull_interval_only_for_auto_pull():
    """Setting interval on a non-AUTO_PULL mode is operator typo."""
    with pytest.raises(FederationError, match="only AUTO_PULL"):
        _sync(
            mode=FederationSyncMode.AUTO_PUSH,
            pull_interval_seconds=120,
        )


def test_is_auto_sync_enabled_disabled():
    s = _sync(mode=FederationSyncMode.DISABLED)
    assert is_auto_sync_enabled(setting=s) is False


def test_is_auto_sync_enabled_manual():
    s = _sync(mode=FederationSyncMode.MANUAL)
    assert is_auto_sync_enabled(setting=s) is False


def test_is_auto_sync_enabled_auto_push():
    s = _sync(mode=FederationSyncMode.AUTO_PUSH)
    assert is_auto_sync_enabled(setting=s) is True


def test_is_auto_sync_enabled_auto_pull():
    s = _sync(
        mode=FederationSyncMode.AUTO_PULL,
        pull_interval_seconds=120,
    )
    assert is_auto_sync_enabled(setting=s) is True


# ---- authorize_share -----------------------------------------------


def test_authorize_share_happy_path():
    trust = _trust(
        local_install="globex.platform.example",
        remote_install="acme.platform.example",
        capabilities=frozenset({FederationCapability.APP_TRANSFER}),
    )
    # No raise = authorized
    authorize_share(
        trust=trust, kind=ShareableKind.APP_MANIFEST,
        now_unix=1_700_000_100,
    )


def test_authorize_share_kind_capability_mismatch():
    """Trust grants APP_TRANSFER; share requests SECRET_VALUE
    which needs SECRET_SHARING."""
    trust = _trust(
        capabilities=frozenset({FederationCapability.APP_TRANSFER}),
    )
    with pytest.raises(FederationError, match="SECRET_SHARING|secret_sharing"):
        authorize_share(
            trust=trust, kind=ShareableKind.SECRET_VALUE,
            now_unix=1_700_000_100,
        )


def test_authorize_share_revoked():
    trust = _trust(
        capabilities=frozenset({FederationCapability.APP_TRANSFER}),
        is_revoked=True,
    )
    with pytest.raises(FederationError, match="not active"):
        authorize_share(
            trust=trust, kind=ShareableKind.APP_MANIFEST,
            now_unix=1_700_000_100,
        )


def test_authorize_share_expired():
    trust = _trust(
        capabilities=frozenset({FederationCapability.APP_TRANSFER}),
    )
    with pytest.raises(FederationError, match="not active"):
        authorize_share(
            trust=trust, kind=ShareableKind.APP_MANIFEST,
            now_unix=trust.expires_at_unix + 1,
        )


# ---- discovery filter ----------------------------------------------


def test_discovery_filter_passes_through():
    apps = (
        DiscoveryAppEntry(
            app_slug="api",
            last_deployed_at_unix=1_700_000_000,
            health_badge="healthy",
            environments=("prod", "staging"),
        ),
    )
    out = filter_discovery_response(
        apps=apps,
        requestor_install="acme.platform.example",
        granting_install="globex.platform.example",
    )
    assert len(out) == 1


def test_discovery_filter_rejects_self():
    with pytest.raises(FederationError, match="self"):
        filter_discovery_response(
            apps=(),
            requestor_install="acme.platform.example",
            granting_install="acme.platform.example",
        )
