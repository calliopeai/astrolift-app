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
