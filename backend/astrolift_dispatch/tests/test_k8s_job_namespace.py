"""The K8s Job spawner must ensure the per-org agent namespace exists before
creating the Job (#983 agents-grid).

Unlike the app deploy path there's no separate provision_namespace step, so a
first-ever agent dispatch would 404 on the Job POST. ``spawn()`` calls the
cluster driver's idempotent ``ensure_namespace`` first; this pins that order.
"""

from __future__ import annotations

from types import SimpleNamespace

from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner


class _ApplyResult:
    ok = True

    def summary(self):  # pragma: no cover - not reached when ok
        return "ok"


class _FailedApplyResult:
    ok = False

    def summary(self):
        return "job rejected"


class _DeleteResult:
    ok = True


class _RecordingDriver:
    def __init__(self, *, fail_apply=False):
        self.calls: list[str] = []
        self.fail_apply = fail_apply
        self.deleted: list[dict] = []
        self.applied: list[dict] = []
        self.live_job = None
        self.pods = []
        self.propagation_policy = None

    def ensure_namespace(self, cluster, name, labels, annotations):
        self.calls.append(f"ensure_namespace:{name}")
        return None

    def apply_manifests(self, cluster, namespace, manifests):
        self.calls.append(f"apply_manifests:{namespace}")
        self.applied.extend(manifests)
        return _FailedApplyResult() if self.fail_apply else _ApplyResult()

    def list_csi_drivers(self, _cluster):
        return ["smb.csi.k8s.io"]

    def persistent_volume_claim_exists(self, _cluster, _namespace, _name):
        return True

    def delete_manifests(self, cluster, namespace, manifests, *, propagation_policy=None):
        self.propagation_policy = propagation_policy
        self.calls.append(f"delete_manifests:{namespace}")
        self.deleted.extend(manifests)
        return _DeleteResult()

    def get_manifest(self, cluster, namespace, kind, name):
        return self.live_job

    def list_manifests(self, cluster, namespace, kind):
        return self.pods


class _Ctx:
    slug = "astrolift-eks"


class _PrimaryQS:
    def __init__(self, container):
        self._c = container

    def filter(self, **_):
        return self

    def first(self):
        return self._c


class _Container:
    image_ref = "docker.io/library/busybox:latest"
    port = 0
    command = []
    args = []


class _Workload:
    containers = _PrimaryQS(_Container())


class _Task:
    guid = "abc12345-0000-0000-0000-000000000000"
    timeout_seconds = 900
    vnc_enabled = False
    environment_spec = None
    agent_definition = _Workload()


def test_spawn_ensures_namespace_before_applying_job(monkeypatch):
    driver = _RecordingDriver()
    # spawn() does `from core.cluster_management import ...` at call time, so
    # patch the source module, not the spawner module.
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver, raising=False)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx(), raising=False)
    # The brief/snapshot injectors are import-time-resolved inside spawn(); stub
    # them to identity so the test isolates the namespace-then-apply ordering.
    import astrolift_dispatch.brief_injector as brief_injector
    import astrolift_dispatch.snapshot_injector as snapshot_injector

    monkeypatch.setattr(brief_injector, "inject_brief_into_job_spec", lambda m, t: m)
    monkeypatch.setattr(snapshot_injector, "inject_snapshot_into_job_spec", lambda m, t: m)

    spawner = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-steadymd")
    result = spawner.spawn(_Task())

    assert result.ok, result.error
    # ensure_namespace must run, and must precede apply_manifests.
    assert "ensure_namespace:astrolift-agents-steadymd" in driver.calls
    assert "apply_manifests:astrolift-agents-steadymd" in driver.calls
    assert driver.calls.index("ensure_namespace:astrolift-agents-steadymd") < driver.calls.index(
        "apply_manifests:astrolift-agents-steadymd"
    )


def test_rendered_job_has_kubernetes_deadline():
    from astrolift_dispatch.spawners.k8s_job import _render_agent_job

    job = _render_agent_job(
        job_name="agent-task-deadline",
        workload=_Workload(),
        namespace="astrolift-agents-steadymd",
        task=_Task(),
    )

    assert job["spec"]["activeDeadlineSeconds"] == 900
    assert job["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] == "Always"


def test_mutable_implicit_latest_image_is_always_pulled():
    from astrolift_dispatch.spawners.k8s_job import _image_pull_policy

    assert _image_pull_policy("busybox") == "Always"
    assert _image_pull_policy("registry.example:5000/team/agent") == "Always"
    assert _image_pull_policy("registry.example/team/agent:latest") == "Always"
    assert _image_pull_policy("registry.example/team/agent:v1") == "IfNotPresent"
    assert _image_pull_policy("registry.example/team/agent@sha256:abc") == "IfNotPresent"


def test_failed_apply_cleans_up_partial_job_and_secret(monkeypatch):
    driver = _RecordingDriver(fail_apply=True)
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver, raising=False)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx(), raising=False)
    import astrolift_dispatch.brief_injector as brief_injector
    import astrolift_dispatch.snapshot_injector as snapshot_injector

    monkeypatch.setattr(brief_injector, "inject_brief_into_job_spec", lambda m, t: m)
    monkeypatch.setattr(snapshot_injector, "inject_snapshot_into_job_spec", lambda m, t: m)

    result = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-steadymd").spawn(_Task())

    assert result.ok is False
    assert result.error == "job rejected"
    assert [item["kind"] for item in driver.deleted] == ["Job", "Secret"]
    assert driver.calls.index("apply_manifests:astrolift-agents-steadymd") < driver.calls.index(
        "delete_manifests:astrolift-agents-steadymd"
    )


def test_terminal_cleanup_deletes_only_temporary_secret(monkeypatch):
    driver = _RecordingDriver()
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver, raising=False)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx(), raising=False)
    monkeypatch.setattr(
        "astrolift_dispatch.spawners.k8s_job._task_for_external_id",
        lambda _external_id: None,
    )

    spawner = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-steadymd")
    spawner.cleanup_task_secret("agent-task-complete")

    assert [item["kind"] for item in driver.deleted] == ["Secret"]
    assert driver.deleted[0]["metadata"]["name"] == "agent-task-complete-secrets"


def test_stop_kills_job_before_attachment_recovery(monkeypatch):
    driver = _RecordingDriver()
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver, raising=False)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx(), raising=False)
    monkeypatch.setattr(
        "astrolift_dispatch.spawners.k8s_job._task_for_external_id",
        lambda _external_id: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    spawner = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-steadymd")

    try:
        spawner.stop("agent-task-restarted")
    except RuntimeError as exc:
        assert str(exc) == (
            "Job agent-task-restarted was deleted but filesystem cleanup failed: database unavailable"
        )
    else:  # pragma: no cover - the recovery failure must remain visible
        raise AssertionError("stop should surface attachment recovery failure")

    assert [item["kind"] for item in driver.deleted] == ["Job", "Secret"]


def test_agent_filesystem_mount_materializes_credentials_and_owned_storage(monkeypatch):
    driver = _RecordingDriver()
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver, raising=False)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx(), raising=False)
    import astrolift_dispatch.agent_secrets as agent_secrets
    import astrolift_dispatch.brief_injector as brief_injector
    import astrolift_dispatch.snapshot_injector as snapshot_injector
    import astrolift_services.filesystem_bindings as filesystem_bindings

    binding = SimpleNamespace(
        guid="019fffff-1111-7111-8111-111111111111",
        name="shared-files",
        mount_path="/workspace",
        sub_path="",
        source_kind="csi",
        protocol="smb3",
        claim_name="",
        claim_namespace="",
        csi_driver="smb.csi.k8s.io",
        volume_handle="files.internal##share",
        volume_attributes={"source": "//files.internal/share"},
        secret_refs={"username": "secret/fsx#username", "password": "secret/fsx#password"},
        mount_options=["vers=3.0"],
        read_only=False,
        capacity="100Gi",
        access_modes=["ReadWriteMany"],
        workload_names=[],
        container_names=[],
        managed_service=SimpleNamespace(
            guid="019fffff-2222-7222-8222-222222222222",
            kind="filesystem",
            name="shared",
        ),
    )
    monkeypatch.setattr(filesystem_bindings, "agent_volume_bindings", lambda _spec: [binding])
    monkeypatch.setattr(
        agent_secrets,
        "resolve_secrets_backend",
        lambda _cluster: SimpleNamespace(
            get=lambda ref: {
                "secret/fsx": {"username": "agent-user", "password": "not-persisted"},
            }[ref],
        ),
    )
    monkeypatch.setattr(brief_injector, "inject_brief_into_job_spec", lambda m, t: m)
    monkeypatch.setattr(snapshot_injector, "inject_snapshot_into_job_spec", lambda m, t: m)

    spawner = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-steadymd")
    result = spawner.spawn(_Task())

    assert result.ok, result.error
    assert [item["kind"] for item in driver.applied] == [
        "Secret",
        "PersistentVolume",
        "PersistentVolumeClaim",
        "Job",
    ]
    assert driver.applied[0]["stringData"] == {
        "password": "not-persisted",
        "username": "agent-user",
    }
    job = driver.applied[-1]
    pod_spec = job["spec"]["template"]["spec"]
    assert pod_spec["volumes"][0]["name"] == "shared-files"
    assert pod_spec["containers"][0]["volumeMounts"][0]["mountPath"] == "/workspace"

    spawner.stop(result.external_id)

    assert [item["kind"] for item in driver.deleted] == [
        "Job",
        "Secret",
        "Secret",
        "PersistentVolumeClaim",
        "PersistentVolume",
    ]


def test_agent_existing_claim_is_referenced_but_never_deleted(monkeypatch):
    driver = _RecordingDriver()
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver, raising=False)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx(), raising=False)
    import astrolift_dispatch.brief_injector as brief_injector
    import astrolift_dispatch.snapshot_injector as snapshot_injector
    import astrolift_services.filesystem_bindings as filesystem_bindings

    binding = SimpleNamespace(
        guid="019fffff-1111-7111-8111-111111111111",
        name="existing-files",
        mount_path="/workspace",
        sub_path="",
        source_kind="existing_pvc",
        protocol="pvc",
        claim_name="operator-owned-pvc",
        claim_namespace="astrolift-agents-steadymd",
        csi_driver="",
        volume_handle="",
        volume_attributes={},
        secret_refs={},
        mount_options=[],
        read_only=True,
        capacity="1Gi",
        access_modes=["ReadWriteMany"],
        workload_names=[],
        container_names=[],
        managed_service=SimpleNamespace(
            guid="019fffff-2222-7222-8222-222222222222",
            kind="filesystem",
            name="existing",
        ),
    )
    monkeypatch.setattr(filesystem_bindings, "agent_volume_bindings", lambda _spec: [binding])
    monkeypatch.setattr(brief_injector, "inject_brief_into_job_spec", lambda m, t: m)
    monkeypatch.setattr(snapshot_injector, "inject_snapshot_into_job_spec", lambda m, t: m)

    spawner = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-steadymd")
    result = spawner.spawn(_Task())
    spawner.stop(result.external_id)

    assert result.ok, result.error
    assert [item["kind"] for item in driver.applied] == ["Job"]
    assert [item["kind"] for item in driver.deleted] == ["Job", "Secret"]


def test_distinct_tasks_created_in_same_millisecond_get_distinct_jobs(monkeypatch):
    import astrolift_dispatch.brief_injector as brief_injector
    import astrolift_dispatch.snapshot_injector as snapshot_injector
    import core.cluster_management as cm

    driver = _RecordingDriver()
    monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda _c: _Ctx())
    monkeypatch.setattr(brief_injector, "inject_brief_into_job_spec", lambda m, t: m)
    monkeypatch.setattr(snapshot_injector, "inject_snapshot_into_job_spec", lambda m, t: m)
    first, second = _Task(), _Task()
    first.guid = "019f2065-6789-7001-8000-000000000001"
    second.guid = "019f2065-6789-7002-8000-000000000002"
    spawner = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-concurrent")
    a, b, retry = spawner.spawn(first), spawner.spawn(second), spawner.spawn(first)
    assert a.ok and b.ok and retry.ok
    assert retry.external_id == a.external_id
    assert len(a.external_id) <= 63
    assert a.external_id != b.external_id
    assert len({m["metadata"]["name"] for m in driver.applied if m["kind"] == "Job"}) == 2


def test_owned_stop_uses_uid_precondition_and_waits_for_dependents(monkeypatch):
    import core.cluster_management as cm

    driver = _RecordingDriver()
    driver.live_job = {
        "metadata": {
            "uid": "original-job-uid",
            "labels": {"astrolift.dev/task-id": _Task.guid},
        }
    }
    monkeypatch.setattr(cm, "_driver_for_cluster", lambda cluster: driver)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda cluster: _Ctx())
    monkeypatch.setattr("astrolift_dispatch.spawners.k8s_job._task_for_external_id", lambda external_id: None)
    spawner = K8sJobSpawner(cluster=object(), namespace="agents")
    spawner.stop("owned-job", expected_task_guid=_Task.guid)
    assert driver.propagation_policy == "Foreground"
    assert driver.deleted[0]["metadata"]["uid"] == "original-job-uid"
    assert not spawner.confirm_stopped("owned-job")
    driver.live_job = None
    driver.pods = [{"metadata": {"ownerReferences": [{"kind": "Job", "name": "owned-job"}]}}]
    assert not spawner.confirm_stopped("owned-job")
    driver.pods = []
    assert spawner.confirm_stopped("owned-job")


def test_owned_stop_refuses_another_tasks_job(monkeypatch):
    import pytest

    import core.cluster_management as cm

    driver = _RecordingDriver()
    driver.live_job = {"metadata": {"uid": "foreign", "labels": {"astrolift.dev/task-id": "another-task"}}}
    monkeypatch.setattr(cm, "_driver_for_cluster", lambda cluster: driver)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda cluster: _Ctx())
    with pytest.raises(RuntimeError, match="different agent task"):
        K8sJobSpawner(cluster=object(), namespace="agents").stop("owned-job", expected_task_guid=_Task.guid)
    assert driver.deleted == []


def test_owned_stop_does_not_delete_unobserved_job_by_name(monkeypatch):
    import core.cluster_management as cm

    driver = _RecordingDriver()
    monkeypatch.setattr(cm, "_driver_for_cluster", lambda cluster: driver)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda cluster: _Ctx())
    monkeypatch.setattr("astrolift_dispatch.spawners.k8s_job._task_for_external_id", lambda external_id: None)
    K8sJobSpawner(cluster=object(), namespace="agents").stop("missing-job", expected_task_guid=_Task.guid)
    assert [ref["kind"] for ref in driver.deleted] == ["Secret"]


def test_owned_stop_refuses_driver_without_real_resource_reads(monkeypatch):
    import pytest
    from _sdk.cluster import ClusterDriver

    import core.cluster_management as cm

    class UnsupportedDriver(_RecordingDriver):
        get_manifest = ClusterDriver.get_manifest
        list_manifests = ClusterDriver.list_manifests

    driver = UnsupportedDriver()
    monkeypatch.setattr(cm, "_driver_for_cluster", lambda cluster: driver)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda cluster: _Ctx())
    with pytest.raises(RuntimeError, match="cannot verify"):
        K8sJobSpawner(cluster=object(), namespace="agents").stop("owned-job", expected_task_guid=_Task.guid)
    assert driver.deleted == []
