"""Per-app access in the Envoy edge's one shared policy (#2132).

The rule shape and the denied page were proven on a kind cluster with Envoy
Gateway 1.9.1 on 2026-09-27: an Allow on ``:authority`` + the groups claim,
then a Deny on the same hosts, keeps single sign-on and turns others away;
``source: Local`` replaces only Envoy's own 403.
"""

from __future__ import annotations

from k8s_native.edge_gateway import (
    ACCESS_DENIED_POLICY_NAME,
    edge_authorization,
    edge_post_install_manifests,
    edge_security_policy,
    groups_claim,
)

COGNITO = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration",
    "client_id": "central",
    "client_secret": "s",
    "auth_proxy_host": "auth.apps.example.net",
}
RULE = {
    "name": "veruus-demo-prod",
    "hosts": ["veruus-demo.apps.example.net"],
    "groups": ["veruus"],
    "users": ["Guest@Example.com"],
}


def test_the_groups_claim_follows_the_provider():
    assert groups_claim(COGNITO) == "cognito:groups"
    assert groups_claim({"discovery_url": "https://dex.example.net/dex"}) == "groups"
    assert groups_claim({**COGNITO, "groups_claim": "roles"}) == "roles"


def test_a_restricted_app_gets_allows_then_a_deny_on_its_hosts():
    authz = edge_authorization([RULE], COGNITO)

    assert authz["defaultAction"] == "Allow"
    rules = authz["rules"]
    assert [r["action"] for r in rules] == ["Allow", "Allow", "Deny"]
    on_host = [{"name": ":authority", "values": ["veruus-demo.apps.example.net"]}]
    assert all(r["principal"]["headers"] == on_host for r in rules)
    groups, users = rules[0]["principal"]["jwt"], rules[1]["principal"]["jwt"]
    assert groups == {
        "provider": "idp",
        "claims": [{"name": "cognito:groups", "valueType": "StringArray", "values": ["veruus"]}],
    }
    # Emails compare case-insensitively.
    assert users["claims"] == [{"name": "email", "values": ["guest@example.com"]}]


def test_an_app_with_no_grant_or_no_hosts_adds_nothing():
    assert edge_authorization([{**RULE, "groups": [], "users": []}], COGNITO) is None
    assert edge_authorization([{**RULE, "hosts": []}], COGNITO) is None
    assert edge_authorization([], COGNITO) is None


def test_the_shared_policy_carries_the_rules_and_stays_one_policy():
    plain = edge_security_policy(COGNITO)
    with_rules = edge_security_policy(COGNITO, [RULE])

    assert "authorization" not in plain["spec"]
    assert with_rules["spec"]["authorization"]["rules"]
    assert with_rules["metadata"]["name"] == plain["metadata"]["name"]
    assert with_rules["spec"]["targetSelectors"] == plain["spec"]["targetSelectors"]


def test_the_denied_page_replaces_only_envoys_own_403():
    manifests = edge_post_install_manifests(COGNITO, access_rules=[RULE])
    page = next(m for m in manifests if m["metadata"]["name"] == ACCESS_DENIED_POLICY_NAME)

    override = page["spec"]["responseOverride"][0]
    assert page["kind"] == "BackendTrafficPolicy"
    assert override["source"] == "Local"
    assert override["match"] == {"statusCodes": [{"type": "Value", "value": 403}]}
    assert "%REQ(:AUTHORITY)%" in override["response"]["body"]["inline"]
