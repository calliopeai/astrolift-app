"""A pipeline job lands where its `runs_on` says (#82).

`dispatch_router.py` had no caller. `_resolve_cluster` took the org's
oldest managed cluster and ignored `runs_on` entirely, so
`cluster:prod-eu`, `["linux","arm64"]`, `windows` and `self-hosted` all
landed on the same place -- an arm64 job onto amd64 nodes, and a
self-hosted job as a K8s Job on the platform's own cluster.

It also could not run at all. It filtered on `lifecycle_state`, which is
not a field on `TenantCluster` (the column is `lifecycle`), so every call
raised FieldError. Every existing test of this path patches
`_resolve_cluster` or `_get_cluster_client` out, which is exactly why
nobody noticed: the real query was never executed by a test.

So the first test here deliberately does NOT patch it.
"""

from __future__ import annotations

import itertools

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization
from astrolift_pipelines.models import Job, Pipeline, PipelineRun
from astrolift_workflows.activities.pipeline_job_spawn import _resolve_cluster

pytestmark = pytest.mark.django_db

_n = itertools.count(1)


@pytest.fixture
def plugin():
    # bulk_create rather than create: BaseCoreModel.save writes a numeric
    # `version`, which collides with this model's CharField of the same name.
    # ignore_conflicts returns rows without primary keys, so re-read it.
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                plugin_version="1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    return ProviderPlugin.objects.get(slug="aws")


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-route-{next(_n)}")


def _cluster(org, plugin, slug, **kw):
    return TenantCluster.objects.create(
        organization=org,
        slug=slug,
        name=slug,
        provider_plugin=plugin,
        endpoint="https://eks.example.com",
        is_active=kw.pop("is_active", True),
        lifecycle=kw.pop("lifecycle", TenantCluster.Lifecycle.MANAGED.value),
        **kw,
    )


def _run(org, plugin, **job_kw):
    pipeline = Pipeline.objects.create(organization=org, name="ci", repo_url="https://github.com/a/b")
    run = PipelineRun.objects.create(pipeline=pipeline, run_number=next(_n), trigger_ref="main")
    job = Job.objects.create(pipeline=pipeline, job_id="build", name="Build", **job_kw)
    return run, job


def test_the_query_actually_executes(org, plugin):
    """The regression that hid behind every mock.

    `lifecycle_state` is not a field on TenantCluster, so the old
    implementation raised FieldError here on every single call. Nothing
    else in this file matters if this one does not run the real query.
    """
    default = _cluster(org, plugin, "default-a")
    run, job = _run(org, plugin, runs_on="astrolift/default")

    assert _resolve_cluster(run, job).pk == default.pk


def test_a_cluster_directive_picks_that_cluster(org, plugin):
    _cluster(org, plugin, "default-b")
    target = _cluster(org, plugin, "prod-eu")
    run, job = _run(org, plugin, runs_on="cluster:prod-eu")

    assert _resolve_cluster(run, job).pk == target.pk


def test_an_arch_selector_will_not_take_a_mismatched_cluster(org, plugin):
    """The silently-wrong case: an arm64 job used to be scheduled onto
    whatever the org's oldest cluster was, amd64 included."""
    _cluster(org, plugin, "amd-only", node_archs=["amd64"])
    run, job = _run(org, plugin, runs_on="arm64")

    with pytest.raises(RuntimeError, match="no cluster matching"):
        _resolve_cluster(run, job)


def test_an_arch_selector_takes_the_matching_cluster(org, plugin):
    _cluster(org, plugin, "amd-c", node_archs=["amd64"])
    arm = _cluster(org, plugin, "arm-c", node_archs=["arm64"])
    run, job = _run(org, plugin, runs_on="arm64")

    assert _resolve_cluster(run, job).pk == arm.pk


def test_a_self_hosted_job_is_refused_rather_than_run_on_the_platform(org, plugin):
    """It belongs to a registered runner agent. Spawning it as a K8s Job
    would run the operator's self-hosted work on our cluster."""
    _cluster(org, plugin, "default-d")
    run, job = _run(org, plugin, runs_on="self-hosted")

    with pytest.raises(RuntimeError, match="self-hosted runner"):
        _resolve_cluster(run, job)


def test_an_unmanaged_cluster_is_not_a_candidate(org, plugin):
    _cluster(org, plugin, "pending-e", lifecycle="pending")
    run, job = _run(org, plugin, runs_on="astrolift/default")

    with pytest.raises(RuntimeError, match="no cluster matching"):
        _resolve_cluster(run, job)


def test_an_inactive_cluster_is_not_a_candidate(org, plugin):
    """The old predicate checked `deleted_at` and never `is_active`, so a
    deactivated cluster stayed eligible."""
    _cluster(org, plugin, "off-f", is_active=False)
    run, job = _run(org, plugin, runs_on="astrolift/default")

    with pytest.raises(RuntimeError, match="no cluster matching"):
        _resolve_cluster(run, job)


def test_without_a_job_it_resolves_the_org_default(org, plugin):
    """The log-capture path calls this after placement and has no selector
    of its own to apply."""
    default = _cluster(org, plugin, "default-g")
    run, _job = _run(org, plugin)

    assert _resolve_cluster(run).pk == default.pk
