"""DeviceFlowSession — browser-mediated CLI / mobile login (#475).

Spec 27 §4.1 surface: an unauthenticated client (CLI, mobile app, IDE
plugin) hits ``POST /api/cli/v1/auth/start``, gets a ``session_id`` +
a browser ``login_url``, polls ``POST /api/cli/v1/auth/complete``
until the row's ``state`` moves out of ``pending``.

Lifecycle::

                  start             approve            complete (200)
    [no row] ───────────────▶ pending ───────▶ approved ──────────────▶ consumed
                                  │                                       (issued)
                                  ├──── deny / TTL ───▶ denied
                                  └──── TTL elapsed ──▶ expired

The polling client sees state transitions reflected in HTTP status
codes — 202 pending, 200 approved (and only the *first* call to
``/complete`` after approval mints the token pair; subsequent polls
hit the consumed-or-not gate at the view layer).

Why one model not two (session + issued tokens):

* The issued ``access_token`` plaintext is an ``alft_at_…`` bearer
  minted by :mod:`astrolift_identity.api_tokens`, persisted on a
  companion ``ApiToken`` row, and authenticated by the existing
  :class:`ApiTokenAuthMiddleware`. The device-flow row only stores
  the ``ApiToken.id`` it minted so it can be revoked transitively
  if the session is invalidated.
* The ``refresh_token`` is a separate opaque secret stored hashed
  on this row (sha256 of ``alft_rt_…``); single-use, rotated on
  every ``/refresh``. The most recent rotation lives here so a
  replay attempt is detectable by hash miss.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class DeviceFlowSession(BaseCoreModel):
    # ---- lifecycle state -------------------------------------------------
    STATE_PENDING = "pending"
    STATE_APPROVED = "approved"
    STATE_DENIED = "denied"
    STATE_CONSUMED = "consumed"
    STATE_EXPIRED = "expired"
    # Mobile enrollment (#494): the operator's web session already
    # vouched for this row at generation time, so the row skips
    # ``pending`` → ``approved`` and lands at ``pre_approved`` waiting
    # for the mobile to redeem the QR. The redemption transitions
    # straight to ``consumed`` like the browser-approved path.
    STATE_PRE_APPROVED = "pre_approved"

    STATE_CHOICES = (
        (STATE_PENDING, "pending"),
        (STATE_APPROVED, "approved"),
        (STATE_PRE_APPROVED, "pre_approved"),
        (STATE_DENIED, "denied"),
        (STATE_CONSUMED, "consumed"),
        (STATE_EXPIRED, "expired"),
    )

    # ---- public polling identifier --------------------------------------
    # Opaque URL-safe string returned to the CLI on /start as
    # ``session_id``. Distinct from ``guid`` (UUIDv7, internal-ish)
    # because the CLI logs this value and embeds it in the browser
    # ``login_url`` — keeping it separate from the row's primary
    # public key lets us rotate the polling identifier without
    # renaming the canonical guid.
    session_guid = models.CharField(max_length=64, unique=True, db_index=True)

    # ---- client identification ------------------------------------------
    # Human-readable label the CLI / mobile client sends (or that we
    # infer from User-Agent). Surfaced on the approval page so the
    # operator knows what they're approving, and on the
    # ``astroliftMySessions`` listing post-issuance.
    client_label = models.CharField(max_length=128, blank=True, default="")
    # Free-form discriminator (``cli`` / ``mobile`` / ``browser``).
    # Lets ``astroliftMySessions`` group by kind. Not enum-bound here
    # so new client kinds don't require a migration.
    client_kind = models.CharField(max_length=32, blank=True, default="cli")

    # Truncated User-Agent string captured at start time. Forensic
    # use only — surfaced on the approval page so the user can
    # spot a flow they didn't initiate.
    user_agent = models.CharField(max_length=512, blank=True, default="")
    # Caller IP at /start time. Same forensic purpose.
    client_ip = models.GenericIPAddressField(null=True, blank=True)

    # ---- state machine --------------------------------------------------
    state = models.CharField(
        max_length=16,
        choices=STATE_CHOICES,
        default=STATE_PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    denied_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    # Last time the polling client hit /complete. Used by the per-
    # session rate limiter to refuse over-eager polling.
    polled_at = models.DateTimeField(null=True, blank=True)

    # ---- approving user -------------------------------------------------
    approved_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="approved_device_flows",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="device_flow_sessions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # ---- tokens (hashes; plaintext disclosed once) ---------------------
    # Issued ``alft_at_…`` access token's ApiToken row. SET_NULL on
    # revocation so the device-flow row sticks around for audit even
    # after the bearer is gone.
    api_token = models.ForeignKey(
        "astrolift_identity.ApiToken",
        related_name="device_flow_sessions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # SHA-256 of the most recent refresh token plaintext. Rotated on
    # every successful ``/refresh``. NULL until the session is
    # consumed.
    refresh_token_hash = models.CharField(max_length=128, blank=True, default="", db_index=True)
    # Last4 of the refresh plaintext for UI identification without
    # leaking the secret.
    refresh_token_last_4 = models.CharField(max_length=4, blank=True, default="")
    refresh_token_expires_at = models.DateTimeField(null=True, blank=True)

    # ---- access TTL hint stamped at consume time -----------------------
    # The access bearer is an ApiToken; its expiry is on that row. We
    # also stamp the *issuing* TTL here for audit consistency
    # (operators investigating a leaked token see when this session
    # minted it).
    access_token_expires_at = models.DateTimeField(null=True, blank=True)

    # ---- mobile QR enrollment (#494) -----------------------------------
    # SHA-256 of an ``alft_enroll_…`` token the operator generated via
    # ``generateInstallEnrollmentQr``. Mobile presents the plaintext on
    # ``/api/cli/v1/auth/start`` and the row short-circuits the browser
    # approval step — the operator's web session is the proof, the
    # enrollment token is the handoff. Single-use: cleared on first
    # consumption.
    enrollment_token_hash = models.CharField(max_length=128, blank=True, default="", db_index=True)
    enrollment_token_last_4 = models.CharField(max_length=4, blank=True, default="")
    enrollment_token_expires_at = models.DateTimeField(null=True, blank=True)
    # When the enrollment token was burned by the mobile client. NULL
    # means the QR is still claimable; non-NULL means terminal for the
    # enrollment side regardless of session state.
    enrollment_consumed_at = models.DateTimeField(null=True, blank=True)
    # Operator-supplied label that distinguishes this enrollment in
    # ``astroliftMyEnrollments`` listings ("Sarah's iPhone"). Falls back
    # to "" when the operator left it blank.
    enrollment_label = models.CharField(max_length=128, blank=True, default="")
    # Discriminator: did this row originate as a browser-approved CLI
    # flow (``STATE_PENDING`` at /start) or a pre-approved mobile
    # enrollment (``STATE_PRE_APPROVED`` at generation time)? Lets the
    # audit + listings UIs filter without joining a second table.
    ORIGIN_DEVICE_FLOW = "device_flow"
    ORIGIN_ENROLLMENT = "enrollment"
    ORIGIN_CHOICES = (
        (ORIGIN_DEVICE_FLOW, "device_flow"),
        (ORIGIN_ENROLLMENT, "enrollment"),
    )
    origin = models.CharField(
        max_length=16,
        choices=ORIGIN_CHOICES,
        default=ORIGIN_DEVICE_FLOW,
        db_index=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["state", "expires_at"], name="dfs_state_exp_idx"),
            models.Index(
                fields=["approved_user", "state"],
                name="dfs_user_state_idx",
            ),
            # Cap concurrent unexpired enrollments per operator (#494).
            # The rate-limiter query is "how many pre_approved rows
            # does this user own past `now`?" — covered by this
            # composite.
            models.Index(
                fields=["approved_user", "origin", "state"],
                name="dfs_user_origin_state_idx",
            ),
        ]
        verbose_name = "Device-flow session"
        verbose_name_plural = "Device-flow sessions"

    def __str__(self) -> str:
        label = self.client_label or self.client_kind or "device-flow"
        return f"{label} [{self.state}]"

    # ---- helpers -------------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        """True when no further state transitions are valid.

        Consumed, denied, and expired are all final — once we land
        here the row stays here and the polling client will receive
        a stable error code on subsequent ``/complete`` calls.
        """
        return self.state in {self.STATE_CONSUMED, self.STATE_DENIED, self.STATE_EXPIRED}
