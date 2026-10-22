"""Agent runs that read brains (#2432).

A run a person launches reads the brains its agent declares (``brains`` in
the agent's manifest) and the ones the launch asks for (``brainSlugs``), each
as that person. Its tokens are the ones ``issueBrainReadToken`` issues
(``brain_tokens.issue``: 15 minutes, subject the person, audience the brain)
with the run's ``agent_task`` claim, signed by a key of the run's own binding
to the brain (``BrainRunBinding``). That key is published only while the run
is in flight, so revoking a binding stops the edge admitting the run's token
within the JWKS cache rather than at its expiry.

The run gets its first tokens in its environment at spawn and fresh ones
from :func:`refresh`, over its callback key. The launch, the spawn, every
refresh, every steer and every gate answer check again that the person can
still read each brain; a brain they lost is revoked there, and the next
refresh tells the run which and why. Ending the run, cancelling it and
archiving the brain revoke too.

Runs started by a schedule, a loop, a webhook or a workflow stage have no
person behind them. They read the brains shared with their agent
(``BrainAgentGrant``, #2449) that the agent also lists in ``brains`` or
``brains_allowed``, as the agent: bound at spawn, checked again on every
refresh, revoked on unshare and published only while the share, the agent and
the run are live.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import json
import re
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from astrolift_registry.models import BrainAgentGrant, BrainRunBinding, BrainTokenKey, RegisteredApp

MAX_RUN_BRAINS = 10
INDEX_ENV = "BRAINS"
INDEX_VERSION = 1
SURFACE = "agent_run"

# Why a binding stopped, as the run and its execution detail read it.
ENDED = "the run ended"
CANCELLED = "the run was cancelled"
ACCESS_LOST = "the person who launched the run can no longer read this brain"
BRAIN_GONE = "the brain was archived or deleted"
UNSHARED = "the brain is no longer shared with the agent"

#: How a run nobody launched starts (``core.run_trigger``): a schedule or a
#: loop tick, a webhook, a workflow stage.
UNATTENDED_TRIGGERS = ("schedule", "webhook", "parent")


class BrainRunError(Exception):
    """A launch or spawn that cannot give the run its brains. ``code`` is an
    ``AgentDispatchError`` code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclasses.dataclass(frozen=True)
class LaunchBrain:
    brain: RegisteredApp
    source: str


@dataclasses.dataclass(frozen=True)
class RunWiring:
    """What a spawn puts into the run: plain ``env`` and the tokens, which go
    through the run's Secret, keyed by their env names."""

    env: tuple[tuple[str, str], ...]
    secrets: dict[str, str]

    def redact(self, text: str) -> str:
        for token in self.secrets.values():
            encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
            text = text.replace(token, "[redacted]").replace(encoded, "[redacted]")
        return text


def env_names(slug: str) -> dict[str, str]:
    """The env a run reads one brain from: ``BRAIN_<SLUG>_MCP_URL``,
    ``BRAIN_<SLUG>_TOKEN_HEADER`` and ``BRAIN_<SLUG>_TOKEN``."""
    stem = "BRAIN_" + re.sub(r"[^A-Z0-9]", "_", slug.upper())
    return {"mcp_url": f"{stem}_MCP_URL", "token_header": f"{stem}_TOKEN_HEADER", "token": f"{stem}_TOKEN"}


def _requested(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise BrainRunError("validation", "brainSlugs must be a list of brain slugs")
    if len(value) > MAX_RUN_BRAINS:
        raise BrainRunError("validation", f"a run reads at most {MAX_RUN_BRAINS} brains")
    slugs: list[str] = []
    for item in value:
        slug = item.strip() if isinstance(item, str) else ""
        if not slug or len(slug) > 255:
            raise BrainRunError("validation", "brainSlugs must name brains by slug")
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def resolve_launch(
    workload, *, organization_id: int, requested: Any, launcher_id: int | None
) -> list[LaunchBrain]:
    """The brains a launch of agent ``workload`` reads: the agent's own, then
    the asked-for ones that are not among them, all checked against what the
    launching identity can read now (``visible_brains()`` in the current
    tenant and API token). Raises :class:`BrainRunError` for one outside the
    agent's allow-list, one the identity cannot read (``not_found``, never a
    hint it exists elsewhere), one that is not served anywhere, too many, or
    a launch with no person behind it."""
    from astrolift_registry.brain_apps import mcp_urls
    from astrolift_registry.brains import visible_brains

    asked = _requested(requested)
    defaults = list(workload.brains or [])
    extra = [slug for slug in asked if slug not in defaults]
    allowed = workload.brains_allowed
    if allowed is not None:
        refused = [slug for slug in extra if slug not in allowed]
        if refused:
            accepts = ", ".join(allowed) if allowed else "no brains besides its own"
            raise BrainRunError(
                "validation",
                f"agent {workload.slug!r} does not accept brain {refused[0]!r}; it accepts {accepts}",
            )
    wanted = [(slug, BrainRunBinding.Source.DEFAULT) for slug in defaults] + [
        (slug, BrainRunBinding.Source.REQUESTED) for slug in extra
    ]
    if not wanted:
        return []
    if len(wanted) > MAX_RUN_BRAINS:
        raise BrainRunError(
            "validation", f"a run reads at most {MAX_RUN_BRAINS} brains, its agent's included"
        )
    if launcher_id is None:
        raise BrainRunError(
            "precondition", "a run reads brains as the person who launches it, and this launch has none"
        )
    found = {
        app.slug: app
        for app in visible_brains().filter(
            organization_id=organization_id, slug__in=[slug for slug, _ in wanted]
        )
    }
    for slug, _ in wanted:
        if slug not in found:
            raise BrainRunError("not_found", f"brain {slug!r} not found")
    for slug, _ in wanted:
        if not mcp_urls(found[slug]):
            raise BrainRunError("precondition", f"brain {slug!r} is not served in any environment yet")
    stems: dict[str, str] = {}
    for slug, _ in wanted:
        name = env_names(slug)["token"]
        if name in stems:
            raise BrainRunError(
                "validation", f"brains {stems[name]!r} and {slug!r} would share the env name {name}"
            )
        stems[name] = slug
    return [LaunchBrain(brain=found[slug], source=source) for slug, source in wanted]


def requested_slugs(task) -> list[str]:
    """The brains ``task``'s launch asked for beyond its agent's own."""
    return list(
        BrainRunBinding.objects.filter(agent_task_id=task.pk, source=BrainRunBinding.Source.REQUESTED)
        .order_by("pk")
        .values_list("slug", flat=True)
    )


def bind(task, brains: list[LaunchBrain]) -> None:
    for item in brains:
        BrainRunBinding.objects.create(
            organization_id=task.organization_id,
            agent_task=task,
            brain=item.brain,
            slug=item.brain.slug,
            name=item.brain.name,
            source=item.source,
        )


def live_run_bindings():
    """Bindings of runs still in flight, not revoked, to live brains; one
    made from a share also needs the share and the agent live."""
    from astrolift_agents.models import AgentTask

    shared = Q(
        source=BrainRunBinding.Source.GRANT,
        grant__isnull=False,
        grant__deleted_at__isnull=True,
        agent_task__agent_definition__deleted_at__isnull=True,
        agent_task__agent_definition__registered_app__deleted_at__isnull=True,
    )
    return BrainRunBinding.objects.filter(
        Q(source__in=(BrainRunBinding.Source.DEFAULT, BrainRunBinding.Source.REQUESTED)) | shared,
        revoked_at__isnull=True,
        agent_task__deleted_at__isnull=True,
        agent_task__status__in=AgentTask.NON_TERMINAL_STATUSES,
        agent_task__cancel_requested_at__isnull=True,
        brain__isnull=False,
        brain__deleted_at__isnull=True,
        brain__archived_at__isnull=True,
    )


def live_run_keys():
    """The run keys the JWKS publishes: those of live run bindings."""
    return BrainTokenKey.objects.filter(run_binding__in=live_run_bindings())


def _audit(action: str, row: BrainRunBinding, task, *, actor_user_id: int | None, **extra: Any) -> None:
    from core.mutations import AuditEntry, emit_audit
    from core.permissions import Permission

    emit_audit(
        AuditEntry(
            actor_user_id=actor_user_id,
            organization_id=task.organization_id,
            action=action,
            decision="ALLOW",
            target_kind="RegisteredApp",
            target_id=row.slug,
            duration_ms=0,
            permissions=(Permission.APP_READ.value,) if action == "brain.token.issue" else (),
            extra={"agent_task": str(task.guid), "surface": SURFACE, **extra},
        )
    )


def _revoke(rows: list[BrainRunBinding], reason: str) -> list[BrainRunBinding]:
    """Revoke ``rows``: delete their keys, so no token of theirs verifies
    again, and record when and why."""
    rows = [row for row in rows if row.revoked_at is None]
    if not rows:
        return []
    now = timezone.now()
    BrainTokenKey.objects.filter(run_binding__in=[row.pk for row in rows]).delete()
    for row in rows:
        row.revoked_at = now
        row.revoked_reason = reason[:255]
        row.save(update_fields=["revoked_at", "revoked_reason", "updated_at", "version"])
        _audit("brain.token.revoke", row, row.agent_task, actor_user_id=None, reason=row.revoked_reason)
    return rows


def revoke_run(task, reason: str) -> list[BrainRunBinding]:
    """Revoke every binding of ``task``: it ended or a cancel was asked for."""
    rows = BrainRunBinding.objects.filter(agent_task_id=task.pk, revoked_at__isnull=True)
    return _revoke(list(rows.select_related("agent_task")), reason)


def revoke_grant(grant: BrainAgentGrant) -> list[BrainRunBinding]:
    """Revoke every run's binding made from ``grant``: it was unshared."""
    rows = BrainRunBinding.objects.filter(grant=grant, revoked_at__isnull=True)
    return _revoke(list(rows.select_related("agent_task")), UNSHARED)


def agent_grants(workload):
    """The live shares of live brains with agent ``workload``, in its organization."""
    return BrainAgentGrant.objects.filter(
        agent=workload,
        organization_id=workload.registered_app.organization_id,
        brain__organization_id=workload.registered_app.organization_id,
        deleted_at__isnull=True,
        brain__deleted_at__isnull=True,
        brain__archived_at__isnull=True,
    )


def visible_agent_grants(workload):
    """``workload``'s shares whose brain the caller can read, by brain slug."""
    from astrolift_registry.brains import visible_brains

    return (
        agent_grants(workload)
        .filter(brain__in=visible_brains().values("pk"))
        .select_related("brain")
        .order_by("brain__slug")
    )


def _agent_lists(workload) -> list[str]:
    """The brains ``workload`` lists: ``brains``, then ``brains_allowed``."""
    out = list(workload.brains or [])
    return out + [slug for slug in workload.brains_allowed or [] if slug not in out]


def bind_unattended(task) -> None:
    """Bind a run nobody launched to the brains shared with its agent that the
    agent also lists and some environment serves, at most ``MAX_RUN_BRAINS``.
    Nothing for a launched run or one already bound."""
    from astrolift_registry.brain_apps import mcp_urls

    workload = task.agent_definition
    if task.trigger_kind not in UNATTENDED_TRIGGERS or workload is None:
        return
    if BrainRunBinding.objects.filter(agent_task_id=task.pk).exists():
        return
    listed = _agent_lists(workload)
    grants = {
        grant.brain.slug: grant
        for grant in agent_grants(workload).filter(brain__slug__in=listed).select_related("brain")
    }
    names: set[str] = set()
    for slug in [slug for slug in listed if slug in grants][:MAX_RUN_BRAINS]:
        grant = grants[slug]
        name = env_names(slug)["token"]
        if name in names or not mcp_urls(grant.brain):
            continue
        names.add(name)
        BrainRunBinding.objects.get_or_create(
            agent_task=task,
            slug=slug,
            defaults={
                "organization_id": task.organization_id,
                "brain": grant.brain,
                "name": grant.brain.name,
                "source": BrainRunBinding.Source.GRANT,
                "grant": grant,
            },
        )


def revoke_brain(brain_id: int) -> list[BrainRunBinding]:
    """Revoke every run's binding to brain ``brain_id``: it is being archived or deleted."""
    rows = BrainRunBinding.objects.filter(brain_id=brain_id, revoked_at__isnull=True)
    return _revoke(list(rows.select_related("agent_task")), BRAIN_GONE)


def _readable(user, organization_id: int, row: BrainRunBinding) -> bool:
    """Whether ``user`` can still read the brain ``row`` binds, judged as
    that person alone: the API token and the request attributes of whatever
    request asks (a teammate's steer, say) are not theirs."""
    from astrolift_identity.abac import RequestAttributes, request_attributes
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_registry.brain_apps import readable_brain_for

    marker = set_current_api_token(None)
    try:
        with request_attributes(RequestAttributes(actor_user_id=user.pk)):
            brain = readable_brain_for(user, organization_id, row.slug)
    finally:
        reset_current_api_token(marker)
    return brain is not None and brain.pk == row.brain_id


def _still_shared(row: BrainRunBinding) -> bool:
    """Whether the share ``row`` reads through is live, its agent is, and the
    agent still lists the brain."""
    grant = row.grant
    agent = row.agent_task.agent_definition
    return (
        grant is not None
        and grant.deleted_at is None
        and agent is not None
        and grant.agent_id == agent.pk
        and agent.deleted_at is None
        and agent.registered_app.deleted_at is None
        and row.slug in _agent_lists(agent)
    )


def recheck(task) -> list[BrainRunBinding]:
    """Revoke every live binding of ``task`` whose brain the person who
    launched it can no longer read, that is no longer shared with its agent
    (a run nobody launched), or that is gone; returns those revoked now."""
    rows = list(
        BrainRunBinding.objects.filter(agent_task_id=task.pk, revoked_at__isnull=True).select_related(
            "brain",
            "grant",
            "agent_task",
            "agent_task__triggered_by_user",
            "agent_task__agent_definition__registered_app",
        )
    )
    if not rows:
        return []
    user = rows[0].agent_task.triggered_by_user
    gone = [row for row in rows if row.brain is None or row.brain.deleted_at or row.brain.archived_at]
    shared = [row for row in rows if row not in gone and row.source == BrainRunBinding.Source.GRANT]
    unshared = [row for row in shared if not _still_shared(row)]
    lost = [
        row
        for row in rows
        if row not in gone
        and row not in shared
        and (user is None or not _readable(user, task.organization_id, row))
    ]
    return _revoke(gone, BRAIN_GONE) + _revoke(unshared, UNSHARED) + _revoke(lost, ACCESS_LOST)


def _binding_key(row: BrainRunBinding) -> BrainTokenKey:
    """The key ``row``'s tokens are signed with, made on first use."""
    from astrolift_registry.brain_tokens import _new_key

    with transaction.atomic():
        locked = BrainRunBinding.objects.select_for_update().get(pk=row.pk)
        if locked.revoked_at is not None:
            raise BrainRunError("precondition", f"brain {row.slug!r}: {locked.revoked_reason}")
        return BrainTokenKey.objects.filter(run_binding=locked).first() or _new_key(run_binding=locked)


def _subject(row: BrainRunBinding, task):
    """Who ``row``'s tokens are issued to: the agent for a share (``sub``
    ``agent:<guid>``, no email), else the person who launched the run."""
    if row.source != BrainRunBinding.Source.GRANT:
        return task.triggered_by_user
    from types import SimpleNamespace

    return SimpleNamespace(pk=None, guid=f"agent:{task.agent_definition.guid}", email="")


def _issue(row: BrainRunBinding, task, *, reason: str) -> tuple[str, dt.datetime]:
    from astrolift_registry.brain_tokens import BrainTokenError, issue

    subject = _subject(row, task)
    try:
        issued = issue(
            user=subject, brain=row.brain, key=_binding_key(row), claims={"agent_task": str(task.guid)}
        )
    except BrainTokenError as exc:
        raise BrainRunError("precondition", str(exc)) from exc
    extra = {"agent": task.agent_definition.slug} if subject.pk is None else {}
    _audit(
        "brain.token.issue",
        row,
        task,
        actor_user_id=subject.pk,
        reason=reason,
        expires_at=issued.expires_at.isoformat(),
        **extra,
    )
    return issued.token, issued.expires_at


def _entry(row: BrainRunBinding, urls: list[str], expires_at: dt.datetime) -> dict[str, Any]:
    from providers.k8s_native.edge_gateway import BRAIN_TOKEN_HEADER

    return {
        "slug": row.slug,
        "name": row.name,
        "id": str(row.brain.guid),
        "source": row.source,
        "mcp_url": urls[0] if urls else "",
        "mcp_urls": urls,
        "header": BRAIN_TOKEN_HEADER,
        "env": env_names(row.slug),
        "expires_at": expires_at.isoformat(),
    }


def refresh_url(task) -> str:
    from django.conf import settings

    base = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    return f"{base}/api/dispatch/v1/agents/{task.guid}/brains/"


def _tokens(task, *, reason: str, served: bool) -> tuple[list[tuple], list[BrainRunBinding]]:
    """A fresh token for every brain ``task`` still reads, after checking each
    again: ``([(binding, mcp urls, token, expiry)], revoked bindings)``.
    ``served`` refuses, before any token is issued, a brain no environment
    serves."""
    from astrolift_registry.brain_apps import mcp_urls

    recheck(task)
    rows = list(
        BrainRunBinding.objects.filter(agent_task_id=task.pk)
        .select_related("brain", "agent_task__triggered_by_user", "agent_task__agent_definition")
        .order_by("pk")
    )
    live = [(row, mcp_urls(row.brain)) for row in rows if row.revoked_at is None]
    unserved = [row.slug for row, urls in live if not urls]
    if served and unserved:
        raise BrainRunError("precondition", f"brain {unserved[0]!r} is not served in any environment")
    issued = []
    for row, urls in live:
        token, expires_at = _issue(row, row.agent_task, reason=reason)
        issued.append((row, urls, token, expires_at))
    return issued, [row for row in rows if row.revoked_at is not None]


def spawn_wiring(task) -> RunWiring | None:
    """The brain env of ``task``'s pod, or ``None`` when it reads none.
    Raises :class:`BrainRunError` when it cannot have every brain it was
    launched with: the person lost one while the run was queued, say."""
    from astrolift_agents.models import AgentTask
    from providers.k8s_native.edge_gateway import BRAIN_TOKEN_HEADER

    if not isinstance(task, AgentTask) or task.pk is None:
        return None
    bind_unattended(task)
    if not BrainRunBinding.objects.filter(agent_task_id=task.pk).exists():
        return None
    issued, revoked = _tokens(task, reason="launch", served=True)
    if revoked:
        raise BrainRunError("precondition", f"brain {revoked[0].slug!r}: {revoked[0].revoked_reason}")
    env: list[tuple[str, str]] = []
    secrets: dict[str, str] = {}
    entries = []
    for row, urls, token, expires_at in issued:
        names = env_names(row.slug)
        env += [(names["mcp_url"], urls[0]), (names["token_header"], BRAIN_TOKEN_HEADER)]
        secrets[names["token"]] = token
        entries.append(_entry(row, urls, expires_at))
    index = {"version": INDEX_VERSION, "refresh_url": refresh_url(task), "brains": entries}
    env.append((INDEX_ENV, json.dumps(index, separators=(",", ":"))))
    return RunWiring(env=tuple(env), secrets=secrets)


def refresh(task) -> dict[str, Any]:
    """What the run's refresh call answers: a fresh token for every brain it
    still reads (a brain no environment serves just now has no URL), and
    every brain revoked, each with why."""
    issued, revoked = _tokens(task, reason="refresh", served=False)
    return {
        "version": INDEX_VERSION,
        "brains": [
            {**_entry(row, urls, expires_at), "token": token} for row, urls, token, expires_at in issued
        ],
        "revoked": [
            {"slug": row.slug, "reason": row.revoked_reason, "revoked_at": row.revoked_at.isoformat()}
            for row in revoked
        ],
    }
