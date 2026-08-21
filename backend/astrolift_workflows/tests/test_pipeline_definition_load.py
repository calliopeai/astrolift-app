"""The run loads the definition it is a run of (#1531).

`fetch_pipeline_toml` had no callers, there was no parser to hand its
output to, and nothing wrote a `Job` row. `_mark_pipeline_run_running_sync`
queried for jobs, got an empty list every time, and the workflow marked the
run SUCCESS for having nothing to do.

These cover the join: the activity fetches, parses and persists, and a
definition that cannot be read fails the run instead of passing it.
"""

from __future__ import annotations

import itertools

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Pipeline, PipelineRun

pytestmark = pytest.mark.django_db

TOML = (
    'name = "ci"\n'
    "[jobs.build]\n"
    '[[jobs.build.steps]]\nrun = "make"\n'
    '[jobs.test]\nneeds = ["build"]\n'
    '[[jobs.test.steps]]\nrun = "pytest"\n'
)

_run_numbers = itertools.count(1)


@pytest.fixture
def run():
    org = Organization.objects.create(name="Acme", slug="acme-defload")
    pipeline = Pipeline.objects.create(organization=org, name="ci", repo_url="https://github.com/a/b")
    return PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=next(_run_numbers),
        commit_sha="c0ffee",
        trigger_ref="main",
    )


def _load(monkeypatch, *, toml: str = TOML, raises: Exception | None = None):
    from astrolift_workflows.activities import pipeline_job_spawn as mod

    def fake_fetch(pipeline_run):
        if raises is not None:
            raise raises
        return toml

    monkeypatch.setattr("astrolift_pipelines.toml_fetcher.fetch_pipeline_toml", fake_fetch)
    return mod


def test_the_definition_becomes_jobs(run, monkeypatch):
    mod = _load(monkeypatch)

    result = mod._mark_pipeline_run_running_sync(run.pk)

    assert sorted(j["job_id"] for j in result["jobs"]) == ["build", "test"]
    assert "error" not in result
    run.refresh_from_db()
    assert run.status == PipelineRun.Status.RUNNING


def test_needs_survives_into_the_fan_out(run, monkeypatch):
    """The workflow topologically sorts on this; an empty `needs` would
    run every job at once regardless of what the TOML declared."""
    mod = _load(monkeypatch)

    jobs = {j["job_id"]: j for j in mod._mark_pipeline_run_running_sync(run.pk)["jobs"]}

    assert jobs["test"]["needs"] == ["build"]


def test_an_unfetchable_definition_fails_the_run(run, monkeypatch):
    """Not an empty job list. An empty list is 'nothing to do' and the
    workflow reports it green, which is the bug this whole issue is."""
    from astrolift_pipelines.toml_fetcher import TomlFetchError

    mod = _load(monkeypatch, raises=TomlFetchError("no credentials for repo"))

    result = mod._mark_pipeline_run_running_sync(run.pk)

    assert result["jobs"] == []
    assert "no credentials for repo" in result["error"]


def test_an_unparseable_definition_fails_the_run(run, monkeypatch):
    mod = _load(monkeypatch, toml='name = "ci"\n[[jobs.build.steps]]\nname = "neither"\n')

    result = mod._mark_pipeline_run_running_sync(run.pk)

    assert result["jobs"] == []
    # The path, so the operator knows which step to fix.
    assert "jobs.build.steps[0]" in result["error"]


def test_the_workflow_fails_rather_than_passing_on_that_error():
    """The activity reporting an error is only half of it — the workflow
    has to act on it, and its next branch is `if not jobs: success`."""
    import inspect

    from astrolift_workflows.workflows import pipeline_run as wf

    source = inspect.getsource(wf.PipelineRunWorkflow.run)
    error_at = source.index('run_meta.get("error")')
    empty_at = source.index("if not jobs:")
    assert error_at < empty_at, "the error check must come before the empty-jobs shortcut"


def test_a_second_run_resolves_its_own_job(run, monkeypatch):
    """Under snapshot both runs hold a row with job_id 'build'. The spawn
    activity used to look these up by pipeline, which now finds two."""
    from astrolift_pipelines.job_sync import job_for_run

    mod = _load(monkeypatch)
    mod._mark_pipeline_run_running_sync(run.pk)

    second = PipelineRun.objects.create(
        pipeline=run.pipeline,
        run_number=next(_run_numbers),
        commit_sha="deadbe",
        trigger_ref="main",
    )
    mod._mark_pipeline_run_running_sync(second.pk)

    assert job_for_run(run, "build").pipeline_run_id == run.pk
    assert job_for_run(second, "build").pipeline_run_id == second.pk


def test_the_fetch_is_at_the_commit_not_the_branch(run, monkeypatch):
    """A ref is a name that moves. Fetching 'main' resolves to whatever
    main points at when the fetch runs, not to the code that triggered."""
    seen = {}

    def fake_github(pipeline, ref, path):
        seen["ref"] = ref
        return TOML

    monkeypatch.setattr("astrolift_pipelines.toml_fetcher._fetch_from_github", fake_github)

    from astrolift_pipelines.toml_fetcher import fetch_pipeline_toml

    fetch_pipeline_toml(run)

    assert seen["ref"] == "c0ffee"


def test_a_run_without_a_commit_still_falls_back_to_the_ref(run, monkeypatch):
    seen = {}

    def fake_github(pipeline, ref, path):
        seen["ref"] = ref
        return TOML

    monkeypatch.setattr("astrolift_pipelines.toml_fetcher._fetch_from_github", fake_github)
    run.commit_sha = ""
    run.save(update_fields=["commit_sha", "updated_at", "version"])

    from astrolift_pipelines.toml_fetcher import fetch_pipeline_toml

    fetch_pipeline_toml(run)

    assert seen["ref"] == "main"
