"""Tests for edge security defaults (#65)."""

from __future__ import annotations

from _sdk.edge_security import (
    DEFAULT_PROFILE,
    EdgeSecurityProfile,
    HstsPolicy,
    SecurityHeaders,
    TlsPolicy,
    WafPolicy,
    alb_annotations,
    appgw_annotations,
    gcp_annotations,
    nginx_annotations,
)


def test_default_profile_is_secure() -> None:
    assert DEFAULT_PROFILE.tls.min_version == "TLSv1.3"
    assert DEFAULT_PROFILE.hsts.enabled is True
    assert DEFAULT_PROFILE.hsts.max_age_seconds == 31_536_000
    assert DEFAULT_PROFILE.hsts.include_subdomains is True
    assert DEFAULT_PROFILE.waf.enabled is True


def test_headers_dict_includes_hsts_and_security() -> None:
    headers = DEFAULT_PROFILE.headers_dict()
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == ("strict-origin-when-cross-origin")
    assert "max-age=31536000" in headers["Strict-Transport-Security"]
    assert "includeSubDomains" in headers["Strict-Transport-Security"]
    assert "preload" in headers["Strict-Transport-Security"]


def test_hsts_disabled_omits_header() -> None:
    profile = EdgeSecurityProfile(hsts=HstsPolicy(enabled=False))
    headers = profile.headers_dict()
    assert "Strict-Transport-Security" not in headers


def test_csp_emitted_when_set() -> None:
    profile = EdgeSecurityProfile(
        headers=SecurityHeaders(
            content_security_policy="default-src 'self'",
        )
    )
    headers = profile.headers_dict()
    assert headers["Content-Security-Policy"] == "default-src 'self'"


def test_nginx_annotations_set_tls_policy() -> None:
    annos = nginx_annotations(DEFAULT_PROFILE)
    assert annos["nginx.ingress.kubernetes.io/ssl-protocols"] == "TLSv1.3"
    assert annos["nginx.ingress.kubernetes.io/ssl-prefer-server-ciphers"] == "true"


def test_nginx_emits_security_headers_snippet() -> None:
    annos = nginx_annotations(DEFAULT_PROFILE)
    snippet = annos["nginx.ingress.kubernetes.io/configuration-snippet"]
    assert "X-Content-Type-Options: nosniff" in snippet
    assert "X-Frame-Options: DENY" in snippet
    assert "Strict-Transport-Security:" in snippet


def test_nginx_rate_limit_translated() -> None:
    profile = EdgeSecurityProfile(
        waf=WafPolicy(
            rate_limit_requests_per_5min=600,
        )
    )
    annos = nginx_annotations(profile)
    # 600 / 300 = 2 rps
    assert annos["nginx.ingress.kubernetes.io/limit-rps"] == "2"


def test_alb_annotations_use_tls13_policy() -> None:
    annos = alb_annotations(DEFAULT_PROFILE)
    assert annos["alb.ingress.kubernetes.io/ssl-policy"] == ("ELBSecurityPolicy-TLS13-1-2-2021-06")


def test_alb_with_tls12_floor() -> None:
    profile = EdgeSecurityProfile(
        tls=TlsPolicy(min_version="TLSv1.2"),
    )
    annos = alb_annotations(profile)
    assert annos["alb.ingress.kubernetes.io/ssl-policy"] == ("ELBSecurityPolicy-TLS-1-2-2017-01")


def test_alb_includes_waf_when_enabled() -> None:
    annos = alb_annotations(DEFAULT_PROFILE)
    assert "alb.ingress.kubernetes.io/wafv2-acl-arn" in annos


def test_gcp_includes_backend_config_when_waf_enabled() -> None:
    annos = gcp_annotations(DEFAULT_PROFILE)
    assert "cloud.google.com/backend-config" in annos


def test_appgw_emits_ssl_redirect_for_tls13() -> None:
    annos = appgw_annotations(DEFAULT_PROFILE)
    assert annos.get("appgw.ingress.kubernetes.io/ssl-redirect") == "true"


def test_waf_disabled_strips_waf_annotations() -> None:
    profile = EdgeSecurityProfile(waf=WafPolicy(enabled=False))
    alb_annos = alb_annotations(profile)
    assert "alb.ingress.kubernetes.io/wafv2-acl-arn" not in alb_annos
    gcp_annos = gcp_annotations(profile)
    assert "cloud.google.com/backend-config" not in gcp_annos
