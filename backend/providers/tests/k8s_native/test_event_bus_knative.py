from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from astrolift_drivers.registry import PluginManifest, plugins

from _sdk import UnsupportedOperationError
from _sdk.cluster_capabilities import ClusterCapabilities
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.managed.event_bus_knative import (
    API_VERSION,
    KnativeEventingConfig,
    KnativeEventingDriver,
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
                key = (
                    f"{manifest['apiVersion']}/{manifest['kind']}",
                    namespace,
                    manifest["metadata"]["name"],
                )
                self.objects[key] = manifest
        return self.apply_result

    def delete_manifests(self, cluster_id, namespace, manifests):
        self.deleted.append((cluster_id, namespace, manifests))
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


def _subscriber(name: str = "triage-worker", **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "ref": {
            "apiVersion": "v1",
            "kind": "Service",
            "name": name,
        },
    }
    value.update(overrides)
    return value


def _trigger(name: str = "triage", **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "name": name,
        "subscriber": _subscriber(),
        "filter": {"attributes": {"type": "dev.astrolift.finding"}},
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
        service_handle_hint="events",
        size="custom",
        config={"triggers": [_trigger()], **config},
        managed_service_id="service-1",
    )


def _driver(
    cluster: _Cluster | None = None,
    **policy: Any,
) -> tuple[KnativeEventingDriver, _Cluster]:
    live = cluster or _Cluster()
    return (
        KnativeEventingDriver(
            config=KnativeEventingConfig(cluster_driver=live, **policy),
        ),
        live,
    )


def _broker(cluster: _Cluster) -> dict[str, Any]:
    return cluster.objects[(f"{API_VERSION}/Broker", "steady-md-triage", "triage-prod-events")]


def _stored_trigger(cluster: _Cluster, name: str = "triage") -> dict[str, Any]:
    return cluster.objects[
        (
            f"{API_VERSION}/Trigger",
            "steady-md-triage",
            f"triage-prod-events-{name}",
        )
    ]


def _mark_ready(resource: dict[str, Any], *, address: str = "") -> None:
    resource["status"] = {
        "conditions": [{"type": "Ready", "status": "True"}],
    }
    if address:
        resource["status"]["address"] = {"url": address}


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


def test_provision_renders_owned_broker_delivery_and_trigger() -> None:
    driver, cluster = _driver()

    result = driver.provision(
        _spec(
            delivery={
                "retry": 5,
                "backoffPolicy": "exponential",
                "backoffDelay": "PT1S",
                "deadLetterSink": _subscriber("dead-letter"),
            },
        ),
    )

    assert result.ok is True
    assert result.ready is False
    assert result.handle == "event_bus/cluster-1/steady-md-triage/triage-prod-events"
    broker = _broker(cluster)
    assert broker["metadata"]["annotations"]["eventing.knative.dev/broker.class"] == ("MTChannelBasedBroker")
    assert broker["metadata"]["labels"]["astrolift.io/managed-service-id"] == "service-1"
    assert broker["spec"]["delivery"]["retry"] == 5
    trigger = _stored_trigger(cluster)
    assert trigger["spec"]["broker"] == "triage-prod-events"
    assert trigger["spec"]["subscriber"]["ref"]["name"] == "triage-worker"
    assert trigger["spec"]["filter"]["attributes"]["type"] == "dev.astrolift.finding"


def test_install_broker_config_and_allowed_class_are_applied() -> None:
    broker_config = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "name": "kafka-broker-config",
        "namespace": "knative-eventing",
    }
    driver, cluster = _driver(
        broker_class="Kafka",
        broker_config=broker_config,
        allowed_broker_classes=("Kafka",),
    )

    result = driver.provision(_spec())

    assert result.ok is True
    assert _broker(cluster)["spec"]["config"] == broker_config
    assert _broker(cluster)["metadata"]["annotations"]["eventing.knative.dev/broker.class"] == "Kafka"


@pytest.mark.parametrize(
    "config",
    [
        {"unknown": True},
        {"broker_class": "Kafka"},
        {"broker_config": "not-an-object"},
        {"triggers": "not-an-array"},
        {"triggers": [{"name": "missing-subscriber"}]},
        {
            "triggers": [
                _trigger("duplicate"),
                _trigger("duplicate"),
            ],
        },
        {
            "triggers": [
                _trigger(
                    filter={"attributes": {"type": "one"}},
                    filters=[{"exact": {"type": "two"}}],
                ),
            ],
        },
        {"delivery": {"retry": -1}},
        {"delivery": {"backoffPolicy": "random"}},
        {"delivery": {"retryAfterMax": "PT1H"}},
        {"labels": {"app.kubernetes.io/managed-by": "somebody"}},
        {"annotations": {"BAD KEY": "value"}},
    ],
)
def test_invalid_eventing_config_fails_before_cluster_mutation(
    config: dict[str, Any],
) -> None:
    driver, cluster = _driver()

    result = driver.provision(_spec(**config))

    assert result.ok is False
    assert result.errors == ["invalid_knative_eventing_config"]
    assert cluster.applied == []


@pytest.mark.parametrize(
    "subscriber",
    [
        {"uri": "https://hooks.example.test/events"},
        {"uri": "ftp://internal/events"},
        {
            "ref": {
                "apiVersion": "v1",
                "kind": "Service",
                "name": "worker",
                "namespace": "another-project",
            },
        },
    ],
)
def test_external_and_cross_namespace_subscribers_fail_closed(
    subscriber: dict[str, Any],
) -> None:
    driver, cluster = _driver()

    result = driver.provision(_spec(triggers=[_trigger(subscriber=subscriber)]))

    assert result.ok is False
    assert cluster.applied == []


def test_install_policy_can_enable_external_cross_namespace_and_alpha_delivery() -> None:
    driver, cluster = _driver(
        allow_external_subscribers=True,
        allow_cross_namespace_subscribers=True,
        allow_alpha_delivery_fields=True,
    )

    result = driver.provision(
        _spec(
            delivery={"retryAfterMax": "PT1H"},
            triggers=[
                _trigger(
                    subscriber={"uri": "https://hooks.example.test/events"},
                    delivery={
                        "deadLetterSink": {
                            "ref": {
                                "apiVersion": "v1",
                                "kind": "Service",
                                "name": "dead-letter",
                                "namespace": "shared-events",
                            },
                        },
                    },
                ),
            ],
        ),
    )

    assert result.ok is True
    assert _broker(cluster)["spec"]["delivery"]["retryAfterMax"] == "PT1H"
    assert _stored_trigger(cluster)["spec"]["subscriber"]["uri"].startswith("https://")


def test_explicit_same_namespace_reference_and_uri_are_allowed_by_default() -> None:
    driver, cluster = _driver()

    result = driver.provision(
        _spec(
            triggers=[
                _trigger(
                    "reference",
                    subscriber={
                        "ref": {
                            "apiVersion": "v1",
                            "kind": "Service",
                            "name": "triage-worker",
                            "namespace": "steady-md-triage",
                        },
                    },
                ),
                _trigger(
                    "uri",
                    subscriber={
                        "uri": "http://triage-worker.steady-md-triage.svc.cluster.local",
                    },
                ),
            ],
        ),
    )

    assert result.ok is True
    assert _stored_trigger(cluster, "reference")["spec"]["subscriber"]["ref"]["namespace"] == "steady-md-triage"
    assert _stored_trigger(cluster, "uri")["spec"]["subscriber"]["uri"].endswith(
        ".svc.cluster.local",
    )


def test_trigger_resource_names_are_scoped_to_their_broker() -> None:
    driver, cluster = _driver()
    first = _spec()
    second = replace(
        first,
        service_handle_hint="audit-events",
        managed_service_id="service-2",
    )

    assert driver.provision(first).ok is True
    assert driver.provision(second).ok is True

    trigger_names = {
        name
        for kind, namespace, name in cluster.objects
        if kind == f"{API_VERSION}/Trigger" and namespace == "steady-md-triage"
    }
    assert trigger_names == {
        "triage-prod-events-triage",
        "triage-prod-audit-events-triage",
    }


def test_existing_broker_is_refused_even_with_its_exact_uid() -> None:
    driver, cluster = _driver()
    key = (f"{API_VERSION}/Broker", "steady-md-triage", "triage-prod-events")
    foreign = {
        "metadata": {
            "uid": "uid-1",
            "labels": {"app.kubernetes.io/managed-by": "another-controller"},
        },
    }
    cluster.objects[key] = foreign

    refused = driver.provision(_spec())
    assert refused.ok is False
    assert "operator-authorized" in refused.message

    # Knowing the uid proved only that the caller could see the object.
    # Adoption is operator-only (#2021); the flags are rejected, not ignored.
    flagged = driver.provision(_spec(adopt_existing=True, expected_existing_uid="uid-1"))
    assert flagged.ok is False
    assert "unsupported Knative Eventing config fields" in flagged.message
    assert cluster.objects[key] is foreign
    assert cluster.applied == []


def test_existing_trigger_is_refused_even_with_its_exact_uid() -> None:
    driver, cluster = _driver()
    key = (f"{API_VERSION}/Trigger", "steady-md-triage", "triage-prod-events-triage")
    foreign = {
        "metadata": {
            "uid": "trigger-uid",
            "labels": {"app.kubernetes.io/managed-by": "another-controller"},
        },
    }
    cluster.objects[key] = foreign

    refused = driver.provision(_spec())
    assert refused.ok is False
    assert "operator-authorized" in refused.message

    flagged = driver.provision(
        _spec(triggers=[_trigger(adopt_existing=True, expected_existing_uid="trigger-uid")]),
    )
    assert flagged.ok is False
    assert "unsupported Trigger fields" in flagged.message
    assert cluster.objects[key] is foreign
    assert cluster.applied == []


def test_update_prunes_removed_owned_trigger_and_reconciles_remaining_set() -> None:
    driver, cluster = _driver()
    assert driver.provision(
        _spec(triggers=[_trigger("first"), _trigger("second")]),
    ).ok
    handle = "event_bus/cluster-1/steady-md-triage/triage-prod-events"

    result = driver.update(
        UpdateSpec(handle=handle, config={"triggers": [_trigger("second")]}),
    )

    assert result.ok is True
    assert [row["metadata"]["name"] for row in cluster.deleted[-1][2]] == [
        "triage-prod-events-first",
    ]
    assert (
        f"{API_VERSION}/Trigger",
        "steady-md-triage",
        "triage-prod-events-first",
    ) not in cluster.objects
    assert _stored_trigger(cluster, "second")["spec"]["broker"] == "triage-prod-events"


def test_update_refuses_immutable_broker_class_change() -> None:
    driver, _cluster = _driver(
        allow_class_override=True,
        allowed_broker_classes=("MTChannelBasedBroker", "Kafka"),
    )
    provisioned = driver.provision(_spec())

    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"broker_class": "Kafka", "triggers": [_trigger()]},
        ),
    )

    assert result.ok is False
    assert result.retryable is False
    assert "immutable" in result.message


def test_update_refuses_to_prune_foreign_trigger() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec(triggers=[_trigger("foreign")]))
    _stored_trigger(cluster, "foreign")["metadata"]["labels"]["astrolift.io/managed-service-id"] = "other-service"

    result = driver.update(UpdateSpec(handle=provisioned.handle, config={"triggers": []}))

    assert result.ok is False
    assert result.retryable is False
    assert cluster.deleted == []


def test_status_and_binding_require_ready_broker_and_triggers() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    handle = ServiceHandle(provisioned.handle)

    assert driver.status(handle).state == "provisioning"
    with pytest.raises(ValueError, match="not ready"):
        driver.binding(handle)

    _mark_ready(
        _broker(cluster),
        address="http://triage-prod-events-kn-channel.steady-md-triage.svc.cluster.local",
    )
    assert driver.status(handle).state == "provisioning"
    _mark_ready(_stored_trigger(cluster))

    assert driver.status(handle).state == "available"
    binding = driver.binding(handle)
    assert binding.env_vars["EVENT_BUS_NAME"].literal == "triage-prod-events"
    assert binding.env_vars["EVENT_BUS_REGION"].literal == "kubernetes"
    assert binding.env_vars["EVENT_BUS_ENDPOINT"].literal == (
        "http://triage-prod-events-kn-channel.steady-md-triage.svc.cluster.local"
    )
    assert binding.env_vars["EVENT_BUS_ARN"].literal == ("k8s://cluster-1/steady-md-triage/broker/triage-prod-events")


def test_terminal_broker_and_trigger_conditions_report_errors() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    handle = ServiceHandle(provisioned.handle)
    _broker(cluster)["status"] = {
        "conditions": [
            {
                "type": "Ready",
                "status": "False",
                "reason": "BrokerClassUnavailable",
            },
        ],
    }

    assert driver.status(handle).state == "error"
    _mark_ready(_broker(cluster), address="http://broker.example.svc")
    _stored_trigger(cluster)["status"] = {
        "conditions": [
            {
                "type": "Ready",
                "status": "False",
                "reason": "SubscriberNotFound",
            },
        ],
    }

    trigger_failure = driver.status(handle)
    assert trigger_failure.state == "error"
    assert "SubscriberNotFound" in trigger_failure.message


def test_deprovision_respects_protection_and_never_deletes_foreign_children() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())

    protected = driver.deprovision(
        DeprovisionSpec(
            handle=provisioned.handle,
            config={"deletion_protection": True},
        ),
    )
    assert protected.ok is False
    assert protected.retryable is False
    assert cluster.deleted == []

    forced = driver.deprovision(
        DeprovisionSpec(
            handle=provisioned.handle,
            config={"deletion_protection": True},
        ),
        force_destroy=True,
    )
    assert forced.ok is True
    assert {row["kind"] for row in cluster.deleted[-1][2]} == {"Broker", "Trigger"}


def test_deprovision_refuses_foreign_child_even_when_forced() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    _stored_trigger(cluster)["metadata"]["labels"]["astrolift.io/managed-service-id"] = "another-service"

    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )

    assert result.ok is False
    assert result.retryable is False
    assert cluster.deleted == []


def test_snapshot_restore_and_legacy_handles_fail_closed() -> None:
    driver, _cluster = _driver()
    with pytest.raises(UnsupportedOperationError):
        driver.snapshot(ServiceHandle("event_bus/cluster/ns/name"))
    with pytest.raises(UnsupportedOperationError):
        driver.restore(None, _spec())  # type: ignore[arg-type]

    legacy = driver.status(ServiceHandle("legacy-handle"))
    assert legacy.state == "error"
    assert "legacy" in legacy.message


def test_plugin_catalogue_preflight_and_contract_are_executable(
    _registered_plugin: None,
) -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    driver_type = PLUGIN.managed_service_drivers[("event_bus", "knative_eventing")]
    assert driver_type is KnativeEventingDriver
    schema = driver_type(config=KnativeEventingConfig(cluster_driver=_Cluster())).config_schema()
    assert schema["properties"]["triggers"]["items"]["required"] == [
        "name",
        "subscriber",
    ]

    missing = preflight(
        kind="event_bus",
        variant="knative_eventing",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32",
            installed_crds=frozenset({"brokers.eventing.knative.dev"}),
            crd_inventory_probed=True,
        ),
    )
    installed = preflight(
        kind="event_bus",
        variant="knative_eventing",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32",
            installed_crds=frozenset(
                {
                    "brokers.eventing.knative.dev",
                    "triggers.eventing.knative.dev",
                },
            ),
            crd_inventory_probed=True,
        ),
    )

    assert missing.ok is False
    assert missing.failures[0].code == "missing_crd"
    assert installed.ok is True
    row = next(
        item for item in list_catalog("k8s_native") if (item.kind, item.variant) == ("event_bus", "knative_eventing")
    )
    assert row.available is True
