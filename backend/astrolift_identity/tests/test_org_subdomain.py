"""Tests for per-org subdomain + cookie scoping (#163, spec 03 §3.4)."""

from __future__ import annotations

import pytest

from astrolift_identity.org_subdomain import (
    RESERVED_SUBDOMAIN_LABELS,
    CrossOrgAccessDenied,
    HostBinding,
    OrgSubdomainConfig,
    check_session_against_host,
    cookie_attrs_for,
    resolve_host,
)


def _cfg(**kw) -> OrgSubdomainConfig:
    base = dict(
        org_id=1,
        org_slug="acme",
        base_zone="platform.example.com",
        subdomain_label="acme",
        custom_domains=(),
        idp_id=42,
    )
    base.update(kw)
    return OrgSubdomainConfig(**base)


# ---- config guards --------------------------------------------------


def test_config_rejects_empty_slug():
    with pytest.raises(ValueError):
        OrgSubdomainConfig(
            org_id=1, org_slug="", base_zone="x.example.com",
            subdomain_label="acme",
        )


def test_config_rejects_reserved_subdomain_label():
    """An admin typo must not let an org claim 'admin' or 'api' as
    its subdomain — those are install-level routes."""
    for label in ("admin", "api", "auth", "www"):
        with pytest.raises(ValueError, match="reserved"):
            _cfg(subdomain_label=label)


def test_reserved_label_set_includes_critical_routes():
    """Lock the set so a future addition that misses 'auth' or 'api'
    surfaces as a code review failure."""
    assert "admin" in RESERVED_SUBDOMAIN_LABELS
    assert "api" in RESERVED_SUBDOMAIN_LABELS
    assert "auth" in RESERVED_SUBDOMAIN_LABELS


def test_fqdn_empty_when_no_label():
    cfg = _cfg(subdomain_label="")
    assert cfg.fqdn == ""


def test_fqdn_concatenates_label_and_base():
    cfg = _cfg(subdomain_label="acme")
    assert cfg.fqdn == "acme.platform.example.com"


# ---- resolve_host ---------------------------------------------------


def test_exact_subdomain_match_returns_org_binding():
    cfgs = [_cfg()]
    binding = resolve_host(host="acme.platform.example.com", configs=cfgs)
    assert binding.org_id == 1
    assert binding.org_slug == "acme"
    assert binding.cookie_domain == "acme.platform.example.com"
    assert binding.requires_idp_id == 42


def test_resolve_strips_port_from_host():
    cfgs = [_cfg()]
    binding = resolve_host(host="acme.platform.example.com:8443", configs=cfgs)
    assert binding.org_slug == "acme"


def test_custom_domain_match_returns_org_binding():
    cfgs = [_cfg(custom_domains=("platform.acme.com",))]
    binding = resolve_host(host="platform.acme.com", configs=cfgs)
    assert binding.org_id == 1
    # Cookie domain matches the custom host exactly, NOT the
    # subdomain. Cookies set on platform.acme.com don't bleed to
    # acme.platform.example.com.
    assert binding.cookie_domain == "platform.acme.com"


def test_no_match_returns_default_host_binding():
    cfgs = [_cfg()]
    binding = resolve_host(host="login.platform.example.com", configs=cfgs)
    assert binding.org_id is None
    assert binding.org_slug == ""
    assert binding.cookie_domain == "login.platform.example.com"


def test_resolve_is_case_insensitive():
    cfgs = [_cfg()]
    binding = resolve_host(host="ACME.PLATFORM.example.com", configs=cfgs)
    assert binding.org_slug == "acme"


def test_empty_host_rejected():
    with pytest.raises(ValueError):
        resolve_host(host="", configs=[_cfg()])


# ---- session enforcement -------------------------------------------


def test_matching_session_passes():
    binding = HostBinding(
        org_id=1, org_slug="acme",
        host="acme.platform.example.com",
        cookie_domain="acme.platform.example.com",
        requires_idp_id=42,
    )
    check_session_against_host(binding=binding, session_org_slug="acme")


def test_mismatched_session_raises_with_both_slugs():
    binding = HostBinding(
        org_id=1, org_slug="acme",
        host="acme.platform.example.com",
        cookie_domain="acme.platform.example.com",
        requires_idp_id=42,
    )
    with pytest.raises(CrossOrgAccessDenied) as exc:
        check_session_against_host(binding=binding, session_org_slug="other-org")
    assert exc.value.presented_org_slug == "other-org"
    assert exc.value.host_org_slug == "acme"


def test_default_host_accepts_any_session():
    """Install default host = no org enforcement; caller renders
    the org selector / sign-in page."""
    binding = HostBinding(
        org_id=None, org_slug="",
        host="login.platform.example.com",
        cookie_domain="login.platform.example.com",
        requires_idp_id=None,
    )
    check_session_against_host(binding=binding, session_org_slug="acme")
    check_session_against_host(binding=binding, session_org_slug="")


def test_anonymous_on_org_subdomain_does_not_raise():
    """Anonymous request on a per-org subdomain is normal (the caller
    redirects to the org's IdP). Not a CrossOrgAccessDenied."""
    binding = HostBinding(
        org_id=1, org_slug="acme",
        host="acme.platform.example.com",
        cookie_domain="acme.platform.example.com",
        requires_idp_id=42,
    )
    check_session_against_host(binding=binding, session_org_slug="")


# ---- cookie attrs ---------------------------------------------------


def test_cookie_attrs_use_exact_host_no_leading_dot():
    """Leading-dot Domain= would extend to subdomains. We bind to
    the EXACT host so isolation holds — a cookie set on
    acme.platform.example.com isn't sent to other.platform.example.com."""
    binding = HostBinding(
        org_id=1, org_slug="acme",
        host="acme.platform.example.com",
        cookie_domain="acme.platform.example.com",
        requires_idp_id=42,
    )
    attrs = cookie_attrs_for(binding)
    assert attrs["domain"] == "acme.platform.example.com"
    assert not attrs["domain"].startswith(".")


def test_cookie_attrs_baseline_security_flags():
    binding = HostBinding(
        org_id=1, org_slug="acme",
        host="acme.platform.example.com",
        cookie_domain="acme.platform.example.com",
        requires_idp_id=42,
    )
    attrs = cookie_attrs_for(binding)
    assert attrs["secure"] is True
    assert attrs["httponly"] is True
    assert attrs["samesite"] == "Lax"
    assert attrs["path"] == "/"


def test_cookie_attrs_secure_false_for_dev():
    """Local dev over HTTP: caller flips secure=False so the cookie
    actually persists. Production callers always pass True (or omit)."""
    binding = HostBinding(
        org_id=1, org_slug="acme",
        host="localhost",
        cookie_domain="localhost",
        requires_idp_id=None,
    )
    attrs = cookie_attrs_for(binding, secure=False)
    assert attrs["secure"] is False
