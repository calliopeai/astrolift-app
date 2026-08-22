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
    with_params: dict = field(default_factory=dict)


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


def test_a_uses_step_runs_the_action_it_names(tmp_path):
    """This used to record `skipped` and continue, so a job whose steps were
    all `uses:` reported success while doing nothing at all."""
    steps = [
        FakeStep(position=0, run="echo one"),
        FakeStep(
            position=1,
            uses="astrolift/git-checkout@v1",
            step_id="checkout",
            with_params={"repository": "https://example.invalid/r.git", "ref": "main"},
        ),
    ]

    script = render_step_script(steps)

    # The action's own commands are in the script, under the step's label.
    assert "git clone" in script
    assert "::: checkout /" in script
    # And it settles as a real step, not a skip.
    assert f"{STATUS_SKIPPED} 0" not in script.split("# --- checkout ---")[1]


def test_a_multi_command_action_stops_at_the_first_failure(tmp_path, monkeypatch):
    """The failure has to be a command that is NOT last.

    A real action's commands tend to fail together — git clone failing means
    git checkout fails too — so the last command's exit code masks whether
    the middle of the action was actually guarded. This uses an action whose
    failing command is followed by a succeeding one, which is the only shape
    that tells the two apart.
    """

    class _Stub:
        def render_steps(self, with_params, env, context):
            return [
                {"name": "first", "run": "echo starting"},
                # `sh -c` rather than a bare `exit`: a bare `exit` ends the
                # subshell the caller wraps this in, which would make the
                # test pass whether or not the guard is there.
                {"name": "boom", "run": "sh -c 'exit 7'"},
                {"name": "after", "run": "echo should-not-run"},
            ]

    monkeypatch.setattr("astrolift_pipelines.actions.registry.resolve_action", lambda uses: _Stub())
    steps = [FakeStep(position=0, uses="astrolift/whatever@v1", step_id="act")]

    code, out, records = _execute(render_step_script(steps), tmp_path)

    assert code == 7
    assert "starting" in out
    assert "should-not-run" not in out
    assert records[0] == (STATUS_RAN, 7)


def test_an_unknown_action_fails_before_the_pod_is_created():
    """`render_step_script` runs before the K8s Job exists, so a pipeline
    naming an action that does not exist fails with the name in the message
    rather than starting a container that cannot do its job."""
    steps = [FakeStep(position=0, uses="astrolift/deploy@v1", step_id="deploy")]

    with pytest.raises(StepScriptError) as exc:
        render_step_script(steps)

    assert "astrolift/deploy@v1" in str(exc.value)
    # The message lists what does exist, because the fix is a one-word edit.
    assert "astrolift/astrolift-deploy@v1" in str(exc.value)


def test_an_action_missing_a_required_input_fails_at_render():
    steps = [FakeStep(position=0, uses="astrolift/git-checkout@v1", step_id="checkout", with_params={})]

    with pytest.raises(StepScriptError) as exc:
        render_step_script(steps)

    assert "checkout" in str(exc.value)


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
