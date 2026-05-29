"""Tests for the plugin parity harness (#62)."""

from __future__ import annotations

from _sdk.base import ProviderPlugin
from _sdk.cluster import ClusterDriver
from _sdk.dns import DnsDriver
from _sdk.identity import WorkloadIdentityDriver
from _sdk.ingress import IngressDriver
from _sdk.managed_service import ManagedServiceDriver
from _sdk.matrix_check import load_plugins
from _sdk.parity import (
    check_all_plugins,
    check_class_against_protocol,
    check_plugin_parity,
)
from _sdk.registry import ImageRegistryDriver
from _sdk.secrets import SecretsBackend
from _sdk.tls import TlsDriver

ROLE_PROTOCOLS = {
    "cluster": ClusterDriver,
    "ingress": IngressDriver,
    "dns": DnsDriver,
    "tls": TlsDriver,
    "secrets": SecretsBackend,
    "identity": WorkloadIdentityDriver,
    "registry": ImageRegistryDriver,
}


def test_class_with_all_methods_passes() -> None:
    class FullDriver:
        def get(self, path):
            return None
        def upsert(self, path, kvs):
            return None
        def delete(self, path):
            return None
        def list(self, prefix):
            return []

    issues = check_class_against_protocol(
        plugin_id="x", role_or_kind="secrets",
        cls=FullDriver, protocol=SecretsBackend,
    )
    assert issues == []


def test_class_missing_method_flagged() -> None:
    class Partial:
        def get(self, path):
            return None
        # missing upsert / delete / list

    issues = check_class_against_protocol(
        plugin_id="x", role_or_kind="secrets",
        cls=Partial, protocol=SecretsBackend,
    )
    codes = {i.code for i in issues}
    assert codes == {"missing_method"}
    methods_missing = {i.method for i in issues}
    assert "upsert" in methods_missing
    assert "delete" in methods_missing
    assert "list" in methods_missing


def test_check_plugin_parity_with_full_plugin() -> None:
    class _Full:
        # SecretsBackend signatures
        def get(self, path):
            return None
        def upsert(self, path, kvs):
            return None
        def delete(self, path):
            return None
        def list(self, prefix):
            return []

    plugin = ProviderPlugin(
        id="x", display_name="x",
        drivers={"secrets": _Full},
    )
    report = check_plugin_parity(
        plugin=plugin,
        role_protocols={"secrets": SecretsBackend},
        managed_service_protocol=ManagedServiceDriver,
    )
    assert report.ok is True


def test_real_plugins_pass_parity() -> None:
    """Every shipped plugin (AWS, GCP, Azure, k8s_native) must
    pass parity. This is the test that catches the case where a
    new managed-service driver lands without all the required
    methods."""
    plugins = load_plugins()
    if not plugins:
        return  # entry-point group empty in some test contexts
    report = check_all_plugins(
        plugins=plugins,
        role_protocols=ROLE_PROTOCOLS,
        managed_service_protocol=ManagedServiceDriver,
    )
    assert report.ok is True, [
        f"{i.plugin_id}/{i.role_or_kind}: {i.detail}"
        for i in report.issues
    ]


def test_parity_skips_unmapped_roles() -> None:
    """A driver registered under a role that's not in the
    role_protocols dict should NOT cause a failure — it just
    doesn't get checked."""
    class _Anything:
        pass

    plugin = ProviderPlugin(
        id="x", display_name="x",
        drivers={"experimental_role": _Anything},
    )
    report = check_plugin_parity(
        plugin=plugin,
        role_protocols={"secrets": SecretsBackend},  # no entry for experimental_role
        managed_service_protocol=ManagedServiceDriver,
    )
    assert report.ok is True
