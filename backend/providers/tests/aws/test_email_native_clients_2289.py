"""Actual botocore credential resolution and signed requests on owned HTTP only."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import socket
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.credentials import SSOProvider
from botocore.exceptions import ConnectionClosedError, CredentialRetrievalError
from botocore.httpsession import URLLib3Session
from botocore.session import Session
from opentelemetry import context

from _sdk.cloud_credentials import CloudCredential, CredentialMode
from aws.email_delivery_clients import EmailNativeClientError, EmailNativeClients
from aws.session import clear_credential_cache

ACCOUNT = "123456789012"
REGION = "us-west-2"
PRIVATE = "synthetic-private-email-credential-marker"


class Withdrawn(ValueError):
    pass


@pytest.fixture
def wire(monkeypatch, tmp_path):
    import os

    for key in tuple(os.environ):
        if key.startswith("AWS_") or key.upper().endswith("_PROXY"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    clear_credential_cache()
    state = dict(
        requests=[],
        clients=[],
        allow=True,
        gates=0,
        reply_hook=None,
        metadata_error=False,
        stall=threading.Event(),
        release=threading.Event(),
        suppression=[],
        lose_send_reply=False,
    )

    def checkpoint():
        state["gates"] += 1
        if not state["allow"]:
            raise Withdrawn("CURRENT_SOURCE_WITHDRAWN")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, body, status=200, *, json_body=False):
            if state["stall"].is_set():
                state["release"].wait(timeout=4)
            hook, state["reply_hook"] = state["reply_hook"], None
            if hook:
                hook()
            payload = json.dumps(body).encode() if json_body else body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json" if json_body else "text/xml")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(payload)

        def do_PUT(self):
            state["requests"].append(("PUT", self.path, {}))
            assert self.path == "/latest/api/token"
            self.reply("fixture-imds-token")

        def do_GET(self):
            state["requests"].append(("GET", self.path, {key.lower(): value for key, value in self.headers.items()}))
            if state["metadata_error"]:
                self.reply({"private": PRIVATE}, 500, json_body=True)
            elif self.path == "/ecs" or self.path.endswith("/fixture-role"):
                self.reply(
                    dict(
                        AccessKeyId="fixture-access-" + PRIVATE,
                        SecretAccessKey=PRIVATE,
                        Token=PRIVATE,
                        Expiration="2030-01-01T00:00:00Z",
                        Code="Success",
                    ),
                    json_body=True,
                )
            elif self.path == "/latest/meta-data/iam/security-credentials/":
                self.reply("fixture-role")
            elif self.path.startswith("/federation/credentials"):
                self.reply(
                    {
                        "roleCredentials": dict(
                            accessKeyId="fixture-access-" + PRIVATE,
                            secretAccessKey=PRIVATE,
                            sessionToken=PRIVATE,
                            expiration=1893456000000,
                        )
                    },
                    json_body=True,
                )
            elif self.path.startswith("/v2/email/identities/"):
                self.reply(
                    {"VerifiedForSendingStatus": True, "Tags": [{"Key": "fixture", "Value": PRIVATE}]}, json_body=True
                )
            else:
                raise AssertionError("UNEXPECTED_OWNED_GET")

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
            if self.path == "/v2/email/outbound-emails":
                state["requests"].append(("POST", self.path, {"Action": ["SendEmail"]}))
                assert json.loads(raw)["FromEmailAddress"] == "sender@example.test"
                if state["lose_send_reply"]:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.reply({"MessageId": "fixture-native-message-id"}, json_body=True)
                return
            params = parse_qs(raw)
            state["requests"].append(("POST", self.path, params))
            action = params["Action"][0]
            if action in {"AssumeRole", "AssumeRoleWithWebIdentity"}:
                result = f"""<Credentials><AccessKeyId>fixture-access-{PRIVATE}</AccessKeyId>
                <SecretAccessKey>{PRIVATE}</SecretAccessKey><SessionToken>{PRIVATE}</SessionToken>
                <Expiration>2030-01-01T00:00:00Z</Expiration></Credentials><AssumedRoleUser>
                <AssumedRoleId>fixture:mail</AssumedRoleId><Arn>arn:aws:sts::{ACCOUNT}:assumed-role/fixture/mail</Arn>
                </AssumedRoleUser>"""
            else:
                assert action == "GetCallerIdentity"
                assert self.headers["Authorization"].startswith("AWS4-HMAC-SHA256")
                result = (
                    f"<Account>{ACCOUNT}</Account><Arn>arn:aws:iam::{ACCOUNT}:role/fixture</Arn>"
                    "<UserId>fixture</UserId>"
                )
            self.reply(
                f'<{action}Response xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
                f"<{action}Result>{result}</{action}Result><ResponseMetadata>"
                f"<RequestId>fixture-request</RequestId></ResponseMetadata></{action}Response>"
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    original_send, original_create = URLLib3Session.send, Session.create_client

    def send(transport, request):
        state["suppression"].append(context.get_value(context._SUPPRESS_INSTRUMENTATION_KEY))
        url = urlsplit(request.url)
        if url.hostname in {
            "sts.amazonaws.com",
            f"sts.{REGION}.amazonaws.com",
            f"email.{REGION}.amazonaws.com",
            f"portal.sso.{REGION}.amazonaws.com",
        }:
            request.url = origin + url.path + ("?" + url.query if url.query else "")
        elif url.hostname != "127.0.0.1" or url.port != server.server_port:
            pytest.fail("UNOWNED_NATIVE_NETWORK_REFUSED")
        return original_send(transport, request)

    def create(session, *args, **kwargs):
        client = original_create(session, *args, **kwargs)
        state["clients"].append(client)
        return client

    monkeypatch.setattr(URLLib3Session, "send", send)
    monkeypatch.setattr(Session, "create_client", create)
    state.update(checkpoint=checkpoint, origin=origin, tmp=tmp_path)
    try:
        yield state
    finally:
        state["release"].set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        clear_credential_cache()


def ambient(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fixture-access-" + PRIVATE)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", PRIVATE)


def owner(wire, credential=None):
    return EmailNativeClients(
        credential or CloudCredential("aws", declared_account=ACCOUNT), REGION, wire["checkpoint"]
    )


def actions(wire):
    return [params.get("Action", [path])[0] for _method, path, params in wire["requests"]]


def bounds(wire):
    assert wire["clients"]
    for client in wire["clients"]:
        config = client.meta.config
        assert (config.connect_timeout, config.read_timeout, config.retries["total_max_attempts"]) == (3, 5, 1)


def irsa(wire, monkeypatch):
    token = wire["tmp"] / "projected-token"
    token.write_text(PRIVATE)
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(token))
    monkeypatch.setenv("AWS_ROLE_ARN", f"arn:aws:iam::{ACCOUNT}:role/projected")
    return token


def test_signed_clients_ignore_process_endpoint_overrides(wire, monkeypatch):
    ambient(monkeypatch)
    for key in ("AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_STS", "AWS_ENDPOINT_URL_SESV2"):
        monkeypatch.setenv(key, "http://127.0.0.1:1/unowned")
    with owner(wire) as clients:
        assert clients.session._session.get_default_client_config().ignore_configured_endpoint_urls
        sts = clients.client("sts")
        assert sts.get_caller_identity()["Account"] == ACCOUNT
        assert clients.client("sesv2").get_email_identity(EmailIdentity="example.test")["VerifiedForSendingStatus"]
        assert clients.client("sts") is sts
    assert actions(wire) == ["GetCallerIdentity", "/v2/email/identities/example.test"]
    bounds(wire)


def test_projected_web_identity_refresh_reads_current_token(wire, monkeypatch):
    token = irsa(wire, monkeypatch)
    with owner(wire) as clients:
        sts = clients.client("sts")
        assert sts.get_caller_identity()["Account"] == ACCOUNT
        credentials = clients.session.get_credentials()
        credentials._refresh_using.__self__._cache.clear()
        credentials._expiry_time = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1)
        token.write_text(PRIVATE + "-rotated")
        assert sts.get_caller_identity()["Account"] == ACCOUNT
    assert actions(wire) == [
        "AssumeRoleWithWebIdentity",
        "GetCallerIdentity",
        "AssumeRoleWithWebIdentity",
        "GetCallerIdentity",
    ]
    assert wire["requests"][0][2]["WebIdentityToken"] == [PRIVATE]
    assert wire["requests"][2][2]["WebIdentityToken"] == [PRIVATE + "-rotated"]
    bounds(wire)


def test_withdrawal_after_projected_credential_reply_prevents_signed_call(wire, monkeypatch):
    irsa(wire, monkeypatch)
    with owner(wire) as clients:
        wire["reply_hook"] = lambda: wire.update(allow=False)
        with pytest.raises(Withdrawn):
            clients.client("sts").get_caller_identity()
    assert actions(wire) == ["AssumeRoleWithWebIdentity"]


def test_profile_role_nested_clients_inherit_bounds(wire, monkeypatch):
    (wire["tmp"] / "config").write_text(
        f"[profile mail]\nrole_arn=arn:aws:iam::{ACCOUNT}:role/profile\nsource_profile=source\n"
    )
    (wire["tmp"] / "credentials").write_text(
        f"[source]\naws_access_key_id=fixture-access-{PRIVATE}\naws_secret_access_key={PRIVATE}\n"
    )
    monkeypatch.setenv("AWS_PROFILE", "mail")
    with owner(wire) as clients:
        assert clients.client("sts").get_caller_identity()["Account"] == ACCOUNT
    assert actions(wire) == ["AssumeRole", "GetCallerIdentity"]
    bounds(wire)


def test_registered_role_retains_external_id(wire, monkeypatch):
    ambient(monkeypatch)
    credential = CloudCredential(
        "aws",
        mode=CredentialMode.AWS_ASSUME_ROLE,
        role_arn=f"arn:aws:iam::{ACCOUNT}:role/registered",
        external_id="fixture-external",
        declared_account=ACCOUNT,
    )
    with owner(wire, credential) as clients:
        assert clients.client("sts").get_caller_identity()["Account"] == ACCOUNT
    assert wire["requests"][0][2]["ExternalId"] == ["fixture-external"]
    assert actions(wire) == ["AssumeRole", "GetCallerIdentity"]
    bounds(wire)


@pytest.mark.parametrize("withdraw", [False, True])
def test_actual_container_credentials_are_gated_before_and_after(wire, monkeypatch, withdraw):
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", wire["origin"] + "/ecs")
    with owner(wire) as clients:
        if withdraw:
            wire["reply_hook"] = lambda: wire.update(allow=False)
            with pytest.raises(Withdrawn):
                clients.client("sts")
        else:
            assert clients.client("sts").get_caller_identity()["Account"] == ACCOUNT
            provider = clients.session._session.get_component("credential_provider").get_provider("container-role")
            assert (provider._fetcher.RETRY_ATTEMPTS, provider._fetcher.TIMEOUT_SECONDS) == (1, 2)
            bounds(wire)
    assert actions(wire) == (["/ecs"] if withdraw else ["/ecs", "GetCallerIdentity"])


def test_container_failed_response_is_not_retried(wire, monkeypatch):
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", wire["origin"] + "/ecs")
    wire["metadata_error"] = True
    with owner(wire) as clients, pytest.raises(CredentialRetrievalError):
        clients.client("sts")
    assert actions(wire) == ["/ecs"]


def test_imdsv2_uses_bounded_owned_metadata_transport(wire, monkeypatch):
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "false")
    monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", wire["origin"])
    with owner(wire) as clients:
        assert clients.client("sts").get_caller_identity()["Account"] == ACCOUNT
        native = clients.session._session
        assert native.get_config_variable("metadata_service_timeout") == 3
        assert native.get_config_variable("metadata_service_num_attempts") == 1
    assert [row[0] for row in wire["requests"]] == ["PUT", "GET", "GET", "POST"]
    assert wire["requests"][1][2]["x-aws-ec2-metadata-token"] == "fixture-imds-token"
    bounds(wire)


def test_sso_nested_refresh_client_uses_private_defaults(wire, monkeypatch):
    cache = wire["tmp"] / "sso"
    cache.mkdir()
    monkeypatch.setattr(SSOProvider, "_SSO_TOKEN_CACHE_DIR", str(cache))
    start = "https://fixture.awsapps.com/start"
    (cache / (hashlib.sha1(start.encode()).hexdigest() + ".json")).write_text(
        json.dumps(dict(accessToken=PRIVATE, expiresAt="2030-01-01T00:00:00Z"))
    )
    (wire["tmp"] / "config").write_text(
        f"[profile mail]\nsso_start_url={start}\nsso_region={REGION}\nsso_account_id={ACCOUNT}\nsso_role_name=fixture\n"
    )
    monkeypatch.setenv("AWS_PROFILE", "mail")
    with owner(wire) as clients:
        assert clients.client("sts").get_caller_identity()["Account"] == ACCOUNT
    assert actions(wire)[0].startswith("/federation/credentials")
    assert actions(wire)[1] == "GetCallerIdentity"
    bounds(wire)


@pytest.mark.parametrize("nested", [False, True])
def test_unbounded_process_provider_refuses_without_execution(wire, monkeypatch, nested):
    config = "[profile mail]\ncredential_process=/unavailable-fixture-process\n"
    if nested:
        config = (
            f"[profile mail]\nrole_arn=arn:aws:iam::{ACCOUNT}:role/profile\nsource_profile=source\n"
            "[profile source]\ncredential_process=/unavailable-fixture-process\n"
        )
    (wire["tmp"] / "config").write_text(config)
    monkeypatch.setenv("AWS_PROFILE", "mail")
    with owner(wire) as clients, pytest.raises(EmailNativeClientError, match="EMAIL_CREDENTIAL_PROVIDER_UNSUPPORTED"):
        clients.client("sts")
    assert wire["requests"] == []


def test_initial_withdrawal_and_retained_client_after_close_refuse(wire, monkeypatch):
    ambient(monkeypatch)
    wire["allow"] = False
    with pytest.raises(Withdrawn):
        owner(wire)
    assert wire["clients"] == []
    wire["allow"] = True
    clients = owner(wire)
    sts = clients.client("sts")
    clients.close()
    clients.close()
    with pytest.raises(EmailNativeClientError, match="EMAIL_CLIENTS_CLOSED"):
        sts.get_caller_identity()
    assert wire["requests"] == []


def test_debug_response_signing_and_token_logs_are_private(wire, monkeypatch, caplog, capsys):
    irsa(wire, monkeypatch)
    caplog.set_level(logging.DEBUG)
    with owner(wire) as clients:
        clients.client("sts").get_caller_identity()
        clients.client("sesv2").get_email_identity(EmailIdentity="example.test")
    logging.getLogger("botocore.fixture_outside_private").debug("unrelated-outside-private")
    captured = capsys.readouterr()
    assert PRIVATE not in caplog.text + repr([vars(row) for row in caplog.records]) + captured.out + captured.err
    assert "unrelated-outside-private" in caplog.text


def test_unknown_service_refuses_without_network(wire, monkeypatch):
    ambient(monkeypatch)
    with owner(wire) as clients, pytest.raises(EmailNativeClientError, match="EMAIL_CLIENT_SERVICE_UNSUPPORTED"):
        clients.client("s3")
    assert wire["requests"] == []


def test_real_stalled_container_reply_has_one_bounded_attempt(wire, monkeypatch):
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", wire["origin"] + "/ecs")
    wire["stall"].set()
    start = time.monotonic()
    with owner(wire) as clients, pytest.raises(CredentialRetrievalError):
        clients.client("sts")
    elapsed = time.monotonic() - start
    assert 1.5 < elapsed < 3.5
    assert actions(wire) == ["/ecs"]


def test_all_nested_clients_close_after_refresh_withdrawal(wire, monkeypatch):
    irsa(wire, monkeypatch)
    closed = []
    clients = owner(wire)
    sts = clients.client("sts")
    sts.get_caller_identity()
    for client in wire["clients"]:
        original = client.close

        def close(original=original, client=client):
            closed.append(client)
            original()

        monkeypatch.setattr(client, "close", close)
    credentials = clients.session.get_credentials()
    credentials._refresh_using.__self__._cache.clear()
    credentials._expiry_time = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1)
    wire["allow"] = False
    with pytest.raises(Withdrawn):
        sts.get_caller_identity()
    clients.close()
    assert len(closed) == len(wire["clients"]) == 2
    assert actions(wire) == ["AssumeRoleWithWebIdentity", "GetCallerIdentity"]


def test_native_transports_suppress_tracing_only_inside_private_scope(wire, monkeypatch):
    ambient(monkeypatch)
    assert not context.get_value(context._SUPPRESS_INSTRUMENTATION_KEY)
    with owner(wire) as clients:
        clients.client("sts").get_caller_identity()
        clients.client("sesv2").get_email_identity(EmailIdentity="example.test")
    assert wire["suppression"] == [True, True]
    assert not context.get_value(context._SUPPRESS_INSTRUMENTATION_KEY)


def test_selected_interactive_login_provider_refuses_without_subprocess(wire, monkeypatch):
    (wire["tmp"] / "config").write_text("[profile mail]\nlogin_session=fixture-login\n")
    monkeypatch.setenv("AWS_PROFILE", "mail")
    with owner(wire) as clients, pytest.raises(EmailNativeClientError, match="EMAIL_CREDENTIAL_PROVIDER_UNSUPPORTED"):
        clients.client("sts")
    assert wire["requests"] == []


SEND = {
    "FromEmailAddress": "sender@example.test",
    "Destination": {"ToAddresses": ["recipient@example.test"]},
    "Content": {"Simple": {"Subject": {"Data": "fixture"}, "Body": {"Text": {"Data": "fixture"}}}},
}


def test_acknowledged_send_retains_original_id_after_authority_withdrawal(wire, monkeypatch):
    ambient(monkeypatch)
    with owner(wire) as clients:
        ses = clients.client("sesv2")
        wire["reply_hook"] = lambda: wire.update(allow=False)
        # The driver may retain this original reply privately. Returning it
        # publicly still requires fresh caller/source admission in the driver.
        reply = ses.send_email(**SEND)
        assert reply["MessageId"] == "fixture-native-message-id"
        with pytest.raises(Withdrawn):
            ses.send_email(**SEND)
    assert actions(wire) == ["SendEmail"]


def test_lost_send_reply_is_one_attempt_and_cannot_be_called_after_withdrawal(wire, monkeypatch):
    ambient(monkeypatch)
    wire["lose_send_reply"] = True
    with owner(wire) as clients:
        ses = clients.client("sesv2")
        with pytest.raises(ConnectionClosedError):
            ses.send_email(**SEND)
        wire["allow"] = False
        with pytest.raises(Withdrawn):
            ses.send_email(**SEND)
    assert actions(wire) == ["SendEmail"]


@pytest.mark.parametrize("metadata", ["container-role", "iam-role"])
def test_metadata_connection_pools_are_owned_and_closed(wire, monkeypatch, metadata):
    if metadata == "container-role":
        monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", wire["origin"] + "/ecs")
    else:
        monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "false")
        monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", wire["origin"])
    clients = owner(wire)
    provider = clients.session._session.get_component("credential_provider").get_provider(metadata)
    fetcher = provider._fetcher if metadata == "container-role" else provider._role_fetcher
    transport = fetcher._session
    closed = []
    original = transport.close

    def close():
        closed.append(True)
        original()

    monkeypatch.setattr(transport, "close", close)
    assert clients.client("sts").get_caller_identity()["Account"] == ACCOUNT
    wire["allow"] = False
    clients.close()
    clients.close()
    assert closed == [True]
