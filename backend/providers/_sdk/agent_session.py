"""The agent-box: a long-lived pod that exists to be exec'd into (#128).

The IDE half already shipped: a sandbox terminal's exec session can *be* the
agent REPL, ``astro exec --app X -- claude`` over the control-plane relay. The
platform had nothing warm to exec into. ``kind=agent`` workloads are
``run_family=task, run_mode=once``: batch runs that end.

``run_family=service`` is close but not it. A service runs the agent as its
entrypoint and serves traffic; an agent-box runs nothing in particular and
exists so a human or a relay can attach to it.

tmux is the load-bearing part rather than a convenience. Without it the agent
process is a child of the exec session, so a dropped WebSocket, an IDE restart
or a laptop lid closing kills it mid-thought. With it the exec session attaches
to a session that already exists and outlives the connection, which is also
what allows more than one viewer and reattaching from somewhere else.

Everything here is a pure builder so the shape can be tested without a cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The tmux session every agent-box holds open. Fixed rather than generated so
#: ``astro exec -- tmux attach`` needs no lookup, and so a second attach from a
#: different client lands in the same session rather than starting a rival one.
SESSION_NAME = "astrolift"

#: How often the keep-alive loop re-checks liveness and idleness. Short enough
#: that a finished session frees its node promptly, long enough not to show up
#: in a pod's CPU profile.
POLL_SECONDS = 5

#: Default idle timeout. A forgotten agent-box burning a node is the failure
#: mode this exists to prevent, so the default is finite rather than infinite.
DEFAULT_IDLE_TIMEOUT_SECONDS = 3600

#: Sentinel for "never reap". Explicit, because an operator who wants a box to
#: outlive a long detached run should have to say so.
NEVER = 0


@dataclass(frozen=True)
class SessionSpec:
    """What an agent-box container needs in order to be attachable."""

    image: str
    session_name: str = SESSION_NAME
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    shell: str = "/bin/bash"
    env: dict[str, str] = field(default_factory=dict)
    working_dir: str = "/workspace"

    def __post_init__(self) -> None:
        if not self.image:
            raise ValueError("an agent-box needs an image to run")
        if self.idle_timeout_seconds < 0:
            raise ValueError("idle_timeout_seconds cannot be negative; use NEVER (0) to disable reaping")
        if not self.session_name.isidentifier():
            # tmux target names are interpolated into the keep-alive script.
            raise ValueError(f"session name {self.session_name!r} is not a safe tmux target")


def keepalive_script(spec: SessionSpec) -> str:
    """The container's PID 1: start the tmux server, then outlive it deliberately.

    Three properties, and each is the reason for a line that would otherwise
    look redundant.

    *The pod dies when the session dies.* ``has-session`` is the loop condition,
    so exiting the shell inside tmux ends the pod rather than leaving an empty
    container billing a node until something else notices.

    *Idleness is not "nobody is attached".* Reaping on attachment alone would
    kill an agent that is working while its operator is at lunch, which is the
    exact failure tmux was introduced to prevent. A box is idle only when no
    client is attached *and* tmux has seen no pane activity for the timeout, so
    a detached-but-busy agent keeps its pod.

    *Activity is read from tmux, not guessed.* ``window_activity`` is a unix
    timestamp tmux advances on pane output, so the check survives a restart of
    the loop and needs no bookkeeping of its own. It is deliberately not
    ``session_activity``, which sounds like the right field and is not: that one
    does not move when a detached session produces output, so reaping on it
    killed a working agent within one timeout. The maximum across windows is
    taken because a box may hold several and only one need be busy.
    """
    name = spec.session_name
    timeout = spec.idle_timeout_seconds
    return f"""set -eu
tmux new-session -d -s {name} -c {spec.working_dir} {spec.shell}
tmux set-option -t {name} status off
IDLE_TIMEOUT={timeout}
while tmux has-session -t {name} 2>/dev/null; do
  if [ "$IDLE_TIMEOUT" -gt 0 ]; then
    clients=$(tmux list-clients -t {name} 2>/dev/null | wc -l)
    if [ "$clients" -eq 0 ]; then
      last=$(tmux list-windows -t {name} -F '#{{window_activity}}' 2>/dev/null | sort -n | tail -1)
      last=${{last:-0}}
      now=$(date +%s)
      if [ "$last" -gt 0 ] && [ $((now - last)) -ge "$IDLE_TIMEOUT" ]; then
        tmux kill-session -t {name} 2>/dev/null || true
        break
      fi
    fi
  fi
  sleep {POLL_SECONDS}
done
"""


def container_spec(spec: SessionSpec) -> dict[str, object]:
    """The Kubernetes container for an agent-box.

    ``stdin`` and ``tty`` are set because an exec that attaches to tmux needs a
    terminal on the other end; without them tmux refuses to attach and the
    session appears broken rather than absent.

    No ports and no probes. The box serves nothing, so a readiness probe would
    be asserting something untrue, and an HTTP liveness check would restart a
    perfectly healthy pod that simply has no listener. The keep-alive loop
    exiting is the liveness signal.
    """
    return {
        "name": "agent-box",
        "image": spec.image,
        "command": ["/bin/sh", "-lc"],
        "args": [keepalive_script(spec)],
        "workingDir": spec.working_dir,
        "stdin": True,
        "tty": True,
        "env": [{"name": k, "value": v} for k, v in sorted(spec.env.items())],
    }


def attach_argv(session_name: str = SESSION_NAME) -> list[str]:
    """What ``astro exec`` should run to join the box.

    ``new-session -A`` attaches to the session if it exists and creates it if it
    does not, so a race between the keep-alive loop's first line and an eager
    client is an attach rather than an error.
    """
    return ["tmux", "new-session", "-A", "-s", session_name]
