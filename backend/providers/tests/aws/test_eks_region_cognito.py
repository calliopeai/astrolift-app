"""EKS driver region + Cognito discovery tests (#860 / #859).

Exercises the three driver-level reads that back the operator-facing
pickers:

* ``list_regions`` — ``ec2:DescribeRegions`` with a static fallback on
  API failure (#860). The register dialog's region picker.
* ``list_cognito_user_pools`` — paginated ``cognito-idp:ListUserPools``
  with per-pool DescribeUserPool for the hosted domain + ARN
  composition from the caller's account id (#859).
* ``list_cognito_user_pool_clients`` — paginated
  ``cognito-idp:ListUserPoolClients`` (#859).

All three use injected stub boto3 clients so the tests are
deterministic and never touch AWS — the constructor's
``ec2_client`` / ``sts_client`` / ``cognito_idp_client`` injection
points are the seams.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from aws.cluster_eks import EKSClusterDriver, EKSConfig


def _driver(
    *,
    ec2_client: Any | None = None,
    sts_client: Any | None = None,
    cognito_idp_client: Any | None = None,
    region: str = "us-west-2",
) -> EKSClusterDriver:
    """Build a driver with stubbed AWS clients. ``eks`` is always a
    MagicMock — none of these read paths touch it, but the constructor
    builds a real boto3 client otherwise."""
    return EKSClusterDriver(
        config=EKSConfig(region=region, cluster_name="prod-eks"),
        eks_client=MagicMock(),
        sts_client=sts_client if sts_client is not None else MagicMock(),
        ec2_client=ec2_client if ec2_client is not None else MagicMock(),
        cognito_idp_client=cognito_idp_client,
    )


# ─── list_regions (#860) ──────────────────────────────────────────────


def test_list_regions_live_describe_regions_sorted_and_labeled() -> None:
    """The driver lists the account's enabled regions via
    DescribeRegions, sorts them by slug, and decorates each with the
    friendly label + continent from the static table."""
    ec2 = MagicMock()
    ec2.describe_regions.return_value = {
        "Regions": [
            {"RegionName": "us-west-2"},
            {"RegionName": "eu-central-1"},
            {"RegionName": "us-east-1"},
        ],
    }
    driver = _driver(ec2_client=ec2)

    regions = driver.list_regions()

    # DescribeRegions called with no AllRegions kwarg → enabled-only.
    ec2.describe_regions.assert_called_once_with()
    assert [r.id for r in regions] == ["eu-central-1", "us-east-1", "us-west-2"]
    by_id = {r.id: r for r in regions}
    assert by_id["us-west-2"].label == "US West (Oregon)"
    assert by_id["us-west-2"].continent == "Americas"
    assert by_id["eu-central-1"].label == "Europe (Frankfurt)"
    assert by_id["eu-central-1"].continent == "Europe"


def test_list_regions_unknown_slug_falls_back_to_slug_as_label() -> None:
    """A region absent from the label table still surfaces — the slug
    doubles as the label and the continent is blank. The table is a UX
    nicety, not an allow-list."""
    ec2 = MagicMock()
    ec2.describe_regions.return_value = {"Regions": [{"RegionName": "xx-region-9"}]}
    driver = _driver(ec2_client=ec2)

    [region] = driver.list_regions()

    assert region.id == "xx-region-9"
    assert region.label == "xx-region-9"
    assert region.continent == ""


def test_list_regions_api_failure_falls_back_to_static_list() -> None:
    """When DescribeRegions raises (no creds, throttle, network) the
    driver returns the curated static fallback rather than raising —
    the picker must never be empty (the frontend layers free-entry on
    top, but a populated list is the happy path)."""
    ec2 = MagicMock()
    ec2.describe_regions.side_effect = RuntimeError("AccessDenied")
    driver = _driver(ec2_client=ec2)

    regions = driver.list_regions()

    slugs = {r.id for r in regions}
    # A representative slice of the static fallback set.
    assert "us-east-1" in slugs
    assert "us-west-2" in slugs
    assert "eu-west-1" in slugs
    assert len(regions) >= 10
    # Still labeled from the static table.
    assert next(r for r in regions if r.id == "us-east-1").label == "US East (N. Virginia)"


def test_list_regions_empty_response_falls_back_to_static_list() -> None:
    """An empty Regions list (malformed / unexpected response) is
    treated as a failure and falls back to the static list rather than
    returning an empty picker."""
    ec2 = MagicMock()
    ec2.describe_regions.return_value = {"Regions": []}
    driver = _driver(ec2_client=ec2)

    regions = driver.list_regions()

    assert len(regions) >= 10


# ─── list_cognito_user_pools (#859) ───────────────────────────────────


def _sts_with_account(account_id: str) -> MagicMock:
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": account_id}
    return sts


def test_list_cognito_user_pools_composes_arn_and_reads_domain() -> None:
    """ListUserPools returns only Id + Name; the driver composes the
    ARN from the caller's account id + region + pool id and reads the
    hosted domain from a per-pool DescribeUserPool."""
    cognito = MagicMock()
    cognito.list_user_pools.return_value = {
        "UserPools": [
            {"Id": "us-west-2_aaa", "Name": "customers"},
            {"Id": "us-west-2_bbb", "Name": "staff"},
        ],
    }
    cognito.describe_user_pool.side_effect = lambda UserPoolId: {  # noqa: N803
        "us-west-2_aaa": {"UserPool": {"Domain": "customers-auth"}},
        "us-west-2_bbb": {"UserPool": {}},  # no hosted domain
    }[UserPoolId]
    driver = _driver(
        sts_client=_sts_with_account("111122223333"),
        cognito_idp_client=cognito,
        region="us-west-2",
    )

    pools = driver.list_cognito_user_pools()

    assert [p.pool_id for p in pools] == ["us-west-2_aaa", "us-west-2_bbb"]
    aaa = pools[0]
    assert aaa.pool_arn == "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_aaa"
    assert aaa.name == "customers"
    assert aaa.domain == "customers-auth"
    assert aaa.region == "us-west-2"
    # Pool with no hosted domain surfaces with an empty domain string.
    assert pools[1].domain == ""


def test_list_cognito_user_pools_paginates() -> None:
    """The driver walks every page of ListUserPools via NextToken."""
    cognito = MagicMock()
    cognito.list_user_pools.side_effect = [
        {"UserPools": [{"Id": "p1", "Name": "one"}], "NextToken": "tok"},
        {"UserPools": [{"Id": "p2", "Name": "two"}]},
    ]
    cognito.describe_user_pool.return_value = {"UserPool": {"Domain": ""}}
    driver = _driver(sts_client=_sts_with_account("111122223333"), cognito_idp_client=cognito)

    pools = driver.list_cognito_user_pools()

    assert [p.pool_id for p in pools] == ["p1", "p2"]
    # Second call carried the NextToken from the first response.
    second_call_kwargs = cognito.list_user_pools.call_args_list[1].kwargs
    assert second_call_kwargs.get("NextToken") == "tok"


def test_list_cognito_user_pools_describe_failure_degrades_domain_only() -> None:
    """A failed per-pool DescribeUserPool blanks that pool's domain but
    must NOT drop the pool — the operator can still pick it and type the
    domain manually."""
    cognito = MagicMock()
    cognito.list_user_pools.return_value = {"UserPools": [{"Id": "p1", "Name": "one"}]}
    cognito.describe_user_pool.side_effect = RuntimeError("AccessDenied on DescribeUserPool")
    driver = _driver(sts_client=_sts_with_account("111122223333"), cognito_idp_client=cognito)

    pools = driver.list_cognito_user_pools()

    assert len(pools) == 1
    assert pools[0].pool_id == "p1"
    assert pools[0].domain == ""


def test_list_cognito_user_pools_skips_pools_without_id() -> None:
    """Defensive: a pool entry missing an Id is skipped rather than
    composing a malformed ARN."""
    cognito = MagicMock()
    cognito.list_user_pools.return_value = {
        "UserPools": [{"Name": "no-id"}, {"Id": "p1", "Name": "ok"}],
    }
    cognito.describe_user_pool.return_value = {"UserPool": {"Domain": "d"}}
    driver = _driver(sts_client=_sts_with_account("111122223333"), cognito_idp_client=cognito)

    pools = driver.list_cognito_user_pools()

    assert [p.pool_id for p in pools] == ["p1"]


# ─── list_cognito_user_pool_clients (#859) ────────────────────────────


def test_list_cognito_user_pool_clients_maps_fields() -> None:
    """ListUserPoolClients rows map 1:1 onto the client info shape."""
    cognito = MagicMock()
    cognito.list_user_pool_clients.return_value = {
        "UserPoolClients": [
            {"ClientId": "c1", "ClientName": "web"},
            {"ClientId": "c2", "ClientName": "mobile"},
        ],
    }
    driver = _driver(cognito_idp_client=cognito)

    clients = driver.list_cognito_user_pool_clients("us-west-2_aaa")

    assert [(c.client_id, c.client_name) for c in clients] == [
        ("c1", "web"),
        ("c2", "mobile"),
    ]
    cognito.list_user_pool_clients.assert_called_once()
    assert cognito.list_user_pool_clients.call_args.kwargs["UserPoolId"] == "us-west-2_aaa"


def test_list_cognito_user_pool_clients_paginates() -> None:
    """The driver walks every page of ListUserPoolClients via NextToken."""
    cognito = MagicMock()
    cognito.list_user_pool_clients.side_effect = [
        {"UserPoolClients": [{"ClientId": "c1", "ClientName": "web"}], "NextToken": "n"},
        {"UserPoolClients": [{"ClientId": "c2", "ClientName": "mobile"}]},
    ]
    driver = _driver(cognito_idp_client=cognito)

    clients = driver.list_cognito_user_pool_clients("pool")

    assert [c.client_id for c in clients] == ["c1", "c2"]
    assert cognito.list_user_pool_clients.call_args_list[1].kwargs.get("NextToken") == "n"
