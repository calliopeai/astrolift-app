"""
ABAC policy evaluation (spec 03 §5, #2157).

Runs after RBAC in :mod:`astrolift_identity.permission_resolver` and can
only deny: a check RBAC refused never reaches here, and nothing here turns
a refusal into a grant.

What a policy means (the frontend's ``components/access/policy-model.ts``
says the same sentences):

* A policy **applies** to a check when its scope covers the target, its
  ``action_pattern`` glob matches the permission slug, every key in its
  ``resource_pattern`` matches the target, and its ``actor_pattern``
  matches the actor.
* Every condition is a requirement. A DENY policy denies *unless* all its
  conditions hold; with no conditions it denies outright. An ALLOW policy
  allows *only when* all its conditions hold, so when one fails it denies;
  with no conditions it changes nothing. DENY overrides ALLOW because any
  one denying policy is enough.

Fail closed, everywhere:

* A condition that needs an attribute this request does not carry (no
  client IP, no session, no approval count, no environment) cannot be
  evaluated, and an applying policy with such a condition denies. The
  reason names the missing attribute.
* A resource key whose value is unknown for this check (``env`` and
  ``region`` unless the call site supplied them), an unknown resource or
  actor key, or a malformed pattern makes the policy count as applying.
  ``app_slug`` and ``project_slug`` are read from the target itself, so a
  target that is not in an app (or a project) definitely does not match.
* A condition of a kind this module does not know, or with a malformed
  value, cannot be evaluated and denies.

Attributes come from the tenant context (the org, the actor, the target
chain) and from the request (:class:`RequestAttributes`, set by the tenant
middleware). The person-bound ones (client IP, session age, sign-in
factors) are used only when the check is about the request's own user; a
diagnosis of somebody else sees them as unavailable. Policies and the
actor's IdP groups are cached on the request's attributes, so the cache
lives exactly as long as one request.
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import datetime as dt
import fnmatch
import ipaddress
from collections.abc import Callable, Iterable, Iterator
from typing import Any

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_UNSET: Any = object()


# ---------------------------------------------------------------------
# Request attributes
# ---------------------------------------------------------------------


@dataclasses.dataclass
class RequestAttributes:
    """What ABAC conditions read about the request.

    ``environment``, ``region`` and ``approvals`` describe the operation, not
    the person, and are supplied by the call site that knows them through
    :func:`operation_attributes`. ``now`` is the evaluation clock (tests
    pin it). The rest is person-bound and read lazily from ``request``.
    """

    actor_user_id: int | None = None
    client_ip: str | None = None
    environment: str | None = None
    region: str | None = None
    approvals: int | None = None
    now: dt.datetime | None = None
    request: Any = None
    authenticated_at: Any = _UNSET
    auth_factors: Any = _UNSET
    cache: dict = dataclasses.field(default_factory=dict)

    def session_authenticated_at(self) -> dt.datetime | None:
        if self.authenticated_at is _UNSET:
            self.authenticated_at = _read_authenticated_at(self.request)
        return self.authenticated_at

    def session_factors(self) -> frozenset[str] | None:
        if self.auth_factors is _UNSET:
            self.auth_factors = _read_factors(self.request)
        return self.auth_factors

    def clock(self) -> dt.datetime:
        from django.utils import timezone

        return self.now or timezone.now()


_attributes: contextvars.ContextVar[RequestAttributes | None] = contextvars.ContextVar(
    "astrolift_abac_attributes", default=None
)


def current_attributes() -> RequestAttributes | None:
    return _attributes.get()


def set_request_attributes(attrs: RequestAttributes | None) -> contextvars.Token:
    return _attributes.set(attrs)


def clear_request_attributes() -> None:
    _attributes.set(None)


@contextlib.contextmanager
def request_attributes(attrs: RequestAttributes | None) -> Iterator[None]:
    token = _attributes.set(attrs)
    try:
        yield
    finally:
        _attributes.reset(token)


@contextlib.contextmanager
def operation_attributes(**overrides: Any) -> Iterator[RequestAttributes]:
    """Add what a call site knows about the operation (``environment``,
    ``region``, ``approvals``) for the checks it runs inside the block.

    Shares the request's cache, so policies are still loaded once.
    """

    base = current_attributes() or RequestAttributes()
    attrs = dataclasses.replace(base, **overrides)
    attrs.cache = base.cache
    with request_attributes(attrs):
        yield attrs


def attributes_from_request(request, actor_user_id: int | None) -> RequestAttributes:
    return RequestAttributes(
        actor_user_id=actor_user_id,
        client_ip=policy_client_ip(request),
        request=request,
    )


def policy_client_ip(request) -> str | None:
    """The client IP an allowlist may trust.

    Not the left-most ``X-Forwarded-For`` entry the audit trail records:
    the client writes that one itself and could claim any address. The
    right-most entry is the one the proxy in front of us appended, and
    without a proxy it is ``REMOTE_ADDR``. Behind more than one proxy this
    is the inner proxy's address, so an allowlist of client ranges denies
    rather than admits.
    """

    import ipaddress

    meta = getattr(request, "META", None) or {}
    xff = meta.get("HTTP_X_FORWARDED_FOR", "") or ""
    candidate = xff.split(",")[-1].strip() if xff.strip() else (meta.get("REMOTE_ADDR") or "")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def attributes_for(actor_user_id: int | None) -> RequestAttributes:
    """The attributes that describe a check about ``actor_user_id``.

    The request's own when the actor is the request's user. For anyone else
    the operation attributes and the clock carry over and the person-bound
    ones are unavailable.
    """

    attrs = current_attributes()
    if attrs is None:
        return RequestAttributes(actor_user_id=actor_user_id)
    if attrs.actor_user_id is not None and attrs.actor_user_id == actor_user_id:
        return attrs
    other = RequestAttributes(
        actor_user_id=actor_user_id,
        environment=attrs.environment,
        region=attrs.region,
        approvals=attrs.approvals,
        now=attrs.now,
        authenticated_at=None,
        auth_factors=None,
    )
    other.cache = attrs.cache
    return other


def _read_authenticated_at(request) -> dt.datetime | None:
    """When the session's user last authenticated: the IdP's ``auth_time``
    for an SSO sign-in, else the tracked session row's creation. A bearer
    token has no session and no sign-in time."""

    if request is None or getattr(request, "_api_token", None) is not None:
        return None
    session = getattr(request, "session", None)
    if session is None:
        return None
    from astrolift_identity.step_up_sso import SESSION_SSO_AUTH_TIME_KEY

    try:
        raw = session.get(SESSION_SSO_AUTH_TIME_KEY)
    except Exception:  # noqa: BLE001 -- an unreadable session is no session
        return None
    if isinstance(raw, (int, float)):
        return dt.datetime.fromtimestamp(int(raw), tz=dt.UTC)
    key = getattr(session, "session_key", None)
    if not key:
        return None
    from astrolift_identity.models import AstroliftSession

    created = (
        AstroliftSession.objects.filter(session_key=key, revoked_at__isnull=True)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    return created


def _read_factors(request) -> frozenset[str] | None:
    """The sign-in factors the session carries: its login method and, while
    elevated, the step-up method."""

    if request is None or getattr(request, "_api_token", None) is not None:
        return None
    session = getattr(request, "session", None)
    if session is None:
        return None
    from astrolift_identity.session_elevation import get_status
    from astrolift_identity.sessions import SESSION_LOGIN_METHOD_KEY

    try:
        method = session.get(SESSION_LOGIN_METHOD_KEY)
        status = get_status(session)
    except Exception:  # noqa: BLE001
        return None
    factors = {str(method).lower()} if method else set()
    if status.elevated and status.method:
        factors.add(str(status.method).lower())
    return frozenset(factors) if factors else None


# ---------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ConditionOutcome:
    kind: str
    holds: bool | None  # None: could not be evaluated
    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class PolicyOutcome:
    policy_guid: str
    policy_slug: str
    effect: str
    applies: bool
    denies: bool
    detail: str
    conditions: tuple[ConditionOutcome, ...] = ()
    # Why the policy was taken to apply without knowing (an attribute the
    # check does not carry, an unknown key): fail-closed guesses, kept apart
    # so a simulation can tell a definite denial from a guessed one.
    assumed: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class AbacResult:
    denied: bool
    reason: str
    outcomes: tuple[PolicyOutcome, ...] = ()

    @property
    def applied(self) -> tuple[PolicyOutcome, ...]:
        return tuple(o for o in self.outcomes if o.applies)


NOT_DENIED = AbacResult(denied=False, reason="no policy applies")


# ---------------------------------------------------------------------
# The subject of one check
# ---------------------------------------------------------------------


@dataclasses.dataclass
class Subject:
    """Everything a policy can match on for one check.

    ``chain`` is the target and its ancestors, target first, as the
    resolver walks it. ``roles_at_target`` are the role slugs the actor
    holds that cover the target.
    """

    organization_id: int
    actor_user_id: int
    permission: str
    chain: list[tuple[str, int]]
    roles_at_target: frozenset[str]
    groups: frozenset[str]
    attrs: RequestAttributes
    slug_lookup: Callable[[str, int], str | None]

    def scope_slug(self, kind: str) -> str | None:
        for k, ident in self.chain:
            if k == kind:
                return self.slug_lookup(k, ident)
        return None


def _scope_slug_lookup(attrs: RequestAttributes) -> Callable[[str, int], str | None]:
    def lookup(kind: str, ident: int) -> str | None:
        key = ("slug", kind, ident)
        if key not in attrs.cache:
            attrs.cache[key] = _load_slug(kind, ident)
        return attrs.cache[key]

    return lookup


def _load_slug(kind: str, ident: int) -> str | None:
    if kind == "APP":
        from astrolift_registry.models import RegisteredApp

        return RegisteredApp.all_objects.filter(pk=ident).values_list("slug", flat=True).first()
    if kind == "PROJECT":
        from astrolift_identity.models import Project

        return Project.all_objects.filter(pk=ident).values_list("slug", flat=True).first()
    return None


# ---------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------


def org_policies(organization_id: int | None, attrs: RequestAttributes) -> list:
    """The org's live policies, loaded once per request."""

    if organization_id is None:
        return []
    key = ("policies", organization_id)
    if key not in attrs.cache:
        from astrolift_identity.models import Policy

        attrs.cache[key] = list(
            Policy.objects.filter(organization_id=organization_id).order_by("created_at", "pk")
        )
    return attrs.cache[key]


def evaluate(subject: Subject, policies: Iterable | None = None) -> AbacResult:
    """Every policy of the org against one check. Denied if any denies."""

    if policies is None:
        policies = org_policies(subject.organization_id, subject.attrs)
    outcomes = tuple(_evaluate_policy(p, subject) for p in policies)
    denying = [o for o in outcomes if o.denies]
    if denying:
        return AbacResult(denied=True, reason=f"abac: {denying[0].detail}", outcomes=outcomes)
    applied = [o for o in outcomes if o.applies]
    reason = (
        "no policy applies"
        if not applied
        else "applying policies allow it: " + ", ".join(o.policy_slug for o in applied)
    )
    return AbacResult(denied=False, reason=reason, outcomes=outcomes)


def applies_everywhere(policy) -> bool:
    """A policy that covers every scope of its org and matches on nothing
    the target decides. Only these can be folded into "held anywhere"
    answers (lists, the capability manifest)."""

    scope_ok = policy.scope_level == "ORG" or policy.scope_id is None
    actor = policy.actor_pattern if isinstance(policy.actor_pattern, dict) else None
    return (
        scope_ok
        and _pattern_is_empty(policy.resource_pattern)
        and actor is not None
        and not _values(actor.get("user_role_at_scope"))
    )


def _pattern_is_empty(pattern: Any) -> bool:
    return isinstance(pattern, dict) and not any(_values(v) for v in pattern.values())


def _values(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [v for v in raw if isinstance(v, str) and v.strip()]
    return []


def _evaluate_policy(policy, subject: Subject) -> PolicyOutcome:
    slug = policy.slug
    effect = policy.effect

    unknown: list[str] = []

    def outcome(applies: bool, denies: bool, detail: str, conditions=()) -> PolicyOutcome:
        return PolicyOutcome(
            policy_guid=str(policy.guid),
            policy_slug=slug,
            effect=effect,
            applies=applies,
            denies=denies,
            detail=detail,
            conditions=tuple(conditions),
            assumed=tuple(unknown) if applies else (),
        )

    # 1. Scope.
    if not _scope_covers(policy, subject):
        return outcome(False, False, f"policy {slug}: its scope does not cover this target")

    # 2. Action.
    pattern = (policy.action_pattern or "").strip() or "*"
    if not fnmatch.fnmatchcase(subject.permission, pattern):
        return outcome(False, False, f"policy {slug}: action {pattern!r} does not match {subject.permission}")

    # 3. Resource, then 4. actor. A definite mismatch anywhere means the
    # policy does not apply; an unknown means it applies (fail closed).
    for part in (
        _match_resource(policy.resource_pattern, subject),
        _match_actor(policy.actor_pattern, subject),
    ):
        matched, why = part
        if matched is False:
            return outcome(False, False, f"policy {slug}: {why}")
        if matched is None:
            unknown.append(why)

    # 5. Conditions.
    conditions, malformed = _conditions(policy.conditions, subject)
    applies_note = f" (assumed to apply: {'; '.join(unknown)})" if unknown else ""
    if malformed:
        return outcome(True, True, f"policy {slug} denies: {malformed}{applies_note}", conditions)

    failed = [c for c in conditions if c.holds is False]
    unevaluable = [c for c in conditions if c.holds is None]
    verb = "denies" if effect == "DENY" else "allows only when its conditions hold, and"
    if not conditions:
        if effect == "DENY":
            return outcome(True, True, f"policy {slug} denies {subject.permission}{applies_note}")
        return outcome(
            True, False, f"policy {slug} allows {subject.permission} with no conditions{applies_note}"
        )
    if failed:
        detail = "; ".join(f"{c.kind}: {c.detail}" for c in failed)
        return outcome(
            True, True, f"policy {slug} {verb} a condition does not hold ({detail}){applies_note}", conditions
        )
    if unevaluable:
        detail = "; ".join(f"{c.kind}: {c.detail}" for c in unevaluable)
        return outcome(
            True,
            True,
            f"policy {slug} {verb} a condition cannot be evaluated, so it denies ({detail}){applies_note}",
            conditions,
        )
    return outcome(True, False, f"policy {slug}: every condition holds{applies_note}", conditions)


def _scope_covers(policy, subject: Subject) -> bool:
    level = (policy.scope_level or "").upper()
    if level == "ORG":
        return policy.scope_id is None or policy.scope_id == subject.organization_id
    if level in ("TEAM", "PROJECT", "APP"):
        # A level with no id names no one scope; read it as every scope
        # (fail closed) rather than none.
        return policy.scope_id is None or (level, policy.scope_id) in subject.chain
    return True


# ---- resource and actor keys -----------------------------------------
#
# The keys a policy's ``resource_pattern`` and ``actor_pattern`` may name.
# The matchers below read these tables and nothing else, and the condition
# catalog query (``astroliftPolicyConditionCatalog``) publishes them, so the
# policy editor offers exactly the keys this module evaluates.


@dataclasses.dataclass(frozen=True, slots=True)
class ResourceKey:
    """One ``resource_pattern`` key: glob values matched against ``read``.

    ``read`` returns None when the value is not known. When
    ``missing_mismatches`` is set that is a definite mismatch (the target
    is not in any app), otherwise the value is unknown and the policy
    counts as applying (fail closed) with ``missing`` as the reason.
    """

    key: str
    label: str
    description: str
    read: Callable[[Subject], str | None]
    missing: str
    missing_mismatches: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class ActorKey:
    """One ``actor_pattern`` key: the actor matches when ``read`` holds any
    of the pattern's values."""

    key: str
    label: str
    description: str
    read: Callable[[Subject], frozenset[str]]
    mismatch: str


RESOURCE_KEYS: tuple[ResourceKey, ...] = (
    ResourceKey(
        key="app_slug",
        label="App",
        description="The slug of the app the target is in, read from the target itself.",
        read=lambda s: s.scope_slug("APP"),
        missing="the target is not in any app",
        missing_mismatches=True,
    ),
    ResourceKey(
        key="project_slug",
        label="Project",
        description="The slug of the project the target is in, read from the target itself.",
        read=lambda s: s.scope_slug("PROJECT"),
        missing="the target is not in any project",
        missing_mismatches=True,
    ),
    ResourceKey(
        key="env",
        label="Environment",
        description="The environment of the operation, when the call site supplies it.",
        read=lambda s: s.attrs.environment,
        missing="this check carries no environment",
    ),
    ResourceKey(
        key="region",
        label="Region",
        description="The region of the operation, when the call site supplies it.",
        read=lambda s: s.attrs.region,
        missing="this check carries no region",
    ),
)

ACTOR_KEYS: tuple[ActorKey, ...] = (
    ActorKey(
        key="user_in_groups",
        label="In IdP group",
        description="The actor is in any of these IdP groups, as asserted at their last sign-in.",
        read=lambda s: s.groups,
        mismatch="the actor is in none of the groups {values}",
    ),
    ActorKey(
        key="user_role_at_scope",
        label="Holds role here",
        description="The actor holds any of these role slugs on the target or an ancestor that reaches it.",
        read=lambda s: s.roles_at_target,
        mismatch="the actor holds none of the roles {values} here",
    ),
)

_RESOURCE_KEYS = {k.key: k for k in RESOURCE_KEYS}
_ACTOR_KEYS = {k.key: k for k in ACTOR_KEYS}


def _match_resource(pattern: Any, subject: Subject) -> tuple[bool | None, str]:
    if pattern in (None, {}):
        return True, ""
    if not isinstance(pattern, dict):
        return None, "resource pattern is not an object"
    unknown: list[str] = []
    for key, raw in pattern.items():
        values = _values(raw)
        if not values:
            if raw not in (None, "", []):
                unknown.append(f"resource {key} has an unreadable value")
            continue
        spec = _RESOURCE_KEYS.get(key)
        if spec is None:
            unknown.append(f"unknown resource key {key!r}")
            continue
        actual = spec.read(subject)
        if actual is None:
            if spec.missing_mismatches:
                return False, spec.missing
            unknown.append(spec.missing)
            continue
        if not any(fnmatch.fnmatchcase(actual, v) for v in values):
            return False, f"resource {key} {actual!r} does not match {values}"
    if unknown:
        return None, "; ".join(unknown)
    return True, ""


def _match_actor(pattern: Any, subject: Subject) -> tuple[bool | None, str]:
    if pattern in (None, {}):
        return True, ""
    if not isinstance(pattern, dict):
        return None, "actor pattern is not an object"
    unknown: list[str] = []
    for key, raw in pattern.items():
        values = _values(raw)
        if not values:
            if raw not in (None, "", []):
                unknown.append(f"actor {key} has an unreadable value")
            continue
        spec = _ACTOR_KEYS.get(key)
        if spec is None:
            unknown.append(f"unknown actor key {key!r}")
            continue
        if not spec.read(subject).intersection(values):
            return False, spec.mismatch.format(values=values)
    if unknown:
        return None, "; ".join(unknown)
    return True, ""


def _conditions(raw: Any, subject: Subject) -> tuple[list[ConditionOutcome], str]:
    if raw in (None, []):
        return [], ""
    if not isinstance(raw, list):
        return [], "its conditions are not a list"
    return [_condition(c, subject) for c in raw], ""


def _condition(raw: Any, subject: Subject) -> ConditionOutcome:
    if not isinstance(raw, dict):
        return ConditionOutcome("custom", None, "the condition is not an object")
    kind = raw.get("kind")
    handler = _HANDLERS.get(kind) if isinstance(kind, str) else None
    if handler is None:
        return ConditionOutcome(str(kind), None, f"unknown condition kind {kind!r}")
    try:
        return handler(raw, subject)
    except (TypeError, ValueError) as exc:
        return ConditionOutcome(kind, None, f"malformed condition: {exc}")


# ---- condition kinds --------------------------------------------------


def _parse_hhmm(text: str, *, end: bool) -> int:
    hh, _, mm = text.partition(":")
    h, m = int(hh), int(mm)
    if not (0 <= m < 60) or not (0 <= h < 24 or (end and h == 24 and m == 0)):
        raise ValueError(f"bad time {text!r}")
    return h * 60 + m


def _time_window(raw: dict, subject: Subject) -> ConditionOutcome:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    days = [d.lower() for d in _values(raw.get("days"))]
    hours = _values(raw.get("hours"))
    tz_name = raw.get("tz") or "UTC"
    if not days or any(d not in WEEKDAYS for d in days):
        return ConditionOutcome("time_window", None, f"malformed days {raw.get('days')!r}")
    if not hours:
        return ConditionOutcome("time_window", None, "no hours given")
    try:
        tz = ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        return ConditionOutcome("time_window", None, f"unknown time zone {tz_name!r}")
    ranges = []
    for h in hours:
        start_s, sep, end_s = h.partition("-")
        if not sep:
            return ConditionOutcome("time_window", None, f"malformed hours {h!r}")
        start, stop = _parse_hhmm(start_s.strip(), end=False), _parse_hhmm(end_s.strip(), end=True)
        if start == stop:
            return ConditionOutcome("time_window", None, f"empty range {h!r}")
        ranges.append((start, stop))
    local = subject.attrs.clock().astimezone(tz)
    minute = local.hour * 60 + local.minute
    today = WEEKDAYS[local.weekday()]
    yesterday = WEEKDAYS[(local.weekday() - 1) % 7]
    for start, stop in ranges:
        if start < stop:
            if today in days and start <= minute < stop:
                return ConditionOutcome("time_window", True, f"{local:%a %H:%M} {tz_name} is inside {hours}")
        elif (today in days and minute >= start) or (yesterday in days and minute < stop):
            return ConditionOutcome("time_window", True, f"{local:%a %H:%M} {tz_name} is inside {hours}")
    return ConditionOutcome("time_window", False, f"{local:%a %H:%M} {tz_name} is outside {days} {hours}")


def _ip_allowlist(raw: dict, subject: Subject) -> ConditionOutcome:
    cidrs = _values(raw.get("cidrs"))
    if not cidrs:
        return ConditionOutcome("ip_allowlist", None, "no ranges given")
    networks = [ipaddress.ip_network(c.strip(), strict=False) for c in cidrs]
    ip_text = subject.attrs.client_ip if _is_own_request(subject) else None
    if ip_text is None:
        return ConditionOutcome("ip_allowlist", None, "no client IP for this request")
    ip = ipaddress.ip_address(ip_text)
    if any(ip.version == n.version and ip in n for n in networks):
        return ConditionOutcome("ip_allowlist", True, f"{ip} is inside {cidrs}")
    return ConditionOutcome("ip_allowlist", False, f"{ip} is outside {cidrs}")


def _approval_required(raw: dict, subject: Subject) -> ConditionOutcome:
    need = raw.get("min_approvers", 1)
    if not isinstance(need, int) or isinstance(need, bool) or need < 1:
        raise ValueError(f"min_approvers {need!r}")
    have = subject.attrs.approvals
    if have is None:
        return ConditionOutcome("approval_required", None, "this check carries no approval count")
    if have >= need:
        return ConditionOutcome("approval_required", True, f"{have} of {need} approvals")
    return ConditionOutcome("approval_required", False, f"{have} of {need} approvals")


def _env_match(raw: dict, subject: Subject) -> ConditionOutcome:
    allowed = _values(raw.get("env_in"))
    if not allowed:
        return ConditionOutcome("env_match", None, "no environments given")
    env = subject.attrs.environment
    if env is None:
        return ConditionOutcome("env_match", None, "this check carries no environment")
    if env in allowed:
        return ConditionOutcome("env_match", True, f"environment {env!r} is in {allowed}")
    return ConditionOutcome("env_match", False, f"environment {env!r} is not in {allowed}")


def _device_assertion(raw: dict, subject: Subject) -> ConditionOutcome:
    required = {f.lower() for f in _values(raw.get("required_factors"))}
    if not required:
        return ConditionOutcome("device_assertion", None, "no factors given")
    factors = subject.attrs.session_factors() if _is_own_request(subject) else None
    if factors is None:
        return ConditionOutcome("device_assertion", None, "no signed-in session for this request")
    missing = sorted(required - factors)
    if missing:
        return ConditionOutcome("device_assertion", False, f"the session did not use {missing}")
    return ConditionOutcome("device_assertion", True, f"the session used {sorted(required)}")


def _freshness(raw: dict, subject: Subject) -> ConditionOutcome:
    limit = raw.get("max_session_age_minutes")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"max_session_age_minutes {limit!r}")
    at = subject.attrs.session_authenticated_at() if _is_own_request(subject) else None
    if at is None:
        return ConditionOutcome("freshness", None, "no sign-in time for this request")
    age = (subject.attrs.clock() - at).total_seconds() / 60
    if age <= limit:
        return ConditionOutcome("freshness", True, f"signed in {int(age)} min ago, within {limit}")
    return ConditionOutcome("freshness", False, f"signed in {int(age)} min ago, over {limit}")


def _is_own_request(subject: Subject) -> bool:
    return subject.attrs.actor_user_id is not None and subject.attrs.actor_user_id == subject.actor_user_id


# ---- the condition catalog ---------------------------------------------
#
# Every condition kind this module evaluates, with the fields its handler
# reads. ``_HANDLERS`` is built from this table, so a kind is evaluable
# exactly when the catalog lists it, and the policy editor builds its
# pickers from the same rows through ``astroliftPolicyConditionCatalog``.


@dataclasses.dataclass(frozen=True, slots=True)
class ConditionField:
    """One field of a condition, as its handler reads it.

    ``type`` is one of: ``weekdays`` (a list of ``options``), ``time_ranges``
    (a list of ``HH:MM-HH:MM``, an end before the start wraps past
    midnight), ``time_zone`` (an IANA name), ``cidrs`` (a list of IP
    ranges), ``strings`` (a list of values) or ``integer`` (at least
    ``minimum``).
    """

    name: str
    type: str
    label: str
    description: str
    required: bool = True
    default: Any = None
    options: tuple[str, ...] = ()
    minimum: int | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ConditionKind:
    """One condition kind: its fields, the attributes it needs, its handler.

    ``needs`` names what the check must carry: ``clock`` (always), ``client_ip``,
    ``session`` (person-bound: only for the request's own user, so a
    diagnosis or a simulation about someone else cannot evaluate it), or
    ``operation`` (supplied by the call site that knows the environment or
    the approval count). A condition that lacks what it needs cannot be
    evaluated, and an applying policy with it denies.
    """

    kind: str
    label: str
    description: str
    fields: tuple[ConditionField, ...]
    needs: str
    example: dict
    handler: Callable[[dict, Subject], ConditionOutcome]


CONDITION_KINDS: tuple[ConditionKind, ...] = (
    ConditionKind(
        kind="time_window",
        label="During hours",
        description="Holds while the evaluation clock, in the time zone, is inside one of the ranges on one of the days.",
        fields=(
            ConditionField("days", "weekdays", "Days", "The days the ranges apply on.", options=WEEKDAYS),
            ConditionField("hours", "time_ranges", "Hours", "One or more HH:MM-HH:MM ranges."),
            ConditionField(
                "tz", "time_zone", "Time zone", "An IANA time zone name.", required=False, default="UTC"
            ),
        ),
        needs="clock",
        example={"kind": "time_window", "days": ["mon", "fri"], "hours": ["09:00-18:00"], "tz": "UTC"},
        handler=_time_window,
    ),
    ConditionKind(
        kind="ip_allowlist",
        label="From network",
        description="Holds when the request's client IP is inside one of the ranges.",
        fields=(ConditionField("cidrs", "cidrs", "Ranges", "IPv4 or IPv6 ranges, like 10.0.0.0/8."),),
        needs="client_ip",
        example={"kind": "ip_allowlist", "cidrs": ["10.0.0.0/8"]},
        handler=_ip_allowlist,
    ),
    ConditionKind(
        kind="approval_required",
        label="Approved",
        description="Holds when the operation carries at least this many approvals.",
        fields=(
            ConditionField(
                "min_approvers",
                "integer",
                "Approvers",
                "The fewest approvals the operation needs.",
                required=False,
                default=1,
                minimum=1,
            ),
        ),
        needs="operation",
        example={"kind": "approval_required", "min_approvers": 2},
        handler=_approval_required,
    ),
    ConditionKind(
        kind="env_match",
        label="In environment",
        description="Holds when the operation's environment is one of these.",
        fields=(ConditionField("env_in", "strings", "Environments", "Environment names, exactly."),),
        needs="operation",
        example={"kind": "env_match", "env_in": ["staging"]},
        handler=_env_match,
    ),
    ConditionKind(
        kind="device_assertion",
        label="Signed in with",
        description="Holds when the session used every one of these sign-in factors.",
        fields=(
            ConditionField(
                "required_factors", "strings", "Factors", "Sign-in and step-up methods, like sso or webauthn."
            ),
        ),
        needs="session",
        example={"kind": "device_assertion", "required_factors": ["webauthn"]},
        handler=_device_assertion,
    ),
    ConditionKind(
        kind="freshness",
        label="Signed in recently",
        description="Holds when the session's user signed in no longer ago than this.",
        fields=(
            ConditionField(
                "max_session_age_minutes",
                "integer",
                "Minutes",
                "The oldest sign-in that still holds.",
                minimum=1,
            ),
        ),
        needs="session",
        example={"kind": "freshness", "max_session_age_minutes": 30},
        handler=_freshness,
    ),
)

_HANDLERS: dict[str, Callable[[dict, Subject], ConditionOutcome]] = {
    k.kind: k.handler for k in CONDITION_KINDS
}


def new_subject(
    *,
    organization_id: int,
    actor_user_id: int,
    permission: str,
    chain: list[tuple[str, int]],
    roles_at_target: Iterable[str],
    groups: Iterable[str],
    attrs: RequestAttributes | None = None,
) -> Subject:
    attrs = attrs if attrs is not None else attributes_for(actor_user_id)
    return Subject(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        permission=permission,
        chain=list(chain),
        roles_at_target=frozenset(roles_at_target),
        groups=frozenset(groups),
        attrs=attrs,
        slug_lookup=_scope_slug_lookup(attrs),
    )
