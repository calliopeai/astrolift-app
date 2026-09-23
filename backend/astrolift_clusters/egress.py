"""
Egress control policy (#72, spec 13 §7).

Pure-Python module. The cluster-driver activity that emits k8s
NetworkPolicy objects + the cilium-egress-gateway plugin both
consult this for the same answers.

Three concerns:

* **Default deny** of internal CIDRs (RFC 1918 + cluster pod/service
  CIDRs the operator declares). Tenant pods must not be able to
  reach the platform DB or each other's namespaces.
* **Per-app allowlist** of CIDRs and FQDNs. NetworkPolicy enforces
  CIDR-level rules; the egress proxy adds FQDN-level enforcement
  on top.
* **Source-IP shape** declaration: when an app needs deterministic
  source IPs (third-party allow-lists), the policy records whether
  to route via NAT gateway pinning or an egress proxy.
"""

from __future__ import annotations

import dataclasses
import ipaddress
from collections.abc import Sequence
from enum import StrEnum

# RFC 1918 + link-local + carrier-grade NAT. Tenant pods don't need
# to reach these by default; egress proxies and DBs run on private
# CIDRs the operator declares per-cluster on top of these.
DEFAULT_INTERNAL_CIDRS: tuple[str, ...] = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "fc00::/7",
)


# Cluster DNS pods. ``k8s-app: kube-dns`` labels the CoreDNS/kube-dns pods
# (EKS, GKE, AKS, kubeadm), not their namespace, so the peer needs both
# selectors; a namespaceSelector on the pod label matches nothing (#1868).
CLUSTER_DNS_PEER = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
}


class SourceIPMode(StrEnum):
    DEFAULT = "default"  # whatever the cluster's normal egress is
    NAT_PINNED = "nat_pinned"  # provider NAT gateway with stable IP set
    PROXY = "proxy"  # route via cilium egress gateway / similar


# ---- guards ---------------------------------------------------------


class EgressPolicyError(ValueError):
    pass


def _validate_cidr(cidr: str) -> None:
    try:
        ipaddress.ip_network(cidr, strict=True)
    except (ValueError, TypeError) as exc:
        raise EgressPolicyError(f"invalid CIDR {cidr!r}: {exc}") from exc


def _validate_fqdn(fqdn: str) -> None:
    """Light validation — the egress proxy does authoritative
    resolution. We just guard against obvious garbage in the
    admin UI: empty, whitespace, scheme-prefixed."""
    if not fqdn or fqdn.strip() != fqdn:
        raise EgressPolicyError(f"invalid FQDN {fqdn!r}: empty or has surrounding whitespace")
    if "://" in fqdn or "/" in fqdn:
        raise EgressPolicyError(f"FQDN {fqdn!r} should be a hostname, not a URL")


# ---- policy --------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class EgressPolicy:
    """Per-app egress configuration."""

    allowed_cidrs: tuple[str, ...] = ()
    """When non-empty, switches to allowlist mode: only listed CIDRs
    (plus the implicit internal-deny floor) are reachable."""

    allowed_fqdns: tuple[str, ...] = ()
    """FQDN-level rules. Requires the cluster's egress proxy to be
    enabled — k8s NetworkPolicy alone can't enforce hostnames."""

    extra_internal_cidrs: tuple[str, ...] = ()
    """Cluster-specific internal CIDRs the operator declares (e.g.
    pod/service CIDRs that aren't in RFC 1918)."""

    source_ip_mode: SourceIPMode = SourceIPMode.DEFAULT
    """Set to NAT_PINNED or PROXY when an app needs deterministic
    source IPs for third-party allow-lists."""

    def __post_init__(self) -> None:
        for c in self.allowed_cidrs:
            _validate_cidr(c)
        for c in self.extra_internal_cidrs:
            _validate_cidr(c)
        for f in self.allowed_fqdns:
            _validate_fqdn(f)


@dataclasses.dataclass(frozen=True, slots=True)
class EgressDecision:
    """The driver-facing rendering. ``deny_cidrs`` always ships
    even in allowlist mode — the deny rule is the floor, allowlist
    is what gets carved out from the default-allow remainder."""

    deny_cidrs: tuple[str, ...]
    allow_cidrs: tuple[str, ...]
    allow_fqdns: tuple[str, ...]
    requires_proxy: bool
    """True when the policy declares FQDNs (NetworkPolicy alone
    can't enforce them) or when source_ip_mode == PROXY."""


def render(policy: EgressPolicy) -> EgressDecision:
    """Translate a policy into the deny/allow lists the cluster
    driver emits as NetworkPolicy.

    Default-deny internal CIDRs always apply. Allowlist mode
    additionally restricts the app to the listed CIDRs/FQDNs (the
    rest of the public internet is denied).
    """
    deny_cidrs = tuple(DEFAULT_INTERNAL_CIDRS) + policy.extra_internal_cidrs
    allow_cidrs = policy.allowed_cidrs
    allow_fqdns = policy.allowed_fqdns

    requires_proxy = bool(allow_fqdns) or policy.source_ip_mode == SourceIPMode.PROXY

    return EgressDecision(
        deny_cidrs=deny_cidrs,
        allow_cidrs=allow_cidrs,
        allow_fqdns=allow_fqdns,
        requires_proxy=requires_proxy,
    )


def is_internal_destination(*, ip_or_cidr: str, internal_cidrs: Sequence[str]) -> bool:
    """Helper for allowlist authoring + the policy linter:
    'is this CIDR I'm about to add covered by the deny floor?'

    Accepts a bare IP (treated as /32 or /128) or a CIDR.
    """
    try:
        target = ipaddress.ip_network(ip_or_cidr, strict=False)
    except (ValueError, TypeError):
        return False
    for cidr in internal_cidrs:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except (ValueError, TypeError):
            continue
        # subnet_of raises TypeError on mismatched IP versions —
        # an IPv4 target can't be inside an IPv6 internal range.
        if target.version != net.version:
            continue
        # Subset: pure 'inside'. Superset: an admin allow-listed
        # 0.0.0.0/0 would erase the deny floor — surface it.
        if target.subnet_of(net) or net.subnet_of(target):
            return True
    return False


def lint_policy(policy: EgressPolicy) -> tuple[str, ...]:
    """Return warnings about a policy. Warnings are not errors —
    the operator may intentionally allow-list an internal CIDR
    for legitimate reasons (cross-namespace mesh access, etc.)
    but they should see what they're doing."""
    warnings: list[str] = []
    internals = list(DEFAULT_INTERNAL_CIDRS) + list(policy.extra_internal_cidrs)
    for cidr in policy.allowed_cidrs:
        if is_internal_destination(ip_or_cidr=cidr, internal_cidrs=internals):
            warnings.append(
                f"allowed_cidrs entry {cidr!r} overlaps an internal "
                "deny range; the allowlist will erase the deny floor "
                "for that range"
            )
    if policy.allowed_fqdns and policy.source_ip_mode == SourceIPMode.DEFAULT:
        warnings.append(
            "allowed_fqdns set but source_ip_mode=default; "
            "FQDN-level egress requires PROXY mode (NetworkPolicy "
            "alone can only enforce CIDRs)"
        )
    return tuple(warnings)
