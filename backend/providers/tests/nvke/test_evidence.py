"""Keep catalog completeness distinct from implementation and execution evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.nvke_coverage import render
from scripts.nvke_evidence import LEDGER, expected_rows, load, validate


def ledger():
    return json.loads(LEDGER.read_text())


def test_every_native_surface_has_an_explicit_scoped_delivery_row():
    data = load()
    assert {family: set(rows) for family, rows in data["rows"].items()} == expected_rows()
    document = render()
    for family in data["rows"].values():
        for row in family.values():
            assert row["remaining"].replace("|", "\\|") in document
    assert "Every row starts pending" not in document
    assert "no native controller effect proven" in document
    assert "not a current green test run" in document


@pytest.mark.parametrize("family", expected_rows())
def test_upstream_surface_changes_cannot_silently_acquire_shared_coverage(family):
    data = ledger()
    data["rows"][family].pop(next(iter(data["rows"][family])))
    with pytest.raises(ValueError, match="every row exactly once"):
        validate(data)
    data = ledger()
    data["rows"][family]["unreviewed-native-operation"] = copy.deepcopy(next(iter(data["rows"][family].values())))
    with pytest.raises(ValueError, match="every row exactly once"):
        validate(data)


@pytest.mark.parametrize("qualification", ["complete", "live", "supported", "unsupported"])
def test_shared_wiring_cannot_be_promoted_to_native_completion_or_excluded(qualification):
    data = ledger()
    data["rows"]["actions"]["createCluster"]["qualification"] = qualification
    with pytest.raises(ValueError, match=r"cannot infer|cannot be reclassified"):
        validate(data)


@pytest.mark.parametrize("change", ["missing-reference", "no-evidence", "no-remaining", "no-scope", "unused-evidence"])
def test_qualifications_need_traceable_sources_scope_and_remaining_work(change):
    data = ledger()
    row = data["rows"]["actions"]["createCluster"]
    if change == "missing-reference":
        row["evidence"] = ["not-a-source"]
    elif change == "no-evidence":
        row["evidence"] = []
    elif change == "no-remaining":
        row["remaining"] = ""
    elif change == "no-scope":
        data["evidence"]["http-fixture"]["scope"] = ""
    else:
        data["evidence"]["unused"] = copy.deepcopy(data["evidence"]["http-fixture"])
    with pytest.raises(ValueError):
        validate(data)


def test_docs_alone_cannot_imply_local_execution():
    data = ledger()
    data["rows"]["commands"]["nvkectl version"]["evidence"] = ["limits"]
    with pytest.raises(ValueError, match="test or observation"):
        validate(data)


@pytest.mark.parametrize("change", ["upstream", "mutable-commit", "source-path", "missing-selector"])
def test_evidence_source_identity_cannot_drift(change):
    data = ledger()
    if change == "upstream":
        data["upstreamRevision"] = "0" * 40
    elif change == "mutable-commit":
        data["repositories"]["cli"]["revision"] = "main"
    elif change == "source-path":
        data["evidence"]["http-fixture"]["path"] = "../outside.py"
    else:
        data["evidence"]["http-fixture"]["symbol"] = "test_missing_execution_evidence"
    with pytest.raises(ValueError):
        validate(data)


def test_paired_source_check_uses_the_selected_immutable_blob_and_refuses_mismatch():
    data = ledger()
    with (
        patch("scripts.nvke_evidence.subprocess.check_output", return_value=b"changed source") as git,
        pytest.raises(ValueError, match="immutable source"),
    ):
        validate(data, {"cli": Path("/selected/cli")})
    selected = data["evidence"]["cli-wiring"]
    assert git.call_args.args[0] == [
        "git",
        "-C",
        "/selected/cli",
        "show",
        f"{data['repositories']['cli']['revision']}:{selected['path']}",
    ]
