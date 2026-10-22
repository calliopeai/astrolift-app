"""The install connection shared clusters register through (#1905).

A shared cluster (no organization) is trusted by every organization, so its
gateway is the install's, connected by the platform operator. Organization
clusters keep their organization's connection. An org admin, who holds every
``zentinelle.*`` permission, can neither read nor change the install
connection. A shared cluster an organization registered before this moves to
the install connection only when the operator moves it.

Same fakes as #1887: Zentinelle's HTTP API and the cluster driver are
recorded, never reached. Here each connect mints its own install credential,
so every call shows which connection made it.
"""

from __future__ import annotations

import secrets
import uuid
from types import SimpleNamespace

import pytest
from constance.test import override_config
from django.contrib.auth import get_user_model
from django.core.management import call_command

from astrolift_graphql import GUID
from astrolift_operations import zentinelle_connect
from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection
from astrolift_operations.schema.mutations import (
    ConnectZentinelleInput,
    DisconnectZentinelleInput,
    OperationsMutation,
    SetZentinelleGatewayUrlInput,
    ZentinelleClusterInput,
)
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_operations.tests import test_zentinelle_connect_1887 as base
from astrolift_operations.tests.test_zentinelle_connect_1887 import (
    API,
    BASE,
    CODE,
    IMAGE,
    FakeResponse,
    FakeZentinelle,
    _as,
    _code,
    _connect,
    _make_cluster,
)
from core.permissions import PermissionDenied

pytestmark = pytest.mark.django_db

_no_opensearch_profile_index = base._no_opensearch_profile_index
admin = base.admin
audit = base.audit
driver = base.driver
org = base.org
plugin = base.plugin
user = base.user

IN_CLUSTER = "http://zentinelle.zentinelle.svc.cluster.local:8000"


class FakeZentinelles(FakeZentinelle):
    """One Zentinelle that tells installs apart: each connect mints a new install credential."""

    def __init__(self) -> None:
        super().__init__()
        self.installs: dict[str, str] = {}

    def __call__(self, method, url, *, json=None, headers=None, timeout=None, allow_redirects=True):
        path = url[len(BASE) :]
        if (method, path) == ("POST", f"{API}/connect") and (method, path) not in self.overrides:
            self.install_credential = "sk_astroinst_" + secrets.token_hex(24)
            self.installs[self.install_credential] = json["install"]["name"]
        token = dict(headers or {}).get("Authorization", "").removeprefix("Bearer ")
        if token in self.installs:
            self.install_credential = token
        return super().__call__(
            method, url, json=json, headers=headers, timeout=timeout, allow_redirects=allow_redirects
        )

    def by(self) -> list[tuple[str, str, str]]:
        """(method, path, which install) of every call."""
        return [
            (
                c.method,
                c.url[len(BASE) :],
                self.installs.get(c.headers.get("Authorization", "").removeprefix("Bearer "), "-"),
            )
            for c in self.calls
        ]


@pytest.fixture
def zentinelle(monkeypatch) -> FakeZentinelles:
    fake = FakeZentinelles()
    monkeypatch.setattr(zentinelle_connect.requests, "request", fake)
    return fake


@pytest.fixture
def operator():
    email = f"operator-{uuid.uuid4().hex[:6]}@astro.test"
    return get_user_model().objects.create(email=email, username=email, is_superuser=True)


def _info(user):
    request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


def _connect_install(organization, user, *, gateway_url=None):
    with _as(organization, user):
        return OperationsMutation().connect_zentinelle_install(
            _info(user), input=ConnectZentinelleInput(url=BASE, enrollment_code=CODE, gateway_url=gateway_url)
        )


def _shared(organization, user, mutation, cluster):
    with _as(organization, user):
        return getattr(OperationsMutation(), mutation)(
            _info(user), input=ZentinelleClusterInput(cluster_id=GUID(str(cluster.guid)))
        )


def _install() -> ZentinelleConnection:
    return ZentinelleConnection.objects.get(organization__isnull=True)


def _registered_by(connection_guid, cluster, *, deployed=True) -> ZentinelleClusterGateway:
    """A shared cluster an organization registered before #1905."""
    return ZentinelleClusterGateway.objects.create(
        connection=ZentinelleConnection.objects.get(guid=str(connection_guid)),
        cluster=cluster,
        zentinelle_cluster_id=str(cluster.guid),
        gateway_deployed=deployed,
    )


# ---- the operator connects the install ------------------------------------


def test_the_operator_connects_the_install_and_the_credential_is_not_returned(
    org, operator, admin, zentinelle, audit
):
    result = _connect_install(org, operator)

    assert result.ok, result.errors
    install = _install()
    assert install.is_install and install.status == ZentinelleConnection.Status.CONNECTED
    assert zentinelle.install_credential not in repr(result)
    assert zentinelle.calls[0].json["install"]["name"].startswith("Astrolift install (")
    assert [(entry.action, entry.decision) for entry in audit] == [("zentinelle.install.connect", "ALLOW")]


def test_the_install_and_an_organization_are_connected_side_by_side_once_each(
    org, operator, admin, zentinelle
):
    assert _connect_install(org, operator).ok
    assert _connect(org).ok

    again = _connect_install(org, operator)

    assert again.ok is False and _code(again) == "CONFLICT"
    assert ZentinelleConnection.objects.filter(organization__isnull=True).count() == 1
    assert ZentinelleConnection.objects.filter(organization=org).count() == 1


# ---- the tenant boundary ---------------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "input_for"),
    [
        ("connect_zentinelle_install", lambda c: ConnectZentinelleInput(url=BASE, enrollment_code=CODE)),
        (
            "set_zentinelle_install_gateway_url",
            lambda c: SetZentinelleGatewayUrlInput(gateway_url=IN_CLUSTER),
        ),
        ("disconnect_zentinelle_install", lambda c: DisconnectZentinelleInput(force=True)),
        (
            "register_zentinelle_shared_cluster",
            lambda c: ZentinelleClusterInput(cluster_id=GUID(str(c.guid))),
        ),
        (
            "move_zentinelle_shared_cluster_to_install",
            lambda c: ZentinelleClusterInput(cluster_id=GUID(str(c.guid))),
        ),
        (
            "unregister_zentinelle_shared_cluster",
            lambda c: ZentinelleClusterInput(cluster_id=GUID(str(c.guid))),
        ),
    ],
)
def test_an_org_admin_cannot_change_the_install_connection(
    org, user, operator, plugin, admin, zentinelle, driver, audit, mutation, input_for
):
    shared = _make_cluster(plugin, None, "shared")
    assert _connect_install(org, operator).ok
    assert _shared(org, operator, "register_zentinelle_shared_cluster", shared).ok
    before = ZentinelleConnection.objects.values().get(organization__isnull=True)
    calls, applied, deleted = len(zentinelle.calls), len(driver.applied), len(driver.deleted)
    audit.clear()

    with _as(org, user):
        result = getattr(OperationsMutation(), mutation)(_info(user), input=input_for(shared))

    assert result.ok is False and _code(result) == "PERMISSION_DENIED"
    assert ZentinelleConnection.objects.values().get(organization__isnull=True) == before
    assert ZentinelleClusterGateway.objects.get(cluster=shared).connection.is_install
    assert (len(zentinelle.calls), len(driver.applied), len(driver.deleted)) == (calls, applied, deleted)
    assert [entry.decision for entry in audit] == ["DENY"]


def test_an_org_admin_cannot_read_the_install_connection(
    org, user, operator, plugin, admin, zentinelle, driver
):
    shared = _make_cluster(plugin, None, "shared")
    assert _connect_install(org, operator).ok
    assert _shared(org, operator, "register_zentinelle_shared_cluster", shared).ok

    with _as(org, user), pytest.raises(PermissionDenied):
        OperationsQuery().astrolift_zentinelle_install_connection(_info(user))
    with _as(org, user):
        own = OperationsQuery().astrolift_zentinelle_connection(_info(user))
    with _as(org, operator):
        seen = OperationsQuery().astrolift_zentinelle_install_connection(_info(operator))

    assert own is None
    assert seen is not None and [c.cluster_slug for c in seen.clusters] == [shared.slug]


def test_an_org_admins_own_mutations_never_reach_the_install_connection(
    org, user, operator, plugin, admin, zentinelle, driver
):
    """The org mutations look rows up by the caller's organization; the install has none."""
    shared = _make_cluster(plugin, None, "shared")
    assert _connect_install(org, operator).ok
    assert _shared(org, operator, "register_zentinelle_shared_cluster", shared).ok
    calls = len(zentinelle.calls)

    with _as(org, user):
        mutations = OperationsMutation()
        results = [
            mutations.disconnect_zentinelle(_info(user), input=DisconnectZentinelleInput(force=True)),
            mutations.set_zentinelle_gateway_url(
                _info(user), input=SetZentinelleGatewayUrlInput(gateway_url=IN_CLUSTER)
            ),
            mutations.unregister_zentinelle_cluster(
                _info(user), input=ZentinelleClusterInput(cluster_id=GUID(str(shared.guid)))
            ),
        ]

    assert [_code(result) for result in results] == ["NOT_FOUND"] * 3
    assert _install().status == ZentinelleConnection.Status.CONNECTED and _install().gateway_url == ""
    assert len(zentinelle.calls) == calls


# ---- registration ----------------------------------------------------------


@override_config(ZENTINELLE_GATEWAY_ENABLED=True, ZENTINELLE_GATEWAY_IMAGE=IMAGE)
def test_a_shared_cluster_registers_through_the_install_and_an_org_cluster_does_not(
    org, operator, plugin, admin, zentinelle, driver
):
    shared = _make_cluster(plugin, None, "shared")
    owned = _make_cluster(plugin, org, "owned")
    assert _connect_install(org, operator, gateway_url=IN_CLUSTER).ok

    registered = _shared(org, operator, "register_zentinelle_shared_cluster", shared)
    refused = _shared(org, operator, "register_zentinelle_shared_cluster", owned)

    assert registered.ok, registered.errors
    assert refused.ok is False and _code(refused) == "NOT_FOUND"
    assert ZentinelleClusterGateway.objects.get(cluster=shared).connection.is_install
    assert not ZentinelleClusterGateway.objects.filter(cluster=owned).exists()
    assert ("POST", f"{API}/clusters", f"Astrolift install ({_host()})") in zentinelle.by()
    env = {
        e["name"]: e["value"]
        for e in driver.last("Deployment")["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["ZENTINELLE_URL"] == IN_CLUSTER


def test_the_install_connection_refuses_an_organizations_cluster(
    org, operator, plugin, admin, zentinelle, driver
):
    owned = _make_cluster(plugin, org, "owned")
    assert _connect_install(org, operator).ok

    with pytest.raises(zentinelle_connect.ZentinelleConnectError, match="that organization's"):
        zentinelle_connect.register_cluster(connection=_install(), cluster=owned)

    assert ("POST", f"{API}/clusters") not in [(m, p) for m, p, _who in zentinelle.by()]


# ---- moving a shared cluster an organization registered --------------------


@override_config(ZENTINELLE_GATEWAY_ENABLED=True, ZENTINELLE_GATEWAY_IMAGE=IMAGE)
def test_the_install_never_takes_over_a_shared_cluster_without_an_explicit_move(
    org, operator, plugin, admin, zentinelle, driver
):
    shared = _make_cluster(plugin, None, "shared")
    org_connection = _connect(org).data
    _registered_by(org_connection.id, shared)
    assert _connect_install(org, operator).ok

    registered = _shared(org, operator, "register_zentinelle_shared_cluster", shared)
    zentinelle_connect.reconcile_gateways()

    assert registered.ok is False and _code(registered) == "CONFLICT"
    assert "move it to the install connection" in registered.errors[0].message
    gateway = ZentinelleClusterGateway.objects.get(cluster=shared)
    assert gateway.connection.organization == org


@override_config(ZENTINELLE_GATEWAY_ENABLED=True, ZENTINELLE_GATEWAY_IMAGE=IMAGE)
def test_the_operator_moves_a_shared_cluster_install_first_then_revokes_the_orgs(
    org, user, operator, plugin, admin, zentinelle, driver, audit
):
    shared = _make_cluster(plugin, None, "shared")
    org_connection = _connect(org).data
    org_install = zentinelle.install_credential
    _registered_by(org_connection.id, shared)
    assert _connect_install(org, operator, gateway_url=IN_CLUSTER).ok
    zentinelle.calls.clear()
    driver.applied.clear()
    audit.clear()

    result = _shared(org, operator, "move_zentinelle_shared_cluster_to_install", shared)

    assert result.ok, result.errors
    install_name = f"Astrolift install ({_host()})"
    assert zentinelle.by() == [
        ("POST", f"{API}/clusters", install_name),
        ("DELETE", f"{API}/clusters/{shared.guid}", zentinelle.installs[org_install]),
    ]
    assert driver.kinds() == ["Namespace", "Secret", "Deployment", "Service", "HorizontalPodAutoscaler"]
    env = {
        e["name"]: e["value"]
        for e in driver.last("Deployment")["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["ZENTINELLE_URL"] == IN_CLUSTER
    gateway = ZentinelleClusterGateway.objects.get(cluster=shared)
    assert gateway.connection.is_install and gateway.last_error == ""
    assert [(entry.action, entry.decision) for entry in audit] == [
        ("zentinelle.install.cluster.move", "ALLOW")
    ]
    with _as(org, user):
        own = OperationsQuery().astrolift_zentinelle_connection(_info(user))
    assert own.clusters == []


@override_config(ZENTINELLE_GATEWAY_ENABLED=True, ZENTINELLE_GATEWAY_IMAGE=IMAGE)
def test_a_move_whose_old_registration_cannot_be_revoked_stands_and_says_so(
    org, operator, plugin, admin, zentinelle, driver
):
    shared = _make_cluster(plugin, None, "shared")
    _registered_by(_connect(org).data.id, shared)
    assert _connect_install(org, operator).ok
    zentinelle.overrides[("DELETE", f"{API}/clusters/{shared.guid}")] = FakeResponse(500, {"detail": "boom"})

    result = _shared(org, operator, "move_zentinelle_shared_cluster_to_install", shared)

    assert result.ok, result.errors
    gateway = ZentinelleClusterGateway.objects.get(cluster=shared)
    assert gateway.connection.is_install
    assert "could not be revoked in Zentinelle" in gateway.last_error and "by hand" in gateway.last_error


@override_config(ZENTINELLE_GATEWAY_ENABLED=True, ZENTINELLE_GATEWAY_IMAGE=IMAGE)
def test_a_move_whose_redeploy_fails_keeps_the_revoke_note_and_moving_again_redeploys(
    org, operator, plugin, admin, zentinelle, driver
):
    shared = _make_cluster(plugin, None, "shared")
    _registered_by(_connect(org).data.id, shared)
    assert _connect_install(org, operator, gateway_url=IN_CLUSTER).ok
    zentinelle.overrides[("DELETE", f"{API}/clusters/{shared.guid}")] = FakeResponse(500, {"detail": "boom"})
    driver.fail["Deployment"] = "connection refused"

    result = _shared(org, operator, "move_zentinelle_shared_cluster_to_install", shared)

    assert result.ok is False and "moved to the install connection" in result.errors[0].message
    gateway = ZentinelleClusterGateway.objects.get(cluster=shared)
    assert gateway.connection.is_install and gateway.gateway_deployed is False
    assert "could not be deployed" in gateway.last_error and "by hand" in gateway.last_error

    del driver.fail["Deployment"]
    zentinelle.calls.clear()
    again = _shared(org, operator, "move_zentinelle_shared_cluster_to_install", shared)

    assert again.ok, again.errors
    assert zentinelle.by() == []
    gateway.refresh_from_db()
    assert gateway.gateway_deployed is True and gateway.last_error == ""
    env = {
        e["name"]: e["value"]
        for e in driver.last("Deployment")["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["ZENTINELLE_URL"] == IN_CLUSTER


def test_a_move_zentinelle_refuses_leaves_the_cluster_with_the_organization(
    org, operator, plugin, admin, zentinelle, driver
):
    shared = _make_cluster(plugin, None, "shared")
    _registered_by(_connect(org).data.id, shared)
    assert _connect_install(org, operator).ok
    zentinelle.overrides[("POST", f"{API}/clusters")] = FakeResponse(409, {"detail": "already registered"})

    result = _shared(org, operator, "move_zentinelle_shared_cluster_to_install", shared)

    assert result.ok is False
    assert ZentinelleClusterGateway.objects.get(cluster=shared).connection.organization == org
    assert ("DELETE", f"{API}/clusters/{shared.guid}") not in [(m, p) for m, p, _who in zentinelle.by()]


# ---- removal and the installer --------------------------------------------


def test_the_operator_disconnects_the_install_and_its_shared_gateways(
    org, operator, plugin, admin, zentinelle, driver, audit
):
    shared = _make_cluster(plugin, None, "shared")
    assert _connect_install(org, operator).ok
    assert _shared(org, operator, "register_zentinelle_shared_cluster", shared).ok
    audit.clear()

    with _as(org, operator):
        result = OperationsMutation().disconnect_zentinelle_install(
            _info(operator), input=DisconnectZentinelleInput()
        )

    assert result.ok, result.errors
    assert not ZentinelleConnection.objects.filter(organization__isnull=True).exists()
    assert not ZentinelleClusterGateway.objects.filter(cluster=shared).exists()
    assert ("Secret", zentinelle_connect.GATEWAY_SECRET_NAME) in [(k, n) for _ns, k, n in driver.deleted]
    assert [(entry.action, entry.decision) for entry in audit] == [("zentinelle.install.disconnect", "ALLOW")]


def test_connect_zentinelle_install_connects_the_install_connection(zentinelle, audit, monkeypatch):
    monkeypatch.setenv("ZENTINELLE_ENROLLMENT_CODE", CODE)
    monkeypatch.setattr(
        "astrolift_operations.management.commands.connect_zentinelle.Command._enable_flags",
        staticmethod(lambda: None),
    )

    call_command("connect_zentinelle", install=True, url=BASE, code_env="ZENTINELLE_ENROLLMENT_CODE")

    assert _install().status == ZentinelleConnection.Status.CONNECTED
    assert [(entry.action, entry.organization_id) for entry in audit] == [
        ("zentinelle.install.connect", None)
    ]


def _host() -> str:
    from urllib.parse import urlsplit

    url = zentinelle_connect.install_base_url()
    return urlsplit(url).netloc or url
