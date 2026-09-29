from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from astrolift_drivers.registry import PluginManifest, plugins

from _sdk import UnsupportedOperationError
from _sdk.cluster_capabilities import ClusterCapabilities
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.managed.api_gateway import (
    API_VERSION,
    GatewayAPIConfig,
    GatewayAPIDriver,
)
from k8s_native.plugin import PLUGIN
from k8s_native.preflight import preflight


@dataclass
class _Result:
    ok: bool = True
    errors: list[str] = field(default_factory=list)

    def summary(self) -> list[str]:
        return list(self.errors)


class _Cluster:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.applied: list[tuple[str, str, list[dict[str, Any]]]] = []
        self.deleted: list[tuple[str, str, list[dict[str, Any]]]] = []
        self.apply_result = _Result()
        self.delete_result = _Result()

    def get_manifest(self, cluster_id, namespace, kind, name):
        del cluster_id
        return self.objects.get((kind, namespace, name))

    def apply_manifests(self, cluster_id, namespace, manifests):
        self.applied.append((cluster_id, namespace, manifests))
        if self.apply_result.ok:
            for manifest in manifests:
                self.objects[
                    (f"{manifest['apiVersion']}/{manifest['kind']}", namespace, manifest["metadata"]["name"])
                ] = manifest
        return self.apply_result

    def delete_manifests(self, cluster_id, namespace, manifests):
        self.deleted.append((cluster_id, namespace, manifests))
        if self.delete_result.ok:
            for manifest in manifests:
                self.objects.pop(
                    (f"{manifest['apiVersion']}/{manifest['kind']}", namespace, manifest["metadata"]["name"]),
                    None,
                )
        return self.delete_result


def _listener(**overrides: Any) -> dict[str, Any]:
    return {"name": "http", "protocol": "HTTP", "port": 80, **overrides}


def _route(**overrides: Any) -> dict[str, Any]:
    value = {
        "kind": "HTTPRoute",
        "name": "api",
        "spec": {
            "hostnames": ["api.example.test"],
            "rules": [{"backendRefs": [{"name": "api", "port": 8080}]}],
        },
    }
    value.update(overrides)
    return value


def _spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="steady-md",
        app_id="app-1",
        app_slug="triage",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="edge",
        size="custom",
        config={"listeners": [_listener()], **config},
        managed_service_id="service-1",
    )


def _driver(cluster: _Cluster | None = None, **policy: Any) -> tuple[GatewayAPIDriver, _Cluster]:
    live = cluster or _Cluster()
    return GatewayAPIDriver(
        config=GatewayAPIConfig(
            cluster_driver=live,
            gateway_class_name="envoy-gateway",
            **policy,
        ),
    ), live


def _gateway(cluster: _Cluster) -> dict[str, Any]:
    return cluster.objects[(f"{API_VERSION}/Gateway", "steady-md-triage", "triage-prod-edge")]


def _http_route(cluster: _Cluster) -> dict[str, Any]:
    return cluster.objects[(f"{API_VERSION}/HTTPRoute", "steady-md-triage", "api")]


@pytest.fixture
def _registered_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    drivers = dict(PLUGIN.drivers)
    drivers.update(
        {f"managed:{kind}:{variant}": driver for (kind, variant), driver in PLUGIN.managed_service_drivers.items()},
    )
    monkeypatch.setattr(
        plugins,
        "_plugins",
        {
            "k8s_native": PluginManifest(
                plugin_id="k8s_native",
                display_name=PLUGIN.display_name,
                version="test",
                drivers=drivers,
            ),
        },
    )


def test_provision_renders_owned_gateway_and_attached_http_route() -> None:
    driver, cluster = _driver()

    result = driver.provision(_spec(routes=[_route()]))

    assert result.ok is True
    assert result.ready is False
    assert result.handle == "api_gateway/cluster-1/steady-md-triage/triage-prod-edge"
    gateway = _gateway(cluster)
    assert gateway["spec"]["gatewayClassName"] == "envoy-gateway"
    assert gateway["metadata"]["labels"]["astrolift.io/managed-service-id"] == "service-1"
    assert "HTTPRoute" in gateway["metadata"]["annotations"]["astrolift.io/managed-children"]
    route = _http_route(cluster)
    assert route["spec"]["parentRefs"] == [{"name": "triage-prod-edge"}]
    assert route["spec"]["rules"][0]["backendRefs"][0]["name"] == "api"


def test_gateway_exposes_addresses_infrastructure_tls_and_listener_sets() -> None:
    driver, cluster = _driver(allow_listener_sets=True)
    listener = _listener(
        name="https",
        protocol="HTTPS",
        port=443,
        hostname="api.example.test",
        tls={"mode": "Terminate", "certificateRefs": [{"name": "api-tls"}]},
    )

    result = driver.provision(
        _spec(
            listeners=[listener],
            addresses=[{"type": "IPAddress", "value": "192.0.2.1"}],
            infrastructure={"annotations": {"example.test/tier": "premium"}},
            allowed_listeners={"namespaces": {"from": "Same"}},
        ),
    )

    assert result.ok is True
    gateway = _gateway(cluster)
    assert gateway["spec"]["addresses"][0]["value"] == "192.0.2.1"
    assert gateway["spec"]["infrastructure"]["annotations"]["example.test/tier"] == "premium"
    assert gateway["spec"]["allowedListeners"]["namespaces"]["from"] == "Same"


@pytest.mark.parametrize(
    "config",
    [
        {"listeners": []},
        {"listeners": [_listener(name="BAD NAME")]},
        {"listeners": [_listener(protocol="HTTPS")]},
        {
            "listeners": [
                _listener(
                    name="https",
                    protocol="HTTPS",
                    port=443,
                    tls={
                        "mode": "Terminate",
                        "certificateRefs": [{"name": "tls", "namespace": "other"}],
                    },
                ),
            ],
        },
        {"listeners": [_listener(allowedRoutes={"namespaces": {"from": "All"}})]},
        {"listeners": [_listener()], "allowed_listeners": {"namespaces": {"from": "Same"}}},
        {
            "listeners": [_listener()],
            "resources": [
                {
                    "kind": "ReferenceGrant",
                    "name": "unsafe-grant",
                    "spec": {"from": [], "to": []},
                },
            ],
        },
        {
            "listeners": [_listener()],
            "resources": [
                {
                    "kind": "BackendTLSPolicy",
                    "name": "custom-target",
                    "spec": {
                        "targetRefs": [{"group": "example.test", "kind": "Backend", "name": "api"}],
                        "validation": {"wellKnownCACertificates": "System"},
                    },
                },
            ],
        },
        {
            "listeners": [_listener()],
            "resources": [
                {
                    "kind": "ListenerSet",
                    "name": "unsafe-listeners",
                    "spec": {
                        "parentRef": {"name": "triage-prod-edge"},
                        "listeners": [
                            _listener(
                                name="shared",
                                allowedRoutes={"namespaces": {"from": "All"}},
                            ),
                        ],
                    },
                },
            ],
        },
        {"listeners": [_listener()], "routes": [_route(kind="TCPRoute")]},
        {
            "listeners": [_listener()],
            "routes": [_route(spec={"rules": [{"backendRefs": [{"name": "api", "namespace": "other"}]}]})],
        },
        {
            "listeners": [_listener()],
            "routes": [
                _route(spec={"rules": [{"backendRefs": [{"group": "example.test", "kind": "API", "name": "x"}]}]})
            ],
        },
        {
            "listeners": [_listener()],
            "routes": [_route(spec={"rules": [{"filters": [{"type": "ExtensionRef", "extensionRef": {}}]}]})],
        },
        {"listeners": [_listener()], "labels": {"app.kubernetes.io/managed-by": "foreign"}},
        {"listeners": [_listener()], "unknown": True},
    ],
)
def test_install_security_policy_and_shape_fail_before_mutation(config: dict[str, Any]) -> None:
    driver, cluster = _driver()

    result = driver.provision(replace(_spec(), config=config))

    assert result.ok is False
    assert result.errors == ["invalid_gateway_api_config"]
    assert cluster.applied == []


def test_install_policy_can_enable_advanced_gateway_api_features() -> None:
    driver, cluster = _driver(
        allow_cross_namespace_routes=True,
        allow_cross_namespace_backends=True,
        allow_custom_backends=True,
        allow_extension_refs=True,
        allow_experimental_routes=True,
    )
    route = _route(
        kind="TCPRoute",
        spec={
            "rules": [
                {
                    "backendRefs": [
                        {
                            "group": "example.test",
                            "kind": "Backend",
                            "name": "api",
                            "namespace": "other",
                        },
                    ],
                    "filters": [{"type": "ExtensionRef", "extensionRef": {"name": "policy"}}],
                },
            ],
        },
    )

    result = driver.provision(
        _spec(
            listeners=[_listener(allowedRoutes={"namespaces": {"from": "All"}})],
            routes=[route],
        ),
    )

    assert result.ok is True
    assert any(manifest["kind"] == "TCPRoute" for manifest in cluster.applied[-1][2])


def test_listener_set_cannot_bypass_cross_namespace_route_policy() -> None:
    driver, cluster = _driver(allow_listener_sets=True)
    resource = {
        "kind": "ListenerSet",
        "name": "unsafe-listeners",
        "spec": {
            "parentRef": {"name": "triage-prod-edge"},
            "listeners": [
                _listener(
                    name="shared",
                    allowedRoutes={"namespaces": {"from": "All"}},
                ),
            ],
        },
    }

    result = driver.provision(_spec(resources=[resource]))

    assert result.ok is False
    assert cluster.applied == []


def test_auxiliary_gateway_resources_are_owned_and_pruned() -> None:
    driver, cluster = _driver(allow_listener_sets=True)
    resources = [
        {
            "kind": "BackendTLSPolicy",
            "name": "api-tls",
            "spec": {
                "targetRefs": [{"group": "", "kind": "Service", "name": "api"}],
                "validation": {"hostname": "api.example.test", "wellKnownCACertificates": "System"},
            },
        },
        {
            "kind": "ListenerSet",
            "name": "extra-listeners",
            "spec": {
                "parentRef": {"name": "triage-prod-edge"},
                "listeners": [_listener(name="metrics", port=9090)],
            },
        },
    ]
    provisioned = driver.provision(_spec(resources=resources))

    assert provisioned.ok is True
    assert {manifest["kind"] for manifest in cluster.applied[-1][2]} == {
        "Gateway",
        "BackendTLSPolicy",
        "ListenerSet",
    }
    updated = driver.update(UpdateSpec(provisioned.handle, config={"listeners": [_listener()]}))

    assert updated.ok is True
    assert {stub["kind"] for stub in cluster.deleted[-1][2]} == {
        "BackendTLSPolicy",
        "ListenerSet",
    }


def test_gateway_class_override_requires_install_policy() -> None:
    denied, _ = _driver()
    allowed, cluster = _driver(allow_class_override=True)

    assert denied.provision(_spec(gateway_class_name="cilium")).ok is False
    assert allowed.provision(_spec(gateway_class_name="cilium")).ok is True
    assert _gateway(cluster)["spec"]["gatewayClassName"] == "cilium"


def test_existing_gateway_and_routes_are_refused_even_with_their_exact_uids() -> None:
    driver, cluster = _driver()
    gateway_key = (f"{API_VERSION}/Gateway", "steady-md-triage", "triage-prod-edge")
    route_key = (f"{API_VERSION}/HTTPRoute", "steady-md-triage", "api")
    foreign_gateway = {"metadata": {"uid": "gw-uid", "labels": {}}}
    cluster.objects[gateway_key] = foreign_gateway

    refused = driver.provision(_spec(routes=[_route()]))
    assert refused.ok is False
    assert "operator-authorized" in refused.message

    # Knowing the uid proved only that the caller could see the object.
    # Adoption is operator-only (#2021); the flags are rejected, not ignored.
    flagged = driver.provision(_spec(adopt_existing=True, expected_existing_uid="gw-uid", routes=[_route()]))
    assert flagged.ok is False
    assert "unsupported Gateway config fields" in flagged.message

    del cluster.objects[gateway_key]
    foreign_route = {"metadata": {"uid": "route-uid", "labels": {}}}
    cluster.objects[route_key] = foreign_route
    route_refused = driver.provision(_spec(routes=[_route()]))
    assert route_refused.ok is False
    assert "HTTPRoute api already exists" in route_refused.message
    route_flagged = driver.provision(
        _spec(routes=[_route(adopt_existing=True, expected_existing_uid="route-uid")]),
    )
    assert route_flagged.ok is False
    assert "unsupported route fields" in route_flagged.message
    assert cluster.objects[route_key] is foreign_route
    assert cluster.applied == []


def test_reprovision_never_hijacks_another_astrolift_gateway() -> None:
    driver, _ = _driver()
    assert driver.provision(_spec()).ok is True

    result = driver.provision(replace(_spec(), managed_service_id="service-2"))

    assert result.ok is False
    assert "another Astrolift" in result.message


def test_update_prunes_removed_owned_routes_and_rejects_class_change() -> None:
    driver, cluster = _driver(allow_class_override=True)
    provisioned = driver.provision(_spec(routes=[_route()]))

    updated = driver.update(UpdateSpec(provisioned.handle, config={"listeners": [_listener()]}))
    class_change = driver.update(
        UpdateSpec(provisioned.handle, config={"listeners": [_listener()], "gateway_class_name": "cilium"}),
    )

    assert updated.ok is True
    assert (f"{API_VERSION}/HTTPRoute", "steady-md-triage", "api") not in cluster.objects
    assert any(stub["kind"] == "HTTPRoute" for stub in cluster.deleted[-1][2])
    assert class_change.ok is False
    assert class_change.retryable is False


def test_update_and_delete_refuse_foreign_children() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec(routes=[_route()]))
    _http_route(cluster)["metadata"]["labels"]["astrolift.io/managed-service-id"] = "someone-else"

    updated = driver.update(UpdateSpec(provisioned.handle, config={"listeners": [_listener()]}))
    deleted = driver.deprovision(DeprovisionSpec(provisioned.handle), force_destroy=True)

    assert updated.ok is False
    assert updated.retryable is False
    assert deleted.ok is False
    assert deleted.retryable is False


def test_deprovision_honors_protection_and_gateway_ownership() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())

    protected = driver.deprovision(
        DeprovisionSpec(provisioned.handle, config={"deletion_protection": True}),
    )
    _gateway(cluster)["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "foreign"
    forced_foreign = driver.deprovision(DeprovisionSpec(provisioned.handle), force_destroy=True)

    assert protected.ok is False
    assert protected.retryable is False
    assert forced_foreign.ok is False
    assert forced_foreign.retryable is False


def test_status_waits_for_gateway_address_and_route_conditions() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec(routes=[_route()]))
    gateway = _gateway(cluster)
    gateway["status"] = {
        "addresses": [{"type": "Hostname", "value": "gateway.example.test"}],
        "conditions": [
            {"type": "Accepted", "status": "True"},
            {"type": "Programmed", "status": "True"},
        ],
    }

    assert driver.status(ServiceHandle(provisioned.handle)).state == "provisioning"
    _http_route(cluster)["status"] = {
        "parents": [
            {
                "conditions": [
                    {"type": "Accepted", "status": "True"},
                    {"type": "ResolvedRefs", "status": "True"},
                ],
            },
        ],
    }
    assert driver.status(ServiceHandle(provisioned.handle)).state == "available"
    _http_route(cluster)["status"]["parents"][0]["conditions"][1] = {
        "type": "ResolvedRefs",
        "status": "False",
        "reason": "BackendNotFound",
    }
    assert driver.status(ServiceHandle(provisioned.handle)).state == "error"


def test_binding_uses_programmed_secure_ipv6_address() -> None:
    driver, cluster = _driver()
    listener = _listener(
        name="https",
        protocol="HTTPS",
        port=8443,
        tls={"mode": "Terminate", "certificateRefs": [{"name": "api-tls"}]},
    )
    provisioned = driver.provision(_spec(listeners=[listener]))
    _gateway(cluster)["status"] = {
        "addresses": [{"type": "IPAddress", "value": "2001:db8::1"}],
        "conditions": [
            {"type": "Accepted", "status": "True"},
            {"type": "Programmed", "status": "True"},
        ],
    }

    binding = driver.binding(ServiceHandle(provisioned.handle))

    assert binding.env_vars["API_GATEWAY_URL"].literal == "https://[2001:db8::1]:8443"
    assert binding.env_vars["API_GATEWAY_PORT"].literal == "8443"


def test_binding_requires_programmed_address() -> None:
    driver, _ = _driver()
    provisioned = driver.provision(_spec())

    with pytest.raises(ValueError, match="not been programmed"):
        driver.binding(ServiceHandle(provisioned.handle))


def test_snapshot_restore_are_explicitly_unsupported() -> None:
    driver, _ = _driver()

    with pytest.raises(UnsupportedOperationError):
        driver.snapshot(ServiceHandle("api_gateway/cluster/ns/name"))
    with pytest.raises(UnsupportedOperationError):
        driver.restore(object(), _spec())


def test_plugin_catalogue_preflight_and_contract_are_executable(_registered_plugin: None) -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    assert PLUGIN.managed_service_drivers[("api_gateway", "gateway_api")] is GatewayAPIDriver
    missing = preflight(
        kind="api_gateway",
        variant="gateway_api",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32",
            installed_crds=frozenset(),
            crd_inventory_probed=True,
        ),
    )
    installed = preflight(
        kind="api_gateway",
        variant="gateway_api",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32",
            installed_crds=frozenset(
                {
                    "gateways.gateway.networking.k8s.io",
                    "httproutes.gateway.networking.k8s.io",
                },
            ),
            crd_inventory_probed=True,
        ),
    )
    # include_extended, because api_gateway is opt-in tier: Astrolift already
    # terminates ingress and routes to workloads, so a bookable gateway is a
    # second answer to a question the platform has answered (#1470). The driver
    # is unaffected by that cut, which is exactly what this asserts.
    row = next(
        item
        for item in list_catalog("k8s_native", include_extended=True)
        if (item.kind, item.variant) == ("api_gateway", "gateway_api")
    )

    assert missing.ok is False
    assert "v1.5.0" in missing.install_hints[0]
    assert installed.ok is True
    assert row.available is True
    assert row.status == "preview"
    assert row.config_schema["required"] == ["listeners"]
    assert {"API_GATEWAY_URL", "API_GATEWAY_HOST", "API_GATEWAY_PORT"} <= set(row.binding_envs)
