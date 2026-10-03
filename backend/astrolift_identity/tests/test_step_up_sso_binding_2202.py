from __future__ import annotations

import datetime as dt
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from authlib.integrations.django_client import OAuth
from authlib.jose import JsonWebKey, jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model, login
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import caches
from django.test import RequestFactory
from django.utils import timezone

from astrolift_identity import step_up_sso
from astrolift_identity.session_elevation import is_elevated
from auth1.models import UserInfo
from core.mutations import register_audit_writer

pytestmark = pytest.mark.django_db


@pytest.fixture
def oidc(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(pem)
    key_path.chmod(0o600)
    public = JsonWebKey.import_key(pem).as_dict(is_private=False)
    public.update(kid="native-key", alg="RS256", use="sig")
    wire = SimpleNamespace(
        claims={}, calls=0, raw_claims=False, fail=False, signing_key=pem, top_level_auth_time=False
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/.well-known/openid-configuration":
                self.send_json(
                    {
                        "issuer": wire.issuer,
                        "authorization_endpoint": wire.issuer + "/authorize",
                        "token_endpoint": wire.issuer + "/token",
                        "jwks_uri": wire.issuer + "/jwks",
                        "id_token_signing_alg_values_supported": ["RS256"],
                    }
                )
            elif self.path == "/jwks":
                self.send_json({"keys": [public]})
            else:
                self.send_json({}, 404)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            wire.calls += 1
            if wire.fail:
                self.send_json({"error": "invalid_grant", "error_description": "PRIVATE_OIDC_CANARY"}, 400)
                return
            token = {"access_token": "PRIVATE_OIDC_CANARY", "token_type": "Bearer"}
            if wire.top_level_auth_time:
                token["auth_time"] = int(time.time())
            if wire.raw_claims:
                token["userinfo"] = wire.claims
            else:
                token["id_token"] = jwt.encode(
                    {"alg": "RS256", "kid": "native-key"}, wire.claims, wire.signing_key
                ).decode()
            self.send_json(token)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    wire.issuer = f"https://localhost:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    oauth = OAuth()
    oauth.register(
        "auth0_stepup",
        client_id="native-client",
        client_secret="native-client-secret",
        client_kwargs={"scope": "openid", "verify": str(cert_path)},
        server_metadata_url=wire.issuer + "/.well-known/openid-configuration",
    )
    monkeypatch.setattr(step_up_sso, "_oauth", oauth)
    for cache in caches.all():
        cache.clear()
    try:
        yield wire
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def ceremony(oidc):
    actor = get_user_model().objects.create_user(username="sso-actor", email="actor@example.test")
    other = get_user_model().objects.create_user(username="sso-other", email="actor@example.test")
    identity = UserInfo.objects.create(
        sub="owned-subject",
        iss=oidc.issuer,
        internal_user=actor,
        updated_at=timezone.now(),
        email_verified=True,
        email=actor.email,
        iat=int(time.time()),
        exp=int(time.time()) + 300,
    )
    session = SessionStore()
    session.create()
    factory = RequestFactory()
    start = factory.get("/app/auth1/elevate-sso/?return=/app/admin/")
    start.user, start.session = actor, session
    login(start, actor, backend="django.contrib.auth.backends.ModelBackend")
    session.save()
    response = step_up_sso.elevate_sso_start(start)
    assert response.status_code == 302
    session.save()
    query = parse_qs(urlparse(response["Location"]).query)
    now = int(time.time())
    oidc.claims = {
        "sub": identity.sub,
        "iss": oidc.issuer,
        "aud": "native-client",
        "nonce": query["nonce"][0],
        "iat": now,
        "exp": now + 300,
        "auth_time": now,
        "email": actor.email,
        "email_verified": True,
    }
    callback = factory.get(
        "/app/auth1/elevate-sso/callback/", {"state": query["state"][0], "code": "native-code"}
    )
    callback.user, callback.session = actor, SessionStore(session_key=session.session_key)
    from core import mutations

    original_audit_writer = mutations._audit_writer
    audits = []
    register_audit_writer(audits.append)
    try:
        yield SimpleNamespace(
            actor=actor, other=other, identity=identity, session=session, request=callback, audits=audits
        )
    finally:
        register_audit_writer(original_audit_writer)


def test_real_tls_oidc_signature_link_and_database_session_elevate_once(oidc, ceremony):
    response = step_up_sso.elevate_sso_callback(ceremony.request)
    assert response["Location"] == "/app/admin/"
    assert is_elevated(ceremony.request.session)
    assert is_elevated(SessionStore(session_key=ceremony.session.session_key))
    assert any(row.action == "auth.elevate_admin.sso.success" for row in ceremony.audits)
    assert oidc.calls == 1
    assert "stepUp=state_mismatch" in step_up_sso.elevate_sso_callback(ceremony.request)["Location"]
    assert oidc.calls == 1


@pytest.mark.parametrize(
    "case",
    [
        "different_subject",
        "missing_subject",
        "unlinked_identity",
        "changed_link",
        "changed_issuer",
        "inactive_actor",
        "different_actor",
        "different_session",
        "session_row_expired",
        "session_row_removed",
        "password_changed",
        "persisted_actor_changed",
        "missing_binding",
        "raw_claims",
        "bad_signature",
        "missing_nonce",
        "wrong_nonce",
        "nonce_opt_out",
        "wrong_issuer",
        "wrong_audience",
        "stale_auth_time",
        "future_auth_time",
        "boolean_auth_time",
        "infinite_auth_time",
        "missing_auth_time",
        "top_level_auth_time",
        "provider_failure",
    ],
)
def test_real_oidc_and_pg_refuse_unbound_or_unverified_proof(oidc, ceremony, case, caplog):
    claims = oidc.claims
    if case == "different_subject":
        claims["sub"] = "other-subject"
        UserInfo.objects.create(
            sub=claims["sub"],
            iss=oidc.issuer,
            internal_user=ceremony.other,
            updated_at=timezone.now(),
            email_verified=True,
            iat=int(time.time()),
            exp=int(time.time()) + 300,
        )
    elif case == "missing_subject":
        claims.pop("sub")
    elif case in {"unlinked_identity", "changed_link"}:
        ceremony.identity.internal_user = None if case == "unlinked_identity" else ceremony.other
        ceremony.identity.save(update_fields=["internal_user"])
    elif case == "changed_issuer":
        ceremony.identity.iss = "https://different.example.test"
        ceremony.identity.save(update_fields=["iss"])
    elif case == "inactive_actor":
        get_user_model().objects.filter(pk=ceremony.actor.pk).update(is_active=False)
    elif case == "different_actor":
        ceremony.request.user = ceremony.other
    elif case in {
        "session_row_expired",
        "session_row_removed",
        "password_changed",
        "persisted_actor_changed",
    }:
        # Authentication middleware may already have loaded the bag before a
        # concurrent withdrawal. Admission must read persistence again.
        dict(ceremony.request.session)
        if case == "session_row_expired":
            Session.objects.filter(session_key=ceremony.request.session.session_key).update(
                expire_date=timezone.now() - dt.timedelta(seconds=1)
            )
        elif case == "session_row_removed":
            Session.objects.filter(session_key=ceremony.request.session.session_key).delete()
        elif case == "password_changed":
            ceremony.actor.set_password("replacement-native-password")
            ceremony.actor.save(update_fields=["password"])
        else:
            current = SessionStore(session_key=ceremony.request.session.session_key)
            current[SESSION_KEY] = str(ceremony.other.pk)
            current[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
            current[HASH_SESSION_KEY] = ceremony.other.get_session_auth_hash()
            current.save()
    elif case == "different_session":
        replacement = SessionStore()
        replacement.update(dict(ceremony.session))
        replacement.create()
        ceremony.request.session = replacement
    elif case == "missing_binding":
        ceremony.request.session.pop(step_up_sso.SESSION_SSO_BINDING_KEY)
        ceremony.request.session.save()
    elif case == "raw_claims":
        oidc.raw_claims = True
    elif case == "bad_signature":
        oidc.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    elif case == "missing_nonce":
        claims.pop("nonce")
    elif case in {"wrong_nonce", "nonce_opt_out"}:
        claims["nonce"] = "different-nonce"
        if case == "nonce_opt_out":
            claims["nonce_supported"] = False
    elif case == "wrong_issuer":
        claims["iss"] = "https://different.example.test"
    elif case == "wrong_audience":
        claims["aud"] = "different-client"
    elif case == "stale_auth_time":
        claims["auth_time"] = int(time.time()) - 3600
    elif case == "future_auth_time":
        claims["auth_time"] = int(time.time()) + 3600
    elif case == "boolean_auth_time":
        claims["auth_time"] = True
    elif case == "infinite_auth_time":
        claims["auth_time"] = float("inf")
    elif case in {"missing_auth_time", "top_level_auth_time"}:
        claims.pop("auth_time")
        oidc.top_level_auth_time = case == "top_level_auth_time"
    elif case == "provider_failure":
        oidc.fail = True
    response = step_up_sso.elevate_sso_callback(ceremony.request)
    assert "stepUp=" in response["Location"]
    assert not is_elevated(ceremony.request.session)
    assert not is_elevated(SessionStore(session_key=ceremony.request.session.session_key))
    assert step_up_sso.SESSION_SSO_AUTH_TIME_KEY not in ceremony.request.session
    assert step_up_sso.SESSION_SSO_BINDING_KEY not in ceremony.request.session
    assert not any(row.decision == "ALLOW" for row in ceremony.audits)
    assert "PRIVATE_OIDC_CANARY" not in repr(ceremony.audits)
    assert "PRIVATE_OIDC_CANARY" not in repr([record.__dict__ for record in caplog.records])
