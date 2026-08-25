"""Render a job's steps into the shell script its pod runs (#1501).

Before this, `_build_job_manifest` gave every pipeline job the same
container command::

    ["/bin/sh", "-c", "echo 'pipeline job started'; exit 0"]

Unconditional. The TOML was fetched, parsed and persisted into `Job` and
`Step` rows, the DAG was honoured, the namespace and security context were
correct, and the thing that ran was `echo`. Every pipeline reported
SUCCESS, including the ones that should have failed.

This module is the missing middle: `Step` rows in, one `/bin/sh` script
out.

Two properties the script has to hold, and one it deliberately does not:

* **A failing step fails the job.** The first non-zero exit stops
  execution, which matches the Job's own `backoffLimit: 0` /
  `restartPolicy: Never`. Reporting green while a build failed is the bug
  this exists to end.
* **The control plane can tell which step failed** without reading pod
  logs, which are deleted with the Job seconds after it finishes
  (#1218). Each step appends a record to the termination-log file, and
  Kubernetes surfaces that file's contents on the pod's terminated
  container state. It is a 4096-byte channel intended for exactly this.
* **It resolves `uses:`** through `astrolift_pipelines.actions.registry`.
  Before this, a `uses:` step was recorded as skipped and execution
  continued, so a job whose steps were all `uses:` reported success while
  doing nothing at all. An unknown action or a missing required input now
  fails the job at render time, before the pod is created, because the
  alternative is a container that starts and cannot do the thing it was
  asked to do.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

# Kubernetes' default terminationMessagePath. The kubelet reads this file
# when the container exits and puts it on
# `status.containerStatuses[].state.terminated.message`, truncated to
# 4096 bytes.
TERMINATION_LOG = "/dev/termination-log"

# Records are `<position> <status> <exit code>`, one per line — short on
# purpose, because the whole file has 4096 bytes and a long pipeline
# should not lose its tail.
RECORD = re.compile(r"^(\d+)\s+(ran|skipped)\s+(-?\d+)$")

STATUS_RAN = "ran"
STATUS_SKIPPED = "skipped"

# A shell-legal environment variable name. A step whose env carries
# anything else is refused rather than quietly dropped: `env` comes from
# the operator's TOML, and silently ignoring half of it produces a build
# that fails for reasons nothing on the page explains.
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class StepScriptError(ValueError):
    """A job whose steps cannot be rendered into a runnable script."""


#: Actions that cannot work in a cluster-run pipeline job, and why (#1584).
#:
#: `_build_job_manifest` renders every job with `privileged: False`,
#: `allowPrivilegeEscalation: False`, `runAsNonRoot: True` and no docker
#: socket. A `docker build` step therefore has no daemon to reach, cannot
#: start one, and cannot escalate to try. The usual escapes are ruled out by
#: the same spec: kaniko needs root, and rootless buildah needs user
#: namespaces plus `/dev/fuse` and seccomp allowances the spec does not grant.
#:
#: Refused here rather than left to fail in the container, for the reason the
#: unknown-action check above already exists: this runs before the Job is
#: created, so the operator gets the action name and a next step instead of a
#: pod that starts, runs, and dies confusingly.
IMPOSSIBLE_IN_CLUSTER_JOB: dict[str, str] = {
    "astrolift/docker-build": (
        "cluster-run pipeline jobs are unprivileged and have no docker socket, "
        "so an image build cannot run in one. Build on a self-hosted runner, or "
        "push the build to your own CI (see `astro ci` and pushCiWorkflow)"
    ),
}


def _refuse_if_impossible_here(uses: str, *, label: str, context: dict) -> None:
    """Refuse an action the execution environment cannot support.

    Only when the caller says it is rendering for a cluster-run job. A
    self-hosted runner has a daemon and its own security context, and
    refusing there would break the one path that works today -- so the
    default is to allow, and the spawner opts in.
    """
    if not context.get("cluster_run"):
        return
    reason = IMPOSSIBLE_IN_CLUSTER_JOB.get(str(uses).split("@", 1)[0].strip())
    if reason:
        raise StepScriptError(f"{label}: {uses}: {reason}")


def render_step_script(steps: list[Any], *, context: dict | None = None) -> str:
    """The `/bin/sh` script for *steps*, in order.

    `steps` are `Step` rows (anything with `position`, `step_id`, `run`,
    `uses` and `env` works, which is what keeps this testable without a
    database).

    `context` is handed to a built-in action's `render_steps`. Optional
    because most callers have nothing to add, and no built-in reads it yet;
    it exists because the action contract already takes it.
    """
    if not steps:
        # A job with no steps is a job that does nothing, and it should
        # say so rather than inheriting the old stub's silent success.
        return "echo 'pipeline job has no steps'\nexit 0\n"

    lines = [
        "#!/bin/sh",
        # Not `set -e`: each step's status is checked explicitly so the
        # record can be written before exiting. `set -u` would break
        # operator scripts that rely on unset variables.
        "set +e",
        f": > {TERMINATION_LOG} 2>/dev/null || true",
        "",
    ]

    for step in sorted(steps, key=lambda s: int(getattr(s, "position", 0) or 0)):
        position = int(getattr(step, "position", 0) or 0)
        label = str(getattr(step, "step_id", "") or "") or f"step {position}"
        run = getattr(step, "run", None)
        uses = getattr(step, "uses", None)

        lines.append(f"# --- {label} ---")
        lines.append(f"echo {shlex.quote(f'::: {label}')}")

        if not run and uses:
            # A built-in action renders to ordinary shell, so from here down
            # it is treated exactly like a `run` step: one subshell, one
            # record, one position. Keeping the mapping 1:1 with the Step row
            # is what lets `parse_step_records` settle the StepRun.
            run = _render_uses(step, uses=str(uses), label=label, context=context)

        if not run:
            # A step carrying neither `run` nor `uses`. Recorded rather than
            # passed over in silence, so the StepRun says `skipped` and an
            # operator reading the pod's output sees why.
            lines.append(f"echo {shlex.quote('::: skipped (no run command)')}")
            lines.append(f"echo '{position} {STATUS_SKIPPED} 0' >> {TERMINATION_LOG}")
            lines.append("")
            continue

        for name, value in sorted((getattr(step, "env", None) or {}).items()):
            if not _ENV_NAME.match(str(name)):
                raise StepScriptError(f"{label}: {name!r} is not a legal environment variable name")
            lines.append(f"export {name}={shlex.quote(str(value))}")

        # The operator's script, verbatim, in a subshell so a `cd` or an
        # `exit` in one step cannot reach into the next.
        lines.append("(")
        lines.append(str(run))
        lines.append(")")
        lines.append("__astrolift_rc=$?")
        lines.append(f'echo "{position} {STATUS_RAN} $__astrolift_rc" >> {TERMINATION_LOG}')
        lines.append("if [ $__astrolift_rc -ne 0 ]; then")
        lines.append(f"  echo {shlex.quote(f'::: {label} failed')} >&2")
        # Every remaining step is left without a record, which is what
        # tells the control plane they never ran.
        lines.append("  exit $__astrolift_rc")
        lines.append("fi")
        lines.append("")

    lines.append("exit 0")
    return "\n".join(lines) + "\n"


def _render_uses(step: Any, *, uses: str, label: str, context: dict | None) -> str:
    """Turn a `uses:` step into the shell it stands for.

    Raises `StepScriptError` on an unknown action or a missing required
    input. That is deliberate and it happens here rather than in the
    container: `render_step_script` runs before the K8s Job is created, so
    a pipeline naming an action that does not exist fails with the name in
    the message instead of starting a pod that cannot do its job.
    """
    from astrolift_pipelines.actions import ActionInputError
    from astrolift_pipelines.actions.registry import UnknownActionError, resolve_action

    _refuse_if_impossible_here(uses, label=label, context=context or {})

    try:
        action = resolve_action(uses)
        rendered = action.render_steps(
            with_params=dict(getattr(step, "with_params", None) or {}),
            env=dict(getattr(step, "env", None) or {}),
            context=dict(context or {}),
        )
    except UnknownActionError as exc:
        raise StepScriptError(f"{label}: {exc}") from exc
    except ActionInputError as exc:
        raise StepScriptError(f"{label}: {uses}: {exc}") from exc

    out: list[str] = []
    for sub in rendered:
        command = str(sub.get("run") or "").strip()
        if not command:
            continue
        name = str(sub.get("name") or "")
        if name:
            out.append(f"echo {shlex.quote(f'::: {label} / {name}')}")
        for key, value in sorted((sub.get("env") or {}).items()):
            if not _ENV_NAME.match(str(key)):
                raise StepScriptError(f"{label}: {key!r} is not a legal environment variable name")
            out.append(f"export {key}={shlex.quote(str(value))}")
        # `set -e` is not in force inside the subshell the caller wraps this
        # in, so a failing command in the middle of a multi-command action
        # would otherwise be stepped over and the action reported as passing.
        out.append(f"{command} || exit $?")

    if not out:
        raise StepScriptError(f"{label}: {uses} rendered no commands")
    return "\n".join(out)


def parse_step_records(message: str) -> dict[int, tuple[str, int]]:
    """Read a terminated container's message into `{position: (status, exit)}`.

    Tolerant on purpose: the message is truncated at 4096 bytes and an
    operator's own script may write to the same file, so anything that is
    not a record is ignored rather than failing the settlement of the
    records that are there.
    """
    out: dict[int, tuple[str, int]] = {}
    for line in (message or "").splitlines():
        match = RECORD.match(line.strip())
        if match is None:
            continue
        out[int(match.group(1))] = (match.group(2), int(match.group(3)))
    return out
