"""``manage.py connect_zentinelle``: the installer's agent connects an org (calliope-installer#433).

Same fake Zentinelle as the mutation tests (#1887). The command must do the
mutation's exchange, read the code only from the environment, turn the
Constance flags on, and be a no-op when run again.
"""

from __future__ import annotations

import io
import uuid

import pytest
from constance import config
from constance.test import override_config
from django.core.management import CommandError, call_command

import core.cluster_management as cluster_management
import core.mutations as core_mutations
from astrolift_clusters.models import ProviderPlugin
from astrolift_identity.models import Organization
from astrolift_operations import zentinelle_connect
from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection
from astrolift_operations.tests.test_zentinelle_connect_1887 import (
    API,
    BASE,
    CODE,
    FakeDriver,
    FakeZentinelle,
    _make_cluster,
    _stored_in,
)
from core.secrets import EncryptedSecret, decrypt

pytestmark = pytest.mark.django_db

ENV = "ZENTINELLE_ENROLLMENT_CODE"


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
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def audit():
    captured = []
    original = core_mutations._audit_writer

    def _writer(entry):
        captured.append(entry)
        original(entry)

    core_mutations.register_audit_writer(_writer)
    yield captured
    core_mutations.register_audit_writer(original)


def _run(org_slug, *, url=BASE, code_env=ENV):
    out, err = io.StringIO(), io.StringIO()
    call_command("connect_zentinelle", org=org_slug, url=url, code_env=code_env, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


@override_config(ZENTINELLE_ENABLED=False, ZENTINELLE_GATEWAY_ENABLED=False)
def test_connects_the_org_with_the_code_from_the_env_and_turns_the_flags_on(
    org, zentinelle, audit, monkeypatch
):
    monkeypatch.setenv(ENV, CODE)

    output = _run(org.slug)

    [call] = zentinelle.calls
    assert (call.method, call.url) == ("POST", f"{BASE}{API}/connect")
    assert call.json["code"] == CODE
    row = ZentinelleConnection.objects.get(organization=org)
    assert row.status == ZentinelleConnection.Status.CONNECTED
    assert row.base_url == BASE
    assert row.zentinelle_install_id == "inst-1"
    assert decrypt(EncryptedSecret(row.credential_backend_kind, bytes(row.credential_ciphertext))) == (
        zentinelle.install_credential.encode()
    )
    assert config.ZENTINELLE_ENABLED is True
    assert config.ZENTINELLE_GATEWAY_ENABLED is True
    assert [entry.action for entry in audit] == ["zentinelle.connect"]
    assert audit[0].organization_id == org.pk
    assert zentinelle.install_credential not in output + repr(audit)
    assert CODE not in output + repr(audit)
    assert _stored_in(zentinelle.install_credential) == []
    assert _stored_in(CODE) == []


@override_config(ZENTINELLE_ENABLED=False, ZENTINELLE_GATEWAY_ENABLED=False)
def test_running_again_with_a_live_connection_is_a_no_op_success(org, zentinelle, monkeypatch):
    monkeypatch.setenv(ENV, CODE)
    _run(org.slug)
    first = ZentinelleConnection.objects.get(organization=org)
    config.ZENTINELLE_GATEWAY_ENABLED = False
    monkeypatch.delenv(ENV)

    output = _run(org.slug, url=BASE + "/")

    assert "nothing to do" in output
    assert zentinelle.paths() == [("POST", f"{API}/connect"), ("GET", f"{API}/install")]
    assert list(ZentinelleConnection.all_objects.filter(organization=org)) == [first]
    assert config.ZENTINELLE_GATEWAY_ENABLED is True


def test_a_live_connection_to_another_zentinelle_is_refused(org, zentinelle, monkeypatch):
    monkeypatch.setenv(ENV, CODE)
    _run(org.slug)

    with pytest.raises(CommandError, match="disconnects it"):
        _run(org.slug, url="https://other-zentinelle.test")
    assert len(zentinelle.calls) == 1


@override_config(ZENTINELLE_ENABLED=False, ZENTINELLE_GATEWAY_ENABLED=False)
def test_a_revoked_connection_is_replaced(org, zentinelle, monkeypatch):
    monkeypatch.setenv(ENV, CODE)
    _run(org.slug)
    revoked = ZentinelleConnection.objects.get(organization=org)
    revoked.status = ZentinelleConnection.Status.REVOKED
    revoked.save()

    _run(org.slug)

    live = ZentinelleConnection.objects.get(organization=org)
    assert live.pk != revoked.pk
    assert live.status == ZentinelleConnection.Status.CONNECTED
    revoked.refresh_from_db()
    assert revoked.deleted_at is not None
    assert [c.url for c in zentinelle.calls] == [f"{BASE}{API}/connect"] * 2


@override_config(ZENTINELLE_ENABLED=False, ZENTINELLE_GATEWAY_ENABLED=False)
def test_a_connection_zentinelle_revoked_but_not_yet_marked_here_is_replaced(org, zentinelle, monkeypatch):
    monkeypatch.setenv(ENV, CODE)
    _run(org.slug)
    stale = ZentinelleConnection.objects.get(organization=org)
    assert stale.status == ZentinelleConnection.Status.CONNECTED
    zentinelle.revoked = True

    def connect_again(method, url, **kwargs):
        if url.endswith(f"{API}/connect"):
            zentinelle.revoked = False
        return FakeZentinelle.__call__(zentinelle, method, url, **kwargs)

    monkeypatch.setattr(zentinelle_connect.requests, "request", connect_again)

    output = _run(org.slug)

    assert "nothing to do" not in output
    live = ZentinelleConnection.objects.get(organization=org)
    assert live.pk != stale.pk
    assert live.status == ZentinelleConnection.Status.CONNECTED
    stale.refresh_from_db()
    assert stale.status != ZentinelleConnection.Status.CONNECTED
    assert stale.deleted_at is not None
    assert ("GET", f"{API}/install") in zentinelle.paths()
    assert zentinelle.paths()[-1] == ("POST", f"{API}/connect")


def test_an_unreachable_zentinelle_fails_the_run_rather_than_reporting_success(org, zentinelle, monkeypatch):
    monkeypatch.setenv(ENV, CODE)
    _run(org.slug)
    zentinelle.overrides[("GET", f"{API}/install")] = zentinelle_connect.requests.ConnectionError("down")

    with pytest.raises(CommandError, match="could not reach Zentinelle"):
        _run(org.slug)
    assert ZentinelleConnection.objects.get(organization=org).status == ZentinelleConnection.Status.CONNECTED


@override_config(ZENTINELLE_ENABLED=False, ZENTINELLE_GATEWAY_ENABLED=False)
def test_a_refused_code_connects_nothing_and_leaves_the_flags_off(org, zentinelle, monkeypatch):
    monkeypatch.setenv(ENV, "enroll-wrong")

    with pytest.raises(CommandError, match="refused the enrollment code"):
        _run(org.slug)
    assert not ZentinelleConnection.all_objects.filter(organization=org).exists()
    assert config.ZENTINELLE_ENABLED is False
    assert config.ZENTINELLE_GATEWAY_ENABLED is False


def test_an_empty_env_or_a_code_passed_as_the_env_name_is_refused(org, zentinelle, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(CommandError, match="is empty"):
        _run(org.slug)
    with pytest.raises(CommandError, match="must name an environment variable"):
        _run(org.slug, code_env=CODE)
    assert zentinelle.calls == []


def test_an_unknown_org_or_a_bad_url_is_refused(org, zentinelle, monkeypatch):
    monkeypatch.setenv(ENV, CODE)
    with pytest.raises(CommandError, match="no organization"):
        _run("no-such-org")
    with pytest.raises(CommandError, match="https"):
        _run(org.slug, url="ftp://zentinelle.test")
    assert zentinelle.calls == []


@override_config(ZENTINELLE_ENABLED=False, ZENTINELLE_GATEWAY_ENABLED=False)
def test_a_gateway_url_is_stored_at_connect_and_applied_to_a_live_connection(
    org, zentinelle, monkeypatch, settings
):
    settings.DEBUG = False
    in_cluster = "http://zentinelle.zentinelle.svc.cluster.local:8000"
    monkeypatch.setenv(ENV, CODE)
    out = io.StringIO()
    call_command(
        "connect_zentinelle", org=org.slug, url=BASE, code_env=ENV, gateway_url=in_cluster, stdout=out
    )
    assert ZentinelleConnection.objects.get(organization=org).gateway_url == in_cluster

    monkeypatch.delenv(ENV)
    call_command("connect_zentinelle", org=org.slug, url=BASE, code_env=ENV, gateway_url="", stdout=out)
    assert ZentinelleConnection.objects.get(organization=org).gateway_url == ""

    with pytest.raises(CommandError, match="gatewayUrl must use https"):
        call_command(
            "connect_zentinelle",
            org=org.slug,
            url=BASE,
            code_env=ENV,
            gateway_url="http://z.example",
            stdout=out,
        )


# ---- the install connection registers the install's own clusters (#1905) ------


@pytest.fixture
def plugin():
    [row] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="k8s", slug="k8s_native", capabilities_manifest={}, config_schema={})]
    )
    return row


@pytest.fixture
def driver(monkeypatch) -> FakeDriver:
    fake = FakeDriver()
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda cluster: fake)
    return fake


def _run_install(*, register=True, code_env=ENV):
    out = io.StringIO()
    call_command(
        "connect_zentinelle",
        install=True,
        url=BASE,
        code_env=code_env,
        register_shared_clusters=register,
        stdout=out,
    )
    return out.getvalue()


def test_install_connect_registers_each_shared_cluster_once(org, plugin, zentinelle, driver, monkeypatch):
    shared = [_make_cluster(plugin, None, "shared-a"), _make_cluster(plugin, None, "shared-b")]
    _make_cluster(plugin, org, "orgs")
    retired = _make_cluster(plugin, None, "retired")
    retired.is_active = False
    retired.save()
    monkeypatch.setenv(ENV, CODE)

    output = _run_install()

    install = ZentinelleConnection.objects.get(organization__isnull=True)
    gateways = ZentinelleClusterGateway.objects.filter(connection=install)
    assert {g.cluster_id for g in gateways} == {c.pk for c in shared}
    for cluster in shared:
        assert f"{cluster.slug}: registered" in output

    # Again, connected: no code read, nothing registered twice.
    monkeypatch.delenv(ENV)
    zentinelle.calls.clear()
    added = _make_cluster(plugin, None, "added-later")
    output = _run_install()

    assert "nothing to do" in output and f"{added.slug}: registered" in output
    assert [p for p in zentinelle.paths() if p == ("POST", f"{API}/clusters")] == [
        ("POST", f"{API}/clusters")
    ]
    assert ZentinelleClusterGateway.objects.filter(connection=install).count() == 3


def test_a_cluster_that_cannot_register_fails_the_run_after_the_others(
    plugin, zentinelle, driver, monkeypatch
):
    _make_cluster(plugin, None, "shared")
    monkeypatch.setenv(ENV, CODE)
    driver.fail["Namespace"] = "forbidden"

    with pytest.raises(CommandError, match="shared clusters not registered"):
        _run_install()
    assert ZentinelleConnection.objects.filter(organization__isnull=True).exists()


def test_register_shared_clusters_needs_install(org, zentinelle, monkeypatch):
    monkeypatch.setenv(ENV, CODE)
    with pytest.raises(CommandError, match="goes with --install"):
        call_command(
            "connect_zentinelle", org=org.slug, url=BASE, code_env=ENV, register_shared_clusters=True
        )
    assert zentinelle.calls == []
