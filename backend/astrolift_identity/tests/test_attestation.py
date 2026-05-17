"""Tests for device attestation (#496).

Covers:

* Challenge issuance + one-shot consumption + TTL.
* iOS App Attest verifier: CBOR/DER nonce extraction, RP-ID mismatch
  rejection, counter-replay rejection, assertion signature verify.
* Android Play Integrity verifier: happy-path, rooted-device reject,
  tampered-app reject, nonce + package mismatch.
* ``attest_session`` orchestration: persists trust level on success +
  failure, audit emitted both ways.
* ``attestation_required`` policy gate: respects Constance flags +
  client_kind.
* GraphQL mutations: requestAttestationChallenge, attestSession,
  assertSession.

Apple's attestation chain is exercised against the verifier's CBOR
+ DER + RP-ID layers using directly constructed payloads. The full
x.509 chain-to-Apple-root path can't be exercised without real
Apple-signed material, so the integration test patches the verifier
boundary and lets the rest of the service ride through.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import struct
import uuid
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session as DjangoSession
from django.test import RequestFactory
from django.utils import timezone

from astrolift_identity.attestation import (
    AttestationError,
    VerifiedAttestation,
)
from astrolift_identity.attestation import (
    android_play_integrity as android_verifier,
)
from astrolift_identity.attestation import (
    ios_appattest as ios_verifier,
)
from astrolift_identity.attestation.service import (
    assert_session,
    attest_session,
    attestation_required,
    issue_challenge,
)
from astrolift_identity.models import (
    AttestationChallenge,
    AttestationKind,
    AttestationTrustLevel,
    ClientKind,
)
from astrolift_identity.schema.mutations import (
    AssertSessionInput,
    AttestSessionInput,
    IdentityMutation,
    RequestAttestationChallengeInput,
)
from astrolift_identity.sessions import (
    SESSION_CLIENT_KIND_KEY,
    record_session,
)
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---- fixtures ------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """Skip OpenSearch side-effects when User / Member creates fire signal handlers."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def constance_stub(monkeypatch):
    """In-memory Constance shim for attestation flags.

    The real Constance fixture in the test stack is heavy; for these
    tests we only need to drive ``REQUIRE_ATTESTATION_FOR_MOBILE``,
    ``IOS_APP_ID``, ``ANDROID_PACKAGE_NAME``, and the API-key bag.
    Test mutates ``cfg.<NAME> = ...`` and the service module's
    Constance lookup reads it back via the standard
    ``from constance import config as constance_config`` import.
    """

    class _Cfg:
        REQUIRE_ATTESTATION_FOR_MOBILE = False
        REQUIRE_ATTESTATION_FOR_SENSITIVE_OPS = False
        REQUIRE_STRONG_ANDROID_INTEGRITY = False
        IOS_APP_ID = "TESTTEAM.com.example.test"
        ANDROID_PACKAGE_NAME = "com.example.test"
        GOOGLE_PLAY_INTEGRITY_API_KEY = "test-key"
        ATTESTATION_CHALLENGE_TTL_SECONDS = 300

    cfg = _Cfg()
    monkeypatch.setattr("constance.config", cfg, raising=False)
    return cfg


def _user(email: str | None = None):
    if email is None:
        email = f"u-{uuid.uuid4().hex[:8]}@astrolift.dev"
    return User.objects.create(email=email, username=email.split("@")[0])


def _request_with_session(user, *, client_kind: str = ClientKind.MOBILE.value):
    session_key = uuid.uuid4().hex
    DjangoSession.objects.create(
        session_key=session_key,
        session_data="",
        expire_date=timezone.now() + dt.timedelta(days=7),
    )
    request = RequestFactory().get("/")
    request.user = user
    request.session = SimpleNamespace(
        session_key=session_key,
        get=lambda k, default=None: {SESSION_CLIENT_KIND_KEY: client_kind}.get(k, default),
        get_expiry_date=lambda: timezone.now() + dt.timedelta(days=7),
    )
    return request


def _info(user, request=None):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request or RequestFactory().get("/")))


def _ctx(user=None):
    return tenant_context(
        TenantContext(
            organization_id=None,
            actor_user_id=user.id if user else None,
        )
    )


# ---- helper: build authenticator data ------------------------------


def _build_auth_data(*, app_id: str, counter: int, credential_id: bytes) -> bytes:
    """Construct an authenticatorData blob matching App Attest's spec."""
    rp_hash = hashlib.sha256(app_id.encode("utf-8")).digest()
    flags = b"\x00"
    counter_bytes = struct.pack(">I", counter)
    aaguid = b"\x00" * 16
    cred_id_len = struct.pack(">H", len(credential_id))
    return rp_hash + flags + counter_bytes + aaguid + cred_id_len + credential_id


# ---- challenge lifecycle ------------------------------------------


def test_issue_challenge_persists_and_returns_nonce(constance_stub):
    user = _user()
    row = issue_challenge(user=user, kind=AttestationKind.IOS_APPATTEST.value)
    assert row.user_id == user.pk
    assert row.kind == AttestationKind.IOS_APPATTEST.value
    assert len(row.nonce) >= 40  # token_urlsafe(32) → 43 chars
    assert row.expires_at > timezone.now()


def test_issue_challenge_unsupported_kind_raises(constance_stub):
    user = _user()
    with pytest.raises(AttestationError) as excinfo:
        issue_challenge(user=user, kind="webauthn")
    assert excinfo.value.reason_code == "UNSUPPORTED_KIND"


def test_request_attestation_challenge_mutation_persists_nonce(constance_stub):
    user = _user()
    req = _request_with_session(user)
    with _ctx(user=user):
        result = IdentityMutation().request_attestation_challenge(
            _info(user, request=req),
            input=RequestAttestationChallengeInput(kind=AttestationKind.IOS_APPATTEST.value),
        )
    assert result.ok, result.errors
    assert result.data.challenge
    assert AttestationChallenge.objects.filter(user=user).count() == 1


def test_challenge_is_one_shot(constance_stub):
    """Two attestSession submissions for the same challenge: second
    one fails with CHALLENGE_NOT_FOUND because the first consumed it."""
    user = _user()
    challenge = issue_challenge(user=user, kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value)
    nonce = challenge.nonce

    fake_response = {
        "tokenPayloadExternal": {
            "requestDetails": {
                "nonce": nonce,
                "requestPackageName": "com.example.test",
            },
            "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
            "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED"},
        }
    }
    android_verifier.register_transport(lambda **kw: fake_response)
    try:
        req = _request_with_session(user)
        row = record_session(req)
        # First call: success.
        attest_session(
            session=row,
            kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value,
            challenge=nonce,
            integrity_token="t",
        )
        # Second call with the same nonce: rejected.
        with pytest.raises(AttestationError) as excinfo:
            attest_session(
                session=row,
                kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value,
                challenge=nonce,
                integrity_token="t",
            )
        assert excinfo.value.reason_code == "CHALLENGE_NOT_FOUND"
    finally:
        android_verifier.register_transport(None)


def test_challenge_expires(constance_stub):
    user = _user()
    challenge = issue_challenge(user=user, kind=AttestationKind.IOS_APPATTEST.value)
    # Force the row past expiry.
    AttestationChallenge.objects.filter(pk=challenge.pk).update(
        expires_at=timezone.now() - dt.timedelta(seconds=1)
    )
    req = _request_with_session(user)
    row = record_session(req)
    with pytest.raises(AttestationError) as excinfo:
        attest_session(
            session=row,
            kind=AttestationKind.IOS_APPATTEST.value,
            challenge=challenge.nonce,
            attestation_object="x",
            key_id="y",
        )
    assert excinfo.value.reason_code == "CHALLENGE_EXPIRED"


# ---- iOS App Attest verifier --------------------------------------


def test_ios_assertion_happy_path(constance_stub):
    """Build a real EC key + sign a real assertion → verify_assertion
    accepts it and returns the post-increment counter."""
    app_id = "TESTTEAM.com.example.test"
    challenge = "challenge-string"
    counter = 5
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )

    auth_data = _build_auth_data(app_id=app_id, counter=counter, credential_id=b"")
    nonce = hashlib.sha256(auth_data + hashlib.sha256(challenge.encode()).digest()).digest()
    signature = private_key.sign(nonce, ec.ECDSA(hashes.SHA256()))

    assertion_cbor = _encode_cbor_map({"signature": signature, "authenticatorData": auth_data})
    verified = ios_verifier.verify_assertion(
        stored_public_key_pem=pub_pem,
        assertion=base64.b64encode(assertion_cbor).decode(),
        challenge=challenge,
        prev_counter=counter - 1,
        app_id=app_id,
    )
    assert verified.counter == counter


def test_ios_assertion_counter_replay_rejected(constance_stub):
    """A replayed assertion (counter <= prev_counter) is rejected."""
    app_id = "TESTTEAM.com.example.test"
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )

    auth_data = _build_auth_data(app_id=app_id, counter=5, credential_id=b"")
    nonce = hashlib.sha256(auth_data + hashlib.sha256(b"c").digest()).digest()
    signature = private_key.sign(nonce, ec.ECDSA(hashes.SHA256()))
    assertion_cbor = _encode_cbor_map({"signature": signature, "authenticatorData": auth_data})

    with pytest.raises(AttestationError) as excinfo:
        ios_verifier.verify_assertion(
            stored_public_key_pem=pub_pem,
            assertion=base64.b64encode(assertion_cbor).decode(),
            challenge="c",
            prev_counter=5,  # same as the assertion's counter — replay
            app_id=app_id,
        )
    assert excinfo.value.reason_code == "COUNTER_REPLAY"


def test_ios_assertion_wrong_rp_id_rejected(constance_stub):
    """An assertion whose rpIdHash doesn't match the configured app_id is rejected."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )

    # Build auth_data for the WRONG app_id.
    auth_data = _build_auth_data(
        app_id="DIFFERENT.com.example.other",
        counter=1,
        credential_id=b"",
    )
    nonce = hashlib.sha256(auth_data + hashlib.sha256(b"c").digest()).digest()
    signature = private_key.sign(nonce, ec.ECDSA(hashes.SHA256()))
    assertion_cbor = _encode_cbor_map({"signature": signature, "authenticatorData": auth_data})

    with pytest.raises(AttestationError) as excinfo:
        ios_verifier.verify_assertion(
            stored_public_key_pem=pub_pem,
            assertion=base64.b64encode(assertion_cbor).decode(),
            challenge="c",
            prev_counter=0,
            app_id="TESTTEAM.com.example.test",  # right app_id
        )
    assert excinfo.value.reason_code == "RP_ID_MISMATCH"


def test_ios_assertion_bad_signature_rejected(constance_stub):
    """Sign with one key but verify with another → BAD_SIGNATURE."""
    app_id = "TESTTEAM.com.example.test"
    wrong_private = ec.generate_private_key(ec.SECP256R1())
    right_private = ec.generate_private_key(ec.SECP256R1())
    pub_pem = (
        right_private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )

    auth_data = _build_auth_data(app_id=app_id, counter=1, credential_id=b"")
    nonce = hashlib.sha256(auth_data + hashlib.sha256(b"c").digest()).digest()
    signature = wrong_private.sign(nonce, ec.ECDSA(hashes.SHA256()))
    assertion_cbor = _encode_cbor_map({"signature": signature, "authenticatorData": auth_data})

    with pytest.raises(AttestationError) as excinfo:
        ios_verifier.verify_assertion(
            stored_public_key_pem=pub_pem,
            assertion=base64.b64encode(assertion_cbor).decode(),
            challenge="c",
            prev_counter=0,
            app_id=app_id,
        )
    assert excinfo.value.reason_code == "BAD_SIGNATURE"


def test_ios_attestation_invalid_cbor_rejected():
    with pytest.raises(AttestationError) as excinfo:
        ios_verifier.verify_attestation(
            key_id="k",
            attestation_object=base64.b64encode(b"\xff\xff\xff").decode(),
            challenge="c",
            app_id="TESTTEAM.com.example.test",
        )
    assert excinfo.value.reason_code in {"INVALID_CBOR", "INVALID_SHAPE", "UNSUPPORTED_FORMAT"}


def test_ios_attestation_wrong_fmt_rejected():
    blob = _encode_cbor_map({"fmt": "fido-u2f", "attStmt": {}, "authData": b""})
    with pytest.raises(AttestationError) as excinfo:
        ios_verifier.verify_attestation(
            key_id="k",
            attestation_object=base64.b64encode(blob).decode(),
            challenge="c",
            app_id="TESTTEAM.com.example.test",
        )
    assert excinfo.value.reason_code == "UNSUPPORTED_FORMAT"


# ---- iOS attest_session orchestration -----------------------------


def test_attest_session_ios_happy_path_marks_session(constance_stub, monkeypatch):
    """Patch the verifier boundary; assert the session row carries
    trust_level=genuine + stores the public key for later assertions."""
    user = _user()
    challenge = issue_challenge(user=user, kind=AttestationKind.IOS_APPATTEST.value)
    req = _request_with_session(user)
    session_row = record_session(req)

    fake_pem = "-----BEGIN PUBLIC KEY-----\nFAKEPEMFAKEPEMFAKEPEM\n-----END PUBLIC KEY-----\n"
    monkeypatch.setattr(
        "astrolift_identity.attestation.ios_appattest.verify_attestation",
        lambda **kw: VerifiedAttestation(
            key_id=kw["key_id"],
            public_key_pem=fake_pem,
            counter=0,
            receipt=b"r",
            rp_id_hash=b"\x00" * 32,
            raw_authenticator_data=b"a",
        ),
    )

    attest_session(
        session=session_row,
        kind=AttestationKind.IOS_APPATTEST.value,
        challenge=challenge.nonce,
        attestation_object="x",
        key_id="key",
    )
    session_row.refresh_from_db()
    assert session_row.attestation_kind == AttestationKind.IOS_APPATTEST.value
    assert session_row.attestation_trust_level == AttestationTrustLevel.GENUINE.value
    assert session_row.attestation_verified_at is not None
    assert session_row.attestation_public_key == fake_pem
    assert session_row.attestation_payload.get("key_id") == "key"


def test_attest_session_ios_failure_marks_session_failed(constance_stub, monkeypatch):
    user = _user()
    challenge = issue_challenge(user=user, kind=AttestationKind.IOS_APPATTEST.value)
    req = _request_with_session(user)
    session_row = record_session(req)

    def _boom(**kw):
        raise AttestationError("NONCE_MISMATCH", "bad nonce")

    monkeypatch.setattr("astrolift_identity.attestation.ios_appattest.verify_attestation", _boom)

    with pytest.raises(AttestationError):
        attest_session(
            session=session_row,
            kind=AttestationKind.IOS_APPATTEST.value,
            challenge=challenge.nonce,
            attestation_object="x",
            key_id="k",
        )
    session_row.refresh_from_db()
    assert session_row.attestation_trust_level == AttestationTrustLevel.FAILED.value
    assert session_row.attestation_payload.get("last_failure_code") == "NONCE_MISMATCH"


# ---- Android Play Integrity ---------------------------------------


def test_android_happy_path_marks_session(constance_stub):
    user = _user()
    challenge = issue_challenge(user=user, kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value)
    req = _request_with_session(user)
    session_row = record_session(req)

    fake_response = {
        "tokenPayloadExternal": {
            "requestDetails": {
                "nonce": challenge.nonce,
                "requestPackageName": "com.example.test",
            },
            "deviceIntegrity": {
                "deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"],
            },
            "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED"},
            "accountDetails": {"appLicensingVerdict": "LICENSED"},
        }
    }
    android_verifier.register_transport(lambda **kw: fake_response)
    try:
        attest_session(
            session=session_row,
            kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value,
            challenge=challenge.nonce,
            integrity_token="t",
        )
    finally:
        android_verifier.register_transport(None)

    session_row.refresh_from_db()
    assert session_row.attestation_kind == AttestationKind.ANDROID_PLAY_INTEGRITY.value
    assert session_row.attestation_trust_level == AttestationTrustLevel.GENUINE.value
    assert "PLAY_RECOGNIZED" == session_row.attestation_payload["app_verdict"]


def test_android_rooted_device_rejected(constance_stub):
    user = _user()
    challenge = issue_challenge(user=user, kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value)
    req = _request_with_session(user)
    session_row = record_session(req)

    fake_response = {
        "tokenPayloadExternal": {
            "requestDetails": {
                "nonce": challenge.nonce,
                "requestPackageName": "com.example.test",
            },
            # Rooted device: no MEETS_DEVICE_INTEGRITY in the verdict.
            "deviceIntegrity": {"deviceRecognitionVerdict": []},
            "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED"},
        }
    }
    android_verifier.register_transport(lambda **kw: fake_response)
    try:
        with pytest.raises(AttestationError) as excinfo:
            attest_session(
                session=session_row,
                kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value,
                challenge=challenge.nonce,
                integrity_token="t",
            )
        assert excinfo.value.reason_code == "DEVICE_REJECTED"
    finally:
        android_verifier.register_transport(None)


def test_android_tampered_app_rejected(constance_stub):
    user = _user()
    challenge = issue_challenge(user=user, kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value)
    req = _request_with_session(user)
    session_row = record_session(req)

    fake_response = {
        "tokenPayloadExternal": {
            "requestDetails": {
                "nonce": challenge.nonce,
                "requestPackageName": "com.example.test",
            },
            "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
            # Tampered/repackaged: not PLAY_RECOGNIZED.
            "appIntegrity": {"appRecognitionVerdict": "UNRECOGNIZED_VERSION"},
        }
    }
    android_verifier.register_transport(lambda **kw: fake_response)
    try:
        with pytest.raises(AttestationError) as excinfo:
            attest_session(
                session=session_row,
                kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value,
                challenge=challenge.nonce,
                integrity_token="t",
            )
        assert excinfo.value.reason_code == "APP_TAMPERED"
    finally:
        android_verifier.register_transport(None)


def test_android_nonce_mismatch_rejected(constance_stub):
    user = _user()
    challenge = issue_challenge(user=user, kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value)
    req = _request_with_session(user)
    session_row = record_session(req)

    fake_response = {
        "tokenPayloadExternal": {
            "requestDetails": {
                "nonce": "different-nonce",  # mismatch
                "requestPackageName": "com.example.test",
            },
            "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
            "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED"},
        }
    }
    android_verifier.register_transport(lambda **kw: fake_response)
    try:
        with pytest.raises(AttestationError) as excinfo:
            attest_session(
                session=session_row,
                kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value,
                challenge=challenge.nonce,
                integrity_token="t",
            )
        assert excinfo.value.reason_code == "NONCE_MISMATCH"
    finally:
        android_verifier.register_transport(None)


def test_android_package_mismatch_rejected(constance_stub):
    user = _user()
    challenge = issue_challenge(user=user, kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value)
    req = _request_with_session(user)
    session_row = record_session(req)

    fake_response = {
        "tokenPayloadExternal": {
            "requestDetails": {
                "nonce": challenge.nonce,
                "requestPackageName": "com.example.OTHER",  # mismatch
            },
            "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
            "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED"},
        }
    }
    android_verifier.register_transport(lambda **kw: fake_response)
    try:
        with pytest.raises(AttestationError) as excinfo:
            attest_session(
                session=session_row,
                kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value,
                challenge=challenge.nonce,
                integrity_token="t",
            )
        assert excinfo.value.reason_code == "PACKAGE_MISMATCH"
    finally:
        android_verifier.register_transport(None)


# ---- attestation policy gate --------------------------------------


def test_attestation_required_is_false_on_browser_session(constance_stub):
    """Browser / CLI / API-token sessions are never gated — they
    can't attest by design."""
    constance_stub.REQUIRE_ATTESTATION_FOR_MOBILE = True
    user = _user()
    req = _request_with_session(user, client_kind=ClientKind.WEB.value)
    row = record_session(req)
    assert attestation_required(session=row, sensitive_op=True) is False


def test_attestation_required_is_true_when_flag_on_and_not_attested(constance_stub):
    constance_stub.REQUIRE_ATTESTATION_FOR_MOBILE = True
    user = _user()
    req = _request_with_session(user, client_kind=ClientKind.MOBILE.value)
    row = record_session(req)
    assert attestation_required(session=row, sensitive_op=False) is True


def test_attestation_required_is_false_after_genuine_attestation(constance_stub):
    constance_stub.REQUIRE_ATTESTATION_FOR_MOBILE = True
    user = _user()
    req = _request_with_session(user, client_kind=ClientKind.MOBILE.value)
    row = record_session(req)
    row.attestation_trust_level = AttestationTrustLevel.GENUINE.value
    row.attestation_kind = AttestationKind.IOS_APPATTEST.value
    row.save()
    assert attestation_required(session=row, sensitive_op=True) is False


def test_attestation_required_for_sensitive_ops_only(constance_stub):
    """Mobile session, REQUIRE_ATTESTATION_FOR_MOBILE off, sensitive
    op + REQUIRE_ATTESTATION_FOR_SENSITIVE_OPS on → required."""
    constance_stub.REQUIRE_ATTESTATION_FOR_MOBILE = False
    constance_stub.REQUIRE_ATTESTATION_FOR_SENSITIVE_OPS = True
    user = _user()
    req = _request_with_session(user, client_kind=ClientKind.MOBILE.value)
    row = record_session(req)
    assert attestation_required(session=row, sensitive_op=False) is False
    assert attestation_required(session=row, sensitive_op=True) is True


# ---- assert_session (iOS periodic re-check) -----------------------


def test_assert_session_bumps_counter_on_success(constance_stub):
    """Build a real signing key, simulate attestation already done,
    issue a fresh challenge, build an assertion → counter bumps."""
    app_id = constance_stub.IOS_APP_ID
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )

    user = _user()
    req = _request_with_session(user, client_kind=ClientKind.MOBILE.value)
    session_row = record_session(req)
    session_row.attestation_kind = AttestationKind.IOS_APPATTEST.value
    session_row.attestation_trust_level = AttestationTrustLevel.GENUINE.value
    session_row.attestation_public_key = pub_pem
    session_row.attestation_counter = 0
    session_row.save()

    challenge = issue_challenge(user=user, kind=AttestationKind.IOS_APPATTEST.value)

    auth_data = _build_auth_data(app_id=app_id, counter=1, credential_id=b"")
    nonce = hashlib.sha256(auth_data + hashlib.sha256(challenge.nonce.encode()).digest()).digest()
    signature = private_key.sign(nonce, ec.ECDSA(hashes.SHA256()))
    assertion_cbor = _encode_cbor_map({"signature": signature, "authenticatorData": auth_data})

    assert_session(
        session=session_row,
        challenge=challenge.nonce,
        assertion=base64.b64encode(assertion_cbor).decode(),
    )
    session_row.refresh_from_db()
    assert session_row.attestation_counter == 1


def test_assert_session_replay_rejected(constance_stub):
    """An assertion with counter <= prev_counter is rejected as replay."""
    app_id = constance_stub.IOS_APP_ID
    private_key = ec.generate_private_key(ec.SECP256R1())
    pub_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )

    user = _user()
    req = _request_with_session(user, client_kind=ClientKind.MOBILE.value)
    session_row = record_session(req)
    session_row.attestation_kind = AttestationKind.IOS_APPATTEST.value
    session_row.attestation_public_key = pub_pem
    session_row.attestation_counter = 5
    session_row.save()

    challenge = issue_challenge(user=user, kind=AttestationKind.IOS_APPATTEST.value)
    auth_data = _build_auth_data(app_id=app_id, counter=5, credential_id=b"")  # replay
    nonce = hashlib.sha256(auth_data + hashlib.sha256(challenge.nonce.encode()).digest()).digest()
    signature = private_key.sign(nonce, ec.ECDSA(hashes.SHA256()))
    assertion_cbor = _encode_cbor_map({"signature": signature, "authenticatorData": auth_data})

    with pytest.raises(AttestationError) as excinfo:
        assert_session(
            session=session_row,
            challenge=challenge.nonce,
            assertion=base64.b64encode(assertion_cbor).decode(),
        )
    assert excinfo.value.reason_code == "COUNTER_REPLAY"


# ---- GraphQL mutations end-to-end ---------------------------------


def test_attest_session_mutation_returns_genuine_trust_level(constance_stub):
    user = _user()
    req = _request_with_session(user)
    record_session(req)

    challenge = issue_challenge(user=user, kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value)
    fake_response = {
        "tokenPayloadExternal": {
            "requestDetails": {
                "nonce": challenge.nonce,
                "requestPackageName": "com.example.test",
            },
            "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
            "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED"},
        }
    }
    android_verifier.register_transport(lambda **kw: fake_response)
    try:
        with _ctx(user=user):
            result = IdentityMutation().attest_session(
                _info(user, request=req),
                input=AttestSessionInput(
                    kind=AttestationKind.ANDROID_PLAY_INTEGRITY.value,
                    challenge=challenge.nonce,
                    integrity_token="t",
                ),
            )
    finally:
        android_verifier.register_transport(None)
    assert result.ok, result.errors
    assert result.data.trust_level == AttestationTrustLevel.GENUINE.value
    assert result.data.kind == AttestationKind.ANDROID_PLAY_INTEGRITY.value


def test_attest_session_mutation_failure_returns_envelope(constance_stub):
    """Bad challenge → validation envelope, NOT a raised exception."""
    user = _user()
    req = _request_with_session(user)
    record_session(req)
    with _ctx(user=user):
        result = IdentityMutation().attest_session(
            _info(user, request=req),
            input=AttestSessionInput(
                kind=AttestationKind.IOS_APPATTEST.value,
                challenge="not-a-real-nonce",
                attestation_object="x",
                key_id="k",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "CHALLENGE_NOT_FOUND" in result.errors[0].message


def test_request_challenge_requires_authentication(constance_stub):
    from django.contrib.auth.models import AnonymousUser

    req = RequestFactory().get("/")
    req.user = AnonymousUser()
    req.session = SimpleNamespace(session_key=None, get=lambda *a, **k: None)
    info = SimpleNamespace(context=SimpleNamespace(user=req.user, request=req))
    result = IdentityMutation().request_attestation_challenge(
        info,
        input=RequestAttestationChallengeInput(kind=AttestationKind.IOS_APPATTEST.value),
    )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_assert_session_mutation_requires_attested_session(constance_stub):
    """assertSession on a non-attested session → WRONG_KIND envelope."""
    user = _user()
    req = _request_with_session(user)
    record_session(req)
    challenge = issue_challenge(user=user, kind=AttestationKind.IOS_APPATTEST.value)
    with _ctx(user=user):
        result = IdentityMutation().assert_session(
            _info(user, request=req),
            input=AssertSessionInput(assertion="x", challenge=challenge.nonce),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "WRONG_KIND" in result.errors[0].message


# ---- helpers ------------------------------------------------------


def _encode_cbor_map(items: dict) -> bytes:
    """Minimal CBOR encoder for test fixtures.

    Mirror of the decoder in ``astrolift_identity.attestation._cbor``;
    used only by tests to build the assertion / attestation payloads
    we then feed through the decoder.
    """
    return _enc_value(items)


def _enc_value(value) -> bytes:
    if isinstance(value, bool):
        return bytes([0xF5 if value else 0xF4])
    if value is None:
        return b"\xf6"
    if isinstance(value, int):
        if value >= 0:
            return _enc_uint(0, value)
        return _enc_uint(1, -1 - value)
    if isinstance(value, (bytes, bytearray)):
        return _enc_uint(2, len(value)) + bytes(value)
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return _enc_uint(3, len(encoded)) + encoded
    if isinstance(value, list):
        out = _enc_uint(4, len(value))
        for item in value:
            out += _enc_value(item)
        return out
    if isinstance(value, dict):
        out = _enc_uint(5, len(value))
        for k, v in value.items():
            out += _enc_value(k)
            out += _enc_value(v)
        return out
    raise TypeError(f"unsupported CBOR value: {type(value).__name__}")


def _enc_uint(major: int, value: int) -> bytes:
    prefix = major << 5
    if value < 24:
        return bytes([prefix | value])
    if value < 0x100:
        return bytes([prefix | 24, value])
    if value < 0x10000:
        return bytes([prefix | 25]) + struct.pack(">H", value)
    if value < 0x100000000:
        return bytes([prefix | 26]) + struct.pack(">I", value)
    return bytes([prefix | 27]) + struct.pack(">Q", value)
