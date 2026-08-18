"""The managed-service-id key must agree across three places (#1419).

Cost attribution groups on exactly one key per cloud. Three things have to name
the same string, and nothing checked that they did:

  1. the canonical serializer in ``core.cloud_tags``
  2. the key the billing client groups on, in ``providers/<cloud>/cost.py``
  3. the key drivers actually write

They drifted to fourteen distinct spellings across GCP and Azure, and the only
symptom was a growing "Shared / untagged" line on the bill. Nothing errored.

The scan below is the part that keeps it caught: a new spelling appearing in a
driver fails here rather than showing up as a billing discrepancy months later.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from _sdk.managed_service_tags import (
    CANONICAL_KEYS,
    LEGACY_KEYS,
    canonical_key,
    is_owned_by,
    read_managed_service_id,
    readable_keys,
)

PROVIDERS_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Any string that looks like it is trying to be the managed-service-id key.
#: Deliberately loose: the point is to catch a spelling nobody declared.
_KEY_PATTERN = re.compile(r"^(?:x-)?astrolift[._/-]?(?:io)?[._/-]?managed[._-]service[._-]id$")


def _driver_sources(cloud: str) -> list[pathlib.Path]:
    root = PROVIDERS_ROOT / cloud
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*.py") if "test" not in p.parts]


def _string_constants(path: pathlib.Path) -> list[str]:
    """Every string literal in a module, excluding docstrings and comments.

    Scanning raw text instead would flag prose. ``gcp/cost.py`` has a comment
    spelling out the canonical-to-GCP mapping, which is documentation doing its
    job, not a driver writing the wrong key.
    """
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


def _spellings_in(cloud: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in _driver_sources(cloud):
        for literal in _string_constants(path):
            if _KEY_PATTERN.match(literal):
                found.setdefault(literal, []).append(str(path.relative_to(PROVIDERS_ROOT)))
    return found


# ---- the three sources agree -------------------------------------------------


def test_the_canonical_key_matches_what_the_serializer_emits():
    """``core.cloud_tags`` is the stated single source. If these diverge, every
    driver doing the right thing still misses the group-by."""
    core_tags = pytest.importorskip("core.cloud_tags")

    tag_set = core_tags.CloudTagSet(
        install_slug="i", org_slug="o", app_slug="a", env_slug="e", managed_service_id="msid"
    )
    for cloud, serializer in (
        ("aws", core_tags.to_aws),
        ("gcp", core_tags.to_gcp),
        ("azure", core_tags.to_azure),
    ):
        emitted = [k for k in serializer(tag_set) if "managed" in k.lower()]
        assert emitted == [canonical_key(cloud)], cloud


@pytest.mark.parametrize(
    ("cloud", "module_path", "attribute"),
    [
        ("aws", "aws.cost", "AWS_MANAGED_SERVICE_TAG_KEY"),
        ("gcp", "gcp.cost", "GCP_MANAGED_SERVICE_LABEL_KEY"),
        ("azure", "azure.cost", "AZURE_MANAGED_SERVICE_TAG_KEY"),
    ],
)
def test_the_billing_client_groups_on_the_canonical_key(cloud, module_path, attribute):
    """A group-by names exactly one key. If it is not the canonical one, every
    correctly-tagged resource is invisible to it."""
    module = pytest.importorskip(module_path)

    assert getattr(module, attribute) == canonical_key(cloud)


# ---- no undeclared spellings -------------------------------------------------


@pytest.mark.parametrize("cloud", ["aws", "gcp", "azure", "k8s_native"])
def test_drivers_use_no_undeclared_managed_service_id_spelling(cloud):
    """The check that would have caught the drift.

    A spelling is acceptable only if it is the canonical key or an explicitly
    declared legacy alias. Anything else is a resource whose cost will never be
    attributed, and nothing else will report it.
    """
    if cloud not in CANONICAL_KEYS:
        pytest.skip(f"{cloud} tags no managed services by id")

    allowed = set(readable_keys(cloud))
    found = _spellings_in(cloud)
    undeclared = {k: v for k, v in found.items() if k not in allowed}

    assert not undeclared, (
        f"{cloud} drivers use managed-service-id spellings that are neither canonical "
        f"nor declared legacy: "
        + "; ".join(f"{k!r} in {sorted(set(v))}" for k, v in sorted(undeclared.items()))
        + f". Canonical is {canonical_key(cloud)!r}. Write that; add the old one to "
        f"LEGACY_KEYS only if shipped resources already carry it."
    )


def test_aws_writes_only_the_canonical_key():
    """AWS never drifted and has no legacy aliases, so it is the one cloud
    where the scan can assert the strong property directly."""
    assert LEGACY_KEYS["aws"] == ()
    assert set(_spellings_in("aws")) <= {canonical_key("aws")}


def test_the_drift_is_enumerated_rather_than_open_ended():
    """LEGACY_KEYS is a migration ledger. Every entry should still be present
    in a driver; one that is not has been fully migrated and the comment about
    why it cannot be removed no longer applies to it."""
    stale: list[str] = []
    for cloud in ("gcp", "azure"):
        present = set(_spellings_in(cloud))
        for legacy in LEGACY_KEYS[cloud]:
            if legacy not in present:
                stale.append(f"{cloud}:{legacy}")

    assert not stale, (
        "legacy spellings no longer written by any driver: "
        + ", ".join(sorted(stale))
        + ". Keep them readable while provisioned resources may still carry them, "
        "but note the migration is complete."
    )


# ---- reading ------------------------------------------------------------------


def test_a_legacy_tagged_resource_is_still_recognised():
    """The reason the read path accepts old spellings. Dropping them would make
    the platform stop recognising resources it created, which orphans them."""
    legacy = LEGACY_KEYS["gcp"][0]

    assert read_managed_service_id({legacy: "msid-1"}, "gcp") == "msid-1"


def test_the_canonical_key_wins_when_both_are_present():
    """A resource mid-migration can carry both. The canonical one is the one
    billing grouped on, so it is the authoritative answer."""
    tags = {canonical_key("gcp"): "new", LEGACY_KEYS["gcp"][0]: "old"}

    assert read_managed_service_id(tags, "gcp") == "new"


def test_an_untagged_resource_reads_as_empty():
    assert read_managed_service_id({"unrelated": "x"}, "gcp") == ""
    assert read_managed_service_id(None, "gcp") == ""


def test_ownership_fails_closed_on_an_empty_id():
    """An unowned resource and a caller that forgot to pass an id look the same
    to a naive equality check, and the cost of confusing them is deleting
    somebody else's resource."""
    assert not is_owned_by({}, "", "gcp")
    assert not is_owned_by({canonical_key("gcp"): ""}, "", "gcp")


def test_ownership_matches_across_spellings():
    assert is_owned_by({LEGACY_KEYS["gcp"][0]: "msid-1"}, "msid-1", "gcp")
    assert not is_owned_by({LEGACY_KEYS["gcp"][0]: "msid-2"}, "msid-1", "gcp")


def test_an_unknown_cloud_is_refused_with_something_actionable():
    from _sdk.managed_service_tags import UnknownCloud

    with pytest.raises(UnknownCloud, match="never be attributed"):
        canonical_key("oracle")
