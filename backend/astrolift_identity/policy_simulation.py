"""
The policy simulation: what a draft ABAC policy would deny (#2126).

Read-only. The draft is run through the evaluator itself
(:func:`astrolift_identity.abac.evaluate`), alone, against two things:

* **HOLDERS**: the org's current holders of the actions it touches (catalog
  permissions its action pattern matches), now. A draft aimed at one scope,
  or at apps or projects by slug, is checked on those objects against
  everyone whose grants reach them (``access.access_on``). A draft that
  covers the whole org and names no app or project is checked on every
  grant carrying a touched action, at the grant's own scope. Group grants
  count each member.
* **AUDIT**: the org's recorded, allowed decisions in the window whose
  gating permissions the action pattern matches, replayed at the time they
  happened, with the client IP and session age the record carries. A
  decision was already allowed by RBAC, so only a denial is news.

Each check comes out DENIED, UNKNOWN or NOT_DENIED. UNKNOWN is the
fail-closed guess: the draft would deny because the check lacks an
attribute a simulation cannot have (a person's session, a client IP, the
operation's environment or approvals, or, for a recorded decision, which
object it was on). In a real request those may be present, so it may or
may not deny. A malformed or unknown condition denies for real, so it is
DENIED.

The platform operator is not subject to any org's policies and is left out.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import fnmatch
from types import SimpleNamespace

from django.utils import timezone

from astrolift_graphql import GUID

MAX_TARGETS = 25
MAX_EVENTS = 5000
DEFAULT_DAYS = 7
MAX_DAYS = 90

DENIED = "DENIED"
UNKNOWN = "UNKNOWN"
NOT_DENIED = "NOT_DENIED"
_RANK = {DENIED: 0, UNKNOWN: 1, NOT_DENIED: 2}

# What a simulation cannot know about a check; a condition needing it is a guess.
_UNKNOWABLE = {"client_ip", "session", "operation"}


def classify(outcome, *, unknowable: frozenset[str] = frozenset()) -> str:
    """DENIED, UNKNOWN or NOT_DENIED for one policy outcome.

    ``unknowable`` names extra reasons that make a denial a guess (the
    recorded decision's missing target).
    """

    from astrolift_identity.abac import CONDITION_KINDS

    if not outcome.denies:
        return NOT_DENIED
    if outcome.assumed or unknowable:
        return UNKNOWN
    needs = {k.kind: k.needs for k in CONDITION_KINDS}
    if any(c.holds is False for c in outcome.conditions) or not outcome.conditions:
        return DENIED
    # Every condition held or could not be evaluated, and it denies: it is a
    # guess only when an unevaluated one needs what a simulation lacks.
    guessed = [c for c in outcome.conditions if c.holds is None and needs.get(c.kind) in _UNKNOWABLE]
    return UNKNOWN if guessed else DENIED


def _draft_policy(draft, scope_pk: int | None):
    return SimpleNamespace(
        guid="draft",
        slug="draft",
        effect=(draft.effect or "DENY").upper(),
        scope_level=(draft.scope_level or "ORG").upper(),
        scope_id=scope_pk,
        action_pattern=(draft.action_pattern or "*").strip() or "*",
        resource_pattern=draft.resource_pattern if draft.resource_pattern is not None else {},
        conditions=draft.conditions if draft.conditions is not None else [],
        actor_pattern=draft.actor_pattern if draft.actor_pattern is not None else {},
    )


def _slug_globs(pattern, key: str) -> list[str]:
    from astrolift_identity.abac import _values

    return _values(pattern.get(key)) if isinstance(pattern, dict) else []


@dataclasses.dataclass
class _Holder:
    user_id: int
    scope: tuple[str, int]
    permissions: set[str] = dataclasses.field(default_factory=set)
    roles: set[str] = dataclasses.field(default_factory=set)


def simulate(org_id: int | None, draft, *, days: int | None, limit: int):
    from astrolift_identity.access import resolve_target
    from core.permissions import Permission

    errors: list[str] = []
    if org_id is None:
        return _failed(["no active organization"])
    effect = (draft.effect or "").upper()
    level = (draft.scope_level or "").upper()
    if effect not in ("ALLOW", "DENY"):
        errors.append("effect must be ALLOW or DENY")
    if level not in ("ORG", "TEAM", "PROJECT", "APP"):
        errors.append("scopeLevel must be ORG, TEAM, PROJECT or APP")
    scope_target = None
    if not errors and draft.scope_id not in (None, ""):
        scope_target = resolve_target(level, draft.scope_id, org_id)
        if scope_target is None:
            errors.append("scope not found")
    if errors:
        return _failed(errors)

    notes: list[str] = []
    for name, value, kind in (
        ("resourcePattern", draft.resource_pattern, dict),
        ("conditions", draft.conditions, list),
        ("actorPattern", draft.actor_pattern, dict),
    ):
        if value is not None and not isinstance(value, kind):
            notes.append(
                f"{name} is not {'an object' if kind is dict else 'a list'}: it denies every check it applies to."
            )

    policy = _draft_policy(draft, scope_target.pk if scope_target is not None else None)
    actions = sorted(p.value for p in Permission if fnmatch.fnmatchcase(p.value, policy.action_pattern))
    window = min(max(days or DEFAULT_DAYS, 1), MAX_DAYS)

    holders, holder_notes = _holders(org_id, policy, actions, scope_target, limit)
    notes += holder_notes
    audit = _audit(org_id, policy, actions, scope_target, window, limit)
    notes += audit["notes"]
    notes.append("The draft is evaluated alone; the org's saved policies are not applied with it.")

    from astrolift_identity.schema.access_ux import PolicySimulationType

    sources = ["HOLDERS"] + (["AUDIT"] if audit["recorded"] else [])
    return PolicySimulationType(
        ok=True,
        errors=[],
        sources=sources,
        actions=actions,
        holders_count=holders["count"],
        holders_denied_count=holders["denied"],
        holders_unknown_count=holders["unknown"],
        holders=holders["rows"],
        audit_recorded=audit["recorded"],
        window_days=window,
        decisions_evaluated=audit["evaluated"],
        decisions_denied_count=audit["denied"],
        decisions_unknown_count=audit["unknown"],
        decisions=audit["rows"],
        notes=notes,
    )


# ---------------------------------------------------------------------
# Holders
# ---------------------------------------------------------------------


def _member_groups(org_id: int) -> dict[int, frozenset[str]]:
    from astrolift_identity.models import Member

    out: dict[int, frozenset[str]] = {}
    for user_id, raw in Member.objects.filter(
        scope_kind=Member.ScopeKind.ORG, scope_id=org_id, is_active=True
    ).values_list("user_id", "idp_groups"):
        if isinstance(raw, list):
            out[user_id] = frozenset(g for g in raw if isinstance(g, str) and g)
    return out


def _targets(org_id: int, policy, scope_target) -> tuple[list, bool]:
    """The objects a targeted draft is about, and whether the list was cut."""

    from astrolift_identity.access import Target
    from astrolift_identity.models import Project
    from astrolift_registry.models import RegisteredApp

    if scope_target is not None and scope_target.kind != "ORG":
        return [scope_target], False
    app_globs = _slug_globs(policy.resource_pattern, "app_slug")
    project_globs = _slug_globs(policy.resource_pattern, "project_slug")
    if not app_globs and not project_globs:
        return [], False
    out = []
    apps = RegisteredApp.objects.filter(organization_id=org_id).values_list(
        "pk", "guid", "slug", "project__slug"
    )
    for pk, guid, slug, project_slug in apps.order_by("slug", "pk"):
        if app_globs and not any(fnmatch.fnmatchcase(slug, g) for g in app_globs):
            continue
        if project_globs and not (
            project_slug and any(fnmatch.fnmatchcase(project_slug, g) for g in project_globs)
        ):
            continue
        out.append(Target(kind="APP", pk=pk, guid=str(guid), requested_kind="APP"))
    if project_globs and not app_globs:
        for pk, guid, slug in Project.objects.filter(organization_id=org_id).values_list(
            "pk", "guid", "slug"
        ):
            if any(fnmatch.fnmatchcase(slug, g) for g in project_globs):
                out.append(Target(kind="PROJECT", pk=pk, guid=str(guid), requested_kind="PROJECT"))
    return out[:MAX_TARGETS], len(out) > MAX_TARGETS


def _holders(org_id: int, policy, actions: list[str], scope_target, limit: int):
    from django.contrib.auth import get_user_model

    from astrolift_identity import abac
    from astrolift_identity.access import access_on
    from astrolift_identity.grant_preview import _row_permissions
    from astrolift_identity.models import GroupRoleMapping, Member, RoleBinding
    from astrolift_identity.permission_resolver import _scope_ancestry
    from astrolift_identity.schema.access_ux import PolicySimulationHolderType
    from astrolift_identity.schema.queries import _org_scope_q, _resolve_source_scope_labels, _scope_guids
    from astrolift_identity.schema.types import user_to_type
    from core.permissions import PermissionScope, ScopeKind
    from core.tenancy import TenantContext

    notes: list[str] = []
    wanted = set(actions)
    groups_of = _member_groups(org_id)
    members_in: dict[str, list[int]] = {}
    for user_id, groups in groups_of.items():
        for g in groups:
            members_in.setdefault(g, []).append(user_id)

    held: dict[tuple[int, tuple[str, int]], _Holder] = {}

    def hold(user_id: int, scope: tuple[str, int], perms: set[str], role_slug: str) -> None:
        entry = held.setdefault((user_id, scope), _Holder(user_id, scope))
        entry.permissions |= perms
        entry.roles.add(role_slug)

    targets, cut = _targets(org_id, policy, scope_target)
    targeted = (
        bool(targets)
        or (scope_target is not None and scope_target.kind != "ORG")
        or bool(
            _slug_globs(policy.resource_pattern, "app_slug")
            or _slug_globs(policy.resource_pattern, "project_slug")
        )
    )
    if cut:
        notes.append(f"Only the first {MAX_TARGETS} matching objects were checked.")
    if targeted and not targets:
        notes.append("No app or project of this organization matches the draft's slugs.")
    if targeted:
        for target in targets:
            scope = (target.kind, target.pk)
            for row in access_on(org_id, target):
                if row.role is None:
                    continue
                perms = _row_permissions(row)
                users = [row.user.pk] if row.user is not None else members_in.get(row.group_external_id, [])
                for user_id in users:
                    # Roles held here count for user_role_at_scope even when
                    # they carry no touched action.
                    hold(user_id, scope, perms & wanted, row.role.slug)
    else:
        now = timezone.now()
        for binding in RoleBinding.objects.select_related("role").filter(_org_scope_q(org_id)):
            if binding.expires_at is not None and binding.expires_at <= now:
                continue
            perms = set(binding.role.permissions or ()) & wanted
            if not perms:
                continue
            scope = (binding.scope_kind, binding.scope_id)
            users = (
                [binding.user_id]
                if binding.user_id is not None
                else members_in.get(binding.group_external_id, [])
            )
            for user_id in users:
                hold(user_id, scope, perms, binding.role.slug)
        for mapping in GroupRoleMapping.objects.select_related("role").filter(organization_id=org_id):
            perms = set(mapping.role.permissions or ()) & wanted
            if not perms:
                continue
            for user_id in members_in.get(mapping.group_external_id, []):
                hold(user_id, (mapping.scope_kind, mapping.scope_id), perms, mapping.role.slug)
        notes.append(
            "Team shares are checked at the sharing team: the draft names no app, so the answer is the same."
        )

    user_ids = {h.user_id for h in held.values() if h.permissions}
    users = {u.pk: u for u in get_user_model().objects.filter(pk__in=user_ids)}
    operators = {pk for pk, u in users.items() if u.is_superuser and u.is_active}
    if operators:
        notes.append("Platform operators are not subject to an organization's policies and are left out.")

    tenant = TenantContext(organization_id=org_id)
    chains: dict[tuple[str, int], list] = {}
    attrs = abac.RequestAttributes(authenticated_at=None, auth_factors=None)
    evaluated = []
    for holder in held.values():
        if not holder.permissions or holder.user_id in operators or holder.user_id not in users:
            continue
        if holder.scope not in chains:
            kind, ident = holder.scope
            chains[holder.scope] = _scope_ancestry(tenant, PermissionScope(kind=ScopeKind(kind), id=ident))
        chain = chains[holder.scope]
        if not chain:
            continue
        results: dict[str, list[str]] = {DENIED: [], UNKNOWN: [], NOT_DENIED: []}
        detail = ""
        for slug in sorted(holder.permissions):
            subject = abac.new_subject(
                organization_id=org_id,
                actor_user_id=holder.user_id,
                permission=slug,
                chain=chain,
                roles_at_target=holder.roles,
                groups=groups_of.get(holder.user_id, frozenset()),
                attrs=attrs,
            )
            outcome = abac.evaluate(subject, [policy]).outcomes[0]
            verdict = classify(outcome)
            results[verdict].append(slug)
            if verdict != NOT_DENIED and not detail:
                detail = outcome.detail
        verdict = DENIED if results[DENIED] else UNKNOWN if results[UNKNOWN] else NOT_DENIED
        evaluated.append((verdict, holder, results, detail))

    people = {h.user_id for _v, h, _r, _d in evaluated}
    denied_people = {h.user_id for v, h, _r, _d in evaluated if v == DENIED}
    unknown_people = {h.user_id for v, h, _r, _d in evaluated if v == UNKNOWN} - denied_people
    evaluated.sort(key=lambda e: (_RANK[e[0]], users[e[1].user_id].get_username().lower(), e[1].scope))
    page = evaluated[:limit]

    scopes = {h.scope for _v, h, _r, _d in page}
    guids = _scope_guids(scopes)
    labels = _resolve_source_scope_labels([SimpleNamespace(scope_kind=k, scope_id=i) for k, i in scopes])
    member_guids = dict(
        Member.objects.filter(
            scope_kind=Member.ScopeKind.ORG,
            scope_id=org_id,
            user_id__in=[h.user_id for _v, h, _r, _d in page],
        ).values_list("user_id", "guid")
    )
    rows = [
        PolicySimulationHolderType(
            user=user_to_type(users[h.user_id]),
            member_id=GUID(str(member_guids[h.user_id])) if h.user_id in member_guids else None,
            outcome=verdict,
            denied=results[DENIED],
            unknown=results[UNKNOWN],
            allowed=results[NOT_DENIED],
            scope_kind=h.scope[0],
            scope_guid=GUID(guids[h.scope]) if h.scope in guids else None,
            source_scope_label=labels.get(h.scope, ""),
            detail=detail,
        )
        for verdict, h, results, detail in page
    ]
    return {
        "count": len(people),
        "denied": len(denied_people),
        "unknown": len(unknown_people),
        "rows": rows,
    }, notes


# ---------------------------------------------------------------------
# Recorded decisions
# ---------------------------------------------------------------------


def _audit(org_id: int, policy, actions: list[str], scope_target, window: int, limit: int) -> dict:
    from astrolift_identity import abac
    from astrolift_identity.idp_groups import member_groups
    from astrolift_identity.permission_resolver import _live_grants
    from astrolift_identity.schema.access_ux import PolicySimulationDecisionType
    from astrolift_operations.models import AuditEvent
    from core.tenancy import TenantContext

    since = timezone.now() - dt.timedelta(days=window)
    in_window = AuditEvent.objects.filter(organization_id=org_id, occurred_at__gte=since)
    recorded = in_window.exists()
    out = {"recorded": recorded, "evaluated": 0, "denied": 0, "unknown": 0, "rows": [], "notes": []}
    if not recorded:
        out["notes"].append(f"No decisions are recorded for this organization in the last {window} days.")
        return out
    out["notes"].append(
        "Recorded decisions are the audited mutations; reads are not recorded, so they are not replayed."
    )

    # A recorded decision does not say which object it was on, so a draft
    # that depends on the object is replayed as if it covered every object,
    # and a denial is only a guess.
    targeted = (scope_target is not None and scope_target.kind != "ORG") or bool(
        _slug_globs(policy.resource_pattern, "app_slug")
        or _slug_globs(policy.resource_pattern, "project_slug")
    )
    replay = policy
    if targeted:
        pattern = dict(policy.resource_pattern) if isinstance(policy.resource_pattern, dict) else {}
        pattern.pop("app_slug", None)
        pattern.pop("project_slug", None)
        replay = SimpleNamespace(
            **{**vars(policy), "scope_level": "ORG", "scope_id": None, "resource_pattern": pattern}
        )
    unknowable = frozenset({"target"}) if targeted else frozenset()

    events = list(
        in_window.filter(decision=AuditEvent.Decision.ALLOW, actor_kind="user").order_by(
            "-occurred_at", "-pk"
        )[:MAX_EVENTS]
    )
    if len(events) == MAX_EVENTS:
        out["notes"].append(f"Only the latest {MAX_EVENTS} recorded decisions were replayed.")
    wanted = set(actions)
    actor_cache: dict[int, tuple[frozenset[str], frozenset[str]]] = {}
    for event in events:
        perms = [p for p in (event.data or {}).get("permissions") or [] if isinstance(p, str) and p in wanted]
        if not perms or not str(event.actor_id).isdigit():
            continue
        actor = int(event.actor_id)
        if actor not in actor_cache:
            tenant = TenantContext(organization_id=org_id, actor_user_id=actor)
            roles = frozenset(g.role.slug for g in _live_grants(tenant, [("ORG", org_id)]))
            actor_cache[actor] = (member_groups(actor, org_id), roles)
        groups, roles = actor_cache[actor]
        age = event.request_session_age_seconds
        attrs = abac.RequestAttributes(
            actor_user_id=actor,
            client_ip=event.request_ip or None,
            now=event.occurred_at,
            authenticated_at=event.occurred_at - dt.timedelta(seconds=age) if age is not None else None,
            auth_factors=None,
        )
        out["evaluated"] += 1
        worst, detail = NOT_DENIED, ""
        for slug in perms:
            subject = abac.new_subject(
                organization_id=org_id,
                actor_user_id=actor,
                permission=slug,
                chain=[("ORG", org_id)],
                roles_at_target=roles,
                groups=groups,
                attrs=attrs,
            )
            outcome = abac.evaluate(subject, [replay]).outcomes[0]
            verdict = classify(outcome, unknowable=unknowable if outcome.denies else frozenset())
            if _RANK[verdict] < _RANK[worst]:
                worst, detail = verdict, outcome.detail
        if worst == NOT_DENIED:
            continue
        out["denied" if worst == DENIED else "unknown"] += 1
        if len(out["rows"]) < limit:
            out["rows"].append(
                PolicySimulationDecisionType(
                    id=GUID(str(event.guid)),
                    occurred_at=event.occurred_at,
                    action=event.action,
                    actor_id=str(event.actor_id),
                    actor_display=event.actor_display or "",
                    outcome=worst,
                    permissions=perms,
                    detail=detail + ("; the record does not name the object it was on" if targeted else ""),
                )
            )
    out["notes"].append("A replay uses each actor's current IdP groups and organization roles.")
    return out


def _failed(errors: list[str]):
    from astrolift_identity.schema.access_ux import PolicySimulationType

    return PolicySimulationType(
        ok=False,
        errors=errors,
        sources=[],
        actions=[],
        holders_count=0,
        holders_denied_count=0,
        holders_unknown_count=0,
        holders=[],
        audit_recorded=False,
        window_days=0,
        decisions_evaluated=0,
        decisions_denied_count=0,
        decisions_unknown_count=0,
        decisions=[],
        notes=[],
    )
