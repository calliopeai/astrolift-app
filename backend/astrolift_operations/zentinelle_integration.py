"""
Zentinelle GRC integration policy (#228, spec 17 §6 + §11).

Pure-Python policy. Zentinelle is the Calliope Agent GRC product;
Astrolift exposes its audit-event stream as evidence input via
the existing WebhookSubscription pipeline. This module owns:

* **Event-type catalog** — locked vocabulary of audit events
  Zentinelle ingests as compliance evidence.
* **Per-event payload schema** — versioned envelope so payload
  shape evolution doesn't break Zentinelle's parser mid-flight.
* **Subscription template** — pre-canned WebhookSubscription
  values an operator applies once to enable the integration.
* **Anti-replay nonce format** — idempotency_key shape so
  re-deliveries don't double-count evidence on Zentinelle's
  side.
* **Bidirectional callback contract** — Zentinelle marks
  evidence \"validated\" and writes back to Astrolift's
  AuditEvent (deferred per spec; this module captures the
  shape for when it lands).

This module is shared — Astrolift uses it to format outgoing
events; Zentinelle's collector code reads the SAME constants
when parsing inbound. Single source of truth for the wire format.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import StrEnum


class ZentinelleIntegrationError(ValueError):
    pass


# ---- payload version ----------------------------------------------


PAYLOAD_VERSION = 1
"""Bump when the envelope shape changes incompatibly. Zentinelle's
collector switches on this; old payloads remain parseable until
its retention window expires."""


# ---- event-type catalog -------------------------------------------


class ZentinelleEventType(StrEnum):
    """Locked vocabulary of audit events Zentinelle ingests as
    evidence. SOC 2 / HIPAA / ISO 27001 (per #271) collectors
    cross-reference these types when generating control-test
    evidence."""

    # Identity / access controls
    ROLE_BINDING_GRANT = "AUDIT.role_binding.grant"
    """A user gained a role binding. Maps to access-control
    evidence in SOC 2 CC6.1, ISO 27001 A.9.2.2."""

    ROLE_BINDING_REVOKE = "AUDIT.role_binding.revoke"
    """Off-boarding evidence. CC6.3."""

    USER_INVITED = "AUDIT.user.invited"
    USER_ACCEPTED = "AUDIT.user.accepted"
    USER_DEACTIVATED = "AUDIT.user.deactivated"

    # App lifecycle (deploy + change management)
    APP_DEPLOY = "AUDIT.app.deploy"
    """Deployment evidence — change management, CC8.1."""

    APP_REGISTERED = "AUDIT.app.registered"
    APP_DEREGISTERED = "AUDIT.app.deregistered"

    # Secret rotation evidence (#150 cross-ref)
    SECRET_ROTATED = "AUDIT.secret.rotated"
    """Per-secret rotation event. Counts toward 'rotation %%
    within window' compliance metric."""

    SECRET_VIEWED = "AUDIT.secret.viewed"
    """Operator opened a secret in the UI. SOC 2 audit trail
    for sensitive-data access."""

    # Configuration / governance
    ORG_SETTINGS_UPDATED = "AUDIT.org.update"
    OBSERVABILITY_PROFILE_UPDATED = "AUDIT.observability.update"
    RESIDENCY_POLICY_UPDATED = "AUDIT.residency.update"
    """Cross-references #271's region constraints requirement."""

    # Compliance-specific
    COMPLIANCE_REPORT_GENERATED = "AUDIT.compliance.report_generated"
    """Bookend event — Zentinelle's evidence chain shows the
    report generation itself."""


# Event types that Zentinelle's default subscription template
# subscribes to. Operator can override but starting set is
# 'everything we currently consider compliance evidence.'
DEFAULT_SUBSCRIBED_EVENTS = tuple(t.value for t in ZentinelleEventType)


# ---- per-event payload schema -------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ZentinelleEnvelope:
    r"""The wire envelope every event payload sits inside.

    Design: outer envelope is stable across event types; the
    \`payload\` dict is type-specific. Zentinelle's collector
    can validate envelope first, then dispatch to per-type
    parsers."""

    payload_version: int
    """Always PAYLOAD_VERSION at emit time."""

    event_type: str
    """One of ZentinelleEventType.value."""

    event_id: str
    """ULID — sortable + globally unique. Zentinelle uses this
    as the dedup key."""

    org_id: int
    """Tenant scope. Zentinelle's evidence chain is org-keyed."""

    actor_user_id: int | None
    """Who took the action. None for system-initiated events
    (e.g. scheduled secret rotation)."""

    occurred_at_unix: int
    """Server-side timestamp. Zentinelle's evidence chain uses
    this as the canonical time, not the delivery time."""

    payload: Mapping
    """Per-event-type body. Schema lives in the per-type
    constants below."""

    idempotency_key: str
    """Anti-replay nonce. Zentinelle dedupes on this; resending
    the same payload (e.g. retry) is a no-op."""

    def __post_init__(self) -> None:
        if self.payload_version != PAYLOAD_VERSION:
            raise ZentinelleIntegrationError(
                f"payload_version {self.payload_version} != current {PAYLOAD_VERSION}"
            )
        if not self.event_id:
            raise ZentinelleIntegrationError("event_id required")
        if not self.idempotency_key:
            raise ZentinelleIntegrationError("idempotency_key required (anti-replay)")
        if self.org_id <= 0:
            raise ZentinelleIntegrationError("org_id must be positive")
        # Validate event_type is in vocabulary
        try:
            ZentinelleEventType(self.event_type)
        except ValueError as exc:
            raise ZentinelleIntegrationError(
                f"unknown event_type {self.event_type!r}; vocabulary: "
                f"{[e.value for e in ZentinelleEventType]}"
            ) from exc


# Per-type required payload keys. Wire-format contract.
REQUIRED_PAYLOAD_KEYS: dict[ZentinelleEventType, frozenset[str]] = {
    ZentinelleEventType.ROLE_BINDING_GRANT: frozenset(
        {
            "role_id",
            "scope",
            "subject_user_id",
        }
    ),
    ZentinelleEventType.ROLE_BINDING_REVOKE: frozenset(
        {
            "role_id",
            "scope",
            "subject_user_id",
        }
    ),
    ZentinelleEventType.USER_INVITED: frozenset(
        {
            "subject_email",
            "invited_by_user_id",
        }
    ),
    ZentinelleEventType.USER_ACCEPTED: frozenset(
        {
            "subject_user_id",
        }
    ),
    ZentinelleEventType.USER_DEACTIVATED: frozenset(
        {
            "subject_user_id",
            "reason",
        }
    ),
    ZentinelleEventType.APP_DEPLOY: frozenset(
        {
            "app_slug",
            "deployment_id",
            "image_digest",
            "environment",
            "trigger_kind",
        }
    ),
    ZentinelleEventType.APP_REGISTERED: frozenset(
        {
            "app_slug",
            "repo_url",
        }
    ),
    ZentinelleEventType.APP_DEREGISTERED: frozenset(
        {
            "app_slug",
        }
    ),
    ZentinelleEventType.SECRET_ROTATED: frozenset(
        {
            "secret_path",
            "rotation_kind",
        }
    ),
    ZentinelleEventType.SECRET_VIEWED: frozenset(
        {
            "secret_path",
        }
    ),
    ZentinelleEventType.ORG_SETTINGS_UPDATED: frozenset(
        {
            "fields_changed",
        }
    ),
    ZentinelleEventType.OBSERVABILITY_PROFILE_UPDATED: frozenset(
        {
            "fields_changed",
        }
    ),
    ZentinelleEventType.RESIDENCY_POLICY_UPDATED: frozenset(
        {
            "fields_changed",
        }
    ),
    ZentinelleEventType.COMPLIANCE_REPORT_GENERATED: frozenset(
        {
            "template_slug",
            "overall_verdict",
        }
    ),
}


def validate_payload(
    *,
    event_type: ZentinelleEventType,
    payload: Mapping,
) -> None:
    """Refuse missing required keys at emit time. Zentinelle's
    collector ALSO validates; this is the platform-side guard
    so a missing field fails the audit-emit, not the audit
    delivery."""
    required = REQUIRED_PAYLOAD_KEYS.get(event_type)
    if required is None:
        raise ZentinelleIntegrationError(f"no schema for event_type {event_type!r}")
    missing = required - set(payload.keys())
    if missing:
        raise ZentinelleIntegrationError(
            f"{event_type.value} payload missing required keys: {sorted(missing)}"
        )


# ---- idempotency key format ---------------------------------------


def idempotency_key_for(
    *,
    org_id: int,
    event_id: str,
) -> str:
    r"""Locked format: \`zentinelle-<org>-<event_id>\`. Zentinelle
    dedupes on exact match. Including org_id prevents cross-org
    collisions even in pathological event_id reuse."""
    if org_id <= 0:
        raise ZentinelleIntegrationError("org_id must be positive")
    if not event_id:
        raise ZentinelleIntegrationError("event_id required")
    return f"zentinelle-{org_id}-{event_id}"


# ---- subscription template ----------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ZentinelleSubscriptionTemplate:
    """Pre-canned WebhookSubscription values. The install
    playbook applies this to enable the integration in one
    operator action."""

    name: str
    """Display name in the operator's webhook list."""

    target_url_template: str
    r"""e.g. \`https://{zentinelle_url}/integrations/astrolift/v1/audit\`.
    Operator substitutes their Zentinelle URL at apply time."""

    event_types: tuple[str, ...]
    """Subscribed events. Default is DEFAULT_SUBSCRIBED_EVENTS;
    operator can narrow."""

    signing_secret_required: bool = True
    """HMAC signing always required for compliance evidence —
    receiver must verify or the audit chain is meaningless."""


DEFAULT_TEMPLATE = ZentinelleSubscriptionTemplate(
    name="Zentinelle GRC integration",
    target_url_template=("https://{zentinelle_url}/integrations/astrolift/v1/audit"),
    event_types=DEFAULT_SUBSCRIBED_EVENTS,
)


def render_subscription_target(
    *,
    template: ZentinelleSubscriptionTemplate,
    zentinelle_url: str,
) -> str:
    """Substitute the operator's Zentinelle URL into the
    template. Validates the URL is HTTPS (compliance evidence
    in cleartext is unacceptable)."""
    url = zentinelle_url.strip()
    if not url:
        raise ZentinelleIntegrationError("zentinelle_url is required")
    if not url.startswith("https://"):
        raise ZentinelleIntegrationError(f"zentinelle_url must be HTTPS for compliance evidence; got {url!r}")
    # Strip trailing slash so the format substitution doesn't
    # produce double-slash paths
    url = url.rstrip("/")
    # Strip the protocol prefix for the template substitution
    # since template includes 'https://' itself
    bare = url[len("https://") :]
    return template.target_url_template.format(
        zentinelle_url=bare,
    )


# ---- compliance-framework cross-reference -------------------------


# Map event types to compliance frameworks they generate evidence
# for. Used by the operator-facing UI 'this event feeds these
# frameworks' badge so they understand impact of muting an event.
_FRAMEWORK_RELEVANCE: dict[ZentinelleEventType, frozenset[str]] = {
    ZentinelleEventType.ROLE_BINDING_GRANT: frozenset({"soc2", "hipaa", "iso27001"}),
    ZentinelleEventType.ROLE_BINDING_REVOKE: frozenset({"soc2", "hipaa", "iso27001"}),
    ZentinelleEventType.USER_INVITED: frozenset({"soc2", "iso27001"}),
    ZentinelleEventType.USER_ACCEPTED: frozenset({"soc2", "iso27001"}),
    ZentinelleEventType.USER_DEACTIVATED: frozenset({"soc2", "hipaa", "iso27001"}),
    ZentinelleEventType.APP_DEPLOY: frozenset({"soc2", "iso27001"}),
    ZentinelleEventType.APP_REGISTERED: frozenset({"soc2"}),
    ZentinelleEventType.APP_DEREGISTERED: frozenset({"soc2", "hipaa"}),
    ZentinelleEventType.SECRET_ROTATED: frozenset({"soc2", "hipaa", "iso27001"}),
    ZentinelleEventType.SECRET_VIEWED: frozenset({"hipaa"}),
    ZentinelleEventType.ORG_SETTINGS_UPDATED: frozenset({"soc2"}),
    ZentinelleEventType.OBSERVABILITY_PROFILE_UPDATED: frozenset({"soc2"}),
    ZentinelleEventType.RESIDENCY_POLICY_UPDATED: frozenset({"hipaa"}),
    ZentinelleEventType.COMPLIANCE_REPORT_GENERATED: frozenset({"soc2", "hipaa", "iso27001"}),
}


def frameworks_for_event(
    *,
    event_type: ZentinelleEventType,
) -> tuple[str, ...]:
    """Returns the framework slugs (from #271's TEMPLATE_REGISTRY)
    this event feeds evidence for. UI surfaces as a badge."""
    return tuple(sorted(_FRAMEWORK_RELEVANCE.get(event_type, frozenset())))


# ---- bidirectional callback (deferred per spec) -------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ZentinelleValidationCallback:
    """Zentinelle marks evidence 'validated' and calls back into
    Astrolift's AuditEvent. Captured here so when the deferred
    follow-up lands, the contract is already defined.

    Astrolift authorizes the callback via a privileged API token
    bound to the integration; the token has narrow scope —
    write-only access to AuditEvent.zentinelle_validated_at +
    AuditEvent.zentinelle_evidence_id, nothing else.
    """

    event_id: str
    """References the original AuditEvent.guid."""

    zentinelle_evidence_id: str
    """Zentinelle's stable reference. Astrolift stores for the
    operator UI's 'view in Zentinelle' link."""

    validated_at_unix: int

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ZentinelleIntegrationError("event_id required")
        if not self.zentinelle_evidence_id:
            raise ZentinelleIntegrationError("zentinelle_evidence_id required")
