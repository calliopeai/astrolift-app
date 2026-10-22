"""Brain read tokens: how a machine client reads a brain through the edge (#2320).

A brain app sits behind the cluster's central-auth gate, which admits only a
browser session. A client the caller connects (Chat Studio, cykick) instead
gets a short-lived token from here, minted only after ``visible_brains()``
admits the caller, so team shares, the API token ceiling and archiving decide
it exactly as they decide the listing. The cluster edge verifies the token
against this install's JWKS and lets it into that one brain's hosts, with the
caller's identity in the usual headers (``edge_gateway.BRAIN_TOKEN_HEADER``).

A revoked share stops new tokens at once; a token already issued lives out
its 15 minutes (decision 2026-10-13: lag accepted over an ext-authz call on
every request).

Typed tokens (#2470, agreed on the issue with Mimwell #527): for a brain a
platform operator staged and enabled (``BrainTypedTokenRollout``), every read
token keeps its legacy claims and adds ``contract`` "1", ``sub_kind`` (``user``,
``agent`` or ``app``), ``org`` (the brain's organization GUID), ``teams`` (Team
GUIDs) and, for an agent or an app, ``agent`` (Workload GUID) or ``app``
(RegisteredApp GUID). They grant reads only. Every other brain gets the legacy
shapes unchanged.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import secrets
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from astrolift_registry.models import BrainTokenKey, RegisteredApp

TOKEN_TTL = dt.timedelta(minutes=15)
KEY_SIGNING_LIFETIME = dt.timedelta(days=30)
KEY_PUBLISHED_AFTER_RETIREMENT = dt.timedelta(days=1)
JWKS_PATH = "/api/brains/v1/jwks.json"
SCOPE = "brain:read"
ALGORITHM = "ES256"
TYPED_CONTRACT = "1"
TYPED_CLAIMS = frozenset({"contract", "sub_kind", "org", "teams", "agent", "app"})
#: This install's limits on a typed token: documented local refusals, not
#: shared contract guarantees. A token past either is refused, never trimmed.
MAX_TYPED_TEAMS = 64
MAX_TYPED_TOKEN_BYTES = 8192


class BrainTokenError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class TypedSubject:
    """Who a typed token speaks for; read only for a brain that issues them.

    ``user``: the person, with the teams they are a live member of in the
    brain's organization, within ``team_ceiling`` (``None``: the whole
    organization; empty: no teams). ``agent`` and ``app``: the Workload or
    RegisteredApp ``guid``, with no teams."""

    kind: str
    guid: str = ""
    team_ceiling: frozenset[str] | None = None


def _rollout(brain: RegisteredApp):
    from astrolift_registry.models import BrainTypedTokenRollout

    return BrainTypedTokenRollout.objects.filter(brain_id=brain.pk, organization_id=brain.organization_id)


def consumer_staged(brain: RegisteredApp) -> bool:
    """Whether ``brain``'s workloads get the typed verifier settings."""
    return _rollout(brain).exists()


def typed_issuance(brain: RegisteredApp) -> bool:
    """Whether every read token for ``brain`` is typed."""
    return _rollout(brain).filter(issuance_enabled_at__isnull=False).exists()


def member_teams(user: Any, organization: Any, team_ceiling: frozenset[str] | None) -> list[str]:
    """The GUIDs of the live teams of ``organization`` that ``user`` is a live
    member of, within ``team_ceiling``, in GUID order. Empty unless they are a
    live member of the organization itself."""
    from astrolift_identity.models import Member, Team

    live = Member.objects.filter(
        user_id=user.pk, user__is_active=True, is_active=True, lifecycle=Member.Lifecycle.ACTIVE
    )
    if not live.filter(scope_kind=Member.ScopeKind.ORG, scope_id=organization.pk).exists():
        return []
    teams = Team.objects.filter(
        organization_id=organization.pk,
        pk__in=live.filter(scope_kind=Member.ScopeKind.TEAM).values("scope_id"),
    )
    if team_ceiling is not None:
        teams = teams.filter(guid__in=sorted(team_ceiling))
    guids = [
        str(guid) for guid in teams.order_by("guid").values_list("guid", flat=True)[: MAX_TYPED_TEAMS + 1]
    ]
    if len(guids) > MAX_TYPED_TEAMS:
        raise BrainTokenError(
            f"a typed read token carries at most {MAX_TYPED_TEAMS} teams, and this person is on more "
            "in the brain's organization"
        )
    return guids


def typed_claims(subject: TypedSubject, *, brain: RegisteredApp, user: Any) -> dict[str, Any]:
    """The typed claims of a token whose ``sub`` and ``email`` come from ``user``."""
    claims: dict[str, Any] = {
        "contract": TYPED_CONTRACT,
        "sub_kind": subject.kind,
        "org": str(brain.organization.guid),
    }
    if subject.kind == "user":
        if getattr(user, "pk", None) is None or not str(getattr(user, "email", "") or "").strip():
            raise BrainTokenError("a typed read token names a person by their email, and this one has none")
        claims["teams"] = member_teams(user, brain.organization, subject.team_ceiling)
        return claims
    if subject.kind not in ("agent", "app"):
        raise BrainTokenError(f"unknown typed read token subject {subject.kind!r}")
    guid = str(uuid.UUID(subject.guid))
    if str(getattr(user, "guid", "") or "") != f"{subject.kind}:{guid}":
        raise BrainTokenError(f"a typed {subject.kind} token's subject must be {subject.kind}:{guid}")
    claims["teams"] = []
    claims[subject.kind] = guid
    return claims


def check_typed_size(token: str) -> None:
    if len(token) > MAX_TYPED_TOKEN_BYTES:
        raise BrainTokenError(
            f"the typed read token is {len(token)} bytes, over this install's {MAX_TYPED_TOKEN_BYTES}"
        )


def issuer() -> str:
    """This install's URL, the ``iss`` of every token and the JWKS's origin."""
    base = (getattr(settings, "APP_BASE_URL", "") or getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    if not base.startswith("https://") and not settings.DEBUG:
        raise BrainTokenError("APP_BASE_URL must be the install's https URL to issue brain tokens")
    return base


def jwks_uri() -> str:
    return issuer() + JWKS_PATH


#: What a brain's container verifies read tokens with (#2441): this
#: install's JWKS and issuer, and the brain's own guid as the audience.
MCP_VERIFICATION_ENV = ("BRAIN_MCP_JWKS_URL", "BRAIN_MCP_ISSUER", "BRAIN_MCP_AUDIENCE")
#: What a staged brain's container accepts typed tokens with (#2470): the
#: switch, and the organization GUID their ``org`` must equal.
TYPED_VERIFIER_ENV = ("BRAIN_MCP_TYPED_PRINCIPALS", "BRAIN_MCP_EXPECTED_ORG_GUID")


def stamp_mcp_verification_env(resources: list[dict[str, Any]], app: RegisteredApp) -> None:
    """Set ``MCP_VERIFICATION_ENV`` on every container of ``app``'s long-running
    workloads when ``app`` is a brain. The values come from the install and
    replace any the brain's own manifest sets. An install with no https URL
    issues no tokens, so nothing is set (and logged).

    A brain staged for typed tokens also gets ``TYPED_VERIFIER_ENV``, pinned
    to its organization's GUID; an ``env`` entry outranks the ``envFrom``
    Secrets, so the manifest cannot pick the organization. On any other brain
    the install removes both from ``env``; an ``[env]`` value or a secret
    bundle still reaches the pod through ``envFrom``, but no typed token is
    issued for that brain."""
    import logging

    from astrolift_manifest.app_kinds import BRAIN_KINDS

    if getattr(app, "app_kind", "") not in BRAIN_KINDS:
        return
    try:
        values = dict(zip(MCP_VERIFICATION_ENV, (jwks_uri(), issuer(), str(app.guid)), strict=True))
    except BrainTokenError:
        logging.getLogger(__name__).warning(
            "%s is a brain but the install has no https URL for tokens", app.slug
        )
        values = {}
    if values and consumer_staged(app):
        values.update(zip(TYPED_VERIFIER_ENV, ("true", str(app.organization.guid)), strict=True))
    owned = set(values) | set(TYPED_VERIFIER_ENV)
    for resource in resources:
        if resource.get("kind") not in ("Deployment", "StatefulSet"):
            continue
        pod = ((resource.get("spec") or {}).get("template") or {}).get("spec") or {}
        for container in pod.get("containers") or []:
            current = container.get("env") or []
            env = [e for e in current if e.get("name") not in owned]
            if values or len(env) != len(current):
                container["env"] = env + [{"name": k, "value": v} for k, v in values.items()]


def _new_key(**owner: Any) -> BrainTokenKey:
    from core.secrets import encrypt_at_rest

    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    kid = secrets.token_hex(8)
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    sealed = encrypt_at_rest(pem)
    return BrainTokenKey.objects.create(
        kid=kid,
        private_key_backend_kind=sealed.backend_kind,
        private_key_ciphertext=sealed.backend_ref,
        public_jwk={**public, "kid": kid, "use": "sig", "alg": ALGORITHM},
        **owner,
    )


def signing_key(*, now: dt.datetime | None = None) -> BrainTokenKey:
    """The key that signs now; rotated after ``KEY_SIGNING_LIFETIME``."""
    now = now or timezone.now()
    with transaction.atomic():
        live = list(
            BrainTokenKey.objects.select_for_update()
            .filter(retired_at__isnull=True, binding__isnull=True, run_binding__isnull=True)
            .order_by("-created_at")
        )
        current = live[0] if live and live[0].created_at > now - KEY_SIGNING_LIFETIME else None
        for key in live:
            if key is not current:
                key.retired_at = now
                key.save(update_fields=["retired_at", "updated_at", "version"])
        return current or _new_key()


def jwks(*, now: dt.datetime | None = None) -> dict[str, Any]:
    """The public keys the edge verifies against: the live ones and those
    retired within ``KEY_PUBLISHED_AFTER_RETIREMENT``, the keys of every
    live hosted-app binding (#2243), and those of every agent run's live
    binding to a brain (#2432)."""
    from django.db.models import Q

    from astrolift_registry.brain_apps import live_binding_keys
    from astrolift_registry.brain_runs import live_run_keys

    now = now or timezone.now()
    caller_keys = Q(binding__isnull=True, run_binding__isnull=True) & (
        Q(retired_at__isnull=True) | Q(retired_at__gt=now - KEY_PUBLISHED_AFTER_RETIREMENT)
    )
    rows = BrainTokenKey.objects.filter(
        caller_keys | Q(pk__in=live_binding_keys().values("pk")) | Q(pk__in=live_run_keys().values("pk"))
    ).order_by("-created_at")
    return {"keys": [dict(row.public_jwk) for row in rows]}


@dataclasses.dataclass(frozen=True)
class BrainReadToken:
    token: str
    expires_at: dt.datetime
    # TYPED_CONTRACT for a typed token, "" for a legacy one.
    contract: str = ""


def issue(
    *,
    user: Any,
    brain: RegisteredApp,
    now: dt.datetime | None = None,
    key: BrainTokenKey | None = None,
    claims: dict[str, str] | None = None,
    subject: TypedSubject | None = None,
) -> BrainReadToken:
    """A token for ``user`` to read ``brain``. The caller has already checked
    that ``visible_brains()`` admits ``brain``; this does not.

    ``key`` signs instead of the install's key, and ``claims`` are added to
    the token: an agent run's tokens carry its ``agent_task`` and are signed
    by the key of its binding to the brain (``brain_runs``, #2432).

    ``subject`` is what a typed token says about ``user``. It is read only
    when ``brain`` issues typed tokens, and then it is required."""
    from core.secrets import EncryptedSecret, decrypt

    if TYPED_CLAIMS.intersection(claims or {}):
        raise BrainTokenError("typed claims come from the brain's rollout, never from a caller")
    typed: dict[str, Any] = {}
    if typed_issuance(brain):
        if subject is None:
            raise BrainTokenError(
                "this brain issues typed read tokens and the request names no typed subject"
            )
        typed = typed_claims(subject, brain=brain, user=user)
    now = now or timezone.now()
    key = key or signing_key(now=now)
    pem = decrypt(
        EncryptedSecret(
            backend_kind=key.private_key_backend_kind, backend_ref=bytes(key.private_key_ciphertext)
        )
    )
    expires = now + TOKEN_TTL
    payload = {
        # First, so no extra claim can stand in for one the edge checks.
        **(claims or {}),
        "iss": issuer(),
        "aud": str(brain.guid),
        "sub": str(getattr(user, "guid", "") or user.pk),
        "email": str(getattr(user, "email", "") or ""),
        "scope": SCOPE,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_hex(12),
        **typed,
    }
    token = jwt.encode(payload, pem, algorithm=ALGORITHM, headers={"kid": key.kid})
    if typed:
        check_typed_size(token)
    return BrainReadToken(token=token, expires_at=expires, contract=TYPED_CONTRACT if typed else "")


def connection_descriptor(*, user: Any, brain: RegisteredApp) -> dict[str, Any]:
    """What a client stores to mount the brain: where, how and until when.
    A typed token carries the caller's teams within the API token's team,
    when the request came with a team-scoped one."""
    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_registry.brains import describe_brain
    from providers.k8s_native.edge_gateway import BRAIN_TOKEN_HEADER

    bearer = get_current_api_token()
    ceiling = frozenset({str(bearer.team.guid)}) if bearer is not None and bearer.team_id else None
    issued = issue(user=user, brain=brain, subject=TypedSubject("user", team_ceiling=ceiling))
    described = describe_brain(brain)
    return {
        "id": described["id"],
        "slug": described["slug"],
        "environments": described["environments"],
        "header": BRAIN_TOKEN_HEADER,
        "token": issued.token,
        "expires_at": issued.expires_at.isoformat(),
    }
