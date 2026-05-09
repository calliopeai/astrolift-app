"""Tests for SCIM 2.0 policy + parsing (#78, #91 — RFC 7644 + spec 27 §4.2)."""

from __future__ import annotations

import pytest

from astrolift_identity.scim import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SCHEMA_GROUP,
    SCHEMA_USER,
    SCIM_RATE_LIMIT_PER_MIN,
    GroupRoleMapping,
    ScimError,
    ScimFilter,
    ScimUser,
    ScopeKind,
    filter_matches,
    is_deprovision_request,
    normalize_pagination,
    parse_filter,
    parse_group_payload,
    parse_user_payload,
    role_assignments_for_user,
)

# ---- payload parsing -----------------------------------------------


def test_parse_user_with_emails_array():
    payload = {
        "schemas": [SCHEMA_USER],
        "userName": "alice@acme.com",
        "name": {"givenName": "Alice", "familyName": "Smith"},
        "emails": [
            {"value": "alice@old.com", "primary": False},
            {"value": "alice@acme.com", "primary": True},
        ],
        "active": True,
        "externalId": "okta-123",
    }
    user = parse_user_payload(payload)
    assert user.user_name == "alice@acme.com"
    assert user.email == "alice@acme.com"  # primary picked
    assert user.display_name == "Alice Smith"
    assert user.external_id == "okta-123"


def test_parse_user_falls_back_to_first_email_when_no_primary():
    payload = {
        "userName": "bob",
        "emails": [{"value": "bob@x.com"}],
    }
    assert parse_user_payload(payload).email == "bob@x.com"


def test_parse_user_handles_string_email():
    """Some IdPs send emails as bare strings instead of objects."""
    payload = {
        "userName": "charlie",
        "emails": ["charlie@x.com"],
    }
    assert parse_user_payload(payload).email == "charlie@x.com"


def test_parse_user_displayname_falls_back_to_username():
    payload = {"userName": "dee", "emails": [{"value": "dee@x.com"}]}
    assert parse_user_payload(payload).display_name == "dee"


def test_parse_user_active_defaults_true():
    payload = {"userName": "x", "emails": [{"value": "x@x.com"}]}
    assert parse_user_payload(payload).active is True


def test_parse_user_active_false_respected():
    payload = {
        "userName": "x", "emails": [{"value": "x@x.com"}], "active": False,
    }
    assert parse_user_payload(payload).active is False


def test_parse_user_requires_username():
    with pytest.raises(ValueError, match="user_name"):
        parse_user_payload({"emails": [{"value": "x@x.com"}]})


def test_parse_user_requires_email():
    with pytest.raises(ValueError, match="email"):
        parse_user_payload({"userName": "x"})


def test_parse_user_rejects_non_dict():
    with pytest.raises(ScimError):
        parse_user_payload("not a dict")  # type: ignore[arg-type]


def test_parse_group():
    payload = {
        "schemas": [SCHEMA_GROUP],
        "displayName": "ops-team",
        "members": [
            {"value": "okta-1"},
            {"value": "okta-2"},
        ],
        "externalId": "okta-grp-7",
    }
    group = parse_group_payload(payload)
    assert group.display_name == "ops-team"
    assert group.members_external_ids == ("okta-1", "okta-2")


# ---- filter parsing ------------------------------------------------


def test_filter_eq():
    f = parse_filter('userName eq "alice"')
    assert f == ScimFilter(attribute="userName", operator="eq", value="alice")


def test_filter_co_email_pattern():
    f = parse_filter('email co "@acme.com"')
    assert f.operator == "co"
    assert f.value == "@acme.com"


def test_filter_empty_returns_none():
    """No filter = list everything."""
    assert parse_filter("") is None


def test_filter_unsupported_syntax_raises():
    """Compound filters (and/or) aren't in the minimum required
    set per RFC 7644 §3.4.2.2."""
    with pytest.raises(ScimError, match="unsupported"):
        parse_filter('userName eq "a" and email eq "b"')


def test_filter_matches_eq_userName():
    f = ScimFilter(attribute="userName", operator="eq", value="alice")
    assert filter_matches(filt=f, user=_user("alice")) is True
    assert filter_matches(filt=f, user=_user("bob")) is False


def test_filter_matches_co_email():
    f = ScimFilter(attribute="email", operator="co", value="@acme.com")
    assert filter_matches(filt=f, user=_user("a", email="a@acme.com")) is True
    assert filter_matches(filt=f, user=_user("a", email="a@other.com")) is False


def test_filter_matches_sw_displayname():
    f = ScimFilter(attribute="displayName", operator="sw", value="Alice")
    assert filter_matches(filt=f, user=_user("a", display="Alice Smith")) is True


def test_filter_matches_active_bool():
    f = ScimFilter(attribute="active", operator="eq", value="false")
    inactive = ScimUser(
        user_name="x", email="x@x.com", display_name="X", active=False,
    )
    assert filter_matches(filt=f, user=inactive) is True


def test_filter_unsupported_attribute():
    f = ScimFilter(attribute="phoneNumbers", operator="eq", value="x")
    with pytest.raises(ScimError):
        filter_matches(filt=f, user=_user("a"))


def _user(name: str, email: str = "", display: str = "") -> ScimUser:
    return ScimUser(
        user_name=name,
        email=email or f"{name}@x.com",
        display_name=display or name,
    )


# ---- pagination ----------------------------------------------------


def test_pagination_defaults():
    offset, page = normalize_pagination(start_index=None, count=None)
    assert offset == 0
    assert page == DEFAULT_PAGE_SIZE


def test_pagination_clamps_max():
    """Cap to prevent a runaway client from fetching the whole org."""
    _, page = normalize_pagination(start_index=1, count=10000)
    assert page == MAX_PAGE_SIZE


def test_pagination_converts_1_index_to_offset():
    offset, _ = normalize_pagination(start_index=51, count=50)
    assert offset == 50  # 0-indexed


def test_pagination_clamps_negative():
    offset, page = normalize_pagination(start_index=-5, count=-10)
    assert offset == 0
    assert page == 0


# ---- group -> role mapping -----------------------------------------


def test_mapping_org_scope_id_optional():
    """ORG scope can have None scope_id (defaults to the org)."""
    m = GroupRoleMapping(
        organization_id=1, group_external_id="okta-1",
        role_id=2, scope_kind=ScopeKind.ORG,
    )
    assert m.scope_id is None


def test_mapping_team_scope_requires_id():
    with pytest.raises(ValueError, match="scope_id"):
        GroupRoleMapping(
            organization_id=1, group_external_id="okta-1",
            role_id=2, scope_kind=ScopeKind.TEAM, scope_id=None,
        )


def test_mapping_org_scope_id_must_match_org():
    """ORG scope with a different scope_id is a misconfig."""
    with pytest.raises(ValueError, match="ORG scope"):
        GroupRoleMapping(
            organization_id=1, group_external_id="okta-1",
            role_id=2, scope_kind=ScopeKind.ORG, scope_id=99,
        )


def test_mapping_requires_external_id():
    with pytest.raises(ValueError):
        GroupRoleMapping(
            organization_id=1, group_external_id="",
            role_id=2, scope_kind=ScopeKind.ORG,
        )


def test_role_assignments_match_groups():
    mappings = [
        GroupRoleMapping(
            organization_id=1, group_external_id="okta-platform",
            role_id=10, scope_kind=ScopeKind.ORG,
        ),
        GroupRoleMapping(
            organization_id=1, group_external_id="okta-app-team",
            role_id=11, scope_kind=ScopeKind.PROJECT, scope_id=42,
        ),
        GroupRoleMapping(
            organization_id=1, group_external_id="okta-other",
            role_id=99, scope_kind=ScopeKind.ORG,
        ),
    ]
    out = role_assignments_for_user(
        user_group_external_ids=["okta-platform", "okta-app-team"],
        mappings=mappings,
    )
    role_ids = {m.role_id for m in out}
    assert role_ids == {10, 11}


def test_role_assignments_dedupe_same_role_scope():
    """Two IdP groups granting the same (role, scope) shouldn't
    produce duplicate binding rows."""
    mappings = [
        GroupRoleMapping(
            organization_id=1, group_external_id="g1",
            role_id=10, scope_kind=ScopeKind.ORG,
        ),
        GroupRoleMapping(
            organization_id=1, group_external_id="g2",
            role_id=10, scope_kind=ScopeKind.ORG,
        ),
    ]
    out = role_assignments_for_user(
        user_group_external_ids=["g1", "g2"], mappings=mappings,
    )
    assert len(out) == 1


# ---- deprovisioning ------------------------------------------------


def test_deprovision_active_false():
    assert is_deprovision_request(payload={"active": False}) is True


def test_deprovision_active_true_is_provision():
    assert is_deprovision_request(payload={"active": True}) is False


def test_deprovision_via_patch_operations():
    """SCIM PATCH op shape: replace active=false."""
    payload = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [
            {"op": "replace", "path": "active", "value": False},
        ],
    }
    assert is_deprovision_request(payload=payload) is True


def test_deprovision_other_patch_ops_dont_trigger():
    payload = {
        "Operations": [
            {"op": "replace", "path": "displayName", "value": "Alice S."},
        ],
    }
    assert is_deprovision_request(payload=payload) is False


# ---- locked constants ----------------------------------------------


def test_rate_limit_locked():
    assert SCIM_RATE_LIMIT_PER_MIN == 240
