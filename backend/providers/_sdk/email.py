"""Email-driver read-only operations (#629, #631, #632, #633, #634).

These methods extend the surface plugin authors expose for transactional-
email backends beyond the lifecycle primitives that live in
``managed_service.py``. They power the email-detail surface in the
control plane: quota tile, sender reputation / sandbox status, suppression
list view + manage, DKIM/SPF/DMARC verification panel, and identity-
verification badge.

Why a separate module:

* ``ManagedServiceDriver`` is the **lifecycle** contract (provision /
  update / deprovision / status / binding) — it is the same shape across
  every kind. The email-detail operations are kind-specific and read-
  heavy, so they live on a sibling protocol that the email driver
  optionally implements. The resolver layer catches
  :class:`UnsupportedOperationError` to render "not supported on this
  cloud" instead of bubbling a 500.

* Suppression-list mutations are operator-sensitive (a manual remove of
  a hard-bounced address risks the sender's reputation if the original
  bounce was legitimate). The resolver wires
  ``@driver_op(audit=True, sensitive_kind=...)`` on the mutation entry
  so every add / remove is captured in ``AuditEvent`` alongside the
  triggering operator + IP.

Non-AWS backends today raise :class:`UnsupportedOperationError` from
every method (Azure Communication Services + the GCP third-party stub
don't expose comparable primitives). When a backend learns one of
these operations it implements the protocol method and removes the
exception; the resolver layer treats both cases uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 -- runtime annotation on @dataclass field
from enum import StrEnum
from typing import Protocol


class SuppressionReason(StrEnum):
    """Why an address is on the account suppression list.

    SES models three populated-by reasons: hard-bounce, complaint, and
    operator-added (manual). The platform adds ``MANUAL`` as the
    operator-induced flag so the suppression-list table can render
    provenance.
    """

    BOUNCE = "BOUNCE"
    COMPLAINT = "COMPLAINT"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class SuppressionEntry:
    """One row in the account suppression list."""

    address: str
    reason: SuppressionReason
    suppressed_at: datetime
    detail: str = ""
    """Free-form provider message — bounce sub-type, complaint feedback-
    loop class, or operator-supplied note for manual entries. Surface as
    a secondary line in the suppression table."""


@dataclass(frozen=True)
class SendQuota:
    """Account-level send-rate + 24h volume quota (#629).

    SES caps at ``max_send_rate`` messages per second and
    ``max_24_hour_send`` total messages per rolling 24h. ``sent_last_24h``
    is the in-flight consumption counter; the UI renders headroom as a
    progress bar and warns at 80%.
    """

    max_send_rate: float
    max_24_hour_send: float
    sent_last_24h: float


@dataclass(frozen=True)
class SendStatPoint:
    """One 15-minute interval of send counters (#629, #633).

    SES ``get_send_statistics`` returns 15-minute buckets for the last
    two weeks. The UI tiles aggregate the latest ~7 days for the
    bounce-rate / complaint-rate trend.
    """

    timestamp: datetime
    delivery_attempts: int
    bounces: int
    complaints: int
    rejects: int


@dataclass(frozen=True)
class AccountSendStatus:
    """Sandbox / production status + reputation snapshot (#633).

    SES accounts start in **sandbox** mode where they can only send to
    verified recipients + verified domains. Graduation to production
    requires an AWS support request. The platform surfaces the mode and
    a reputation score so operators can spot a sender reputation drift
    before SES throttles the account.
    """

    sending_enabled: bool
    production_access: bool
    """False = sandbox; True = production-access granted by AWS support."""

    reputation_score: float | None = None
    """0.0-1.0 approximation derived from bounce + complaint rates over
    the last 7 days. ``None`` when the account hasn't sent enough mail
    yet for a meaningful score (SES + the platform agree on ~1000 sends
    as the floor). Below 0.7 is yellow; below 0.5 is red."""

    bounce_rate_pct: float | None = None
    complaint_rate_pct: float | None = None


@dataclass(frozen=True)
class DkimToken:
    """One DKIM CNAME selector + the expected target.

    SES domain identities require three CNAME records of the form
    ``<token>._domainkey.<domain>`` pointing at
    ``<token>.dkim.amazonses.com``. The UI renders each as a copyable
    row so the operator can paste them straight into their DNS console.
    """

    token: str
    cname_host: str
    cname_target: str


@dataclass(frozen=True)
class IdentityVerification:
    """Identity-verification details + DKIM tokens (#634).

    ``status`` is one of ``Pending`` / ``Success`` / ``Failed`` (mirrors
    SES). ``verification_token`` is the TXT challenge for the operator
    to add when verifying a domain via TXT; ``dkim_tokens`` are the
    three CNAMEs that authenticate signed mail.
    """

    identity: str
    is_domain: bool
    status: str
    verification_token: str = ""
    """Empty when the identity is an email address (SES verifies via
    confirmation link, not a DNS challenge)."""

    dkim_tokens: list[DkimToken] = field(default_factory=list)
    """Empty for email-address identities (DKIM signing requires a
    domain identity)."""


class DnsCheckOutcome(StrEnum):
    """Color-coded outcome of a single DNS-side authentication check.

    Maps to the UI palette: ``GREEN`` = passing, ``YELLOW`` = present
    but suboptimal (relaxed policy, partial alignment), ``RED`` = absent
    or actively wrong, ``UNKNOWN`` = the probe failed to reach DNS so
    the operator can retry without us blaming the auth setup.
    """

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DnsAuthCheck:
    """One row of the DKIM / SPF / DMARC panel (#632).

    ``protocol`` is one of ``DKIM`` / ``SPF`` / ``DMARC``. ``records``
    is the parsed TXT/CNAME content the probe found; empty when the
    record doesn't exist. ``message`` carries a human-friendly hint
    when the outcome is ``YELLOW`` or ``RED`` (e.g. "SPF record present
    but doesn't include amazonses.com").
    """

    protocol: str
    outcome: DnsCheckOutcome
    records: list[str] = field(default_factory=list)
    message: str = ""


@dataclass(frozen=True)
class DnsAuthStatus:
    """Composite DKIM/SPF/DMARC status for one sending domain (#632)."""

    identity: str
    checked_at: datetime
    dkim: DnsAuthCheck
    spf: DnsAuthCheck
    dmarc: DnsAuthCheck

    @property
    def overall(self) -> DnsCheckOutcome:
        """Worst-of for the badge color the row renders at the top of
        the panel. ``RED`` if any check is red; otherwise ``YELLOW`` if
        any is yellow; otherwise ``GREEN`` if all are green; otherwise
        ``UNKNOWN``."""
        outcomes = (self.dkim.outcome, self.spf.outcome, self.dmarc.outcome)
        if DnsCheckOutcome.RED in outcomes:
            return DnsCheckOutcome.RED
        if DnsCheckOutcome.YELLOW in outcomes:
            return DnsCheckOutcome.YELLOW
        if all(o == DnsCheckOutcome.GREEN for o in outcomes):
            return DnsCheckOutcome.GREEN
        return DnsCheckOutcome.UNKNOWN


@dataclass(frozen=True)
class EmailTemplate:
    """One transactional-email template (#635).

    Mirrors the SES ``Template`` shape but stays vendor-neutral so a
    future SendGrid / Mailgun driver implementation can populate the
    same dataclass. ``created_at`` is ``None`` when the backend doesn't
    expose a creation timestamp (SES surfaces ``CreatedTimestamp`` only
    on the list-call metadata, not on the per-template fetch)."""

    name: str
    subject: str
    html_body: str
    text_body: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class TemplateSendStatPoint:
    """One 15-minute interval of send counters for a specific template
    (#628).

    Sourced from the backend's per-template metrics surface (SES uses
    the ``ses2:TemplateName`` CloudWatch dimension on ``AWS/SES``); the
    UI renders these as a sparkline + summary tile on the template-
    detail page. Drivers MUST return points oldest-first so the line
    chart renders without re-sorting."""

    timestamp: datetime
    sends: int
    deliveries: int
    bounces: int
    complaints: int


class EmailObservabilityDriver(Protocol):
    """Read-only email-detail operations + suppression-list mutations.

    Plugin authors implement this alongside :class:`ManagedServiceDriver`
    when the backend exposes the corresponding primitives. Methods that
    aren't supported on a given backend raise
    :class:`_sdk.UnsupportedOperationError` so the resolver layer can
    surface "not supported on this cloud" cleanly.

    All methods are thread-safe (drivers must not hold conversational
    state between calls). The resolver layer caches the SDK clients per
    process; instance reuse is expected.
    """

    # ---- send health ------------------------------------------------

    def get_send_quota(self) -> SendQuota:
        """Current send rate + 24h volume + headroom."""
        ...

    def get_send_statistics(self) -> list[SendStatPoint]:
        """15-minute send/bounce/complaint points for the last 14 days.

        Drivers may down-sample to whatever granularity the backend
        exposes; ordering is oldest-first."""
        ...

    def get_account_send_status(self) -> AccountSendStatus:
        """Sandbox-vs-production + reputation snapshot."""
        ...

    # ---- identity / dns auth ---------------------------------------

    def get_identity_verification_details(
        self,
        identity: str,
    ) -> IdentityVerification:
        """Verification status + DKIM tokens for one identity."""
        ...

    def verify_dns_authentication(self, identity: str) -> DnsAuthStatus:
        """Probe the public DNS resolver for DKIM / SPF / DMARC records
        on ``identity`` and grade them.

        The probe is best-effort: ``UNKNOWN`` when the resolver itself
        fails (network partition, NXDOMAIN-on-zone, …). Drivers SHOULD
        return a populated ``DnsAuthStatus`` even on probe failure so
        the UI renders the panel with an actionable retry hint.
        """
        ...

    # ---- suppression list ------------------------------------------

    def list_suppression_entries(
        self,
        *,
        page_size: int = 100,
    ) -> list[SuppressionEntry]:
        """Top ``page_size`` entries from the account suppression list.

        Drivers MUST return at most ``page_size`` rows; deep pagination
        isn't required for the MVP UI surface. SES caps at 200 per call
        and supports a continuation token; the platform follows up with
        cursor-paginated variants once an operator hits the ceiling."""
        ...

    def add_suppression_entry(
        self,
        *,
        address: str,
        reason: SuppressionReason,
        note: str = "",
    ) -> SuppressionEntry:
        """Add an address to the account suppression list.

        Used by the operator to suppress a known-bad recipient before
        SES auto-suppresses (e.g. a hostile recipient generating fake
        complaints). The audit row carries the operator + reason +
        note. ``reason`` is typically ``MANUAL`` from the UI path.
        """
        ...

    def remove_suppression_entry(self, *, address: str) -> bool:
        """Remove an address from the suppression list (un-suppress).

        Returns True when the address was on the list and is now off;
        False when the address wasn't on the list. Idempotent: a
        repeated call returns False without raising. The audit row
        carries the operator + the original suppression reason for
        provenance — operators routinely un-suppress hard-bounced
        addresses that have been remediated.
        """
        ...

    # ---- template management (#635, #628) --------------------------

    def list_templates(self) -> list[EmailTemplate]:
        """List transactional-email templates for this identity's
        account (#635).

        Returns at most 100 entries on a single call — SES caps the
        ``list_templates`` page at 100 rows + a continuation token; the
        platform's MVP UI doesn't paginate (the operator's template
        count is typically O(10)). Drivers without a templates surface
        raise :class:`UnsupportedOperationError`."""
        ...

    def get_template(self, *, name: str) -> EmailTemplate:
        """Fetch one template by name (#635).

        Raises :class:`ManagedServiceError` (or the driver's equivalent)
        when the template doesn't exist. The detail page resolver
        catches that and renders a 404 envelope."""
        ...

    def create_template(
        self,
        *,
        name: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> EmailTemplate:
        """Create a new transactional-email template (#635).

        ``name`` is the unique identifier the workload references on
        ``SendTemplatedEmail``. ``subject`` is an SES-style template
        string that can carry ``{{handlebar}}`` substitutions; the
        driver passes it through unchanged. Raises on duplicate name
        so the mutation envelope can surface a clean error."""
        ...

    def update_template(
        self,
        *,
        name: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> EmailTemplate:
        """Update an existing template in-place (#635).

        Replaces every field on the template; partial-update isn't
        portable across backends (SES, SendGrid, Postmark all expect
        the full body on update). Raises when the template doesn't
        exist; callers should ``create_template`` instead in that case."""
        ...

    def delete_template(self, *, name: str) -> bool:
        """Delete a template by name (#635).

        Returns ``True`` when the template existed and was deleted;
        ``False`` when the template wasn't found (idempotent). The
        backend MAY still hold references in send history; deletion
        only removes the future-send path."""
        ...

    def get_template_send_statistics(
        self,
        *,
        name: str,
        days: int = 14,
    ) -> list[TemplateSendStatPoint]:
        """Per-template send/delivery/bounce/complaint counters (#628).

        Sourced from the backend's metrics pipeline (SES → CloudWatch
        on the ``ses2:TemplateName`` dimension; future SendGrid driver
        → the ``/stats/templates`` endpoint). Points are 15-minute
        granularity, oldest-first; ``days`` caps the lookback window.
        Returns an empty list when the metrics pipeline has no data
        (zero sends in the window, or CloudWatch event publishing
        disabled on the configuration set)."""
        ...
