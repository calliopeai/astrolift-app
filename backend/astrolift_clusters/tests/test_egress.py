"""Tests for egress policy (#72, spec 13 §7)."""

from __future__ import annotations

import pytest

from astrolift_clusters.egress import (
    DEFAULT_INTERNAL_CIDRS,
    EgressPolicy,
    EgressPolicyError,
    SourceIPMode,
    is_internal_destination,
    lint_policy,
    render,
)


# ---- guards ---------------------------------------------------------


def test_allowed_cidr_must_parse():
    with pytest.raises(EgressPolicyError, match="invalid CIDR"):
        EgressPolicy(allowed_cidrs=("not-a-cidr",))


def test_internal_cidr_must_parse():
    with pytest.raises(EgressPolicyError, match="invalid CIDR"):
        EgressPolicy(extra_internal_cidrs=("999.0.0.0/8",))


def test_allowed_fqdn_must_be_hostname_not_url():
    with pytest.raises(EgressPolicyError, match="should be a hostname"):
        EgressPolicy(allowed_fqdns=("https://api.example.com",))
    with pytest.raises(EgressPolicyError, match="should be a hostname"):
        EgressPolicy(allowed_fqdns=("api.example.com/path",))


def test_allowed_fqdn_must_not_be_empty_or_padded():
    with pytest.raises(EgressPolicyError, match="empty"):
        EgressPolicy(allowed_fqdns=("",))
    with pytest.raises(EgressPolicyError, match="whitespace"):
        EgressPolicy(allowed_fqdns=("  api.example.com  ",))


# ---- defaults / render ---------------------------------------------


def test_empty_policy_renders_default_deny():
    out = render(EgressPolicy())
    assert "10.0.0.0/8" in out.deny_cidrs
    assert "192.168.0.0/16" in out.deny_cidrs
    assert "169.254.0.0/16" in out.deny_cidrs
    assert "fc00::/7" in out.deny_cidrs
    assert out.allow_cidrs == ()
    assert out.allow_fqdns == ()
    assert out.requires_proxy is False


def test_extra_internal_cidrs_extend_deny_floor():
    out = render(EgressPolicy(extra_internal_cidrs=("100.96.0.0/12",)))
    assert "100.96.0.0/12" in out.deny_cidrs
    # Defaults still present
    assert "10.0.0.0/8" in out.deny_cidrs


def test_allowlist_mode_carries_through():
    policy = EgressPolicy(
        allowed_cidrs=("203.0.113.0/24",),
        allowed_fqdns=("api.stripe.com",),
        source_ip_mode=SourceIPMode.PROXY,
    )
    out = render(policy)
    assert out.allow_cidrs == ("203.0.113.0/24",)
    assert out.allow_fqdns == ("api.stripe.com",)
    assert out.requires_proxy is True


def test_proxy_required_when_fqdns_listed():
    """NetworkPolicy alone can't enforce FQDNs — caller must wire
    the egress proxy."""
    out = render(EgressPolicy(allowed_fqdns=("api.example.com",)))
    assert out.requires_proxy is True


def test_proxy_required_when_source_ip_mode_proxy():
    out = render(EgressPolicy(source_ip_mode=SourceIPMode.PROXY))
    assert out.requires_proxy is True


def test_nat_pinned_does_not_force_proxy():
    """NAT pinning is a provider-level NAT gateway, not an in-cluster
    proxy — different path."""
    out = render(EgressPolicy(source_ip_mode=SourceIPMode.NAT_PINNED))
    assert out.requires_proxy is False


# ---- is_internal_destination ---------------------------------------


def test_internal_detection_subset_match():
    """10.1.2.0/24 is inside 10.0.0.0/8 → internal."""
    assert is_internal_destination(
        ip_or_cidr="10.1.2.0/24",
        internal_cidrs=DEFAULT_INTERNAL_CIDRS,
    ) is True


def test_internal_detection_bare_ip():
    """Bare IP treated as /32. 192.168.5.5 is internal."""
    assert is_internal_destination(
        ip_or_cidr="192.168.5.5",
        internal_cidrs=DEFAULT_INTERNAL_CIDRS,
    ) is True


def test_external_destination_not_internal():
    assert is_internal_destination(
        ip_or_cidr="8.8.8.8",
        internal_cidrs=DEFAULT_INTERNAL_CIDRS,
    ) is False


def test_superset_overlap_flagged():
    """An admin allow-listing 0.0.0.0/0 would supersede every
    internal range. Surface it."""
    assert is_internal_destination(
        ip_or_cidr="0.0.0.0/0",
        internal_cidrs=DEFAULT_INTERNAL_CIDRS,
    ) is True


def test_garbage_input_returns_false_quietly():
    assert is_internal_destination(
        ip_or_cidr="not-an-ip",
        internal_cidrs=DEFAULT_INTERNAL_CIDRS,
    ) is False


# ---- lint -----------------------------------------------------------


def test_lint_warns_when_allowlist_overlaps_internal():
    policy = EgressPolicy(allowed_cidrs=("10.5.0.0/16",))
    warnings = lint_policy(policy)
    assert any("internal" in w for w in warnings)


def test_lint_warns_when_fqdns_set_without_proxy():
    """FQDN entries are useless without the egress proxy on."""
    policy = EgressPolicy(
        allowed_fqdns=("api.example.com",),
        source_ip_mode=SourceIPMode.DEFAULT,
    )
    warnings = lint_policy(policy)
    assert any("FQDN" in w for w in warnings)


def test_lint_clean_policy_no_warnings():
    policy = EgressPolicy(
        allowed_cidrs=("203.0.113.0/24",),
        allowed_fqdns=("api.example.com",),
        source_ip_mode=SourceIPMode.PROXY,
    )
    assert lint_policy(policy) == ()
