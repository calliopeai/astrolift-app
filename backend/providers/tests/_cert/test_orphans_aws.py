"""Tests for the AWS orphan scanner (spec 43 §3.2, §6).

Every case plants something in a fake inventory and asks whether the scan finds
it. AWS is the cloud epic #983 closed against by hand, so the cases here lean on
the residue that pass actually produced: an instance nobody deleted, a cluster
whose members were removed while the cluster kept billing, and IAM that carries
no tag to be found by.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from _cert.campaign import Campaign
from _cert.orphans import CloudResource, OrphansFound
from _cert.orphans import aws as aws_orphans

CAMPAIGN = Campaign("cert2026q3")

FAMILIES = (
    "rds_instance",
    "rds_cluster",
    "elasticache_cluster",
    "elasticache_replication_group",
    "elasticache_serverless",
    "sqs",
    "sns",
    "s3",
    "iam_role",
    "iam_policy",
)


class FakeAwsInventory:
    """Every family returns what the test planted, or nothing.

    ``fails`` makes one family raise, which is how a denied or throttled API is
    modelled.
    """

    def __init__(
        self,
        planted: dict[str, list[CloudResource]] | None = None,
        fails: dict[str, str] | None = None,
    ):
        self._planted = planted or {}
        self._fails = fails or {}

    def _family(self, name: str) -> list[CloudResource]:
        if name in self._fails:
            raise PermissionError(self._fails[name])
        return self._planted.get(name, [])

    def rds_instances(self):
        return self._family("rds_instance")

    def rds_clusters(self):
        return self._family("rds_cluster")

    def elasticache_clusters(self):
        return self._family("elasticache_cluster")

    def elasticache_replication_groups(self):
        return self._family("elasticache_replication_group")

    def elasticache_serverless_caches(self):
        return self._family("elasticache_serverless")

    def sqs_queues(self):
        return self._family("sqs")

    def sns_topics(self):
        return self._family("sns")

    def s3_buckets(self):
        return self._family("s3")

    def iam_roles(self):
        return self._family("iam_role")

    def iam_policies(self):
        return self._family("iam_policy")


def test_a_clean_account_scans_clean_and_says_nothing():
    report = aws_orphans.scan(FakeAwsInventory(), CAMPAIGN)

    assert report.is_clean
    assert report.orphans == ()
    report.raise_if_dirty()


def test_a_surviving_rds_instance_is_found_by_its_app_tag():
    """The most expensive AWS leak there is: a db.t3 left running bills by the
    hour until somebody reads a console."""
    inventory = FakeAwsInventory(
        {
            "rds_instance": [
                CloudResource(
                    identifier="astrolift-conflict-cert2026q3-happy-aws-records",
                    location="us-west-2a",
                    tags={
                        "astrolift.io/app": "cert2026q3-happy-aws",
                        "astrolift.io/environment": "production",
                        "astrolift.io/managed-by": "platform",
                    },
                )
            ]
        }
    )

    report = aws_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == ["rds_instance"]
    assert report.orphans[0].matched_on == "tag astrolift.io/app=cert2026q3-happy-aws"
    assert report.orphans[0].location == "us-west-2a"


def test_a_cluster_whose_instances_are_gone_is_still_found():
    """``describe_db_instances`` does not return clusters. An Aurora, DocumentDB
    or Neptune cluster with no members left is invisible to an instance-only
    scan and still bills for storage and backups."""
    inventory = FakeAwsInventory(
        {
            "rds_cluster": [
                CloudResource(
                    identifier="astrolift-conflict-cert2026q3-graph-aws",
                    tags={"astrolift.io/app": "cert2026q3-graph-aws"},
                )
            ]
        }
    )

    report = aws_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == ["rds_cluster"]


@pytest.mark.parametrize(
    "family",
    ["elasticache_cluster", "elasticache_replication_group", "elasticache_serverless"],
)
def test_each_elasticache_api_is_scanned_separately(family):
    """Three drivers, three creates, three describes: memcached makes a cache
    cluster, redis makes a replication group, and the serverless variant makes
    neither. Reading one of the three and calling ElastiCache scanned is the
    exact shape of a scan that lies."""
    inventory = FakeAwsInventory(
        {family: [CloudResource(identifier="al-cache", tags={"astrolift.io/app": "cert2026q3-redis-aws"})]}
    )

    report = aws_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == [family]


def test_a_queue_is_reported_by_url_so_the_finding_is_actionable():
    """A bare queue name is not enough to delete a queue. The URL is, and it
    still carries the name the campaign matches on."""
    url = "https://sqs.us-west-2.amazonaws.com/123456789012/astrolift-conflict-cert2026q3-happy-aws-jobs"
    inventory = FakeAwsInventory({"sqs": [CloudResource(identifier=url, location="us-west-2")]})

    report = aws_orphans.scan(inventory, CAMPAIGN)

    assert [o.identifier for o in report.orphans] == [url]
    assert report.orphans[0].matched_on.startswith("name ")


def test_an_sns_topic_carrying_the_campaign_operator_tag_is_found():
    """The campaign tag spelling on AWS is ``astrolift.io/extra/campaign``. It
    does not reach a resource today -- the provision path drops operator tags --
    so this pins the read side against the day it does."""
    inventory = FakeAwsInventory(
        {
            "sns": [
                CloudResource(
                    identifier="arn:aws:sns:us-west-2:123456789012:al-alerts",
                    tags={"astrolift.io/extra/campaign": "cert2026q3"},
                )
            ]
        }
    )

    report = aws_orphans.scan(inventory, CAMPAIGN)

    assert report.orphans[0].matched_on == "tag astrolift.io/extra/campaign=cert2026q3"


def test_iam_residue_is_found_by_name_because_roles_carry_no_app_tag():
    """Every role the platform creates is stamped with ``managed-by`` and
    nothing else, so the deterministic name is the only handle. A role left
    behind after its workload is gone is the "no dangling IAM/role/grant" clause
    of VERIFY-CLEAN."""
    inventory = FakeAwsInventory(
        {
            "iam_role": [
                CloudResource(
                    identifier="astrolift-conflict-cert2026q3-happy-aws",
                    tags={"astrolift.io/managed-by": "platform"},
                )
            ],
            "iam_policy": [CloudResource(identifier="cert2026q3-happy-aws-objects")],
        }
    )

    report = aws_orphans.scan(inventory, CAMPAIGN)

    assert sorted(o.service for o in report.orphans) == ["iam_policy", "iam_role"]
    assert all(o.matched_on.startswith("name ") for o in report.orphans)


def test_a_truncated_role_name_is_a_known_blind_spot():
    """``aws/_naming.py::iam_role_name`` truncates at 63 characters and appends
    a digest, so a long org-and-app prefix can push the campaign slug off the
    end. The role carries no app tag either, so nothing finds it.

    Pinned as a test rather than left in prose: this is the one place the AWS
    scan is knowingly blind, and it should fail loudly if someone later assumes
    otherwise."""
    from aws._naming import iam_role_name

    truncated = iam_role_name("astrolift", "a-very-long-customer-organization-slug", CAMPAIGN.slug + "-happy-aws")
    assert CAMPAIGN.slug not in truncated

    inventory = FakeAwsInventory(
        {"iam_role": [CloudResource(identifier=truncated, tags={"astrolift.io/managed-by": "platform"})]}
    )

    assert aws_orphans.scan(inventory, CAMPAIGN).is_clean


def test_another_tenants_resources_are_left_alone():
    """The campaign account is shared. Reporting a customer's database as
    campaign residue is how an orphan scan gets someone's data deleted."""
    inventory = FakeAwsInventory(
        {
            "rds_instance": [
                CloudResource(
                    identifier="astrolift-conflict-checkout-production-pg",
                    tags={"astrolift.io/app": "checkout", "astrolift.io/environment": "production"},
                ),
                CloudResource(identifier="legacy-reporting-db", tags={}),
            ],
            "s3": [CloudResource(identifier="conflict-terraform-state", tags={})],
            "iam_role": [CloudResource(identifier="OrganizationAccountAccessRole", tags={})],
        }
    )

    report = aws_orphans.scan(inventory, CAMPAIGN)

    assert report.is_clean


def test_a_family_that_cannot_be_read_is_not_reported_as_clean():
    """A denied or throttled API is an unknown result. Swallowing it is
    precisely how an orphan scan comes to lie."""
    inventory = FakeAwsInventory(fails={"iam_role": "User is not authorized to perform: iam:ListRoles"})

    report = aws_orphans.scan(inventory, CAMPAIGN)

    assert not report.is_clean
    assert report.orphans == ()
    assert [e.service for e in report.errors] == ["iam_role"]

    with pytest.raises(OrphansFound, match="unread family is an unknown result"):
        report.raise_if_dirty()


def test_one_denied_family_does_not_hide_the_others():
    """Aborting the scan on the first failure is the tempting implementation and
    the wrong one: the leftover in the family that *was* readable still costs
    money."""
    inventory = FakeAwsInventory(
        planted={"s3": [CloudResource(identifier="al-cert2026q3-archive")]},
        fails={"rds_instance": "Throttling: Rate exceeded"},
    )

    report = aws_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == ["s3"]
    assert [e.service for e in report.errors] == ["rds_instance"]


def test_the_failure_names_every_leftover_rather_than_counting_them():
    """A count is read once, by the person who already believes teardown
    worked. The message has to carry the identifiers a human acts on."""
    inventory = FakeAwsInventory(
        {
            "rds_instance": [CloudResource(identifier="cert2026q3-records", location="us-west-2a")],
            "s3": [CloudResource(identifier="cert2026q3-archive", location="us-west-2")],
        }
    )

    report = aws_orphans.scan(inventory, CAMPAIGN)
    with pytest.raises(OrphansFound) as excinfo:
        report.raise_if_dirty()

    message = str(excinfo.value)
    assert "cert2026q3-records" in message
    assert "us-west-2a" in message
    assert "cert2026q3-archive" in message
    assert "defect in a deprovision path" in message


def test_every_family_the_spec_names_is_actually_queried():
    """RDS, ElastiCache, SQS, SNS, S3 and IAM, split wherever one API call
    cannot see the whole family. A family quietly dropped from the list makes
    the scan narrower without making it noisier."""
    report = aws_orphans.scan(FakeAwsInventory(), CAMPAIGN)

    assert set(report.scanned) == set(FAMILIES)


# ---- tag normalization -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([{"Key": "astrolift.io/app", "Value": "cert2026q3-x"}], {"astrolift.io/app": "cert2026q3-x"}),
        ([{"key": "astrolift.io/app", "value": "cert2026q3-x"}], {"astrolift.io/app": "cert2026q3-x"}),
        ({"astrolift.io/app": "cert2026q3-x"}, {"astrolift.io/app": "cert2026q3-x"}),
        (None, {}),
        ([], {}),
    ],
)
def test_every_tag_shape_aws_returns_is_normalized(raw, expected):
    """AWS hands back three different tag shapes across the six services this
    scans -- SQS alone returns a dict. A normalizer that knows one of them turns
    a whole family into a silent pass."""
    assert aws_orphans.tag_mapping(raw) == expected


def test_a_tag_read_that_is_absent_is_empty_but_a_tag_read_that_is_denied_raises():
    """An untagged S3 bucket is normal and still matched by name; a denied
    ``GetBucketTagging`` is a permissions hole. Collapsing the second into the
    first is how a scan reports clean on an account it could not read."""

    class Denied(Exception):
        """Shaped like a botocore ``ClientError``, which is the only part of it
        the scanner reads."""

        response: ClassVar[dict] = {"Error": {"Code": "AccessDenied"}}

    class NoTags(Exception):
        response: ClassVar[dict] = {"Error": {"Code": "NoSuchTagSet"}}

    def denied():
        raise Denied

    def untagged():
        raise NoTags

    assert aws_orphans._tags_unless_absent(untagged, ("NoSuchTagSet",)) == {}
    with pytest.raises(Denied):
        aws_orphans._tags_unless_absent(denied, ("NoSuchTagSet",))
