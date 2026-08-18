"""Tests for the GCP orphan scanner (spec 43 §3.2, §6).

Every case plants something in a fake inventory and asks whether the scan finds
it. The two that matter most are the ones about *not* reporting: a scan that
cries wolf gets muted, and a muted scan is the same as no scan.
"""

from __future__ import annotations

import pytest

from _cert.campaign import Campaign
from _cert.orphans import CloudResource, OrphansFound
from _cert.orphans import gcp as gcp_orphans

CAMPAIGN = Campaign("cert2026q3")

EMPTY: dict[str, list[CloudResource]] = {}


class FakeGcpInventory:
    """Every family returns what the test planted, or nothing.

    ``fails`` makes one family raise, which is how a denied API is modelled.
    """

    def __init__(self, planted: dict[str, list[CloudResource]] | None = None, fails: dict[str, str] | None = None):
        self._planted = planted or {}
        self._fails = fails or {}

    def _family(self, name: str) -> list[CloudResource]:
        if name in self._fails:
            raise PermissionError(self._fails[name])
        return self._planted.get(name, [])

    def cloud_sql_instances(self):
        return self._family("cloud_sql")

    def memorystore_instances(self):
        return self._family("memorystore")

    def pubsub_topics(self):
        return self._family("pubsub_topic")

    def pubsub_subscriptions(self):
        return self._family("pubsub_subscription")

    def storage_buckets(self):
        return self._family("gcs")

    def service_accounts(self):
        return self._family("iam_service_account")

    def project_iam_bindings(self):
        return self._family("iam_project_binding")


def test_a_clean_project_scans_clean_and_says_nothing():
    report = gcp_orphans.scan(FakeGcpInventory(), CAMPAIGN)

    assert report.is_clean
    assert report.orphans == ()
    report.raise_if_dirty()


def test_a_surviving_cloud_sql_instance_is_found_by_its_label():
    """The RDS-equivalent leak, and the most expensive one: an instance left
    running bills by the hour until someone reads a report."""
    inventory = FakeGcpInventory(
        {
            "cloud_sql": [
                CloudResource(
                    identifier="astrolift-conflict-cert2026q3-happy-gcp-records",
                    location="us-central1",
                    tags={"astrolift-app": "cert2026q3-happy-gcp", "astrolift-environment": "cert"},
                )
            ]
        }
    )

    report = gcp_orphans.scan(inventory, CAMPAIGN)

    assert not report.is_clean
    assert [o.service for o in report.orphans] == ["cloud_sql"]
    assert report.orphans[0].matched_on == "tag astrolift-app=cert2026q3-happy-gcp"


def test_a_bucket_tagged_with_the_gcs_spelling_is_found():
    """GCS writes ``astrolift-io-app``, not the ``astrolift-app`` every other
    GCP driver writes. A scanner that reads one spelling misses every bucket."""
    inventory = FakeGcpInventory(
        {"gcs": [CloudResource(identifier="al-cert-archive", tags={"astrolift-io-app": "cert2026q3-happy-gcp"})]}
    )

    report = gcp_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == ["gcs"]


def test_a_subscription_left_behind_by_a_deleted_topic_is_found():
    """Subscriptions outlive their topics, keep accruing backlog, and are
    invisible to anyone listing topics."""
    inventory = FakeGcpInventory(
        {
            "pubsub_subscription": [
                CloudResource(
                    identifier="projects/p/subscriptions/astrolift-cert2026q3-happy-gcp-jobs",
                    tags={},
                )
            ]
        }
    )

    report = gcp_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == ["pubsub_subscription"]
    assert report.orphans[0].matched_on.startswith("name ")


def test_iam_residue_is_found_by_name_because_it_has_no_labels():
    """GCP service accounts support no labels at all, and a project IAM binding
    is a role and a member string with no metadata. The deterministic name is
    the only handle, which is why campaign apps carry the slug in their name."""
    inventory = FakeGcpInventory(
        {
            "iam_service_account": [CloudResource(identifier="cert2026q3-happy-gcp@proj.iam.gserviceaccount.com")],
            "iam_project_binding": [
                CloudResource(
                    identifier=(
                        "roles/storage.objectAdmin -> serviceAccount:cert2026q3-happy-gcp@proj.iam.gserviceaccount.com"
                    )
                )
            ],
        }
    )

    report = gcp_orphans.scan(inventory, CAMPAIGN)

    assert sorted(o.service for o in report.orphans) == ["iam_project_binding", "iam_service_account"]


def test_another_tenants_resources_are_left_alone():
    """The scan runs against a shared project. Reporting a customer's database
    as campaign residue is how an orphan scan gets someone's data deleted."""
    inventory = FakeGcpInventory(
        {
            "cloud_sql": [
                CloudResource(
                    identifier="astrolift-conflict-checkout-production-pg",
                    tags={"astrolift-app": "checkout", "astrolift-environment": "production"},
                ),
                CloudResource(identifier="legacy-reporting-db", tags={}),
            ]
        }
    )

    report = gcp_orphans.scan(inventory, CAMPAIGN)

    assert report.is_clean


def test_a_family_that_cannot_be_read_is_not_reported_as_clean():
    """A denied API is an unknown result. Swallowing it is precisely how an
    orphan scan comes to lie."""
    inventory = FakeGcpInventory(fails={"memorystore": "caller lacks redis.instances.list"})

    report = gcp_orphans.scan(inventory, CAMPAIGN)

    assert not report.is_clean
    assert report.orphans == ()
    assert [e.service for e in report.errors] == ["memorystore"]

    with pytest.raises(OrphansFound, match="unread family is an unknown result"):
        report.raise_if_dirty()


def test_one_denied_family_does_not_hide_the_others():
    """Aborting the scan on the first failure is the tempting implementation and
    the wrong one: the leftover in the family that *was* readable still costs
    money."""
    inventory = FakeGcpInventory(
        planted={"gcs": [CloudResource(identifier="al-cert2026q3-archive")]},
        fails={"cloud_sql": "caller lacks cloudsql.instances.list"},
    )

    report = gcp_orphans.scan(inventory, CAMPAIGN)

    assert [o.service for o in report.orphans] == ["gcs"]
    assert [e.service for e in report.errors] == ["cloud_sql"]


def test_the_failure_names_every_leftover_rather_than_counting_them():
    """A count is read once, by the person who already believes teardown
    worked. The message has to carry the identifiers a human acts on."""
    inventory = FakeGcpInventory(
        {
            "cloud_sql": [CloudResource(identifier="cert2026q3-records", location="us-central1")],
            "gcs": [CloudResource(identifier="cert2026q3-archive")],
        }
    )

    report = gcp_orphans.scan(inventory, CAMPAIGN)
    with pytest.raises(OrphansFound) as excinfo:
        report.raise_if_dirty()

    message = str(excinfo.value)
    assert "cert2026q3-records" in message
    assert "us-central1" in message
    assert "cert2026q3-archive" in message
    assert "defect in a deprovision path" in message


def test_every_family_the_spec_names_is_actually_queried():
    """Cloud SQL, Memorystore, Pub/Sub, GCS and IAM are the GCP equivalents of
    the AWS scan's RDS, ElastiCache, SQS, S3 and IAM. A family quietly dropped
    from the list makes the scan narrower without making it noisier."""
    report = gcp_orphans.scan(FakeGcpInventory(), CAMPAIGN)

    assert set(report.scanned) == {
        "cloud_sql",
        "memorystore",
        "pubsub_topic",
        "pubsub_subscription",
        "gcs",
        "iam_service_account",
        "iam_project_binding",
    }
