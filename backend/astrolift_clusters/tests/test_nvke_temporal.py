from __future__ import annotations

import asyncio
import socket
import ssl
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
from asgiref.sync import sync_to_async
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from django.utils import timezone

from astrolift_clusters import nvke_temporal as native
from astrolift_clusters.models import NvkeTemporalAuthority
from astrolift_clusters.nvke_sites import NvkeSiteError
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context, create, put
from astrolift_clusters.tests.test_nvke_sites import world as _world
from astrolift_identity import abac
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, RoleBinding
from core.permissions import PermissionDenied
from core.schema.audit import MutationAuditLog
from core.testing.temporal import temporal_test_env

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return _world.__wrapped__()


@pytest.fixture
def site(world):
    with context(world):
        return create()


def review(world, site, row):
    return {
        "site_id": site.guid,
        "expected_actor_id": world.user.pk,
        "expected_site_version": site.version,
        "expected_version": row.version,
    }


def configure(world, site, row=None, **overrides):
    values = {
        "site_id": site.guid,
        "expected_actor_id": world.user.pk,
        "expected_site_version": site.version,
        "expected_version": row.version if row else 0,
        "address": row.address if row else "native.example.test:7233",
        "namespace": row.namespace if row else "native-owned",
        "task_queue": row.task_queue if row else "native-workers",
        "server_name": row.server_name if row else "",
        "use_tls": row.use_tls if row else True,
        "timeout_seconds": row.timeout_seconds if row else 30,
    }
    values.update(overrides)
    with context(world):
        result = native.configure(**values)
    site.refresh_from_db()
    return result


def test_native_authority_is_separate_encrypted_and_version_fenced(world, site, monkeypatch):
    with context(world):
        put(site, credentials={"token": "PRIVATE_HTTP_CREDENTIAL"})
        site.refresh_from_db()
    row = configure(world, site)
    assert native.credentials_for(row) == {}
    row = configure(world, site, row, credentials={"api_key": "PRIVATE_NATIVE_KEY"})
    assert b"PRIVATE_NATIVE_KEY" not in bytes(row.credential_ciphertext)
    assert native.credentials_for(row) == {"api_key": "PRIVATE_NATIVE_KEY"}
    old = bytes(row.credential_ciphertext)
    with monkeypatch.context() as private:

        def no_decrypt(_row):
            pytest.fail("preserving native credentials must not decrypt them")

        private.setattr(native, "credentials_for", no_decrypt)
        preserved = configure(world, site, row, credentials=None)
    assert bytes(preserved.credential_ciphertext) == old
    with pytest.raises(NvkeSiteError) as stale:
        configure(world, site, row, credentials={"api_key": "STALE_SECRET"})
    assert stale.value.code == "VERSION_MISMATCH"
    cleared = configure(world, site, preserved, credentials={})
    assert native.credentials_for(cleared) == {}


def test_disable_remove_and_replacement_never_change_logical_identity(world, site):
    row = configure(world, site)
    with context(world):
        disabled = native.set_enabled(**review(world, site, row), enabled=False)
        site.refresh_from_db()
        assert disabled.is_enabled is False
        removed = native.set_enabled(**review(world, site, disabled), remove=True)
        site.refresh_from_db()
        assert removed.deleted_at and not removed.is_enabled
        assert native.read(site.guid) is None
    replacement = configure(world, site)
    assert replacement.guid != row.guid
    assert NvkeTemporalAuthority.all_objects.filter(site=site).count() == 2
    other = _world.__wrapped__()
    with context(other):
        other_site = create()
    replacement.site = other_site
    with pytest.raises(ValueError, match="cannot change"):
        replacement.save()


@pytest.mark.parametrize(
    "changed",
    [
        {"address": "https://PRIVATE_USER:PRIVATE_KEY@native.test:7233"},
        {"address": "native.test"},
        {"address": "native.test:0"},
        {"address": "native.test:7233/path"},
        {"address": "native.test:7233?secret=PRIVATE_KEY"},
        {"namespace": ""},
        {"task_queue": "bad\nqueue"},
        {"credentials": {"token": "PRIVATE_HTTP_TOKEN"}},
        {"credentials": {"api_key": "bad\nkey"}},
        {"credentials": {"client_certificate_pem": "PRIVATE_CERT"}},
        {"use_tls": False, "credentials": {"api_key": "PRIVATE_KEY"}},
        {"timeout_seconds": True},
    ],
)
def test_invalid_native_configuration_is_rejected_without_secrets(world, site, changed):
    with pytest.raises(NvkeSiteError) as error:
        configure(world, site, **changed)
    assert error.value.code == "INVALID_INPUT"
    assert "PRIVATE" not in str(error.value)
    assert not NvkeTemporalAuthority.objects.exists()


def test_plaintext_downgrade_requires_explicit_credential_clear(world, site):
    row = configure(world, site, credentials={"api_key": "PRIVATE_NATIVE_KEY"})
    with pytest.raises(NvkeSiteError):
        configure(world, site, row, use_tls=False)
    plain = configure(world, site, row, use_tls=False, credentials={})
    assert not plain.use_tls and not plain.credential_ciphertext


def test_wrong_actor_foreign_site_and_revoked_permission_cannot_configure(world, site):
    with pytest.raises(NvkeSiteError) as actor:
        configure(world, site, expected_actor_id=world.user.pk + 1)
    assert actor.value.code == "SOURCE_CHANGED"
    foreign = _world.__wrapped__()
    with pytest.raises(NvkeSiteError) as hidden:
        configure(foreign, site)
    assert hidden.value.code == "NOT_FOUND"
    RoleBinding.objects.filter(user=world.user).update(deleted_at=timezone.now())
    with pytest.raises(PermissionDenied):
        configure(world, site)
    assert not NvkeTemporalAuthority.objects.exists()


def test_graphql_native_credentials_and_rejected_address_are_redacted(world, site):
    query = """mutation($unrelated:ConfigureNvkeTemporalAuthorityInput!){configureNvkeTemporalAuthority(input:$unrelated){ok errors{code} data{id version siteVersion namespace taskQueue hasCredentials useTls}}}"""
    variables = {
        "unrelated": {
            "siteId": str(site.guid),
            "expectedActorId": world.user.pk,
            "expectedSiteVersion": site.version,
            "expectedVersion": 0,
            "address": "native.test:7233",
            "namespace": "native-owned",
            "taskQueue": "native-workers",
            "credentials": {"apiKey": "PRIVATE_API_KEY"},
        }
    }
    result = execute(world, query, variables)
    assert result.errors is None and result.data["configureNvkeTemporalAuthority"]["ok"]
    log = MutationAuditLog.objects.filter(operation="nvke.temporal_configure").latest("timestamp")
    assert "PRIVATE" not in str(log.variables)
    assert log.variables["unrelated"]["credentials"] == "***REDACTED***"
    site.refresh_from_db()
    variables["unrelated"].update(
        expectedSiteVersion=site.version,
        expectedVersion=1,
        address="PRIVATE_USER:PRIVATE_KEY@native.test:7233",
    )
    rejected = execute(world, query, variables)
    assert rejected.errors is None and not rejected.data["configureNvkeTemporalAuthority"]["ok"]
    log = MutationAuditLog.objects.filter(operation="nvke.temporal_configure").latest("timestamp")
    assert "PRIVATE" not in str(log.variables)
    catalog = execute(
        world, "{astroliftNvkeContract{actions{key} nativeWorkflows{name inputType permission inputSchema}}}"
    )
    assert catalog.errors is None
    assert len(catalog.data["astroliftNvkeContract"]["actions"]) == 88
    assert len(catalog.data["astroliftNvkeContract"]["nativeWorkflows"]) == 3


def test_cached_admin_decisions_and_revoked_tokens_do_not_authorize_native_io(world, site):
    with context(world), abac.request_attributes(abac.attributes_for(world.user.pk)):
        native._actor(world.user.pk)
        RoleBinding.objects.filter(user=world.user).update(deleted_at=timezone.now())
        with pytest.raises(PermissionDenied):
            native._actor(world.user.pk)
    other = _world.__wrapped__()
    issued = mint_token()
    token = ApiToken.objects.create(
        user=other.user,
        organization=other.org,
        name="native test caller",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["admin"],
    )
    mark = set_current_api_token(token)
    try:
        with context(other):
            native._actor(other.user.pk)
            ApiToken.objects.filter(pk=token.pk).update(scopes=[])
            with pytest.raises(PermissionDenied):
                native._actor(other.user.pk)
            ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
            with pytest.raises(NvkeSiteError) as revoked:
                native._actor(other.user.pk)
            assert revoked.value.code == "AUTHORITY_REVOKED"
    finally:
        reset_current_api_token(mark)


@pytest.mark.parametrize(
    "changed", [{"address": ""}, {"namespace": ""}, {"task_queue": ""}, {"timeout_seconds": 0}]
)
@pytest.mark.django_db(transaction=True)
async def test_factory_refuses_invalid_stored_authority_before_decryption(world, site, monkeypatch, changed):
    row = await sync_to_async(configure)(world, site)
    for field, value in changed.items():
        setattr(row, field, value)

    def no_decrypt(_row):
        pytest.fail("invalid native authority must not decrypt credentials or connect")

    monkeypatch.setattr(native, "credentials_for", no_decrypt)
    with pytest.raises(NvkeSiteError) as invalid:
        await native.connect_native(row, lambda: None)
    assert invalid.value.code == "INVALID_INPUT"


@pytest.mark.django_db(transaction=True)
async def test_real_native_namespace_verification_never_falls_back_to_astro(world, site, settings):
    async with temporal_test_env() as env:
        address, namespace = env.client.service_client.config.target_host, env.client.namespace
        assert ":" in address
        row = await sync_to_async(configure)(world, site, address=address, namespace=namespace, use_tls=False)
        with context(world):
            verified = await sync_to_async(native.verify)(**review(world, site, row))
        assert verified.verification_code == "NAMESPACE_VERIFIED" and verified.verified_at
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        closed_address = f"127.0.0.1:{sock.getsockname()[1]}"
        sock.close()
        row = await sync_to_async(configure)(
            world, site, row, address=closed_address, namespace=namespace, use_tls=False, timeout_seconds=1
        )
        settings.TEMPORAL_ADDRESS = address
        with context(world), pytest.raises(NvkeSiteError) as unavailable:
            await sync_to_async(native.verify)(**review(world, site, row))
        assert unavailable.value.code == "NATIVE_TEMPORAL_UNAVAILABLE"
        assert row.verification_code == "" and row.verified_at is None


def certificates(tmp_path):
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NVKE owned test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    result = {"ca": ca.public_bytes(serialization.Encoding.PEM)}
    for name, usage in (
        ("server", ExtendedKeyUsageOID.SERVER_AUTH),
        ("client", ExtendedKeyUsageOID.CLIENT_AUTH),
    ):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "native-temporal.test")]))
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("native-temporal.test")]), critical=False
            )
            .sign(ca_key, hashes.SHA256())
        )
        result[name] = cert.public_bytes(serialization.Encoding.PEM)
        result[name + "_key"] = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    for name, data in result.items():
        path = tmp_path / (name + ".pem")
        path.write_bytes(data)
        path.chmod(0o600)
    return result


@asynccontextmanager
async def tls_proxy(address, tmp_path, bundle, on_connect=None):
    target = urlsplit("//" + address)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(tmp_path / "server.pem", tmp_path / "server_key.pem")
    tls.load_verify_locations(cadata=bundle["ca"].decode())
    tls.verify_mode = ssl.CERT_REQUIRED
    tls.set_alpn_protocols(["h2"])
    writers, tasks, peers = set(), set(), []

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        writers.add(writer)
        upstream = None
        copies = []
        try:
            peers.append(writer.get_extra_info("peercert"))
            if on_connect:
                await on_connect()
            remote, upstream = await asyncio.open_connection(target.hostname, target.port)
            writers.add(upstream)

            async def copy(source, destination):
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()

            copies = [asyncio.create_task(copy(reader, upstream)), asyncio.create_task(copy(remote, writer))]
            await asyncio.wait(copies, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for child in copies:
                child.cancel()
            await asyncio.gather(*copies, return_exceptions=True)
            for stream in (writer, upstream):
                if stream:
                    stream.close()
                    writers.discard(stream)
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=tls)
    try:
        yield f"127.0.0.1:{server.sockets[0].getsockname()[1]}", peers
    finally:
        server.close()
        for writer in list(writers):
            writer.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


@pytest.mark.django_db(transaction=True)
async def test_real_native_mtls_honors_client_identity_ca_and_server_name(world, site, tmp_path):
    bundle = certificates(tmp_path)
    change = None

    async def on_connect():
        if change:
            await sync_to_async(change, thread_sensitive=False)()

    async with (
        temporal_test_env() as env,
        tls_proxy(env.client.service_client.config.target_host, tmp_path, bundle, on_connect) as (
            address,
            peers,
        ),
    ):
        row = await sync_to_async(configure)(
            world,
            site,
            address=address,
            namespace=env.client.namespace,
            server_name="native-temporal.test",
            credentials={
                "ca_pem": bundle["ca"].decode(),
                "client_certificate_pem": bundle["client"].decode(),
                "client_key_pem": bundle["client_key"].decode(),
            },
        )
        with context(world):
            verified = await sync_to_async(native.verify)(**review(world, site, row))
        assert verified.verification_code == "NAMESPACE_VERIFIED"
        assert peers and all(peers)
        row = await sync_to_async(configure)(
            world, site, row, server_name="wrong-native-name.test", timeout_seconds=2
        )
        with context(world), pytest.raises(NvkeSiteError) as wrong_name:
            await sync_to_async(native.verify)(**review(world, site, row))
        assert wrong_name.value.code == "NATIVE_TEMPORAL_UNAVAILABLE"
        row = await sync_to_async(configure)(
            world,
            site,
            row,
            server_name="native-temporal.test",
            credentials={"ca_pem": "INVALID_CA"},
            timeout_seconds=2,
        )
        with context(world), pytest.raises(NvkeSiteError) as wrong_ca:
            await sync_to_async(native.verify)(**review(world, site, row))
        assert wrong_ca.value.code == "NATIVE_TEMPORAL_UNAVAILABLE"
        row = await sync_to_async(configure)(
            world, site, row, credentials={"ca_pem": bundle["ca"].decode()}, timeout_seconds=2
        )
        with context(world), pytest.raises(NvkeSiteError) as missing_client:
            await sync_to_async(native.verify)(**review(world, site, row))
        assert missing_client.value.code == "NATIVE_TEMPORAL_UNAVAILABLE"
        row = await sync_to_async(configure)(
            world,
            site,
            row,
            credentials={
                "ca_pem": bundle["ca"].decode(),
                "client_certificate_pem": bundle["client"].decode(),
                "client_key_pem": bundle["client_key"].decode(),
            },
            timeout_seconds=5,
        )

        def change():
            from django.db import connections

            try:
                return NvkeTemporalAuthority.objects.filter(pk=row.pk).update(is_enabled=False)
            finally:
                connections["default"].close()

        with context(world), pytest.raises(NvkeSiteError) as changed:
            await sync_to_async(native.verify)(**review(world, site, row))
        assert changed.value.code == "SOURCE_CHANGED"
        await sync_to_async(row.refresh_from_db)()
        assert row.verified_at is None and not row.is_enabled
