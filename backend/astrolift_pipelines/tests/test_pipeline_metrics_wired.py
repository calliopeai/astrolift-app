"""The pipeline Prometheus series have producers (#98).

`astrolift_pipelines/metrics.py` declared five helpers -- run completions,
job durations, the pending gauge, webhook deliveries, webhook signature
failures -- registered on `prometheus_client`'s default registry, which is
the registry `config.views.metrics_view` serves through `generate_latest()`.
Nothing ever called any of them, so `/metrics` carried **zero** pipeline
series. The signature-failure counter is the sharpest example: it is the one
you would alert on, and it had no producer.

Two kinds of test here, deliberately.

The behavioural ones read the registry after driving a real transition,
because asserting "the helper was called" would pass just as happily for a
helper that records under the wrong labels.

The structural one is a ratchet. There are six job-run terminal paths and
three run-level ones, and the way this module went dark is that nothing
forced a new path to record. So it parses the source and asserts every
function that assigns a terminal status also records -- a test about the
shape of the code, because a behavioural test cannot cover a seventh path
that does not exist yet.
"""

from __future__ import annotations

import ast
import inspect
import itertools

import pytest
from prometheus_client import REGISTRY

from astrolift_identity.models import Organization
from astrolift_pipelines import metrics as pipeline_metrics
from astrolift_pipelines.models import Job, JobRun, Pipeline, PipelineRun
from astrolift_pipelines.tests import run_status_sites

pytestmark = pytest.mark.django_db

_n = itertools.count(1)


def _sample(name: str, **labels) -> float:
    """Current value of a metric sample, or 0.0 when it has never been set.

    Reads the live default registry rather than the module's private globals:
    that is the same path `/metrics` takes, so a metric registered on some
    other registry would fail here exactly as it would in production.
    """
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-metrics-{next(_n)}")


def _run(org, *, status=None):
    pipeline = Pipeline.objects.create(
        organization=org, name=f"ci-{next(_n)}", repo_url="https://github.com/a/b"
    )
    return PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=next(_n),
        trigger_ref="main",
        trigger_kind="push",
        status=status or PipelineRun.Status.RUNNING,
    )


# ---------------------------------------------------------------------------
# The series actually move
# ---------------------------------------------------------------------------


def test_a_finished_run_increments_the_run_counter(org):
    from django.utils import timezone

    run = _run(org)
    run.started_at = timezone.now()
    run.finished_at = timezone.now()
    run.status = PipelineRun.Status.SUCCESS
    run.save()

    labels = {
        "org": org.slug,
        "pipeline": run.pipeline.name,
        "status": "success",
        "trigger_kind": "push",
    }
    before = _sample("astrolift_pipeline_runs_total", **labels)
    pipeline_metrics.record_run_completed(run)
    after = _sample("astrolift_pipeline_runs_total", **labels)

    assert after == before + 1


def test_a_finished_run_observes_its_duration(org):
    from datetime import timedelta

    from django.utils import timezone

    run = _run(org)
    run.started_at = timezone.now() - timedelta(seconds=30)
    run.finished_at = timezone.now()
    run.status = PipelineRun.Status.SUCCESS
    run.save()

    labels = {"org": org.slug, "pipeline": run.pipeline.name, "status": "success"}
    before = _sample("astrolift_pipeline_run_duration_seconds_count", **labels)
    pipeline_metrics.record_run_completed(run)
    after = _sample("astrolift_pipeline_run_duration_seconds_count", **labels)

    assert after == before + 1


def test_a_run_with_no_timestamps_counts_but_does_not_observe(org):
    """A run that never started has no duration to report, and inventing one
    would poison the histogram."""
    run = _run(org, status=PipelineRun.Status.FAILURE)

    dur_labels = {"org": org.slug, "pipeline": run.pipeline.name, "status": "failure"}
    before = _sample("astrolift_pipeline_run_duration_seconds_count", **dur_labels)
    pipeline_metrics.record_run_completed(run)

    assert _sample("astrolift_pipeline_run_duration_seconds_count", **dur_labels) == before
    assert (
        _sample(
            "astrolift_pipeline_runs_total",
            org=org.slug,
            pipeline=run.pipeline.name,
            status="failure",
            trigger_kind="push",
        )
        >= 1
    )


def test_the_job_histogram_carries_the_backend_label(org):
    """The label exists because a job runs one of two ways; both must be
    distinguishable in the series."""
    from datetime import timedelta

    from django.utils import timezone

    run = _run(org)
    job = Job.objects.create(pipeline=run.pipeline, job_id="build", name="Build")
    job_run = JobRun.objects.create(
        pipeline_run=run,
        job=job,
        status=JobRun.Status.SUCCESS,
        started_at=timezone.now() - timedelta(seconds=5),
        finished_at=timezone.now(),
    )

    for backend in ("k8s_job", "self_hosted"):
        labels = {
            "org": org.slug,
            "pipeline": run.pipeline.name,
            "job_id": "build",
            "status": "success",
            "backend": backend,
        }
        before = _sample("astrolift_pipeline_job_duration_seconds_count", **labels)
        pipeline_metrics.record_job_completed(job_run, backend=backend)
        after = _sample("astrolift_pipeline_job_duration_seconds_count", **labels)
        assert after == before + 1, backend


def test_webhook_signature_failures_are_counted(org):
    before = _sample("astrolift_pipeline_webhook_signature_failures_total", org=org.slug, provider="github")
    pipeline_metrics.record_webhook_signature_failure(org.slug, "github")
    after = _sample("astrolift_pipeline_webhook_signature_failures_total", org=org.slug, provider="github")

    assert after == before + 1


# ---------------------------------------------------------------------------
# The ratchet: no terminal transition without a metric
# ---------------------------------------------------------------------------


# Only these two have metrics. StepRun deliberately does not: there is no
# per-step series in metrics.py, and an earlier version of this ratchet
# flagged _settle_step_runs for settling steps, which is not something to
# record. Naming the models keeps the guard honest in both directions.
_METERED_MODELS = {"JobRun", "PipelineRun"}

# Every wrapper that ends in a metric being recorded. Named rather than
# matched loosely because each module wraps the call its own way: the spawn
# activity has _record_job_metric / _record_run_metric, the cancel mutation
# has _record_cancelled_run.
_RECORDER_NAMES = frozenset(
    {
        "_record_job_metric",
        "_record_run_metric",
        "_record_cancelled_run",
        "record_job_completed",
        "record_run_completed",
    }
)


def _functions_assigning_terminal_status(module) -> dict[str, ast.FunctionDef]:
    """Every top-level function that sets a JobRun/PipelineRun terminal status."""
    tree = ast.parse(inspect.getsource(module))
    terminal = {"SUCCESS", "FAILURE", "CANCELLED"}
    out: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Assign):
                continue
            value = sub.value
            # match `<x>.status = JobRun.Status.FAILURE` exactly, including
            # which model's Status enum it came from.
            if (
                isinstance(value, ast.Attribute)
                and value.attr in terminal
                and isinstance(value.value, ast.Attribute)
                and value.value.attr == "Status"
                and isinstance(value.value.value, ast.Name)
                and value.value.value.id in _METERED_MODELS
            ):
                out[node.name] = node
                break
    return out


def _records_a_metric(fn: ast.FunctionDef) -> bool:
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Call):
            name = ""
            if isinstance(sub.func, ast.Name):
                name = sub.func.id
            elif isinstance(sub.func, ast.Attribute):
                name = sub.func.attr
            if name in {
                "_record_job_metric",
                "_record_run_metric",
                "record_job_completed",
                "record_run_completed",
            }:
                return True
    return False


def test_every_terminal_transition_in_the_spawn_activity_records():
    """The guard against the way this went dark.

    Six job-run terminal paths live in one module. A seventh added without a
    metric call is invisible to every behavioural test, so this asserts the
    property directly.
    """
    from astrolift_workflows.activities import pipeline_job_spawn

    found = _functions_assigning_terminal_status(pipeline_job_spawn)

    # A structural test whose matcher stops matching passes over an empty set
    # and proves nothing. Pin the count so a refactor that moves these
    # transitions fails here instead of going quiet.
    assert len(found) >= 5, (
        f"expected the terminal-transition helpers, found {sorted(found)}. "
        "If they moved, update this ratchet rather than deleting it."
    )

    offenders = [name for name, fn in found.items() if not _records_a_metric(fn)]
    assert not offenders, (
        "these functions settle a run or job to a terminal status without "
        f"recording a metric: {offenders}. Call _record_job_metric / "
        "_record_run_metric, or /metrics goes quiet for that path."
    )


def test_the_runner_completion_endpoint_records():
    """The self-hosted half, in a different module and easy to forget.

    Matched by name, not by the assignment pattern: this endpoint maps the
    request's status string through a dict and assigns the variable
    (``job_run.status = new_status``), so the AST shape the other ratchet
    looks for does not appear here at all. An earlier version of this test
    relied on that shape, found nothing, and passed vacuously -- it stayed
    green when the metric call was deleted.
    """
    from astrolift_pipelines import views

    tree = ast.parse(inspect.getsource(views))
    target = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "runner_job_complete"
        ),
        None,
    )

    assert target is not None, (
        "runner_job_complete is gone or renamed; this ratchet is now blind. "
        "Point it at whatever settles a self-hosted job instead."
    )
    assert _records_a_metric(target), (
        "runner_job_complete settles a job run without recording a metric, so "
        "every self-hosted job is missing from astrolift_pipeline_job_duration_seconds"
    )


def test_every_terminal_transition_anywhere_records():
    """The wider guard, added after the narrow one missed a module.

    The version of this ratchet that shipped with #1585 parsed
    `pipeline_job_spawn` and nothing else, so the fourth place a run
    reaches a terminal status -- `cancel_pipeline_run`, a GraphQL mutation
    in a different app -- was invisible to it. That path recorded no
    metric, `astrolift_pipeline_runs_total` undercounted every
    cancellation, and this suite was green.

    Enumerating modules by hand is what allowed that, so this sweeps the
    tree instead. A transition added in a module nobody thought of is
    precisely the case that goes dark.
    """
    sites = run_status_sites.find_sites()

    assert len(sites) >= 7, (
        f"the sweep found only {len(sites)} status transitions: {sorted(sites)}. "
        "A matcher that stops matching passes vacuously; fix the matcher."
    )

    offenders = [name for name, fn in sites.items() if not run_status_sites.calls_any(fn, _RECORDER_NAMES)]
    assert not offenders, (
        "these settle a run or job to a terminal status without recording a "
        f"metric: {offenders}. /metrics goes quiet for each one."
    )


def test_the_cancel_service_records():
    """Pinned by name, because the cancel path has no assignment shape.

    It settles the run through `state_machine.transition_pipeline_run`,
    which assigns from a variable (`run.status = next_status`), so neither
    end matches the sweep above. Same situation as the runner endpoint, and
    the same remedy.

    This is where the cancel metric lives now. It used to live in the
    GraphQL mutation; moving it to the service means a second cancel entry
    point cannot forget it.
    """
    target = run_status_sites.find_named("astrolift_pipelines/cancellation.py", "cancel_pipeline_run")

    assert target is not None, (
        "cancellation.cancel_pipeline_run is gone or renamed; this ratchet is "
        "now blind. Point it at whatever cancels a run instead."
    )
    assert run_status_sites.calls_any(target, _RECORDER_NAMES), (
        "the cancel service settles a run without recording a metric, so "
        "astrolift_pipeline_runs_total undercounts every cancellation"
    )


def test_the_metrics_module_is_registered_on_the_registry_metrics_view_serves():
    """`/metrics` is `generate_latest()` over prometheus_client's DEFAULT
    registry. A metric registered anywhere else is invisible there, which
    would make every wiring above pointless."""
    names = {
        "astrolift_pipeline_runs_total",
        "astrolift_pipeline_run_duration_seconds",
        "astrolift_pipeline_job_duration_seconds",
        "astrolift_pipeline_webhook_signature_failures_total",
    }
    registered = {m.name for m in REGISTRY.collect()} | {f"{m.name}_total" for m in REGISTRY.collect()}
    missing = {n for n in names if n not in registered and n.removesuffix("_total") not in registered}

    assert not missing, f"not on the default registry, so /metrics will not serve them: {missing}"
