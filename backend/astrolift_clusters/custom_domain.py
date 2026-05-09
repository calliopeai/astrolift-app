"""
Custom domain lifecycle policy (#57, spec 13 §2.2-2.3).

Pure-Python policy. The DNS validation workflow + cert
provisioning workflow consult this module for state transitions
and validation token generation.

Domain lifecycle states (spec 13 §2.2):

  pending_validation
    Tenant added the domain; we generated a TXT challenge token
    they need to add to their DNS. Workflow polls every N minutes.

  validating
    TXT record observed; ACME validation in flight (cert-manager
    HTTP-01 or DNS-01 challenge).

  active
    Cert issued, attached to ingress, host rule added.

  error
    Validation failed (TXT mismatch, ACME rate limit, DNS
    propagation timeout).

  expired
    Cert expiry past; renewal failed and retry exhausted.

Apex-domain (``acme.com``, no subdomain) handling: per spec 13
§2.3 tenants must use a flattened ALIAS/ANAME at their DNS
provider OR front via Cloudflare/equivalent. We can't support
plain CNAMEs at the zone apex (RFC 1034 forbids).
"""

from __future__ import annotations

import dataclasses
import re
import secrets
from enum import Enum


class DomainState(str, Enum):
    PENDING_VALIDATION = "pending_validation"
    VALIDATING = "validating"
    ACTIVE = "active"
    ERROR = "error"
    EXPIRED = "expired"


# Spec 13 §2.2: 10-minute SLA on validation completing from
# CNAME set up. Internal worker polls every minute; if 10
# minutes pass without TXT match we transition to ERROR with a
# 'check your DNS' message.
VALIDATION_TIMEOUT_SECONDS = 10 * 60


# RFC 1035 hostname / FQDN regex (relaxed): labels [a-z0-9-]
# (no leading/trailing hyphen), separated by dots, total <=253.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}$"
)


class DomainError(ValueError):
    pass


def is_valid_hostname(hostname: str) -> bool:
    """RFC 1035 hostname check. Caller hands user-typed input
    here before recording a CustomDomain row."""
    if not hostname or len(hostname) > 253:
        return False
    return bool(_HOSTNAME_RE.match(hostname.lower()))


# ---- apex detection ------------------------------------------------


def is_apex(hostname: str) -> bool:
    """A 'apex' domain is the zone root (``acme.com``); a
    'subdomain' is anything below it (``api.acme.com``,
    ``www.acme.com``).

    Heuristic: count labels. Two labels = apex (acme.com).
    Three or more labels = subdomain.

    This is a heuristic — strictly ``co.uk`` is two labels but
    you can't apex-bind there. Caller can override via the
    public-suffix list integration when warranted; the policy
    here is for the common case (~99% of customer domains).
    """
    if not is_valid_hostname(hostname):
        raise DomainError(f"hostname {hostname!r} is not valid")
    return hostname.lower().count(".") == 1


# ---- TXT challenge token --------------------------------------------


# Spec 13 §2.2 — TXT record name convention for ownership challenge.
TXT_CHALLENGE_NAME_PREFIX = "_astrolift-validate"


@dataclasses.dataclass(frozen=True, slots=True)
class TxtChallenge:
    """The TXT record the tenant must add to their DNS."""

    record_name: str
    """e.g. ``_astrolift-validate.acme.com`` (apex) or
    ``_astrolift-validate.api.acme.com`` (subdomain)."""

    record_value: str
    """The cryptographically random token the platform generates
    and the tenant copies into their TXT record. 32 bytes
    base64url so it's safe in any DNS provider's UI."""


def issue_txt_challenge(*, hostname: str) -> TxtChallenge:
    """Generate a fresh challenge for a hostname. Caller
    persists ``record_value`` on the CustomDomain row for the
    polling workflow to compare against."""
    if not is_valid_hostname(hostname):
        raise DomainError(f"hostname {hostname!r} is not valid")
    record_name = f"{TXT_CHALLENGE_NAME_PREFIX}.{hostname.lower()}"
    record_value = secrets.token_urlsafe(32)
    return TxtChallenge(
        record_name=record_name, record_value=record_value,
    )


def verify_txt_challenge(
    *,
    expected: TxtChallenge,
    observed_records: list[str],
) -> bool:
    """Compare the TXT record values the resolver returned
    against our expected token. Returns True if the expected
    token is present.

    Multiple TXT values at the same name is normal (Google site
    verification, SPF, etc.). Match is True when our value is
    *anywhere* in the set."""
    return expected.record_value in observed_records


# ---- state transition guard ---------------------------------------


# Allowed transitions per spec 13 §2.2. Anything else is a bug
# in the workflow.
_ALLOWED_TRANSITIONS: dict[DomainState, frozenset[DomainState]] = {
    DomainState.PENDING_VALIDATION: frozenset({
        DomainState.VALIDATING, DomainState.ERROR,
    }),
    DomainState.VALIDATING: frozenset({
        DomainState.ACTIVE, DomainState.ERROR, DomainState.PENDING_VALIDATION,
    }),
    DomainState.ACTIVE: frozenset({
        DomainState.EXPIRED, DomainState.ERROR,
    }),
    DomainState.ERROR: frozenset({
        DomainState.PENDING_VALIDATION,  # tenant can retry
    }),
    DomainState.EXPIRED: frozenset({
        DomainState.PENDING_VALIDATION, DomainState.ACTIVE,
    }),
}


def can_transition(*, from_state: DomainState, to_state: DomainState) -> bool:
    """Check transition validity. The workflow calls this before
    persisting a state change so a bug can't drive a domain
    through impossible transitions."""
    if from_state == to_state:
        return True  # idempotent re-fire
    return to_state in _ALLOWED_TRANSITIONS.get(from_state, frozenset())


def assert_transition(*, from_state: DomainState, to_state: DomainState) -> None:
    if not can_transition(from_state=from_state, to_state=to_state):
        raise DomainError(
            f"invalid domain state transition: "
            f"{from_state.value} -> {to_state.value}"
        )


# ---- DNS instruction copy ------------------------------------------


def dns_instruction(*, hostname: str, ingress_target: str) -> str:
    """Generate the human-readable DNS-config instruction for the
    tenant's UI panel. Apex domains get ALIAS/ANAME guidance;
    subdomains get plain CNAME.

    The ``ingress_target`` is the platform-side target the
    tenant points their record at (e.g.
    ``ingress.platform.acme.com`` or an A-record like
    ``203.0.113.10``)."""
    if not is_valid_hostname(hostname):
        raise DomainError(f"hostname {hostname!r} is not valid")
    if is_apex(hostname):
        return (
            f"At your DNS provider, add an ALIAS (or ANAME) record "
            f"at the apex of {hostname} pointing to {ingress_target}. "
            "If your provider doesn't support ALIAS/ANAME (e.g. "
            "raw bind), use a CDN like Cloudflare in front."
        )
    return (
        f"At your DNS provider, add a CNAME record for {hostname} "
        f"pointing to {ingress_target}."
    )
