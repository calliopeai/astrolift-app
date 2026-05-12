"""Tests for DNS layout + zone registration policy (#153, spec 13 §2.0.3-§2.0.4)."""

from __future__ import annotations

import pytest

from astrolift_clusters.dns_layout import (
    ZONE_REGISTRATION_ORDER,
    DnsLayoutError,
    DnsLayoutMode,
    LayoutMigration,
    NsDelegationCheck,
    ZoneRegistrationStep,
    evaluate_ns_delegation,
    expected_route_count_for_dual_serve,
    hostname_for_app,
    hostnames_for_dual_serve,
    is_dual_serve_active,
)

# ---- hostname construction -----------------------------------------


def test_hostname_flat():
    """Flat: <app>.<zone>."""
    h = hostname_for_app(
        app_slug="api",
        org_slug="acme",
        base_zone="acme.platform.example",
        mode=DnsLayoutMode.FLAT,
    )
    assert h == "api.acme.platform.example"


def test_hostname_org_prefixed():
    """Org-prefixed: <app>.<org>.<zone>."""
    h = hostname_for_app(
        app_slug="api",
        org_slug="acme",
        base_zone="apps.platform.example",
        mode=DnsLayoutMode.ORG_PREFIXED,
    )
    assert h == "api.acme.apps.platform.example"


def test_hostname_lowercases():
    h = hostname_for_app(
        app_slug="API",
        org_slug="ACME",
        base_zone="APPS.PLATFORM.EXAMPLE",
        mode=DnsLayoutMode.FLAT,
    )
    assert h == "api.apps.platform.example"


def test_hostname_strips_trailing_dot():
    h = hostname_for_app(
        app_slug="api",
        org_slug="acme",
        base_zone="acme.platform.example.",
        mode=DnsLayoutMode.FLAT,
    )
    assert h == "api.acme.platform.example"


def test_hostname_rejects_invalid_app_slug():
    with pytest.raises(DnsLayoutError, match="app_slug"):
        hostname_for_app(
            app_slug="My_API",
            org_slug="acme",
            base_zone="z.com",
            mode=DnsLayoutMode.FLAT,
        )


def test_hostname_rejects_empty_app_slug():
    with pytest.raises(DnsLayoutError, match="app_slug"):
        hostname_for_app(
            app_slug="",
            org_slug="acme",
            base_zone="z.com",
            mode=DnsLayoutMode.FLAT,
        )


def test_hostname_rejects_empty_zone():
    with pytest.raises(DnsLayoutError, match="base_zone"):
        hostname_for_app(
            app_slug="api",
            org_slug="acme",
            base_zone="",
            mode=DnsLayoutMode.FLAT,
        )


# ---- zone registration order ---------------------------------------


def test_zone_registration_order_locked():
    """Spec §2.0.4: NS check first, mark_active last. Lock the
    sequence — out-of-order registration can route traffic to
    a non-functioning zone."""
    assert ZONE_REGISTRATION_ORDER[0] == ZoneRegistrationStep.VALIDATE_NS_DELEGATION
    assert ZONE_REGISTRATION_ORDER[-1] == ZoneRegistrationStep.MARK_ACTIVE
    # Health check is the gate before mark_active
    assert (
        ZONE_REGISTRATION_ORDER.index(ZoneRegistrationStep.HEALTH_CHECK)
        == ZONE_REGISTRATION_ORDER.index(ZoneRegistrationStep.MARK_ACTIVE) - 1
    )


# ---- NS delegation -------------------------------------------------


def test_ns_delegation_passes_when_set_matches():
    check = NsDelegationCheck(
        zone="acme.platform.example",
        expected_nameservers=frozenset({"ns1.platform.example", "ns2.platform.example"}),
        observed_nameservers=frozenset({"ns1.platform.example", "ns2.platform.example"}),
    )
    passed, reason = evaluate_ns_delegation(check=check)
    assert passed is True


def test_ns_delegation_passes_with_extra_nameservers_warns():
    """Extra nameservers (operator left old provider's NS in
    place) is a warning not a failure."""
    check = NsDelegationCheck(
        zone="acme.platform.example",
        expected_nameservers=frozenset({"ns1.platform.example", "ns2.platform.example"}),
        observed_nameservers=frozenset(
            {
                "ns1.platform.example",
                "ns2.platform.example",
                "old-ns.previous-provider.com",
            }
        ),
    )
    passed, reason = evaluate_ns_delegation(check=check)
    assert passed is True
    assert "extra" in reason.lower()


def test_ns_delegation_fails_when_observed_empty():
    check = NsDelegationCheck(
        zone="acme.platform.example",
        expected_nameservers=frozenset({"ns1.platform.example"}),
        observed_nameservers=frozenset(),
    )
    passed, reason = evaluate_ns_delegation(check=check)
    assert passed is False
    assert "no NS records observed" in reason


def test_ns_delegation_fails_when_expected_missing():
    check = NsDelegationCheck(
        zone="acme.platform.example",
        expected_nameservers=frozenset({"ns1.platform.example", "ns2.platform.example"}),
        observed_nameservers=frozenset({"ns1.platform.example"}),
    )
    passed, reason = evaluate_ns_delegation(check=check)
    assert passed is False
    assert "incomplete" in reason


def test_ns_delegation_normalizes_case_and_trailing_dots():
    """DNS hostnames are case-insensitive and trailing-dotted by
    convention. Tolerate operator-typed variations."""
    check = NsDelegationCheck(
        zone="acme.platform.example",
        expected_nameservers=frozenset({"NS1.platform.example."}),
        observed_nameservers=frozenset({"ns1.platform.example"}),
    )
    passed, reason = evaluate_ns_delegation(check=check)
    assert passed is True


# ---- layout migration ---------------------------------------------


def test_migration_basic():
    m = LayoutMigration(
        from_mode=DnsLayoutMode.FLAT,
        to_mode=DnsLayoutMode.ORG_PREFIXED,
        started_at_unix=1_700_000_000,
    )
    assert m.dual_serve_days == 30


def test_migration_rejects_same_mode():
    """Migration to same mode is a no-op; refuse so operator
    doesn't trigger a meaningless dual-serve window."""
    with pytest.raises(DnsLayoutError, match="differ"):
        LayoutMigration(
            from_mode=DnsLayoutMode.FLAT,
            to_mode=DnsLayoutMode.FLAT,
            started_at_unix=0,
        )


def test_migration_rejects_zero_days():
    with pytest.raises(DnsLayoutError):
        LayoutMigration(
            from_mode=DnsLayoutMode.FLAT,
            to_mode=DnsLayoutMode.ORG_PREFIXED,
            started_at_unix=0,
            dual_serve_days=0,
        )


def test_migration_caps_at_90_days():
    """Operator-extended ceiling; longer migrations should be
    split into shorter windows for risk control."""
    with pytest.raises(DnsLayoutError, match="90"):
        LayoutMigration(
            from_mode=DnsLayoutMode.FLAT,
            to_mode=DnsLayoutMode.ORG_PREFIXED,
            started_at_unix=0,
            dual_serve_days=120,
        )


def test_dual_serve_returns_both_hostnames():
    m = LayoutMigration(
        from_mode=DnsLayoutMode.FLAT,
        to_mode=DnsLayoutMode.ORG_PREFIXED,
        started_at_unix=1_700_000_000,
    )
    old, new = hostnames_for_dual_serve(
        app_slug="api",
        org_slug="acme",
        base_zone="apps.platform.example",
        migration=m,
    )
    assert old == "api.apps.platform.example"
    assert new == "api.acme.apps.platform.example"


def test_dual_serve_active_within_window():
    m = LayoutMigration(
        from_mode=DnsLayoutMode.FLAT,
        to_mode=DnsLayoutMode.ORG_PREFIXED,
        started_at_unix=1_700_000_000,
        dual_serve_days=30,
    )
    # Day 1
    assert is_dual_serve_active(migration=m, now_unix=1_700_000_000 + 86400) is True
    # Day 30 - 1 second
    assert (
        is_dual_serve_active(
            migration=m,
            now_unix=1_700_000_000 + 30 * 86400 - 1,
        )
        is True
    )


def test_dual_serve_inactive_after_window():
    m = LayoutMigration(
        from_mode=DnsLayoutMode.FLAT,
        to_mode=DnsLayoutMode.ORG_PREFIXED,
        started_at_unix=1_700_000_000,
        dual_serve_days=30,
    )
    # Day 30 + 1 second
    assert (
        is_dual_serve_active(
            migration=m,
            now_unix=1_700_000_000 + 30 * 86400 + 1,
        )
        is False
    )


def test_dual_serve_inactive_before_start():
    m = LayoutMigration(
        from_mode=DnsLayoutMode.FLAT,
        to_mode=DnsLayoutMode.ORG_PREFIXED,
        started_at_unix=1_700_000_000,
    )
    assert (
        is_dual_serve_active(
            migration=m,
            now_unix=1_699_999_999,
        )
        is False
    )


# ---- route count budget -------------------------------------------


def test_route_count_doubles():
    assert expected_route_count_for_dual_serve(app_count=50) == 100


def test_route_count_zero():
    assert expected_route_count_for_dual_serve(app_count=0) == 0


def test_route_count_rejects_negative():
    with pytest.raises(DnsLayoutError):
        expected_route_count_for_dual_serve(app_count=-1)
