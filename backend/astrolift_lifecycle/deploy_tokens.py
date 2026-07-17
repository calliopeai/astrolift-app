"""
Deploy-token issue + rotate + verify with grace window (#143).

Three primitives:

* ``issue_token(app, name, scopes, expires_in_days, by_user_id)`` —
  create a fresh token. Returns plaintext + the row.
* ``rotate_token(token, *, grace_period_seconds, by_user_id)`` —
  generate a new secret, park the old hash in
  ``previous_token_hash``, stamp ``previous_token_expires_at``.
  CI runners using the old token keep working until grace expires;
  rotators don't have to coordinate roll-out.
* ``verify_token(plaintext, app)`` — check incoming requests. Looks
  up by hash; honours the previous-hash grace window. Returns the
  DeployToken row when valid, None otherwise.

The plaintext is the only ``alft_…`` formatted secret we emit; we
hash with SHA-256 before persisting so a DB leak doesn't yield
usable credentials.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import logging
import secrets

from django.db.models import Q
from django.utils import timezone

log = logging.getLogger(__name__)

DEFAULT_GRACE_PERIOD_SECONDS = 24 * 60 * 60  # 24h — matches the UI rotate-dialog copy (#425)
DEFAULT_TTL_DAYS = 365
MAX_TTL_DAYS = 365 * 5
PLAINTEXT_PREFIX = "alft_dt_"
# Legacy prefix shipped by the early ``create_deploy_token`` /
# ``rotate_deploy_token`` mutations before #449 canonicalised the mint
# sites onto ``PLAINTEXT_PREFIX``. Verification accepts both shapes
# while the Constance ``DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED`` flag is
# on; operators flip it off after a rotation cycle confirms no in-flight
# token still wears the legacy shape. Keep the constant exported so the
# middleware and auth-scheme classifier reuse the exact bytes.
LEGACY_PLAINTEXT_PREFIX = "alfdt_"


@dataclasses.dataclass(slots=True, frozen=True)
class IssuedToken:
    plaintext: str
    token_hash: str
    last4: str


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _mint() -> IssuedToken:
    plaintext = PLAINTEXT_PREFIX + secrets.token_urlsafe(32)
    return IssuedToken(
        plaintext=plaintext,
        token_hash=_hash(plaintext),
        last4=plaintext[-4:],
    )


def issue_token(
    *,
    app,
    name: str,
    scopes: list[str] | None = None,
    expires_in_days: int | None = DEFAULT_TTL_DAYS,
    by_user_id: int | None = None,
):
    """Mint a brand-new DeployToken row for ``app``.

    Returns ``(deploy_token_row, plaintext)``. Plaintext is the
    one-time secret to surface to the operator; the row only carries
    the SHA-256 hash + last4.
    """
    from astrolift_lifecycle.models import DeployToken

    minted = _mint()
    days = expires_in_days if expires_in_days is None else min(int(expires_in_days), MAX_TTL_DAYS)
    expires_at = timezone.now() + dt.timedelta(days=int(days)) if days else None
    row = DeployToken.objects.create(
        registered_app=app,
        name=name.strip(),
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
        scopes=list(scopes or ["app.deploy"]),
        created_by_user_id=by_user_id,
        expires_at=expires_at,
    )
    return row, minted.plaintext


def rotate_token(
    token,
    *,
    grace_period_seconds: int | None = None,
    immediate: bool = False,
):
    """Mint a new secret onto an existing DeployToken row.

    ``grace_period_seconds`` defaults to 1 hour; pass ``immediate=True``
    to skip the grace window (forced revoke of the old secret —
    breaks CI immediately, only do this on token compromise).

    Returns ``(deploy_token_row, plaintext_new_token)``.
    """
    grace = (
        0
        if immediate
        else (grace_period_seconds if grace_period_seconds is not None else DEFAULT_GRACE_PERIOD_SECONDS)
    )

    minted = _mint()
    now = timezone.now()
    token.previous_token_hash = "" if immediate else token.token_hash
    token.previous_token_expires_at = None if immediate else now + dt.timedelta(seconds=grace)
    token.token_hash = minted.token_hash
    token.token_last_4 = minted.last4
    token.last_rotated_at = now
    # Bump revoked back to false in case the operator rotated a
    # previously-revoked token (unusual but valid recovery path).
    token.is_revoked = False
    token.save(
        update_fields=[
            "token_hash",
            "token_last_4",
            "previous_token_hash",
            "previous_token_expires_at",
            "last_rotated_at",
            "is_revoked",
            "updated_at",
            "version",
        ]
    )
    return token, minted.plaintext


def is_acceptable_prefix(plaintext: str) -> bool:
    """Return True when ``plaintext`` carries a prefix the verifier
    would consider for lookup. Always accepts the canonical
    ``alft_dt_`` shape; accepts the legacy ``alfdt_`` shape only while
    the Constance ``DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED`` flag is on.

    Exposed so the bearer middleware and the auth-scheme classifier can
    use the same gate the verifier does — keeps prefix policy in one
    place (#449)."""
    if not plaintext:
        return False
    if plaintext.startswith(PLAINTEXT_PREFIX):
        return True
    if plaintext.startswith(LEGACY_PLAINTEXT_PREFIX) and legacy_prefix_accepted_from_constance():
        return True
    return False


def verify_token(plaintext: str, app=None):
    """Look up a DeployToken by plaintext, honouring the rotation
    grace window.

    Returns the matching row when:
      * its ``token_hash`` matches AND it isn't revoked AND not expired
      * OR its ``previous_token_hash`` matches AND we're inside the
        grace window AND it isn't revoked.

    ``app`` is optional; if passed we further restrict to that
    registered_app so a leaked token can't be used against the
    wrong app.

    Prefix policy (#449): the canonical ``alft_dt_`` is always
    accepted; the legacy ``alfdt_`` shape (issued by pre-#449 mint
    sites) is accepted while the Constance
    ``DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED`` flag is on, and a warning
    + audit event fires on every legacy-prefix accept so operators
    can size the straggler population before flipping the flag off.
    """
    from astrolift_lifecycle.models import DeployToken

    if not plaintext:
        return None
    is_legacy = plaintext.startswith(LEGACY_PLAINTEXT_PREFIX) and not plaintext.startswith(PLAINTEXT_PREFIX)
    if not is_acceptable_prefix(plaintext):
        return None
    digest = _hash(plaintext)
    now = timezone.now()
    qs = DeployToken.objects.filter(
        Q(token_hash=digest) | (Q(previous_token_hash=digest) & Q(previous_token_expires_at__gt=now)),
        deleted_at__isnull=True,
        is_revoked=False,
    )
    if app is not None:
        qs = qs.filter(registered_app=app)
    row = qs.first()
    if row is None:
        return None
    if row.expires_at and row.expires_at <= now:
        return None
    if is_legacy:
        _emit_legacy_prefix_accepted(row)
    return row


def _emit_legacy_prefix_accepted(row) -> None:
    """Surface a warning + audit event when verify accepts a legacy
    ``alfdt_`` token. Keeps an audit-visible trail of straggler
    callers so operators know when it's safe to flip the
    ``DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED`` flag off.

    Failures inside the audit writer must never break verification —
    a deploy-token caller's auth path is the wrong place to surface
    an event-pipeline outage. Both the log and the event emit run
    inside a try/except for that reason."""
    try:
        log.warning(
            "deploy_token.legacy_prefix_accepted",
            extra={
                "deploy_token_id": str(getattr(row, "guid", "")),
                "registered_app_id": getattr(row, "registered_app_id", None),
            },
        )
    except Exception:
        pass
    try:
        from core.events import Event

        Event.emit(
            "deploy_token.legacy_prefix_accepted",
            payload={
                "deploy_token_guid": str(getattr(row, "guid", "")),
                "token_last_4": getattr(row, "token_last_4", ""),
            },
            resource_kind="deploy_token",
            resource_id=str(getattr(row, "guid", "")),
            # The token row already carries the app FK — thread it so
            # the event lands on the per-app feed (#1111).
            registered_app_id=getattr(row, "registered_app_id", None),
        )
    except Exception:
        # Audit pipeline outages must not break deploy-token auth.
        pass


def touch_deploy_token(token, *, ip: str | None = None, user_agent: str | None = None) -> None:
    """Stamp ``last_used_at`` / ``last_used_ip`` / ``last_used_agent`` (#425).

    Called on every request that authed against this deploy token —
    the deploy-token middleware fires it after :func:`verify_token`
    returns a live row. ``update_fields`` advances the audit
    ``Tracking`` columns (``updated_at`` / ``version``) so other row
    data doesn't accidentally re-save. An invalid IP coerces to
    ``None`` because ``GenericIPAddressField`` would otherwise raise.

    Mirrors :func:`astrolift_identity.api_tokens.touch_token` so both
    bearer kinds carry the same forensic surface.
    """
    now = timezone.now()
    token.last_used_at = now
    token.last_used_ip = ip if _is_ip(ip) else None
    token.last_used_agent = (user_agent or "")[:512]
    token.save(
        update_fields=[
            "last_used_at",
            "last_used_ip",
            "last_used_agent",
            "updated_at",
            "version",
        ]
    )


def client_ip_from_request(request) -> str | None:
    """Extract the caller's IP, honouring ``X-Forwarded-For``.

    Behind ALB / Cloudfront we receive a comma-separated chain; the
    left-most entry is the original client. Falls back to
    ``REMOTE_ADDR`` (single proxy / direct connect) when no XFF is
    present. Returns ``None`` when we can't resolve a usable IP.

    Identical contract to
    :func:`astrolift_identity.api_tokens.client_ip_from_request`;
    duplicated here so deploy-token-only deployments don't pull in
    the identity app's middleware module.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "") if hasattr(request, "META") else ""
    if xff:
        first = xff.split(",")[0].strip()
        if _is_ip(first):
            return first
    remote = request.META.get("REMOTE_ADDR", "") if hasattr(request, "META") else ""
    return remote if _is_ip(remote) else None


def user_agent_from_request(request) -> str:
    """Truncate the request User-Agent to ``last_used_agent`` column width."""
    if not hasattr(request, "META"):
        return ""
    return (request.META.get("HTTP_USER_AGENT", "") or "")[:512]


def _is_ip(value: str | None) -> bool:
    """Cheap parse: GenericIPAddressField needs a well-formed v4/v6."""
    if not value:
        return False
    import ipaddress

    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


# ---- rotation grace window (Constance-tunable, #425) ----------------

MIN_ROTATION_GRACE_SECONDS = 60
MAX_ROTATION_GRACE_SECONDS = 7 * 24 * 60 * 60  # 7 days


def rotation_grace_seconds_from_constance() -> int:
    """Resolve the operator-tunable deploy-token rotation grace.

    Defaults to ``DEFAULT_GRACE_PERIOD_SECONDS`` (24h) when Constance
    is unavailable (DB not migrated, plugin disabled, test bootstrap)
    so rotation never fails on a side-channel that's out of the
    rotation's control. Mirrors the equivalent
    :func:`astrolift_operations.webhook_rotation.grace_seconds_from_constance`
    helper.
    """
    try:
        from constance import config as constance_config

        raw = getattr(
            constance_config,
            "DEPLOY_TOKEN_ROTATION_GRACE_SECONDS",
            DEFAULT_GRACE_PERIOD_SECONDS,
        )
        value = int(raw)
    except Exception:
        return DEFAULT_GRACE_PERIOD_SECONDS
    if value < MIN_ROTATION_GRACE_SECONDS:
        return MIN_ROTATION_GRACE_SECONDS
    if value > MAX_ROTATION_GRACE_SECONDS:
        return MAX_ROTATION_GRACE_SECONDS
    return value


def legacy_prefix_accepted_from_constance() -> bool:
    """Resolve whether the verifier should accept the legacy
    ``alfdt_`` prefix (#449).

    Defaults to ``True`` when Constance is unavailable so an in-flight
    DB row issued by a pre-#449 mint can still auth — refusing-by-default
    here would silently break every existing CI runner the moment this
    code ships. Operators flip the flag to ``False`` once they've
    confirmed (via the ``deploy_token.legacy_prefix_accepted`` audit
    stream) that no callers carry the legacy prefix anymore.
    """
    try:
        from constance import config as constance_config

        raw = getattr(
            constance_config,
            "DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED",
            True,
        )
    except Exception:
        return True
    return bool(raw)
