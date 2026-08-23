"""Tests for the lifecycle runner (spec 41 §2, spec 43 §0.1).

The runner's job is to be believed. Two things make it believable and both are
tested here: that a green grid means every step really passed, and that a red
one names the step, the cell and the assertion precisely enough to file.

Every fault case plants exactly one defect and asserts which step catches it. A
defect caught at the wrong step is worse than one missed, because it sends the
next person to read the wrong code.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from _cert.harness.cell import Binding, Cell, UnrunnableCell
from _cert.harness.model import Outcome, Step
from _cert.harness.runner import LifecycleRunner, Poll
from tests._cert.fake_platform import UPDATE_IMAGE, FakePlatform, happy_cell


def runner_for(platform: FakePlatform, **kwargs) -> LifecycleRunner:
    """No real waiting: the polls run their predicate without sleeping so a
    convergence failure is a fast test rather than a 45-minute one."""
    instant = Poll(timeout_s=0.0, interval_s=0.0, sleep=lambda _: None, now=lambda: 0.0)
    return LifecycleRunner(
        platform=platform,
        scan=platform.scan,
        update_image_ref=UPDATE_IMAGE,
        provision_poll=instant,
        teardown_poll=instant,
        estimate_logged=True,
        **kwargs,
    )


def outcome_at(result, step: Step) -> Outcome:
    found = result.first.result_for(step) if step is not Step.REPRODUCE else result.reproduce
    return found.outcome if found else Outcome.SKIPPED


# ---- the happy path -----------------------------------------------------------


def test_a_healthy_platform_goes_green_at_every_step_including_reproduce():
    """The control case. If this ever needs loosening to stay green, the
    loosening is the finding."""
    result = runner_for(FakePlatform()).run_cell(happy_cell())

    assert [step.outcome for step in result.steps] == [Outcome.GREEN] * 7
    assert result.is_green
    assert result.differences == ()


def test_the_grid_renders_one_row_per_cell_with_a_column_per_step():
    """The grid is the certification evidence. Something transcribed by hand
    into a ticket is not."""
    from _cert.harness.model import Grid

    grid = Grid()
    grid.add(runner_for(FakePlatform()).run_cell(happy_cell()))
    rendered = grid.render()

    assert "happy-path/aws" in rendered
    assert "REPRODUCE" in rendered
    assert "1/1 cells GREEN" in rendered
    assert grid.as_dict()["green"] is True


# ---- one fault per step -------------------------------------------------------


@pytest.mark.parametrize(
    ("fault", "step", "expected_in_assertion"),
    [
        ("register_fails", Step.BUILDOUT, "BUILDOUT step must complete against the platform"),
        ("app_never_ready", Step.BUILDOUT, "must be ready after registration"),
        ("service_stuck", Step.BUILDOUT, "must be ACTIVE"),
        ("pods_not_ready", Step.VERIFY_UP, "ready replica"),
        ("binding_missing", Step.VERIFY_UP, "binding must be present in the pod"),
        ("selftest_fails", Step.VERIFY_UP, "connectable from the pod"),
        ("no_tls", Step.VERIFY_UP, "verified TLS"),
        ("update_fails", Step.UPDATE, "must roll cleanly"),
        ("rollback_noop", Step.UPDATE, "rollback must return the app"),
        ("old_revision_lingers", Step.VERIFY_UPD, "previous revision must be retired"),
        ("teardown_never_converges", Step.TEARDOWN, "must finish"),
        ("orphan_left_behind", Step.VERIFY_CLEAN, "must leave nothing behind"),
        ("leaked_name", Step.REPRODUCE, "same state"),
    ],
)
def test_the_named_step_is_the_one_reported_red(fault, step, expected_in_assertion):
    result = runner_for(FakePlatform(fault=fault)).run_cell(happy_cell())

    assert outcome_at(result, step) is Outcome.RED, (
        f"{fault} should be caught at {step}; the grid says {[(str(s.step), str(s.outcome)) for s in result.steps]}"
    )
    reported = result.first.result_for(step) if step is not Step.REPRODUCE else result.reproduce
    assert expected_in_assertion in reported.assertion


@pytest.mark.parametrize(
    ("fault", "step"),
    [
        ("app_never_ready", Step.BUILDOUT),
        ("selftest_fails", Step.VERIFY_UP),
        ("update_fails", Step.UPDATE),
        ("old_revision_lingers", Step.VERIFY_UPD),
    ],
)
def test_a_red_step_is_the_first_red_and_the_rest_of_the_cycle_is_skipped_not_run(fault, step):
    """Continuing past a red would pile assertions about a state nobody expects
    on top of the one real defect, and the report would take an hour to read."""
    result = runner_for(FakePlatform(fault=fault)).run_cell(happy_cell())

    assert result.first.first_red.step is step
    later = [Step.BUILDOUT, Step.VERIFY_UP, Step.UPDATE, Step.VERIFY_UPD]
    for skipped in later[later.index(step) + 1 :]:
        assert outcome_at(result, skipped) is Outcome.SKIPPED


# ---- the properties that make it safe to leave running ------------------------


@pytest.mark.parametrize("fault", ["app_never_ready", "service_stuck", "selftest_fails", "update_fails"])
def test_teardown_runs_even_when_the_cycle_already_failed(fault):
    """The cost-safety property. A cell that fails at BUILDOUT has usually
    created something first, and abandoning it on a metered account is worse
    than the defect that stranded it. This is the single most important thing
    about leaving the harness unattended overnight."""
    platform = FakePlatform(fault=fault)
    result = runner_for(platform).run_cell(happy_cell(), reproduce=False)

    assert outcome_at(result, Step.TEARDOWN) is not Outcome.SKIPPED
    assert platform.get_app("cert2026q3-happy-aws") is None
    assert platform.inventory.rds == []
    assert platform.inventory.s3 == []


def test_teardown_is_skipped_not_green_when_registration_never_created_anything():
    """The counterpart to the case above, and the reason the distinction is
    worth a third outcome. A grid is only evidence if every green on it was
    earned: ``register_fails`` leaves no app, so deregister never runs, and a
    green TEARDOWN would read as "the teardown path was exercised" to the next
    person certifying this cloud."""
    result = runner_for(FakePlatform(fault="register_fails")).run_cell(happy_cell(), reproduce=False)

    assert outcome_at(result, Step.TEARDOWN) is Outcome.SKIPPED
    # VERIFY-CLEAN still runs and is still meaningful: a half-finished register
    # can have created cloud resources the platform never got a row for.
    assert outcome_at(result, Step.VERIFY_CLEAN) is Outcome.GREEN


def test_buildout_refuses_to_start_on_a_previous_runs_leftover():
    """Adopting residue would certify a lifecycle that only works once, and it
    would hide the very teardown defect the campaign is hunting."""
    platform = FakePlatform()
    runner_for(platform).run_cell(happy_cell(), reproduce=False)
    # A teardown that freed the resources but left the app row still served.
    from _cert.harness.platform import AppObservation

    platform.app = AppObservation(slug="cert2026q3-happy-aws", provisioning_status="deregistered")

    second = runner_for(platform).run_cell(happy_cell(), reproduce=False)

    assert outcome_at(second, Step.BUILDOUT) is Outcome.RED
    assert "may exist before BUILDOUT" in second.first.result_for(Step.BUILDOUT).assertion


def test_buildout_enforces_class_c_estimate_before_registering_anything():
    platform = FakePlatform()
    runner = runner_for(platform)
    runner.estimate_logged = False
    class_c_cell = replace(
        happy_cell(),
        bindings=(Binding("postgres", "records", "aurora_postgres_serverless_v2"),),
    )

    result = runner.run_cell(class_c_cell, reproduce=False)

    buildout = result.first.result_for(Step.BUILDOUT)
    assert buildout.outcome is Outcome.RED
    assert "spend gate" in buildout.assertion
    assert "pre-flight cost estimate" in buildout.detail
    assert platform.get_app("cert2026q3-happy-aws") is None


def test_a_cycle_without_an_orphan_scanner_cannot_claim_clean():
    """A scan is not optional decoration on VERIFY-CLEAN; it is the step. A
    runner with no scanner that reported green would certify teardown on the
    basis of the platform's own opinion of itself."""
    runner = LifecycleRunner(platform=FakePlatform(), update_image_ref=UPDATE_IMAGE, scan=None, estimate_logged=True)
    runner.provision_poll = runner.teardown_poll = Poll(0.0, 0.0, sleep=lambda _: None, now=lambda: 0.0)

    result = runner.run_cell(happy_cell(), reproduce=False)

    assert outcome_at(result, Step.VERIFY_CLEAN) is Outcome.RED
    assert "requires an orphan scanner" in result.first.result_for(Step.VERIFY_CLEAN).assertion


def test_update_refuses_rather_than_inventing_an_image_to_roll_to():
    """Campaign blocker B3 was a deploy that failed on a tag that had moved. A
    default here would reproduce that as a platform defect on the report."""
    runner = runner_for(FakePlatform())
    runner.update_image_ref = ""

    result = runner.run_cell(happy_cell(), reproduce=False)

    assert outcome_at(result, Step.UPDATE) is Outcome.RED
    assert "second published image" in result.first.result_for(Step.UPDATE).assertion


def test_an_unexpected_exception_still_names_its_step():
    """The harness has bugs too, and a cell that dies on one still has to say
    where it died -- otherwise an overnight run comes back as a traceback with
    no cell attached."""

    class Exploding(FakePlatform):
        def list_workloads(self, slug):
            raise ZeroDivisionError("harness bug")

    result = runner_for(Exploding()).run_cell(happy_cell(), reproduce=False)
    reported = result.first.result_for(Step.VERIFY_UP)

    assert reported.outcome is Outcome.RED
    assert "VERIFY-UP step must not raise" in reported.assertion
    assert "ZeroDivisionError" in reported.detail


# ---- the failure is actionable -------------------------------------------------


def test_a_failure_line_names_the_cell_the_step_and_the_assertion():
    """Spec 41 §2: "a failure is actionable output, not a mystery". That is a
    shape constraint, so it is asserted on the shape."""
    result = runner_for(FakePlatform(fault="selftest_fails")).run_cell(happy_cell())
    (line,) = [failure for failure in result.failures() if "VERIFY-UP" in failure]

    assert "happy-path/aws" in line
    assert "VERIFY-UP" in line
    assert "connectable from the pod" in line
    assert "postgres: connection refused" in line
    assert "Traceback" not in line


def test_reproduce_is_skipped_rather_than_run_when_the_first_pass_is_red():
    """Reproducing a broken cycle doubles the spend and proves nothing."""
    result = runner_for(FakePlatform(fault="selftest_fails")).run_cell(happy_cell())

    assert result.reproduce.outcome is Outcome.SKIPPED
    assert result.second is None


# ---- cells the runner will not run ---------------------------------------------


def test_a_negative_cell_is_refused_rather_than_run_as_a_cycle():
    """Its expected outcome is a refusal, so a cycle would report the thing it
    is testing for as a BUILDOUT failure."""
    manifest = {
        "name": "cert2026q3-neg-untagged-aws",
        "campaign": {"cell": "negative/untagged-teardown/aws", "cloud": "aws", "negative": {"case": "untagged"}},
        "workloads": [{"name": "web", "is_public": True, "containers": [{"image_ref": "x:1", "is_primary": True}]}],
    }

    with pytest.raises(UnrunnableCell, match="not an unattended cycle"):
        Cell.from_toml(manifest)


def test_a_cell_with_no_public_workload_is_refused():
    """VERIFY-UP would pass by having nothing to check."""
    manifest = {
        "name": "x",
        "campaign": {"cell": "c", "cloud": "aws"},
        "workloads": [{"name": "worker", "containers": [{"image_ref": "x:1", "is_primary": True}]}],
    }

    with pytest.raises(UnrunnableCell, match="no public workload"):
        Cell.from_toml(manifest)


def test_every_committed_happy_path_manifest_loads_as_a_runnable_cell():
    """These are the three cells the campaign certifies. A manifest the runner
    cannot read is a Phase 2 morning lost."""
    from _cert.collection import MANIFEST_ROOT

    for path in sorted((MANIFEST_ROOT / "happy-path").glob("*.toml")):
        cell = Cell.load(path)
        assert cell.app_name.startswith("cert2026q3-")
        assert cell.bindings
        assert cell.public_workload
        assert cell.image_tag and ":" not in cell.image_tag
