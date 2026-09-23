"""Agent Substrate spawn backend (#1853 spike, do not merge).

Runs an agent box as a substrate actor, so an idle box can suspend (process
memory, tmux and filesystem snapshotted to object storage, its worker pod
freed) and resume when someone attaches. Measured on kind (gVisor, arm64):
ResumeActor p50 0.51 s, attach on a suspended box p50 0.41 s, 100 boxes on 4
worker pods with every shell variable and workspace file intact.

Boxes, not batch tasks. Substrate has no completion signal: an actor whose
main process exits still reads RUNNING, so status() can report a crash but
never a success. The task path stays on K8s Jobs.

The seam: nothing outside this module imports it or speaks substrate. The
registry names it only while SUBSTRATE_SPAWNER_ENABLED is on. Upstream says
its API will change, so every call goes through one transport class.

How the agent pod baseline (#1848, #1850) translates:

- resources: the template's limits size the gVisor sandbox; the WorkerPool's
  pods must be at least that big, since an actor occupies a whole worker.
- securityContext: substrate runs every actor as uid 0 with a capability set,
  inside gVisor. Root mode maps to the runtime's default set. Non-root is
  refused: at substrate d277088b a uid-42042 directory in the durable dir
  wedges suspend and delete (the worker cannot remove it).
- ServiceAccount token: the actor never sees one; the worker pod holds it,
  outside the sandbox.
- NetworkPolicy fence: becomes the actor's EgressPolicy, an allowlist of the
  public IPv4 space minus the fence's internal ranges, plus
  AGENT_EGRESS_ALLOW_CIDRS. Egress is HTTP(S) only; ssh and other TCP are
  blocked by substrate whatever the policy says.
- per-task Secrets: refused. Template env is literal, is stored in
  substrate's database and is captured in every snapshot; credentials belong
  at the gateway (#1851).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import ipaddress
import json
import logging
import pathlib
import subprocess
from typing import TYPE_CHECKING

from astrolift_dispatch.spawners.base import ContainerSpawner, SpawnResult, TaskStatus

if TYPE_CHECKING:
    from astrolift_agents.models import AgentTask

logger = logging.getLogger(__name__)

#: Substrate validation bounds (ateapi.proto at d277088b).
ARGV_MAX_CHARS = 4096
ENV_MAX_ITEMS = 32
NAME_MAX_CHARS = 63

#: Where the box keeps its work. A durableDir, so it rides every snapshot.
WORKSPACE = "/workspace"
SHIM_PORT = 80
_SHIM = pathlib.Path(__file__).with_name("substrate_attach_shim.py")

#: containerd's default set, which a root-mode agent pod holds today. Substrate
#: otherwise grants only AUDIT_WRITE, KILL and NET_BIND_SERVICE, and apt-get
#: needs the rest.
RUNTIME_DEFAULT_CAPS = (
    "CHOWN",
    "DAC_OVERRIDE",
    "FSETID",
    "FOWNER",
    "MKNOD",
    "NET_RAW",
    "SETGID",
    "SETUID",
    "SETFCAP",
    "SETPCAP",
    "SYS_CHROOT",
)

#: Loopback and "this network" would reach the egress gateway's own pod.
_GATEWAY_LOCAL = ("127.0.0.0/8", "0.0.0.0/8")

#: Actor states that still hold a box. SUSPENDED is alive, just not on a worker.
_LIVE = {
    "ACTOR_STATE_RESUMING",
    "ACTOR_STATE_RUNNING",
    "ACTOR_STATE_SUSPENDING",
    "ACTOR_STATE_SUSPENDED",
    "ACTOR_STATE_PAUSING",
    "ACTOR_STATE_PAUSED",
}

NON_ROOT_UNSUPPORTED = (
    "the substrate backend cannot run a non-root box yet: at substrate d277088b a file the "
    "agent user creates in the durable workspace wedges suspend and delete; run the spec as root "
    "or use the k8s_job backend"
)
SECRETS_UNSUPPORTED = (
    "the substrate backend cannot carry per-box secrets: template env is literal, stored in "
    "substrate's database and captured in every snapshot; inject credentials at the gateway "
    "(#1851) or use the k8s_job backend"
)


class SubstrateError(RuntimeError):
    """A substrate call failed."""


class KubectlAte:
    """Transport: the kubectl-ate plugin.

    It port-forwards to ate-api-server and authenticates with a token it mints
    for substrate's client ServiceAccount, so the caller needs only a kube
    context. A production backend would ride the cluster driver instead.
    """

    def __init__(self, context: str, *, kubectl: str = "kubectl", timeout: int = 120) -> None:
        self._context = context
        self._kubectl = kubectl
        self._timeout = timeout

    def __call__(self, *args: str, stdin: str | None = None) -> dict | None:
        """Run one command; the parsed JSON, {} for no JSON, None for NotFound."""
        cmd = [self._kubectl, "ate", *(["--context", self._context] if self._context else []), *args]
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=self._timeout)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            if "code = NotFound" in err:
                return None
            raise SubstrateError(err or f"kubectl ate {' '.join(args[:2])} failed")
        out = proc.stdout.strip()
        if not out.startswith("{"):
            return {}
        return json.loads(out)


def _payload(data: bytes) -> str:
    """gzip+base64, the only way to ship a file: no ConfigMap volumes, argv capped."""
    encoded = base64.b64encode(gzip.compress(data, mtime=0)).decode()
    if len(encoded) > ARGV_MAX_CHARS:
        raise ValueError(
            f"payload is {len(encoded)} chars; substrate caps an argv string at {ARGV_MAX_CHARS}"
        )
    return encoded


def _startup_script() -> str:
    return "\n".join(
        [
            "set -eu",
            f"mkdir -p {WORKSPACE} /opt/astrolift",
            'echo "$1" | base64 -d | gunzip > /opt/astrolift/attach_shim.py',
            'echo "$2" | base64 -d | gunzip > /opt/astrolift/keepalive.sh',
            f"BOX_PORT={SHIM_PORT} python3 /opt/astrolift/attach_shim.py > /tmp/attach-shim.log 2>&1 &",
            "exec /bin/sh -l /opt/astrolift/keepalive.sh",
        ]
    )


def public_ipv4_allowlist(extra_internal: tuple[str, ...] = ()) -> list[str]:
    """The IPv4 space minus the agent fence's internal ranges, as CIDRs.

    Substrate egress rules only allow, so the fence's "anything but internal"
    has to be written as its complement.
    """
    from astrolift_clusters.egress import DEFAULT_INTERNAL_CIDRS

    blocked = [
        ipaddress.ip_network(cidr)
        for cidr in (*DEFAULT_INTERNAL_CIDRS, *_GATEWAY_LOCAL, *extra_internal)
        if ":" not in cidr
    ]
    allowed = [ipaddress.ip_network("0.0.0.0/0")]
    for block in blocked:
        nxt = []
        for net in allowed:
            if block.subnet_of(net):
                nxt.extend(net.address_exclude(block))
            elif not net.subnet_of(block):
                nxt.append(net)
        allowed = nxt
    return [str(net) for net in sorted(allowed)]


def render_egress_policy(allow_cidrs: str = "") -> dict:
    extra = [part.strip() for part in allow_cidrs.split(",") if part.strip()]
    return {"rules": [{"cidrs": {"cidrs": public_ipv4_allowlist() + extra}}]}


def render_actor_template(
    *,
    atespace: str,
    image: str,
    env: dict[str, str],
    cpu: str,
    memory: str,
    storage_location: str,
    sandbox_config: str,
    worker_labels: dict[str, str],
) -> dict:
    """The protojson ActorTemplate for a box. Named by content: templates are immutable."""
    from _sdk.agent_session import NEVER, SessionSpec, container_spec, keepalive_script

    if "@sha256:" not in image:
        raise ValueError(f"substrate needs a digest-pinned image, got {image!r}")
    # NEVER: the keep-alive measures idleness against tmux activity stamps that
    # ride the snapshot while the wall clock jumps on restore, so any box
    # suspended longer than its timeout reaps itself within 5 s of resuming.
    # Idle becomes a suspend decision made outside the box.
    session = SessionSpec(image=image, idle_timeout_seconds=NEVER, working_dir=WORKSPACE)
    box = container_spec(session)
    merged_env = {item["name"]: item["value"] for item in box["env"]}
    merged_env.update(env)
    merged_env.setdefault("TERM", "xterm-256color")
    if len(merged_env) > ENV_MAX_ITEMS:
        raise ValueError(
            f"substrate allows {ENV_MAX_ITEMS} env vars per container, box needs {len(merged_env)}"
        )
    spec = {
        "workerSelector": {"matchLabels": dict(worker_labels)},
        "containers": [
            {
                "name": "agent-box",
                "image": image,
                "command": [
                    "/bin/sh",
                    "-c",
                    _startup_script(),
                    "box",
                    _payload(_SHIM.read_bytes()),
                    _payload(keepalive_script(session).encode()),
                ],
                "env": [{"name": k, "value": v} for k, v in sorted(merged_env.items())],
                "wakeupProbe": {"httpGet": {"path": "/readyz", "port": SHIM_PORT}},
                "volumeMounts": [{"name": "workspace", "mountPath": WORKSPACE}],
                "securityContext": {"capabilities": {"add": list(RUNTIME_DEFAULT_CAPS)}},
            }
        ],
        "volumes": [{"name": "workspace", "durableDir": {}}],
        "resources": {"limits": [{"name": "cpu", "quantity": cpu}, {"name": "memory", "quantity": memory}]},
        "snapshotsConfig": {
            "onPause": "SNAPSHOT_CONTENT_SCOPE_FULL",
            "onCommit": "SNAPSHOT_CONTENT_SCOPE_FULL",
            "storageLocation": storage_location,
        },
        "sandboxConfig": {"sandboxClass": "SANDBOX_CLASS_GVISOR", "configName": sandbox_config},
    }
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]
    return {"metadata": {"atespace": atespace, "name": f"astro-box-{digest}"}, **spec}


def _split(external_id: str) -> tuple[str, str]:
    atespace, _, name = external_id.partition("/")
    if not atespace or not name:
        raise ValueError(f"not a substrate actor id: {external_id!r}")
    return atespace, name


class SubstrateSpawner(ContainerSpawner):
    """Agent boxes as substrate actors. external_id is ``<atespace>/<actor>``."""

    def __init__(
        self, cluster=None, namespace: str = "default", *, transport=None, config: dict | None = None
    ):
        self._cluster = cluster
        self._namespace = namespace
        self._config = config
        self._ate = transport

    # -- configuration -----------------------------------------------------

    def _cfg(self) -> dict:
        if self._config is None:
            from django.conf import settings

            self._config = {
                "context": settings.SUBSTRATE_KUBE_CONTEXT,
                "storage_location": settings.SUBSTRATE_SNAPSHOT_LOCATION,
                "sandbox_config": settings.SUBSTRATE_SANDBOX_CONFIG,
                "worker_labels": dict(
                    pair.split("=", 1) for pair in settings.SUBSTRATE_WORKER_LABELS.split(",") if "=" in pair
                ),
                "cpu": settings.AGENT_POD_CPU_LIMIT,
                "memory": settings.AGENT_POD_MEMORY_LIMIT,
                "allow_cidrs": "",
            }
            try:
                from constance import config as constance

                self._config["allow_cidrs"] = str(constance.AGENT_EGRESS_ALLOW_CIDRS or "")
            except Exception:  # constance is optional outside the app
                pass
        return self._config

    def _transport(self):
        if self._ate is None:
            self._ate = KubectlAte(self._cfg()["context"])
        return self._ate

    def _atespace(self) -> str:
        name = f"astro-{self._namespace}"[:NAME_MAX_CHARS].rstrip("-")
        ate = self._transport()
        if ate("get", "atespace", name, "-o", "json") is None:
            ate("create", "atespace", name)
        return name

    # -- ContainerSpawner --------------------------------------------------

    def spawn(self, task: AgentTask) -> SpawnResult:
        """Create the box's actor, suspended at its template's golden snapshot.

        No resume here: the first attach resumes it. Creating costs a database
        row; the template (and its golden snapshot) is shared by every box
        with the same image, env and size.
        """
        from astrolift_dispatch.agent_secrets import effective_secret_refs, env_var_entries
        from astrolift_dispatch.spawners.k8s_job import _resolve_base_image

        spec = getattr(task, "environment_spec", None)
        if getattr(spec, "run_as_non_root", False):
            return SpawnResult(external_id="", ok=False, error=NON_ROOT_UNSUPPORTED)
        if effective_secret_refs(spec):
            return SpawnResult(external_id="", ok=False, error=SECRETS_UNSUPPORTED)
        image = _resolve_base_image(task.agent_definition, spec) if task.agent_definition else ""
        env = {entry["name"]: entry["value"] for entry in env_var_entries(getattr(spec, "env_vars", None))}
        actor = f"agent-task-{str(task.guid).replace('-', '')}"
        cfg = self._cfg()
        if not cfg["storage_location"]:
            return SpawnResult(external_id="", ok=False, error="SUBSTRATE_SNAPSHOT_LOCATION is not set")
        try:
            atespace = self._atespace()
            template = render_actor_template(
                atespace=atespace,
                image=image,
                env=env,
                cpu=cfg["cpu"],
                memory=cfg["memory"],
                storage_location=cfg["storage_location"],
                sandbox_config=cfg["sandbox_config"],
                worker_labels=cfg["worker_labels"],
            )
            ate = self._transport()
            name = template["metadata"]["name"]
            if ate("get", "actor-template", name, "-a", atespace, "-o", "json") is None:
                ate("create", "actor-template", "-f", "-", stdin=json.dumps(template))
            if ate("get", "actor", actor, "-a", atespace, "-o", "json") is None:
                ate("create", "actor", actor, "-a", atespace, "--template", name, "-o", "json")
            policy = render_egress_policy(cfg["allow_cidrs"])
            if ate("get", "egress-policy", actor, "-a", atespace, "-o", "json") in (None, {}):
                ate("create", "egress-policy", actor, "-a", atespace, "-f", "-", stdin=json.dumps(policy))
        except (SubstrateError, ValueError, subprocess.SubprocessError) as exc:
            logger.warning("substrate_spawner: spawn failed for %s: %s", actor, exc)
            return SpawnResult(external_id="", ok=False, error=str(exc))
        return SpawnResult(external_id=f"{atespace}/{actor}")

    def reserve_input_wait(self, task: AgentTask, seconds: int) -> None:
        # No wall-clock cap on an actor; a suspended box costs no worker.
        return None

    def status(self, external_id: str) -> TaskStatus:
        atespace, name = _split(external_id)
        try:
            actor = self._transport()("get", "actor", name, "-a", atespace, "-o", "json")
        except SubstrateError as exc:
            return TaskStatus(failed=True, error_message=str(exc))
        if actor is None:
            return TaskStatus(failed=True, error_message=f"actor {external_id} no longer exists")
        state = (actor.get("status") or {}).get("state", "")
        if state in _LIVE:
            return TaskStatus(running=True)
        return TaskStatus(
            failed=True, error_message=f"actor {external_id} is {state or 'in an unknown state'}"
        )

    def stop(self, external_id: str, *, expected_task_guid: str | None = None) -> None:
        atespace, name = _split(external_id)
        if expected_task_guid and name != f"agent-task-{str(expected_task_guid).replace('-', '')}":
            raise RuntimeError(f"actor {external_id} does not belong to task {expected_task_guid}")
        # Deleting the actor deletes the snapshot it owns; any_state stops a
        # running box. The template is shared and stays.
        self._transport()("delete", "actor", name, "-a", atespace, "--any-state")

    def confirm_stopped(self, external_id: str) -> bool:
        atespace, name = _split(external_id)
        return self._transport()("get", "actor", name, "-a", atespace, "-o", "json") is None

    # -- substrate-only ----------------------------------------------------

    def suspend(self, external_id: str) -> str:
        """Snapshot the box and free its worker. Returns the actor state."""
        atespace, name = _split(external_id)
        actor = self._transport()("suspend", "actor", name, "-a", atespace, "-o", "json") or {}
        return (actor.get("status") or {}).get("state", "")

    def resume(self, external_id: str) -> str:
        """Put the box back on a worker. Returns the worker pod it landed on."""
        atespace, name = _split(external_id)
        actor = self._transport()("resume", "actor", name, "-a", atespace, "-o", "json") or {}
        return ((actor.get("status") or {}).get("workerAssignment") or {}).get("workerPod", "")

    @staticmethod
    def attach_target(external_id: str) -> dict[str, str]:
        """How a relay reaches the box: a WebSocket to atenet-router.

        A K8s exec cannot do it: the worker pod changes on every resume and an
        exec into it lands outside the sandbox. The router resumes a suspended
        box on the upgrade request itself.
        """
        _split(external_id)
        return {
            "url": "ws://atenet-router.ate-system.svc/attach",
            "header": "ate-target-actor",
            "value": external_id,
        }
