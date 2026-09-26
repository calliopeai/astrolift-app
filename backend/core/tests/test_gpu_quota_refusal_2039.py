"""Deploy refuses GPU workloads past the org's GPU quota (#2039)."""

from __future__ import annotations

import pytest

from astrolift_billing.models import Quota
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_services.models import ManagedService
from core.app_deploy import gpu_quota_refusal
from core.tests.utils.scope_world import ScopeWorld, make_cluster

pytestmark = pytest.mark.django_db


def _toml(gpu: int, replicas: int = 1) -> str:
    return (
        'name = "llm"\n\n[[workloads]]\nname = "llm"\nkind = "deployment"\n'
        f"gpu = {gpu}\nreplicas = {replicas}\n\n"
        '  [[workloads.containers]]\n  name = "app"\n  is_primary = true\n  port = 8000\n'
    )


def _manifest(raw: str):
    return normalize(parse_raw(raw), defaults=NormalizationDefaults())


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def world():
    w = ScopeWorld("gq2039")
    w.cluster = make_cluster(w, "gq2039")
    w.medops_app.manifest_raw = _toml(gpu=2, replicas=2)
    w.medops_app.save(update_fields=["manifest_raw"])
    w.prod = AppEnvironment.objects.create(registered_app=w.medops_app, tenant_cluster=w.cluster, name="prod")
    w.staging = AppEnvironment.objects.create(
        registered_app=w.medops_app, tenant_cluster=w.cluster, name="staging"
    )
    Deployment.objects.create(
        registered_app=w.medops_app, app_environment=w.prod, image_tag="v1", status=Deployment.Status.RUNNING
    )
    w.deploy = Deployment.objects.create(
        registered_app=w.medops_app, app_environment=w.staging, image_tag="v2"
    )
    return w


def _quota(org, limit: int) -> None:
    Quota.objects.create(
        organization=org,
        scope_kind=Quota.ScopeKind.ORG,
        scope_id=org.id,
        resource=Quota.Resource.GPU,
        hard_limit=limit,
        soft_limit=limit,
    )


def test_no_quota_means_no_limit(world):
    assert gpu_quota_refusal(world.deploy, _manifest(_toml(gpu=8))) is None


def test_running_deploys_count_against_the_quota(world):
    _quota(world.org, 6)
    # prod runs 2 GPUs x 2 replicas; staging asks for 2 more: 6 fits, 7 does not.
    assert gpu_quota_refusal(world.deploy, _manifest(_toml(gpu=2))) is None
    refusal = gpu_quota_refusal(world.deploy, _manifest(_toml(gpu=3)))
    assert refusal and "already runs 4 of its 6-GPU quota" in refusal


def test_redeploying_an_env_does_not_count_what_it_replaces(world):
    _quota(world.org, 4)
    prod = Deployment.objects.create(
        registered_app=world.medops_app, app_environment=world.prod, image_tag="v3"
    )
    assert gpu_quota_refusal(prod, _manifest(_toml(gpu=2, replicas=2))) is None


def test_vllm_models_count_and_cpu_deploys_are_never_refused(world):
    _quota(world.org, 5)
    ManagedService.objects.create(
        registered_app=world.medops_app,
        app_environment=world.prod,
        kind=ManagedService.Kind.MODEL_ENDPOINT,
        variant="vllm",
        name="qwen",
        config={"model": "Qwen/Qwen3-8B", "gpu": 1},
    )
    assert "already runs 5" in gpu_quota_refusal(world.deploy, _manifest(_toml(gpu=1)))
    assert gpu_quota_refusal(world.deploy, _manifest(_toml(gpu=0))) is None


def test_another_orgs_gpus_do_not_count(world):
    other = ScopeWorld("gq2039b")
    other.medops_app.manifest_raw = _toml(gpu=8)
    other.medops_app.save(update_fields=["manifest_raw"])
    env = AppEnvironment.objects.create(
        registered_app=other.medops_app, tenant_cluster=world.cluster, name="prod"
    )
    Deployment.objects.create(
        registered_app=other.medops_app, app_environment=env, image_tag="v1", status=Deployment.Status.RUNNING
    )
    _quota(world.org, 6)
    assert gpu_quota_refusal(world.deploy, _manifest(_toml(gpu=2))) is None


def test_an_autoscaled_workload_counts_at_its_ceiling(world):
    _quota(world.org, 8)
    raw = _toml(gpu=1).replace("replicas = 1\n", "replicas = 1\nhpa_min = 1\nhpa_max = 5\n")
    # prod holds 4; this one can scale to 5.
    assert "needs 5 GPU(s)" in gpu_quota_refusal(world.deploy, _manifest(raw))
