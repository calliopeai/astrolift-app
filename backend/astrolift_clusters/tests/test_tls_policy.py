"""Tests for TLS issuance + renewal policy (#70, spec 13 §5)."""

from __future__ import annotations

import pytest

from astrolift_clusters.tls_policy import (
    ChallengeKind,
    CustomDomainCertContext,
    MtlsConfig,
    MtlsMode,
    RenewalSeverity,
    TlsError,
    ZoneCertPlan,
    ZoneLayout,
    at_notification_threshold,
    can_enable_mesh_mtls,
    challenge_for_custom_domain,
    expand_cert_plan,
    renewal_severity,
    wildcard_sans_for_org,
)

# ---- wildcard SAN construction -------------------------------------


def test_wildcard_sans_org_prefixed():
    """Spec 13 §5.1: three SANs per org under shared base zone."""
    scope = wildcard_sans_for_org(
        base_zone="apps.platform.example",
        org_slug="acme",
        layout=ZoneLayout.ORG_PREFIXED,
    )
    assert scope.sans == (
        "acme.apps.platform.example",
        "*.acme.apps.platform.example",
        "*.pr.acme.apps.platform.example",
    )
    # Challenge stays at the base zone (TXT goes there).
    assert scope.challenge_zone == "apps.platform.example"


def test_wildcard_sans_flat_layout():
    """Spec 13 §2.0.2: flat layout = one wildcard at *.<zone>."""
    scope = wildcard_sans_for_org(
        base_zone="acme.platform.example",
        org_slug="acme",
        layout=ZoneLayout.FLAT,
    )
    assert scope.sans == (
        "acme.platform.example",
        "*.acme.platform.example",
        "*.pr.acme.platform.example",
    )
    assert scope.challenge_zone == "acme.platform.example"


def test_wildcard_sans_lowercases_inputs():
    """RFC 1035 says hostnames are case-insensitive; canonicalize
    so renewal-time digest comparison stays stable across casing
    drift in the database."""
    scope = wildcard_sans_for_org(
        base_zone="ACME.Platform.Example",
        org_slug="ACME",
        layout=ZoneLayout.FLAT,
    )
    assert scope.sans[0] == "acme.platform.example"


def test_wildcard_sans_strips_trailing_dot():
    """DNS zones often arrive trailing-dotted from drivers; strip
    so SANs match cert provider expectations (no trailing dot)."""
    scope = wildcard_sans_for_org(
        base_zone="acme.platform.example.",
        org_slug="acme",
        layout=ZoneLayout.FLAT,
    )
    assert scope.sans[0] == "acme.platform.example"


def test_wildcard_sans_requires_inputs():
    with pytest.raises(TlsError):
        wildcard_sans_for_org(
            base_zone="", org_slug="acme", layout=ZoneLayout.FLAT,
        )
    with pytest.raises(TlsError):
        wildcard_sans_for_org(
            base_zone="zone", org_slug="", layout=ZoneLayout.FLAT,
        )


def test_wildcard_san_order_is_deterministic():
    """Cert digests change when SAN order changes; consumers
    (reverse proxies, mTLS clients) cache by digest. Renewal
    must produce the same digest when scope is unchanged."""
    a = wildcard_sans_for_org(
        base_zone="z.com", org_slug="acme", layout=ZoneLayout.FLAT,
    )
    b = wildcard_sans_for_org(
        base_zone="z.com", org_slug="acme", layout=ZoneLayout.FLAT,
    )
    assert a.sans == b.sans


# ---- custom-domain challenge selection -----------------------------


def test_challenge_subdomain_resolving_to_ingress():
    ctx = CustomDomainCertContext(
        domain="api.acme.com",
        is_apex=False, is_wildcard=False,
        dns_driver_supports_txt=False,
        resolves_to_ingress=True,
    )
    assert challenge_for_custom_domain(ctx=ctx) == ChallengeKind.HTTP_01


def test_challenge_subdomain_with_dns_driver_no_resolve():
    """DNS-01 fallback when domain hasn't propagated yet but we
    own the zone."""
    ctx = CustomDomainCertContext(
        domain="api.acme.com",
        is_apex=False, is_wildcard=False,
        dns_driver_supports_txt=True,
        resolves_to_ingress=False,
    )
    assert challenge_for_custom_domain(ctx=ctx) == ChallengeKind.DNS_01


def test_challenge_subdomain_neither_works():
    """No A-record propagation + no DNS write access = block until
    operator points CNAME."""
    ctx = CustomDomainCertContext(
        domain="api.acme.com",
        is_apex=False, is_wildcard=False,
        dns_driver_supports_txt=False,
        resolves_to_ingress=False,
    )
    with pytest.raises(TlsError, match="point CNAME"):
        challenge_for_custom_domain(ctx=ctx)


def test_challenge_apex_with_dns_driver_prefers_dns01():
    """Apex without CNAME flattening is unreliable for HTTP-01;
    prefer DNS-01 when we manage the zone."""
    ctx = CustomDomainCertContext(
        domain="acme.com",
        is_apex=True, is_wildcard=False,
        dns_driver_supports_txt=True,
        resolves_to_ingress=True,
    )
    assert challenge_for_custom_domain(ctx=ctx) == ChallengeKind.DNS_01


def test_challenge_apex_without_dns_driver_uses_http01_when_resolves():
    """No DNS write access; A-record exists at apex (operator
    configured ALIAS/ANAME); HTTP-01 will work."""
    ctx = CustomDomainCertContext(
        domain="acme.com",
        is_apex=True, is_wildcard=False,
        dns_driver_supports_txt=False,
        resolves_to_ingress=True,
    )
    assert challenge_for_custom_domain(ctx=ctx) == ChallengeKind.HTTP_01


def test_challenge_apex_without_either_fails():
    ctx = CustomDomainCertContext(
        domain="acme.com",
        is_apex=True, is_wildcard=False,
        dns_driver_supports_txt=False,
        resolves_to_ingress=False,
    )
    with pytest.raises(TlsError, match="apex"):
        challenge_for_custom_domain(ctx=ctx)


def test_challenge_wildcard_must_use_dns01():
    """*.example.com has no HTTP server to answer the challenge."""
    ctx = CustomDomainCertContext(
        domain="*.acme.com",
        is_apex=False, is_wildcard=True,
        dns_driver_supports_txt=True,
        resolves_to_ingress=False,
    )
    assert challenge_for_custom_domain(ctx=ctx) == ChallengeKind.DNS_01


def test_challenge_wildcard_without_dns_driver_fails():
    """Customer-managed DNS pointing model can't issue wildcards
    via this platform — they'd need to bring their own."""
    ctx = CustomDomainCertContext(
        domain="*.acme.com",
        is_apex=False, is_wildcard=True,
        dns_driver_supports_txt=False,
        resolves_to_ingress=True,
    )
    with pytest.raises(TlsError, match="wildcard"):
        challenge_for_custom_domain(ctx=ctx)


# ---- renewal severity ----------------------------------------------


@pytest.mark.parametrize("days,expected", [
    (60, RenewalSeverity.OK),
    (31, RenewalSeverity.OK),
    (30, RenewalSeverity.INFO),
    (20, RenewalSeverity.INFO),
    (15, RenewalSeverity.INFO),
    (14, RenewalSeverity.WARN),
    (10, RenewalSeverity.WARN),
    (8, RenewalSeverity.WARN),
    (7, RenewalSeverity.CRITICAL),
    (1, RenewalSeverity.CRITICAL),
    (0, RenewalSeverity.EXPIRED),
    (-1, RenewalSeverity.EXPIRED),
])
def test_renewal_severity_thresholds(days, expected):
    """Spec 13 §5.3: tiers at 30/14/7 days."""
    assert renewal_severity(days_remaining=days) == expected


def test_threshold_fires_only_on_crossing():
    """Daily scan: if cert was at 31 yesterday (OK) and 30 today
    (INFO), emit INFO. If yesterday was 30 and today is 25, both
    are INFO — no emission."""
    assert at_notification_threshold(
        days_remaining_today=30, days_remaining_yesterday=31,
    ) == RenewalSeverity.INFO
    assert at_notification_threshold(
        days_remaining_today=25, days_remaining_yesterday=30,
    ) is None
    assert at_notification_threshold(
        days_remaining_today=14, days_remaining_yesterday=15,
    ) == RenewalSeverity.WARN
    assert at_notification_threshold(
        days_remaining_today=7, days_remaining_yesterday=8,
    ) == RenewalSeverity.CRITICAL


def test_threshold_does_not_fire_on_healthy_renewal():
    """Cert renewed: yesterday was 5 days remaining (CRITICAL),
    today is 90 (OK). Do NOT spam a 'phew' notification."""
    assert at_notification_threshold(
        days_remaining_today=90, days_remaining_yesterday=5,
    ) is None


def test_threshold_fires_on_expiration():
    assert at_notification_threshold(
        days_remaining_today=0, days_remaining_yesterday=1,
    ) == RenewalSeverity.EXPIRED


# ---- mTLS validation -----------------------------------------------


def test_mtls_off_accepts_no_extras():
    cfg = MtlsConfig(mode=MtlsMode.OFF)
    assert cfg.mode == MtlsMode.OFF


def test_mtls_off_with_secret_rejected():
    """Setting a CA bundle while mode is OFF means the operator
    expected mTLS but typo'd the mode. Refuse to mask the
    misconfiguration."""
    with pytest.raises(TlsError, match="cannot accept"):
        MtlsConfig(
            mode=MtlsMode.OFF,
            edge_client_ca_secret="acme-ca",
        )


def test_mtls_edge_requires_ca_secret():
    with pytest.raises(TlsError, match="edge_client_ca_secret"):
        MtlsConfig(mode=MtlsMode.EDGE)


def test_mtls_edge_with_secret_ok():
    cfg = MtlsConfig(
        mode=MtlsMode.EDGE,
        edge_client_ca_secret="acme-ca",
    )
    assert cfg.edge_client_ca_secret == "acme-ca"


def test_mtls_mesh_requires_provider():
    with pytest.raises(TlsError, match="mesh_provider"):
        MtlsConfig(mode=MtlsMode.MESH, mesh_provider="")


def test_mtls_mesh_provider_must_be_known():
    with pytest.raises(TlsError, match="mesh_provider"):
        MtlsConfig(mode=MtlsMode.MESH, mesh_provider="consul")


def test_mtls_edge_and_mesh_requires_both_fields():
    cfg = MtlsConfig(
        mode=MtlsMode.EDGE_AND_MESH,
        edge_client_ca_secret="acme-ca",
        mesh_provider="istio",
    )
    assert cfg.mode == MtlsMode.EDGE_AND_MESH


def test_can_enable_mesh_mtls_matches_cluster():
    assert can_enable_mesh_mtls(
        cluster_mesh_provider="istio", requested_provider="istio",
    ) is True


def test_can_enable_mesh_mtls_provider_mismatch():
    assert can_enable_mesh_mtls(
        cluster_mesh_provider="linkerd", requested_provider="istio",
    ) is False


def test_can_enable_mesh_mtls_no_cluster_mesh():
    assert can_enable_mesh_mtls(
        cluster_mesh_provider="", requested_provider="istio",
    ) is False


# ---- zone cert plan ------------------------------------------------


def test_zone_cert_plan_flat_one_org():
    plan = ZoneCertPlan(
        zone="acme.platform.example",
        layout=ZoneLayout.FLAT,
        org_slugs=("acme",),
    )
    scopes = expand_cert_plan(plan=plan)
    assert len(scopes) == 1
    assert scopes[0].sans[0] == "acme.platform.example"


def test_zone_cert_plan_flat_rejects_multi_org():
    """Flat layout = one org per zone by construction."""
    with pytest.raises(TlsError, match="exactly one org"):
        ZoneCertPlan(
            zone="z.com",
            layout=ZoneLayout.FLAT,
            org_slugs=("acme", "globex"),
        )


def test_zone_cert_plan_org_prefixed_multi_org():
    plan = ZoneCertPlan(
        zone="apps.platform.example",
        layout=ZoneLayout.ORG_PREFIXED,
        org_slugs=("acme", "globex"),
    )
    scopes = expand_cert_plan(plan=plan)
    assert len(scopes) == 2
    assert scopes[0].sans[0] == "acme.apps.platform.example"
    assert scopes[1].sans[0] == "globex.apps.platform.example"


def test_zone_cert_plan_org_prefixed_requires_at_least_one_org():
    with pytest.raises(TlsError, match="at least one"):
        ZoneCertPlan(
            zone="apps.platform.example",
            layout=ZoneLayout.ORG_PREFIXED,
            org_slugs=(),
        )
