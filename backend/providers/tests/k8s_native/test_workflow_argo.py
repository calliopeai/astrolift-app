from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from astrolift_drivers.registry import PluginManifest, plugins

from _sdk.cluster_capabilities import ClusterCapabilities
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.managed.workflow_argo import (
    API_VERSION,
    ArgoWorkflowsConfig,
    ArgoWorkflowsDriver,
)
from k8s_native.plugin import PLUGIN
from k8s_native.preflight import preflight

IMAGE = "registry.example.test/workflows/worker@sha256:" + "a" * 64


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
        self.deleted: list[tuple[str, str, list[dict[str, Any]], str | None]] = []
        self.apply_result = _Result()
        self.delete_result = _Result()

    def get_manifest(self, cluster_id, namespace, kind, name):
        del cluster_id
        return self.objects.get((kind, namespace, name))

    def list_manifests(self, cluster_id, namespace, kind):
        del cluster_id
        return [
            manifest
            for (stored_kind, stored_namespace, _name), manifest in self.objects.items()
            if stored_kind == kind and stored_namespace == namespace
        ]

    def apply_manifests(self, cluster_id, namespace, manifests):
        self.applied.append((cluster_id, namespace, manifests))
        if self.apply_result.ok:
            for manifest in manifests:
                self.objects[
                    (
                        f"{manifest['apiVersion']}/{manifest['kind']}",
                        namespace,
                        manifest["metadata"]["name"],
                    )
                ] = manifest
        return self.apply_result

    def delete_manifests(
        self,
        cluster_id,
        namespace,
        manifests,
        *,
        propagation_policy=None,
    ):
        self.deleted.append((cluster_id, namespace, manifests, propagation_policy))
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


def _workflow_spec(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "entrypoint": "main",
        "arguments": {"parameters": [{"name": "message", "value": "hello"}]},
        "templates": [
            {
                "name": "main",
                "container": {
                    "image": IMAGE,
                    "command": ["workflow-worker"],
                    "args": ["{{workflow.parameters.message}}"],
                },
            },
        ],
    }
    value.update(overrides)
    return value


def _cron(name: str = "hourly", **spec_overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "schedules": ["0 * * * *"],
        "timezone": "UTC",
        "suspend": False,
    }
    spec.update(spec_overrides)
    return {"name": name, "spec": spec}


def _event(name: str = "webhook", **spec_overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "event": {"selector": 'payload.type == "triage"'},
        "submit": {
            "arguments": {
                "parameters": [
                    {
                        "name": "message",
                        "valueFrom": {"event": "payload.message"},
                    },
                ],
            },
        },
    }
    spec.update(spec_overrides)
    return {"name": name, "spec": spec}


def _spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="steady-md",
        app_id="app-1",
        app_slug="triage",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="pipeline",
        size="custom",
        config={"workflow_spec": _workflow_spec(), **config},
        managed_service_id="service-1",
    )


def _driver(
    cluster: _Cluster | None = None,
    **policy: Any,
) -> tuple[ArgoWorkflowsDriver, _Cluster]:
    live = cluster or _Cluster()
    config = ArgoWorkflowsConfig(cluster_driver=live, **policy)
    namespace = config.namespace or "steady-md-triage"
    accounts = {
        config.service_account_name,
        *config.allowed_service_accounts,
    }
    for account in accounts:
        if account:
            live.objects[("v1/ServiceAccount", namespace, account)] = {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {"name": account, "namespace": namespace},
            }
    return (
        ArgoWorkflowsDriver(config=config),
        live,
    )


def _resource(cluster: _Cluster, kind: str, name: str) -> dict[str, Any]:
    return cluster.objects[(f"{API_VERSION}/{kind}", "steady-md-triage", name)]


def _template(cluster: _Cluster, name: str = "triage-prod-pipeline") -> dict[str, Any]:
    return _resource(cluster, "WorkflowTemplate", name)


def _child(
    cluster: _Cluster,
    kind: str,
    name: str,
    parent: str = "triage-prod-pipeline",
) -> dict[str, Any]:
    return _resource(cluster, kind, f"{parent}-{name}")


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


def test_provision_renders_owned_template_schedule_and_event_binding() -> None:
    driver, cluster = _driver()

    result = driver.provision(
        _spec(
            cron_workflows=[_cron()],
            event_bindings=[_event()],
        ),
    )

    assert result.ok is True
    assert result.ready is True
    assert result.handle == ("workflow_engine/cluster-1/steady-md-triage/triage-prod-pipeline")
    template = _template(cluster)
    assert template["metadata"]["labels"]["astrolift.io/managed-service-id"] == ("service-1")
    assert template["spec"]["serviceAccountName"] == "argo-workflow"
    assert template["spec"]["parallelism"] == 10
    assert template["spec"]["activeDeadlineSeconds"] == 3600
    assert template["spec"]["ttlStrategy"]["secondsAfterCompletion"] == 86400
    assert template["spec"]["podGC"]["strategy"] == "OnWorkflowCompletion"
    assert template["spec"]["workflowMetadata"]["labels"]["astrolift.io/managed-service-id"] == "service-1"

    cron = _child(cluster, "CronWorkflow", "hourly")
    assert cron["spec"]["concurrencyPolicy"] == "Forbid"
    assert cron["spec"]["workflowSpec"]["workflowTemplateRef"] == {
        "name": "triage-prod-pipeline",
    }
    assert cron["spec"]["workflowSpec"]["activeDeadlineSeconds"] == 3600
    event = _child(cluster, "WorkflowEventBinding", "webhook")
    assert event["spec"]["submit"]["workflowTemplateRef"] == {
        "name": "triage-prod-pipeline",
    }


def test_full_native_dag_and_workflow_controls_are_preserved() -> None:
    driver, cluster = _driver()
    dag = {
        "name": "main",
        "dag": {
            "failFast": False,
            "tasks": [
                {
                    "name": "fan-out",
                    "template": "worker",
                    "withItems": ["one", "two"],
                    "retryStrategy": {"limit": 3},
                },
            ],
        },
    }
    worker = {
        "name": "worker",
        "inputs": {"parameters": [{"name": "item"}]},
        "container": {
            "image": IMAGE,
            "args": ["{{item}}"],
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}},
        },
    }

    result = driver.provision(
        _spec(
            workflow_spec=_workflow_spec(
                templates=[dag, worker],
                parallelism=7,
                activeDeadlineSeconds=1800,
                synchronization={"mutexes": [{"name": "triage"}]},
                retryStrategy={"limit": 2},
                artifactRepositoryRef={"configMap": "artifact-repositories"},
                volumeClaimGC={"strategy": "OnWorkflowCompletion"},
                workflowMetadata={
                    "labels": {"example.test/class": "triage"},
                    "annotations": {"example.test/owner": "engineering"},
                },
            ),
        ),
    )

    assert result.ok is True
    native = _template(cluster)["spec"]
    assert native["templates"] == [dag, worker]
    assert native["parallelism"] == 7
    assert native["synchronization"]["mutexes"][0]["name"] == "triage"
    assert native["artifactRepositoryRef"]["configMap"] == "artifact-repositories"
    assert native["workflowMetadata"]["labels"]["example.test/class"] == "triage"


@pytest.mark.parametrize(
    "config",
    [
        {"unknown": True},
        {"workflow_spec": {}},
        {"workflow_spec": {"entrypoint": "main"}},
        {"workflow_spec": _workflow_spec(priority=10)},
        {"workflow_spec": _workflow_spec(mutex={"name": "removed"})},
        {"workflow_spec": _workflow_spec(schedule="* * * * *")},
        {
            "workflow_spec": _workflow_spec(
                templates=[
                    {
                        "name": "main",
                        "container": {"image": "registry.example.test/worker:latest"},
                    },
                ],
            ),
        },
        {
            "workflow_spec": _workflow_spec(
                workflowMetadata={
                    "labelsFrom": {
                        "astrolift.io/managed-service-id": {
                            "expression": "'spoofed'",
                        },
                    },
                },
            ),
        },
        {"workflow_spec": _workflow_spec(serviceAccountName="admin")},
        {"workflow_spec": _workflow_spec(parallelism=51)},
        {"workflow_spec": _workflow_spec(activeDeadlineSeconds=86401)},
        {
            "workflow_spec": _workflow_spec(
                ttlStrategy={"secondsAfterCompletion": 604801},
            ),
        },
        {"workflow_spec": _workflow_spec(podSpecPatch='{"hostNetwork":true}')},
        {
            "workflow_spec": _workflow_spec(
                executorPlugins=[{"name": "custom", "sidecar": {"image": IMAGE}}],
            ),
        },
        {
            "workflow_spec": _workflow_spec(
                templates=[{"name": "main", "resource": {"action": "create"}}],
            ),
        },
        {
            "workflow_spec": _workflow_spec(
                templates=[{"name": "main", "plugin": {"example": {}}}],
            ),
        },
        {
            "workflow_spec": _workflow_spec(
                templates=[
                    {
                        "name": "main",
                        "http": {"url": "https://hooks.example.test"},
                    },
                ],
            ),
        },
        {
            "workflow_spec": _workflow_spec(
                templates=[
                    {
                        "name": "main",
                        "container": {
                            "image": IMAGE,
                            "securityContext": {
                                "capabilities": {"add": ["SYS_ADMIN"]},
                            },
                        },
                    },
                ],
            ),
        },
        {
            "workflow_spec": _workflow_spec(
                templates=[
                    {
                        "name": "main",
                        "container": {
                            "image": IMAGE,
                            "securityContext": {"privileged": True},
                        },
                    },
                ],
            ),
        },
        {
            "workflow_spec": _workflow_spec(
                volumes=[{"name": "host", "hostPath": {"path": "/"}}],
            ),
        },
        {
            "workflow_spec": {
                "workflowTemplateRef": {
                    "name": "cluster-template",
                    "clusterScope": True,
                },
            },
        },
        {
            "workflow_spec": _workflow_spec(
                templates=[
                    {
                        "name": "main",
                        "steps": [
                            [
                                {
                                    "name": "shared",
                                    "templateRef": {
                                        "name": "shared",
                                        "template": "main",
                                    },
                                },
                            ],
                        ],
                    },
                ],
            ),
        },
        {"labels": {"app.kubernetes.io/managed-by": "someone-else"}},
    ],
)
def test_unsafe_or_invalid_workflow_config_fails_before_cluster_mutation(
    config: dict[str, Any],
) -> None:
    driver, cluster = _driver()

    result = driver.provision(_spec(**config))

    assert result.ok is False
    assert result.errors == ["invalid_argo_workflows_config"]
    assert cluster.applied == []


@pytest.mark.parametrize(
    "cron",
    [
        {"name": "bad", "spec": {"schedule": "* * * * *"}},
        {"name": "bad", "spec": {"schedules": []}},
        {
            "name": "bad",
            "spec": {"schedules": ["* * * * *"], "concurrencyPolicy": "Queue"},
        },
        {
            "name": "bad",
            "spec": {"schedules": ["* * * * *"], "successfulJobsHistoryLimit": 101},
        },
        {
            "name": "bad",
            "spec": {
                "schedules": ["* * * * *"],
                "workflowSpec": {"templates": [{"name": "inline"}]},
            },
        },
    ],
)
def test_invalid_cron_workflow_fails_closed(cron: dict[str, Any]) -> None:
    driver, cluster = _driver()

    result = driver.provision(_spec(cron_workflows=[cron]))

    assert result.ok is False
    assert cluster.applied == []


@pytest.mark.parametrize(
    "event",
    [
        {"name": "bad", "spec": {"submit": {}}},
        {"name": "bad", "spec": {"event": {"selector": ""}, "submit": {}}},
        {
            "name": "bad",
            "spec": {
                "event": {"selector": "true"},
                "submit": {"workflowTemplateRef": "not-an-object"},
            },
        },
    ],
)
def test_invalid_event_binding_fails_closed(event: dict[str, Any]) -> None:
    driver, cluster = _driver()

    result = driver.provision(_spec(event_bindings=[event]))

    assert result.ok is False
    assert cluster.applied == []


def test_install_policy_can_enable_advanced_native_workflow_features() -> None:
    driver, cluster = _driver(
        service_account_name="workflow-runner",
        allow_service_account_override=True,
        allowed_service_accounts=("workflow-runner", "gpu-runner"),
        allow_workflow_template_refs=True,
        allow_cluster_template_refs=True,
        allow_resource_templates=True,
        allow_executor_plugins=True,
        allow_external_http_templates=True,
        allow_host_access=True,
        allow_privileged_pods=True,
        allow_pod_spec_patch=True,
        allow_tagged_images=True,
        allowed_image_prefixes=("registry.example.test/",),
    )

    result = driver.provision(
        _spec(
            workflow_spec=_workflow_spec(
                serviceAccountName="gpu-runner",
                podSpecPatch='{"runtimeClassName":"nvidia"}',
                executorPlugins=[{"name": "custom", "sidecar": {"image": IMAGE}}],
                volumes=[{"name": "host", "hostPath": {"path": "/data"}}],
                templates=[
                    {
                        "name": "main",
                        "container": {
                            "image": "registry.example.test/worker:v1",
                            "securityContext": {"privileged": True},
                        },
                    },
                    {
                        "name": "resource",
                        "resource": {"action": "get", "manifest": "apiVersion: v1"},
                    },
                    {"name": "http", "http": {"url": "https://example.test"}},
                    {"name": "plugin", "plugin": {"example": {"message": "hi"}}},
                ],
            ),
        ),
    )

    assert result.ok is True
    native = _template(cluster)["spec"]
    assert native["serviceAccountName"] == "gpu-runner"
    assert native["templates"][0]["container"]["image"].endswith(":v1")
    assert native["templates"][1]["resource"]["action"] == "get"


def test_image_allowlist_matches_repository_boundaries() -> None:
    driver, cluster = _driver(
        allowed_image_prefixes=("registry.example.test/team",),
    )

    accepted = driver.provision(
        _spec(
            workflow_spec=_workflow_spec(
                templates=[
                    {
                        "name": "main",
                        "container": {
                            "image": "registry.example.test/team/worker@sha256:" + "b" * 64,
                        },
                    },
                ],
            ),
        ),
    )
    rejected = driver.provision(
        replace(
            _spec(
                workflow_spec=_workflow_spec(
                    templates=[
                        {
                            "name": "main",
                            "container": {
                                "image": "registry.example.test/team-evil/worker@sha256:" + "c" * 64,
                            },
                        },
                    ],
                ),
            ),
            managed_service_id="service-2",
            service_handle_hint="other",
        ),
    )

    assert accepted.ok is True
    assert rejected.ok is False
    assert "outside the cluster image allowlist" in rejected.message
    assert len(cluster.applied) == 1


def test_install_policy_can_enable_cluster_workflow_template_composition() -> None:
    cluster = _Cluster()
    cluster.objects[(f"{API_VERSION}/ClusterWorkflowTemplate", None, "shared-platform-workflow")] = {
        "apiVersion": API_VERSION,
        "kind": "ClusterWorkflowTemplate",
        "metadata": {"name": "shared-platform-workflow", "uid": "shared-uid"},
        "spec": _workflow_spec(),
    }
    driver, cluster = _driver(
        cluster,
        allow_workflow_template_refs=True,
        allow_cluster_template_refs=True,
        trusted_workflow_template_uids={"cluster/shared-platform-workflow": "shared-uid"},
    )

    result = driver.provision(
        _spec(
            workflow_spec={
                "workflowTemplateRef": {
                    "name": "shared-platform-workflow",
                    "clusterScope": True,
                },
            },
        ),
    )

    assert result.ok is True
    assert _template(cluster)["spec"]["workflowTemplateRef"] == {
        "name": "shared-platform-workflow",
        "clusterScope": True,
    }


def test_referenced_templates_are_uid_pinned_and_recursively_policy_checked() -> None:
    cluster = _Cluster()
    cluster.objects[(f"{API_VERSION}/WorkflowTemplate", "steady-md-triage", "unsafe")] = {
        "apiVersion": API_VERSION,
        "kind": "WorkflowTemplate",
        "metadata": {"name": "unsafe", "uid": "unsafe-uid"},
        "spec": _workflow_spec(
            templates=[
                {
                    "name": "main",
                    "container": {
                        "image": IMAGE,
                        "securityContext": {"privileged": True},
                    },
                },
            ],
        ),
    }
    unpinned, _ = _driver(cluster, allow_workflow_template_refs=True)
    pinned, _ = _driver(
        cluster,
        allow_workflow_template_refs=True,
        trusted_workflow_template_uids={"steady-md-triage/unsafe": "unsafe-uid"},
    )
    config = _spec(
        workflow_spec={
            "workflowTemplateRef": {"name": "unsafe"},
        },
    )

    untrusted = unpinned.provision(config)
    unsafe = pinned.provision(config)

    assert untrusted.ok is False
    assert "pinned to its exact UID" in untrusted.message
    assert unsafe.ok is False
    assert "privileged" in unsafe.message


def test_referenced_template_cycles_fail_closed() -> None:
    cluster = _Cluster()
    labels = {
        "app.kubernetes.io/managed-by": "astrolift",
        "astrolift.io/managed-service-id": "shared-service",
    }
    for name, target in (("first", "second"), ("second", "first")):
        cluster.objects[(f"{API_VERSION}/WorkflowTemplate", "steady-md-triage", name)] = {
            "apiVersion": API_VERSION,
            "kind": "WorkflowTemplate",
            "metadata": {"name": name, "labels": labels},
            "spec": {"workflowTemplateRef": {"name": target}},
        }
    driver, _ = _driver(cluster, allow_workflow_template_refs=True)

    result = driver.provision(
        _spec(workflow_spec={"workflowTemplateRef": {"name": "first"}}),
    )

    assert result.ok is False
    assert "cyclic Argo template reference" in result.message


def test_provision_bootstraps_missing_executor_service_account_and_rbac() -> None:
    cluster = _Cluster()
    driver = ArgoWorkflowsDriver(
        config=ArgoWorkflowsConfig(cluster_driver=cluster),
    )

    result = driver.provision(_spec())

    assert result.ok is True
    applied = cluster.applied[-1][2]
    assert [manifest["kind"] for manifest in applied[:3]] == [
        "ServiceAccount",
        "Role",
        "RoleBinding",
    ]
    assert applied[1]["rules"] == [
        {
            "apiGroups": ["argoproj.io"],
            "resources": ["workflowtaskresults"],
            "verbs": ["create", "patch"],
        },
    ]
    assert applied[2]["subjects"][0]["name"] == "argo-workflow"
    assert driver.status(ServiceHandle(result.handle)).state == "available"
    cluster.objects.pop(
        (
            "rbac.authorization.k8s.io/v1/Role",
            "steady-md-triage",
            "astrolift-argo-workflow-executor",
        ),
    )
    assert driver.status(ServiceHandle(result.handle)).state == "error"


def test_managed_namespace_install_fails_outside_declared_watch_scope() -> None:
    driver, cluster = _driver(
        watch_all_namespaces=False,
        managed_namespaces=("another-project",),
    )

    result = driver.provision(_spec())

    assert result.ok is False
    assert "not configured to watch namespace" in result.message
    assert cluster.applied == []


def test_child_resource_names_are_scoped_to_their_template() -> None:
    driver, cluster = _driver()
    first = _spec(cron_workflows=[_cron()], event_bindings=[_event()])
    second = replace(
        first,
        service_handle_hint="audit-pipeline",
        managed_service_id="service-2",
    )

    assert driver.provision(first).ok is True
    assert driver.provision(second).ok is True

    cron_names = {
        name
        for kind, namespace, name in cluster.objects
        if kind == f"{API_VERSION}/CronWorkflow" and namespace == "steady-md-triage"
    }
    assert cron_names == {
        "triage-prod-pipeline-hourly",
        "triage-prod-audit-pipeline-hourly",
    }


def test_existing_template_is_refused_even_with_its_exact_uid() -> None:
    driver, cluster = _driver()
    foreign = {
        "metadata": {
            "uid": "uid-1",
            "labels": {"app.kubernetes.io/managed-by": "another-controller"},
        },
    }
    key = (f"{API_VERSION}/WorkflowTemplate", "steady-md-triage", "triage-prod-pipeline")
    cluster.objects[key] = foreign

    refused = driver.provision(_spec())
    assert refused.ok is False
    assert "operator-authorized" in refused.message

    # Knowing the uid proved only that the caller could see the object.
    # Adoption is operator-only (#2021); the flags are rejected, not ignored.
    flagged = driver.provision(_spec(adopt_existing=True, expected_existing_uid="uid-1"))
    assert flagged.ok is False
    assert "unsupported Argo config fields" in flagged.message
    assert cluster.objects[key] is foreign
    assert cluster.applied == []


def test_existing_child_is_refused_even_with_its_exact_uid() -> None:
    driver, cluster = _driver()
    foreign = {
        "metadata": {
            "uid": "cron-uid",
            "labels": {"app.kubernetes.io/managed-by": "another-controller"},
        },
    }
    key = (f"{API_VERSION}/CronWorkflow", "steady-md-triage", "triage-prod-pipeline-hourly")
    cluster.objects[key] = foreign

    refused = driver.provision(_spec(cron_workflows=[_cron()]))
    assert refused.ok is False
    assert "operator-authorized" in refused.message

    flagged = driver.provision(
        _spec(
            cron_workflows=[
                {
                    **_cron(),
                    "adopt_existing": True,
                    "expected_existing_uid": "cron-uid",
                },
            ],
        ),
    )
    assert flagged.ok is False
    assert "unsupported CronWorkflow fields" in flagged.message
    assert cluster.objects[key] is foreign
    assert cluster.applied == []


def test_update_prunes_removed_owned_children() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(
        _spec(
            cron_workflows=[_cron("first"), _cron("second")],
            event_bindings=[_event()],
        ),
    )

    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "workflow_spec": _workflow_spec(),
                "cron_workflows": [_cron("second")],
            },
        ),
    )

    assert result.ok is True
    deleted = {(row["kind"], row["metadata"]["name"]) for row in cluster.deleted[-1][2]}
    assert deleted == {
        ("CronWorkflow", "triage-prod-pipeline-first"),
        ("WorkflowEventBinding", "triage-prod-pipeline-webhook"),
    }
    assert cluster.deleted[-1][3] == "Orphan"
    assert (
        _child(cluster, "CronWorkflow", "second")["spec"]["workflowSpec"]["workflowTemplateRef"]["name"]
        == "triage-prod-pipeline"
    )


def test_update_and_delete_refuse_foreign_children() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec(cron_workflows=[_cron()]))
    _child(cluster, "CronWorkflow", "hourly")["metadata"]["labels"]["astrolift.io/managed-service-id"] = (
        "another-service"
    )

    updated = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"workflow_spec": _workflow_spec()},
        ),
    )
    deleted = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )

    assert updated.ok is False and updated.retryable is False
    assert deleted.ok is False and deleted.retryable is False
    assert cluster.deleted == []


def test_status_surfaces_missing_children_and_cron_submission_errors() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec(cron_workflows=[_cron()]))
    handle = ServiceHandle(provisioned.handle)

    assert driver.status(handle).state == "available"
    cron = _child(cluster, "CronWorkflow", "hourly")
    cron["status"] = {
        "conditions": [
            {
                "type": "SubmissionError",
                "status": "True",
                "message": "template input is invalid",
            },
        ],
    }
    failure = driver.status(handle)
    assert failure.state == "error"
    assert failure.message == "template input is invalid"

    cluster.objects.pop(
        (
            f"{API_VERSION}/CronWorkflow",
            "steady-md-triage",
            "triage-prod-pipeline-hourly",
        ),
    )
    assert driver.status(handle).state == "error"


def test_binding_exposes_portable_and_authenticated_server_coordinates() -> None:
    driver, _cluster = _driver(argo_server_url="https://argo.example.test/")
    provisioned = driver.provision(_spec())

    binding = driver.binding(ServiceHandle(provisioned.handle))

    assert binding.env_vars["WORKFLOW_ENGINE_ID"].literal == "triage-prod-pipeline"
    assert binding.env_vars["WORKFLOW_ENGINE_TYPE"].literal == "ARGO_WORKFLOWS"
    assert binding.env_vars["WORKFLOW_ENGINE_ARN"].literal == (
        "k8s://cluster-1/steady-md-triage/workflowtemplate/triage-prod-pipeline"
    )
    assert binding.env_vars["ARGO_SERVER_URL"].literal == "https://argo.example.test"
    assert binding.env_vars["ARGO_EVENT_ENDPOINT"].literal == (
        "https://argo.example.test/api/v1/events/steady-md-triage/"
    )
    assert "credentials are intentionally not emitted" in binding.notes


def test_deprovision_respects_protection_and_deletes_owned_children_first() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(
        _spec(
            cron_workflows=[_cron()],
            event_bindings=[_event()],
            deletion_protection=True,
        ),
    )

    protected = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert protected.ok is False
    assert protected.retryable is False
    assert cluster.deleted == []

    forced = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert forced.ok is True
    assert [row["kind"] for row in cluster.deleted[-1][2]] == [
        "CronWorkflow",
        "WorkflowEventBinding",
        "WorkflowTemplate",
    ]
    assert cluster.deleted[-1][3] == "Orphan"


def test_active_cron_runs_block_pruning_and_guard_destructive_teardown() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec(cron_workflows=[_cron()]))
    _child(cluster, "CronWorkflow", "hourly")["status"] = {
        "active": [{"name": "triage-prod-pipeline-hourly-abc"}],
    }

    pruned = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"workflow_spec": _workflow_spec()},
        ),
    )
    guarded = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    forced = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )

    assert pruned.ok is False and pruned.retryable is False
    assert "active Argo CronWorkflows" in pruned.message
    assert guarded.ok is False and guarded.errors == ["active_workflows"]
    assert forced.ok is True
    assert cluster.deleted[-1][3] == "Background"


def test_manual_and_event_runs_are_retained_unless_data_deletion_is_requested() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(_spec())
    workflow_key = (
        f"{API_VERSION}/Workflow",
        "steady-md-triage",
        "manual-run-1",
    )
    cluster.objects[workflow_key] = {
        "apiVersion": API_VERSION,
        "kind": "Workflow",
        "metadata": {
            "name": "manual-run-1",
            "namespace": "steady-md-triage",
            "labels": {
                "app.kubernetes.io/managed-by": "astrolift",
                "astrolift.io/managed-service-id": "service-1",
            },
        },
        "status": {"phase": "Running"},
    }

    guarded = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    cluster.objects[workflow_key]["status"]["phase"] = "Succeeded"
    retained = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))

    assert guarded.ok is False and guarded.errors == ["active_workflows"]
    assert retained.ok is True
    assert workflow_key in cluster.objects
    assert all(row["kind"] != "Workflow" for row in cluster.deleted[-1][2])

    reprovisioned = driver.provision(_spec())
    deleted = driver.deprovision(
        DeprovisionSpec(handle=reprovisioned.handle),
        delete_data=True,
    )

    assert deleted.ok is True
    assert workflow_key not in cluster.objects
    assert cluster.deleted[-1][2][0]["kind"] == "Workflow"


def test_declarative_snapshot_restore_and_legacy_handles_fail_closed() -> None:
    driver, cluster = _driver()
    provisioned = driver.provision(
        _spec(
            workflow_spec=_workflow_spec(
                workflowMetadata={
                    "labels": {"example.test/class": "triage"},
                    "annotations": {"example.test/owner": "engineering"},
                },
            ),
            cron_workflows=[
                {
                    **_cron(),
                    "labels": {"example.test/schedule": "hourly"},
                    "annotations": {"example.test/source": "snapshot"},
                },
            ],
            event_bindings=[_event()],
            labels={"example.test/service": "triage"},
            annotations={"example.test/team": "engineering"},
            deletion_protection=True,
        ),
    )

    snapshot = driver.snapshot(ServiceHandle(provisioned.handle))
    restored = driver.restore(
        snapshot,
        replace(
            _spec(workflow_spec=_workflow_spec(entrypoint="wrong")),
            service_handle_hint="restored",
            managed_service_id="service-2",
        ),
    )

    assert snapshot.snapshot_id.startswith("workflow_engine/cluster-1/steady-md-triage/")
    assert restored.ok is True
    restored_template = _template(cluster, "triage-prod-restored")
    assert restored_template["spec"]["entrypoint"] == "main"
    assert restored_template["metadata"]["labels"]["example.test/service"] == "triage"
    assert restored_template["metadata"]["annotations"]["example.test/team"] == "engineering"
    assert restored_template["metadata"]["annotations"]["astrolift.io/deletion-protection"] == "true"
    restored_cron = _child(cluster, "CronWorkflow", "hourly", "triage-prod-restored")
    assert restored_cron["spec"]["schedules"] == ["0 * * * *"]
    assert restored_cron["metadata"]["labels"]["example.test/schedule"] == "hourly"
    assert restored_cron["metadata"]["annotations"]["example.test/source"] == "snapshot"
    assert _child(cluster, "WorkflowEventBinding", "webhook", "triage-prod-restored")

    snapshot_resource = cluster.objects[
        (
            "v1/ConfigMap",
            "steady-md-triage",
            snapshot.snapshot_id.rsplit("/", 1)[-1],
        )
    ]
    assert snapshot_resource["immutable"] is True
    declaration = json.loads(snapshot_resource["data"]["declaration.json"])
    assert declaration["cron_workflows"][0]["name"] == "hourly"
    assert declaration["event_bindings"][0]["name"] == "webhook"
    snapshot_resource["metadata"]["annotations"]["astrolift.io/workflow-snapshot-of"] = "wrong"
    invalid = driver.restore(
        snapshot,
        replace(
            _spec(),
            service_handle_hint="invalid",
            managed_service_id="service-3",
        ),
    )
    assert invalid.ok is False
    assert "provenance" in invalid.message

    snapshot_resource["metadata"]["annotations"]["astrolift.io/workflow-snapshot-of"] = snapshot.handle
    snapshot_resource["data"]["declaration.json"] += " "
    tampered = driver.restore(
        snapshot,
        replace(
            _spec(),
            service_handle_hint="tampered",
            managed_service_id="service-4",
        ),
    )
    assert tampered.ok is False
    assert "integrity" in tampered.message

    legacy = driver.status(ServiceHandle("legacy-handle"))
    assert legacy.state == "error"
    assert "legacy" in legacy.message


def test_snapshot_integrity_name_survives_max_length_parent_name() -> None:
    driver, _cluster = _driver()
    provisioned = driver.provision(
        replace(_spec(), service_handle_hint="x" * 63),
    )

    assert len(provisioned.handle.rsplit("/", 1)[-1]) == 63
    snapshot = driver.snapshot(ServiceHandle(provisioned.handle))
    snapshot_name = snapshot.snapshot_id.rsplit("/", 1)[-1]
    restored = driver.restore(
        snapshot,
        replace(
            _spec(),
            service_handle_hint="restored-long",
            managed_service_id="service-2",
        ),
    )

    assert snapshot_name.startswith("astrolift-workflow-snapshot-")
    assert len(snapshot_name) == 51
    assert restored.ok is True


def test_plugin_catalogue_preflight_and_contract_are_executable(
    _registered_plugin: None,
) -> None:
    from astrolift_services.managed_service_catalog import list_catalog

    driver_type = PLUGIN.managed_service_drivers[("workflow_engine", "argo_workflows")]
    assert driver_type is ArgoWorkflowsDriver
    schema = driver_type(config=ArgoWorkflowsConfig(cluster_driver=_Cluster())).config_schema()
    assert schema["required"] == ["workflow_spec"]

    missing = preflight(
        kind="workflow_engine",
        variant="argo_workflows",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32",
            installed_crds=frozenset(
                {
                    "workflows.argoproj.io",
                    "workflowtemplates.argoproj.io",
                },
            ),
            crd_inventory_probed=True,
            operator_versions={"argo-workflows": "4.1.1"},
        ),
    )
    stale = preflight(
        kind="workflow_engine",
        variant="argo_workflows",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32",
            installed_crds=frozenset(
                {
                    "workflows.argoproj.io",
                    "workflowtemplates.argoproj.io",
                    "cronworkflows.argoproj.io",
                    "workfloweventbindings.argoproj.io",
                },
            ),
            crd_inventory_probed=True,
            operator_versions={"argo-workflows": "3.7.13"},
        ),
    )
    installed = preflight(
        kind="workflow_engine",
        variant="argo_workflows",
        capabilities=ClusterCapabilities(
            cluster_id="cluster-1",
            kubernetes_version="1.32",
            installed_crds=frozenset(
                {
                    "workflows.argoproj.io",
                    "workflowtemplates.argoproj.io",
                    "cronworkflows.argoproj.io",
                    "workfloweventbindings.argoproj.io",
                },
            ),
            crd_inventory_probed=True,
            operator_versions={"argo-workflows": "v4.1.1"},
        ),
    )
    row = next(
        item
        for item in list_catalog("k8s_native")
        if (item.kind, item.variant) == ("workflow_engine", "argo_workflows")
    )

    assert missing.ok is False
    assert {failure.code for failure in missing.failures} == {"missing_crd"}
    assert stale.ok is False
    assert stale.failures[0].code == "operator_too_old"
    assert "v4.1.1" in stale.install_hints[0]
    assert installed.ok is True
    assert row.available is True
    assert "ARGO_EVENT_ENDPOINT" in row.binding_envs
