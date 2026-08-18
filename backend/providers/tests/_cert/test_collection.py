"""Tests for the certification manifest collection (spec 43 §3.1).

The collection is data, so the tests are about the properties that make it
usable as evidence: it parses, it covers the grid or says why not, every cell
carries the campaign identity the orphan scanners look for, and the committed
tree is what the ledger says it is.
"""

from __future__ import annotations

import tomllib

import pytest
from astrolift_manifest.parser import parse_raw

from _cert import collection
from _cert.campaign import CAMPAIGN_TAG_KEY
from _sdk.coverage import CLOUDS, OPT_IN_TIER

NEGATIVE_CASES = {
    "foreign-binding-collision",
    "untagged-teardown",
    "keyless-binding-no-role",
    "teardown-mid-provision",
}


def _manifests() -> list[tuple[str, str]]:
    """Every committed manifest as (relative path, text), negatives included."""
    root = collection.MANIFEST_ROOT
    return [(str(p.relative_to(root)), p.read_text(encoding="utf-8")) for p in sorted(root.rglob("*.toml"))]


def _negative_manifests() -> list[tuple[str, dict]]:
    return [(name, tomllib.loads(text)) for name, text in _manifests() if name.startswith("negative/")]


# ---- the grid is covered, or the hole is declared ----------------------------


def test_every_default_tier_cell_has_a_manifest_or_a_declared_reason():
    """The guard that keeps the collection honest as the catalogue grows: a kind
    added to the matrix shows up here as a missing manifest instead of quietly
    sitting outside the grid."""
    missing = [
        (kind, cloud)
        for kind in collection.default_tier_kinds()
        for cloud in CLOUDS
        if cloud not in collection.VARIANTS.get(kind, {}) and (kind, cloud) not in collection.NOT_EXPRESSIBLE
    ]

    assert not missing, "default-tier cells with neither a manifest nor an entry in NOT_EXPRESSIBLE: " + ", ".join(
        f"{kind}/{cloud}" for kind, cloud in sorted(missing)
    )


def test_no_cell_is_excused_that_the_matrix_says_is_executable():
    """A stale excuse is worse than no manifest: it reads as a known gap long
    after the variant shipped."""
    stale = [
        (kind, cloud) for (kind, cloud) in collection.NOT_EXPRESSIBLE if collection.executable_variants(kind, cloud)
    ]

    assert not stale, "NOT_EXPRESSIBLE claims a hole the availability matrix does not have: " + ", ".join(
        f"{kind}/{cloud}" for kind, cloud in sorted(stale)
    )


def test_every_chosen_variant_is_executable_on_its_cloud():
    """A manifest naming a planned variant fails at driver lookup, which teaches
    nobody anything about the cloud."""
    wrong = [
        f"{kind}/{cloud}:{variant}"
        for kind, per_cloud in collection.VARIANTS.items()
        for cloud, variant in per_cloud.items()
        if variant not in collection.executable_variants(kind, cloud)
    ]

    assert not wrong, "variants that are not executable per the availability matrix: " + ", ".join(sorted(wrong))


def test_opt_in_kinds_are_outside_the_campaign():
    """Spec 43 scopes the campaign to the guaranteed cross-cloud surface. An
    opt-in kind here would spend metered time on something the platform does not
    promise."""
    booked = {kind for kind in collection.VARIANTS} | {kind for kind, _ in collection.NOT_EXPRESSIBLE}

    assert not booked & OPT_IN_TIER


# ---- the committed tree matches the ledger -----------------------------------


def test_the_committed_manifests_match_a_fresh_render():
    """Same contract as docs/managed_service_coverage.md: the ledger is the
    review surface, and a hand-edit to a generated file would be invisible."""
    drifted = [
        str(path.relative_to(collection.MANIFEST_ROOT))
        for path, text in collection.rendered().items()
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]

    assert not drifted, (
        "generated manifests differ from the ledger: "
        + ", ".join(sorted(drifted))
        + ". Run `make verification-manifests` in backend/providers."
    )


def test_the_grid_is_the_size_the_ledger_claims():
    """Pins the shape of the metered run: 23 default-tier kinds across three
    clouds, minus the six cells with no executable variant.

    ``encryption_key/azure`` moved out of the excused set when #1454 landed the
    Key Vault key driver; #1480's ledger was written against the tree before
    it."""
    assert len(collection.default_tier_kinds()) == 23
    assert len(collection.cells()) == 63
    assert len(collection.NOT_EXPRESSIBLE) == 6


# ---- every manifest is real ---------------------------------------------------


def test_every_manifest_parses_with_the_real_backend_parser():
    """These are inputs to a metered run. A manifest that fails to parse costs
    an install, a credential window and someone's afternoon."""
    failures = []
    for name, text in _manifests():
        try:
            parse_raw(text)
        except Exception as exc:
            failures.append(f"{name}: {exc}")

    assert not failures, "manifests the parser rejects:\n" + "\n".join(failures)


def test_every_manifest_carries_the_campaign_identity():
    """An untagged resource is invisible to the orphan scanner, which is the
    failure that makes an orphan scan lie. Both handles are checked: the app
    name the drivers stamp everywhere, and the operator tag the harness threads
    into ProvisionSpec.tags."""
    for name, text in _manifests():
        data = tomllib.loads(text)
        campaign = data.get("campaign", {})

        assert campaign.get("slug") == collection.CAMPAIGN.slug, f"{name} declares no campaign slug"
        assert campaign.get("tags", {}).get(CAMPAIGN_TAG_KEY) == collection.CAMPAIGN.slug, (
            f"{name} declares no {CAMPAIGN_TAG_KEY} tag"
        )
        assert collection.CAMPAIGN.owns_app(data["name"]), (
            f"{name} names an app the scanners cannot attribute: {data['name']!r} "
            f"must start with {collection.CAMPAIGN.app_prefix!r}"
        )


def test_every_manifest_names_a_spec_42_fixture_and_nothing_bespoke():
    """Spec 43's rule. A bespoke image is a fixture nobody maintains and a
    result nobody can reproduce."""
    for name, text in _manifests():
        data = tomllib.loads(text)

        assert data["campaign"]["fixture"] == collection.FIXTURE_REPO, f"{name} names a non-fixture repo"
        for workload in data["workloads"]:
            for container in workload["containers"]:
                assert container["image_ref"] == collection.FIXTURE_IMAGE, f"{name} uses an unpinned or foreign image"


def test_no_manifest_deploys_a_floating_tag():
    """Campaign blocker B3 in spec 41: the first AWS deploy failed because
    ``latest`` moved under it. Every image here is pinned to a fixture sha."""
    for name, text in _manifests():
        assert ":latest" not in text, f"{name} deploys a floating tag"


def test_the_declared_cell_matches_where_the_manifest_lives():
    """The cell string is what the harness reports and what a red row in the
    grid is named after. A copy-paste that leaves the old cell in place makes
    the evidence point at the wrong square."""
    for name, text in _manifests():
        data = tomllib.loads(text)
        expected = name.removesuffix(".toml")

        assert data["campaign"]["cell"] == expected, f"{name} declares cell {data['campaign']['cell']!r}"


# ---- the negative cases ------------------------------------------------------


def test_all_four_negative_cases_exist_on_all_three_clouds():
    """A campaign that only proves the happy path certifies nothing about
    safety, and a negative case that exists on one cloud proves nothing about
    the other two."""
    found = {(data["campaign"]["negative"]["case"], data["campaign"]["cloud"]) for _, data in _negative_manifests()}

    assert found == {(case, cloud) for case in NEGATIVE_CASES for cloud in CLOUDS}


@pytest.mark.parametrize("field", ["case", "expect", "precondition", "assert"])
def test_every_negative_manifest_states_its_precondition_and_its_expectation(field):
    """A negative cell is only evidence if it says what must happen. Without a
    precondition it is unrunnable; without an expectation an operator marks it
    green because nothing crashed."""
    for name, data in _negative_manifests():
        negative = data["campaign"]["negative"]

        assert negative.get(field), f"{name} declares no {field!r}"


def test_the_refusal_cases_expect_a_refusal_rather_than_a_failure():
    """'Refused' and 'errored' are different results. A driver that crashes
    halfway through a delete has not refused, and the cell must not read as
    green because something went wrong."""
    for name, data in _negative_manifests():
        negative = data["campaign"]["negative"]
        if negative["case"] in {"foreign-binding-collision", "untagged-teardown"}:
            assert negative["expect"] == "refused", f"{name} expects {negative['expect']!r}"
            assert negative.get("assert_untouched") or negative.get("assert_not"), (
                f"{name} states no assertion that the existing resource survived; a refusal "
                f"that still mutated the resource is the failure this case exists to catch"
            )
