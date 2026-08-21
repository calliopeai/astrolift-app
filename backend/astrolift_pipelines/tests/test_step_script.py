"""The script a pipeline job's pod runs (#1501).

The bug these exist to keep closed: the container command was
``echo 'pipeline job started'; exit 0``, unconditional, so every pipeline
reported SUCCESS without running any of the operator's steps.

The renderer is a pure function, so most of this runs the script it
produces under a real ``/bin/sh`` and asserts on what actually happened.
Asserting on the generated text would pass just as happily for a script
that does not work.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from astrolift_pipelines.step_script import (
    STATUS_RAN,
    STATUS_SKIPPED,
    StepScriptError,
    parse_step_records,
    render_step_script,
)


@dataclass
class FakeStep:
    position: int
    run: str | None = None
    uses: str | None = None
    step_id: str = ""
    env: dict = field(default_factory=dict)


def _execute(script: str, tmp_path: Path) -> tuple[int, str, dict]:
    """Run *script* under /bin/sh with the termination log redirected.

    Returns ``(exit code, stdout, parsed records)``.
    """
    log = tmp_path / "termination-log"
    script = script.replace("/dev/termination-log", str(log))
    proc = subprocess.run(
        ["/bin/sh", "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=30,
    )
    message = log.read_text() if log.exists() else ""
    return proc.returncode, proc.stdout, parse_step_records(message)


def test_a_run_step_actually_runs(tmp_path):
    steps = [FakeStep(position=0, step_id="greet", run="echo hello-from-the-step")]

    code, out, records = _execute(render_step_script(steps), tmp_path)

    assert code == 0
    assert "hello-from-the-step" in out
    assert records == {0: (STATUS_RAN, 0)}


def test_steps_run_in_position_order(tmp_path):
    steps = [
        FakeStep(position=2, run="echo third"),
        FakeStep(position=0, run="echo first"),
        FakeStep(position=1, run="echo second"),
    ]

    code, out, records = _execute(render_step_script(steps), tmp_path)

    assert code == 0
    assert out.index("first") < out.index("second") < out.index("third")
    assert set(records) == {0, 1, 2}


def test_a_failing_step_fails_the_job_and_stops_the_rest(tmp_path):
    """The headline bug, from the script's side.

    `backoffLimit: 0` means the Job gets one shot, so the first non-zero
    exit has to end the run — and the steps after it must leave no record,
    which is what tells the control plane they never ran.
    """
    steps = [
        FakeStep(position=0, run="echo before"),
        FakeStep(position=1, step_id="build", run="exit 42"),
        FakeStep(position=2, run="echo after-should-not-run"),
    ]

    code, out, records = _execute(render_step_script(steps), tmp_path)

    assert code == 42
    assert "before" in out
    assert "after-should-not-run" not in out
    assert records == {0: (STATUS_RAN, 0), 1: (STATUS_RAN, 42)}
    assert 2 not in records


def test_a_uses_step_is_recorded_as_skipped_and_the_job_continues(tmp_path):
    """Scoped decision, not an oversight: `uses:` is not resolved.

    It records `skipped` and execution continues, so a job whose only step
    is a `uses:` still reports success while doing nothing. The record is
    what stops that being silent.
    """
    steps = [
        FakeStep(position=0, run="echo one"),
        FakeStep(position=1, uses="astrolift/deploy@v1", step_id="deploy"),
        FakeStep(position=2, run="echo three"),
    ]

    code, out, records = _execute(render_step_script(steps), tmp_path)

    assert code == 0
    assert "one" in out and "three" in out
    assert records[1] == (STATUS_SKIPPED, 0)
    assert "astrolift/deploy@v1" in out


def test_a_step_with_neither_run_nor_uses_is_skipped_not_silent(tmp_path):
    steps = [FakeStep(position=0, step_id="empty")]

    code, _out, records = _execute(render_step_script(steps), tmp_path)

    assert code == 0
    assert records == {0: (STATUS_SKIPPED, 0)}


def test_step_env_reaches_the_command(tmp_path):
    steps = [FakeStep(position=0, run='echo "[$GREETING]"', env={"GREETING": "hello world"})]

    code, out, _records = _execute(render_step_script(steps), tmp_path)

    assert code == 0
    assert "[hello world]" in out


def test_env_values_are_quoted_not_interpolated(tmp_path):
    """`env` is operator TOML. A value is a value, never a command."""
    steps = [FakeStep(position=0, run='echo "[$PAYLOAD]"', env={"PAYLOAD": "$(id -u)"})]

    code, out, _records = _execute(render_step_script(steps), tmp_path)

    assert code == 0
    assert "[$(id -u)]" in out


def test_an_illegal_env_name_is_refused_rather_than_dropped():
    steps = [FakeStep(position=0, run="true", env={"not a name": "x"})]

    with pytest.raises(StepScriptError, match="not a name"):
        render_step_script(steps)


def test_one_steps_cd_does_not_leak_into_the_next(tmp_path):
    """A subshell per step, so a step that wanders cannot move the next one."""
    (tmp_path / "sub").mkdir()
    steps = [
        FakeStep(position=0, run="cd sub"),
        FakeStep(position=1, run="pwd"),
    ]

    code, out, _records = _execute(render_step_script(steps), tmp_path)

    assert code == 0
    printed = [line for line in out.splitlines() if line.startswith("/")]
    assert printed, out
    assert not printed[-1].endswith("/sub")


def test_an_exit_inside_a_step_ends_that_step_not_the_script(tmp_path):
    """A subshell per step, so `exit 0` in one does not skip the rest."""
    steps = [
        FakeStep(position=0, run="exit 0"),
        FakeStep(position=1, run="echo reached-the-second"),
    ]

    code, out, records = _execute(render_step_script(steps), tmp_path)

    assert code == 0
    assert "reached-the-second" in out
    assert records == {0: (STATUS_RAN, 0), 1: (STATUS_RAN, 0)}


def test_a_job_with_no_steps_says_so(tmp_path):
    code, out, records = _execute(render_step_script([]), tmp_path)

    assert code == 0
    assert "no steps" in out
    assert records == {}


def test_parse_ignores_anything_that_is_not_a_record():
    # The message is truncated at 4096 bytes and the operator's own script
    # can write to the same file, so a half-line or stray output must not
    # cost us the records that are intact.
    message = "\n".join(
        [
            "some output the build wrote",
            "0 ran 0",
            "1 ran",  # truncated
            "2 skipped 0",
            "not a record at all",
        ]
    )

    assert parse_step_records(message) == {0: (STATUS_RAN, 0), 2: (STATUS_SKIPPED, 0)}


def test_parse_tolerates_an_empty_message():
    assert parse_step_records("") == {}
