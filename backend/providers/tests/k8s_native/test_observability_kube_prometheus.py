from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from _sdk import UnsupportedOperationError
from _sdk.availability import MATRIX
from _sdk.cluster import ClusterContext, NamespaceState
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
        self.apply_results: list[_Result] = []
        self.delete_results: list[_Result] = []
        self.namespaces = {
            "acme-payments": NamespaceState(
                name="acme-payments",
                labels={},
                annotations={},
                phase="Active",
            ),
        }
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
            "astrolift-kube-prometheus-prometheus",
            "astrolift-kube-prometheus-alertmanager",
            "astrolift-kube-prometheus-stack-grafana",
        ):
            self.objects[("v1/Service", "astrolift-system", service)] = {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": service, "namespace": "astrolift-system"},
            }
        self.objects[
            (
                "monitoring.coreos.com/v1/Prometheus",
                "astrolift-system",
                "astrolift-kube-prometheus-prometheus",
            )
        ] = {
            "apiVersion": "monitoring.coreos.com/v1",
            "kind": "Prometheus",
            "metadata": {"name": "astrolift-kube-prometheus-prometheus", "namespace": "astrolift-system"},
            "spec": {
                "serviceMonitorSelector": {},
                "serviceMonitorNamespaceSelector": {},
                "podMonitorSelector": {},
                "podMonitorNamespaceSelector": {},
                "ruleSelector": {},
                "ruleNamespaceSelector": {},
            },
        }
        self.objects[("apps/v1/Deployment", "astrolift-system", "astrolift-kube-prometheus-stack-grafana")] = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "astrolift-kube-prometheus-stack-grafana", "namespace": "astrolift-system"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "grafana-sc-dashboard",
                                "env": [
                                    {"name": "LABEL", "value": "grafana_dashboard"},
                                    {"name": "LABEL_VALUE", "value": "1"},
                                    {"name": "NAMESPACE", "value": "ALL"},
                                ],
                            },
                        ],
                    },
                },
            },
        }
        self.objects[
            (
                "networking.k8s.io/v1/NetworkPolicy",
                "astrolift-system",
                "astrolift-prometheus-trusted-ingress",
            )
        ] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "astrolift-prometheus-trusted-ingress"},
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/name": "prometheus",
                        "prometheus": "astrolift-kube-prometheus-prometheus",
                    },
                },
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {"kubernetes.io/metadata.name": "astrolift-system"},
                                },
                            },
                            {
                                "namespaceSelector": {
                                    "matchLabels": {"astrolift.io/trusted-observability-access": "true"},
                                },
                            },
                        ],
                        "ports": [{"port": 9090, "protocol": "TCP"}],
                    },
                ],
            },
        }
        self.objects[
            (
                "networking.k8s.io/v1/NetworkPolicy",
                "astrolift-system",
                "astrolift-alertmanager-operator-ingress",
            )
        ] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "astrolift-alertmanager-operator-ingress"},
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "alertmanager": "astrolift-kube-prometheus-alertmanager",
                        "app.kubernetes.io/name": "alertmanager",
                    },
                },
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {"kubernetes.io/metadata.name": "astrolift-system"},
                                },
                            },
                        ],
                    },
                ],
            },
        }

    def get_manifest(self, cluster_id, namespace, kind, name):
        del cluster_id
        value = self.objects.get((kind, namespace, name))
        return copy.deepcopy(value) if value is not None else None

    def get_namespace(self, cluster_id, name):
        del cluster_id
        return copy.deepcopy(self.namespaces.get(name))

    def apply_manifests(self, cluster_id, namespace, manifests):
        del cluster_id
        self.applied.append(copy.deepcopy(manifests))
        result = self.apply_results.pop(0) if self.apply_results else self.apply_result
        if result.ok:
            for manifest in manifests:
                self.objects[
                    (
                        f"{manifest['apiVersion']}/{manifest['kind']}",
                        namespace,
                        manifest["metadata"]["name"],
                    )
                ] = copy.deepcopy(manifest)
        return result

    def delete_manifests(self, cluster_id, namespace, manifests, **kwargs):
        del cluster_id, kwargs
        self.deleted.append(copy.deepcopy(manifests))
        result = self.delete_results.pop(0) if self.delete_results else self.delete_result
        if result.ok:
            for manifest in manifests:
                self.objects.pop(
                    (
                        f"{manifest['apiVersion']}/{manifest['kind']}",
                        namespace,
                        manifest["metadata"]["name"],
                    ),
                    None,
                )
        return result


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
    dashboard_key = next(iter(children[1]["data"]))
    dashboard = json.loads(children[1]["data"][dashboard_key])
    assert dashboard_key == f"{dashboard['uid']}.json"
    assert len(dashboard["uid"]) <= 40
    assert dashboard["uid"].startswith("astrolift-acme-service-1-overview")
    assert all('namespace="acme-payments"' in panel["targets"][0]["expr"] for panel in dashboard["panels"])

    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["OBSERVABILITY_PROVIDER"].literal == "kube-prometheus-stack"
    assert "PROMETHEUS_URL" not in binding.env_vars
    assert "METRICS_ENDPOINT" not in binding.env_vars
    assert "ALERTMANAGER_URL" not in binding.env_vars
    assert binding.env_vars["OBSERVABILITY_NAMESPACE"].literal == "acme-payments"
    assert binding.env_vars["DASHBOARD_URL"].literal.endswith(f"/d/{dashboard['uid']}")
    assert "denied by default" in binding.notes
    assert not binding.iam_grants


def test_direct_prometheus_binding_requires_trusted_operator_opt_in_and_never_exposes_alertmanager() -> None:
    driver, cluster = _driver(allow_workload_prometheus_access=True)
    result = driver.provision(_spec())

    with pytest.raises(ValueError, match="trusted-observability-access=true"):
        driver.binding(ServiceHandle(result.handle))

    cluster.namespaces["acme-payments"] = NamespaceState(
        name="acme-payments",
        labels={"astrolift.io/trusted-observability-access": "true"},
        annotations={},
        phase="Active",
    )

    binding = driver.binding(ServiceHandle(result.handle))

    expected = "http://astrolift-kube-prometheus-prometheus.astrolift-system.svc.cluster.local:9090"
    assert binding.env_vars["PROMETHEUS_URL"].literal == expected
    assert binding.env_vars["METRICS_ENDPOINT"].literal == expected
    assert "ALERTMANAGER_URL" not in binding.env_vars
    assert "cluster-wide read visibility" in binding.notes


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
    keys = {next(iter(dashboard["data"])) for dashboard in dashboards}
    assert len(keys) == 2
    assert all(key.startswith("astrolift-acme-service-1-") and key.endswith(".json") for key in keys)


def test_empty_custom_collections_do_not_require_policy_opt_in() -> None:
    driver, _ = _driver()

    result = driver.provision(_spec(config={"rule_groups": [], "dashboards": []}))

    assert result.ok is True


@pytest.mark.parametrize(
    ("policy", "message"),
    [
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
    dashboard_key = next(iter(custom["data"]))
    document = json.loads(custom["data"][dashboard_key])
    assert dashboard_key == f"{document['uid']}.json"
    assert len(document["uid"]) <= 40
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
        ("v1/Service", "astrolift-system", "astrolift-kube-prometheus-prometheus"),
    )
    missing_service, _ = _driver(cluster)
    service_result = missing_service.provision(_spec())
    assert service_result.ok is False
    assert "prometheus is not installed" in service_result.message

    external, _ = _driver(cluster, verify_services=False)
    assert external.provision(_spec()).ok is True


def test_preflight_rejects_unselected_resources_and_wrong_grafana_sidecar_contract() -> None:
    cluster = _Cluster()
    prometheus = cluster.objects[
        (
            "monitoring.coreos.com/v1/Prometheus",
            "astrolift-system",
            "astrolift-kube-prometheus-prometheus",
        )
    ]
    prometheus["spec"]["serviceMonitorSelector"] = {"matchLabels": {"release": "other"}}
    wrong_selector, _ = _driver(cluster)
    result = wrong_selector.provision(_spec())
    assert result.ok is False
    assert "selectors do not watch all" in result.message

    cluster = _Cluster()
    grafana = cluster.objects[("apps/v1/Deployment", "astrolift-system", "astrolift-kube-prometheus-stack-grafana")]
    grafana["spec"]["template"]["spec"]["containers"][0]["env"][1]["value"] = "custom"
    wrong_sidecar, _ = _driver(cluster)
    result = wrong_sidecar.provision(_spec())
    assert result.ok is False
    assert "fixed Astrolift dashboard contract" in result.message

    external, _ = _driver(cluster, verify_selection=False)
    assert external.provision(_spec()).ok is True


def test_preflight_rejects_missing_or_weakened_shared_endpoint_network_policy() -> None:
    cluster = _Cluster()
    cluster.objects.pop(
        (
            "networking.k8s.io/v1/NetworkPolicy",
            "astrolift-system",
            "astrolift-alertmanager-operator-ingress",
        ),
    )
    missing_policy, _ = _driver(cluster)
    result = missing_policy.provision(_spec())
    assert result.ok is False
    assert "access boundary astrolift-alertmanager-operator-ingress" in result.message

    cluster = _Cluster()
    prometheus_policy = cluster.objects[
        (
            "networking.k8s.io/v1/NetworkPolicy",
            "astrolift-system",
            "astrolift-prometheus-trusted-ingress",
        )
    ]
    prometheus_policy["spec"]["ingress"][0]["from"].append({"namespaceSelector": {}})
    weakened_policy, _ = _driver(cluster)
    result = weakened_policy.provision(_spec())
    assert result.ok is False
    assert "was weakened" in result.message


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
    cluster.objects.pop(("v1/Service", "astrolift-system", "astrolift-kube-prometheus-prometheus"))
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


@pytest.mark.parametrize("failed_apply_index", [1, 2])
def test_failed_update_preserves_current_deletion_protection_and_retry_converges(failed_apply_index: int) -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(
        _spec(config={**_monitor_config(), "deletion_protection": True}),
    )
    cluster.apply_results = [
        *[_Result() for _ in range(failed_apply_index)],
        _Result(False, ["injected apply failure"]),
    ]

    failed = driver.update(UpdateSpec(handle=provisioned.handle, config={"deletion_protection": False}))

    assert failed.ok is False
    assert _root(cluster)["metadata"]["annotations"]["astrolift.io/deletion-protection"] == "true"
    protected = driver.deprovision(DeprovisionSpec(provisioned.handle))
    assert protected.ok is False and protected.errors == ["deletion_protection_enabled"]

    cluster.apply_results = []
    retried = driver.update(UpdateSpec(handle=provisioned.handle, config={"deletion_protection": False}))
    assert retried.ok is True
    assert _root(cluster)["metadata"]["annotations"]["astrolift.io/deletion-protection"] == "false"


def test_dashboard_uids_and_file_keys_are_unique_across_organizations_and_services() -> None:
    driver, cluster = _driver()
    acme = driver.provision(_spec())
    beta = driver.provision(
        _spec(
            organization_slug="beta",
            managed_service_id="service-2",
        ),
    )
    assert acme.ok and beta.ok

    dashboards = [
        manifest
        for key, manifest in cluster.objects.items()
        if key[0] == "v1/ConfigMap" and manifest.get("metadata", {}).get("labels", {}).get("grafana_dashboard") == "1"
    ]
    uids = []
    keys = []
    for dashboard in dashboards:
        file_key, encoded = next(iter(dashboard["data"].items()))
        uid = json.loads(encoded)["uid"]
        keys.append(file_key)
        uids.append(uid)
        assert file_key == f"{uid}.json"
        assert len(uid) <= 40
    assert len(uids) == len(set(uids)) == 2
    assert len(keys) == len(set(keys)) == 2

    unusual = driver.provision(
        _spec(
            organization_slug="This Organization Has A Long Unsafe Name That Must Be Normalized Before Persistence",
            managed_service_id="service-3",
        ),
    )
    assert unusual.ok is True
    unusual_namespace = unusual.handle.split("/")[2]
    unusual_root_name = unusual.handle.split("/")[3]
    unusual_root = cluster.objects[("v1/ConfigMap", unusual_namespace, unusual_root_name)]
    unusual_ref = next(
        ref
        for ref in json.loads(unusual_root["metadata"]["annotations"]["astrolift.io/children"])
        if ref["kind"] == "ConfigMap"
    )
    unusual_dashboard = cluster.objects[("v1/ConfigMap", unusual_namespace, unusual_ref["name"])]
    uid_before = json.loads(next(iter(unusual_dashboard["data"].values())))["uid"]
    assert driver.update(UpdateSpec(handle=unusual.handle, config={})).ok is True
    unusual_dashboard = cluster.objects[("v1/ConfigMap", unusual_namespace, unusual_ref["name"])]
    uid_after = json.loads(next(iter(unusual_dashboard["data"].values())))["uid"]
    assert uid_after == uid_before


def test_json_schema_matches_runtime_monitor_rule_and_dashboard_shape() -> None:
    driver, _ = _driver(allow_custom_rules=True, allow_custom_dashboards=True)
    schema = driver.config_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    valid = {
        **_monitor_config(sample_limit=1, target_limit=1),
        "rule_groups": [
            {
                "name": "availability",
                "rules": [{"alert": "ApiUnavailable", "expr": "up == 0"}],
            },
        ],
        "dashboards": [{"name": "latency", "document": {}}],
    }
    validator.validate(valid)
    assert driver.provision(_spec(config=valid)).ok is True

    endpoint_only_limit = _monitor_config(endpoints=[{"port": "metrics", "sample_limit": 1}])
    assert list(validator.iter_errors(endpoint_only_limit))
    assert driver.provision(_spec(config=endpoint_only_limit)).ok is False

    zero_monitor_limit = _monitor_config(sample_limit=0)
    assert list(validator.iter_errors(zero_monitor_limit))
    assert driver.provision(_spec(config=zero_monitor_limit)).ok is False

    reserved_label = _monitor_config(labels={"astrolift.io/component": "spoof"})
    assert list(validator.iter_errors(reserved_label))
    assert driver.provision(_spec(config=reserved_label)).ok is False


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
    network_policies = {manifest["metadata"]["name"]: manifest for manifest in component.helm_values["extraManifests"]}
    assert dashboards == {"enabled": True, "searchNamespace": "ALL"}
    assert component.chart_name == "kube-prometheus-stack"
    assert component.chart_version == "65.1.0"
    assert KubePrometheusConfig().prometheus_service_name == "astrolift-kube-prometheus-prometheus"
    assert KubePrometheusConfig().alertmanager_service_name == "astrolift-kube-prometheus-alertmanager"
    assert KubePrometheusConfig().grafana_service_name == "astrolift-kube-prometheus-stack-grafana"
    prometheus_ingress = network_policies["astrolift-prometheus-trusted-ingress"]
    trusted_from = prometheus_ingress["spec"]["ingress"][0]["from"]
    assert {
        "namespaceSelector": {
            "matchLabels": {"astrolift.io/trusted-observability-access": "true"},
        },
    } in trusted_from
    alertmanager_ingress = network_policies["astrolift-alertmanager-operator-ingress"]
    assert alertmanager_ingress["spec"]["ingress"] == [
        {
            "from": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "astrolift-system"},
                    },
                },
            ],
        },
    ]
    assert prometheus["serviceMonitorNamespaceSelector"] == {}
    assert prometheus["podMonitorNamespaceSelector"] == {}
    assert prometheus["ruleNamespaceSelector"] == {}
    assert prometheus["serviceMonitorSelectorNilUsesHelmValues"] is False
    assert prometheus["podMonitorSelectorNilUsesHelmValues"] is False
    assert prometheus["ruleSelectorNilUsesHelmValues"] is False
