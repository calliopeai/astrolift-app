"""Tests for the REPRODUCE comparison (spec 43 §0.1 step 7).

Step 7 has never run for any cell on any cloud, so these cases are the first
statement of what "end state identical" is going to mean when it does. They are
about the comparison rather than the second run: running the cycle twice proves
repeatability, and the defect the step exists for -- non-idempotent teardown --
lives in the gap between repeatable and idempotent.
"""

from __future__ import annotations

from _cert.harness.fingerprint import Fingerprint, census, compare
from _cert.harness.platform import ManagedServiceObservation, WorkloadObservation
from _cert.orphans.model import Orphan, ScanError, ScanReport


def report(*identifiers: str, scanned: tuple[str, ...] = ("rds_instance", "s3"), errors: tuple[str, ...] = ()):
    return ScanReport(
        cloud="aws",
        campaign="cert2026q3",
        scanned=scanned,
        orphans=tuple(
            Orphan(cloud="aws", service="rds_instance", identifier=name, location="", matched_on="name")
            for name in identifiers
        ),
        errors=tuple(ScanError(service=service, error="AccessDenied") for service in errors),
    )


def fingerprint(
    *,
    services=(("records", "postgres", "rds", "active"),),
    resources=("rds_instance:al-records",),
    clean_resources=(),
    families=("rds_instance", "s3"),
    app_visible=False,
) -> Fingerprint:
    return Fingerprint(
        up_workloads=(("web", 2),),
        up_services=services,
        up_cloud_resources=resources,
        clean_app_visible=app_visible,
        clean_cloud_resources=clean_resources,
        clean_families_read=families,
    )


def test_two_identical_runs_compare_equal():
    assert compare(fingerprint(), fingerprint()) == ()


def test_a_resource_name_that_moved_between_runs_is_the_headline_difference():
    """The non-idempotent-teardown signature, and the reason the comparison
    reaches the cloud at all: the platform API exposes a binding's name, kind,
    variant and status but never ``ManagedService.backend_ref``, the
    provider-side handle. Both runs look identical through the API while one
    provisioned ``…-records`` and the other ``…-records-2``."""
    first = fingerprint(resources=("rds_instance:al-cert-records",))
    second = fingerprint(resources=("rds_instance:al-cert-records-2",))

    (difference,) = compare(first, second)

    assert difference.what == "cloud resources at VERIFY-UP"
    assert "al-cert-records-2" in difference.second
    assert "non-idempotent-teardown signature" in difference.meaning


def test_two_clean_scans_that_read_different_families_are_not_the_same_result():
    """The subtle one. Both runs end with zero leftovers, so a comparison that
    looked only at the leftovers would call them equal -- but the second scan
    could not read S3, so its "clean" covers less ground than the first's."""
    first = fingerprint(families=("rds_instance", "s3"))
    second = fingerprint(families=("rds_instance",))

    (difference,) = compare(first, second)

    assert difference.what == "resource families successfully scanned"
    assert "partly blind" in difference.meaning


def test_residue_in_only_one_run_is_reported_with_which_run_had_it():
    first = fingerprint(clean_resources=())
    second = fingerprint(clean_resources=("s3:al-cert-archive",))

    (difference,) = compare(first, second)

    assert difference.what == "cloud residue after TEARDOWN"
    assert "al-cert-archive" in difference.second
    assert difference.first == "(none)"


def test_a_binding_set_that_changed_between_runs_is_reported_separately():
    """Distinct from a name move: the same manifest booking a different set of
    bindings means provisioning read residue, not that a name was taken."""
    first = fingerprint(services=(("records", "postgres", "rds", "active"),))
    second = fingerprint(
        services=(
            ("records", "postgres", "rds", "active"),
            ("records", "postgres", "rds", "degraded"),
        )
    )

    (difference,) = compare(first, second)

    assert difference.what == "managed services at VERIFY-UP"


def test_an_app_that_survived_one_teardown_and_not_the_other_is_a_difference():
    assert compare(fingerprint(app_visible=False), fingerprint(app_visible=True))


# ---- what is deliberately not compared ----------------------------------------


def test_volatile_platform_detail_is_projected_out_rather_than_filtered():
    """Two correct runs differ in transient status text, deployment ids and
    endpoint addresses by construction. Comparing those would make REPRODUCE
    permanently red, which is indistinguishable from not running it.

    The exclusion is a projection over named fields, so a field added to an
    observation later stays out of the comparison until somebody puts it in on
    purpose."""
    settled = [ManagedServiceObservation("records", "postgres", "rds", "active", status_error="")]
    recovered = [ManagedServiceObservation("records", "postgres", "rds", "active", status_error="retried after a 429")]
    workloads = [WorkloadObservation("web", desired_replicas=2, ready_replicas=2, images=("app:main-a",))]
    rolled = [WorkloadObservation("web", desired_replicas=2, ready_replicas=2, images=("app:main-b",))]

    assert Fingerprint.up_projection(workloads, settled, None) == Fingerprint.up_projection(rolled, recovered, None)


def test_the_census_ignores_the_order_a_cloud_listed_things_in():
    """No list API promises an order, and an ordering difference is not an
    end-state difference."""
    assert census(report("b", "a")) == census(report("a", "b"))


def test_an_unread_family_is_excluded_from_the_families_a_run_claims_to_have_read():
    """``scanned`` is what the scan asked for; a family that errored was asked
    and not answered. Counting it as read would let a denied API look like
    coverage."""
    projection = Fingerprint.clean_projection(False, report(scanned=("rds_instance", "s3"), errors=("s3",)))

    assert projection["clean_families_read"] == ("rds_instance",)
