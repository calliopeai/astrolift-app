"""Cross-tenant isolation for the self-hosted runner protocol (#1183).

Two fixes are covered:

* ``_find_matching_job_run`` (the body of the ``runner_claim_job`` view)
  now constrains the pending-job search to the runner's own org, so a
  runner can never claim — and receive the config/secrets of — a job
  owned by another org.

* ``runner_register`` used to *skip* its auth check entirely when
  ``RUNNER_REGISTRATION_TOKEN`` was unset, which let any caller register
  a runner (and mint an API key) under any org's slug. It now fails
  closed: no token configured → registration refused (403).
"""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Job, JobRun, Pipeline, PipelineRun, Runner
from astrolift_pipelines.runner_views import _find_matching_job_run, runner_register

pytestmark = pytest.mark.django_db


@pytest.fixture
def org_a():
    return Organization.objects.create(name="Pipe Org A", slug="pipe-org-a")


@pytest.fixture
def org_b():
    return Organization.objects.create(name="Pipe Org B", slug="pipe-org-b")


def _runner(org):
    return Runner.objects.create(
        organization=org,
        name=f"runner-{org.slug}",
        os=Runner.Os.LINUX,
        arch=Runner.Arch.AMD64,
        status=Runner.Status.IDLE,
    )


def _pending_job_run(org, *, runs_on="astrolift/default"):
    """A PENDING JobRun in ``org`` whose job matches any runner."""
    pipeline = Pipeline.objects.create(
        organization=org,
        name=f"pipe-{org.slug}",
        repo_url="https://github.com/acme/app",
        default_branch="main",
    )
    prun = PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=1,
        trigger_kind=PipelineRun.TriggerKind.PUSH,
        trigger_ref="refs/heads/main",
        trigger_actor="bot",
        status=PipelineRun.Status.PENDING,
    )
    job = Job.objects.create(pipeline=pipeline, job_id="build", name="Build", runs_on=runs_on)
    return JobRun.objects.create(pipeline_run=prun, job=job, status=JobRun.Status.PENDING)


# ---------------------------------------------------------------------------
# _find_matching_job_run — a runner claims only its own org's jobs
# ---------------------------------------------------------------------------


def test_runner_claims_only_own_org_job(org_a, org_b):
    """Both orgs have a matching pending job; the org-A runner matches
    org-A's job and never org-B's."""
    runner_a = _runner(org_a)
    jr_a = _pending_job_run(org_a)
    jr_b = _pending_job_run(org_b)

    match = _find_matching_job_run(runner_a)
    assert match is not None
    assert match.pk == jr_a.pk
    assert match.pk != jr_b.pk


def test_runner_ignores_foreign_org_pending_job(org_a, org_b):
    """Only org B has a pending job; the org-A runner finds nothing —
    the foreign job is invisible, not merely deprioritised."""
    runner_a = _runner(org_a)
    _pending_job_run(org_b)

    assert _find_matching_job_run(runner_a) is None


# ---------------------------------------------------------------------------
# runner_register — fail closed when no registration token is configured
# ---------------------------------------------------------------------------


def _register(body, *, auth=None):
    rf = RequestFactory()
    extra = {"HTTP_AUTHORIZATION": auth} if auth is not None else {}
    req = rf.post(
        "/api/pipelines/v1/runners/register/",
        data=json.dumps(body),
        content_type="application/json",
        **extra,
    )
    return runner_register(req)


def test_register_refused_when_token_unset(settings, org_a):
    """No ``RUNNER_REGISTRATION_TOKEN`` configured → 403, and no runner
    is created (previously this path skipped auth and registered)."""
    settings.RUNNER_REGISTRATION_TOKEN = None
    resp = _register({"org_slug": org_a.slug, "name": "r1"})
    assert resp.status_code == 403
    assert Runner.objects.filter(organization=org_a).count() == 0


def test_register_rejects_wrong_token_when_set(settings, org_a):
    """Token configured but the caller presents the wrong one → 401."""
    settings.RUNNER_REGISTRATION_TOKEN = "s3cret"
    resp = _register({"org_slug": org_a.slug, "name": "r1"}, auth="Bearer nope")
    assert resp.status_code == 401
    assert Runner.objects.filter(organization=org_a).count() == 0


def test_register_succeeds_with_correct_token(settings, org_a):
    """Token configured and matched → 201, runner minted for the org."""
    settings.RUNNER_REGISTRATION_TOKEN = "s3cret"
    resp = _register({"org_slug": org_a.slug, "name": "r1"}, auth="Bearer s3cret")
    assert resp.status_code == 201
    assert Runner.objects.filter(organization=org_a, name="r1").count() == 1


# --- #1188: runner_claim_job calls runner.is_available() -------------------
# The method was referenced by the claim view but defined nowhere, so every
# job-claim raised AttributeError at runtime. Pin the contract: it exists and
# is True only for an IDLE (claimable) runner.
@pytest.mark.parametrize(
    "status,expected",
    [
        (Runner.Status.IDLE, True),
        (Runner.Status.ACTIVE, False),
        (Runner.Status.OFFLINE, False),
        (Runner.Status.SUSPENDED, False),
    ],
)
def test_runner_is_available_only_when_idle(org_a, status, expected):
    runner = _runner(org_a)
    runner.status = status
    assert runner.is_available() is expected
