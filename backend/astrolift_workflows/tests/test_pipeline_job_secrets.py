"""Pipeline jobs can be given secrets, and cannot leak them (#1529).

`secret_plumbing` was complete and tested and had no production caller:
`resolve_pipeline_secrets` took a name list nothing could construct,
because no model held a secret name and no parser read one. A job needing
a registry credential or a deploy key could not express it at all.

The names are derived from the `${secrets.NAME}` references the GHA
converter already emits, so a converted pipeline runs as converted.

The half that matters more is the other one. #1218 built log capture
first on purpose: capture without secrets is safe, secrets without
redaction are not, and the first mounted credential would otherwise land
in a stored excerpt that outlives the pod.
"""

from __future__ import annotations

import itertools

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Job, JobRun, Pipeline, PipelineRun, Step
from astrolift_pipelines.secret_plumbing import secret_names_for_job, secret_names_in

pytestmark = pytest.mark.django_db

_numbers = itertools.count(1)


# ---- which names a definition asks for ------------------------------------


def test_a_reference_in_a_script_is_a_request_for_that_secret():
    assert secret_names_in('echo "${secrets.REGISTRY_TOKEN}" | docker login') == ["REGISTRY_TOKEN"]


def test_the_converter_output_shape_is_recognised():
    # What gha/parser.py rewrites `${{ secrets.X }}` into.
    assert secret_names_in("${secrets.NPM_TOKEN}") == ["NPM_TOKEN"]


def test_names_are_deduplicated_and_ordered():
    assert secret_names_in("${secrets.B} ${secrets.A} ${secrets.B}") == ["A", "B"]


def test_an_env_lookalike_is_not_a_secret():
    # `${env.X}` is a plain variable; treating it as a secret would fail
    # the job on a name the org store was never meant to hold.
    assert secret_names_in("${env.REGISTRY} ${secrets.TOKEN}") == ["TOKEN"]


def test_references_are_found_in_env_and_with_not_only_run(pipeline):
    """A registry credential arrives through `with` as often as `run`."""
    job = Job.objects.create(pipeline=pipeline, job_id="build", name="Build")
    Step.objects.create(job=job, position=0, run="make", env={"T": "${secrets.FROM_ENV}"})
    Step.objects.create(job=job, position=1, uses="a/b", with_params={"token": "${secrets.FROM_WITH}"})

    steps = list(Step.objects.filter(job=job).order_by("position"))
    assert secret_names_for_job(job, steps) == ["FROM_ENV", "FROM_WITH"]


def test_a_job_referring_to_nothing_asks_for_nothing(pipeline):
    job = Job.objects.create(pipeline=pipeline, job_id="build", name="Build")
    Step.objects.create(job=job, position=0, run="make build")

    assert secret_names_for_job(job, [Step.objects.get(job=job)]) == []


# ---- the leak test the issue asks for -------------------------------------


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-secrets")


@pytest.fixture
def pipeline(org):
    return Pipeline.objects.create(organization=org, name="ci", repo_url="https://github.com/a/b")


@pytest.fixture
def job_run(pipeline):
    run = PipelineRun.objects.create(pipeline=pipeline, run_number=next(_numbers), trigger_ref="main")
    job = Job.objects.create(pipeline=pipeline, job_id="build", name="Build")
    Step.objects.create(job=job, position=0, run='echo "${secrets.TOKEN}"')
    return JobRun.objects.create(pipeline_run=run, job=job, status=JobRun.Status.RUNNING)


def _capture(monkeypatch, job_run, *, lines, resolved=None, raises=False):
    from astrolift_workflows.activities import pipeline_job_spawn as mod

    def fake_resolve(pipeline_run, names):
        if raises:
            raise RuntimeError("secret store unreachable")
        return resolved or {}

    monkeypatch.setattr("astrolift_pipelines.secret_plumbing.resolve_pipeline_secrets", fake_resolve)
    monkeypatch.setattr(mod, "_resolve_cluster", lambda run, job=None: object())
    monkeypatch.setattr(
        "core.cluster_observability.fetch_pod_log_tail",
        _async_returning(lines),
    )
    mod._capture_job_logs(
        job_run=job_run,
        run=job_run.pipeline_run,
        pod={"metadata": {"name": "p-1"}},
    )
    job_run.refresh_from_db()
    return job_run.log_excerpt


def _async_returning(value):
    async def _f(**kwargs):
        return value

    return _f


def test_a_secret_value_cannot_reach_the_stored_excerpt(job_run, monkeypatch):
    """The test item 5 of the issue asks for."""
    excerpt = _capture(
        monkeypatch,
        job_run,
        lines=["logging in with hunter2-the-real-token", "done"],
        resolved={"TOKEN": "hunter2-the-real-token"},
    )

    assert "hunter2-the-real-token" not in excerpt
    assert "***" in excerpt
    # Redaction, not deletion — the surrounding output is why anyone looks.
    assert "done" in excerpt


def test_an_unmaskable_secret_drops_the_excerpt_rather_than_storing_it(job_run, monkeypatch):
    """Fail closed. An excerpt we cannot mask is the leak this prevents."""
    excerpt = _capture(monkeypatch, job_run, lines=["logging in with hunter2"], raises=True)

    assert excerpt == ""


def test_a_job_without_secrets_still_gets_its_logs(pipeline, monkeypatch):
    """Fail-closed must not cost the ordinary case its diagnostics."""
    run = PipelineRun.objects.create(pipeline=pipeline, run_number=next(_numbers), trigger_ref="main")
    job = Job.objects.create(pipeline=pipeline, job_id="plain", name="Plain")
    Step.objects.create(job=job, position=0, run="make build")
    jr = JobRun.objects.create(pipeline_run=run, job=job, status=JobRun.Status.RUNNING)

    excerpt = _capture(monkeypatch, jr, lines=["compiling", "ok"], raises=True)

    assert "compiling" in excerpt


# ---- the secret never appears in the Job spec -----------------------------


def test_values_are_mounted_by_reference_not_inlined():
    """A literal in the spec is readable by anyone who can get the object,
    and lands in K8s audit events."""
    from astrolift_pipelines.secret_plumbing import make_env_from_refs
    from astrolift_workflows.activities.pipeline_job_spawn import _build_job_manifest

    class _J:
        container_image = "python:3.12"
        job_id = "build"

    class _R:
        pk = 7

    secret_env = make_env_from_refs("pipeline-job-abc-secrets", ["TOKEN"])
    manifest = _build_job_manifest("k-1", "ns", _J(), _R(), "echo hi", secret_env)

    env = manifest["spec"]["template"]["spec"]["containers"][0]["env"]
    entry = next(e for e in env if e["name"] == "TOKEN")
    assert entry["valueFrom"]["secretKeyRef"] == {
        "name": "pipeline-job-abc-secrets",
        "key": "TOKEN",
    }
    assert "value" not in entry


def test_the_manifest_is_unchanged_when_a_job_has_no_secrets():
    from astrolift_workflows.activities.pipeline_job_spawn import _build_job_manifest

    class _J:
        container_image = ""
        job_id = "build"

    class _R:
        pk = 7

    env = _build_job_manifest("k-1", "ns", _J(), _R(), "echo hi")["spec"]["template"]["spec"]["containers"][
        0
    ]["env"]

    assert [e["name"] for e in env] == ["ASTROLIFT_PIPELINE_RUN_ID", "ASTROLIFT_JOB_ID"]


# ---- the Secret does not outlive the pod ----------------------------------


def test_every_terminal_path_cleans_the_secret_up():
    """A per-run Secret left behind is a plaintext credential in a
    namespace the pod that needed it has already left. Three terminal
    paths reach that state: success, failure and cancellation — plus a
    spawn that dies after materializing, which poll never revisits."""
    import inspect

    from astrolift_workflows.activities import pipeline_job_spawn as mod

    for fn in (mod._poll_pipeline_job_sync, mod._cancel_pipeline_job_sync):
        source = inspect.getsource(fn)
        deletes = source.count("_delete_k8s_job(")
        cleanups = source.count("_cleanup_secrets_quietly(")
        assert cleanups == deletes, f"{fn.__name__}: {deletes} job deletes but {cleanups} secret cleanups"

    assert "_cleanup_secrets_quietly(" in inspect.getsource(mod._spawn_pipeline_job_sync)
