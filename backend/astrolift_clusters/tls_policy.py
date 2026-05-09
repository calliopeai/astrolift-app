"""
TLS issuance + renewal policy (#70, spec 13 §5).

Pure-Python policy. The ``IssueWildcardCertWorkflow``,
``IssueCustomDomainCertWorkflow``, and the daily renewal scan
job all consult this module for:

* **SAN-list construction** for org-wildcard certs (spec §5.1):
  base zone, org-prefixed apex, wildcard for tenant apps,
  wildcard for preview envs.
* **Per-base-zone wildcard strategy** (§2.0.2): one cert per
  zone in flat layout; one cert per org-prefix in org-prefixed
  layout.
* **Validation challenge selection** for custom domains (§5.2):
  HTTP-01 when the domain resolves to ingress; DNS-01 when not
  (or when apex/CNAME-flattened, which can't take the
  HTTP-01 path predictably).
* **Renewal severity** (§5.3): notification tier from days
  remaining until expiry (info/warn/critical).
* **mTLS mode validation** (§5.4): edge vs mesh, refusing
  contradictory configurations.

Pairs with #57 (custom domain DNS state machine) — the cert
challenge can't be issued until the DNS challenge is resolved
or the A record is pointing at ingress.
"""

from __future__ import annotations

import dataclasses
from enum import Enum


class TlsError(ValueError):
    pass


# ---- wildcard SAN construction -------------------------------------


class ZoneLayout(str, Enum):
    """Spec 13 §2.0.2."""

    FLAT = "flat"
    """One cert at ``*.<zone>``. Used when each org gets its own
    base zone (e.g. ``acme.platform.example``)."""

    ORG_PREFIXED = "org_prefixed"
    """One cert per org-prefix at ``<org>.<zone>`` +
    ``*.<org>.<zone>`` + ``*.pr.<org>.<zone>``. Used when the
    platform shares a base zone across orgs."""


@dataclasses.dataclass(frozen=True, slots=True)
class WildcardCertScope:
    """The SANs to request for one wildcard cert."""

    sans: tuple[str, ...]
    """RFC 5280 SubjectAltName values, in deterministic order so
    cert digests stable across renewals when the scope hasn't
    changed (renewal otherwise looks like a new cert to consumers)."""

    challenge_zone: str
    """The DNS zone the validation TXT record will live in.
    The DNS driver writes ``_acme-challenge.<challenge_zone>``."""


def wildcard_sans_for_org(
    *,
    base_zone: str,
    org_slug: str,
    layout: ZoneLayout,
) -> WildcardCertScope:
    """Construct the SAN list for one org's wildcard cert."""
    if not base_zone or not org_slug:
        raise TlsError("base_zone and org_slug both required")

    base_zone = base_zone.strip(".").lower()
    org_slug = org_slug.lower()

    if layout == ZoneLayout.FLAT:
        # The org owns the entire base zone (e.g. acme.platform.example).
        # SANs cover the apex + tenant apps + preview envs.
        sans = (
            base_zone,
            f"*.{base_zone}",
            f"*.pr.{base_zone}",
        )
        challenge_zone = base_zone
    elif layout == ZoneLayout.ORG_PREFIXED:
        # Multi-tenant base zone (e.g. apps.platform.example).
        # SANs are scoped under the org prefix.
        prefix = f"{org_slug}.{base_zone}"
        sans = (
            prefix,
            f"*.{prefix}",
            f"*.pr.{prefix}",
        )
        challenge_zone = base_zone
        # ^ challenge stays at the base zone — DNS-01 wildcard
        # validation needs the TXT record at the apex of the
        # DNS scope, not the org prefix.
    else:
        raise TlsError(f"unknown zone layout {layout!r}")

    return WildcardCertScope(sans=sans, challenge_zone=challenge_zone)


# ---- custom-domain challenge selection -----------------------------


class ChallengeKind(str, Enum):
    HTTP_01 = "http-01"
    """Server presents a token at
    ``/.well-known/acme-challenge/<token>``. Requires the domain
    to resolve to platform ingress."""

    DNS_01 = "dns-01"
    """ACME server queries ``_acme-challenge.<domain>`` TXT.
    Required for wildcard certs and for apex domains where
    ingress resolution is via ALIAS/ANAME (which some DNS
    providers don't support)."""


@dataclasses.dataclass(frozen=True, slots=True)
class CustomDomainCertContext:
    """The minimum facts needed to pick the right challenge."""

    domain: str

    is_apex: bool
    """True if the domain is the apex (zone root). Apex CNAME
    isn't legal per RFC 1912; resolution is via ALIAS/ANAME or
    A-record. HTTP-01 reliability depends on the provider."""

    is_wildcard: bool
    """True for ``*.example.com`` style. Wildcard MUST use
    DNS-01 — there's no HTTP server to answer for it."""

    dns_driver_supports_txt: bool
    """The platform-managed DNS driver can write TXT records
    for this zone. False when the customer manages DNS
    externally (subdomain pointing model)."""

    resolves_to_ingress: bool
    """A-record or AAAA-record at the domain points to platform
    ingress. False before propagation, or for apex with no
    flattening support."""


def challenge_for_custom_domain(
    *,
    ctx: CustomDomainCertContext,
) -> ChallengeKind:
    """Pick HTTP-01 vs DNS-01 for one custom domain."""
    if ctx.is_wildcard:
        # Wildcards have no server to receive the HTTP challenge.
        if not ctx.dns_driver_supports_txt:
            raise TlsError(
                f"wildcard cert for {ctx.domain!r} requires DNS-01 "
                "challenge but DNS driver cannot write TXT records "
                "(customer-managed DNS pointing model)"
            )
        return ChallengeKind.DNS_01

    if ctx.is_apex:
        # Apex: HTTP-01 works only if the domain resolves to ingress.
        # DNS-01 is more reliable when DNS-driver-managed.
        if ctx.dns_driver_supports_txt:
            return ChallengeKind.DNS_01
        if ctx.resolves_to_ingress:
            return ChallengeKind.HTTP_01
        raise TlsError(
            f"apex {ctx.domain!r} doesn't resolve to ingress and "
            "DNS driver can't write TXT records — issue a CNAME-able "
            "subdomain or hand DNS over to the platform"
        )

    # Standard subdomain: HTTP-01 if it resolves; DNS-01 if we
    # can write TXT; otherwise issuance must wait.
    if ctx.resolves_to_ingress:
        return ChallengeKind.HTTP_01
    if ctx.dns_driver_supports_txt:
        return ChallengeKind.DNS_01
    raise TlsError(
        f"subdomain {ctx.domain!r} doesn't resolve to ingress and "
        "DNS driver can't write TXT records — point CNAME at "
        "ingress to enable HTTP-01"
    )


# ---- renewal severity ----------------------------------------------


class RenewalSeverity(str, Enum):
    """Spec 13 §5.3: notification tiers."""

    OK = "ok"
    """Cert is healthy. No notification."""

    INFO = "info"
    """30-day mark — renewal scheduled by cert-manager soon."""

    WARN = "warn"
    """14-day mark — renewal hasn't happened yet, surface to
    operator dashboard."""

    CRITICAL = "critical"
    """7-day mark — page oncall."""

    EXPIRED = "expired"
    """Past expiry. Production traffic is broken."""


# Spec 13 §5.3: notification thresholds at 30/14/7 days. The
# implementation rule: the severity tier is the lowest threshold
# the days_remaining has crossed, so daily scan emits exactly
# one tier per cert per day.
_RENEWAL_THRESHOLDS = (
    (0, RenewalSeverity.EXPIRED),
    (7, RenewalSeverity.CRITICAL),
    (14, RenewalSeverity.WARN),
    (30, RenewalSeverity.INFO),
)


def renewal_severity(*, days_remaining: int) -> RenewalSeverity:
    """Map days-remaining to a severity tier. Negative or zero
    means expired; values above 30 return OK."""
    if days_remaining <= 0:
        return RenewalSeverity.EXPIRED
    for threshold, tier in _RENEWAL_THRESHOLDS:
        if threshold > 0 and days_remaining <= threshold:
            return tier
    return RenewalSeverity.OK


def at_notification_threshold(
    *,
    days_remaining_today: int,
    days_remaining_yesterday: int,
) -> RenewalSeverity | None:
    """Return the severity tier ONLY when today's scan crosses a
    new threshold relative to yesterday — fires exactly once per
    threshold per cert. Without this guard, a cert at 25 days
    would emit INFO every single day from day 30 down to 14.
    """
    today = renewal_severity(days_remaining=days_remaining_today)
    yday = renewal_severity(days_remaining=days_remaining_yesterday)
    if today == yday:
        return None
    if today == RenewalSeverity.OK:
        # Cert just renewed. Skip notification (good outcome).
        return None
    return today


# ---- mTLS mode validation ------------------------------------------


class MtlsMode(str, Enum):
    """Spec 13 §5.4."""

    OFF = "off"
    EDGE = "edge"
    """Client cert validation at ingress. Internal services."""

    MESH = "mesh"
    """Auto-mTLS between pods inside the service mesh."""

    EDGE_AND_MESH = "edge_and_mesh"
    """Both layers active — defense in depth."""


@dataclasses.dataclass(frozen=True, slots=True)
class MtlsConfig:
    """Per-ingress mTLS config."""

    mode: MtlsMode
    edge_client_ca_secret: str = ""
    """K8s Secret name holding the CA bundle clients are
    validated against. Required for EDGE / EDGE_AND_MESH."""

    mesh_provider: str = ""
    """``istio`` or ``linkerd``. Required for MESH /
    EDGE_AND_MESH. Must match the cluster-level mesh enrollment."""

    def __post_init__(self) -> None:
        if self.mode in (MtlsMode.EDGE, MtlsMode.EDGE_AND_MESH):
            if not self.edge_client_ca_secret:
                raise TlsError(
                    f"mTLS mode {self.mode.value} requires "
                    "edge_client_ca_secret (CA bundle to validate "
                    "client certs against)"
                )
        elif self.edge_client_ca_secret:
            # OFF / MESH-only with a CA secret set is a
            # misconfiguration — the secret won't be consumed.
            raise TlsError(
                f"mTLS mode {self.mode.value} cannot accept "
                "edge_client_ca_secret (would not be consumed)"
            )

        if self.mode in (MtlsMode.MESH, MtlsMode.EDGE_AND_MESH):
            if self.mesh_provider not in ("istio", "linkerd"):
                raise TlsError(
                    f"mTLS mode {self.mode.value} requires "
                    "mesh_provider to be 'istio' or 'linkerd'"
                )


def can_enable_mesh_mtls(
    *,
    cluster_mesh_provider: str,
    requested_provider: str,
) -> bool:
    """Spec 13 §5.4: per-ingress mesh mTLS only valid when the
    cluster is enrolled in the matching mesh."""
    if not cluster_mesh_provider:
        return False
    return cluster_mesh_provider == requested_provider


# ---- per-zone cert plan --------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ZoneCertPlan:
    """Daily renewal scan walks the platform's managed zones and
    emits one of these per zone — input to the cert-manager
    Certificate resource generation."""

    zone: str
    layout: ZoneLayout
    org_slugs: tuple[str, ...]
    """Orgs that need certs in this zone. For FLAT layout, this
    is one org; for ORG_PREFIXED, all orgs with apps in the
    zone."""

    def __post_init__(self) -> None:
        if self.layout == ZoneLayout.FLAT and len(self.org_slugs) != 1:
            raise TlsError(
                "flat-layout zone must have exactly one org "
                f"(zone {self.zone!r} has {len(self.org_slugs)} orgs)"
            )
        if self.layout == ZoneLayout.ORG_PREFIXED and not self.org_slugs:
            raise TlsError(
                f"org-prefixed zone {self.zone!r} requires at least "
                "one org slug"
            )


def expand_cert_plan(*, plan: ZoneCertPlan) -> tuple[WildcardCertScope, ...]:
    """For one zone, emit the wildcard cert scopes the platform
    should issue. FLAT → one scope; ORG_PREFIXED → one scope per
    org."""
    return tuple(
        wildcard_sans_for_org(
            base_zone=plan.zone, org_slug=slug, layout=plan.layout,
        )
        for slug in plan.org_slugs
    )
