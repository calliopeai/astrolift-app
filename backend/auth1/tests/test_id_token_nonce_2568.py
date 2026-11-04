"""The login callback refuses an ID token minted for another login (#2568).

auth1's callback leaves the ID token to authlib: its signature, issuer,
audience, and the nonce saved when the login began. Through 1.8.0 authlib
dropped that nonce whenever the token itself carried ``nonce_supported: false``
(GHSA-c5mf-xmp2-59rc), so a token issued for some other login signed in here.

These tests run the real login and callback views on Postgres against a local
TLS identity provider that signs real ID tokens, so authlib's own checks decide.
"""

from __future__ import annotations

import datetime as dt
import json
import ssl
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from authlib.integrations.django_client import OAuth
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from auth1.models import UserInfo
from auth1.sessions import Auth1SessionWorkflow

pytestmark = pytest.mark.django_db
User = get_user_model()

FRONTEND = "https://astro.example"
CLIENT_ID = "nonce-client"


@pytest.fixture(autouse=True)
def isolated(settings):
    settings.FRONTEND_URL = FRONTEND
    settings.CORS_ALLOWED_ORIGINS = [FRONTEND]
    settings.CACHES = {
        **settings.CACHES,
        "nonce_ratelimit": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": str(uuid.uuid4()),
        },
    }
    settings.RATELIMIT_USE_CACHE = "nonce_ratelimit"


@pytest.fixture
def idp(tmp_path, monkeypatch):
    """An identity provider on localhost TLS whose token endpoint signs ``idp.claims``."""
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
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    public.update(kid="nonce-key", alg="RS256", use="sig")
    wire = SimpleNamespace(claims={})

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
            id_token = jwt.encode(wire.claims, pem, algorithm="RS256", headers={"kid": "nonce-key"})
            self.send_json(
                {"access_token": "nonce-access-token", "token_type": "Bearer", "id_token": id_token}
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    wire.issuer = f"https://localhost:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    oauth = OAuth()
    oauth.register(
        "auth0",
        client_id=CLIENT_ID,
        client_secret="nonce-client-secret",
        client_kwargs={"scope": "openid email profile", "verify": str(cert_path)},
        server_metadata_url=wire.issuer + "/.well-known/openid-configuration",
    )
    monkeypatch.setattr(Auth1SessionWorkflow, "_client", oauth)
    try:
        yield wire
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _begin_login(client: Client) -> tuple[str, str]:
    """Start a login; return the state and the nonce authlib saved for it."""
    response = client.get(reverse("login"), {"next": f"{FRONTEND}/auth/callback"})
    query = parse_qs(urlsplit(response["Location"]).query)
    return query["state"][0], query["nonce"][0]


def _id_token(idp, user, nonce, **extra) -> dict:
    now = int(time.time())
    return {
        "sub": f"sub-{uuid.uuid4().hex}",
        "iss": idp.issuer,
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 300,
        "nonce": nonce,
        "email": user.email,
        "email_verified": True,
        **extra,
    }


def _owner() -> User:
    name = f"owner-{uuid.uuid4().hex[:6]}"
    return User.objects.create(username=name, email=f"{name}@acme.example")


def test_an_id_token_for_this_login_signs_in(idp):
    owner = _owner()
    client = Client()
    state, nonce = _begin_login(client)
    idp.claims = _id_token(idp, owner, nonce)

    response = client.get(reverse("callback"), {"state": state, "code": "provider-code"})

    assert set(parse_qs(urlsplit(response["Location"]).query)) == {"code"}
    assert client.session["_auth_user_id"] == str(owner.pk)


@pytest.mark.parametrize("opt_out", [{}, {"nonce_supported": False}], ids=["plain", "nonce_supported_false"])
def test_an_id_token_for_another_login_does_not_sign_in(idp, opt_out):
    owner = _owner()
    client = Client(raise_request_exception=False)
    state, _ = _begin_login(client)
    idp.claims = _id_token(idp, owner, "nonce-of-another-login", **opt_out)

    response = client.get(reverse("callback"), {"state": state, "code": "provider-code"})

    assert response.status_code == 500
    assert "_auth_user_id" not in client.session
    assert not UserInfo.objects.exists()
