"""Original HTTP operator + PostgreSQL + a real verified SMTP/TLS connection."""

import datetime
import hashlib
import ipaddress
import json
import logging
import socket
import ssl
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections

from astrolift_identity.models import Member, Organization
from astrolift_operations.models import InstallAlertMailTest, NotificationPreference

pytestmark = pytest.mark.django_db(transaction=True)
AUTH_MARKER = "private-smtp-auth-marker-2289"


@pytest.fixture
def smtp_wire(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cp, kp = tmp_path / "owned-ca.pem", tmp_path / "owned-key.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(cp))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cp, kp)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.2)
    state = {
        "port": listener.getsockname()[1],
        "commands": [],
        "messages": [],
        "auth_fail": False,
        "lost_reply": False,
        "on_command": None,
        "after_data": None,
        "connections": 0,
        "tls": 0,
        "starttls": False,
        "hang_reply": False,
        "after_welcome": None,
        "large_reply": False,
        "disconnected": threading.Event(),
    }
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                raw, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            state["connections"] += 1
            try:
                conn = raw if state["starttls"] else context.wrap_socket(raw, server_side=True)
                with conn:
                    if not state["starttls"]:
                        state["tls"] += 1
                    conn.settimeout(5)
                    stream = conn.makefile("rb")
                    if callback := state["after_welcome"]:
                        close_old_connections()
                        try:
                            callback()
                        finally:
                            close_old_connections()
                    conn.sendall(
                        b"220-" + b"a" * 600 + b"\r\n" if state["large_reply"] else b"220 owned SMTP test\r\n"
                    )
                    if state["large_reply"]:
                        for _ in range(63):
                            conn.sendall(b"220-" + b"a" * 600 + b"\r\n")
                    while line := stream.readline(4096):
                        verb = line.split(b" ", 1)[0].strip().decode("ascii").upper()
                        state["commands"].append(verb)
                        if callback := state["on_command"]:
                            close_old_connections()
                            try:
                                callback(verb)
                            finally:
                                close_old_connections()
                        if verb in {"EHLO", "HELO"}:
                            conn.sendall(b"250-owned\r\n250-STARTTLS\r\n250 AUTH PLAIN\r\n")
                        elif verb == "STARTTLS":
                            conn.sendall(b"220 switch to TLS\r\n")
                            stream.close()
                            conn = context.wrap_socket(conn, server_side=True)
                            conn.settimeout(5)
                            state["tls"] += 1
                            stream = conn.makefile("rb")
                        elif verb == "AUTH":
                            conn.sendall(
                                b"535 private-server-error-canary\r\n"
                                if state["auth_fail"]
                                else b"235 authenticated\r\n"
                            )
                        elif verb in {"MAIL", "RCPT", "RSET"}:
                            conn.sendall(b"250 accepted envelope\r\n")
                        elif verb == "DATA":
                            conn.sendall(b"354 send content\r\n")
                            data = bytearray()
                            while (chunk := stream.readline(16384)) != b".\r\n":
                                if not chunk:
                                    break
                                data.extend(chunk)
                            state["messages"].append(bytes(data))
                            if callback := state["after_data"]:
                                close_old_connections()
                                try:
                                    callback()
                                finally:
                                    close_old_connections()
                            if state["lost_reply"]:
                                break
                            if state["hang_reply"]:
                                stop.wait(6)
                                break
                            conn.sendall(b"250 owned queue identifier\r\n")
                        elif verb == "QUIT":
                            conn.sendall(b"221 bye\r\n")
                            break
                        else:
                            conn.sendall(b"500 invalid command\r\n")
                    stream.close()
                    conn.close()
                    state["disconnected"].set()
            except (OSError, ssl.SSLError):
                raw.close()
                state["disconnected"].set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=6)
        assert not thread.is_alive()


@pytest.fixture
def world(client, smtp_wire, settings):
    cache.clear()
    org = Organization.objects.create(name="Alert channel diagnostics", slug="alert-channel-2289")
    user = get_user_model().objects.create_superuser(
        username="alert-channel-operator", email="operator@example.test", password="fixture-only"
    )
    member = Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk)
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "localhost"
    settings.EMAIL_PORT = smtp_wire["port"]
    settings.EMAIL_USE_SSL = True
    settings.EMAIL_USE_TLS = False
    settings.EMAIL_HOST_USER = "owned-user"
    settings.EMAIL_HOST_PASSWORD = AUTH_MARKER
    settings.FROM_EMAIL = "notices@example.test"
    settings.EMAIL_SSL_KEYFILE = settings.EMAIL_SSL_CERTFILE = None
    from constance import config

    previous = config.EMAIL_NOTIFICATIONS
    config.EMAIL_NOTIFICATIONS = True
    client.force_login(user)
    try:
        yield SimpleNamespace(
            client=client, org=org, user=user, member=member, wire=smtp_wire, settings=settings
        )
    finally:
        config.EMAIL_NOTIFICATIONS = previous


def query(world, source, variables=None, **headers):
    response = world.client.post(
        "/app/gql/config/",
        content_type="application/json",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_PLATFORM="web",
        data=json.dumps({"query": source, "variables": variables or {}}),
        **headers,
    )
    assert response.status_code == 200
    return response.json()


def support(world):
    result = query(
        world,
        "{installAlertMailSupport{allowed reason transport sender recipient tlsMode sourceFingerprint}}",
    )
    assert not result.get("errors"), result
    return result["data"]["installAlertMailSupport"]


def send(world, nonce, fingerprint=None):
    if fingerprint is None:
        fingerprint = support(world)["sourceFingerprint"]
    result = query(
        world,
        "mutation($input:SendInstallAlertMailTestInput!){sendInstallAlertMailTest(input:$input){ok errors{code message} data{id requestId status sender recipient acceptedAt deliveryObserved reasonCode}}}",
        {
            "input": {
                "requestId": str(nonce),
                "expectedSourceFingerprint": fingerprint,
                "eventKind": "deploy.failed",
            }
        },
    )
    assert not result.get("errors"), result
    return result["data"]["sendInstallAlertMailTest"]


def test_native_tls_acceptance_original_intent_replay_and_content_free_history(world, caplog):
    observed = support(world)
    nonce = uuid4()
    first = send(world, nonce, observed["sourceFingerprint"])
    assert first["ok"] and first["data"]["status"] == "accepted"
    assert first["data"]["deliveryObserved"] is False
    assert world.wire["tls"] == 1
    assert len(world.wire["messages"]) == 1
    assert "Message-ID:" in world.wire["messages"][0].decode()
    second = send(world, nonce, observed["sourceFingerprint"])
    assert second["data"]["id"] == first["data"]["id"]
    assert len(world.wire["messages"]) == 1
    page = query(
        world,
        "{installAlertMailTestsPage{items{id status recipient deliveryObserved} totalCount nextCursor}}",
    )
    assert page["data"]["installAlertMailTestsPage"]["totalCount"] == 1
    fields = {f.name for f in InstallAlertMailTest._meta.fields}
    assert not fields.intersection({"subject", "body", "password", "host", "username"})
    assert AUTH_MARKER not in caplog.text
    assert "owned queue identifier" not in caplog.text


def test_lost_final_ack_never_resends_and_remains_unknown(world):
    nonce = uuid4()
    fingerprint = support(world)["sourceFingerprint"]
    world.wire["lost_reply"] = True
    result = send(world, nonce, fingerprint)
    assert result["data"]["status"] == "unknown"
    assert result["data"]["acceptedAt"] is None
    assert len(world.wire["messages"]) == 1
    world.wire["lost_reply"] = False
    repeated = send(world, nonce, fingerprint)
    assert repeated["data"]["status"] == "unknown"
    assert len(world.wire["messages"]) == 1


def test_withdrawal_after_acceptance_retains_private_ack_and_refuses_public_dto(world):
    world.wire["after_data"] = lambda: Member.objects.filter(pk=world.member.pk).update(is_active=False)
    result = send(world, uuid4())
    assert not result["ok"] and result["data"] is None
    row = InstallAlertMailTest.objects.get()
    assert row.status == "accepted" and row.accepted_at is not None
    assert len(world.wire["messages"]) == 1


def test_fresh_membership_withdrawal_before_mail_has_zero_data_effects(world):
    world.wire["on_command"] = (
        lambda verb: Member.objects.filter(pk=world.member.pk).update(is_active=False)
        if verb == "EHLO"
        else None
    )
    result = send(world, uuid4())
    assert not result["ok"]
    assert world.wire["messages"] == []
    assert "MAIL" not in world.wire["commands"]


def test_current_source_change_before_mail_refuses(world):
    world.wire["on_command"] = (
        lambda verb: setattr(world.settings, "EMAIL_HOST_PASSWORD", "rotated-private-source")
        if verb == "EHLO"
        else None
    )
    result = send(world, uuid4())
    assert not result["ok"] and result["errors"][0]["message"] == "ALERT_MAIL_SOURCE_CHANGED"
    assert world.wire["messages"] == []


def test_auth_failure_is_fixed_private_metadata_and_never_data(world, caplog):
    world.wire["auth_fail"] = True
    result = send(world, uuid4())
    assert result["data"]["status"] == "failed"
    assert result["data"]["reasonCode"] == "ALERT_MAIL_AUTHENTICATION_FAILED"
    assert world.wire["messages"] == []
    assert "private-server-error-canary" not in json.dumps(result) + caplog.text
    assert AUTH_MARKER not in json.dumps(result) + caplog.text


@pytest.mark.parametrize("mode", ["unsupported", "plaintext", "flag", "preference", "member", "operator"])
def test_admission_refusals_precede_any_connection(world, mode):
    if mode == "unsupported":
        world.settings.EMAIL_BACKEND = "django_ses.SESBackend"
    elif mode == "plaintext":
        world.settings.EMAIL_USE_SSL = False
    elif mode == "flag":
        from constance import config

        config.EMAIL_NOTIFICATIONS = False
    elif mode == "preference":
        NotificationPreference.objects.create(
            user=world.user, channel="email", event_kind="deploy.failed", enabled=False
        )
    elif mode == "member":
        world.member.is_active = False
        world.member.save()
    else:
        world.user.is_superuser = False
        world.user.save()
    result = query(world, "{installAlertMailSupport{allowed reason sourceFingerprint}}")
    assert result.get("errors") or result["data"]["installAlertMailSupport"]["allowed"] is False
    assert world.wire["connections"] == 0
    assert InstallAlertMailTest.objects.count() == 0


def test_stale_source_fingerprint_before_reservation_refuses(world):
    observed = support(world)
    world.settings.EMAIL_HOST_PASSWORD = "changed-before-dispatch"
    assert not send(world, uuid4(), observed["sourceFingerprint"])["ok"]
    assert InstallAlertMailTest.objects.count() == 0
    assert world.wire["connections"] == 0
    assert hashlib.sha256(AUTH_MARKER.encode()).hexdigest() != observed["sourceFingerprint"]


def bearer(world, scopes=("admin",)):
    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import ApiToken

    secret = mint_token()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="Install alert diagnostic operator",
        token_hash=secret.token_hash,
        token_last_4=secret.last4,
        scopes=list(scopes),
    )
    world.client.logout()
    world.client.defaults["HTTP_AUTHORIZATION"] = "Bearer " + secret.plaintext
    return token


def test_native_starttls_requires_verified_upgrade_before_auth_or_data(world):
    world.wire["starttls"] = True
    world.settings.EMAIL_USE_TLS, world.settings.EMAIL_USE_SSL = True, False
    result = send(world, uuid4())
    assert result["ok"] and result["data"]["status"] == "accepted"
    assert world.wire["tls"] == 1
    assert world.wire["commands"].index("STARTTLS") < world.wire["commands"].index("AUTH")
    assert len(world.wire["messages"]) == 1


def test_native_untrusted_certificate_refuses_before_auth_and_data(world, monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE")
    result = send(world, uuid4())
    assert result["ok"] and result["data"]["status"] == "failed"
    assert result["data"]["reasonCode"] == "ALERT_MAIL_TRANSPORT_UNCONFIRMED"
    assert "AUTH" not in world.wire["commands"] and not world.wire["messages"]


def test_native_final_reply_stall_is_unknown_and_bounded_without_resend(world):
    world.wire["hang_reply"] = True
    nonce = uuid4()
    fingerprint = support(world)["sourceFingerprint"]
    started = time.monotonic()
    result = send(world, nonce, fingerprint)
    elapsed = time.monotonic() - started
    assert 4 <= elapsed < 15
    assert result["data"]["status"] == "unknown" and len(world.wire["messages"]) == 1
    repeated = send(world, nonce, fingerprint)
    assert repeated["data"]["status"] == "unknown" and len(world.wire["messages"]) == 1


@pytest.mark.parametrize("withdrawal", ["revoked", "scope"])
def test_native_bearer_withdrawal_after_ehlo_precedes_data(world, withdrawal):
    token = bearer(world)

    def withdraw(verb):
        if verb == "EHLO":
            if withdrawal == "revoked":
                token.is_revoked = True
            else:
                token.scopes = ["read:apps"]
            token.save()

    world.wire["on_command"] = withdraw
    result = send(world, uuid4())
    assert not result["ok"] and not world.wire["messages"]
    assert "MAIL" not in world.wire["commands"]


def test_read_only_bearer_has_no_install_diagnostic_authority(world):
    bearer(world, ["read:apps"])
    result = query(world, "{installAlertMailSupport{allowed}}")
    assert result.get("errors")
    assert not world.wire["connections"] and not InstallAlertMailTest.objects.exists()


def test_foreign_selected_organization_denied_before_any_transport(world):
    world.org = Organization.objects.create(name="Unrelated organization", slug="alert-unrelated")
    result = query(world, "{installAlertMailSupport{allowed}}")
    assert result.get("errors")
    assert not world.wire["connections"]


def test_original_nonce_cannot_be_reused_by_another_current_operator(world):
    nonce = uuid4()
    original = send(world, nonce)
    other = get_user_model().objects.create_superuser(
        username="other-mail-operator", email="other@example.test", password="fixture-only"
    )
    Member.objects.create(user=other, scope_kind="ORG", scope_id=world.org.pk)
    world.client.force_login(other)
    result = send(world, nonce)
    assert not result["ok"] and result["errors"][0]["message"] == "ALERT_MAIL_INTENT_CONFLICT"
    assert InstallAlertMailTest.objects.get().recipient == original["data"]["recipient"]
    assert len(world.wire["messages"]) == 1


def test_replay_under_source_rotation_refuses_and_history_still_reports_original_acceptance(world):
    nonce = uuid4()
    fingerprint = support(world)["sourceFingerprint"]
    send(world, nonce, fingerprint)
    world.settings.EMAIL_HOST_PASSWORD = "source-rotated-after-acceptance"
    result = send(world, nonce, fingerprint)
    assert not result["ok"] and len(world.wire["messages"]) == 1
    result = query(world, "{installAlertMailTestsPage{items{status deliveryObserved}}}")
    assert result["data"]["installAlertMailTestsPage"]["items"] == [
        {"status": "accepted", "deliveryObserved": False}
    ]


def test_current_preference_withdrawal_before_data_refuses(world):
    def withdraw(verb):
        if verb == "EHLO":
            NotificationPreference.objects.create(
                user=world.user, channel="email", event_kind="deploy.failed", enabled=False
            )

    world.wire["on_command"] = withdraw
    result = send(world, uuid4())
    assert not result["ok"] and not world.wire["messages"]


def test_native_source_withdrawal_after_data_retains_acceptance_then_refuses(world):
    world.wire["after_data"] = lambda: setattr(world.settings, "EMAIL_HOST_PASSWORD", "post-data-rotation")
    result = send(world, uuid4())
    assert not result["ok"] and result["data"] is None
    row = InstallAlertMailTest.objects.get()
    assert row.status == "accepted" and row.accepted_at is not None


def test_owned_history_has_real_keyset_pages_without_row_fanout(world):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    InstallAlertMailTest.objects.bulk_create(
        [
            InstallAlertMailTest(
                organization=world.org,
                requester=world.user,
                request_id=uuid4(),
                event_kind="deploy.failed",
                source_sha256="a" * 64,
                sender="notices@example.test",
                recipient=world.user.email,
                status="unknown",
            )
            for _ in range(51)
        ]
    )
    document = "query($after:String,$limit:Int!){installAlertMailTestsPage(after:$after,limit:$limit){items{id status} totalCount nextCursor}}"
    with CaptureQueriesContext(connection) as queries:
        first = query(world, document, {"after": None, "limit": 1})
    small = len(queries)
    with CaptureQueriesContext(connection) as queries:
        page = query(world, document, {"after": None, "limit": 50})
    large = len(queries)
    assert large <= small + 1
    page = page["data"]["installAlertMailTestsPage"]
    assert len(page["items"]) == 50 and page["totalCount"] == 51
    final = query(world, document, {"after": page["nextCursor"], "limit": 50})["data"][
        "installAlertMailTestsPage"
    ]
    assert len(final["items"]) == 1 and final["nextCursor"] is None
    assert first["data"]["installAlertMailTestsPage"]["items"][0] == page["items"][0]
    assert not world.wire["connections"]


def test_actual_migration_preserves_retained_intents_then_empty_roundtrip(world):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    original_targets = executor.loader.graph.leaf_nodes()
    applied = set(executor.loader.applied_migrations)
    row = InstallAlertMailTest.objects.create(
        organization=world.org,
        requester=world.user,
        request_id=uuid4(),
        event_kind="deploy.failed",
        source_sha256="a" * 64,
        sender="notices@example.test",
        recipient=world.user.email,
        status="unknown",
    )
    try:
        with pytest.raises(RuntimeError, match="Retained install alert mail"):
            executor.migrate([("astrolift_operations", "0029_preview_log_export_source")])
        assert set(MigrationExecutor(connection).loader.applied_migrations) == applied
        assert InstallAlertMailTest._base_manager.get(pk=row.pk).status == "unknown"
        row.soft_delete()
        with pytest.raises(RuntimeError, match="Retained install alert mail"):
            MigrationExecutor(connection).migrate(
                [("astrolift_operations", "0029_preview_log_export_source")]
            )
        InstallAlertMailTest._base_manager.filter(pk=row.pk).delete()  # owned synthetic fixture only
        MigrationExecutor(connection).migrate([("astrolift_operations", "0029_preview_log_export_source")])
        assert "astrolift_operations_installalertmailtest" not in connection.introspection.table_names()
        MigrationExecutor(connection).migrate(original_targets)
        assert set(MigrationExecutor(connection).loader.applied_migrations) == applied
        assert not InstallAlertMailTest.objects.exists()
    finally:
        MigrationExecutor(connection).migrate(original_targets)


def test_constructor_withdrawal_closes_unretained_native_connection(world):
    world.wire["after_welcome"] = lambda: Member.objects.filter(pk=world.member.pk).update(is_active=False)
    result = send(world, uuid4())
    assert not result["ok"] and world.wire["messages"] == []
    assert world.wire["disconnected"].wait(1)
    assert world.wire["commands"] == []


@pytest.mark.parametrize("withdrawal", ["revoked", "expired", "deleted"])
def test_current_browser_sidecar_withdrawal_is_not_revived_after_response(world, withdrawal):
    from django.utils import timezone

    from astrolift_identity.models import AstroliftSession

    support(world)  # real HTTP installs the healthy browser sidecar
    key = world.client.session.session_key
    sidecar = AstroliftSession.all_objects.get(session_key=key)
    stamp = timezone.now() - datetime.timedelta(seconds=1)
    field = {"revoked": "revoked_at", "expired": "expires_at", "deleted": "deleted_at"}[withdrawal]

    def withdraw(verb):
        if verb == "EHLO":
            AstroliftSession.all_objects.filter(pk=sidecar.pk).update(**{field: stamp})

    world.wire["on_command"] = withdraw
    result = send(world, uuid4())
    assert not result["ok"] and not world.wire["messages"]
    sidecar.refresh_from_db()
    assert getattr(sidecar, field) == stamp
    assert "MAIL" not in world.wire["commands"]


def test_ordinary_notice_composer_native_failure_omits_body_subject_and_response(world, caplog, capfd):
    from astrolift_operations.notification_email import send_notice_email

    world.wire["auth_fail"] = True
    # This production logger has its own non-propagating handler and keeps
    # its original stdout stream. Observe actual records at that logger.
    logger = logging.getLogger("astrolift_operations.notification_email")
    logger.addHandler(caplog.handler)
    try:
        sent = send_notice_email(
            to=[world.user.email],
            subject="private-notice-subject-marker",
            text_body="private-notice-body-marker",
            html_body="<p>private-notice-body-marker</p>",
        )
    finally:
        logger.removeHandler(caplog.handler)
    assert sent == 0
    captured = capfd.readouterr()
    formatted = caplog.text + captured.out + captured.err + str([vars(record) for record in caplog.records])
    assert "notice email transport failed" in formatted
    assert "private-notice" not in formatted
    assert "private-server-error-canary" not in formatted
    assert AUTH_MARKER not in formatted


def test_malformed_private_source_is_unsupported_without_a_connection(world):
    world.settings.EMAIL_HOST_PASSWORD = {"private": "invalid-settings"}
    result = support(world)
    assert result["allowed"] is False and result["reason"] == "ALERT_MAIL_SOURCE_UNAVAILABLE"
    assert not world.wire["connections"]


def test_concurrent_original_nonce_during_final_reply_does_not_submit_again(world):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections
    from django.test import Client

    nonce = uuid4()
    fingerprint = support(world)["sourceFingerprint"]
    held, release = threading.Event(), threading.Event()
    world.wire["after_data"] = lambda: (held.set(), release.wait(10))
    original_client = world.client
    duplicate = Client()
    duplicate.force_login(world.user)
    second_world = SimpleNamespace(**{**vars(world), "client": duplicate})

    def original():
        try:
            return send(world, nonce, fingerprint)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(original)
        try:
            assert held.wait(10)
            replay = send(second_world, nonce, fingerprint)
            assert replay["ok"] and replay["data"]["status"] == "sent"
            assert len(world.wire["messages"]) == 1 and world.wire["connections"] == 1
        finally:
            release.set()
        result = first.result(timeout=10)
    world.client = original_client
    assert result["data"]["status"] == "accepted"
    assert InstallAlertMailTest.objects.count() == 1


def test_current_history_excludes_other_original_callers_and_foreign_orgs(world):
    other = get_user_model().objects.create_superuser(
        username="history-other", email="other@example.test", password="fixture-only"
    )
    foreign = Organization.objects.create(name="Other history tenant", slug="alert-history-other")
    rows = []
    for org, actor in ((world.org, world.user), (world.org, other), (foreign, world.user)):
        rows.append(
            InstallAlertMailTest.objects.create(
                organization=org,
                requester=actor,
                request_id=uuid4(),
                event_kind="deploy.failed",
                source_sha256="a" * 64,
                sender="notices@example.test",
                recipient=actor.email,
                status="unknown",
            )
        )
    page = query(world, "{installAlertMailTestsPage{items{id recipient} totalCount}}")
    assert page["data"]["installAlertMailTestsPage"] == {
        "items": [{"id": str(rows[0].guid), "recipient": world.user.email}],
        "totalCount": 1,
    }
    assert not world.wire["connections"]


def test_multiline_native_reply_limit_precedes_credentials_and_data(world, capfd):
    world.wire["large_reply"] = True
    result = send(world, uuid4())
    assert result["ok"] and result["data"]["status"] == "failed"
    assert result["data"]["reasonCode"] == "ALERT_MAIL_RESPONSE_LIMIT"
    assert "AUTH" not in world.wire["commands"] and not world.wire["messages"]
    captured = capfd.readouterr()
    assert "a" * 600 not in json.dumps(result) + captured.out + captured.err


def test_actual_own_mailbox_change_before_data_refuses_original_target(world):
    def change(verb):
        if verb == "EHLO":
            get_user_model().objects.filter(pk=world.user.pk).update(email="changed@example.test")

    world.wire["on_command"] = change
    result = send(world, uuid4())
    assert not result["ok"] and not world.wire["messages"]
    row = InstallAlertMailTest.objects.get()
    assert row.recipient == "operator@example.test"
