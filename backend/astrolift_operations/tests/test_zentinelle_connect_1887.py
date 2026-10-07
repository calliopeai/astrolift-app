"""Connecting an organization to Zentinelle and running its gateway in clusters (#1887).

Real Postgres. Zentinelle's HTTP API is a fake that follows the contract in
calliopeai/zentinelle#389 and records every request; the cluster driver is a
fake that records what would have been applied or deleted. Mutations are
called directly, the repo's resolver-test idiom.
"""

from __future__ import annotations

import base64
import copy
import importlib
import json
import logging
import secrets
import uuid
from types import SimpleNamespace

import pytest
import requests
from constance.test import override_config
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import connection as db_connection

import core.cluster_management as cluster_management
import core.mutations as core_mutations
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Role
from astrolift_identity.system_roles import SYSTEM_ROLES
from astrolift_operations import zentinelle_connect
from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection
from astrolift_operations.schema.mutations import (
    ConnectZentinelleInput,
    DisconnectZentinelleInput,
    OperationsMutation,
    RotateZentinelleGatewayCredentialInput,
    SetZentinelleGatewayEnabledInput,
    ZentinelleClusterInput,
)
from astrolift_operations.schema.queries import OperationsQuery
from core.permissions import Permission, PermissionDenied
from core.secrets import EncryptedSecret, decrypt
from core.tenancy import TenantContext, tenant_context
from providers._sdk.cluster import ApplyError, ApplyResult, DeleteResult

pytestmark = pytest.mark.django_db

BASE = "https://zentinelle.test"
CODE = "enroll-" + "x" * 32
API = "/api/zentinelle/v1/astrolift"
IMAGE = "ghcr.io/calliopeai/zentinelle-gateway:main-107ad0b"


# ---- fakes --------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: dict | None = None, headers: dict | None = None) -> None:
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeZentinelle:
    """The Astrolift-facing endpoints of one Zentinelle (calliopeai/zentinelle#389)."""

    def __init__(self) -> None:
        self.calls: list[SimpleNamespace] = []
        self.install_credential = "sk_astroinst_" + secrets.token_hex(24)
        self.gateway_credentials: list[str] = []
        self.overrides: dict[tuple[str, str], object] = {}
        self.revoked = False

    def __call__(self, method, url, *, json=None, headers=None, timeout=None, allow_redirects=True):
        headers = dict(headers or {})
        self.calls.append(
            SimpleNamespace(
                method=method, url=url, json=json, headers=headers, timeout=timeout, redirects=allow_redirects
            )
        )
        path = url[len(BASE) :] if url.startswith(BASE) else url
        answer = self.overrides.get((method, path))
        if isinstance(answer, Exception):
            raise answer
        if answer is not None:
            return answer
        if (method, path) == ("POST", f"{API}/connect"):
            if json["code"] != CODE:
                return FakeResponse(400, {"error": "invalid_enrollment_code", "detail": "unknown"})
            install = {"id": "inst-1", "name": json["install"]["name"], "tenant_ids": ["tenant-a"]}
            return FakeResponse(201, {"install": install, "credential": self.install_credential})
        if self.revoked or headers.get("Authorization") != f"Bearer {self.install_credential}":
            return FakeResponse(401, {"detail": "Invalid Astrolift install credential"})
        if (method, path) == ("GET", f"{API}/install"):
            return FakeResponse(200, {"install": {"id": "inst-1", "status": "connected"}})
        if (method, path) == ("DELETE", f"{API}/install"):
            self.revoked = True
            return FakeResponse(200, {"install": {"id": "inst-1", "status": "revoked"}})
        if method == "POST" and (path == f"{API}/clusters" or path.endswith("/rotate")):
            credential = "sk_gateway_" + secrets.token_hex(24)
            self.gateway_credentials.append(credential)
            gateway = {"name": "astrolift-gw-1", "credential": credential, "tenant_ids": ["tenant-a"]}
            return FakeResponse(
                201 if path.endswith("/clusters") else 200, {"cluster": {}, "gateway": gateway}
            )
        if method == "DELETE" and path.startswith(f"{API}/clusters/"):
            return FakeResponse(200, {"cluster": {"status": "revoked"}})
        return FakeResponse(404, {"error": "not_found"})

    def paths(self) -> list[tuple[str, str]]:
        return [(c.method, c.url[len(BASE) :]) for c in self.calls]


class FakeDriver:
    """Records applies and deletes. ``fail`` maps a kind to the apply error it
    gets: a message, or a callable building one from the manifest."""

    def __init__(self) -> None:
        self.applied: list[tuple[str, dict]] = []
        self.deleted: list[tuple[str, str, str]] = []
        self.fail: dict[str, object] = {}
        self.fail_delete = ""

    def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False):
        created, errors = [], []
        for manifest in manifests:
            kind, name = manifest["kind"], manifest["metadata"]["name"]
            if kind in self.fail:
                message = self.fail[kind]
                errors.append(
                    ApplyError(
                        kind=kind,
                        name=name,
                        namespace=namespace,
                        exception_type="ApiException",
                        exception_message=message(manifest) if callable(message) else message,
                        is_retryable=False,
                    )
                )
                continue
            self.applied.append((namespace, copy.deepcopy(manifest)))
            created.append(f"{kind}/{name}")
        return ApplyResult(created=created, updated=[], unchanged=[], errors=errors)

    def delete_manifests(self, cluster, namespace, manifests, *, propagation_policy=None):
        if self.fail_delete:
            return DeleteResult(deleted=[], not_found=[], errors=[self.fail_delete])
        for manifest in manifests:
            self.deleted.append((namespace, manifest["kind"], manifest["metadata"]["name"]))
        return DeleteResult(deleted=[m["kind"] for m in manifests], not_found=[], errors=[])

    def kinds(self) -> list[str]:
        return [manifest["kind"] for _ns, manifest in self.applied]

    def last(self, kind: str) -> dict:
        return [manifest for _ns, manifest in self.applied if manifest["kind"] == kind][-1]


# ---- fixtures -----------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def zentinelle(monkeypatch) -> FakeZentinelle:
    fake = FakeZentinelle()
    monkeypatch.setattr(zentinelle_connect.requests, "request", fake)
    return fake


@pytest.fixture
def driver(monkeypatch) -> FakeDriver:
    fake = FakeDriver()
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda cluster: fake)
    return fake


@pytest.fixture
def audit():
    """Every audit entry, still written to the table as in production."""
    captured = []
    original = core_mutations._audit_writer

    def _writer(entry):
        captured.append(entry)
        original(entry)

    core_mutations.register_audit_writer(_writer)
    yield captured
    core_mutations.register_audit_writer(original)


@pytest.fixture
def logs():
    """Every record at DEBUG and up. caplog alone sees nothing from these
    modules: the app loggers (``astrolift_operations``, ``core``, ...) do not
    propagate to the root logger."""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    loggers = [logging.getLogger()] + [
        logger
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger) and not logger.propagate
    ]
    levels = [(logger, logger.level) for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    yield records
    for logger, level in levels:
        logger.removeHandler(handler)
        logger.setLevel(level)


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Globex", slug=f"globex-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def user():
    email = f"admin-{uuid.uuid4().hex[:6]}@acme.test"
    return get_user_model().objects.create(email=email, username=email)


@pytest.fixture
def plugin():
    [row] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="k8s", slug="k8s_native", capabilities_manifest={}, config_schema={})]
    )
    return row


def _make_cluster(plugin, organization, slug):
    return TenantCluster.objects.create(
        organization=organization,
        slug=f"{slug}-{uuid.uuid4().hex[:6]}",
        name=slug,
        provider_plugin=plugin,
        provider_config={},
        region="us-east-1",
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


@pytest.fixture
def cluster(plugin, org):
    return _make_cluster(plugin, org, "dev")


@pytest.fixture
def admin(permission_resolver):
    permission_resolver.grant(Permission.ZENTINELLE_CONNECT)
    permission_resolver.grant(Permission.ZENTINELLE_GATEWAY_MANAGE)
    return permission_resolver


def _info(session=None):
    request = SimpleNamespace(user=None) if session is None else SimpleNamespace(user=None, session=session)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=None))


def _as(organization, user=None):
    return tenant_context(
        TenantContext(organization_id=organization.id, actor_user_id=user.id if user is not None else None)
    )


def _connect(organization, user=None, *, url=BASE + "/", code=CODE):
    with _as(organization, user):
        return OperationsMutation().connect_zentinelle(
            _info(), input=ConnectZentinelleInput(url=url, enrollment_code=code)
        )


def _register(organization, cluster):
    with _as(organization):
        return OperationsMutation().register_zentinelle_cluster(
            _info(), input=ZentinelleClusterInput(cluster_id=GUID(str(cluster.guid)))
        )


def _set_enabled(organization, cluster, enabled):
    with _as(organization):
        return OperationsMutation().set_zentinelle_gateway_enabled(
            _info(),
            input=SetZentinelleGatewayEnabledInput(cluster_id=GUID(str(cluster.guid)), enabled=enabled),
        )


def _code(result) -> str:
    assert result.errors, result
    return str(result.errors[0].code)


def _stored_in(needle: str) -> list[str]:
    """Every table holding ``needle`` in any column, as text, hex or base64."""
    forms = [needle, needle.encode().hex(), base64.b64encode(needle.encode()).decode()]
    hits = []
    with db_connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        for (table,) in cursor.fetchall():
            for form in forms:
                cursor.execute(f'SELECT 1 FROM "{table}" t WHERE strpos(t::text, %s) > 0 LIMIT 1', [form])
                if cursor.fetchone():
                    hits.append(table)
                    break
    return hits


def _logged(needle: str, records) -> bool:
    return any(needle in repr(record.__dict__) or needle in record.getMessage() for record in records)


# ---- permissions ----------------------------------------------------------


def test_the_resync_migration_grants_the_zentinelle_permissions_to_org_owner_and_admin_only():
    zentinelle = {Permission.ZENTINELLE_CONNECT.value, Permission.ZENTINELLE_GATEWAY_MANAGE.value}
    # An existing install: system roles stored before these permissions existed.
    Role.objects.filter(is_system=True, organization=None).update(permissions=[])
    migration = importlib.import_module("astrolift_identity.migrations.0033_resync_system_roles_zentinelle")

    migration.upsert_system_roles(django_apps, None)

    holders = {
        role.slug
        for role in Role.objects.filter(is_system=True, organization=None)
        if zentinelle & set(role.permissions)
    }
    assert holders == {"org_owner", "org_admin"}
    for slug in holders:
        assert zentinelle <= set(Role.objects.get(slug=slug, is_system=True, organization=None).permissions)
    assert {slug for slug, *_rest in SYSTEM_ROLES} == set(
        Role.objects.filter(is_system=True, organization=None).values_list("slug", flat=True)
    )


def test_connect_is_denied_without_zentinelle_connect(org, permission_resolver, zentinelle):
    permission_resolver.grant(Permission.ZENTINELLE_GATEWAY_MANAGE)

    result = _connect(org)

    assert result.ok is False and _code(result) == "PERMISSION_DENIED"
    assert zentinelle.calls == []
    assert not ZentinelleConnection.objects.exists()


def test_cluster_mutations_are_denied_with_zentinelle_connect_alone(
    org, cluster, permission_resolver, zentinelle, driver
):
    permission_resolver.grant(Permission.ZENTINELLE_CONNECT)
    assert _connect(org).ok

    registered = _register(org, cluster)
    toggled = _set_enabled(org, cluster, True)
    with _as(org):
        unregistered = OperationsMutation().unregister_zentinelle_cluster(
            _info(), input=ZentinelleClusterInput(cluster_id=GUID(str(cluster.guid)))
        )

    for result in (registered, toggled, unregistered):
        assert result.ok is False and _code(result) == "PERMISSION_DENIED"
    assert zentinelle.paths() == [("POST", f"{API}/connect")]
    assert driver.applied == [] and driver.deleted == []


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_connect_needs_a_fresh_step_up(org, admin, zentinelle):
    with _as(org):
        result = OperationsMutation().connect_zentinelle(
            _info(session={}), input=ConnectZentinelleInput(url=BASE, enrollment_code=CODE)
        )

    assert result.ok is False and _code(result) == "STEP_UP_REQUIRED"
    assert zentinelle.calls == []


def test_every_zentinelle_mutation_is_listed_as_step_up_gated():
    from astrolift_identity.step_up import list_gated_resolvers

    gated = {probe.resolver.rsplit(".", 1)[1] for probe in list_gated_resolvers()}
    assert {
        "connect_zentinelle",
        "disconnect_zentinelle",
        "register_zentinelle_cluster",
        "set_zentinelle_gateway_enabled",
        "rotate_zentinelle_gateway_credential",
        "unregister_zentinelle_cluster",
    } <= gated


def test_status_query_needs_zentinelle_connect(org, permission_resolver, zentinelle, admin):
    assert _connect(org).ok
    permission_resolver.deny(Permission.ZENTINELLE_CONNECT)
    with _as(org), pytest.raises(PermissionDenied):
        OperationsQuery().astrolift_zentinelle_connection(_info())

    permission_resolver.grant(Permission.ZENTINELLE_CONNECT)
    with _as(org):
        status = OperationsQuery().astrolift_zentinelle_connection(_info())
    assert status is not None and status.status == "connected"


# ---- connect ------------------------------------------------------------


def test_connect_stores_the_install_credential_sealed_and_nowhere_else(
    org, user, admin, zentinelle, audit, logs
):
    result = _connect(org, user)

    assert result.ok, result.errors
    assert result.data.base_url == BASE
    assert result.data.status == "connected"
    assert result.data.zentinelle_install_id == "inst-1"
    assert result.data.tenant_ids == ["tenant-a"]

    [call] = zentinelle.calls
    assert (call.method, call.url) == ("POST", f"{BASE}{API}/connect")
    assert call.json["code"] == CODE
    assert call.json["install"]["name"].startswith("Acme (")
    assert call.redirects is False
    assert call.timeout == zentinelle_connect.HTTP_TIMEOUT_SECONDS

    credential = zentinelle.install_credential
    row = ZentinelleConnection.objects.get(organization=org)
    assert row.connected_by == user
    assert bytes(row.credential_ciphertext) and credential.encode() not in bytes(row.credential_ciphertext)
    assert decrypt(EncryptedSecret(row.credential_backend_kind, bytes(row.credential_ciphertext))) == (
        credential.encode()
    )
    # The scan is live: it finds what the row does store.
    assert "astrolift_operations_zentinelleconnection" in _stored_in("zentinelle.test")
    assert _stored_in(credential) == []
    # The capture is live: it sees this module's own log line.
    assert _logged("connected to https://zentinelle.test", logs)
    assert not _logged(credential, logs)
    assert credential not in repr(result)
    assert [entry.action for entry in audit] == ["zentinelle.connect"]
    assert credential not in repr(audit)


def test_a_refused_enrollment_code_connects_nothing(org, admin, zentinelle):
    result = _connect(org, code="enroll-wrong")

    assert result.ok is False and _code(result) == "VALIDATION"
    assert result.errors[0].field == "enrollmentCode"
    assert not ZentinelleConnection.objects.exists()


def test_a_second_connect_is_a_conflict_without_calling_zentinelle(org, admin, zentinelle):
    assert _connect(org).ok
    zentinelle.calls.clear()

    result = _connect(org)

    assert result.ok is False and _code(result) == "CONFLICT"
    assert zentinelle.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://zentinelle.test",
        "https://user:pw@zentinelle.test",
        "https://zentinelle.test/?next=x",
        "ftp://z",
    ],
)
def test_connect_refuses_urls_that_could_leak_the_credential(org, admin, zentinelle, url, settings):
    settings.DEBUG = False

    result = _connect(org, url=url)

    assert result.ok is False and _code(result) == "VALIDATION"
    assert result.errors[0].field == "url"
    assert zentinelle.calls == []


def test_a_redirect_is_refused_not_followed(org, admin, zentinelle):
    zentinelle.overrides[("POST", f"{API}/connect")] = FakeResponse(
        307, headers={"Location": "https://elsewhere.test/api/zentinelle/v1/astrolift/connect"}
    )

    result = _connect(org)

    assert result.ok is False and _code(result) == "INTERNAL"
    assert "elsewhere.test" in result.errors[0].message
    assert [call.redirects for call in zentinelle.calls] == [False]
    assert not ZentinelleConnection.objects.exists()


def test_an_unreachable_zentinelle_connects_nothing(org, admin, zentinelle):
    zentinelle.overrides[("POST", f"{API}/connect")] = requests.ConnectionError("connection refused")

    result = _connect(org)

    assert result.ok is False and _code(result) == "INTERNAL"
    assert "could not reach Zentinelle" in result.errors[0].message
    assert not ZentinelleConnection.objects.exists()


def test_a_failed_answer_repeats_only_its_status_and_detail(org, admin, zentinelle):
    # Whatever answers the admin's URL, its body is not echoed back beyond ``detail``.
    zentinelle.overrides[("POST", f"{API}/connect")] = FakeResponse(
        502, {"upstream": "10.0.0.7:5432 internal-db", "detail": "bad gateway"}
    )

    result = _connect(org)

    assert result.ok is False and _code(result) == "INTERNAL"
    assert "502: bad gateway" in result.errors[0].message
    assert "internal-db" not in result.errors[0].message


def test_an_answer_without_an_install_credential_is_not_stored(org, admin, zentinelle):
    zentinelle.overrides[("POST", f"{API}/connect")] = FakeResponse(201, {"install": {"id": "x"}})

    result = _connect(org)

    assert result.ok is False and _code(result) == "INTERNAL"
    assert not ZentinelleConnection.objects.exists()


# ---- register -----------------------------------------------------------


def test_register_writes_the_credential_secret_but_no_gateway_while_the_flag_is_off(
    org, cluster, admin, zentinelle, driver, logs
):
    assert _connect(org).ok

    result = _register(org, cluster)

    assert result.ok, result.errors
    assert result.data.status == "registered"
    assert result.data.gateway_enabled is True and result.data.gateway_deployed is False
    assert result.data.zentinelle_cluster_id == str(cluster.guid)

    register = zentinelle.calls[-1]
    assert (register.method, register.url) == ("POST", f"{BASE}{API}/clusters")
    assert register.json == {"cluster_id": str(cluster.guid), "provider": "k8s_native", "region": "us-east-1"}
    assert register.headers["Authorization"] == f"Bearer {zentinelle.install_credential}"

    # The namespace goes first: the cluster proves it takes writes before a credential is minted.
    assert driver.kinds() == ["Namespace", "Secret"]
    [credential] = zentinelle.gateway_credentials
    namespace, secret = driver.applied[-1]
    assert namespace == "astrolift-system"
    assert secret["metadata"]["name"] == "zentinelle-gateway-credential"
    assert secret["metadata"]["namespace"] == "astrolift-system"
    assert base64.b64decode(secret["data"]["credential"]).decode() == credential

    assert _stored_in(credential) == []
    assert _logged("registered cluster", logs)
    assert not _logged(credential, logs)
    assert credential not in repr(result)


@override_config(ZENTINELLE_GATEWAY_ENABLED=True, ZENTINELLE_GATEWAY_IMAGE=IMAGE)
def test_register_deploys_the_gateway_when_the_flag_is_on(org, cluster, admin, zentinelle, driver):
    assert _connect(org).ok

    result = _register(org, cluster)

    assert result.ok, result.errors
    assert result.data.status == "deployed" and result.data.gateway_deployed is True
    assert driver.kinds() == ["Namespace", "Secret", "Deployment", "Service"]
    container = driver.last("Deployment")["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == IMAGE
    env = {item["name"]: item["value"] for item in container["env"]}
    assert env["ZENTINELLE_URL"] == BASE
    assert env["ZENTINELLE_CLUSTER_ID"] == str(cluster.guid)
    assert driver.last("Service")["spec"]["ports"][0]["port"] == 8742


def test_another_orgs_cluster_is_not_found_and_a_shared_one_registers(
    org, other_org, plugin, admin, zentinelle, driver
):
    foreign = _make_cluster(plugin, other_org, "foreign")
    shared = _make_cluster(plugin, None, "shared")
    assert _connect(org).ok
    zentinelle.calls.clear()

    refused = _register(org, foreign)
    accepted = _register(org, shared)

    assert refused.ok is False and _code(refused) == "NOT_FOUND"
    assert accepted.ok, accepted.errors
    assert [path for _m, path in zentinelle.paths()] == [f"{API}/clusters"]


def test_a_cluster_has_one_gateway_whichever_org_registered_it(
    org, other_org, plugin, admin, zentinelle, driver
):
    shared = _make_cluster(plugin, None, "shared")
    assert _connect(org).ok
    assert _register(org, shared).ok
    assert _connect(other_org).ok

    result = _register(other_org, shared)

    assert result.ok is False and _code(result) == "CONFLICT"
    assert ZentinelleClusterGateway.objects.get(cluster=shared).connection.organization == org


def test_register_needs_a_connection(org, cluster, admin, zentinelle, driver):
    result = _register(org, cluster)

    assert result.ok is False and _code(result) == "PRECONDITION"
    assert zentinelle.calls == [] and driver.applied == []


def test_a_refused_install_credential_marks_the_connection_revoked(org, cluster, admin, zentinelle, driver):
    assert _connect(org).ok
    zentinelle.revoked = True

    result = _register(org, cluster)

    assert result.ok is False and _code(result) == "PRECONDITION"
    assert ZentinelleConnection.objects.get(organization=org).status == "revoked"
    assert "Secret" not in driver.kinds()
    assert not ZentinelleClusterGateway.objects.exists()


def test_an_unreachable_cluster_is_refused_before_zentinelle_mints_anything(
    org, cluster, admin, zentinelle, driver
):
    assert _connect(org).ok
    driver.fail["Namespace"] = "connection refused"

    result = _register(org, cluster)

    assert result.ok is False and _code(result) == "INTERNAL"
    assert zentinelle.gateway_credentials == []


def test_a_secret_write_failure_is_recorded_redacted_and_register_again_recovers(
    org, cluster, admin, zentinelle, driver, audit
):
    assert _connect(org).ok
    # An apiserver error that echoes the submitted value back.
    driver.fail["Secret"] = (
        lambda manifest: f'data[credential]: Invalid value: "{manifest["data"]["credential"]}"'
    )

    failed = _register(org, cluster)

    [credential] = zentinelle.gateway_credentials
    encoded = base64.b64encode(credential.encode()).decode()
    assert failed.ok is False and _code(failed) == "INTERNAL"
    message = failed.errors[0].message
    assert "[redacted]" in message and encoded not in message and credential not in message
    row = ZentinelleClusterGateway.objects.get(cluster=cluster)
    assert row.status == "error" and encoded not in row.last_error
    assert credential not in repr(audit) and encoded not in repr(audit)
    assert _stored_in(credential) == []

    del driver.fail["Secret"]
    recovered = _register(org, cluster)

    assert recovered.ok, recovered.errors
    assert recovered.data.status == "registered" and recovered.data.last_error == ""
    assert (
        base64.b64decode(driver.last("Secret")["data"]["credential"]).decode()
        == (zentinelle.gateway_credentials[-1])
    )


# ---- gateway toggle, rotation, unregister --------------------------------


def test_turning_the_gateway_on_needs_the_install_flag(org, cluster, admin, zentinelle, driver):
    assert _connect(org).ok
    assert _register(org, cluster).ok
    applied = list(driver.applied)

    result = _set_enabled(org, cluster, True)

    assert result.ok is False and _code(result) == "PRECONDITION"
    assert driver.applied == applied


@override_config(ZENTINELLE_GATEWAY_ENABLED=True, ZENTINELLE_GATEWAY_IMAGE=IMAGE)
def test_turning_the_gateway_off_removes_it_and_keeps_the_credential(org, cluster, admin, zentinelle, driver):
    assert _connect(org).ok
    assert _register(org, cluster).ok

    off = _set_enabled(org, cluster, False)

    assert off.ok, off.errors
    assert off.data.gateway_enabled is False and off.data.status == "registered"
    assert driver.deleted == [
        ("astrolift-system", "Deployment", "zentinelle-gateway"),
        ("astrolift-system", "Service", "zentinelle-gateway"),
    ]

    on = _set_enabled(org, cluster, True)

    assert on.ok, on.errors
    assert on.data.status == "deployed"
    assert driver.kinds()[-2:] == ["Deployment", "Service"]


def test_rotation_writes_the_next_credential_with_the_overlap(org, cluster, admin, zentinelle, driver):
    assert _connect(org).ok
    assert _register(org, cluster).ok

    with _as(org):
        result = OperationsMutation().rotate_zentinelle_gateway_credential(
            _info(),
            input=RotateZentinelleGatewayCredentialInput(
                cluster_id=GUID(str(cluster.guid)), overlap_seconds=0
            ),
        )

    assert result.ok, result.errors
    assert result.data.credential_rotated_at is not None
    rotate = zentinelle.calls[-1]
    assert rotate.url == f"{BASE}{API}/clusters/{cluster.guid}/rotate"
    assert rotate.json == {"overlap_seconds": 0}
    assert (
        base64.b64decode(driver.last("Secret")["data"]["credential"]).decode()
        == (zentinelle.gateway_credentials[-1])
    )


def test_rotation_refuses_an_overlap_zentinelle_would_refuse(org, cluster, admin, zentinelle, driver):
    assert _connect(org).ok
    assert _register(org, cluster).ok
    zentinelle.calls.clear()

    with _as(org):
        result = OperationsMutation().rotate_zentinelle_gateway_credential(
            _info(),
            input=RotateZentinelleGatewayCredentialInput(
                cluster_id=GUID(str(cluster.guid)), overlap_seconds=86401
            ),
        )

    assert result.ok is False and _code(result) == "VALIDATION"
    assert result.errors[0].field == "overlapSeconds"
    assert zentinelle.calls == []


@override_config(ZENTINELLE_GATEWAY_ENABLED=True)
def test_unregister_revokes_then_removes_the_gateway_and_its_secret(org, cluster, admin, zentinelle, driver):
    assert _connect(org).ok
    assert _register(org, cluster).ok
    assert zentinelle_connect.fenced_agents_reach_gateway(cluster) is True

    with _as(org):
        result = OperationsMutation().unregister_zentinelle_cluster(
            _info(), input=ZentinelleClusterInput(cluster_id=GUID(str(cluster.guid)))
        )

    assert result.ok, result.errors
    assert result.data.unregistered is True
    assert zentinelle.paths()[-1] == ("DELETE", f"{API}/clusters/{cluster.guid}")
    assert [kind for _ns, kind, _name in driver.deleted] == ["Deployment", "Service", "Secret"]
    assert not ZentinelleClusterGateway.objects.filter(cluster=cluster).exists()
    assert zentinelle_connect.fenced_agents_reach_gateway(cluster) is False


# ---- disconnect ----------------------------------------------------------


@override_config(ZENTINELLE_GATEWAY_ENABLED=True, ZENTINELLE_GATEWAY_IMAGE=IMAGE)
def test_disconnect_revokes_first_then_cleans_every_cluster(org, plugin, admin, zentinelle, driver):
    clusters = [_make_cluster(plugin, org, "a"), _make_cluster(plugin, org, "b")]
    assert _connect(org).ok
    for cluster in clusters:
        assert _register(org, cluster).ok
    zentinelle.calls.clear()

    with _as(org):
        result = OperationsMutation().disconnect_zentinelle(_info(), input=DisconnectZentinelleInput())

    assert result.ok, result.errors
    assert result.data.warnings == []
    assert result.data.connection.status == "disconnected"
    assert result.data.connection.clusters == []
    assert zentinelle.paths() == [("DELETE", f"{API}/install")]
    assert sorted(kind for _ns, kind, _name in driver.deleted) == sorted(
        ["Deployment", "Service", "Secret"] * 2
    )
    row = ZentinelleConnection.all_objects.get(organization=org)
    assert row.deleted_at is not None and bytes(row.credential_ciphertext) == b""
    assert not ZentinelleClusterGateway.objects.exists()

    assert _connect(org).ok


def test_disconnect_stops_when_zentinelle_is_unreachable_unless_forced(
    org, cluster, admin, zentinelle, driver
):
    assert _connect(org).ok
    assert _register(org, cluster).ok
    zentinelle.overrides[("DELETE", f"{API}/install")] = requests.ConnectionError("no route to host")

    with _as(org):
        stopped = OperationsMutation().disconnect_zentinelle(_info(), input=DisconnectZentinelleInput())

    assert stopped.ok is False and _code(stopped) == "INTERNAL"
    assert ZentinelleConnection.objects.get(organization=org).status == "connected"
    assert driver.deleted == []

    with _as(org):
        forced = OperationsMutation().disconnect_zentinelle(
            _info(), input=DisconnectZentinelleInput(force=True)
        )

    assert forced.ok, forced.errors
    [warning] = forced.data.warnings
    assert "Zentinelle was not told" in warning
    assert not ZentinelleConnection.objects.filter(organization=org).exists()
    assert [kind for _ns, kind, _name in driver.deleted] == ["Deployment", "Service", "Secret"]


# ---- status query ------------------------------------------------------------


def test_the_status_query_is_scoped_to_the_callers_org(org, other_org, cluster, admin, zentinelle, driver):
    assert _connect(org).ok
    assert _register(org, cluster).ok

    with _as(org):
        mine = OperationsQuery().astrolift_zentinelle_connection(_info())
    with _as(other_org):
        theirs = OperationsQuery().astrolift_zentinelle_connection(_info())

    assert mine is not None and [c.cluster_slug for c in mine.clusters] == [cluster.slug]
    assert mine.gateway_feature_enabled is False
    assert theirs is None


# ---- manifests ----------------------------------------------------------------


def test_the_gateway_runs_non_root_read_only_with_probes_and_limits():
    deployment, service = zentinelle_connect.gateway_manifests(
        cluster_id="c-1", zentinelle_url=BASE, image=IMAGE
    )
    pod = deployment["spec"]["template"]["spec"]
    [container] = pod["containers"]

    assert deployment["metadata"]["namespace"] == service["metadata"]["namespace"] == "astrolift-system"
    assert (
        deployment["spec"]["selector"]["matchLabels"]
        == service["spec"]["selector"]
        == {"app": "zentinelle-gateway"}
    )
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["securityContext"]["runAsUser"] == 65532
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert container["readinessProbe"]["httpGet"] == {"path": "/health", "port": "http"}
    assert container["livenessProbe"]["httpGet"] == {"path": "/health", "port": "http"}
    assert container["resources"]["limits"]["memory"] and container["resources"]["requests"]["cpu"]
    assert container["ports"] == [{"name": "http", "containerPort": 8742, "protocol": "TCP"}]
    [mount] = container["volumeMounts"]
    assert mount == {"name": "gateway-credential", "mountPath": "/var/run/zentinelle", "readOnly": True}
    assert "subPath" not in mount
    [volume] = pod["volumes"]
    assert volume["secret"]["secretName"] == "zentinelle-gateway-credential"
    env = {item["name"]: item["value"] for item in container["env"]}
    assert env == {
        "ZENTINELLE_URL": BASE,
        "ZENTINELLE_CLUSTER_ID": "c-1",
        "ZENTINELLE_GATEWAY_CREDENTIAL_FILE": "/var/run/zentinelle/gateway-credential",
    }
