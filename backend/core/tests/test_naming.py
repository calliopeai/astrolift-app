"""Tests for naming convention rules (#170 part 1, spec 01 §9)."""

from __future__ import annotations

import pytest

from core.naming import (
    ALL_RULES,
    APP_NAME,
    DNS_LABEL,
    ENV_NAME,
    IAM_ROLE,
    K8S_NAMESPACE,
    K8S_RESOURCE,
    ORG_SLUG,
    S3_BUCKET,
    NamingViolation,
    validate_all,
    validate_app_namespace,
    validate_app_subdomain,
)

# ---- single-rule validators ------------------------------------------


@pytest.mark.parametrize("value", ["acme", "acme-corp", "a", "a1b2", "abc-1-def"])
def test_org_slug_accepts_valid(value):
    ORG_SLUG.validate(value)


@pytest.mark.parametrize(
    "value,reason",
    [
        ("", "empty"),
        ("Acme", "uppercase"),
        ("1abc", "leading digit"),
        ("-acme", "leading dash"),
        ("acme-", "trailing dash"),
        ("acme_corp", "underscore"),
        ("acme.corp", "dot"),
        ("a" * 50, "too long"),
    ],
)
def test_org_slug_rejects(value, reason):
    with pytest.raises(NamingViolation):
        ORG_SLUG.validate(value)


def test_violation_message_includes_rule_name_and_value():
    """CI test failures should point at the exact rule + the bad
    string the contributor wrote."""
    with pytest.raises(NamingViolation) as exc:
        ORG_SLUG.validate("Acme_Corp")
    msg = str(exc.value)
    assert "org_slug" in msg
    assert "Acme_Corp" in msg


def test_app_name_max_32_for_k8s_label_budget():
    """K8s labels cap at 63 chars total. App names ride along with
    org slug + delimiter, so app stays at 32 to leave budget for
    org_slug + dashes."""
    APP_NAME.validate("a" * 32)
    with pytest.raises(NamingViolation):
        APP_NAME.validate("a" * 33)


def test_k8s_resource_allows_dots_between_labels():
    """Resource names can be DNS-subdomain-shaped (e.g.
    'cert-manager.io'); namespaces cannot."""
    K8S_RESOURCE.validate("foo.bar")
    K8S_RESOURCE.validate("foo")
    with pytest.raises(NamingViolation):
        K8S_NAMESPACE.validate("foo.bar")


def test_s3_bucket_minimum_length_3():
    """S3 buckets must be 3-63 chars."""
    with pytest.raises(NamingViolation):
        S3_BUCKET.validate("a")
    with pytest.raises(NamingViolation):
        S3_BUCKET.validate("ab")
    S3_BUCKET.validate("abc")


def test_iam_role_max_64():
    IAM_ROLE.validate("r" + "a" * 63)
    with pytest.raises(NamingViolation):
        IAM_ROLE.validate("r" + "a" * 64)


def test_dns_label_max_63():
    DNS_LABEL.validate("a" * 63)
    with pytest.raises(NamingViolation):
        DNS_LABEL.validate("a" * 64)


def test_validate_rejects_non_string():
    with pytest.raises(NamingViolation):
        ORG_SLUG.validate(123)  # type: ignore[arg-type]


# ---- composite validators -------------------------------------------


def test_validate_app_namespace_composes_and_validates():
    ns = validate_app_namespace(org_slug="acme", app_name="api")
    assert ns == "acme-api"


def test_validate_app_namespace_rejects_overlong_composition():
    """Individual parts may be valid; the composition exceeds 63 chars."""
    long_org = "o" + "r" * 38   # 39 chars
    long_app = "a" + "p" * 31   # 32 chars
    # Both pass individually
    ORG_SLUG.validate(long_org)
    APP_NAME.validate(long_app)
    # Composition: 39 + 1 + 32 = 72 chars → too long for namespace
    with pytest.raises(NamingViolation):
        validate_app_namespace(org_slug=long_org, app_name=long_app)


def test_validate_app_subdomain_prod_omits_env_label():
    """Convention: prod gets the bare app label; previews get app-env."""
    fqdn = validate_app_subdomain(
        app_name="api", env_name="prod", base_zone="acme.com"
    )
    assert fqdn == "api.acme.com"


def test_validate_app_subdomain_preview_includes_env():
    fqdn = validate_app_subdomain(
        app_name="api", env_name="pr-42", base_zone="acme.com"
    )
    assert fqdn == "api-pr-42.acme.com"


def test_validate_app_subdomain_rejects_label_overflow():
    """app-env composition must still fit in a DNS label (63 chars)."""
    long_app = "a" * 32
    long_env = "e" * 24
    APP_NAME.validate(long_app)
    ENV_NAME.validate(long_env)
    # 32 + 1 + 24 = 57 → fits
    fqdn = validate_app_subdomain(
        app_name=long_app, env_name=long_env, base_zone="x.com"
    )
    assert fqdn.startswith(long_app + "-" + long_env)


# ---- bulk validator -------------------------------------------------


def test_validate_all_collects_every_violation():
    """CI contract: contributor sees all their naming bugs at once."""
    violations = validate_all([
        (ORG_SLUG, "good-org"),
        (APP_NAME, "Bad_App"),         # bad
        (DNS_LABEL, "ok-label"),
        (S3_BUCKET, "ab"),              # bad (too short)
        (K8S_NAMESPACE, "good"),
    ])
    assert len(violations) == 2
    rule_names = {v.rule_name for v in violations}
    assert rule_names == {"app_name", "s3_bucket"}


def test_validate_all_empty_when_all_pass():
    violations = validate_all([
        (ORG_SLUG, "acme"),
        (APP_NAME, "api"),
    ])
    assert violations == []


# ---- meta -----------------------------------------------------------


def test_all_rules_are_in_registry():
    """Adding a new rule should land in ALL_RULES so CI test
    parametrizations catch it without per-rule edits."""
    rule_names = {r.name for r in ALL_RULES}
    assert "org_slug" in rule_names
    assert "k8s_namespace" in rule_names
    assert "s3_bucket" in rule_names
    assert "iam_role" in rule_names
