r"""
DNS layout modes + post-install zone registration policy (#153, spec 13 §2.0.3-§2.0.4).

Pure-Python policy. Three concerns live here:

* **Layout modes** — flat (\`<app>.<zone>\`) vs org-prefixed
  (\`<app>.<org>.<zone>\`). Layout choice cascades into hostname
  generation, ingress shape, and cert SANs (see #70's
  \`ZoneLayout\`).
* **Hostname construction** — single-source-of-truth so the
  registry, ingress, and cert workflows agree.
* **Post-install zone registration** — validation steps a new
  \`ManagedDomain\` must pass before the platform serves it
  (NS delegation check, cert policy, end-to-end resolution).
* **Layout-migration dual-serve window** — when an existing
  platform changes layout, both old + new hostnames must
  resolve during a transition period.

Pairs with #70 (cert SANs per layout) and the platform's
DNS-driver layer.
"""

from __future__ import annotations

import dataclasses
import re
from enum import StrEnum


class DnsLayoutError(ValueError):
    pass


# ---- layout mode ---------------------------------------------------


class DnsLayoutMode(StrEnum):
    """Spec 13 §2.0.3."""

    FLAT = "flat"
    """Hostname pattern: ``<app>.<zone>``. One zone serves one
    org's apps directly. Used when each org has their own
    base zone (e.g. acme.platform.example, globex.platform.example)."""

    ORG_PREFIXED = "org_prefixed"
    """Hostname pattern: ``<app>.<org>.<zone>``. Multi-tenant
    zone shared across orgs. Used when one base zone serves
    many orgs (e.g. apps.platform.example serving acme + globex)."""


_DNS_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
"""RFC 1035 label rule. Used to validate slug components before
they're concatenated into a hostname."""


def hostname_for_app(
    *,
    app_slug: str,
    org_slug: str,
    base_zone: str,
    mode: DnsLayoutMode,
) -> str:
    """Spec 13 §2.0.3: render the canonical hostname for an app
    under one ManagedDomain in the given layout mode.

    Always lowercase output (RFC 1035) and trailing-dot stripped
    so cert SANs + DNS-driver writes match.
    """
    app_slug = app_slug.lower()
    org_slug = org_slug.lower()
    zone = base_zone.strip(".").lower()

    for component, name in (
        (app_slug, "app_slug"),
        (org_slug, "org_slug"),
    ):
        if not component:
            raise DnsLayoutError(f"{name} is required")
        if not _DNS_LABEL_RE.match(component):
            raise DnsLayoutError(f"{name} {component!r} is not RFC 1035 label valid")

    if not zone:
        raise DnsLayoutError("base_zone is required")

    if mode == DnsLayoutMode.FLAT:
        return f"{app_slug}.{zone}"
    if mode == DnsLayoutMode.ORG_PREFIXED:
        return f"{app_slug}.{org_slug}.{zone}"
    raise DnsLayoutError(f"unknown layout mode {mode!r}")


# ---- post-install zone registration --------------------------------


class ZoneRegistrationStep(StrEnum):
    """Spec 13 §2.0.4: ordered validation + registration flow.

    Each step is gated on the previous succeeding. Failures halt
    the workflow at the failing step and surface the reason for
    the operator (e.g. NS records don't point at our nameservers
    yet — apply at the registrar and re-run).
    """

    VALIDATE_NS_DELEGATION = "validate_ns_delegation"
    """Confirm NS records at the parent zone point at platform
    DNS. Without delegation, our DNS driver can't write records
    that resolvers will ever see."""

    CONFIGURE_CERT_POLICY = "configure_cert_policy"
    """Wildcard vs per-app cert policy registered (see #70)."""

    REGISTER_MANAGED_DOMAIN = "register_managed_domain"
    """Create the ManagedDomain row + bind the DNS driver."""

    UPDATE_INGRESS = "update_ingress"
    """Add the zone to the ingress controller's serving set."""

    HEALTH_CHECK = "health_check"
    """End-to-end: resolve a probe hostname and verify it
    reaches platform ingress. This is the gating check before
    the zone is marked active."""

    MARK_ACTIVE = "mark_active"
    """ManagedDomain.is_active=True. Workflows can now provision
    apps in the zone."""


ZONE_REGISTRATION_ORDER = (
    ZoneRegistrationStep.VALIDATE_NS_DELEGATION,
    ZoneRegistrationStep.CONFIGURE_CERT_POLICY,
    ZoneRegistrationStep.REGISTER_MANAGED_DOMAIN,
    ZoneRegistrationStep.UPDATE_INGRESS,
    ZoneRegistrationStep.HEALTH_CHECK,
    ZoneRegistrationStep.MARK_ACTIVE,
)


# ---- NS delegation validation --------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class NsDelegationCheck:
    """Inputs to the NS-delegation validator. Resolver call lives
    in the activity layer; this module just compares sets."""

    zone: str
    expected_nameservers: frozenset[str]
    """The platform's authoritative NS hostnames the operator
    is supposed to set at the registrar."""

    observed_nameservers: frozenset[str]
    """What a public resolver returns when queried for this zone's
    NS records. Empty set means delegation hasn't taken effect
    (or zone doesn't exist publicly yet)."""


def evaluate_ns_delegation(
    *,
    check: NsDelegationCheck,
) -> tuple[bool, str]:
    """Returns (passed, reason). Reason is human-readable for the
    operator UI / workflow log."""
    if not check.observed_nameservers:
        return False, (
            f"no NS records observed for zone {check.zone!r}; "
            "delegation may not have propagated yet (DNS TTL) or "
            "the parent-zone registrar still points elsewhere"
        )

    expected = {ns.lower().rstrip(".") for ns in check.expected_nameservers}
    observed = {ns.lower().rstrip(".") for ns in check.observed_nameservers}

    missing = expected - observed
    if missing:
        return False, (
            f"NS delegation incomplete: expected nameservers "
            f"{sorted(missing)} not present in observed set "
            f"{sorted(observed)}"
        )

    extra = observed - expected
    if extra:
        # Operator may have left their old DNS provider's
        # nameservers in place. Surface as a soft warning, not a
        # failure, so they can decide.
        return True, (
            f"NS delegation OK; note observed extra nameservers "
            f"{sorted(extra)} (previous provider not removed?)"
        )

    return True, "NS delegation OK"


# ---- layout migration (dual-serve window) --------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class LayoutMigration:
    """Spec 13 §2.0.3: layout change requires a dual-serve window
    where both old and new hostnames resolve, so apps don't break
    mid-migration."""

    from_mode: DnsLayoutMode
    to_mode: DnsLayoutMode
    started_at_unix: int
    dual_serve_days: int = 30
    """Spec recommends 30 days; operator can extend up to 90."""

    def __post_init__(self) -> None:
        if self.from_mode == self.to_mode:
            raise DnsLayoutError("layout migration source and target must differ")
        if self.dual_serve_days <= 0:
            raise DnsLayoutError(f"dual_serve_days {self.dual_serve_days} must be positive")
        if self.dual_serve_days > 90:
            raise DnsLayoutError(
                f"dual_serve_days {self.dual_serve_days} exceeds 90 "
                "(operator-extended ceiling); split into shorter windows"
            )


def hostnames_for_dual_serve(
    *,
    app_slug: str,
    org_slug: str,
    base_zone: str,
    migration: LayoutMigration,
) -> tuple[str, str]:
    """During a layout migration, the platform serves BOTH old
    and new hostnames. Returns (old, new) for the ingress
    controller to set up server blocks / VirtualService entries
    for both."""
    old = hostname_for_app(
        app_slug=app_slug,
        org_slug=org_slug,
        base_zone=base_zone,
        mode=migration.from_mode,
    )
    new = hostname_for_app(
        app_slug=app_slug,
        org_slug=org_slug,
        base_zone=base_zone,
        mode=migration.to_mode,
    )
    return old, new


def is_dual_serve_active(
    *,
    migration: LayoutMigration,
    now_unix: int,
) -> bool:
    """Dual-serve window is open when now is within
    [started_at, started_at + dual_serve_days). Outside the
    window, only the new hostname is served (old returns NXDOMAIN)."""
    end = migration.started_at_unix + migration.dual_serve_days * 86400
    return migration.started_at_unix <= now_unix < end


# ---- ingress route count budget ------------------------------------


def expected_route_count_for_dual_serve(
    *,
    app_count: int,
) -> int:
    """During dual-serve, every app gets two routes. Useful for
    capacity planning (some ingress controllers cap routes per
    cluster — operator should know before kicking off a layout
    migration that doubles their route count)."""
    if app_count < 0:
        raise DnsLayoutError("app_count must be non-negative")
    return app_count * 2
