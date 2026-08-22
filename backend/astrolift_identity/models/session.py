"""
AstroliftSession — sidecar to the underlying django_session row.

The Django default session store tracks ``session_key`` + opaque blob
+ ``expire_date`` and nothing else. The platform needs more:

* ``client_kind`` — was this session issued to a browser, the CLI,
  the mobile app? Lets the platform apply per-kind policy (max
  concurrent sessions per user-kind, reveal-on-mobile blocks, etc.).
* ``last_seen_at`` — when did the holder of this session last call
  the API? Drives the "this CLI hasn't checked in for 90 days,
  auto-revoke" pruner and the operator-facing "stale device" hint.
* ``revoked_at`` + ``revocation_reason`` — explicit revoke vs. expiry
  vs. auto-prune so the audit trail can answer "why did this session
  die?" months later.

This model is populated by ``SessionTrackingMiddleware`` on every
authed request — that gives us a single write point so the schema
isn't lying about ``last_seen_at`` being current.

The underlying ``django_session`` row is the source of truth for
authentication; this row is the sidecar that surfaces and *governs*
that session. Revoke flows delete both: the ``django_session`` row so
the cookie immediately fails auth, and this row's soft-delete fields
so the audit trail survives.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class ClientKind(models.TextChoices):
    """Issuance kind of an :class:`AstroliftSession`.

    Stored as the lowercase value but surfaced uppercase via Strawberry
    (matches the FE convention for narrow string unions).
    """

    WEB = "web", "Web browser"
    CLI = "cli", "Command-line tool"
    MOBILE = "mobile", "Mobile app"
    BROWSER_EXTENSION = "browser_extension", "Browser extension"
    API_TOKEN = "api_token", "API token"


class LoginMethod(models.TextChoices):
    """How the session's initial authentication was completed (#526).

    Drives which step-up verifier the platform offers when a sensitive
    mutation hits ``@requires_elevation``. SSO-only installs can't
    satisfy a password prompt (the User row carries an unusable
    password hash), so the FE has to be told which credential to
    re-collect — that's what this column powers.

    Stored lowercase; choices kept open enough that a future
    enrolment flow (passkey-only sign-up, magic-link) can land its
    own value without forcing every prior install to backfill.
    """

    PASSWORD = "password", "Username + password"
    SSO = "sso", "External IdP (Auth0 / Cognito / OIDC)"
    MAGIC_LINK = "magic_link", "Magic link / one-time email"
    WEBAUTHN = "webauthn", "Passkey / WebAuthn"


# Per-kind defaults for ``MAX_SESSIONS_PER_CLIENT_KIND``. Surfaced as
# constants so tests + the per-user enforcer have a single source of
# truth for "what should happen on a fresh install where the operator
# hasn't tuned the Constance entry yet."
DEFAULT_MAX_SESSIONS_PER_CLIENT_KIND: dict[str, int] = {
    ClientKind.WEB.value: 5,
    ClientKind.CLI.value: 3,
    ClientKind.MOBILE.value: 3,
    ClientKind.BROWSER_EXTENSION.value: 2,
    ClientKind.API_TOKEN.value: 10,
}


# Per-kind staleness thresholds (seconds). ``PruneStaleSessionsWorkflow``
# reads the matching Constance entry per kind, falling back to these on
# a fresh install. Browsers age out fastest (operator usually doesn't
# notice an old tab); CLI sessions age out slowest (long-running CI
# tokens shouldn't get reaped just because they sat idle between
# pipeline runs).
DEFAULT_STALE_SESSION_TTL_SECONDS: dict[str, int] = {
    ClientKind.WEB.value: 30 * 24 * 3600,
    ClientKind.CLI.value: 90 * 24 * 3600,
    ClientKind.MOBILE.value: 60 * 24 * 3600,
    ClientKind.BROWSER_EXTENSION.value: 60 * 24 * 3600,
    ClientKind.API_TOKEN.value: 180 * 24 * 3600,
}


# Minimum interval (seconds) between ``last_seen_at`` writes for the
# same session row. A chatty client (the FE issues a query every few
# seconds) would otherwise hammer the DB; coarsening to 60s keeps the
# liveness signal useful (within a minute of truth) without write
# amplification.
LAST_SEEN_WRITE_THROTTLE_SECONDS = 60


class AttestationKind(models.TextChoices):
    """Which attestation flavour was applied to an :class:`AstroliftSession` (#496).

    Mobile-only by design — desktop browser / CLI clients can't pass
    Apple/Google attestation and stay on ``NONE``. Stored lowercase
    in the DB; surfaced uppercase to Strawberry.
    """

    NONE = "none", "Not attested"
    IOS_APPATTEST = "ios_appattest", "iOS App Attest"
    ANDROID_PLAY_INTEGRITY = "android_play_integrity", "Android Play Integrity"


class AttestationTrustLevel(models.TextChoices):
    """Outcome of the verification ceremony (#496).

    Lifted directly from the issue body so the FE narrow string union
    matches what the backend writes. ``NOT_ATTESTED`` is the default
    for any session that hasn't attempted attestation; ``UNKNOWN`` is
    the slot for "we got a response but neither pass nor fail
    semantics applied" (network blip, partial decode); ``FAILED`` is
    the explicit deny — caller produced an attestation, verification
    rejected it.
    """

    NOT_ATTESTED = "not_attested", "Not attested"
    GENUINE = "genuine", "Genuine"
    UNKNOWN = "unknown", "Unknown"
    FAILED = "failed", "Failed"


class AstroliftSession(BaseCoreModel):
    """Sidecar for one authenticated session.

    Owned by exactly one user. Bound to exactly one underlying auth
    artefact: either a ``django_session.session_key`` (browser /
    mobile / CLI device-flow) or an ``ApiToken.token_hash`` (API
    token sessions). Exactly one of the two FKs is populated.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="astrolift_sessions",
        on_delete=models.CASCADE,
    )

    # The ``django_session`` row this sidecar tracks. Indexed because
    # the lookup-by-key path runs on every authed request.
    session_key = models.CharField(max_length=64, blank=True, default="", db_index=True)

    # API-token-backed sessions don't have a ``django_session`` row;
    # they reference the ``ApiToken`` directly. Mutually exclusive with
    # ``session_key`` — enforced in the middleware that creates the row.
    api_token = models.ForeignKey(
        "astrolift_identity.ApiToken",
        related_name="sessions",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )

    # Org context at issuance time. Lets the admin-revoke gate check
    # "is this session for someone in the active org" without a join
    # through Member every time.
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="astrolift_sessions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    client_kind = models.CharField(
        max_length=32,
        choices=ClientKind.choices,
        default=ClientKind.WEB.value,
        db_index=True,
    )

    # How the operator authenticated to mint this session (#526). The
    # default ``password`` matches the pre-#526 world where every
    # local-login / dev-login row was implicitly password-backed; the
    # session-tracking middleware overrides it from the session bag
    # whenever the IdP login path set the key. ``@requires_elevation``
    # reads this back to tell the FE which credential to re-collect
    # for step-up — SSO sessions can't satisfy a password prompt
    # because the User row has an unusable password hash.
    login_method = models.CharField(
        max_length=32,
        choices=LoginMethod.choices,
        default=LoginMethod.PASSWORD.value,
        db_index=True,
    )

    # Free-form display name the client (CLI / mobile) supplies at
    # session-issue time, e.g. ``"alice@laptop"`` or ``"iPhone 17 Pro"``.
    # Truncated at 200 chars so a pathological label can't bloat the row.
    label = models.CharField(max_length=200, blank=True, default="")

    # Stamped by the session-tracking middleware on every authed
    # request; rate-limited per
    # :data:`LAST_SEEN_WRITE_THROTTLE_SECONDS` so we don't write to
    # the row on every poll.
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_seen_ip = models.GenericIPAddressField(null=True, blank=True)
    last_seen_agent = models.CharField(max_length=512, blank=True, default="")

    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Explicit revocation state. ``deleted_at`` is the soft-delete
    # marker (same row gets it too), but ``revoked_at`` answers the
    # "was this revoked vs. just aged out" question in the audit
    # trail. ``revocation_reason`` is a short tag drawn from
    # ``RevocationReason`` below.
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=64, blank=True, default="")
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # ---- Device attestation (#496) -----------------------------------
    # Mobile sessions only; ``NONE`` for browser / CLI / API-token
    # sessions where neither App Attest nor Play Integrity is
    # applicable. Set by ``attestSession`` mutation after verification
    # against Apple / Google services succeeds.
    attestation_kind = models.CharField(
        max_length=32,
        choices=AttestationKind.choices,
        default=AttestationKind.NONE.value,
        db_index=True,
    )

    # ``not_attested`` until the verifier returns; ``genuine`` after a
    # successful verification; ``failed`` when verification was
    # attempted and rejected; ``unknown`` when verification produced an
    # indeterminate result (network failure, partial response).
    attestation_trust_level = models.CharField(
        max_length=16,
        choices=AttestationTrustLevel.choices,
        default=AttestationTrustLevel.NOT_ATTESTED.value,
        db_index=True,
    )

    attestation_verified_at = models.DateTimeField(null=True, blank=True)

    # iOS App Attest stores the attested public key (PEM) here so
    # subsequent ``assertSession`` calls can verify the assertion
    # signature without re-running the full attestation. Empty on
    # Android (Play Integrity is per-token; no long-lived key).
    attestation_public_key = models.TextField(blank=True, default="")

    # iOS App Attest signed-counter — must strictly increase on every
    # assertion to defeat replay. Initialised to 0 at attestation
    # time per the App Attest spec.
    attestation_counter = models.IntegerField(default=0)

    # Verified claims from the attestation response: kept for audit /
    # forensics so a future incident review can answer "what did
    # Apple/Google tell us about this device when it attested".
    attestation_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "client_kind"], name="astrosess_user_kind_idx"),
            models.Index(fields=["organization", "user"], name="astrosess_org_user_idx"),
            models.Index(
                fields=["attestation_kind", "attestation_trust_level"],
                name="astrosess_attest_idx",
            ),
        ]


class RevocationReason:
    """Stable string tags for :attr:`AstroliftSession.revocation_reason`.

    Not a Django ``TextChoices`` because the set has to stay open
    enough for future tags (a security-incident-driven mass revoke
    pass would add its own) without forcing a schema migration on
    each addition.
    """

    USER_REVOKE = "user_revoke"
    ADMIN_REVOKE = "admin_revoke"
    QUOTA_EVICTION = "quota_eviction"
    AUTO_STALE = "auto_stale"
    LOGOUT = "logout"
    # An IdP deprovisioned the person over SCIM (#78). Distinct from
    # ``admin_revoke`` so the audit trail says the removal came from
    # the directory, not from an operator inside the product.
    SCIM_DEPROVISION = "scim_deprovision"
