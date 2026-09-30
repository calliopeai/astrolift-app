"""Custom TLS/account/callback proof uses read-only AWS clients."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from aws.cluster_eks import EKSClusterDriver, EKSConfig

ACCOUNT = "123456789012"
REGION = "us-west-2"
CERT = f"arn:aws:acm:{REGION}:{ACCOUNT}:certificate/custom"
LB = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/edge/123"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{REGION}_abc"


@pytest.fixture
def setup():
    now = datetime.now(UTC)
    sts, acm, cognito, elb = (MagicMock() for _ in range(4))
    sts.get_caller_identity.return_value = {"Account": ACCOUNT}
    acm.describe_certificate.return_value = {
        "Certificate": {
            "Status": "ISSUED",
            "DomainName": "customer.example",
            "NotBefore": now - timedelta(days=1),
            "NotAfter": now + timedelta(days=30),
        }
    }
    lb_page = {
        "LoadBalancers": [
            {
                "DNSName": "edge.elb.amazonaws.com",
                "LoadBalancerArn": LB,
                "Type": "application",
                "Scheme": "internet-facing",
                "State": {"Code": "active"},
            }
        ]
    }
    listener_page = {"Listeners": [{"Protocol": "HTTPS", "Port": 443, "Certificates": [{"CertificateArn": CERT}]}]}
    paginators = {"describe_load_balancers": MagicMock(), "describe_listeners": MagicMock()}
    paginators["describe_load_balancers"].paginate.return_value = [lb_page]
    paginators["describe_listeners"].paginate.return_value = [listener_page]
    elb.get_paginator.side_effect = lambda operation: paginators[operation]
    elb.describe_tags.return_value = {
        "TagDescriptions": [{"Tags": [{"Key": "ingress.k8s.aws/stack", "Value": "verified-edge"}]}]
    }
    cognito.describe_user_pool.return_value = {
        "UserPool": {"Arn": f"arn:aws:cognito-idp:{REGION}:{ACCOUNT}:userpool/{REGION}_abc"}
    }
    cognito.describe_user_pool_client.return_value = {
        "UserPoolClient": {
            "UserPoolId": f"{REGION}_abc",
            "ClientId": "client",
            "CallbackURLs": ["https://customer.example/oauth2/callback"],
            "AllowedOAuthFlowsUserPoolClient": True,
            "AllowedOAuthFlows": ["code"],
            "ClientSecret": "never-log-this",
        }
    }
    driver = EKSClusterDriver(
        config=EKSConfig(region=REGION, cluster_name="edge"),
        eks_client=MagicMock(),
        sts_client=sts,
        ec2_client=MagicMock(),
        acm_client=acm,
        cognito_idp_client=cognito,
        elbv2_client=elb,
    )
    driver.get_manifest = MagicMock(
        return_value={
            "metadata": {"annotations": {"alb.ingress.kubernetes.io/group.name": "verified-edge"}},
            "status": {"loadBalancer": {"ingress": [{"hostname": "edge.elb.amazonaws.com"}]}},
        }
    )
    return driver, sts, acm, cognito, elb


def validate(driver, **overrides):
    args = dict(
        hostname="customer.example",
        certificate_arn=CERT,
        alb_group="verified-edge",
        discovery_url=ISSUER,
        client_id="client",
        gated=True,
    )
    driver.validate_edge_custom_domain("edge", **(args | overrides))


def test_matching_certified_front_and_exact_callback_only_perform_reads(setup, caplog):
    driver, sts, acm, cognito, elb = setup
    validate(driver)
    assert acm.describe_certificate.call_args.kwargs == {"CertificateArn": CERT}
    assert cognito.describe_user_pool_client.call_args.kwargs == {"UserPoolId": f"{REGION}_abc", "ClientId": "client"}
    assert "never-log-this" not in caplog.text
    for client in (sts, acm, cognito, elb):
        assert all(call[0].startswith(("get_", "describe_")) for call in client.mock_calls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("certificate_arn", CERT.replace(ACCOUNT, "999999999999")),
        ("certificate_arn", CERT.replace(REGION, "eu-west-1")),
        ("certificate_arn", "byo-pem"),
        ("alb_group", "other"),
        ("discovery_url", "https://auth0.example"),
        ("discovery_url", ISSUER + "attacker"),
    ],
)
def test_cross_account_region_front_or_unverifiable_oidc_refuses(setup, field, value):
    with pytest.raises(ValueError):
        validate(setup[0], **{field: value})


@pytest.mark.parametrize(
    "change",
    [
        "revoked",
        "wrong-host",
        "expired",
        "not-yet-valid",
        "callback",
        "no-code",
        "wrong-client",
        "wrong-pool-account",
        "wrong-lb-account",
        "wrong-lb-group",
        "no-lb",
        "not-alb",
        "not-active",
        "internal-front",
        "http-only",
        "wrong-port",
        "foreign-listener-cert",
    ],
)
def test_stale_or_mismatched_provider_metadata_refuses(setup, change):
    driver, _, acm, cognito, elb = setup
    cert = acm.describe_certificate.return_value["Certificate"]
    client = cognito.describe_user_pool_client.return_value["UserPoolClient"]
    if change == "revoked":
        cert["Status"] = "REVOKED"
    if change == "wrong-host":
        cert["DomainName"] = "sibling.example"
    if change == "expired":
        cert["NotAfter"] = datetime.now(UTC) - timedelta(days=1)
    if change == "not-yet-valid":
        cert["NotBefore"] = datetime.now(UTC) + timedelta(days=1)
    if change == "callback":
        client["CallbackURLs"] = ["https://auth.apps.example.net/oauth2/callback"]
    if change == "no-code":
        client["AllowedOAuthFlows"] = ["implicit"]
    if change == "wrong-client":
        client["ClientId"] = "other"
    if change == "wrong-pool-account":
        cognito.describe_user_pool.return_value["UserPool"]["Arn"] = (
            f"arn:aws:cognito-idp:{REGION}:999999999999:userpool/{REGION}_abc"
        )
    if change == "wrong-lb-account":
        elb.get_paginator("describe_load_balancers").paginate.return_value[0]["LoadBalancers"][0]["LoadBalancerArn"] = (
            LB.replace(ACCOUNT, "999999999999")
        )
    if change == "wrong-lb-group":
        elb.describe_tags.return_value["TagDescriptions"][0]["Tags"][0]["Value"] = "sibling"
    if change == "no-lb":
        driver.get_manifest.return_value["status"] = {}
    lb = elb.get_paginator("describe_load_balancers").paginate.return_value[0]["LoadBalancers"][0]
    listener = elb.get_paginator("describe_listeners").paginate.return_value[0]["Listeners"][0]
    if change == "not-alb":
        lb["Type"] = "network"
    if change == "not-active":
        lb["State"]["Code"] = "provisioning"
    if change == "internal-front":
        lb["Scheme"] = "internal"
    if change == "http-only":
        listener["Protocol"] = "HTTP"
    if change == "wrong-port":
        listener["Port"] = 8443
    if change == "foreign-listener-cert":
        listener["Certificates"][0]["CertificateArn"] = CERT.replace(ACCOUNT, "999999999999")
    with pytest.raises(ValueError):
        validate(driver)


def test_public_domain_does_not_require_or_call_cognito(setup):
    driver, _, _, cognito, _ = setup
    validate(driver, gated=False, discovery_url="", client_id="")
    assert cognito.mock_calls == []


def test_declared_cluster_account_mismatch_refuses_before_certificate_reads(setup):
    from _sdk.cloud_credentials import CloudCredential

    driver, _, acm, _, _ = setup
    driver._config = EKSConfig(
        region=REGION, cluster_name="edge", credential=CloudCredential(cloud="aws", declared_account="999999999999")
    )
    with pytest.raises(ValueError, match="declared account"):
        validate(driver)
    assert acm.mock_calls == []
