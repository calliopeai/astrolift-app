"""Unit tests for the pure custom-domain handshake helpers (#397).

The handshake module is intentionally side-effect-free so tests can
exercise it without spinning up models / drivers / a workflow stack.
"""

from __future__ import annotations

from astrolift_lifecycle.custom_domain_handshake import (
    build_handshake,
    generate_challenge_token,
    hostname_parent_zone,
    resolve_cluster_ingress_target,
)

# ---- generate_challenge_token ----------------------------------------


def test_token_default_length():
    t = generate_challenge_token()
    assert len(t) == 32


def test_token_charset_is_safe():
    """Lowercase alphanumeric so the value survives every BIND-flavored
    zone editor's quoting layer without escaping."""
    t = generate_challenge_token(length=128)
    assert all(c.islower() or c.isdigit() for c in t)


def test_token_entropy_distinct():
    """Two consecutive calls almost certainly disagree."""
    a = generate_challenge_token()
    b = generate_challenge_token()
    assert a != b


# ---- hostname_parent_zone --------------------------------------------


def test_parent_zone_of_apex_is_itself():
    assert hostname_parent_zone("acme.com") == "acme.com"


def test_parent_zone_of_subdomain():
    assert hostname_parent_zone("api.acme.com") == "acme.com"


def test_parent_zone_of_deep_subdomain():
    assert hostname_parent_zone("api.staging.acme.com") == "staging.acme.com"


def test_parent_zone_strips_trailing_dot():
    assert hostname_parent_zone("api.acme.com.") == "acme.com"


def test_parent_zone_lowercases():
    assert hostname_parent_zone("API.ACME.com") == "acme.com"


# ---- resolve_cluster_ingress_target ----------------------------------


def test_target_uses_managed_domain_when_set():
    out = resolve_cluster_ingress_target(
        cluster_slug="eks-prd",
        managed_domain_zone="apps.example.com",
    )
    assert out == "ingress.apps.example.com"


def test_target_falls_back_to_cluster_slug():
    out = resolve_cluster_ingress_target(
        cluster_slug="eks-prd",
        managed_domain_zone=None,
    )
    assert out == "eks-prd.ingress.astrolift.app"


# ---- build_handshake -------------------------------------------------


def test_handshake_emits_cname_and_txt_rows():
    hs = build_handshake(
        hostname="api.example.com",
        cluster_ingress_target="ingress.apps.example.com",
        is_platform_managed_zone=False,
    )
    kinds = [r.kind for r in hs.required_records]
    assert "CNAME" in kinds
    assert "TXT" in kinds


def test_handshake_cname_targets_cluster_ingress():
    hs = build_handshake(
        hostname="api.example.com",
        cluster_ingress_target="ingress.apps.example.com",
        is_platform_managed_zone=False,
    )
    cname = next(r for r in hs.required_records if r.kind == "CNAME")
    assert cname.name == "api.example.com."
    assert cname.value == "ingress.apps.example.com."


def test_handshake_txt_uses_challenge_name():
    hs = build_handshake(
        hostname="api.example.com",
        cluster_ingress_target="ingress.x",
        is_platform_managed_zone=False,
    )
    txt = next(r for r in hs.required_records if r.kind == "TXT")
    assert txt.name == "_astrolift-challenge.api.example.com."
    assert txt.value == hs.txt_challenge_token


def test_handshake_propagated_starts_false():
    hs = build_handshake(
        hostname="api.example.com",
        cluster_ingress_target="ingress.x",
        is_platform_managed_zone=False,
    )
    assert all(r.propagated is False for r in hs.required_records)
    assert all(r.message == "" for r in hs.required_records)


def test_handshake_carries_managed_zone_flag():
    hs = build_handshake(
        hostname="api.example.com",
        cluster_ingress_target="ingress.x",
        is_platform_managed_zone=True,
    )
    assert hs.is_platform_managed_zone is True


def test_handshake_accepts_explicit_token():
    """The mutation may pass a pre-generated token (e.g. for an idempotent
    re-create that wants to keep the prior challenge alive)."""
    hs = build_handshake(
        hostname="api.example.com",
        cluster_ingress_target="ingress.x",
        is_platform_managed_zone=False,
        challenge_token="fixed-token",
    )
    assert hs.txt_challenge_token == "fixed-token"
    txt = next(r for r in hs.required_records if r.kind == "TXT")
    assert txt.value == "fixed-token"


def test_handshake_to_dict_shape():
    """The mutation persists to a JSON field via this method; the dict
    keys are the contract the GraphQL type reads."""
    hs = build_handshake(
        hostname="api.example.com",
        cluster_ingress_target="ingress.x",
        is_platform_managed_zone=False,
    )
    d = hs.to_dict()
    assert set(d.keys()) == {
        "hostname",
        "txt_challenge_token",
        "expected_cname_target",
        "required_records",
        "is_platform_managed_zone",
    }
    for r in d["required_records"]:
        assert set(r.keys()) == {
            "kind",
            "name",
            "value",
            "ttl",
            "propagated",
            "last_checked_at",
            "message",
        }


def test_handshake_strips_leading_dot_on_hostname():
    hs = build_handshake(
        hostname=".api.example.com",
        cluster_ingress_target="ingress.x",
        is_platform_managed_zone=False,
    )
    assert hs.hostname == "api.example.com"
