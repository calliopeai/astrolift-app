"""DomainSessionHandoff -- the cross-domain session handoff token (#1631).

An external custom domain cannot be gated by the central auth host's cookie:
that cookie is scoped to the parent zone of ``auth.<base-zone>``, and a
response from one registrable domain cannot set a cookie for an unrelated
one. That is a browser rule, not an oauth2-proxy flag, so
``--cookie-domain`` does not help. See
``core.app_deploy.custom_domain_edge_auth_state`` for the long form.

The shape that does work while keeping **one IdP callback per cluster** --
the constraint, because the IdP is not Cognito on every cloud -- is a
handoff. The auth host completes the IdP flow at its existing single
callback, mints one of these, and redirects the browser to the custom
domain carrying nothing but an opaque id. The domain's own proxy exchanges
that id server-to-server for the verified identity and sets its own
first-party cookie.

So this row is the only thing standing between "logged in at the auth host"
and "logged in at customer.com", and every property below is load-bearing
rather than defensive:

* **Hash only, never plaintext.** As ``TaskToken.token_hash``, which is the
  precedent this follows rather than inventing a second scheme.
* **60-second TTL.** It has to survive one redirect. ``TaskToken``'s 72h is
  right for an agent run and would be a long window to replay a session
  grant in.
* **Single use, enforced by a conditional UPDATE.** See
  ``consume_domain_handoff``; a read-then-write is a race two concurrent
  redirects can both win, and single-use whose second attempt succeeds is
  not single-use.
* **Audience-bound.** A token minted for ``a.com`` must be refused at
  ``b.com``, or one gated domain becomes a session-minting oracle for
  every other domain on the cluster.

Not a ``Tracking``/``BaseCoreModel`` business object, and deliberately so:
a spent credential should leave, not linger soft-deleted. ``TaskToken`` is
a plain ``models.Model`` for the same reason.
"""

from __future__ import annotations

import datetime as dt

from django.db import models

# Long enough for one redirect on a slow connection, short enough that a
# leaked URL in a proxy log or a Referer header is worthless by the time
# anyone reads it.
HANDOFF_TTL = dt.timedelta(seconds=60)


class DomainSessionHandoff(models.Model):
    """A one-shot, audience-bound grant to establish a session on one host."""

    custom_domain = models.ForeignKey(
        "astrolift_lifecycle.CustomDomain",
        on_delete=models.CASCADE,
        related_name="session_handoffs",
        help_text=(
            "The domain this grant is for. Carried as a relation rather than "
            "a bare hostname so 'who established a session on which custom "
            "domain' resolves to an app and an org for audit."
        ),
    )
    hostname = models.CharField(
        max_length=255,
        help_text=(
            "The audience, denormalised from the domain at mint time and "
            "compared on exchange. Denormalised on purpose: renaming the "
            "domain row must not silently widen an already-minted token's "
            "audience to the new hostname."
        ),
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hex digest of the plaintext; the plaintext is never stored.",
    )
    subject = models.CharField(
        max_length=255,
        help_text=(
            "The verified IdP subject the auth host authenticated. The "
            "domain's proxy mints its own session from this rather than "
            "receiving a copy of the auth host's."
        ),
    )
    email = models.EmailField(
        blank=True,
        default="",
        help_text="Verified email claim when the IdP supplies one; blank otherwise.",
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Stamped by the conditional UPDATE in ``consume_domain_handoff``. "
            "Non-null means spent; a second exchange attempt is a replay."
        ),
    )

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"], name="domainhandoff_expires_idx"),
            models.Index(fields=["hostname", "consumed_at"], name="domainhandoff_host_idx"),
        ]

    @property
    def is_spent(self) -> bool:
        return self.consumed_at is not None

    def __str__(self) -> str:
        state = "spent" if self.is_spent else "live"
        return f"DomainSessionHandoff({self.hostname}, {state}, exp={self.expires_at})"
