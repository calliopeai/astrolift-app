"""Runtime for the agent-box (#128): render it, start it, reap it.

This is the wiring the ticket is actually about. ``_sdk.agent_session`` knows
what an attachable container looks like and nothing else; this module is what
calls it, hands it an image and a secret packet, and puts the result on a
cluster.

Shape of the k8s object
-----------------------
A ``batch/v1`` Job with ``backoffLimit: 0`` and ``restartPolicy: Never``,
which reads oddly for something whose whole point is to stay up. It is the
only shape that lets the box die. A Deployment restarts a container that
exits, so the idle timeout would fire, the pod would come straight back, and
a forgotten box would burn a node forever while looking healthy — the exact
failure the timeout exists to prevent. With a Job, the keep-alive loop
exiting is a completion: the pod terminates, the node is freed, and the
reaper below settles the row.

Deliberately no ``activeDeadlineSeconds``. That is the batch cap the agent
Job path uses to bound a runaway task, and a box is not bounded by wall clock
— it is bounded by not being used. Capping it would kill a box mid-session.

Everything that resolves an image, a namespace or a secret is borrowed from
the agent-task dispatch path rather than reimplemented, so a box and a task
land in the same namespace, read the same secret store, and resolve the same
runtime catalog.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from _sdk.agent_session import SESSION_NAME, SessionSpec, attach_argv, container_spec
from _sdk.k8s_naming import agent_namespace, dns_label
from django.utils import timezone

log = logging.getLogger("astrolift_agents.services.agent_box")

#: How long a finished box's Job object sticks around before Kubernetes
#: garbage-collects it. Long enough for the reaper's next sweep to observe the
#: completion and settle the row; short enough that finished boxes don't
#: accumulate as clutter in the namespace.
FINISHED_JOB_TTL_SECONDS = 600

#: How much of a dead box's own output is worth keeping on the row (#131).
#:
#: ``last_error`` is a diagnosis rendered inline — one column in
#: ``astro box ls``, one block in the Boxes tab — not a log archive; the
#: platform already has a log surface for the archive. The line count is
#: what the driver is *asked* for, so a chatty box never streams more than
#: this into the reaper in the first place, and the character budget is
#: what survives onto the row, keeping the whole message inside the
#: 2000-char ceiling every other write in this module truncates to. The
#: excerpt keeps its tail rather than its head: a container that dies on
#: startup says why with the last thing it prints.
BOX_FAILURE_LOG_LINES = 20
BOX_FAILURE_LOG_CHARS = 1200

#: The ceiling every ``last_error`` write in this module shares.
LAST_ERROR_MAX_CHARS = 2000

#: Env the platform owns on every box. Registered as reserved agent
#: environment names so an env spec cannot shadow them.
BOX_ENV_MARKER = "ASTROLIFT_AGENT_BOX"
BOX_ENV_GUID = "ASTROLIFT_AGENT_BOX_GUID"
BOX_ENV_SESSION = "ASTROLIFT_TMUX_SESSION"

#: How long a box's payload URL stays valid (#1877). The box fetches it once,
#: as its pod starts, so this covers scheduling and the image pull, not the
#: session.
BOX_PAYLOAD_URL_TTL_SECONDS = 3600


class AgentBoxError(RuntimeError):
    """A box could not be started, stopped or observed."""


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def box_job_name(box) -> str:
    """Deterministic Job name for a box.

    Derived through the bounded DNS-label helper rather than interpolated:
    the same reason ``agent_namespace`` exists (#1379). Keyed on the guid, not
    the slug, so renaming or re-creating a box can never collide with the
    object a previous box left behind.

    UUIDv7 prefixes contain only time; truncating them collides across parallel
    boxes. Use the full hex (32 chars fits a 63-char DNS label).
    """
    return dns_label("agent-box", str(box.guid).replace("-", ""))


def box_secret_name(job_name: str) -> str:
    """Per-box Secret name, matched by the render and the teardown path."""
    return f"{job_name}-secrets"


def box_namespace(box) -> str:
    """The per-org agent namespace a box runs in.

    The same namespace agent tasks land in, derived through the same bounded
    helper — never an f-string.
    """
    return agent_namespace(box.organization.slug)


def box_attach_command(box) -> list[str]:
    """What ``astro exec --app <box>`` should run to join the session.

    ``SESSION_NAME`` rather than a per-box value: this string has to match
    the ``has-session`` target the in-pod keep-alive loop polls and the
    session ``container_spec`` creates. A box that could name its own
    would let those three disagree, and the way that fails is an attach
    command that connects to nothing (#1470).
    """
    return attach_argv(SESSION_NAME)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _identity_env(box) -> dict[str, str]:
    return {
        BOX_ENV_MARKER: "1",
        BOX_ENV_GUID: str(box.guid),
        BOX_ENV_SESSION: SESSION_NAME,
    }


def _merge_env(*sources: list[dict]) -> list[dict]:
    """Collapse env lists by name, later sources winning.

    Kubernetes rejects a container whose env carries duplicate names under
    server-side apply, and relying on runtime ordering for precedence is
    opaque. Same rule the Job spawner's dedupe applies, applied once here
    because a box resolves its secrets before rendering rather than after.
    """
    by_name: dict[str, dict] = {}
    for source in sources:
        for entry in source:
            name = entry.get("name")
            if name:
                by_name[name] = entry
    return list(by_name.values())


def render_agent_box_job(
    *,
    box,
    image: str,
    namespace: str,
    job_name: str,
    secret_env_names: list[str] | None = None,
    model_service_account: str = "",
    model_env: list[dict] | None = None,
    payload_env: list[dict] | None = None,
    model_gateway=None,
) -> dict:
    """The Job manifest for a box, built around ``container_spec()``.

    The container — its command, its args, ``stdin``/``tty``, its working
    directory — comes from ``_sdk.agent_session`` verbatim. Provider defaults,
    spec environment and resolved secret references come from the control plane;
    secret values remain in the per-box Secret, not in the pod spec.

    Precedence is managed-model defaults, spec env, secret refs, then the platform's own
    identity env last, so nothing an operator writes into an env spec can
    shadow the variables the box needs to describe itself.

    ``payload_env`` is the agent's payload delivery, given when the spec sets
    ``box_workspace`` (#1877). With it the box runs the runner's workspace
    setup into its working directory before the session starts, and that env
    ranks with the identity env, so a spec cannot redirect the setup either.
    Without it the manifest is the bare box it always was.

    ``model_gateway`` wires the box to the Zentinelle gateway (#1851): the
    wiring wins over every source, and another provider's credential or
    endpoint from any source raises ``ModelGatewayError``.
    """
    from astrolift_dispatch.agent_secrets import agent_container_env, secret_env_entries
    from astrolift_dispatch.pod_hardening import harden_agent_pod

    spec = box.environment_spec
    secret_name = box_secret_name(job_name)

    session = SessionSpec(
        image=image,
        session_name=SESSION_NAME,
        idle_timeout_seconds=int(box.idle_timeout_seconds),
        env=_identity_env(box),
        workspace_setup=payload_env is not None,
    )
    container = container_spec(session)

    # The setup targets the session's own working directory, so the attach
    # lands in the workspace it built and the IDE keeps its /workspace.
    workspace_env = []
    if payload_env is not None:
        workspace_env = [*payload_env, {"name": "ASTROLIFT_WORKSPACE", "value": container["workingDir"]}]

    live_refs = [{"env_var": name, "uri": ""} for name in sorted(secret_env_names or [])]
    container["env"] = _merge_env(
        model_env or [],
        agent_container_env(spec, secret_name),
        secret_env_entries(secret_name, live_refs),
        workspace_env,
        container["env"],
    )
    if model_gateway is not None:
        model_gateway.wire_container(container, secret_name=secret_name)

    # No image creates the session's working directory, so the runtime would
    # make it root-owned and a non-root box could not write where it starts.
    # An emptyDir there is writable in both modes (fsGroup covers non-root).
    container["volumeMounts"] = [{"name": "workspace", "mountPath": container["workingDir"]}]

    labels = {
        "astrolift.dev/workload-kind": "agent-box",
        "astrolift.dev/agent-box": str(box.guid),
        # The platform pod/log surfaces select on astrolift.dev/app; keying it
        # to the box guid makes a box's pod discoverable by the same code that
        # finds an agent task's pod, with no box-specific selector anywhere.
        "astrolift.dev/app": str(box.guid),
    }
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "namespace": namespace, "labels": dict(labels)},
        "spec": {
            "backoffLimit": 0,
            "completions": 1,
            "parallelism": 1,
            "ttlSecondsAfterFinished": FINISHED_JOB_TTL_SECONDS,
            "template": {
                "metadata": {"labels": dict(labels)},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [container],
                    "volumes": [{"name": "workspace", "emptyDir": {}}],
                    **({"serviceAccountName": model_service_account} if model_service_account else {}),
                },
            },
        },
    }
    harden_agent_pod(job["spec"]["template"]["spec"], non_root=bool(getattr(spec, "run_as_non_root", False)))
    return job


# ---------------------------------------------------------------------------
# Start / stop
# ---------------------------------------------------------------------------


def resolve_box_image(box) -> str:
    """Image for a box, through the same resolution the Job spawner uses."""
    from astrolift_dispatch.spawners.k8s_job import _resolve_base_image

    if box.environment_spec_id is None and box.image:
        return box.image
    return _resolve_base_image(box.agent_definition, box.environment_spec)


def start_agent_box(box) -> None:
    """Apply the box's manifests to the org's managed cluster.

    Raises :class:`AgentBoxError` with one actionable message on any failure;
    the caller turns that into a MutationResult error rather than a 500. The
    box row is stamped FAILED on the way out so a failed ensure is visible
    rather than silently PENDING forever.
    """
    from astrolift_agents.models import AgentBox
    from astrolift_agents.services.agent_cluster import (
        NoAgentClusterError,
        resolve_agent_cluster,
    )
    from astrolift_dispatch.agent_secrets import (
        AgentSecretResolutionError,
        resolve_task_secret_manifest,
    )
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    job_name = box_job_name(box)
    namespace = box_namespace(box)

    spec = box.environment_spec
    if spec is not None and spec.run_as_non_root and spec.allow_install:
        from astrolift_dispatch.pod_hardening import NON_ROOT_INSTALL_CONFLICT

        _fail(box, NON_ROOT_INSTALL_CONFLICT)
        raise AgentBoxError(NON_ROOT_INSTALL_CONFLICT)

    try:
        cluster = resolve_agent_cluster(box.organization)
    except NoAgentClusterError as exc:
        _fail(box, str(exc))
        raise AgentBoxError(str(exc)) from exc

    try:
        image = resolve_box_image(box)
    except Exception as exc:  # noqa: BLE001 — surface one readable failure
        _fail(box, f"could not resolve an image for the box: {exc}")
        raise AgentBoxError(f"could not resolve an image for the box: {exc}") from exc

    # A spec that asks for the model gateway (#1851) gets a box only behind it.
    gateway = None
    if spec is not None and spec.model_gateway:
        from astrolift_dispatch.model_gateway import ModelGatewayError, resolve_model_gateway

        try:
            gateway = resolve_model_gateway(cluster=cluster, organization=box.organization, spec=spec)
        except ModelGatewayError as exc:
            _fail(box, str(exc))
            raise AgentBoxError(str(exc)) from exc
    elif box.model_gateway_agent_id:
        # Restarted without the gateway: the previous incarnation's key was
        # revoked when it stopped, and this one holds none.
        box.model_gateway_agent_id = ""
        box.model_gateway_expires_at = None
        box.model_gateway_lifetime_ends_at = None
        box.model_gateway_connection = None
        box.save(
            update_fields=[
                "model_gateway_agent_id",
                "model_gateway_expires_at",
                "model_gateway_lifetime_ends_at",
                "model_gateway_connection",
                "updated_at",
                "version",
            ]
        )

    # The env-spec secret packet. Preflighted against the live store so a box
    # whose ANTHROPIC_API_KEY is missing fails to start with that sentence,
    # instead of coming up warm and failing the first time someone attaches.
    try:
        secret_manifest = resolve_task_secret_manifest(
            cluster=cluster,
            spec=box.environment_spec,
            secret_name=box_secret_name(job_name),
            namespace=namespace,
            task_guid=str(box.guid),
            exclude=gateway.owned_env_names if gateway is not None else frozenset(),
        )
    except AgentSecretResolutionError as exc:
        _fail(box, str(exc))
        raise AgentBoxError(str(exc)) from exc

    model_wiring = None
    if box.environment_spec is not None and box.environment_spec.managed_model:
        from astrolift_dispatch.agent_model import ManagedModelError, resolve_managed_model_wiring

        try:
            model_wiring = resolve_managed_model_wiring(cluster=cluster, namespace=namespace)
        except ManagedModelError as exc:
            _fail(box, str(exc))
            raise AgentBoxError(str(exc)) from exc

    payload_env = None
    if spec is not None and spec.box_workspace:
        try:
            payload_env = box_payload_env(box)
        except AgentBoxError as exc:
            _fail(box, str(exc))
            raise

    secret_env_names = sorted((secret_manifest or {}).get("stringData") or {})
    from astrolift_dispatch.model_gateway import ModelGatewayError

    try:
        job = render_agent_box_job(
            box=box,
            image=image,
            namespace=namespace,
            job_name=job_name,
            secret_env_names=secret_env_names,
            model_service_account=model_wiring.service_account if model_wiring else "",
            model_env=model_wiring.env if model_wiring else None,
            payload_env=payload_env,
            model_gateway=gateway,
        )
    except ModelGatewayError as exc:
        _fail(box, str(exc))
        raise AgentBoxError(str(exc)) from exc

    gateway_key = None
    if gateway is not None:
        gateway_key = _mint_box_gateway_key(box, gateway)
        from astrolift_dispatch.model_gateway import with_gateway_key

        secret_manifest = with_gateway_key(
            secret_manifest,
            gateway_key,
            secret_name=box_secret_name(job_name),
            namespace=namespace,
            owner_guid=str(box.guid),
        )

    from astrolift_dispatch.model_gateway import redact

    try:
        driver = _driver_for_cluster(cluster)
        ctx = _context_for_cluster(cluster)
        ensure_ns = getattr(driver, "ensure_namespace", None)
        if callable(ensure_ns):
            ensure_ns(
                ctx.slug,
                namespace,
                {"astrolift.io/managed-by": "platform", "astrolift.io/component": "agents"},
                {},
            )
        from astrolift_dispatch.agent_network_fence import agent_fence_manifests

        manifests = (
            ([model_wiring.service_account_manifest] if model_wiring else [])
            + ([secret_manifest] if secret_manifest else [])
            + agent_fence_manifests(cluster, namespace)
            + [job]
        )
        result = driver.apply_manifests(ctx.slug, namespace, manifests)
    except Exception as exc:  # noqa: BLE001 — one failure mode for the caller
        _revoke_box_gateway_key(box)
        message = redact(f"applying the box manifests failed: {exc}", gateway_key)
        _fail(box, message)
        # Chained, the original exception's message could carry the key.
        cause = exc if gateway_key is None else None
        raise AgentBoxError(message) from cause

    if not getattr(result, "ok", False):
        detail = redact(str(result.summary() if hasattr(result, "summary") else "apply failed"), gateway_key)
        # A partial apply can leave the plaintext-bearing Secret behind.
        _delete_box_objects(cluster, namespace, job_name)
        _revoke_box_gateway_key(box)
        _fail(box, detail)
        raise AgentBoxError(detail)

    box.status = AgentBox.Status.PROVISIONING
    box.image = image[:512]
    box.external_id = job_name
    box.namespace = namespace
    # Cleared, not left: a settled box restarts under its existing slug, and
    # the pod from its previous incarnation is gone. Carrying that name would
    # hand a client a dead pod to dial until the next sweep re-stamps it.
    box.pod_name = ""
    box.started_at = timezone.now()
    box.ended_at = None
    box.last_error = ""
    box.save(
        update_fields=[
            "status",
            "image",
            "external_id",
            "namespace",
            "pod_name",
            "started_at",
            "ended_at",
            "last_error",
            "updated_at",
            "version",
        ]
    )
    log.info("agent_box: started %s as Job %s in %s", box.slug, job_name, namespace)


def _mint_box_gateway_key(box, gateway):
    """Mint the box's gateway key (#1851), recording its agent and the
    connection that mints it before the call, so that stopping the box
    revokes it through that install whatever happens next."""
    from astrolift_dispatch.model_gateway import (
        ModelGatewayError,
        box_agent_id,
        box_key_ttl,
        revoke_run_key,
    )

    agent_id = box_agent_id(box)
    box.model_gateway_agent_id = agent_id
    box.model_gateway_connection = gateway.connection
    box.save(update_fields=["model_gateway_agent_id", "model_gateway_connection", "updated_at", "version"])
    ttl = box_key_ttl(box)
    agent = box.agent_definition
    deployment = getattr(agent, "slug", "") or getattr(box.environment_spec, "slug", "")
    try:
        key = gateway.mint(
            agent_id=agent_id, ttl_seconds=ttl, name=f"{deployment} box {box.slug}", deployment_id=deployment
        )
    except ModelGatewayError as exc:
        if exc.maybe_minted:
            revoke_run_key(connection_id=gateway.connection.pk, agent_id=agent_id, expect_missing=True)
        _fail(box, str(exc))
        raise AgentBoxError(str(exc)) from exc
    box.model_gateway_expires_at = key.expires_at or timezone.now() + timedelta(seconds=ttl)
    box.model_gateway_lifetime_ends_at = key.lifetime_ends_at
    box.save(
        update_fields=["model_gateway_expires_at", "model_gateway_lifetime_ends_at", "updated_at", "version"]
    )
    return key


def _revoke_box_gateway_key(box) -> None:
    """Best effort, through the install that minted the key; its expiry is the backstop. Never raises."""
    if not box.model_gateway_agent_id:
        return
    from astrolift_dispatch.model_gateway import revoke_run_key

    revoke_run_key(connection_id=box.model_gateway_connection_id, agent_id=box.model_gateway_agent_id)


def _renew_box_gateway_key(box) -> None:
    """Keep a live box's gateway key ahead of its expiry (#1851). Never raises.

    Renewed once half of its window is left, so a failure has the other half
    to succeed on a later sweep. A box the reaper no longer sees live is not
    renewed, and its key lapses within one window. Nothing is attempted for a
    key that already expired or reached its lifetime end: only a restart
    mints a new one. Any failure is logged and stays with this box, so the
    sweep carries on for the others.
    """
    try:
        _renew_box_gateway_key_if_due(box)
    except Exception:  # noqa: BLE001 - one box's renewal must not end the sweep
        log.warning("agent_box: renewing the gateway key of box %s failed", box.slug, exc_info=True)


def _renew_box_gateway_key_if_due(box) -> None:
    if not box.model_gateway_agent_id or box.model_gateway_expires_at is None:
        return
    from astrolift_dispatch.model_gateway import ModelGatewayError, box_key_ttl, renew_run_key

    ttl = box_key_ttl(box)
    now = timezone.now()
    expires_at = box.model_gateway_expires_at
    lifetime_ends_at = box.model_gateway_lifetime_ends_at
    if expires_at <= now or (lifetime_ends_at is not None and expires_at >= lifetime_ends_at):
        return
    if expires_at - now > timedelta(seconds=ttl / 2):
        return
    try:
        renewed_until, lifetime_ends_at = renew_run_key(
            connection_id=box.model_gateway_connection_id,
            agent_id=box.model_gateway_agent_id,
            ttl_seconds=ttl,
        )
    except ModelGatewayError as exc:
        log.warning("agent_box: could not renew the gateway key of box %s: %s", box.slug, exc)
        return
    box.model_gateway_expires_at = renewed_until or now + timedelta(seconds=ttl)
    box.model_gateway_lifetime_ends_at = lifetime_ends_at
    box.save(
        update_fields=["model_gateway_expires_at", "model_gateway_lifetime_ends_at", "updated_at", "version"]
    )
    if lifetime_ends_at is not None and box.model_gateway_expires_at >= lifetime_ends_at:
        log.warning(
            "agent_box: the gateway key of box %s reaches Zentinelle's key lifetime at %s and cannot be "
            "renewed past it; restart the box before then for a new key",
            box.slug,
            lifetime_ends_at.isoformat(),
        )


def box_payload_env(box) -> list[dict]:
    """The payload env for a box whose spec sets up its workspace (#1877).

    The payload is the agent's: the same READY Brief a task of that agent
    runs, delivered by the same code. The agent is the box's own when it was
    ensured with one, otherwise the spec's, which is the registered agent
    that carries the spec's slug, the pairing manifest sync creates. Every
    missing link refuses the start with the one it is, because a box that
    came up without the workspace its spec asked for would look healthy and
    be wrong.
    """
    from astrolift_agents.models import Brief
    from astrolift_dispatch.brief_injector import payload_env

    spec = box.environment_spec
    agent = box.agent_definition or _spec_agent(spec)
    brief = agent.brief
    if brief is None or brief.status != Brief.Status.READY:
        raise AgentBoxError(
            f"environment spec {spec.slug} sets up the box workspace, but agent {agent.slug} has no "
            "ready Agent Package Brief; re-sync its source repo"
        )
    try:
        env = payload_env(brief=brief, organization=box.organization, expires_in=BOX_PAYLOAD_URL_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001 — one readable failure for the caller
        raise AgentBoxError(f"could not deliver agent {agent.slug}'s payload to the box: {exc}") from exc
    if not env:
        raise AgentBoxError(
            f"environment spec {spec.slug} sets up the box workspace, but agent {agent.slug} has no "
            "payload bundle; declare [workspace] or [package] in its astrolift.toml and re-sync it"
        )
    return env


def _spec_agent(spec):
    from astrolift_registry.models import Workload

    agents = list(
        Workload.objects.filter(
            slug=spec.slug,
            kind=Workload.Kind.AGENT,
            registered_app__organization_id=spec.organization_id,
            registered_app__deleted_at__isnull=True,
            deleted_at__isnull=True,
        ).select_related("brief")[:2]
    )
    if not agents:
        raise AgentBoxError(
            f"environment spec {spec.slug} sets up the box workspace, but no registered agent is named "
            f"{spec.slug}; ensure the box with an agent, or turn off box_workspace"
        )
    if len(agents) > 1:
        raise AgentBoxError(
            f"environment spec {spec.slug} sets up the box workspace, but more than one registered agent "
            f"is named {spec.slug}; ensure the box with the agent to use"
        )
    return agents[0]


def stop_agent_box(
    box, *, status: str | None = None, reason: str = "", require_teardown: bool = False
) -> None:
    """Delete the box's cluster objects and settle the row.

    Observing a settled workload records its terminal state even when cluster
    cleanup fails, preserving the cleanup error on the row. Explicit destruction
    requires teardown to succeed so a failed stop remains visible and retryable.

    ``reason`` is the box's own cause of death, when the caller observed
    one (#131). It leads ``last_error`` because it is what an operator is
    looking for; a teardown failure is appended after it rather than
    replacing it, since "it died of X and the Job is still on the cluster"
    is two facts and losing either one costs a round-trip.
    """
    from astrolift_agents.models import AgentBox
    from astrolift_agents.services.agent_cluster import resolve_agent_cluster

    target = status or AgentBox.Status.STOPPED
    error = ""
    if box.external_id:
        try:
            cluster = resolve_agent_cluster(box.organization)
            _delete_box_objects(cluster, box.namespace or box_namespace(box), box.external_id)
        except Exception as exc:  # noqa: BLE001
            error = f"cluster teardown did not complete: {exc}"
            log.warning("agent_box: %s for box %s", error, box.slug)
            if require_teardown:
                # A pod left behind loses its model access all the same.
                _revoke_box_gateway_key(box)
                box.last_error = error[:LAST_ERROR_MAX_CHARS]
                box.save(update_fields=["last_error", "updated_at", "version"])
                raise AgentBoxError(error) from exc
    _revoke_box_gateway_key(box)

    box.status = target
    box.ended_at = timezone.now()
    box.last_error = "\n".join(p for p in (reason, error) if p)[:LAST_ERROR_MAX_CHARS]
    box.save(update_fields=["status", "ended_at", "last_error", "updated_at", "version"])


def _delete_box_objects(cluster, namespace: str, job_name: str) -> None:
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    refs = [
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": job_name, "namespace": namespace},
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": box_secret_name(job_name), "namespace": namespace},
        },
    ]
    # Job deletion without a propagation policy may orphan its running pod.
    # Keep the Job until Kubernetes has deleted its dependents.
    result = driver.delete_manifests(ctx.slug, namespace, refs, propagation_policy="Foreground")
    if result is not None and not getattr(result, "ok", True):
        detail = result.summary() if hasattr(result, "summary") else "delete failed"
        raise AgentBoxError(str(detail))


def _fail(box, message: str) -> None:
    from astrolift_agents.models import AgentBox

    box.status = AgentBox.Status.FAILED
    box.ended_at = timezone.now()
    box.last_error = message[:LAST_ERROR_MAX_CHARS]
    box.save(update_fields=["status", "ended_at", "last_error", "updated_at", "version"])


# ---------------------------------------------------------------------------
# Reaper
# ---------------------------------------------------------------------------


def observe_box(box) -> str | None:
    """The status a live box's cluster object says it should have.

    ``None`` means "leave it alone" — an unreachable cluster or a Job that
    has not settled must never be read as a reason to change the row.
    """
    from astrolift_agents.models import AgentBox
    from astrolift_agents.services.agent_cluster import resolve_agent_cluster
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    if not box.external_id:
        return None
    cluster = resolve_agent_cluster(box.organization)
    namespace = box.namespace or box_namespace(box)
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    status = driver.get_workload_status(
        ctx.slug,
        namespace,
        "Job",
        box.external_id,
    )
    conditions = getattr(status, "conditions", None) or []
    complete = any(str(c.get("type")) == "Complete" and str(c.get("status")) == "True" for c in conditions)
    failed = any(str(c.get("type")) == "Failed" and str(c.get("status")) == "True" for c in conditions)
    if failed:
        return AgentBox.Status.FAILED.value
    if complete:
        # The keep-alive loop ended the session: either the idle timeout
        # fired or whoever was attached exited the shell. Both mean the box
        # is gone and the node is free.
        return AgentBox.Status.EXPIRED.value
    # Ready pods only. ``desired_replicas`` is 1 from the instant the Job
    # exists, so counting it would call a box RUNNING — and therefore
    # attachable — before its pod had started (#133).
    if getattr(status, "ready_replicas", 0) > 0:
        _record_pod_name(box, cluster=cluster, namespace=namespace)
        return AgentBox.Status.RUNNING.value
    return None


def _record_pod_name(box, *, cluster, namespace: str) -> None:
    """Stamp the running box's pod onto the row.

    ``pod_name`` was declared, projected onto ``AstroliftAgentBox`` and
    never written by anything, so ``astro box ls`` rendered a blank
    column and every attach fell through to pod resolution even for a
    box that had been warm for an hour. A stamped name lets a client
    dial the relay directly, which is the difference between one
    round-trip and a pod lookup the caller may not be permitted to make.

    Not a substitute for pod resolution: nothing is stamped until the
    first sweep after the pod comes up, and the name is only as fresh as
    the last sweep. It is a fast path, and the resolver stays the
    fallback.

    ``WorkloadStatus`` carries replica counts and conditions, not pod
    names, so this is a second driver call rather than another field off
    the Job read. It is best-effort for the same reason the reap
    swallows an unreachable cluster: an observability failure is not a
    verdict about the box, and a blank name is what we already had.
    """
    from core.cluster_observability import list_app_pods

    try:
        pods = list_app_pods(cluster=cluster, namespace=namespace, app_slug=str(box.guid))
    except Exception:  # noqa: BLE001 — k8s lib raises many subtypes
        log.warning("agent_box: could not resolve a pod name for box %s", box.slug, exc_info=True)
        return

    # A box Job is backoffLimit 0 / restartPolicy Never, so there is one
    # pod per incarnation; prefer a ready one anyway so a terminating
    # leftover from a restart never wins.
    chosen = next((p for p in pods if getattr(p, "ready", False)), None) or next(iter(pods), None)
    name = (getattr(chosen, "name", "") or "")[:255]
    if not name or name == box.pod_name:
        return
    box.pod_name = name
    box.save(update_fields=["pod_name", "updated_at", "version"])


def describe_box_failure(box) -> str:
    """Why a box the reaper just called FAILED actually died (#131).

    The Job's ``Failed`` condition says a pod died; it never says of what.
    That sentence was only ever in the pod, so a box that came up and then
    hit ``tmux: not found`` settled with an empty ``last_error`` and the
    operator had to reach for ``kubectl`` — on the one class of failure
    they cannot infer from the request they made.

    Best-effort in the same shape as :func:`_record_pod_name`: this is
    observability, and observability failing is never a verdict about the
    box. Every layer degrades to the layer below rather than raising, so
    the worst case is a thinner sentence, never a changed reap outcome:

    * pod read fails      → one sentence saying the pod could not be read
    * log read fails      → terminated reason and exit code alone
    * everything works    → reason, exit code, and the log tail

    The pod is still there to read because the box Job carries
    ``ttlSecondsAfterFinished``, so the object outlives the container by
    long enough for the next sweep to see it.
    """
    from astrolift_agents.services.agent_cluster import resolve_agent_cluster
    from core.cluster_observability import list_app_pods

    namespace = box.namespace or box_namespace(box)
    try:
        cluster = resolve_agent_cluster(box.organization)
        pods = list_app_pods(cluster=cluster, namespace=namespace, app_slug=str(box.guid))
    except Exception:  # noqa: BLE001 — k8s lib and cluster lookup raise many subtypes
        log.warning("agent_box: could not read the failed pod for box %s", box.slug, exc_info=True)
        return "the box's Job reported Failed; its pod could not be read for a cause"

    pod = _dead_pod(pods)
    if pod is None:
        return "the box's Job reported Failed; no pod remained to read a cause from"

    parts = [_terminated_sentence(pod)]
    excerpt = _pod_log_excerpt(cluster=cluster, namespace=namespace, pod_name=pod.name)
    if excerpt:
        parts.append(excerpt)
    return "\n".join(parts)


def _dead_pod(pods):
    """The pod that carries the cause, out of what the namespace returned.

    A box Job is ``backoffLimit: 0`` / ``restartPolicy: Never``, so there
    is one pod per incarnation. Prefer one with a terminated container
    anyway, so a leftover from a previous incarnation that is still
    Running can never be mistaken for the corpse.
    """
    pods = [p for p in pods if getattr(p, "name", "")]
    return next((p for p in pods if _terminated_container(p) is not None), None) or next(iter(pods), None)


def _terminated_container(pod):
    """The pod's most informative terminated container, or ``None``.

    A container that exited non-zero outranks one that exited clean: an
    init or sidecar slot reporting ``Completed`` says nothing about why
    the box is gone, and on a box the interesting corpse is whichever
    slot refused to run.
    """
    statuses = [
        c for c in (getattr(pod, "container_statuses", None) or []) if getattr(c, "state", "") == "terminated"
    ]
    return next((c for c in statuses if (c.terminated_exit_code or 0) != 0), None) or next(
        iter(statuses), None
    )


def _terminated_sentence(pod) -> str:
    container = _terminated_container(pod)
    if container is None:
        phase = getattr(pod, "phase", "") or getattr(pod, "status", "") or "unknown"
        return f"pod {pod.name} is {phase}; no container reported a terminated state"

    detail = []
    if container.terminated_reason:
        detail.append(container.terminated_reason)
    if container.terminated_exit_code is not None:
        detail.append(f"exit {container.terminated_exit_code}")
    # A terminated container with neither field is a shape k8s should not
    # produce, but the sentence still has to name the container.
    suffix = f": {', '.join(detail)}" if detail else ""
    return f"container {container.name} terminated{suffix}"


def _pod_log_excerpt(*, cluster, namespace: str, pod_name: str) -> str:
    """The tail of a dead pod's output, capped, or ``""``.

    Bridged with ``async_to_sync`` for the same reason the agent-task log
    resolver does it: the reader is async and the reaper is not.
    """
    from asgiref.sync import async_to_sync

    from core.cluster_observability import fetch_pod_log_tail

    try:
        lines = async_to_sync(fetch_pod_log_tail)(
            cluster=cluster,
            namespace=namespace,
            pod_name=pod_name,
            tail=BOX_FAILURE_LOG_LINES,
        )
    except Exception:  # noqa: BLE001 — a log read must not cost us the reason
        log.warning("agent_box: could not read logs for failed pod %s", pod_name, exc_info=True)
        return ""

    body = "\n".join(line for line in lines if line and line.strip())
    if not body:
        return ""
    if len(body) > BOX_FAILURE_LOG_CHARS:
        body = "…" + body[-BOX_FAILURE_LOG_CHARS:]
    return body


def reap_agent_boxes() -> dict[str, int]:
    """One sweep: settle every live box against its Job.

    This is the control-plane half of the idle timeout. The in-pod loop is
    what actually frees the node; this is what notices, stamps the row
    EXPIRED, and deletes the leftover Secret so a plaintext-bearing object
    does not outlive the pod that needed it.

    Per-box try/except: one org's unreachable cluster must not abort the
    sweep for everyone else.
    """
    from astrolift_agents.models import AgentBox

    summary = {"evaluated": 0, "running": 0, "expired": 0, "failed": 0, "unchanged": 0, "errors": 0}
    boxes = (
        AgentBox.objects.filter(status__in=sorted(AgentBox.LIVE_STATUSES), deleted_at__isnull=True)
        .select_related("organization")
        .order_by("pk")
    )
    for box in boxes:
        summary["evaluated"] += 1
        try:
            observed = observe_box(box)
        except Exception:  # noqa: BLE001 — an unreachable cluster is not a verdict
            summary["errors"] += 1
            log.warning("agent_box: reap could not observe box %s", box.slug, exc_info=True)
            continue
        if observed is None or observed == box.status:
            summary["unchanged"] += 1
            _renew_box_gateway_key(box)
            continue
        if observed == AgentBox.Status.RUNNING.value:
            box.status = AgentBox.Status.RUNNING
            box.save(update_fields=["status", "updated_at", "version"])
            summary["running"] += 1
            _renew_box_gateway_key(box)
            continue
        failed = observed == AgentBox.Status.FAILED.value
        # Read the cause before teardown: the pod holding it is one of the
        # objects stop_agent_box is about to delete.
        reason = describe_box_failure(box) if failed else ""
        stop_agent_box(box, status=observed, reason=reason)
        summary["failed" if failed else "expired"] += 1
    return summary


# ---------------------------------------------------------------------------
# Ensure — the one call behind the IDE's button
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgentBoxEnsureError(RuntimeError):
    """A box could not be ensured. Carries the code the resolver reports."""

    code: str
    message: str
    field: str = ""

    def __str__(self) -> str:
        return self.message


def box_slug_for(*, environment_spec, agent, owner_id: int | None, image: str = "") -> str:
    """The address an ensured box gets, derived rather than generated.

    This is what makes ensure idempotent *in the database* rather than only
    in the lookup above it. The slug is a pure function of what the caller
    asked for, and ``(organization, slug)`` is unique among live rows, so two
    concurrent presses of the button collide on the constraint and the loser
    re-reads the winner's box. A random slug would let both succeed and leave
    the org paying for two nodes — the exact failure the verb exists to
    prevent.

    It is also what an IDE remembers between sessions, so it stays readable:
    the agent or spec it came from, suffixed with the owner only when there
    is one, because two people on the same spec must not share a box.

    ``image`` participates when a caller asked for a one-off box by image
    rather than by durable spec (astrolift-cli#84). It has to: the constraint
    the verb exists to satisfy is "the org must not grow a node per *press*",
    and pressing twice with the same image still yields one box. Pressing
    with a *different* image is a different request, and a node per distinct
    image is inherent to having asked for a different image.

    Hashed rather than embedded, because an image reference carries a
    registry host, a path and a tag or digest, none of which survives being
    squeezed into a slug — and a truncated one would collide two images that
    share a prefix, silently attaching a caller to the wrong box.
    """
    base = getattr(agent, "slug", "") or getattr(environment_spec, "slug", "")
    parts = ["box", str(base)]
    if not base and image:
        # No agent and no spec: the image is the only thing naming this box.
        parts = ["box", "img"]
    if owner_id is not None:
        parts.append(f"u{owner_id}")
    if image:
        import hashlib

        parts.append(hashlib.sha256(image.strip().encode("utf-8")).hexdigest()[:12])
    return "-".join(p for p in parts if p)[:200]


def ensure_agent_box(
    *,
    organization,
    environment_spec_slug: str = "",
    agent_slug: str = "",
    image: str = "",
    name: str = "",
    idle_timeout_seconds: int | None = None,
    owner=None,
):
    """Return the caller's box for this agent, starting one if it is not warm.

    Ensure semantics, which is the whole point: the IDE presses the button
    again and gets the box it already has. A live box is returned untouched —
    nothing is re-applied to the cluster, because re-applying a Job that is
    running is at best a no-op and at worst a restart of somebody's session.
    A settled box (reaped, stopped, failed) is restarted *under its existing
    slug*, so idle-reaping never invalidates the address a client stored.

    Raises :class:`AgentBoxEnsureError` for anything the caller did wrong and
    :class:`AgentBoxError` for anything the cluster did; both become a
    MutationResult failure rather than a 500.
    """
    from astrolift_agents.models import AgentEnvironmentSpec
    from astrolift_registry.models import Workload

    spec_slug = (environment_spec_slug or "").strip()
    agent_ref = (agent_slug or "").strip()
    image_ref = (image or "").strip()
    if not spec_slug and not agent_ref and not image_ref:
        raise AgentBoxEnsureError(
            "validation",
            "a box needs an agent, an environment spec, or an image to know what to run",
            "environmentSpecSlug",
        )

    if image_ref and spec_slug:
        # Mutually exclusive by construction, not by preference: a spec names
        # an image and so does this, and silently preferring one would make
        # the box's contents depend on which the caller happened to send
        # (astrolift-cli#84).
        raise AgentBoxEnsureError(
            "validation",
            "pass an environment spec or an image, not both: a spec already names an image",
            "image",
        )

    if idle_timeout_seconds is not None and idle_timeout_seconds < 0:
        raise AgentBoxEnsureError(
            "validation",
            "idleTimeoutSeconds cannot be negative; use 0 to mean never reap",
            "idleTimeoutSeconds",
        )

    spec = None
    if spec_slug:
        # Org-filtered explicitly: @tenant_scoped asserts a tenant, it does
        # not filter. A foreign spec slug would otherwise pull that org's
        # secret refs into a pod running in this one.
        spec = AgentEnvironmentSpec.objects.filter(
            organization=organization, slug=spec_slug, deleted_at__isnull=True
        ).first()
        if spec is None:
            raise AgentBoxEnsureError("not_found", "environment spec not found", "environmentSpecSlug")

    agent = None
    if agent_ref:
        agent = (
            Workload.objects.filter(
                slug=agent_ref,
                kind=Workload.Kind.AGENT,
                registered_app__organization=organization,
                registered_app__deleted_at__isnull=True,
                deleted_at__isnull=True,
            )
            .select_related("registered_app")
            .first()
        )
        if agent is None:
            raise AgentBoxEnsureError("not_found", "agent not found", "agentSlug")
        if agent.run_mode != Workload.RunMode.PERSISTENT:
            # Boxing a batch agent behind the operator's back would turn what
            # they configured into something that holds a node.
            raise AgentBoxEnsureError(
                "validation",
                (f"agent {agent.slug} is run mode {agent.run_mode}; only a persistent agent can back a box"),
                "agentSlug",
            )

    slug = box_slug_for(
        environment_spec=spec,
        agent=agent,
        owner_id=getattr(owner, "pk", None),
        image=image_ref,
    )
    box, created = _get_or_create_box(
        organization=organization,
        slug=slug,
        name=(name or "").strip(),
        spec=spec,
        agent=agent,
        owner=owner,
        idle_timeout_seconds=idle_timeout_seconds,
        image=image_ref,
    )
    if not created and box.is_live:
        return box

    start_agent_box(box)
    return box


def _get_or_create_box(
    *,
    organization,
    slug: str,
    name: str,
    spec,
    agent,
    owner,
    idle_timeout_seconds: int | None,
    image: str = "",
):
    """Find this caller's box or create it, tolerating a concurrent press.

    ``get_or_create`` under the live-slug uniqueness constraint: the loser of
    a race sees IntegrityError and re-reads the winner's row, so the button
    can be double-clicked without the org growing a second node.
    """
    from django.db import IntegrityError, transaction

    from astrolift_agents.models import AgentBox

    # ``AgentBox.objects`` is the soft-delete manager, so a retired box of the
    # same name is not resurrected here — it is history, and the constraint it
    # was released from lets a fresh one take the slug.
    box = AgentBox.objects.filter(organization=organization, slug=slug).first()
    created = False
    if box is None:
        fields = {
            "organization": organization,
            "slug": slug,
            "name": name or _default_box_name(spec, agent),
            "owner": owner,
            "environment_spec": spec,
            "agent_definition": agent,
            "status": AgentBox.Status.PENDING,
        }
        if image:
            # Frozen here rather than at spawn, because with no spec there is
            # nothing to freeze *from* later: the image reference the caller
            # passed is the only record of what this box runs
            # (astrolift-cli#84).
            fields["image"] = image
        if idle_timeout_seconds is not None:
            fields["idle_timeout_seconds"] = idle_timeout_seconds
        try:
            with transaction.atomic():
                box = AgentBox.objects.create(**fields)
                created = True
        except IntegrityError:
            # The other press won. Read its box rather than reporting a
            # database error for what is a successful ensure.
            box = AgentBox.objects.get(organization=organization, slug=slug)

    if created:
        return box, True

    # An existing settled box is re-pointed at what the caller asked for
    # before it is restarted, so re-ensuring after editing an env spec picks
    # up the edit instead of silently restarting the old shape.
    #
    # ``name`` only when the caller supplied one: it is blank on most presses
    # and an unconditional assignment would overwrite the operator's label
    # with the empty string, or with a default derived from a spec they never
    # asked to be named after.
    #
    # ``owner`` is deliberately not re-pointed. It is part of the ensure key
    # through ``box_slug_for``, so re-pointing it would let one person's press
    # take over another person's box (#1470).
    if not box.is_live:
        box.environment_spec = spec
        box.agent_definition = agent
        # The frozen image is also the input for image-only boxes. Clear a
        # prior spawn's value when ensure instead asks to resolve a recipe.
        box.image = image
        if name:
            box.name = name
        if idle_timeout_seconds is not None:
            box.idle_timeout_seconds = idle_timeout_seconds
        box.save(
            update_fields=[
                "environment_spec",
                "agent_definition",
                "image",
                "name",
                "idle_timeout_seconds",
                "updated_at",
                "version",
            ]
        )
    return box, False


def _default_box_name(spec, agent) -> str:
    return (getattr(agent, "name", "") or getattr(spec, "name", "") or "Agent box")[:200]


def destroy_agent_box(box, *, by=None) -> None:
    """Tear the box down and retire the row.

    Soft delete per the platform rule: the row is history an operator can
    still read, not garbage. The cluster objects are genuinely deleted first,
    because a soft-deleted row that leaves a pod running is a node nobody is
    watching any more.
    """
    stop_agent_box(box, require_teardown=True)
    box.soft_delete(by=by)
