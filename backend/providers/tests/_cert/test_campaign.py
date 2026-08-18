"""Tests for campaign identity (spec 43 §3.1).

The failure this guards is quiet by construction: a scanner that reads a tag key
nothing writes reports every cloud clean, and the campaign certifies a teardown
path that leaks. So the cases here are about agreeing with the *write* paths,
not about the matching logic being self-consistent.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from _cert.campaign import (
    CAMPAIGN_TAG_KEY,
    Campaign,
    UnknownCloud,
    app_keys,
    campaign_keys,
    match,
)
from _sdk.azure_tags import serialize_azure_arm_tags

PROVIDERS_ROOT = pathlib.Path(__file__).resolve().parents[2]
CAMPAIGN = Campaign("cert2026q3")


# ---- the keys agree with what the drivers write ------------------------------


def test_the_azure_campaign_key_is_what_the_serializer_produces():
    """Azure appends a digest of the key name, so a transcribed constant drifts
    the moment that scheme changes. Recompute it the way a driver would."""
    written = serialize_azure_arm_tags({}, custom_tags={CAMPAIGN_TAG_KEY: CAMPAIGN.slug})

    assert list(written) == list(campaign_keys("azure"))
    assert written[campaign_keys("azure")[0]] == CAMPAIGN.slug


def test_the_gcp_and_aws_campaign_keys_match_their_driver_conventions():
    """``aws/managed/_base.py`` writes ``astrolift.io/extra/<key>``; every GCP
    ``_tags_for`` writes ``astrolift-extra-<key>``."""
    assert campaign_keys("aws") == ("astrolift.io/extra/campaign",)
    assert campaign_keys("gcp") == ("astrolift-extra-campaign",)


_APP_KEY_PATTERN = re.compile(r"^astrolift[._/-]?(?:io)?[._/-]?app$")


def _string_constants(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in docstrings
    ]


def _app_key_spellings(cloud: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    root = PROVIDERS_ROOT / cloud / "managed"
    for path in root.rglob("*.py"):
        if "test" in path.parts:
            continue
        for literal in _string_constants(path):
            if _APP_KEY_PATTERN.match(literal):
                found.setdefault(literal, []).append(str(path.relative_to(PROVIDERS_ROOT)))
    return found


@pytest.mark.parametrize("cloud", ["gcp", "azure"])
def test_the_scanner_reads_every_app_key_spelling_the_drivers_write(cloud):
    """The app slug is the handle that works today, because operator tags do not
    reach every driver. Drivers spell it four ways on GCP; a scanner that knows
    three of them silently skips whichever family uses the fourth."""
    declared = set(app_keys(cloud))
    written = _app_key_spellings(cloud)
    # ``astrolift.io/app`` is the pre-serialization input to the Azure and AWS
    # tag builders, never a key that lands on a resource, so it is not a
    # spelling the scanner has to read back.
    undeclared = {
        key: sorted(paths) for key, paths in written.items() if key not in declared and key != "astrolift.io/app"
    }

    assert not undeclared, (
        f"{cloud} drivers write app-key spellings the orphan scanner does not read: {undeclared}. "
        f"Add them to APP_KEYS in providers/_cert/campaign.py, or the scan reports clean for "
        f"every resource those drivers created."
    )


# ---- matching ----------------------------------------------------------------


@pytest.mark.parametrize("cloud", ["aws", "gcp", "azure"])
def test_a_resource_carrying_the_campaign_tag_matches(cloud):
    tags = {campaign_keys(cloud)[0]: CAMPAIGN.slug}

    assert match(CAMPAIGN, cloud, tags=tags) == f"tag {campaign_keys(cloud)[0]}={CAMPAIGN.slug}"


@pytest.mark.parametrize("key", ["astrolift-app", "astrolift-io-app", "astrolift_app", "astrolift_io_app"])
def test_every_gcp_app_key_spelling_matches(key):
    """Four spellings, one campaign. Missing one means missing whichever
    resource family that driver creates."""
    assert match(CAMPAIGN, "gcp", tags={key: "cert2026q3-happy-gcp"}) is not None


def test_a_name_match_reports_that_it_matched_on_the_name():
    """IAM has no tag surface, so a name match is the only handle there -- and
    on a tagged resource it means the tag never landed, which is a finding in
    itself. The report must be able to say which happened."""
    reason = match(CAMPAIGN, "azure", tags={}, name="astrolift-conflict-cert2026q3-happy-azure-pg")

    assert reason.startswith("name ")


def test_another_campaign_with_a_suffixed_slug_is_not_claimed():
    """``cert2026q3`` must not report ``cert2026q3b``'s resources. On a surface
    with no tags the two are otherwise indistinguishable, and deleting another
    campaign's database is worse than missing an orphan."""
    assert match(CAMPAIGN, "gcp", name="astrolift-cert2026q3b-happy-gcp-pg") is None
    assert match(CAMPAIGN, "gcp", tags={"astrolift-app": "cert2026q3b-happy-gcp"}) is None


def test_an_unrelated_resource_does_not_match():
    tags = {"astrolift-app": "customer-checkout", "astrolift-environment": "production"}

    assert match(CAMPAIGN, "gcp", tags=tags, name="astrolift-conflict-checkout-production-pg") is None


def test_an_untagged_unnamed_resource_does_not_match():
    """The honest limit of the scan: a resource carrying neither handle is
    invisible to it. Pinned so the limit stays a known one."""
    assert match(CAMPAIGN, "aws", tags={}, name="") is None


# ---- refusals ----------------------------------------------------------------


def test_an_undeclared_cloud_is_refused_rather_than_scanned_blind():
    with pytest.raises(UnknownCloud, match="no campaign tag key declared"):
        campaign_keys("oracle")
    with pytest.raises(UnknownCloud, match="no app tag keys declared"):
        app_keys("oracle")


@pytest.mark.parametrize("slug", ["cert-2026", "Cert2026", "ab", "2026q3"])
def test_a_slug_that_cannot_survive_every_naming_rule_is_refused(slug):
    """The slug ends up in app names, GCP label values and Azure resource
    names. Rejecting it here beats discovering it as a truncated resource name
    the scanner then cannot match."""
    with pytest.raises(ValueError, match="campaign slug"):
        Campaign(slug)
