"""Real PostgreSQL and the native cluster driver preserve retained data identity."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from _sdk.k8s_dynamic_client import NotFoundError, PreconditionFailedError
from django.utils import timezone
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry import expiry
from astrolift_registry.expiry_cleanup import purge_namespaces, retire_runtime
from astrolift_registry.models import RegisteredApp
from astrolift_registry.namespaces import namespace_refusal
from astrolift_registry.tests.test_temporary_apps_2433 import _env, _temp_app, _world  # noqa: F401
from astrolift_services.models import ManagedService
from core.app_deploy import render_resources_for_deployment

pytestmark = pytest.mark.django_db


class NativeObjects:
    def __init__(self):
        self.objects, self.deletes = {}, []
        self.fail_once = False

    def put(self, value):
        value = deepcopy(value)
        meta = value["metadata"]
        meta.update(uid=str(uuid4()), resourceVersion="1")
        kind = value["kind"]
        if "/" in value["apiVersion"]:
            kind = value["apiVersion"] + "/" + kind
        self.objects[(kind, meta.get("namespace"), meta["name"])] = value
        return value

    def get(self, *, kind, namespace, name):
        return deepcopy(self.objects.get((kind, namespace, name)))

    def list(self, *, kind, namespace):
        return [
            deepcopy(value)
            for (key_kind, key_namespace, _), value in self.objects.items()
            if key_kind == kind and key_namespace == namespace
        ]

    def get_namespace(self, *, name):
        value = self.get(kind="Namespace", namespace=None, name=name)
        if value is None:
            raise NotFoundError(name)
        return value

    def delete(self, *, kind, namespace, name, uid=None, resource_version=None, **kwargs):
        key = (kind, None if kind == "Namespace" else namespace, name)
        value = self.objects.get(key)
        if value is None:
            raise NotFoundError(name)
        if uid != value["metadata"]["uid"] or resource_version != value["metadata"]["resourceVersion"]:
            raise PreconditionFailedError("identity changed")
        self.deletes.append((kind, namespace, name, uid))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("apiserver temporarily unavailable")
        self.objects.pop(key)
        if kind == "Namespace":
            self.objects = {key: value for key, value in self.objects.items() if key[1] != name}
        return True


@pytest.fixture
def world():
    return _world("expiry-native2433")


@pytest.fixture
def native(world, monkeypatch):
    app = _temp_app(world, expires_at=timezone.now() - timedelta(seconds=1))
    app.manifest_raw = 'name = "temp"\n[[workloads]]\nname = "web"\nkind = "deployment"\nreplicas = 1\n[[workloads.containers]]\nname = "web"\nis_primary = true\nimage_ref = "nginx:1"\nport = 80\n'
    app.save(update_fields=["manifest_raw"])
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=world.cluster)
    deployment = Deployment.objects.create(
        registered_app=app, app_environment=env, image_tag="fixture", trigger_kind="manual"
    )
    objects = NativeObjects()
    namespace = app.k8s_namespace
    objects.put(
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace,
                "labels": {
                    "astrolift.io/managed-by": "astrolift",
                    "astrolift.io/organization": world.org.slug,
                    "astrolift.io/app": app.slug,
                },
                "annotations": {"astrolift.io/registered-app-id": str(app.pk)},
            },
        }
    )
    resources = render_resources_for_deployment(deployment, include_managed_filesystems=False)
    for resource in resources:
        objects.put(resource)
    from astrolift_workflows.activities.app_lifecycle import _render_literal_secret_resource

    literal = objects.put(
        _render_literal_secret_resource(
            app_slug=app.slug,
            environment_name=env.name,
            namespace=namespace,
            literals={"PRIVATE_MARKER": "private-temporary-app-fixture"},
        )
    )
    from astrolift_registry.brain_apps import brain_secret_name

    brain_secret = objects.put(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": brain_secret_name(app.slug, env.name),
                "namespace": namespace,
                "labels": {"astrolift.io/managed-by": "astrolift", "astrolift.io/brain-for": app.slug},
            },
            "stringData": {"BRAIN_TOKEN": "private-temporary-app-fixture"},
        }
    )
    for version, kind, name in (
        ("postgresql.cnpg.io/v1", "Cluster", "database"),
        ("v1", "PersistentVolumeClaim", "database-1"),
    ):
        objects.put(
            {
                "apiVersion": version,
                "kind": kind,
                "metadata": {"name": name, "namespace": namespace, "labels": {"cnpg.io/cluster": "database"}},
            }
        )
    driver = K8sNativeClusterDriver(config=K8sNativeConfig(), k8s_client_factory=lambda **kwargs: objects)
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: driver)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: SimpleNamespace(slug=cluster.slug)
    )
    assert expiry.fire(app.pk)
    app.refresh_from_db()
    return SimpleNamespace(
        app=app,
        env=env,
        objects=objects,
        namespace=namespace,
        driver=driver,
        literal_name=literal["metadata"]["name"],
        brain_secret_name=brain_secret["metadata"]["name"],
    )


def test_runtime_teardown_keeps_native_cnpg_and_pvc_until_grace(native):
    n = native
    assert retire_runtime(n.app.pk)
    assert all(kind != "Namespace" for kind, *_ in n.objects.deletes)
    assert ("postgresql.cnpg.io/v1/Cluster", n.namespace, "database") in n.objects.objects
    assert ("PersistentVolumeClaim", n.namespace, "database-1") in n.objects.objects
    n.app.refresh_from_db()
    assert n.app.expiry_cleanup[0]["namespace"]["metadata"]["uid"]
    assert ("Secret", n.namespace, n.literal_name) not in n.objects.objects
    assert ("Secret", n.namespace, n.brain_secret_name) not in n.objects.objects
    assert "private-temporary-app-fixture" not in str(n.app.expiry_cleanup)
    RegisteredApp.objects.filter(pk=n.app.pk).update(deleted_at=timezone.now())
    AppEnvironment.objects.filter(pk=n.env.pk).update(deleted_at=timezone.now())
    assert namespace_refusal(n.namespace, organization_id=n.app.organization_id)
    with pytest.raises(RuntimeError, match="DATA_STILL_RETAINED"):
        purge_namespaces(n.app.pk)
    RegisteredApp.all_objects.filter(pk=n.app.pk).update(
        data_retained_until=timezone.now() - timedelta(seconds=1)
    )
    purge_namespaces(n.app.pk)
    assert not n.objects.objects
    assert n.objects.deletes[-1][0] == "Namespace"


def test_partial_runtime_failure_retains_receipt_and_retries_exact_objects(native):
    n = native
    n.objects.fail_once = True
    with pytest.raises(RuntimeError, match="DELETE_UNCONFIRMED"):
        retire_runtime(n.app.pk)
    n.app.refresh_from_db()
    original = deepcopy(n.app.expiry_cleanup)
    assert original and n.app.deleted_at is None
    retire_runtime(n.app.pk)
    n.app.refresh_from_db()
    assert n.app.expiry_cleanup == original
    assert not any(kind == "Namespace" for kind, *_ in n.objects.deletes)


def test_retry_refuses_replacement_runtime_identity(native):
    n = native
    n.objects.fail_once = True
    with pytest.raises(RuntimeError):
        retire_runtime(n.app.pk)
    key = next(key for key in n.objects.objects if key[0] == "apps/v1/Deployment")
    n.objects.objects[key]["metadata"]["uid"] = str(uuid4())
    with pytest.raises(RuntimeError, match="RUNTIME_REPLACED"):
        retire_runtime(n.app.pk)
    assert len(n.objects.deletes) == 1


def test_old_app_controller_is_removed_when_current_manifest_no_longer_names_it(native):
    old = native.objects.put(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "old-worker",
                "namespace": native.namespace,
                "labels": {"astrolift.dev/app": native.app.slug, "astrolift.dev/workload": "old-worker"},
            },
        }
    )
    managed = native.objects.put(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "managed-database",
                "namespace": native.namespace,
                "labels": {"cnpg.io/cluster": "database"},
                "ownerReferences": [{"uid": "database-uid"}],
            },
        }
    )
    assert f"{native.namespace}/Deployment/old-worker" in retire_runtime(native.app.pk)
    assert ("apps/v1/Deployment", native.namespace, old["metadata"]["name"]) not in native.objects.objects
    assert ("apps/v1/Deployment", native.namespace, managed["metadata"]["name"]) in native.objects.objects


def test_stale_shared_edge_routes_and_paused_filters_are_cleaned_by_exact_namespace_owner(native):
    n = native
    n.objects.put({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "astrolift-edge"}})
    for version, kind, name in (
        ("gateway.networking.k8s.io/v1", "HTTPRoute", "old-route"),
        ("gateway.envoyproxy.io/v1alpha1", "HTTPRouteFilter", "old-paused-filter"),
    ):
        n.objects.put(
            {
                "apiVersion": version,
                "kind": kind,
                "metadata": {
                    "name": name,
                    "namespace": "astrolift-edge",
                    "labels": {"astrolift.dev/app": n.app.slug, "astrolift.dev/namespace": n.namespace},
                },
            }
        )
    other = n.objects.put(
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {
                "name": "another-environment",
                "namespace": "astrolift-edge",
                "labels": {"astrolift.dev/app": n.app.slug, "astrolift.dev/namespace": "other-namespace"},
            },
        }
    )
    removed = retire_runtime(n.app.pk)
    assert "astrolift-edge/HTTPRoute/old-route" in removed
    assert "astrolift-edge/HTTPRouteFilter/old-paused-filter" in removed
    assert ("Namespace", None, "astrolift-edge") in n.objects.objects
    assert (
        "gateway.networking.k8s.io/v1/HTTPRoute",
        "astrolift-edge",
        other["metadata"]["name"],
    ) in n.objects.objects


@pytest.mark.parametrize("target", ["Namespace", "apps/v1/Deployment"])
def test_foreign_native_owner_is_refused_before_deletion(native, target):
    key = next(key for key in native.objects.objects if key[0] == target)
    labels = native.objects.objects[key]["metadata"]["labels"]
    labels["astrolift.io/app" if target == "Namespace" else "astrolift.dev/app"] = "another-app"
    with pytest.raises(RuntimeError, match="OWNER_CHANGED"):
        retire_runtime(native.app.pk)
    assert not native.objects.deletes


def test_missing_original_namespace_without_receipt_is_refused(native):
    native.objects.objects.pop(("Namespace", None, native.namespace))
    with pytest.raises(RuntimeError, match="NAMESPACE_UNOBSERVED"):
        retire_runtime(native.app.pk)
    assert not native.objects.deletes


def test_recorded_shared_edge_cleanup_continues_after_namespace_disappears(native):
    from astrolift_registry.expiry_cleanup import _proof

    n = native
    n.objects.fail_once = True
    with pytest.raises(RuntimeError):
        retire_runtime(n.app.pk)
    n.app.refresh_from_db()
    edge = n.objects.put(
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {
                "name": "owned-route",
                "namespace": "astrolift-edge",
                "labels": {"astrolift.dev/app": n.app.slug, "astrolift.dev/namespace": n.namespace},
            },
        }
    )
    ref = _proof(edge)
    ref["labels"] = edge["metadata"]["labels"]
    ref["native"] = _proof(edge)
    n.app.expiry_cleanup[0]["runtime"].append(ref)
    n.app.save(update_fields=["expiry_cleanup"])
    n.objects.objects = {
        key: value
        for key, value in n.objects.objects.items()
        if key[1] != n.namespace and key != ("Namespace", None, n.namespace)
    }
    assert retire_runtime(n.app.pk) == ["astrolift-edge/HTTPRoute/owned-route"]
    assert not n.objects.objects


def test_purge_refuses_replacement_namespace_and_live_managed_service(native):
    n = native
    retire_runtime(n.app.pk)
    RegisteredApp.objects.filter(pk=n.app.pk).update(
        deleted_at=timezone.now(), data_retained_until=timezone.now() - timedelta(seconds=1)
    )
    service = ManagedService.objects.create(
        registered_app=n.app, app_environment=n.env, name="db", kind="postgres", variant="cnpg"
    )
    with pytest.raises(RuntimeError, match="DATA_STILL_RETAINED"):
        purge_namespaces(n.app.pk)
    ManagedService.objects.filter(pk=service.pk).update(deleted_at=timezone.now())
    n.objects.objects[("Namespace", None, n.namespace)]["metadata"]["uid"] = str(uuid4())
    with pytest.raises(RuntimeError, match="NAMESPACE_REPLACED"):
        purge_namespaces(n.app.pk)
    assert not any(kind == "Namespace" for kind, *_ in n.objects.deletes)


def test_expiry_never_guesses_registry_name_or_deletes_shared_repo(native, world, monkeypatch):
    from astrolift_workflows.activities.capability_deprovision import (
        CapabilityDeprovisionError,
        _deprovision_registry_repo_sync,
    )

    calls = []
    monkeypatch.setattr(
        "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver",
        lambda *args: calls.append(args),
    )
    assert _deprovision_registry_repo_sync(registered_app_id=native.app.pk)["skipped"]
    RegisteredApp.objects.filter(pk=native.app.pk).update(registry_repo_uri="registry.example.test/shared")
    _temp_app(world, slug="shared-repo-user", registry_repo_uri="registry.example.test/shared")
    with pytest.raises(CapabilityDeprovisionError, match="another app uses"):
        _deprovision_registry_repo_sync(registered_app_id=native.app.pk, archive=False)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_real_temporal_deregister_retries_native_runtime_and_keeps_original_data(native, temporal_env):
    """Production workflow/activity/driver, committed PG and a transient SDK failure."""
    import asyncio

    from asgiref.sync import sync_to_async
    from temporalio import activity
    from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

    from astrolift_workflows.activities.app_expiry import retire_expired_app_runtime
    from astrolift_workflows.inputs import Actor, DeregisterAppInput
    from astrolift_workflows.workflows import deregister_app

    n = native
    retained = {
        key: value["metadata"]["uid"]
        for key, value in n.objects.objects.items()
        if key[0] in ("Namespace", "PersistentVolumeClaim", "postgresql.cnpg.io/v1/Cluster")
    }
    # These unrelated provider operations have no external endpoints in this
    # fixture. Native runtime retirement keeps its production implementation.
    results = {
        "mark_app_tearing_down": True,
        "abort_in_flight_deploys": [],
        "list_app_managed_service_ids": [],
        "delete_static_dns_records": {"deleted": []},
        "deprovision_app_registry_repo": {"skipped": True},
        "deprovision_app_identity_role": {"skipped": True},
        "list_app_secret_targets": [],
        "revoke_app_secret_bundle_refs": 0,
        "delete_app_source_webhook": {"status": "not_installed", "hook_id": ""},
        "revoke_app_deploy_tokens": 0,
        "soft_delete_app_records": {"registered_app": 1},
        "mark_app_deregistered": None,
        "remove_app_ci_workflow": {"status": "no_repo", "detail": "app has no source repo configured"},
    }
    platform_observations = []

    def provider_activity(attr, result):
        definition = activity._Definition.must_from_callable(getattr(deregister_app, attr))

        @activity.defn(name=definition.name)
        async def run(*args):
            if attr == "soft_delete_app_records":
                platform_observations.append(deepcopy(n.objects.objects))
            return result

        return run

    activities = [retire_expired_app_runtime]
    activities.extend(provider_activity(attr, result) for attr, result in results.items())
    n.objects.fail_once = True
    queue = f"native-expiry-retention-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[deregister_app.DeregisterAppWorkflow],
        activities=activities,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await temporal_env.client.start_workflow(
            deregister_app.DeregisterAppWorkflow.run,
            DeregisterAppInput(
                registered_app_id=n.app.pk,
                actor=Actor(kind="system", display="app expiry"),
                delete_data=False,
                keep_managed_services=True,
                skip_grace=True,
            ),
            id=f"DeregisterAppWorkflow-{n.app.guid}",
            task_queue=queue,
        )
        result = await asyncio.wait_for(handle.result(), timeout=60)
        history = await handle.fetch_history()
    await Replayer(
        workflows=[deregister_app.DeregisterAppWorkflow], workflow_runner=UnsandboxedWorkflowRunner()
    ).replay_workflow(history)
    assert result.ok and not result.data["still_live_resources"]
    assert any(event.activity_task_started_event_attributes.attempt == 2 for event in history.events)
    assert n.objects.deletes.count(n.objects.deletes[0]) == 2
    assert platform_observations and not any(kind == "Namespace" for kind, *_ in n.objects.deletes)
    assert all(n.objects.objects[key]["metadata"]["uid"] == uid for key, uid in retained.items())
    assert ("Secret", n.namespace, n.literal_name) not in platform_observations[0]
    assert ("Secret", n.namespace, n.brain_secret_name) not in platform_observations[0]
    await sync_to_async(n.app.refresh_from_db)()
    assert (
        n.app.expiry_cleanup[0]["namespace"]["metadata"]["uid"] == retained[("Namespace", None, n.namespace)]
    )
    assert "private-temporary-app-fixture" not in history.to_json()
