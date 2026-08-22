"""Trigger filters actually filter, and a fork does not get org secrets.

`trigger_filters.py` had no caller. The live filter in `webhook_views`
did exact-string membership on branch names, ignored `paths` entirely,
and never looked at whether a pull request came from a fork.

Two defects, one of them a security defect:

* `branches = ["release/*"]` matched nothing, so those pipelines looked
  broken for no visible reason, and a docs-only push triggered every
  pipeline in the org.
* A fork's PR was handed the org's secrets, because nothing decided
  otherwise.
"""

from __future__ import annotations

import itertools

import pytest

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Pipeline, PipelineRun, Trigger
from astrolift_pipelines.webhook_views import _trigger_matches

pytestmark = pytest.mark.django_db

_n = itertools.count(1)


@pytest.fixture
def pipeline():
    org = Organization.objects.create(name="Acme", slug=f"acme-tf-{next(_n)}")
    return Pipeline.objects.create(organization=org, name="ci", repo_url="https://github.com/a/b")


def _trigger(pipeline, kind="push", **config):
    return Trigger.objects.create(pipeline=pipeline, kind=kind, config=config)


def _push(files=None):
    commits = [{"modified": list(files or [])}] if files is not None else []
    return {"commits": commits}


def _pr(*, fork: bool, base: str = "main"):
    return {
        "pull_request": {
            "head": {"repo": {"fork": fork}},
            "base": {"ref": base},
        }
    }


# ---- the glob defect -----------------------------------------------------


def test_a_glob_branch_pattern_now_matches(pipeline):
    """The headline: `release/*` matched nothing under exact membership, so
    the pipeline never fired and looked broken with no reason given."""
    t = _trigger(pipeline, branches=["release/*"])

    assert _trigger_matches(t, "push", "refs/heads/release/2.1", _push()).should_trigger


def test_a_branch_outside_the_pattern_still_does_not_match(pipeline):
    t = _trigger(pipeline, branches=["release/*"])

    assert not _trigger_matches(t, "push", "refs/heads/main", _push()).should_trigger


def test_an_exact_branch_name_keeps_working(pipeline):
    """The old behaviour is a subset of the new one; existing configs must
    not change meaning."""
    t = _trigger(pipeline, branches=["main"])

    assert _trigger_matches(t, "push", "refs/heads/main", _push()).should_trigger
    assert not _trigger_matches(t, "push", "refs/heads/other", _push()).should_trigger


def test_a_trigger_for_another_event_is_not_this_event(pipeline):
    """The kind gate is a property of the row, not the config, so it stays
    at the call site rather than moving into the filter module."""
    t = _trigger(pipeline, kind="pull_request", branches=["main"])

    assert _trigger_matches(t, "push", "refs/heads/main", _push()) is None


# ---- the security defect -------------------------------------------------


def test_a_fork_pull_request_is_marked_to_skip_secrets(pipeline):
    t = _trigger(pipeline, kind="pull_request", branches=["main"])

    result = _trigger_matches(t, "pull_request", "refs/heads/feature", _pr(fork=True))

    assert result.should_trigger
    assert result.is_fork
    assert result.skip_secrets


def test_a_pull_request_from_the_same_repo_keeps_its_secrets(pipeline):
    t = _trigger(pipeline, kind="pull_request", branches=["main"])

    result = _trigger_matches(t, "pull_request", "refs/heads/feature", _pr(fork=False))

    assert result.should_trigger
    assert not result.skip_secrets


def test_the_spawn_activity_withholds_secrets_when_the_run_says_so(pipeline, monkeypatch):
    """The decision is only useful if the thing that mounts secrets reads it.

    Recorded on the run rather than recomputed, because the payload is gone
    by the time a job spawns and a security answer must not be able to come
    out differently the second time.
    """
    from astrolift_pipelines.job_sync import sync_definition
    from astrolift_workflows.activities import pipeline_job_spawn as mod

    run = PipelineRun.objects.create(
        pipeline=pipeline, run_number=next(_n), trigger_ref="main", skip_secrets=True
    )
    from astrolift_ci_convert.common.toml_reader import read_toml

    sync_definition(
        run,
        read_toml('name = "ci"\n[jobs.build]\n[[jobs.build.steps]]\nrun = "echo ${secrets.TOKEN}"\n'),
    )

    resolved = []
    monkeypatch.setattr(
        "astrolift_pipelines.secret_plumbing.resolve_pipeline_secrets",
        lambda pr, names: resolved.append(names) or {},
    )
    monkeypatch.setattr(
        mod, "_get_cluster_client", lambda r: (_ for _ in ()).throw(RuntimeError("stop here"))
    )

    with pytest.raises(RuntimeError):
        mod._spawn_pipeline_job_sync(run.pk, "build")

    assert resolved == [], "a fork run must not resolve org secrets at all"
