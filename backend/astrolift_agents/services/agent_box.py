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

from _sdk.agent_session import SESSION_NAME, SessionSpec, attach_argv, container_spec
from _sdk.k8s_naming import agent_namespace, dns_label
from django.utils import timezone

log = logging.getLogger("astrolift_agents.services.agent_box")

#: How long a finished box's Job object sticks around before Kubernetes
#: garbage-collects it. Long enough for the reaper's next sweep to observe the
#: completion and settle the row; short enough that finished boxes don't
#: accumulate as clutter in the namespace.
FINISHED_JOB_TTL_SECONDS = 600

#: Env the platform owns on every box. Registered as reserved agent
#: environment names so an env spec cannot shadow them.
BOX_ENV_MARKER = "ASTROLIFT_AGENT_BOX"
BOX_ENV_GUID = "ASTROLIFT_AGENT_BOX_GUID"
BOX_ENV_SESSION = "ASTROLIFT_TMUX_SESSION"


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
    """
    return dns_label("agent-box", str(box.guid).replace("-", "")[:16])


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
    """What ``astro exec --app <box>`` should run to join the session."""
    return attach_argv(box.session_name or SESSION_NAME)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _identity_env(box) -> dict[str, str]:
    return {
        BOX_ENV_MARKER: "1",
        BOX_ENV_GUID: str(box.guid),
        BOX_ENV_SESSION: box.session_name,
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
) -> dict:
    """The Job manifest for a box, built around ``container_spec()``.

    The container — its command, its args, ``stdin``/``tty``, its working
    directory — comes from ``_sdk.agent_session`` verbatim. The only thing
    added here is env the SDK has no way to know about: the resolved secret
    packet, which arrives as ``secretKeyRef`` entries pointing at the per-box
    Secret rather than as values in the pod spec.

    Precedence is spec env, then secret refs, then the platform's own
    identity env last, so nothing an operator writes into an env spec can
    shadow the variables the box needs to describe itself.
    """
    from astrolift_dispatch.agent_secrets import agent_container_env, secret_env_entries

    spec = box.environment_spec
    secret_name = box_secret_name(job_name)

    session = SessionSpec(
        image=image,
        session_name=box.session_name,
        idle_timeout_seconds=int(box.idle_timeout_seconds),
        env=_identity_env(box),
    )
    container = container_spec(session)

    live_refs = [{"env_var": name, "uri": ""} for name in sorted(secret_env_names or [])]
    container["env"] = _merge_env(
        agent_container_env(spec, secret_name),
        secret_env_entries(secret_name, live_refs),
        container["env"],
    )

    labels = {
        "astrolift.dev/workload-kind": "agent-box",
        "astrolift.dev/agent-box": str(box.guid),
        # The platform pod/log surfaces select on astrolift.dev/app; keying it
        # to the box guid makes a box's pod discoverable by the same code that
        # finds an agent task's pod, with no box-specific selector anywhere.
        "astrolift.dev/app": str(box.guid),
    }
    return {
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
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Start / stop
# ---------------------------------------------------------------------------


def resolve_box_image(box) -> str:
    """Image for a box, through the same resolution the Job spawner uses."""
    from astrolift_dispatch.spawners.k8s_job import _resolve_base_image

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
        )
    except AgentSecretResolutionError as exc:
        _fail(box, str(exc))
        raise AgentBoxError(str(exc)) from exc

    secret_env_names = sorted((secret_manifest or {}).get("stringData") or {})
    job = render_agent_box_job(
        box=box,
        image=image,
        namespace=namespace,
        job_name=job_name,
        secret_env_names=secret_env_names,
    )

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
        manifests = ([secret_manifest] if secret_manifest else []) + [job]
        result = driver.apply_manifests(ctx.slug, namespace, manifests)
    except Exception as exc:  # noqa: BLE001 — one failure mode for the caller
        _fail(box, f"applying the box manifests failed: {exc}")
        raise AgentBoxError(f"applying the box manifests failed: {exc}") from exc

    if not getattr(result, "ok", False):
        detail = result.summary() if hasattr(result, "summary") else "apply failed"
        # A partial apply can leave the plaintext-bearing Secret behind.
        _delete_box_objects(cluster, namespace, job_name)
        _fail(box, str(detail))
        raise AgentBoxError(str(detail))

    box.status = AgentBox.Status.PROVISIONING
    box.image = image[:512]
    box.external_id = job_name
    box.namespace = namespace
    box.started_at = timezone.now()
    box.ended_at = None
    box.last_error = ""
    box.save(
        update_fields=[
            "status",
            "image",
            "external_id",
            "namespace",
            "started_at",
            "ended_at",
            "last_error",
            "updated_at",
            "version",
        ]
    )
    log.info("agent_box: started %s as Job %s in %s", box.slug, job_name, namespace)


def stop_agent_box(box, *, status: str | None = None) -> None:
    """Delete the box's cluster objects and settle the row.

    Cluster teardown is best-effort on purpose: a box whose cluster is
    unreachable must still be removable from the platform, otherwise an
    operator is stuck with a row they cannot clear. The failure is recorded
    on the row rather than raised.
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

    box.status = target
    box.ended_at = timezone.now()
    box.last_error = error[:2000]
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
    result = driver.delete_manifests(ctx.slug, namespace, refs)
    if result is not None and not getattr(result, "ok", True):
        detail = result.summary() if hasattr(result, "summary") else "delete failed"
        raise AgentBoxError(str(detail))


def _fail(box, message: str) -> None:
    from astrolift_agents.models import AgentBox

    box.status = AgentBox.Status.FAILED
    box.ended_at = timezone.now()
    box.last_error = message[:2000]
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
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    status = driver.get_workload_status(
        ctx.slug,
        box.namespace or box_namespace(box),
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
    if getattr(status, "ready_replicas", 0) or getattr(status, "desired_replicas", 0):
        return AgentBox.Status.RUNNING.value
    return None


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
            continue
        if observed == AgentBox.Status.RUNNING.value:
            box.status = AgentBox.Status.RUNNING
            box.save(update_fields=["status", "updated_at", "version"])
            summary["running"] += 1
            continue
        stop_agent_box(box, status=observed)
        summary["expired" if observed == AgentBox.Status.EXPIRED.value else "failed"] += 1
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


def box_slug_for(*, environment_spec, agent, owner_id: int | None) -> str:
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
    """
    base = getattr(agent, "slug", "") or getattr(environment_spec, "slug", "")
    parts = ["box", str(base)]
    if owner_id is not None:
        parts.append(f"u{owner_id}")
    return "-".join(p for p in parts if p)[:200]


def ensure_agent_box(
    *,
    organization,
    environment_spec_slug: str = "",
    agent_slug: str = "",
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
    if not spec_slug and not agent_ref:
        raise AgentBoxEnsureError(
            "validation",
            "a box needs an agent or an environment spec to know what to run",
            "environmentSpecSlug",
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
                (
                    f"agent {agent.slug} is run mode {agent.run_mode}; "
                    "only a persistent agent can back a box"
                ),
                "agentSlug",
            )

    slug = box_slug_for(environment_spec=spec, agent=agent, owner_id=getattr(owner, "pk", None))
    box, created = _get_or_create_box(
        organization=organization,
        slug=slug,
        name=(name or "").strip(),
        spec=spec,
        agent=agent,
        owner=owner,
        idle_timeout_seconds=idle_timeout_seconds,
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
    if not box.is_live:
        box.environment_spec = spec
        box.agent_definition = agent
        if idle_timeout_seconds is not None:
            box.idle_timeout_seconds = idle_timeout_seconds
        box.save(
            update_fields=[
                "environment_spec",
                "agent_definition",
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
    stop_agent_box(box)
    box.soft_delete(by=by)
