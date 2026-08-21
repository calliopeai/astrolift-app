"""The emitted TOML parses back to the pipeline it came from.

`astrolift_ci_convert` writes TOML and nothing has ever read it, so no
test could tell whether what it emitted meant anything. It did not:
`_write_step` opened `[[jobs.steps]]` — a fixed path with no job id — so
every job's steps collapsed into one shared array under `jobs`, and no
job carried any of its own. `job_id` was already a parameter of that
function and was simply never used.

Both converters (GitHub Actions and GitLab) emit through this writer, so
every workflow either of them has ever converted lost its job-to-step
association on the way out.

These parse the output with `tomllib` and assert the structure, which is
the check a write-only format never gets.
"""

from __future__ import annotations

import tomllib

from astrolift_ci_convert.common.toml_writer import write_toml
from astrolift_ci_convert.common.types import JobDef, PipelineDef, StepDef


def _round_trip(pipeline: PipelineDef) -> dict:
    return tomllib.loads(write_toml(pipeline))


def test_each_job_owns_its_own_steps():
    parsed = _round_trip(
        PipelineDef(
            name="ci",
            jobs=[
                JobDef(job_id="build", name="Build", steps=[StepDef(name="compile", run="make")]),
                JobDef(job_id="test", name="Test", steps=[StepDef(name="pytest", run="pytest")]),
            ],
        )
    )

    jobs = parsed["jobs"]
    assert [s["name"] for s in jobs["build"]["steps"]] == ["compile"]
    assert [s["name"] for s in jobs["test"]["steps"]] == ["pytest"]
    # The shape the bug produced: a shared array hanging off `jobs`.
    assert "steps" not in jobs


def test_step_order_within_a_job_survives():
    parsed = _round_trip(
        PipelineDef(
            name="ci",
            jobs=[
                JobDef(
                    job_id="build",
                    name="Build",
                    steps=[
                        StepDef(name="first", run="echo 1"),
                        StepDef(name="second", run="echo 2"),
                        StepDef(name="third", run="echo 3"),
                    ],
                )
            ],
        )
    )

    assert [s["name"] for s in parsed["jobs"]["build"]["steps"]] == ["first", "second", "third"]


def test_a_steps_env_and_with_attach_to_that_step():
    # Sub-tables attach to the most recent element of the array above
    # them, so these only land correctly if they carry the same path.
    parsed = _round_trip(
        PipelineDef(
            name="ci",
            jobs=[
                JobDef(
                    job_id="deploy",
                    name="Deploy",
                    steps=[
                        StepDef(name="act", uses="astrolift/deploy", with_params={"ref": "main"}),
                        StepDef(name="run", run="make ship", env={"STAGE": "prod"}),
                    ],
                )
            ],
        )
    )

    steps = parsed["jobs"]["deploy"]["steps"]
    assert steps[0]["with"] == {"ref": "main"}
    assert steps[1]["env"] == {"STAGE": "prod"}


def test_a_job_keeps_its_own_scalars():
    parsed = _round_trip(
        PipelineDef(
            name="ci",
            jobs=[
                JobDef(
                    job_id="build",
                    name="Build",
                    runs_on="astrolift/large",
                    needs=["lint"],
                    steps=[StepDef(name="compile", run="make")],
                )
            ],
        )
    )

    build = parsed["jobs"]["build"]
    assert build["name"] == "Build"
    assert build["runs_on"] == "astrolift/large"
    assert build["needs"] == ["lint"]


def test_a_job_with_no_steps_round_trips():
    parsed = _round_trip(PipelineDef(name="ci", jobs=[JobDef(job_id="noop", name="Noop")]))

    assert parsed["jobs"]["noop"]["name"] == "Noop"
