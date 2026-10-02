"""Real PG + Temporal + task-owned Kubernetes cancellation and cleanup proof."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
import yaml
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from astrolift_pipelines.models import JobRun, Pipeline, PipelineRun
from astrolift_pipelines.run_contracts import (
    dispatch_pipeline_run,
    observe_pipeline_cancellation,
    request_pipeline_cancellation,
    reserve_pipeline_run,
)
from astrolift_workflows import client
from astrolift_workflows.activities import pipeline_job_spawn as activities
from astrolift_workflows.workflows.pipeline_run import PipelineRunWorkflow
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.testing.temporal import temporal_worker

kubernetes_package = pytest.importorskip("kubernetes")
k8s_client = kubernetes_package.client
config = kubernetes_package.config

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def local_cache(settings, monkeypatch):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    monkeypatch.setattr("constance.settings.DATABASE_CACHE_BACKEND", None)


@pytest.fixture
def owned_kubernetes():
    path = os.environ.get("ASTROLIFT_PIPELINE_TEST_KUBECONFIG")
    if not path:
        pytest.skip("requires task-owned local Kind kubeconfig")
    assert Path(path).is_absolute()
    with open(path) as stream:
        document = yaml.safe_load(stream)
    context = "kind-astrolift-env-target-2217-a"
    assert document["current-context"] == context
    configuration = k8s_client.Configuration()
    config.load_kube_config(config_file=path, context=context, client_configuration=configuration)
    assert configuration.host.startswith("https://127.0.0.1:")
    from k8s_native.cluster import _build_k8s_client

    helper = _build_k8s_client(kubeconfig_path=path, context=context, in_cluster=False)
    yield path, context, helper
    helper._api_client.close()


async def test_real_pipeline_cancel_observes_engine_close_and_owned_resource_removal(
    owned_kubernetes, temporal_env, permission_resolver, monkeypatch
):
    path, context, kubernetes = owned_kubernetes
    permission_resolver.grant(Permission.APP_UPDATE)
    suffix = uuid.uuid4().hex[:8]

    def setup():
        org = Organization.objects.create(name="Disposable pipeline proof", slug=f"pipeline-kind-{suffix}")
        user = get_user_model().objects.create_user(username=f"pipeline-kind-{suffix}")
        plugin, _ = ProviderPlugin.objects.get_or_create(
            slug="k8s_native", defaults={"name": "Native", "plugin_version": "test"}
        )
        cluster = TenantCluster.objects.create(
            organization=org,
            provider_plugin=plugin,
            name="Disposable local cluster",
            slug=f"pipeline-kind-{suffix}",
            lifecycle="managed",
            provider_config={"kubeconfig_path": path, "context": context},
        )
        pipeline = Pipeline.objects.create(
            organization=org, name="disposable", repo_url="https://example.test/disposable"
        )
        tenant = TenantContext(organization_id=org.pk, actor_user_id=user.pk)
        with tenant_context(tenant):
            run = reserve_pipeline_run(
                pipeline_id=pipeline.guid,
                expected_version=pipeline.version,
                request_id="disposable-k8s-proof",
                user=user,
            )
        return org, user, cluster, run, tenant

    org, user, cluster, run, tenant = await sync_to_async(setup)()
    namespace = f"astrolift-pipelines-{org.slug}"
    permission_resolver.grant(Permission.APP_UPDATE)

    async def connect():
        return temporal_env.client

    monkeypatch.setattr(client, "_get_client_async", connect)
    monkeypatch.setattr(client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(client, "_task_queue", lambda: "astrolift-test")
    # Only the source document is a deterministic fixture; parser, DB,
    # workflow, provider driver and every Kubernetes RPC are production code.
    toml = 'name="disposable"\n[jobs.hold]\ncontainer="registry.k8s.io/pause:3.10"\n[[jobs.hold.steps]]\nrun="sleep 300"\n'
    monkeypatch.setattr("astrolift_pipelines.toml_fetcher.fetch_pipeline_toml", lambda _: toml)
    registered = [
        activities.mark_pipeline_run_running,
        activities.mark_pipeline_run_success,
        activities.mark_pipeline_run_failed,
        activities.spawn_pipeline_job,
        activities.poll_pipeline_job,
        activities.cancel_pipeline_job,
        activities.mark_job_run_cancelled,
        activities.mark_job_run_failed,
    ]

    def submit():
        with tenant_context(tenant):
            return dispatch_pipeline_run(run)

    try:
        async with temporal_worker(temporal_env, workflows=[PipelineRunWorkflow], activities=registered):
            started = await sync_to_async(submit)()
            handle = temporal_env.client.get_workflow_handle(
                started.temporal_workflow_id, run_id=started.temporal_run_id
            )
            for _ in range(200):
                job = await sync_to_async(
                    lambda: JobRun.objects.filter(pipeline_run=run).exclude(k8s_job_uid="").first()
                )()
                if job:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("Real provider did not record the spawned Kubernetes job")
            assert job.cluster_id == cluster.pk
            resource = await sync_to_async(kubernetes.get)(
                kind="Job", namespace=namespace, name=job.temporal_activity_id
            )
            assert resource["metadata"]["uid"] == job.k8s_job_uid

            def request():
                run.refresh_from_db()
                with tenant_context(tenant):
                    return request_pipeline_cancellation(
                        run,
                        expected_version=run.version,
                        workflow_id=run.temporal_workflow_id,
                        temporal_run_id=run.temporal_run_id,
                    )

            requested = await sync_to_async(request)()
            assert requested.cancellation_status == "acknowledged"
            assert requested.cancellation_observed_at is None
            result = await asyncio.wait_for(handle.result(), 45)
            assert result["ok"] is False
            observed = await sync_to_async(observe_pipeline_cancellation)(requested)
            assert observed.status == "cancelled"
            assert observed.cancellation_status == "observed"
            assert observed.cleanup_status == "complete"
            await sync_to_async(job.refresh_from_db)()
            assert job.cleanup_status == "complete"
            assert (
                await sync_to_async(kubernetes.get)(
                    kind="Job", namespace=namespace, name=job.temporal_activity_id
                )
                is None
            )
            pods = await sync_to_async(kubernetes.list)(kind="Pod", namespace=namespace)
            assert not [
                pod
                for pod in pods
                if any(
                    owner.get("uid") == job.k8s_job_uid
                    for owner in pod["metadata"].get("ownerReferences", [])
                )
            ]
    finally:
        await sync_to_async(kubernetes.delete)(kind="Namespace", namespace=None, name=namespace)


@pytest.fixture
def cleanup_world(owned_kubernetes):
    from astrolift_pipelines.models import Job

    path, context, kubernetes = owned_kubernetes
    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name="Disposable cleanup proof", slug=f"cleanup-kind-{suffix}")
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="k8s_native", defaults={"name": "Native", "plugin_version": "test"}
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=plugin,
        name="Disposable cleanup cluster",
        slug=f"cleanup-kind-{suffix}",
        lifecycle="managed",
        provider_config={"kubeconfig_path": path, "context": context},
    )
    pipeline = Pipeline.objects.create(
        organization=org, name="cleanup", repo_url="https://example.test/cleanup"
    )
    run = PipelineRun.objects.create(pipeline=pipeline, run_number=1, status="running")
    job = Job.objects.create(pipeline=pipeline, pipeline_run=run, job_id="hold", name="Hold")
    namespace = f"astrolift-pipelines-{org.slug}"
    kubernetes.server_side_apply(
        namespace=None,
        manifest={"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
        dry_run=False,
    )
    job_run = JobRun.objects.create(
        pipeline_run=run,
        job=job,
        cluster=cluster,
        k8s_namespace=namespace,
        temporal_activity_id="reviewed-job",
        status="running",
        cleanup_status="pending",
    )
    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": "reviewed-job",
            "namespace": namespace,
            "labels": {"astrolift.io/pipeline-run-id": str(run.pk)},
        },
        "spec": {
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [{"name": "hold", "image": "registry.k8s.io/pause:3.10"}],
                }
            }
        },
    }
    kubernetes.server_side_apply(namespace=namespace, manifest=manifest, dry_run=False)
    job_run.k8s_job_uid = kubernetes.get(kind="Job", namespace=namespace, name="reviewed-job")["metadata"][
        "uid"
    ]
    job_run.save()
    try:
        yield org, cluster, run, job_run, namespace, kubernetes
    finally:
        kubernetes.delete(kind="Namespace", namespace=None, name=namespace)


def test_failed_spawn_without_receipt_removes_owned_secret_but_never_guesses_job_identity(cleanup_world):
    org, cluster, run, job, namespace, kubernetes = cleanup_world
    name = f"pipeline-job-{job.guid}-secrets"
    kubernetes.server_side_apply(
        namespace=namespace,
        manifest={
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {"astrolift.dev/pipeline-job-run": str(job.guid)},
            },
            "stringData": {"credential": "disposable-test-value"},
        },
        dry_run=False,
    )
    job.k8s_job_uid = ""
    job.save()
    activities._cleanup_secrets_quietly(job, "unrecorded-namespace", run)
    job.refresh_from_db()
    assert job.cleanup_status == "failed"
    assert kubernetes.get(kind="Secret", namespace=namespace, name=name) is None
    assert kubernetes.get(kind="Job", namespace=namespace, name=job.temporal_activity_id) is not None


def test_real_cleanup_removes_owned_secret_and_settles_step_runs(cleanup_world):
    from astrolift_pipelines.models import Step, StepRun

    org, cluster, run, job, namespace, kubernetes = cleanup_world
    secret_name = f"pipeline-job-{job.guid}-secrets"
    kubernetes.server_side_apply(
        namespace=namespace,
        manifest={
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": secret_name,
                "namespace": namespace,
                "labels": {"astrolift.dev/pipeline-job-run": str(job.guid)},
            },
            "stringData": {"test": "disposable-value"},
        },
        dry_run=False,
    )
    step = Step.objects.create(job=job.job, step_id="wait", position=0, run="sleep 300")
    step_run = StepRun.objects.create(job_run=job, step=step, status="running")
    activities._cancel_pipeline_job_sync(job.pk)
    job.refresh_from_db()
    step_run.refresh_from_db()
    assert job.cleanup_status == "complete"
    assert step_run.status == "cancelled" and step_run.finished_at is not None
    assert kubernetes.get(kind="Secret", namespace=namespace, name=secret_name) is None


def test_real_changed_job_uid_is_never_deleted_or_reported_success(cleanup_world):
    org, cluster, run, job, namespace, kubernetes = cleanup_world
    job.k8s_job_uid = "00000000-0000-0000-0000-000000000000"
    job.save()
    assert activities._poll_pipeline_job_sync(job.pk) == {
        "completed": False,
        "failed": False,
        "exit_code": None,
    }
    activities._cancel_pipeline_job_sync(job.pk)
    job.refresh_from_db()
    assert job.cleanup_status == "failed"
    assert kubernetes.get(kind="Job", namespace=namespace, name=job.temporal_activity_id) is not None


def test_real_secret_with_changed_owner_is_preserved_and_cleanup_fails(cleanup_world):
    org, cluster, run, job, namespace, kubernetes = cleanup_world
    secret_name = f"pipeline-job-{job.guid}-secrets"
    kubernetes.server_side_apply(
        namespace=namespace,
        manifest={
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": secret_name,
                "namespace": namespace,
                "labels": {"astrolift.dev/pipeline-job-run": "another-execution"},
            },
            "stringData": {"test": "disposable-value"},
        },
        dry_run=False,
    )
    activities._cancel_pipeline_job_sync(job.pk)
    job.refresh_from_db()
    assert job.cleanup_status == "failed"
    assert kubernetes.get(kind="Secret", namespace=namespace, name=secret_name) is not None


def test_real_rbac_delete_denial_stays_failed(cleanup_world, owned_kubernetes, tmp_path):
    org, cluster, run, job, namespace, kubernetes = cleanup_world
    path, context, _ = owned_kubernetes
    documents = [
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": "cleanup-reader", "namespace": namespace},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {"name": "cleanup-reader", "namespace": namespace},
            "rules": [
                {
                    "apiGroups": ["", "batch"],
                    "resources": ["jobs", "pods", "secrets"],
                    "verbs": ["get", "list"],
                }
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {"name": "cleanup-reader", "namespace": namespace},
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "cleanup-reader"},
            "subjects": [{"kind": "ServiceAccount", "name": "cleanup-reader", "namespace": namespace}],
        },
    ]
    for document in documents:
        kubernetes.server_side_apply(namespace=namespace, manifest=document, dry_run=False)
    configuration = k8s_client.Configuration()
    config.load_kube_config(config_file=path, context=context, client_configuration=configuration)
    with k8s_client.ApiClient(configuration) as api:
        token = (
            k8s_client.CoreV1Api(api)
            .create_namespaced_service_account_token(
                "cleanup-reader",
                namespace,
                k8s_client.AuthenticationV1TokenRequest(
                    spec=k8s_client.V1TokenRequestSpec(
                        audiences=["https://kubernetes.default.svc.cluster.local"], expiration_seconds=600
                    )
                ),
            )
            .status.token
        )
    with open(path) as stream:
        restricted = yaml.safe_load(stream)
    user_name = restricted["contexts"][0]["context"]["user"]
    restricted["users"] = [{"name": user_name, "user": {"token": token}}]
    restricted_path = tmp_path / "restricted-kubeconfig.yaml"
    restricted_path.write_text(yaml.safe_dump(restricted))
    restricted_path.chmod(0o600)
    cluster.provider_config = {"kubeconfig_path": str(restricted_path), "context": context}
    cluster.save()
    activities._cancel_pipeline_job_sync(job.pk)
    job.refresh_from_db()
    assert job.cleanup_status == "failed"
    assert kubernetes.get(kind="Job", namespace=namespace, name=job.temporal_activity_id) is not None


def test_real_cleanup_stays_on_recorded_cluster_after_selector_changes(cleanup_world):
    org, cluster, run, job, namespace, kubernetes = cleanup_world
    path = os.environ.get("ASTROLIFT_PIPELINE_TEST_SECOND_KUBECONFIG")
    if not path:
        pytest.skip("requires second task-owned local Kind kubeconfig")
    with open(path) as stream:
        document = yaml.safe_load(stream)
    context = "kind-astrolift-env-target-2217-b"
    assert document["current-context"] == context
    from k8s_native.cluster import _build_k8s_client

    neighbor = _build_k8s_client(kubeconfig_path=path, context=context, in_cluster=False)
    other = TenantCluster.objects.create(
        organization=org,
        provider_plugin=cluster.provider_plugin,
        name="Other disposable cluster",
        slug=f"neighbor-{org.slug}",
        lifecycle="managed",
        provider_config={"kubeconfig_path": path, "context": context},
    )
    try:
        neighbor.server_side_apply(
            namespace=None,
            manifest={"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
            dry_run=False,
        )
        neighbor.server_side_apply(
            namespace=namespace,
            manifest={
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {
                    "name": job.temporal_activity_id,
                    "namespace": namespace,
                    "labels": {"astrolift.io/pipeline-run-id": str(run.pk)},
                },
                "spec": {
                    "template": {
                        "spec": {
                            "restartPolicy": "Never",
                            "containers": [{"name": "hold", "image": "registry.k8s.io/pause:3.10"}],
                        }
                    }
                },
            },
            dry_run=False,
        )
        neighboring_uid = neighbor.get(kind="Job", namespace=namespace, name=job.temporal_activity_id)[
            "metadata"
        ]["uid"]
        job.job.runs_on = f"cluster:{other.slug}"
        job.job.save()
        activities._cancel_pipeline_job_sync(job.pk)
        job.refresh_from_db()
        assert job.cluster_id == cluster.pk and job.cleanup_status == "complete"
        assert kubernetes.get(kind="Job", namespace=namespace, name=job.temporal_activity_id) is None
        assert (
            neighbor.get(kind="Job", namespace=namespace, name=job.temporal_activity_id)["metadata"]["uid"]
            == neighboring_uid
        )
    finally:
        neighbor.delete(kind="Namespace", namespace=None, name=namespace)
        neighbor._api_client.close()
