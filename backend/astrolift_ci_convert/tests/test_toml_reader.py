"""Reading an astrolift.toml back into a PipelineDef (#1531).

The point of this direction existing is that the format finally has a
consumer. `toml_writer` emitted every job's steps into one shared array
for as long as it did (#1544) because nothing could tell it was wrong;
these tests are what makes the format checkable at all.

The headline test is the full round trip: a PipelineDef that survives
write → read unchanged proves the two agree without either being taken as
the spec.
"""

from __future__ import annotations

import pytest

from astrolift_ci_convert.common.toml_reader import TomlReadError, read_toml
from astrolift_ci_convert.common.toml_writer import write_toml
from astrolift_ci_convert.common.types import JobDef, PipelineDef, ScheduleDef, StepDef


def test_a_pipeline_survives_a_full_round_trip():
    original = PipelineDef(
        name="ci",
        env={"REGISTRY": "ghcr.io"},
        on_push_branches=["main", "release/*"],
        on_push_tags=["v*"],
        on_pull_request_branches=["main"],
        on_schedule=[ScheduleDef(cron="0 2 * * *")],
        on_workflow_dispatch=True,
        jobs=[
            JobDef(
                job_id="build",
                name="Build",
                runs_on="astrolift/large",
                container="python:3.12",
                needs=["lint"],
                env={"STAGE": "ci"},
                outputs={"digest": "sha256:x"},
                steps=[
                    StepDef(name="checkout", uses="astrolift/checkout", with_params={"depth": "1"}),
                    StepDef(name="compile", run="make build", env={"CC": "clang"}),
                ],
            ),
            JobDef(
                job_id="test",
                name="Test",
                steps=[StepDef(name="pytest", run="pytest -q")],
            ),
        ],
    )

    parsed = read_toml(write_toml(original))

    assert parsed.name == original.name
    assert parsed.env == original.env
    assert parsed.on_push_branches == original.on_push_branches
    assert parsed.on_push_tags == original.on_push_tags
    assert parsed.on_pull_request_branches == original.on_pull_request_branches
    assert [s.cron for s in parsed.on_schedule] == ["0 2 * * *"]
    assert parsed.on_workflow_dispatch is True

    assert [j.job_id for j in parsed.jobs] == ["build", "test"]
    build = parsed.jobs[0]
    assert build.name == "Build"
    assert build.runs_on == "astrolift/large"
    assert build.container == "python:3.12"
    assert build.needs == ["lint"]
    assert build.env == {"STAGE": "ci"}
    assert build.outputs == {"digest": "sha256:x"}

    # The association #1544 was losing.
    assert [s.name for s in build.steps] == ["checkout", "compile"]
    assert [s.name for s in parsed.jobs[1].steps] == ["pytest"]
    assert build.steps[0].uses == "astrolift/checkout"
    assert build.steps[0].with_params == {"depth": "1"}
    assert build.steps[1].run.strip() == "make build"
    assert build.steps[1].env == {"CC": "clang"}


def test_a_minimal_pipeline_reads():
    parsed = read_toml('name = "ci"\n')

    assert parsed.name == "ci"
    assert parsed.jobs == []


def test_job_order_is_stable_across_reads():
    # TOML tables are unordered by spec. A DAG that reorders itself between
    # reads is a debugging nightmare; `needs` carries the real ordering.
    text = 'name = "ci"\n[jobs.zeta]\n[jobs.alpha]\n[jobs.mid]\n'

    assert [j.job_id for j in read_toml(text).jobs] == ["alpha", "mid", "zeta"]
    assert [j.job_id for j in read_toml(text).jobs] == ["alpha", "mid", "zeta"]


def test_a_job_defaults_its_name_to_its_id():
    parsed = read_toml('name = "ci"\n[jobs.build]\n')

    assert parsed.jobs[0].name == "build"
    assert parsed.jobs[0].runs_on == "astrolift/default"


def test_env_values_are_coerced_to_strings():
    # `PORT = 8080` is an int in TOML and a string to the operator.
    parsed = read_toml('name = "ci"\n[env]\nPORT = 8080\n')

    assert parsed.env == {"PORT": "8080"}


# ---- refusals, each naming where ------------------------------------------


def test_invalid_toml_is_refused():
    with pytest.raises(TomlReadError, match="not valid TOML"):
        read_toml("name = = =")


def test_a_pipeline_without_a_name_is_refused():
    with pytest.raises(TomlReadError) as exc:
        read_toml("[jobs.build]\n")
    assert exc.value.path == "name"


def test_a_step_with_neither_run_nor_uses_is_refused():
    with pytest.raises(TomlReadError) as exc:
        read_toml('name = "ci"\n[[jobs.build.steps]]\nname = "nothing"\n')
    assert exc.value.path == "jobs.build.steps[0]"


def test_a_step_with_both_run_and_uses_is_refused():
    # Which one wins would be silent either way.
    with pytest.raises(TomlReadError, match="alternatives"):
        read_toml('name = "ci"\n[[jobs.build.steps]]\nrun = "make"\nuses = "a/b"\n')


def test_steps_as_a_table_rather_than_an_array_is_refused():
    # The shape #1544's bug produced from the other side.
    with pytest.raises(TomlReadError) as exc:
        read_toml('name = "ci"\n[jobs.build]\nsteps = "compile"\n')
    assert exc.value.path == "jobs.build.steps"


def test_a_schedule_without_a_cron_is_refused():
    with pytest.raises(TomlReadError) as exc:
        read_toml('name = "ci"\n[[on.schedule]]\n')
    assert exc.value.path == "on.schedule[0].cron"


def test_a_non_string_branch_list_is_refused():
    with pytest.raises(TomlReadError) as exc:
        read_toml('name = "ci"\n[on.push]\nbranches = [1, 2]\n')
    assert exc.value.path == "on.push.branches"
