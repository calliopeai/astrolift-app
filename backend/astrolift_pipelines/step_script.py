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
* **It does not resolve `uses:`.** A `uses:` step is recorded as skipped
  and execution continues, so a job whose steps are all `uses:` still
  reports success while doing nothing. That is a deliberate scoping
  decision, not an oversight; `astrolift_pipelines/actions/registry.py`
  is where the resolution would live.
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


def render_step_script(steps: list[Any]) -> str:
    """The `/bin/sh` script for *steps*, in order.

    `steps` are `Step` rows (anything with `position`, `step_id`, `run`,
    `uses` and `env` works, which is what keeps this testable without a
    database).
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

        if not run:
            # Includes the `uses:` case and a step carrying neither. Both
            # are recorded rather than passed over in silence, so the
            # StepRun says `skipped` and an operator reading the pod's
            # output sees why.
            reason = f"uses: {uses}" if uses else "no run command"
            lines.append(f"echo {shlex.quote(f'::: skipped ({reason})')}")
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
