from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.availability import MATRIX
from _sdk.cluster import ClusterContext
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig
from k8s_native.managed.observability_kube_prometheus import (
    KubePrometheusConfig,
    KubePrometheusStackDriver,
)
from k8s_native.plugin import PLUGIN


@dataclass
class _Result:
    ok: bool = True
    errors: list[str] = field(default_factory=list)

    def summary(self) -> list[str]:
        return list(self.errors)


class _Cluster:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str | None, str], dict[str, Any]] = {}
        self.applied: list[list[dict[str, Any]]] = []
        self.deleted: list[list[dict[str, Any]]] = []
        self.apply_result = _Result()
        self.delete_result = _Result()
        for crd in (
            "prometheuses.monitoring.coreos.com",
            "prometheusrules.monitoring.coreos.com",
            "servicemonitors.monitoring.coreos.com",
            "podmonitors.monitoring.coreos.com",
        ):
            self.objects[("apiextensions.k8s.io/v1/CustomResourceDefinition", None, crd)] = {
                "apiVersion": "apiextensions.k8s.io/v1",
                "kind": "CustomResourceDefinition",
                "metadata": {"name": crd},
            }
        for service in (
            "astrolift-kube-prometheus-stack-prometheus",
            "astrolift-kube-prometheus-stack-alertmanager",
            "astrolift-kube-prometheus-stack-grafana",
        ):
            self.objects[("v1/Service", "astrolift-system", service)] = {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": service, "namespace": "astrolift-system"},
            }

    def get_manifest(self, cluster_id, namespace, kind, name):
        del cluster_id
        value = self.objects.get((kind, namespace, name))
        return copy.deepcopy(value) if value is not None else None

    def apply_manifests(self, cluster_id, namespace, manifests):
        del cluster_id
        self.applied.append(copy.deepcopy(manifests))
        if self.apply_result.ok:
            for manifest in manifests:
                self.objects[
                    (
                        f"{manifest['apiVersion']}/{manifest['kind']}",
                        namespace,
                        manifest["metadata"]["name"],
                    )
                ] = copy.deepcopy(manifest)
        return self.apply_result

    def delete_manifests(self, cluster_id, namespace, manifests, **kwargs):
        del cluster_id, kwargs
        self.deleted.append(copy.deepcopy(manifests))
        if self.delete_result.ok:
            for manifest in manifests:
                self.objects.pop(
                    (
                        f"{manifest['apiVersion']}/{manifest['kind']}",
                        namespace,
                        manifest["metadata"]["name"],
                    ),
                    None,
                )
        return self.delete_result


def _spec(**overrides: Any) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "acme",
        "app_id": "app-1",
        "app_slug": "payments",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "monitoring",
        "size": "custom",
        "config": {},
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _driver(
    cluster: _Cluster | None = None,
    **policy: Any,
) -> tuple[KubePrometheusStackDriver, _Cluster]:
    live = cluster or _Cluster()
    return KubePrometheusStackDriver(config=KubePrometheusConfig(cluster_driver=live, **policy)), live


def _root(cluster: _Cluster) -> dict[str, Any]:
    return cluster.objects[("v1/ConfigMap", "acme-payments", "payments-prod-monitoring")]


def _children(cluster: _Cluster) -> list[dict[str, Any]]:
    refs = json.loads(_root(cluster)["metadata"]["annotations"]["astrolift.io/children"])
    return [cluster.objects[(f"{ref['apiVersion']}/{ref['kind']}", "acme-payments", ref["name"])] for ref in refs]


def _monitor_config(**overrides: Any) -> dict[str, Any]:
    monitor = {
        "name": "api",
        "selector": {"match_labels": {"app.kubernetes.io/name": "payments"}},
        "endpoints": [{"port": "metrics"}],
        **overrides,
    }
    return {"service_monitors": [monitor]}


def test_default_bundle_creates_safe_rules_dashboard_and_portable_binding() -> None:
    driver, cluster = _driver()

    result = driver.provision(_spec())

    assert result.ok is True
    assert result.ready is True
    assert result.handle == "observability/cluster-1/acme-payments/payments-prod-monitoring"
    root = _root(cluster)
    assert root["metadata"]["labels"]["astrolift.io/component"] == "kube-prometheus-bundle"
    children = _children(cluster)
    assert [child["kind"] for child in children] == ["PrometheusRule", "ConfigMap"]
    rule = children[0]
    assert all('namespace="acme-payments"' in row["expr"] for row in rule["spec"]["groups"][0]["rules"])
    dashboard = json.loads(children[1]["data"]["overview.json"])
    assert dashboard["uid"] == "payments-prod-monitoring-overview-dashboard"
    assert all('namespace="acme-payments"' in panel["targets"][0]["expr"] for panel in dashboard["panels"])

    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["OBSERVABILITY_PROVIDER"].literal == "kube-prometheus-stack"
    assert binding.env_vars["PROMETHEUS_URL"].literal == (
        "http://astrolift-kube-prometheus-stack-prometheus.astrolift-system.svc.cluster.local:9090"
    )
    assert binding.env_vars["OBSERVABILITY_NAMESPACE"].literal == "acme-payments"
    assert binding.env_vars["DASHBOARD_URL"].literal.endswith(
        "/d/payments-prod-monitoring-overview-dashboard",
    )
    assert not binding.iam_grants


def test_service_and_pod_monitors_render_namespaced_safe_endpoints() -> None:
    driver, cluster = _driver()
    config = _monitor_config(sample_limit=5_000, target_limit=20)
    config["pod_monitors"] = [
        {
            "name": "workers",
            "selector": {"match_expressions": [{"key": "role", "operator": "In", "values": ["worker"]}]},
            "endpoints": [
                {
                    "port": "metrics",
                    "path": "/internal/metrics",
                    "scheme": "https",
                    "interval": "1m",
                    "scrape_timeout": "15s",
                    "honor_timestamps": False,
                },
            ],
        },
    ]

    result = driver.provision(_spec(config=config))

    assert result.ok is True
    monitors = {child["kind"]: child for child in _children(cluster) if child["kind"].endswith("Monitor")}
    service = monitors["ServiceMonitor"]
    assert service["spec"]["namespaceSelector"] == {"matchNames": ["acme-payments"]}
    assert service["spec"]["sampleLimit"] == 5_000
    assert service["spec"]["targetLimit"] == 20
    assert service["spec"]["endpoints"][0]["followRedirects"] is False
    pod = monitors["PodMonitor"]
    assert pod["spec"]["podMetricsEndpoints"][0] == {
        "port": "metrics",
        "path": "/internal/metrics",
        "scheme": "https",
        "interval": "1m",
        "scrapeTimeout": "15s",
        "honorLabels": False,
        "honorTimestamps": False,
        "followRedirects": False,
    }


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (_monitor_config(namespace_names=["other-team"]), "cross-namespace"),
        (_monitor_config(endpoints=[{"port": "metrics", "interval": "5s"}]), "below cluster policy"),
        (_monitor_config(endpoints=[{"port": "metrics", "interval": "30s", "scrape_timeout": "1m"}]), "cannot exceed"),
        (_monitor_config(endpoints=[{"port": "metrics", "honor_labels": True}]), "honor_labels"),
        (_monitor_config(endpoints=[{"port": "metrics", "authorization": {}}]), "unsupported fields"),
        (_monitor_config(endpoints=[{"port": "bad port"}]), "named port"),
        (_monitor_config(endpoints=[{"port": "metrics", "path": "/metrics?token=secret"}]), "non-traversing"),
        (_monitor_config(sample_limit=50_001), "cluster policy limit"),
        (_monitor_config(target_limit=0), "cluster policy limit"),
        (_monitor_config(selector={}), "cannot be empty"),
        (_monitor_config(labels={"astrolift.io/component": "spoof"}), "reserved"),
        ({"rule_groups": [{"name": "custom", "rules": []}]}, "custom Prometheus rules"),
        ({"dashboards": [{"name": "custom", "document": {}}]}, "custom Grafana dashboards"),
        ({"unknown": True}, "unsupported fields"),
    ],
)
def test_tenant_monitoring_policy_fails_closed(config: dict[str, Any], message: str) -> None:
    driver, _ = _driver()

    result = driver.provision(_spec(config=config))

    assert result.ok is False
    assert message in result.message


def test_cross_namespace_custom_rules_dashboards_and_honor_labels_require_operator_opt_in() -> None:
    driver, cluster = _driver(
        allow_cross_namespace=True,
        allowed_target_namespaces=("shared-exporters",),
        allow_custom_rules=True,
        allow_custom_dashboards=True,
        allow_honor_labels=True,
        min_scrape_interval_seconds=5,
    )
    config = _monitor_config(
        namespace_names=["shared-exporters", "acme-payments"],
        endpoints=[{"port": "metrics", "interval": "5s", "honor_labels": True}],
    )
    config["rule_groups"] = [
        {
            "name": "latency",
            "interval": "15s",
            "rules": [
                {
                    "record": "astrolift:http_latency_seconds:rate5m",
                    "expr": "rate(http_request_duration_seconds_sum[5m])",
                },
            ],
        },
    ]
    config["dashboards"] = [{"name": "latency", "document": {"schemaVersion": 39, "panels": []}}]

    result = driver.provision(_spec(config=config))

    assert result.ok is True
    children = _children(cluster)
    monitor = next(child for child in children if child["kind"] == "ServiceMonitor")
    assert monitor["spec"]["namespaceSelector"]["matchNames"] == ["acme-payments", "shared-exporters"]
    assert monitor["spec"]["endpoints"][0]["honorLabels"] is True
    rules = next(child for child in children if child["kind"] == "PrometheusRule")
    assert len(rules["spec"]["groups"]) == 2
    dashboards = [child for child in children if child["kind"] == "ConfigMap"]
    assert len(dashboards) == 2


def test_empty_custom_collections_do_not_require_policy_opt_in() -> None:
    driver, _ = _driver()

    result = driver.provision(_spec(config={"rule_groups": [], "dashboards": []}))

    assert result.ok is True


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ({"dashboard_label_key": "astrolift.io/managed-service-id"}, "reserved"),
        ({"monitoring_namespace": "Bad Namespace"}, "DNS label"),
        ({"prometheus_url": "javascript:alert(1)"}, "HTTP(S) URL"),
        ({"grafana_url": "https://user:pass@grafana.example.test"}, "embedded credentials"),
        ({"grafana_url": "https://grafana.example.test/?token=secret"}, "HTTP(S) URL"),
        ({"max_endpoints_per_monitor": 0}, "positive integer"),
        ({"allowed_target_namespaces": ("bad namespace",)}, "invalid"),
    ],
)
def test_invalid_install_policy_fails_closed(policy: dict[str, Any], message: str) -> None:
    driver, _ = _driver(**policy)

    result = driver.provision(_spec())

    assert result.ok is False
    assert message in result.message


def test_custom_dashboard_cannot_spoof_uid_and_default_names_are_reserved() -> None:
    driver, cluster = _driver(allow_custom_dashboards=True, allow_custom_rules=True)
    config = {
        "dashboards": [
            {
                "name": "custom",
                "document": {"id": 123, "uid": "foreign", "schemaVersion": 39, "panels": []},
            },
        ],
    }

    result = driver.provision(_spec(config=config))

    assert result.ok is True
    custom = next(child for child in _children(cluster) if child["metadata"]["name"].endswith("custom-dashboard"))
    document = json.loads(custom["data"]["custom.json"])
    assert document["uid"] == custom["metadata"]["name"]
    assert "id" not in document

    dashboard_collision = driver.update(
        UpdateSpec(
            handle=result.handle,
            config={"dashboards": [{"name": "overview", "document": {}}]},
        ),
    )
    rule_collision = driver.update(
        UpdateSpec(
            handle=result.handle,
            config={
                "rule_groups": [
                    {
                        "name": "astrolift-workload-health",
                        "rules": [{"alert": "Duplicate", "expr": "vector(1)"}],
                    },
                ],
            },
        ),
    )
    assert dashboard_collision.ok is False and "collides" in dashboard_collision.message
    assert rule_collision.ok is False and "collides" in rule_collision.message


def test_preflight_requires_operator_crds_and_services() -> None:
    cluster = _Cluster()
    cluster.objects.pop(
        ("apiextensions.k8s.io/v1/CustomResourceDefinition", None, "servicemonitors.monitoring.coreos.com"),
    )
    missing_crd, _ = _driver(cluster)

    crd_result = missing_crd.provision(_spec())
    assert crd_result.ok is False
    assert "CRD servicemonitors" in crd_result.message

    cluster = _Cluster()
    cluster.objects.pop(
        ("v1/Service", "astrolift-system", "astrolift-kube-prometheus-stack-grafana"),
    )
    missing_service, _ = _driver(cluster)
    service_result = missing_service.provision(_spec())
    assert service_result.ok is False
    assert "grafana is not installed" in service_result.message

    external, _ = _driver(cluster, verify_services=False)
    assert external.provision(_spec()).ok is True


def test_idempotency_collisions_update_and_stale_pruning() -> None:
    driver, cluster = _driver()
    first = driver.provision(_spec(config=_monitor_config()))
    second = driver.provision(_spec(config=_monitor_config()))
    assert first.ok and second.ok

    updated = driver.update(UpdateSpec(handle=first.handle, config={"standard_rules": False}))
    assert updated.ok is True
    assert [child["kind"] for child in _children(cluster)] == ["ConfigMap"]
    assert len(cluster.deleted) == 1
    assert {row["kind"] for row in cluster.deleted[0]} == {"ServiceMonitor", "PrometheusRule"}

    _root(cluster)["metadata"]["labels"]["astrolift.io/managed-service-id"] = "other"
    collision = driver.provision(_spec())
    assert collision.ok is False
    assert "another Astrolift resource" in collision.message


def test_update_validates_stale_ownership_before_mutating_inventory() -> None:
    driver, cluster = _driver()
    result = driver.provision(_spec(config=_monitor_config()))
    stale = next(child for child in _children(cluster) if child["kind"] == "ServiceMonitor")
    stale["metadata"]["labels"]["astrolift.io/managed-service-id"] = "other"
    key = (f"{stale['apiVersion']}/{stale['kind']}", "acme-payments", stale["metadata"]["name"])
    cluster.objects[key] = stale
    inventory_before = copy.deepcopy(_root(cluster)["metadata"]["annotations"]["astrolift.io/children"])
    apply_count = len(cluster.applied)

    update = driver.update(UpdateSpec(handle=result.handle, config={}))

    assert update.ok is False
    assert "ownership changed" in update.message
    assert len(cluster.applied) == apply_count
    assert _root(cluster)["metadata"]["annotations"]["astrolift.io/children"] == inventory_before


def test_status_fails_on_missing_or_foreign_children_and_removed_stack() -> None:
    driver, cluster = _driver()
    result = driver.provision(_spec())
    children = _children(cluster)
    first = children[0]
    cluster.objects.pop((f"{first['apiVersion']}/{first['kind']}", "acme-payments", first["metadata"]["name"]))

    missing = driver.status(ServiceHandle(result.handle))
    assert missing.state == "error"
    assert "missing project monitoring resources" in missing.message

    driver.provision(_spec())
    _children(cluster)[0]["metadata"]["labels"]["astrolift.io/managed-service-id"] = "other"
    foreign = driver.status(ServiceHandle(result.handle))
    assert foreign.state == "error"
    assert "ownership changed" in foreign.message

    driver.provision(_spec())
    cluster.objects.pop(("v1/Service", "astrolift-system", "astrolift-kube-prometheus-stack-prometheus"))
    removed_stack = driver.status(ServiceHandle(result.handle))
    assert removed_stack.state == "error"
    assert "prometheus is not installed" in removed_stack.message


def test_teardown_is_protected_non_destructive_and_ownership_safe() -> None:
    driver, cluster = _driver()
    result = driver.provision(_spec(config={"deletion_protection": True}))

    data_delete = driver.deprovision(DeprovisionSpec(result.handle), delete_data=True, force_destroy=True)
    protected = driver.deprovision(DeprovisionSpec(result.handle))
    assert data_delete.ok is False and data_delete.retryable is False
    assert protected.ok is False and protected.errors == ["deletion_protection_enabled"]

    child = _children(cluster)[0]
    child["metadata"]["labels"]["astrolift.io/managed-service-id"] = "other"
    cluster.objects[(f"{child['apiVersion']}/{child['kind']}", "acme-payments", child["metadata"]["name"])] = child
    refused = driver.deprovision(DeprovisionSpec(result.handle), force_destroy=True)
    assert refused.ok is False
    assert "ownership changed" in refused.message

    child["metadata"]["labels"]["astrolift.io/managed-service-id"] = "service-1"
    cluster.objects[(f"{child['apiVersion']}/{child['kind']}", "acme-payments", child["metadata"]["name"])] = child
    removed = driver.deprovision(DeprovisionSpec(result.handle), force_destroy=True)
    repeated = driver.deprovision(DeprovisionSpec(result.handle), force_destroy=True)
    assert removed.ok is True
    assert "shared metrics data and stack retained" in removed.message
    assert all(row["metadata"]["name"] != "payments-prod-monitoring" for row in cluster.deleted[-2])
    assert cluster.deleted[-1] == [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "payments-prod-monitoring", "namespace": "acme-payments"},
        },
    ]
    assert repeated.ok is True


def test_apply_delete_and_prune_failures_are_reported() -> None:
    driver, cluster = _driver()
    cluster.apply_result = _Result(False, ["admission denied"])
    rejected = driver.provision(_spec())
    assert rejected.ok is False and rejected.errors == ["admission denied"]

    cluster.apply_result = _Result()
    provisioned = driver.provision(_spec(config=_monitor_config()))
    cluster.delete_result = _Result(False, ["apiserver unavailable"])
    prune = driver.update(UpdateSpec(handle=provisioned.handle, config={}))
    assert prune.ok is False and prune.errors == ["kube_prometheus_prune_failed"]
    root = _root(cluster)
    children = json.loads(root["metadata"]["annotations"]["astrolift.io/children"])
    bundle_children = json.loads(root["data"]["bundle.json"])["children"]
    assert any(ref["kind"] == "ServiceMonitor" for ref in children)
    assert bundle_children == children

    cluster.delete_result = _Result()
    driver.provision(_spec())
    cluster.delete_result = _Result(False, ["apiserver unavailable"])
    failed_delete = driver.deprovision(DeprovisionSpec(provisioned.handle))
    assert failed_delete.ok is False and failed_delete.errors == ["apiserver unavailable"]
    assert _root(cluster)["metadata"]["name"] == "payments-prod-monitoring"
    assert all(row["metadata"]["name"] != "payments-prod-monitoring" for row in cluster.deleted[-1])


def test_malformed_inventory_snapshot_restore_and_legacy_handles_fail_closed() -> None:
    driver, cluster = _driver()
    result = driver.provision(_spec())
    _root(cluster)["metadata"]["annotations"]["astrolift.io/children"] = "not-json"
    assert driver.status(ServiceHandle(result.handle)).state == "error"

    with pytest.raises(UnsupportedOperationError, match="declarative configuration"):
        driver.snapshot(ServiceHandle(result.handle))
    with pytest.raises(UnsupportedOperationError, match="reconcile"):
        driver.restore(None, _spec())  # type: ignore[arg-type]
    assert driver.status(ServiceHandle("observability/legacy")).state == "error"


def test_plugin_catalog_and_schema_expose_executable_preview() -> None:
    driver, _ = _driver()
    entry = next(
        row
        for row in MATRIX.managed_services
        if row.plugin_id == "k8s_native" and row.variant == "kube_prometheus_stack"
    )

    assert PLUGIN.managed_service_drivers[("observability", "kube_prometheus_stack")] is KubePrometheusStackDriver
    assert entry.status == "preview"
    assert set(driver.binding_schema().env_vars) == set(entry.binding_envs)
    assert driver.config_schema()["additionalProperties"] is False
    assert "service_monitors" in driver.editable_fields()


def test_k8s_bootstrap_watches_project_monitors_rules_and_dashboards() -> None:
    driver = K8sNativeClusterDriver(config=K8sNativeConfig())
    component = next(
        row
        for row in driver.bootstrap_components(ClusterContext(slug="native", auth_method="kubeconfig"))
        if row.key == "kube-prometheus-stack"
    )

    dashboards = component.helm_values["grafana"]["sidecar"]["dashboards"]
    prometheus = component.helm_values["prometheus"]["prometheusSpec"]
    assert dashboards == {"enabled": True, "searchNamespace": "ALL"}
    assert prometheus["serviceMonitorNamespaceSelector"] == {}
    assert prometheus["podMonitorNamespaceSelector"] == {}
    assert prometheus["ruleNamespaceSelector"] == {}
    assert prometheus["serviceMonitorSelectorNilUsesHelmValues"] is False
    assert prometheus["podMonitorSelectorNilUsesHelmValues"] is False
    assert prometheus["ruleSelectorNilUsesHelmValues"] is False
