"""``AttestationChallenge`` — server-issued nonces for App Attest / Play Integrity (#496).

The attestation handshake is replay-protected by a nonce the server
issues, the device incorporates into its signed attestation, and the
server then verifies on submission. A nonce is:

* **One-shot.** Consumed on the first valid submission and never re-usable.
* **TTL-bound.** Expires after ``ATTESTATION_CHALLENGE_TTL_SECONDS``
  (default 300s / 5min) to bound the replay window.
* **Bound to a user + kind.** A challenge issued for an Android
  Play-Integrity request can't be passed to an iOS App-Attest verifier
  on a different user — narrows the surface a leaked challenge gives.

Stored in its own row (not the session) because:

1. Multiple in-flight challenges are legal (operator restarts the
   ceremony, two devices attesting for the same user concurrently).
2. The challenge predates the session bind: the device requests a
   challenge before the attestation succeeds; the session row only
   gets the verified blob after.

This is *not* a ``BaseCoreModel`` — challenges aren't business objects,
they're ephemeral cryptographic state. They get hard-deleted on
consumption + on the periodic sweeper that drops expired rows. The
schema is minimal to keep the write path cheap (a fresh row per
mobile login attempt).
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

# 32-byte URL-safe nonce → 43 base64 chars. Apple's App Attest spec
# only requires the challenge to be present in the attestation
# certificate's 1.2.840.113635.100.8.2 extension; 32 bytes gives
# >256-bit collision resistance which is well above any practical
# attack budget.
_NONCE_BYTES = 32


def _make_nonce() -> str:
    """Return a fresh URL-safe random nonce string."""
    return secrets.token_urlsafe(_NONCE_BYTES)


class AttestationChallenge(models.Model):
    """One pending attestation nonce.

    Rows are deleted on consumption (atomic) so the unique-by-nonce
    constraint enforces single-use. The sweeper removes expired-but-
    unconsumed rows on a schedule.
    """

    # ``token_urlsafe(32)`` produces 43 chars; round up to 64 to leave
    # head-room for any future bump to 48-byte nonces without a
    # migration.
    nonce = models.CharField(max_length=64, unique=True, default=_make_nonce)

    # Which attestation flavour this challenge was issued for. Validated
    # at submission time so a challenge issued for iOS can't be passed
    # to the Android verifier.
    kind = models.CharField(max_length=32, db_index=True)

    # Bind to the user that requested it — prevents one user's
    # challenge from being re-used to attest a different user's
    # session.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="attestation_challenges",
        on_delete=models.CASCADE,
    )

    # When this nonce stops being valid. The verifier checks this
    # before consuming the row.
    expires_at = models.DateTimeField(db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "kind"], name="attestchal_user_kind_idx"),
        ]

    def is_expired(self, *, now=None) -> bool:
        return (now or timezone.now()) >= self.expires_at

    def __str__(self) -> str:
        return f"AttestationChallenge(user={self.user_id}, kind={self.kind}, expires_at={self.expires_at})"
