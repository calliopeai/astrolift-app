"""Which image a pipeline job runs on (#65).

`build_image.py` had no caller. `_build_job_manifest` fell back to bare
`ubuntu:22.04` — no git, no shell tooling, no astro CLI — so the first
checkout or build step of any job that omitted `container` failed, and
there was no platform-level or per-install way to change it.

The same job got a working image on a self-hosted runner and an unusable
one in-cluster: `runner_views` line 186 already defaulted to the builder
image. Wiring this makes the two paths agree.
"""

from __future__ import annotations

import itertools

import pytest
from django.test import override_settings

from astrolift_identity.models import Organization
from astrolift_pipelines.build_image import (
    _PLATFORM_DEFAULT_IMAGE,
    resolve_job_image,
    resolve_pull_secret_name,
)
from astrolift_pipelines.models import Job, Pipeline, PipelineRun

pytestmark = pytest.mark.django_db

_n = itertools.count(1)


@pytest.fixture
def run():
    org = Organization.objects.create(name="Acme", slug=f"acme-img-{next(_n)}")
    pipeline = Pipeline.objects.create(organization=org, name="ci", repo_url="https://github.com/a/b")
    return PipelineRun.objects.create(pipeline=pipeline, run_number=next(_n), trigger_ref="main")


def _job(run, **kw):
    return Job.objects.create(pipeline=run.pipeline, job_id="build", name="Build", **kw)


def test_what_the_job_asked_for_wins(run):
    job = _job(run, container_image="python:3.12")

    assert resolve_job_image(job, run) == "python:3.12"


def test_whitespace_around_a_declared_image_is_not_part_of_it(run):
    """An image name with a trailing space is an ImagePullBackOff that reads
    like a missing tag."""
    job = _job(run, container_image="  python:3.12  ")

    assert resolve_job_image(job, run) == "python:3.12"


@override_settings(PIPELINE_DEFAULT_IMAGE="ghcr.io/acme/builder:v2")
def test_the_install_can_set_its_own_default(run):
    assert resolve_job_image(_job(run), run) == "ghcr.io/acme/builder:v2"


@override_settings(PIPELINE_DEFAULT_IMAGE="ghcr.io/acme/builder:v2")
def test_the_install_default_does_not_override_the_job(run):
    job = _job(run, container_image="python:3.12")

    assert resolve_job_image(job, run) == "python:3.12"


def test_the_fallback_is_the_builder_not_bare_ubuntu(run):
    """The headline. `ubuntu:22.04` has no git and no shell tooling, so the
    first step of an otherwise valid pipeline failed."""
    resolved = resolve_job_image(_job(run), run)

    assert resolved == _PLATFORM_DEFAULT_IMAGE
    assert resolved != "ubuntu:22.04"


def test_the_manifest_uses_the_resolver(run):
    """The resolver passing its own tests proves nothing about whether the
    thing that builds the pod calls it."""
    from astrolift_workflows.activities.pipeline_job_spawn import _build_job_manifest

    job = _job(run)
    manifest = _build_job_manifest("k-1", "ns", job, run, "echo hi")

    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == _PLATFORM_DEFAULT_IMAGE


def test_the_k8s_path_and_the_runner_path_agree_on_the_default():
    """They disagreed: `runner_views` used the builder image while the K8s
    manifest used `ubuntu:22.04`, so where a job ran decided whether it
    could run at all."""
    from pathlib import Path

    runner = Path("astrolift_pipelines/runner_views.py").read_text()

    assert _PLATFORM_DEFAULT_IMAGE in runner


# ---- pull secret naming (issue #1874) -----------------------------------


def test_pull_secret_name_uses_full_guid_hex_for_uniqueness(run):
    """Two pipelines created back-to-back must produce different pull secret names.

    UUIDv7 prefixes contain only millisecond timestamps, so truncating to [:8]
    causes collisions. The fix uses the full 32-char hex. Regression test for #1874.
    """
    # Create two pipelines with registry credentials
    org = run.pipeline.organization
    org.extra_data = {"pipeline_registry_credentials": "secret-repo-token"}
    org.save()

    # Create two pipelines back-to-back
    pipeline1 = Pipeline.objects.create(organization=org, name="ci-1", repo_url="https://github.com/a/b")
    pipeline2 = Pipeline.objects.create(organization=org, name="ci-2", repo_url="https://github.com/c/d")

    run1 = PipelineRun.objects.create(pipeline=pipeline1, run_number=1, trigger_ref="main")
    run2 = PipelineRun.objects.create(pipeline=pipeline2, run_number=1, trigger_ref="main")

    name1 = resolve_pull_secret_name(run1)
    name2 = resolve_pull_secret_name(run2)

    # Different pipelines must produce different pull secret names
    assert name1 is not None
    assert name2 is not None
    assert name1 != name2
    # Verify the full guid hex is used (32 chars for guid, plus prefix)
    assert len(name1) == len("pipeline-pull-") + 32


def test_pull_secret_name_returns_none_without_credentials(run):
    """When registry credentials are not configured, return None."""
    secret_name = resolve_pull_secret_name(run)

    assert secret_name is None
