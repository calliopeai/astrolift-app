import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from django.db import close_old_connections
from django.utils import timezone

from astrolift_agents.models import AgentBox, AgentEnvironmentSpec, ManagedBoxRuntime
from astrolift_agents.services.agent_box import AgentBoxError, observe_box, start_agent_box, stop_agent_box
from astrolift_agents.services.managed_box_lifecycle import (
    authority_secret_name,
    reconcile_managed_runtime,
)
from astrolift_agents.services.managed_runtime_authority import (
    RuntimeAuthorityError,
    authenticated_runtime,
    validate_runtime_authority,
)
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from astrolift_operations.models import AuditEvent

pytestmark = pytest.mark.django_db


class Cluster:
    def __init__(self):
        self.objects = {}
        self.applied = []
        self.deleted = []
        self.delivery_error = False
        self.delete_error = False
        self.start_error = ""
        self.storage_class = SimpleNamespace(
            name="managed", provisioner="csi.example", reclaim_policy="Retain"
        )

    def list_storage_classes(self, cluster):
        return [self.storage_class]

    def list_csi_drivers(self, cluster):
        return ["csi.example"]

    def get_manifest(self, cluster, namespace, kind, name):
        return copy.deepcopy(self.objects.get((kind, name)))

    def apply_manifests(self, cluster, namespace, manifests, *, create_only=False):
        for source in manifests:
            if (
                self.delivery_error
                and source["kind"] == "Secret"
                and '"token"' in source.get("stringData", {}).get("authority.json", "")
            ):
                raise RuntimeError(source["stringData"]["authority.json"])
            self.applied.append(copy.deepcopy(source))
            key = (source["kind"], source["metadata"]["name"])
            old = self.objects.get(key, {})
            if create_only and old:
                return SimpleNamespace(ok=False)
            value = copy.deepcopy(source)
            meta = value["metadata"]
            if "resourceVersion" in meta:
                assert meta["resourceVersion"] == old["metadata"]["resourceVersion"]
            meta["uid"] = old.get("metadata", {}).get("uid", str(uuid.uuid4()))
            meta["resourceVersion"] = str(int(old.get("metadata", {}).get("resourceVersion", "0")) + 1)
            if source["kind"] == "PersistentVolumeClaim":
                value["status"] = {"phase": "Bound"}
            self.objects[key] = value
            if source["kind"] == "Job":
                self.objects[("Pod", "managed-pod")] = {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": "managed-pod",
                        "namespace": namespace,
                        "uid": str(uuid.uuid4()),
                        "resourceVersion": "1",
                        "labels": copy.deepcopy(value["spec"]["template"]["metadata"]["labels"]),
                        "ownerReferences": [
                            {"kind": "Job", "name": meta["name"], "uid": meta["uid"], "controller": True}
                        ],
                    },
                    "spec": copy.deepcopy(value["spec"]["template"]["spec"]),
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {
                                "name": c["name"],
                                "image": c["image"],
                                "imageID": c["image"],
                                "state": {"running": {"startedAt": "2026-09-30T00:00:00Z"}},
                            }
                            for c in value["spec"]["template"]["spec"]["containers"]
                        ],
                    },
                }
                if self.start_error == "exception":
                    raise RuntimeError("model-fixture")
                if self.start_error == "result":
                    return SimpleNamespace(ok=False)
        return SimpleNamespace(ok=True)

    def get_workload_status(self, *args):
        return SimpleNamespace(ready_replicas=1, conditions=[])

    def delete_manifests(self, cluster, namespace, manifests, **kwargs):
        assert kwargs["propagation_policy"] == "Foreground"
        if self.delete_error:
            raise RuntimeError("cluster unavailable")
        for value in manifests:
            key = (value["kind"], value["metadata"]["name"])
            assert self.objects[key]["metadata"]["uid"] == value["metadata"]["uid"]
            self.deleted.append(copy.deepcopy(value))
            del self.objects[key]
        return SimpleNamespace(ok=True)


@pytest.fixture
def world(monkeypatch, settings):
    settings.PLATFORM_API_URL = "https://platform.example.test"
    settings.MANAGED_IDE_BROWSER_IMAGE = "browser@sha256:" + "b" * 64
    settings.MANAGED_IDE_STORAGE_CLASS = "managed"
    settings.MANAGED_IDE_CSI_DRIVER = "csi.example"
    settings.AGENT_RUNTIME_CLASS = ""
    org = Organization.objects.create(name="Managed", slug="managed")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="K8s", slug="managed", capabilities_manifest={}, config_schema={})]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="Managed",
        slug="managed",
        provider_plugin=plugin,
        lifecycle="managed",
        auth_method="kubeconfig",
    )
    spec = AgentEnvironmentSpec.objects.create(
        organization=org,
        name="IDE",
        slug="ide",
        agent_type="claude",
        runtime="calliope-managed-ide",
        image_tag="ide@sha256:" + "a" * 64,
    )
    box = AgentBox.objects.create(
        organization=org, environment_spec=spec, name="IDE", slug="ide", idle_timeout_seconds=0
    )
    driver = Cluster()
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda _: driver)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda _: SimpleNamespace(slug=cluster.slug)
    )
    monkeypatch.setattr(
        "core.cluster_observability.list_app_pods",
        lambda **kwargs: [SimpleNamespace(name="managed-pod", ready=True)],
    )
    monkeypatch.setattr("astrolift_dispatch.agent_network_fence.agent_fence_manifests", lambda *args: [])
    monkeypatch.setattr(
        "astrolift_dispatch.agent_secrets.resolve_task_secret_manifest",
        lambda **kw: {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {"name": kw["secret_name"], "namespace": kw["namespace"]},
            "stringData": {"MODEL_KEY": "model-fixture"},
        },
    )
    return SimpleNamespace(box=box, spec=spec, cluster=cluster, driver=driver)


def ready(world):
    start_agent_box(world.box)
    assert observe_box(world.box) == "running"
    return ManagedBoxRuntime.objects.get(box=world.box)


def credential(world, runtime):
    manifest = world.driver.objects[("Secret", authority_secret_name(runtime.job_name))]
    return json.loads(manifest["stringData"]["authority.json"])


def test_real_start_observe_certification_and_validation(world):
    start_agent_box(world.box)
    runtime = ManagedBoxRuntime.objects.get(box=world.box)
    assert runtime.phase == "pending"
    assert runtime.job_uid and runtime.claim_uid
    assert credential(world, runtime) == {"version": 1, "status": "pending"}
    assert not runtime.token_hash
    assert observe_box(world.box) == "running"
    runtime.refresh_from_db()
    cert = credential(world, runtime)
    assert runtime.phase == "certified"
    assert cert["ownerEpoch"] == str(runtime.owner_epoch)
    assert cert["token"] not in runtime.token_hash
    response = validate_runtime_authority(
        runtime.pk,
        cert["token"],
        {
            "version": 1,
            "ownerEpoch": cert["ownerEpoch"],
            "podUid": cert["podUid"],
            "runtimeInstanceId": "e" * 32,
        },
    )
    assert response["incarnation"]["containerName"] == "runtime"
    claim = world.driver.objects[("PersistentVolumeClaim", runtime.claim_name)]
    assert claim["spec"]["accessModes"] == ["ReadWriteOncePod"]
    assert "ownerReferences" not in claim["metadata"]
    pod = world.driver.objects[("Pod", "managed-pod")]
    assert pod["spec"]["nodeSelector"] == {"kubernetes.io/arch": "amd64"}
    assert "MODEL_KEY" not in str(pod["spec"]["containers"][1])


def test_credential_rotation_preserves_owner_and_bound_process(world):
    runtime = ready(world)
    old = credential(world, runtime)
    runtime.runtime_instance_id = "e" * 32
    runtime.token_expires_at = timezone.now() + timedelta(minutes=10)
    runtime.save()
    assert reconcile_managed_runtime(runtime.pk)
    runtime.refresh_from_db()
    new = credential(world, runtime)
    assert old["token"] != new["token"]
    assert new["ownerEpoch"] == old["ownerEpoch"]
    assert runtime.runtime_instance_id == "e" * 32
    assert authenticated_runtime(world.box.guid, old["token"]) is None
    assert authenticated_runtime(world.box.guid, new["token"]).pk == runtime.pk


def test_failed_secret_delivery_rolls_back_certification_and_can_retry(world):
    start_agent_box(world.box)
    runtime = ManagedBoxRuntime.objects.get(box=world.box)
    world.driver.delivery_error = True
    with pytest.raises(RuntimeAuthorityError, match="^Managed runtime credential delivery failed.$"):
        reconcile_managed_runtime(runtime.pk)
    runtime.refresh_from_db()
    assert runtime.phase == "pending" and runtime.token_hash == ""
    world.driver.delivery_error = False
    assert reconcile_managed_runtime(runtime.pk)


@pytest.mark.parametrize("delete_error", [False, True])
def test_stop_revokes_before_teardown_and_never_deletes_storage(world, delete_error):
    runtime = ready(world)
    token = credential(world, runtime)["token"]
    world.driver.delete_error = delete_error
    stop_agent_box(world.box)
    runtime.refresh_from_db()
    assert runtime.phase == "revoked"
    assert authenticated_runtime(world.box.guid, token) is None
    assert ("PersistentVolumeClaim", runtime.claim_name) in world.driver.objects
    assert all(ref["kind"] != "PersistentVolumeClaim" for ref in world.driver.deleted)
    assert world.box.status == "stopped"


def test_stop_refuses_to_delete_replacement_job(world):
    runtime = ready(world)
    world.driver.objects[("Job", runtime.job_name)]["metadata"]["uid"] = "replacement"
    with pytest.raises(AgentBoxError, match="ownership changed"):
        stop_agent_box(world.box, require_teardown=True)
    assert ("Job", runtime.job_name) in world.driver.objects
    runtime.refresh_from_db()
    assert runtime.phase == "revoked"


@pytest.mark.parametrize(
    "setting,value",
    [
        ("MANAGED_IDE_BROWSER_IMAGE", "browser:latest"),
        ("MANAGED_IDE_STORAGE_CLASS", "missing"),
        ("MANAGED_IDE_CSI_DRIVER", "missing"),
        ("MANAGED_IDE_STORAGE_CAPACITY", "0Gi"),
    ],
)
def test_bad_configuration_has_no_cluster_or_ownership_side_effect(world, settings, setting, value):
    setattr(settings, setting, value)
    with pytest.raises(AgentBoxError):
        start_agent_box(world.box)
    assert not world.driver.applied
    assert not ManagedBoxRuntime.objects.exists()


def test_nonretaining_storage_is_refused(world):
    world.driver.storage_class.reclaim_policy = "Delete"
    with pytest.raises(AgentBoxError, match="Retain"):
        start_agent_box(world.box)
    assert not world.driver.applied


def test_implicit_idle_timeout_is_not_silently_ignored(world):
    world.box.idle_timeout_seconds = 300
    world.box.save()
    with pytest.raises(AgentBoxError, match="idleTimeoutSeconds"):
        start_agent_box(world.box)
    assert not world.driver.applied


def test_resource_replacement_cannot_be_certified(world):
    start_agent_box(world.box)
    runtime = ManagedBoxRuntime.objects.get(box=world.box)
    world.driver.objects[("PersistentVolumeClaim", runtime.claim_name)]["metadata"]["uid"] = "replacement"
    with pytest.raises(RuntimeAuthorityError):
        reconcile_managed_runtime(runtime.pk)
    runtime.refresh_from_db()
    assert runtime.phase == "pending" and runtime.token_hash == ""


@pytest.mark.parametrize("failure", ["exception", "result"])
def test_partial_apply_failure_revocation_survives_transaction_rollback(world, failure):
    world.driver.start_error = failure
    with pytest.raises(AgentBoxError) as error:
        start_agent_box(world.box)
    assert "model-fixture" not in str(error.value)
    assert error.value.__cause__ is None
    runtime = ManagedBoxRuntime.objects.get(box=world.box)
    world.box.refresh_from_db()
    assert runtime.phase == "revoked" and runtime.token_hash == ""
    assert world.box.status == "failed"
    assert list(
        AuditEvent.objects.filter(
            actor_id=str(runtime.guid),
            action__in=["agent_box.runtime.reserve", "agent_box.runtime.revoke"],
        )
        .order_by("occurred_at")
        .values_list("action", flat=True)
    ) == ["agent_box.runtime.reserve", "agent_box.runtime.revoke"]
    assert not reconcile_managed_runtime(runtime.pk)
    assert ("PersistentVolumeClaim", runtime.claim_name) in world.driver.objects
    assert ("Job", runtime.job_name) not in world.driver.objects
    assert ("Secret", authority_secret_name(runtime.job_name)) not in world.driver.objects


def test_stop_after_reservation_wins_before_apply(world, monkeypatch):
    from astrolift_agents.services import managed_box_lifecycle

    original = managed_box_lifecycle.prepare_managed_box

    def cancelled(**kwargs):
        result = original(**kwargs)
        stop_agent_box(world.box)
        return result

    monkeypatch.setattr(managed_box_lifecycle, "prepare_managed_box", cancelled)
    with pytest.raises(AgentBoxError, match="ownership changed"):
        start_agent_box(world.box)
    world.box.refresh_from_db()
    assert world.box.status == "stopped"
    assert not world.driver.applied
    assert ManagedBoxRuntime.objects.get(box=world.box).phase == "revoked"


def test_stop_before_reservation_prevents_late_start(world, monkeypatch):
    original = world.driver.list_storage_classes

    def cancelled(cluster):
        stop_agent_box(world.box)
        return original(cluster)

    monkeypatch.setattr(world.driver, "list_storage_classes", cancelled)
    with pytest.raises(AgentBoxError, match="cancelled"):
        start_agent_box(world.box)
    assert not world.driver.applied
    assert not ManagedBoxRuntime.objects.exists()
    world.box.refresh_from_db()
    assert world.box.status == "stopped"


def test_create_race_never_adopts_foreign_credential_secret(world, monkeypatch):
    from astrolift_agents.services.agent_box import box_job_name, box_secret_name

    original = world.driver.apply_manifests
    name = box_secret_name(box_job_name(world.box))
    foreign = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "uid": "foreign", "namespace": "foreign", "resourceVersion": "1"},
        "stringData": {"model": "foreign-fixture"},
    }

    def raced(cluster, namespace, manifests, **kwargs):
        if manifests[0]["metadata"]["name"] == name:
            world.driver.objects[("Secret", name)] = copy.deepcopy(foreign)
        return original(cluster, namespace, manifests, **kwargs)

    monkeypatch.setattr(world.driver, "apply_manifests", raced)
    with pytest.raises(AgentBoxError):
        start_agent_box(world.box)
    assert world.driver.objects[("Secret", name)] == foreign
    assert not any(key[0] == "Job" for key in world.driver.objects)
    assert ManagedBoxRuntime.objects.get(box=world.box).phase == "revoked"


@pytest.mark.django_db(transaction=True)
def test_concurrent_stop_without_runtime_fences_inflight_preflight(world, monkeypatch):
    entered, released = Event(), Event()
    original = world.driver.list_storage_classes

    def pause(cluster):
        entered.set()
        assert released.wait(10)
        return original(cluster)

    monkeypatch.setattr(world.driver, "list_storage_classes", pause)

    def start():
        close_old_connections()
        try:
            start_agent_box(AgentBox.objects.get(pk=world.box.pk))
            return "unexpected success"
        except AgentBoxError as error:
            return str(error)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as worker:
        attempt = worker.submit(start)
        try:
            assert entered.wait(10)
            stop_agent_box(AgentBox.objects.get(pk=world.box.pk))
        finally:
            released.set()
        assert "cancelled" in attempt.result(timeout=10)
    world.box.refresh_from_db()
    assert world.box.status == "stopped"
    assert not world.driver.applied
    assert not ManagedBoxRuntime.objects.exists()


def test_reserve_and_revoke_audit_exact_owner_once_without_credentials(world):
    runtime = ready(world)
    token = credential(world, runtime)["token"]
    stop_agent_box(world.box)
    stop_agent_box(world.box)
    events = list(
        AuditEvent.objects.filter(
            action__in=["agent_box.runtime.reserve", "agent_box.runtime.revoke"]
        ).order_by("occurred_at")
    )
    assert [event.action for event in events] == ["agent_box.runtime.reserve", "agent_box.runtime.revoke"]
    for event in events:
        assert event.organization_id == world.box.organization_id
        assert event.actor_kind == "system" and event.actor_id == str(runtime.guid)
        assert event.target_kind == "agent_box" and event.target_id == str(world.box.guid)
        assert event.decision == "ALLOW"
        assert token not in str(event.__dict__)
        assert "model-fixture" not in str(event.__dict__)


@pytest.mark.parametrize("operation", ["get_manifest", "delete_manifests"])
@pytest.mark.parametrize("require_teardown", [False, True])
def test_managed_stop_sanitizes_provider_failure_and_allows_retry(
    world, monkeypatch, caplog, operation, require_teardown
):
    runtime = ready(world)
    token = credential(world, runtime)["token"]
    original = getattr(world.driver, operation)
    observations = []

    def failed(*args, **kwargs):
        runtime.refresh_from_db()
        observations.append(
            (
                runtime.phase,
                runtime.token_hash,
                AuditEvent.objects.filter(
                    action="agent_box.runtime.revoke", actor_id=str(runtime.guid)
                ).count(),
            )
        )
        raise RuntimeError(f"provider credential {token} model-fixture")

    monkeypatch.setattr(world.driver, operation, failed)
    expected = "cluster teardown did not complete: managed runtime provider operation failed; retry Stop"
    if require_teardown:
        with pytest.raises(AgentBoxError) as error:
            stop_agent_box(world.box, require_teardown=True)
        assert str(error.value) == expected
        assert error.value.__cause__ is None and error.value.__suppress_context__
    else:
        stop_agent_box(world.box)
    world.box.refresh_from_db()
    assert world.box.last_error == expected
    assert observations == [("revoked", "", 1)]
    assert token not in caplog.text and "model-fixture" not in caplog.text
    assert ("PersistentVolumeClaim", runtime.claim_name) in world.driver.objects
    assert ("Job", runtime.job_name) in world.driver.objects
    monkeypatch.setattr(world.driver, operation, original)
    stop_agent_box(world.box, require_teardown=True)
    world.box.refresh_from_db()
    assert world.box.last_error == ""
    assert ("Job", runtime.job_name) not in world.driver.objects
    assert ("PersistentVolumeClaim", runtime.claim_name) in world.driver.objects
