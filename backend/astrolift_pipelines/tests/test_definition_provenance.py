"""A run records the definition it executed (#65, spec requirement 5).

`trigger_ref` answers "what fired this run". It does not answer "what did
this run execute", because a branch moves: the same ref fetched a minute
later is a different document. Without a digest, "why did this run behave
differently from that one" is unanswerable after the fact, and it becomes
unanswerable across N files the moment the DSL grows includes.

There is a live warning against skipping this. `Deployment.config_snapshot`
was meant to be the same kind of record for app deploys; nothing ever writes
it, only copies a prior row's forward, so it is `{}` forever -- which is
precisely why `astrolift_lifecycle/drift.py` cannot be wired at all. A
provenance field with no producer is worse than no field, because the
feature that depends on it looks implemented.
"""

from __future__ import annotations

import hashlib
import itertools

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Pipeline, PipelineRun

pytestmark = pytest.mark.django_db

_n = itertools.count(1)

_TOML = """
schema_version = 1
name = "ci"

[[jobs.build.steps]]
name = "compile"
run = "make"
"""


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-prov-{next(_n)}")


def _run(org):
    pipeline = Pipeline.objects.create(
        organization=org, name=f"ci-{next(_n)}", repo_url="https://github.com/a/b"
    )
    return PipelineRun.objects.create(
        pipeline=pipeline, run_number=next(_n), trigger_ref="main", trigger_kind="push"
    )


def _drive(run, monkeypatch, toml_text=_TOML):
    """Run the activity's sync body with the fetch stubbed at the seam."""
    from astrolift_workflows.activities import pipeline_job_spawn

    monkeypatch.setattr("astrolift_pipelines.toml_fetcher.fetch_pipeline_toml", lambda _run: toml_text)
    return pipeline_job_spawn._mark_pipeline_run_running_sync(run.pk)


def test_a_run_records_the_digest_of_what_it_executed(org, monkeypatch):
    run = _run(org)

    _drive(run, monkeypatch)

    run.refresh_from_db()
    assert run.definition_digest == hashlib.sha256(_TOML.encode("utf-8")).hexdigest()


def test_a_run_records_the_dialect_it_read(org, monkeypatch):
    run = _run(org)

    _drive(run, monkeypatch)

    run.refresh_from_db()
    assert run.definition_schema_version == 1


def test_two_runs_of_a_changed_definition_carry_different_digests(org, monkeypatch):
    """The whole point: the ref is the same, the document is not."""
    first = _run(org)
    _drive(first, monkeypatch)

    changed = _TOML.replace('run = "make"', 'run = "make release"')
    second = _run(org)
    _drive(second, monkeypatch, toml_text=changed)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.trigger_ref == second.trigger_ref == "main"
    assert first.definition_digest != second.definition_digest


def test_an_unversioned_document_records_the_implied_version(org, monkeypatch):
    """A pre-versioning file in a repo still yields interpretable provenance
    rather than a zero that reads as "no document"."""
    run = _run(org)

    _drive(run, monkeypatch, toml_text=_TOML.replace("schema_version = 1\n", ""))

    run.refresh_from_db()
    assert run.definition_schema_version == 1
    assert run.definition_digest


def test_a_run_whose_definition_could_not_be_read_records_no_digest(org, monkeypatch):
    """A run must not claim a definition it failed to persist -- that would
    be provenance pointing at something that never executed."""
    run = _run(org)

    outcome = _drive(run, monkeypatch, toml_text="schema_version = 99\nname = 'ci'\n")

    assert outcome["jobs"] == []
    assert outcome["error"]
    run.refresh_from_db()
    assert run.definition_digest == ""
    assert run.definition_schema_version == 0


def test_the_default_is_distinguishable_from_a_recorded_value(org):
    """An API-defined pipeline has no TOML behind it. That is a real state,
    and it must not look like "we forgot to record"."""
    run = _run(org)

    assert run.definition_digest == ""
    assert run.definition_schema_version == 0
